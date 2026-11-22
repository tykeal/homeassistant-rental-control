# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Verified Keymaster operation intents and dispatch guards."""

from __future__ import annotations

import copy
from typing import Any

from homeassistant.core import HomeAssistant

from .allocator.association_evidence import AssociationAttestation
from .allocator.association_evidence import AssociationRebindHold
from .allocator.association_references import OriginalEntryEvidenceState
from .allocator.association_references import ledger_allocation_references
from .allocator.association_references import original_keymaster_entry_evidence
from .allocator.models import CycleObservation
from .allocator.registry_safety_models import validate_pending_fence
from .const import DOMAIN
from .const import PROTECTED_REGISTRY_STORE
from .keymaster_lifecycle import get_keymaster_lifecycle
from .keymaster_operation_fences import (
    _async_acknowledge_fence as _async_acknowledge_fence,
)
from .keymaster_operation_fences import (
    _current_exclusion_snapshot as _current_exclusion_snapshot,
)
from .keymaster_operation_fences import (
    async_resolve_verified_clear_fence as async_resolve_verified_clear_fence,
)
from .keymaster_operation_models import _FENCE_TYPES
from .keymaster_operation_models import VerifiedOperationIntent
from .keymaster_operation_models import (
    VerifiedOperationRejected as VerifiedOperationRejected,
)
from .keymaster_operation_models import _FenceRequest
from .keymaster_operation_validation import _authorization_inputs
from .keymaster_operation_validation import _intent_is_current as _intent_is_current
from .keymaster_operation_validation import _matching_fence
from .keymaster_operation_validation import _native_empty_text_policy
from .keymaster_operation_validation import _resolve_required_capabilities

_REFENCE_REQUIRED_PROPERTIES = (
    "switch.code_slots:{slot}.enabled",
    "switch.code_slots:{slot}.accesslimit_date_range_enabled",
    "datetime.code_slots:{slot}.accesslimit_date_range_start",
    "datetime.code_slots:{slot}.accesslimit_date_range_end",
    "text.code_slots:{slot}.pin",
    "text.code_slots:{slot}.name",
)


def create_verified_operation_intent(
    hass: HomeAssistant,
    rc_entry_id: str,
    *,
    slot: int,
    owner_identity_key: str,
    required_properties: tuple[str, ...],
    fence_type: str = "allocation",
    allow_native_empty_text: bool = False,
) -> VerifiedOperationIntent | None:
    """Create an intent only from an acknowledged exact owner fence.

    This factory deliberately does not resolve a lock by name, manufacture a
    missing fence, or treat a retained observation as authorization. Fence
    creation belongs to the protected allocator transaction; until that
    transaction is acknowledged, the verified route is unavailable.
    """
    if (
        not isinstance(rc_entry_id, str)
        or not rc_entry_id
        or isinstance(slot, bool)
        or not isinstance(slot, int)
        or slot < 1
        or not isinstance(owner_identity_key, str)
        or not owner_identity_key
        or not required_properties
        or fence_type not in _FENCE_TYPES
        or len(set(required_properties)) != len(required_properties)
    ):
        return None
    native_empty_allowed = _native_empty_text_policy(
        fence_type, slot, required_properties, allow_native_empty_text
    )
    if native_empty_allowed is None:
        return None
    inputs = _authorization_inputs(hass, rc_entry_id)
    if inputs is None:
        return None
    capabilities = _resolve_required_capabilities(
        hass,
        inputs,
        slot,
        required_properties,
        allow_native_empty_text=native_empty_allowed,
    )
    if capabilities is None:
        return None
    references = ledger_allocation_references(
        inputs.loaded.data, rc_entry_id, owner_identity_key
    )
    if references is None:
        return None
    owner_reference, allocation_reference, owner_slot = references
    fence = _matching_fence(
        inputs,
        rc_entry_id,
        slot,
        owner_reference,
        allocation_reference,
        fence_type,
    )
    if owner_slot != slot or fence is None:
        return None

    intent = VerifiedOperationIntent(
        hass=hass,
        rc_entry_id=rc_entry_id,
        keymaster_entry_id=inputs.keymaster_entry.entry_id,
        physical_lock_entity_id=inputs.physical_lock_entity_id,
        slot=slot,
        owner_identity_key=owner_identity_key,
        association_generation=inputs.association.association_generation,
        lifecycle_generation=inputs.lifecycle.generation,
        protection_token=inputs.token,
        fence_id=fence["fence_id"],
        fence_type=fence_type,
        allow_native_empty_text=native_empty_allowed,
        owner_reference=owner_reference,
        allocation_reference=allocation_reference,
        capabilities=capabilities,
    )
    return (
        intent
        if _intent_is_current(intent, allow_native_empty_text=native_empty_allowed)
        else None
    )


async def async_prepare_verified_operation_intent(
    hass: HomeAssistant,
    rc_entry_id: str,
    *,
    slot: int,
    owner_identity_key: str,
    required_properties: tuple[str, ...],
    fence_type: str = "allocation",
    allow_native_empty_text: bool = False,
) -> VerifiedOperationIntent | None:
    """Acknowledge an exact owner fence before returning a dispatch intent.

    The storage snapshot is committed only through the injected protected
    store and allocator's shared lock. This path is available only when an
    explicitly verified lifecycle,
    protected store, and loaded allocator are present; it never falls back to
    a constructed entity ID.
    """
    native_empty_allowed = _native_empty_text_policy(
        fence_type, slot, required_properties, allow_native_empty_text
    )
    if native_empty_allowed is None:
        return None
    initial = _authorization_inputs(hass, rc_entry_id)
    if initial is None:
        return None
    references = ledger_allocation_references(
        initial.loaded.data, rc_entry_id, owner_identity_key
    )
    if references is None:
        return None
    owner_reference, allocation_reference, owner_slot = references
    if owner_slot != slot:
        return None
    fence = _matching_fence(
        initial,
        rc_entry_id,
        slot,
        owner_reference,
        allocation_reference,
        fence_type,
    )
    if (
        _resolve_required_capabilities(
            hass,
            initial,
            slot,
            required_properties,
            allow_native_empty_text=native_empty_allowed,
        )
        is None
    ):
        return None
    pending_clear_exists = any(
        validate_pending_fence(record)
        and record["original_km_entry_id"] == initial.keymaster_entry.entry_id
        and record["slot"] == slot
        and record["fence_type"] == "pending_clear"
        for record in initial.loaded.data.get("pending_fences", [])
    )
    if fence_type == "allocation" and pending_clear_exists:
        return None
    if fence is None and not await _async_acknowledge_fence(
        hass,
        _FenceRequest(
            rc_entry_id=rc_entry_id,
            slot=slot,
            owner_identity_key=owner_identity_key,
            owner_reference=owner_reference,
            allocation_reference=allocation_reference,
            fence_type=fence_type,
            allow_native_empty_text=native_empty_allowed,
            initial=initial,
            required_properties=required_properties,
        ),
    ):
        return None
    return create_verified_operation_intent(
        hass,
        rc_entry_id,
        slot=slot,
        owner_identity_key=owner_identity_key,
        required_properties=required_properties,
        fence_type=fence_type,
        allow_native_empty_text=native_empty_allowed,
    )


async def async_refence_observed_legacy_owners(
    hass: HomeAssistant, observation: CycleObservation
) -> None:
    """Re-fence matching legacy owners before a cycle may release ownership.

    An intact v1 ledger can contain a lock-backed owner without the v2
    allocation fence. Re-fencing is allowed only after the current selected
    Keymaster lifecycle observes that exact ledger code in that owner's slot,
    and either immutable safety history or a scoped legacy attestation binds
    the owner to the selected Keymaster entry. This writes no hardware state.
    """
    if observation.lockname is None or observation.keymaster_entry_id is None:
        return
    inputs = _authorization_inputs(hass, observation.entry_id)
    if (
        inputs is None
        or inputs.keymaster_entry.entry_id != observation.keymaster_entry_id
        or inputs.lifecycle.generation != observation.keymaster_generation
    ):
        return
    from .allocator.singleton import get_allocator

    allocator = get_allocator(hass)
    if allocator is None:
        return
    async with allocator._lock:
        registry = copy.deepcopy(allocator._registry)
    data = inputs.loaded.data
    if not isinstance(data, dict):
        return
    selected_entry_id = inputs.keymaster_entry.entry_id
    evidence = _refence_evidence(data, observation.entry_id, selected_entry_id)
    if evidence is None:
        return
    records, attested_references, retained_references = evidence

    for record, owner in _observed_allocation_owners(registry, observation):
        slot = owner.slot
        assert slot is not None
        if owner.entry_id != observation.entry_id:
            continue
        if owner.lockname != observation.lockname or not owner.lock_observed:
            continue
        if slot not in observation.managed_slots:
            continue
        if observation.observed_codes.get(record.code) != slot:
            continue
        references = ledger_allocation_references(
            data, observation.entry_id, owner.identity_key
        )
        if references is None:
            continue
        owner_reference, allocation_reference, persisted_slot = references
        if persisted_slot != slot or owner_reference in retained_references:
            continue
        if _has_matching_allocation_fence(
            records,
            selected_entry_id,
            observation.entry_id,
            slot,
            owner_reference,
            allocation_reference,
        ):
            continue
        if not _original_entry_authorizes_refence(
            data,
            observation.entry_id,
            selected_entry_id,
            owner_reference,
            attested_references,
        ):
            continue
        await async_prepare_verified_operation_intent(
            hass,
            observation.entry_id,
            slot=slot,
            owner_identity_key=owner.identity_key,
            required_properties=tuple(
                property_key.format(slot=slot)
                for property_key in _REFENCE_REQUIRED_PROPERTIES
            ),
            fence_type="allocation",
            allow_native_empty_text=True,
        )


def _refence_evidence(
    data: dict[str, Any],
    rc_entry_id: str,
    selected_entry_id: str,
) -> tuple[list[dict[str, Any]], set[str], set[str]] | None:
    """Return validated fence, attestation, and hold evidence for re-fencing."""
    records = data.get("pending_fences")
    holds = data.get("holds")
    attestations = data.get("attestations")
    if (
        not isinstance(records, list)
        or not isinstance(holds, list)
        or not isinstance(attestations, list)
    ):
        return None
    attested: set[str] = set()
    for raw in attestations:
        attestation = AssociationAttestation.from_record(raw)
        if (
            attestation is not None
            and attestation.rc_entry_id == rc_entry_id
            and attestation.selected_keymaster_entry_id == selected_entry_id
        ):
            attested.update(attestation.legacy_reference_ids)
    retained: set[str] = set()
    for raw in holds:
        hold = AssociationRebindHold.from_record(raw)
        if hold is not None and hold.rc_entry_id == rc_entry_id:
            retained.update(hold.claim_references)
    return records, attested, retained


def _observed_allocation_owners(
    registry: Any, observation: CycleObservation
) -> list[tuple[Any, Any]]:
    """Select owners whose exact code is observed in their claimed slot."""
    return [
        (record, owner)
        for record in registry.records.values()
        for owner in record.owners
        if owner.slot is not None
        and owner.slot in observation.managed_slots
        and observation.observed_codes.get(record.code) == owner.slot
    ]


def _has_matching_allocation_fence(
    fences: list[dict[str, Any]],
    selected_entry_id: str,
    rc_entry_id: str,
    slot: int,
    owner_reference: str,
    allocation_reference: str,
) -> bool:
    """Return whether the exact owner already has its original allocation fence."""
    return any(
        validate_pending_fence(fence)
        and fence["original_km_entry_id"] == selected_entry_id
        and fence["slot"] == slot
        and fence["rc_entry_id"] == rc_entry_id
        and fence["owner_ref"] == owner_reference
        and fence["allocation_ref"] == allocation_reference
        and fence["fence_type"] == "allocation"
        for fence in fences
    )


def _original_entry_authorizes_refence(
    data: dict[str, Any],
    rc_entry_id: str,
    selected_entry_id: str,
    owner_reference: str,
    attested_references: set[str],
) -> bool:
    """Never use an attestation to override known original-entry evidence."""
    evidence = original_keymaster_entry_evidence(
        data,
        rc_entry_id,
        (owner_reference,),
        require_complete_snapshot=False,
    )
    if evidence.state is OriginalEntryEvidenceState.UNIQUE:
        return evidence.keymaster_entry_id == selected_entry_id
    return (
        evidence.state is OriginalEntryEvidenceState.ABSENT
        and owner_reference in attested_references
    )


def verified_lifecycle_active(coordinator: Any) -> bool:
    """Return whether this coordinator has an active exact-identity lifecycle."""
    config_entry = getattr(coordinator, "config_entry", None)
    entry_id = getattr(config_entry, "entry_id", None) or getattr(
        coordinator, "_entry_id", None
    )
    hass = getattr(coordinator, "hass", None)
    return (
        isinstance(entry_id, str)
        and bool(entry_id)
        and hass is not None
        and get_keymaster_lifecycle(hass, entry_id) is not None
    )


def protected_registry_active(hass: Any) -> bool:
    """Return whether the production protected registry is registered."""
    data = getattr(hass, "data", None)
    if not isinstance(data, dict):
        return False
    domain_data = data.get(DOMAIN)
    return isinstance(domain_data, dict) and PROTECTED_REGISTRY_STORE in domain_data


def persisted_owner_identity_for_slot(coordinator: Any, slot: int) -> str | None:
    """Return one cache candidate that the protected ledger must verify."""
    getter = getattr(coordinator, "get_persisted_slot_mappings", None)
    mappings = getter() if callable(getter) else None
    if not isinstance(mappings, dict):
        stored = getattr(coordinator, "_slot_mappings", {})
        mappings = stored.get("mappings") if isinstance(stored, dict) else None
    if not isinstance(mappings, dict):
        return None
    matches = [
        identity_key
        for identity_key, mapping in mappings.items()
        if isinstance(identity_key, str)
        and isinstance(mapping, dict)
        and mapping.get("slot") == slot
    ]
    return matches[0] if len(matches) == 1 else None


async def prepare_verified_intent(
    coordinator: Any,
    slot: int,
    owner_identity_key: str,
    properties: tuple[str, ...],
    fence_type: str,
    *,
    allow_native_empty_text: bool = False,
) -> VerifiedOperationIntent | None:
    """Create or acknowledge an exact protective fence before dispatch."""
    config_entry = getattr(coordinator, "config_entry", None)
    rc_entry_id = getattr(config_entry, "entry_id", None) or getattr(
        coordinator, "_entry_id", None
    )
    if not isinstance(rc_entry_id, str) or not rc_entry_id:
        return None
    try:
        return await async_prepare_verified_operation_intent(
            coordinator.hass,
            rc_entry_id,
            slot=slot,
            owner_identity_key=owner_identity_key,
            required_properties=tuple(prop.format(slot=slot) for prop in properties),
            fence_type=fence_type,
            allow_native_empty_text=allow_native_empty_text,
        )
    except Exception:
        return None
