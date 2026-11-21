# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Dormant verified Keymaster operation intents and dispatch guards."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
import hashlib
import json
import secrets
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from .allocator.association_references import ledger_allocation_references
from .allocator.associations import AssociationMode
from .allocator.associations import AssociationTuple
from .allocator.models import ProtectionAuthorizationState
from .allocator.models import ProtectionCommitToken
from .allocator.models import ProtectionLoadResult
from .allocator.models import ProtectionLoadState
from .allocator.registry_safety_models import validate_pending_fence
from .const import DOMAIN
from .const import PROTECTED_REGISTRY_STORE
from .helpers import get_entry_data
from .keymaster_identity import CapabilityResolution
from .keymaster_identity import resolve_capability
from .keymaster_lifecycle import get_keymaster_lifecycle

_FENCE_TYPES = frozenset({"allocation", "pending_clear"})
_PHYSICAL_LOCK_ENTITY_KEY = "lock_entity_id"
_SERVICE_ALLOWLIST = {
    "button": frozenset({"press"}),
    "datetime": frozenset({"set_value"}),
    "switch": frozenset({"turn_off", "turn_on"}),
    "text": frozenset({"set_value"}),
}


@dataclass(frozen=True, slots=True)
class _AuthorizationInputs:
    """Validated current entry, lifecycle, store, association and token."""

    config_entry: Any = field(repr=False)
    lifecycle: Any = field(repr=False)
    store: Any = field(repr=False)
    association: AssociationTuple
    loaded: Any = field(repr=False)
    token: ProtectionCommitToken = field(repr=False)
    keymaster_entry: Any = field(repr=False)
    physical_lock_entity_id: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class _CurrentIntentInputs:
    """Current lifecycle, association, store snapshot, and selected entry."""

    lifecycle: Any = field(repr=False)
    store: Any = field(repr=False)
    association: AssociationTuple
    loaded: Any = field(repr=False)
    keymaster_entry: Any = field(repr=False)


@dataclass(frozen=True, slots=True)
class _FenceRequest:
    """Exact owner and target identity needed to acknowledge one fence."""

    rc_entry_id: str
    slot: int
    owner_identity_key: str
    owner_reference: str
    allocation_reference: str
    fence_type: str
    initial: _AuthorizationInputs = field(repr=False)
    required_properties: tuple[str, ...] = field(repr=False)


class VerifiedOperationRejected(Exception):
    """A fixed-code rejection that must never trigger legacy fallback."""


@dataclass(frozen=True, slots=True)
class VerifiedOperationIntent:
    """Bind one hardware operation to exact identity, fence, and generations.

    An intent is an authorization snapshot, not a long-lived capability.
    Consumers must call :meth:`async_call` for each individual side effect;
    it waits for protected writes to settle and revalidates the association,
    selected entry, capability addresses, lifecycle generation, and exact
    durable owner/fence references before and after each service call.
    """

    hass: HomeAssistant = field(repr=False, compare=False)
    rc_entry_id: str = field(repr=False)
    keymaster_entry_id: str = field(repr=False)
    physical_lock_entity_id: str = field(repr=False)
    slot: int
    owner_identity_key: str = field(repr=False)
    association_generation: int
    lifecycle_generation: int
    protection_token: ProtectionCommitToken = field(repr=False)
    fence_id: str = field(repr=False)
    fence_type: str
    owner_reference: str = field(repr=False)
    allocation_reference: str = field(repr=False)
    capabilities: tuple[CapabilityResolution, ...] = field(repr=False)

    def entity_id(self, property_key: str) -> str:
        """Return a previously authorized exact address without dispatching.

        This is a read-only address lookup. Every side effect and positive
        confirmation must separately pass ``async_is_current`` so an unrelated
        protected write can settle before authorization is re-evaluated.
        """
        for capability in self.capabilities:
            if (
                capability.identity is not None
                and capability.identity.property_key == property_key
                and capability.entity_id is not None
            ):
                return capability.entity_id
        raise VerifiedOperationRejected("capability_not_authorized")

    async def async_call(
        self,
        property_key: str,
        service: str,
        service_data: dict[str, Any] | None = None,
    ) -> None:
        """Dispatch one exact-entity call with point-in-time revalidation."""
        if not await self.async_is_current():
            raise VerifiedOperationRejected("operation_not_current")
        entity_id = self.entity_id(property_key)
        domain = property_key.split(".", maxsplit=1)[0]
        if service not in _SERVICE_ALLOWLIST.get(domain, frozenset()):
            raise VerifiedOperationRejected("service_not_authorized")
        if not await self.async_is_current():
            raise VerifiedOperationRejected("operation_not_current")
        await self.hass.services.async_call(
            domain=domain,
            service=service,
            target={"entity_id": entity_id},
            service_data=service_data or {},
            blocking=True,
        )
        if not await self.async_is_current():
            # The side effect may have completed. The durable fence remains;
            # callers must report unconfirmed and must not retry by name.
            raise VerifiedOperationRejected("operation_invalidated_after_dispatch")

    async def async_is_current(self) -> bool:
        """Wait for any protected write, then validate its acknowledged state."""
        domain_data = self.hass.data.get(DOMAIN)
        store = (
            domain_data.get(PROTECTED_REGISTRY_STORE)
            if isinstance(domain_data, dict)
            else None
        )
        waiter = getattr(store, "async_wait_for_commit_completion", None)
        if not callable(waiter):
            return False
        try:
            if not await waiter():
                return False
        except Exception:
            return False
        return self.is_current()

    def is_current(self) -> bool:
        """Revalidate every authorization input on the HA event-loop thread."""
        return _intent_is_current(self)


def create_verified_operation_intent(
    hass: HomeAssistant,
    rc_entry_id: str,
    *,
    slot: int,
    owner_identity_key: str,
    required_properties: tuple[str, ...],
    fence_type: str = "allocation",
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
    inputs = _authorization_inputs(hass, rc_entry_id)
    if inputs is None:
        return None
    capabilities = _resolve_required_capabilities(
        hass, inputs, slot, required_properties
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
        owner_reference=owner_reference,
        allocation_reference=allocation_reference,
        capabilities=capabilities,
    )
    return intent if _intent_is_current(intent) else None


async def async_prepare_verified_operation_intent(
    hass: HomeAssistant,
    rc_entry_id: str,
    *,
    slot: int,
    owner_identity_key: str,
    required_properties: tuple[str, ...],
    fence_type: str = "allocation",
) -> VerifiedOperationIntent | None:
    """Acknowledge an exact owner fence before returning a dispatch intent.

    The storage snapshot is committed only through the injected protected
    store and allocator's shared lock. This path remains dormant unless an
    explicitly verified lifecycle, protected store, and loaded allocator are
    present; it never falls back to a constructed entity ID.
    """
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
    if _resolve_required_capabilities(hass, initial, slot, required_properties) is None:
        return None
    fence = _matching_fence(
        initial,
        rc_entry_id,
        slot,
        owner_reference,
        allocation_reference,
        fence_type,
    )
    pending_clear_exists = any(
        validate_pending_fence(record)
        and record["original_km_entry_id"] == initial.keymaster_entry.entry_id
        and record["slot"] == slot
        and record["fence_type"] == "pending_clear"
        for record in initial.loaded.data.get("pending_fences", [])
    )
    if fence_type == "allocation" and pending_clear_exists:
        return None
    if fence is None:
        if not await _async_acknowledge_fence(
            hass,
            _FenceRequest(
                rc_entry_id=rc_entry_id,
                slot=slot,
                owner_identity_key=owner_identity_key,
                owner_reference=owner_reference,
                allocation_reference=allocation_reference,
                fence_type=fence_type,
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
    )


async def _async_acknowledge_fence(
    hass: HomeAssistant,
    request: _FenceRequest,
) -> bool:
    """Commit one current-generation fence with a locked live preflight."""
    from .allocator.registry_safety_models import PendingSlotFence
    from .allocator.registry_snapshot import SnapshotCommitHooks
    from .allocator.registry_snapshot import async_commit_registry_snapshot
    from .allocator.registry_snapshot import serialize_exclusion_snapshots
    from .allocator.singleton import get_allocator

    domain_data = hass.data.get(DOMAIN)
    allocator = get_allocator(hass)
    if (
        not isinstance(domain_data, dict)
        or allocator is None
        or not request.initial.store.uses_async_lock(allocator._lock)
    ):
        return False
    exclusion_generation, exclusions = _current_exclusion_snapshot(
        hass, serialize_exclusion_snapshots
    )

    def safety_transition(
        _candidate: Any,
        data: dict[str, Any],
        transaction_id: str,
        generation: int,
    ) -> None:
        """Append only a fence whose serialized owner matches the request."""
        target_references = ledger_allocation_references(
            data, request.rc_entry_id, request.owner_identity_key
        )
        if target_references != (
            request.owner_reference,
            request.allocation_reference,
            request.slot,
        ):
            raise ValueError("owner_changed")
        records = data.get("pending_fences")
        if not isinstance(records, list) or not all(
            validate_pending_fence(record) for record in records
        ):
            raise ValueError("invalid_fences")
        same_slot_clear = any(
            record["original_km_entry_id"] == request.initial.keymaster_entry.entry_id
            and record["slot"] == request.slot
            and record["fence_type"] == "pending_clear"
            for record in records
        )
        if request.fence_type == "allocation" and same_slot_clear:
            raise ValueError("pending_clear")
        if request.fence_type == "pending_clear" and any(
            record["original_km_entry_id"] == request.initial.keymaster_entry.entry_id
            and record["slot"] == request.slot
            and record["fence_type"] == "pending_clear"
            and (
                record["rc_entry_id"] != request.rc_entry_id
                or record["owner_ref"] != request.owner_reference
                or record["allocation_ref"] != request.allocation_reference
            )
            for record in records
        ):
            raise ValueError("ambiguous_pending_clear")
        records.append(
            PendingSlotFence(
                fence_id=secrets.token_hex(16),
                original_km_entry_id=request.initial.keymaster_entry.entry_id,
                slot=request.slot,
                rc_entry_id=request.rc_entry_id,
                owner_ref=request.owner_reference,
                allocation_ref=request.allocation_reference,
                created_transaction_id=transaction_id,
                created_generation=generation,
                fence_type=request.fence_type,
            ).to_record()
        )

    def event_loop_preflight() -> ProtectionLoadResult | None:
        """Revalidate selected identity and store token before any file I/O."""
        current = _authorization_inputs(hass, request.rc_entry_id)
        if (
            current is None
            or current.lifecycle is not request.initial.lifecycle
            or current.lifecycle.generation != request.initial.lifecycle.generation
            or current.keymaster_entry.entry_id
            != request.initial.keymaster_entry.entry_id
            or current.association != request.initial.association
            or current.token != request.initial.token
            or ledger_allocation_references(
                current.loaded.data, request.rc_entry_id, request.owner_identity_key
            )
            != (
                request.owner_reference,
                request.allocation_reference,
                request.slot,
            )
            or _resolve_required_capabilities(
                hass, current, request.slot, request.required_properties
            )
            is None
        ):
            return ProtectionLoadResult(
                ProtectionLoadState.UNAVAILABLE,
                failure_code="operation_not_current",
            )
        return None

    result = await async_commit_registry_snapshot(
        allocator,
        request.initial.store,
        lambda _registry: None,
        exclusions,
        lambda: _current_exclusion_snapshot(hass, serialize_exclusion_snapshots),
        hooks=SnapshotCommitHooks(
            safety_transition=safety_transition,
            event_loop_preflight=event_loop_preflight,
        ),
    )
    return result.state is ProtectionLoadState.VALID_V2


def _current_exclusion_snapshot(
    hass: HomeAssistant,
    serialize: Any,
) -> tuple[int, list[dict[str, Any]]]:
    """Capture complete exclusions and a stable digest on the HA event loop."""
    entries = hass.config_entries.async_entries(DOMAIN)
    exclusions = serialize(entries)
    encoded = json.dumps(
        exclusions,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    generation = int.from_bytes(hashlib.sha256(encoded).digest()[:8], "big")
    return generation, exclusions


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
        )
    except Exception:
        return None


async def async_resolve_verified_clear_fence(
    intent: VerifiedOperationIntent,
) -> bool:
    """Tombstone a confirmed pending-clear fence before local slot release."""
    if intent.fence_type != "pending_clear" or not await intent.async_is_current():
        return False
    from .allocator.registry_safety_models import resolve_fence_to_tombstone
    from .allocator.registry_snapshot import SnapshotCommitHooks
    from .allocator.registry_snapshot import async_commit_registry_snapshot
    from .allocator.registry_snapshot import serialize_exclusion_snapshots
    from .allocator.singleton import get_allocator

    hass = intent.hass
    inputs = _authorization_inputs(hass, intent.rc_entry_id)
    allocator = get_allocator(hass)
    if (
        inputs is None
        or allocator is None
        or not inputs.store.uses_async_lock(allocator._lock)
    ):
        return False
    _generation, exclusions = _current_exclusion_snapshot(
        hass, serialize_exclusion_snapshots
    )

    def safety_transition(
        _candidate: Any,
        data: dict[str, Any],
        transaction_id: str,
        generation: int,
    ) -> None:
        """Resolve only this exact fence and retain its original identity."""
        fences = data.get("pending_fences")
        tombstones = data.get("tombstones")
        if not isinstance(fences, list) or not isinstance(tombstones, list):
            raise ValueError("invalid_safety_history")
        matches = [
            fence
            for fence in fences
            if validate_pending_fence(fence)
            and fence["fence_id"] == intent.fence_id
            and fence["fence_type"] == "pending_clear"
            and fence["rc_entry_id"] == intent.rc_entry_id
            and fence["original_km_entry_id"] == intent.keymaster_entry_id
            and fence["slot"] == intent.slot
            and fence["owner_ref"] == intent.owner_reference
            and fence["allocation_ref"] == intent.allocation_reference
        ]
        if len(matches) != 1:
            raise ValueError("clear_fence_changed")
        fence = matches[0]
        fences.remove(fence)
        tombstones.append(
            resolve_fence_to_tombstone(
                fence,
                tombstone_id=secrets.token_hex(16),
                resolved_transaction_id=transaction_id,
                resolved_generation=generation,
                resolution_kind="clear_confirmed",
                retained_at=datetime.now(UTC).isoformat(),
            )
        )

    result = await async_commit_registry_snapshot(
        allocator,
        inputs.store,
        lambda _registry: None,
        exclusions,
        lambda: _current_exclusion_snapshot(hass, serialize_exclusion_snapshots),
        hooks=SnapshotCommitHooks(
            safety_transition=safety_transition,
            event_loop_preflight=lambda: (
                None
                if intent.is_current()
                else ProtectionLoadResult(
                    ProtectionLoadState.UNAVAILABLE,
                    failure_code="operation_not_current",
                )
            ),
        ),
    )
    return result.state is ProtectionLoadState.VALID_V2


def _authorization_inputs(
    hass: HomeAssistant, rc_entry_id: str
) -> _AuthorizationInputs | None:
    """Capture one selected association and current protected snapshot."""
    if get_entry_data(hass, rc_entry_id) is None:
        return None
    lifecycle = get_keymaster_lifecycle(hass, rc_entry_id)
    config_entry = hass.config_entries.async_get_entry(rc_entry_id)
    domain_data = hass.data.get(DOMAIN)
    if lifecycle is None or config_entry is None or not isinstance(domain_data, dict):
        return None
    store = domain_data.get(PROTECTED_REGISTRY_STORE)
    if store is None or not _is_config_entry(config_entry):
        return None
    association = AssociationTuple.from_config(config_entry.data)
    if (
        association is None
        or association.mode is not AssociationMode.VERIFIED
        or association.verified_keymaster_entry_id != lifecycle.keymaster_entry_id
        or association.association_generation < 1
    ):
        return None
    try:
        if store.authorization_state is not ProtectionAuthorizationState.READY:
            return None
        if store.association_for_entry(rc_entry_id) != association:
            return None
        loaded = store.loaded_result
        if (
            loaded is None
            or loaded.state is not ProtectionLoadState.VALID_V2
            or loaded.data is None
            or loaded.transaction_id is None
            or loaded.generation is None
        ):
            return None
        token = ProtectionCommitToken(loaded.transaction_id, loaded.generation)
        if not store.is_current_commit_token(token):
            return None
    except Exception:
        return None
    keymaster_entry = hass.config_entries.async_get_entry(
        association.verified_keymaster_entry_id
    )
    if keymaster_entry is None or keymaster_entry.state is not ConfigEntryState.LOADED:
        return None
    physical_lock_entity_id = keymaster_entry.data.get(_PHYSICAL_LOCK_ENTITY_KEY)
    if (
        not isinstance(physical_lock_entity_id, str)
        or not physical_lock_entity_id
        or "." not in physical_lock_entity_id
    ):
        return None
    return _AuthorizationInputs(
        config_entry,
        lifecycle,
        store,
        association,
        loaded,
        token,
        keymaster_entry,
        physical_lock_entity_id,
    )


def _resolve_required_capabilities(
    hass: HomeAssistant,
    inputs: _AuthorizationInputs,
    slot: int,
    required_properties: tuple[str, ...],
) -> tuple[CapabilityResolution, ...] | None:
    """Resolve and cross-check every same-slot live property."""
    generation = inputs.lifecycle.generation
    current = inputs.lifecycle.current
    capabilities: list[CapabilityResolution] = []
    for property_key in required_properties:
        resolution = current.get(property_key)
        identity = resolution.identity if resolution is not None else None
        if (
            resolution is None
            or not resolution.ready
            or identity is None
            or identity.slot != slot
            or identity.keymaster_entry_id != inputs.keymaster_entry.entry_id
            or identity.generation != generation
            or resolution.entity_id is None
        ):
            return None
        fresh = resolve_capability(
            hass,
            inputs.keymaster_entry.entry_id,
            property_key,
            generation=generation,
        )
        if not fresh.ready or fresh.entity_id != resolution.entity_id:
            return None
        capabilities.append(fresh)
    return tuple(capabilities)


def _matching_fence(
    inputs: _AuthorizationInputs,
    rc_entry_id: str,
    slot: int,
    owner_reference: str,
    allocation_reference: str,
    fence_type: str,
) -> dict[str, Any] | None:
    """Return one exact durable fence, refusing missing or duplicate matches."""
    fences = inputs.loaded.data.get("pending_fences")
    if not isinstance(fences, list):
        return None
    matches = [
        fence
        for fence in fences
        if validate_pending_fence(fence)
        and fence["fence_type"] == fence_type
        and fence["rc_entry_id"] == rc_entry_id
        and fence["original_km_entry_id"] == inputs.keymaster_entry.entry_id
        and fence["slot"] == slot
        and fence["owner_ref"] == owner_reference
        and fence["allocation_ref"] == allocation_reference
    ]
    return matches[0] if len(matches) == 1 else None


def _intent_is_current(intent: VerifiedOperationIntent) -> bool:
    """Return whether the exact protected identity snapshot still holds."""
    inputs = _current_intent_inputs(intent)
    if inputs is None:
        return False
    return (
        _current_store_snapshot_matches(intent, inputs)
        and _current_selected_entry_matches(intent, inputs)
        and _current_capabilities_match(intent, inputs.lifecycle)
        and _current_owner_fence_matches(intent, inputs.loaded.data)
    )


def _current_intent_inputs(
    intent: VerifiedOperationIntent,
) -> _CurrentIntentInputs | None:
    """Validate current entry and association before inspecting the store."""
    hass = intent.hass
    entry_data = get_entry_data(hass, intent.rc_entry_id)
    lifecycle = get_keymaster_lifecycle(hass, intent.rc_entry_id)
    config_entry = hass.config_entries.async_get_entry(intent.rc_entry_id)
    domain_data = hass.data.get(DOMAIN)
    if (
        entry_data is None
        or lifecycle is None
        or config_entry is None
        or lifecycle.generation != intent.lifecycle_generation
        or not isinstance(domain_data, dict)
    ):
        return None
    if not _is_config_entry(config_entry):
        return None
    association = AssociationTuple.from_config(config_entry.data)
    if (
        association is None
        or association.mode is not AssociationMode.VERIFIED
        or association.verified_keymaster_entry_id != intent.keymaster_entry_id
        or association.association_generation != intent.association_generation
    ):
        return None
    store = domain_data.get(PROTECTED_REGISTRY_STORE)
    if store is None:
        return None
    try:
        loaded = store.loaded_result
    except Exception:
        return None
    if (
        loaded is None
        or loaded.state is not ProtectionLoadState.VALID_V2
        or loaded.data is None
    ):
        return None
    keymaster_entry = hass.config_entries.async_get_entry(intent.keymaster_entry_id)
    if keymaster_entry is None:
        return None
    return _CurrentIntentInputs(
        lifecycle,
        store,
        association,
        loaded,
        keymaster_entry,
    )


def _current_store_snapshot_matches(
    intent: VerifiedOperationIntent, inputs: _CurrentIntentInputs
) -> bool:
    """Require a settled READY snapshot retaining this exact association."""
    try:
        return (
            inputs.store.authorization_state is ProtectionAuthorizationState.READY
            and inputs.store.operation_snapshot_ready()
            and inputs.store.association_for_entry(intent.rc_entry_id)
            == inputs.association
        )
    except Exception:
        return False


def _current_selected_entry_matches(
    intent: VerifiedOperationIntent, inputs: _CurrentIntentInputs
) -> bool:
    """Require the selected Keymaster entry and physical lock to remain live."""
    keymaster_entry = inputs.keymaster_entry
    if (
        keymaster_entry.state is not ConfigEntryState.LOADED
        or keymaster_entry.data.get(_PHYSICAL_LOCK_ENTITY_KEY)
        != intent.physical_lock_entity_id
    ):
        return False
    return True


def _current_capabilities_match(
    intent: VerifiedOperationIntent, lifecycle: Any
) -> bool:
    """Require every same-slot address to remain freshly resolved."""
    current = lifecycle.current
    for previous in intent.capabilities:
        identity = previous.identity
        if identity is None or identity.generation != intent.lifecycle_generation:
            return False
        resolution = current.get(identity.property_key)
        if (
            resolution is None
            or not resolution.ready
            or resolution.entity_id != previous.entity_id
            or resolution.identity != identity
        ):
            return False
        fresh = resolve_capability(
            intent.hass,
            intent.keymaster_entry_id,
            identity.property_key,
            generation=intent.lifecycle_generation,
        )
        if not fresh.ready or fresh.entity_id != previous.entity_id:
            return False
    return True


def _current_owner_fence_matches(intent: VerifiedOperationIntent, data: Any) -> bool:
    """Require the exact owner allocation and durable fence to remain present."""
    owner_references = ledger_allocation_references(
        data, intent.rc_entry_id, intent.owner_identity_key
    )
    if owner_references != (
        intent.owner_reference,
        intent.allocation_reference,
        intent.slot,
    ):
        return False
    fences = data.get("pending_fences")
    if not isinstance(fences, list):
        return False
    return any(
        validate_pending_fence(fence)
        and fence["fence_id"] == intent.fence_id
        and fence["fence_type"] == intent.fence_type
        and fence["rc_entry_id"] == intent.rc_entry_id
        and fence["original_km_entry_id"] == intent.keymaster_entry_id
        and fence["slot"] == intent.slot
        and fence["owner_ref"] == intent.owner_reference
        and fence["allocation_ref"] == intent.allocation_reference
        for fence in fences
    )


def _is_config_entry(value: object) -> bool:
    """Recognize the narrow config-entry shape used by this internal guard."""
    return (
        value is not None
        and isinstance(getattr(value, "entry_id", None), str)
        and isinstance(getattr(value, "data", None), Mapping)
    )
