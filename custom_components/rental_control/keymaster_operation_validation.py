# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Revalidation rules for protected Keymaster operation intents."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from .allocator.association_references import ledger_allocation_references
from .allocator.associations import AssociationMode
from .allocator.associations import AssociationTuple
from .allocator.models import ProtectionAuthorizationState
from .allocator.models import ProtectionCommitToken
from .allocator.models import ProtectionLoadState
from .allocator.registry_safety_models import validate_pending_fence
from .const import DOMAIN
from .const import PROTECTED_REGISTRY_STORE
from .helpers import get_entry_data
from .keymaster_identity import CapabilityResolution
from .keymaster_identity import CapabilityStatus
from .keymaster_identity import resolve_capability
from .keymaster_lifecycle import get_keymaster_lifecycle
from .keymaster_operation_models import _PHYSICAL_LOCK_ENTITY_KEY
from .keymaster_operation_models import _SET_REQUIRED_PROPERTIES
from .keymaster_operation_models import VerifiedOperationIntent
from .keymaster_operation_models import _AuthorizationInputs
from .keymaster_operation_models import _CurrentIntentInputs


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


def _native_empty_text_policy(
    fence_type: str,
    slot: int,
    required_properties: tuple[str, ...],
    requested: bool,
) -> bool | None:
    """Scope native-empty text to clear evidence or the exact verified set."""
    if fence_type == "pending_clear":
        return True
    if not requested:
        return False
    expected_set_properties = {
        property_key.format(slot=slot) for property_key in _SET_REQUIRED_PROPERTIES
    }
    if (
        fence_type != "allocation"
        or set(required_properties) != expected_set_properties
    ):
        return None
    return True


def _resolve_required_capabilities(
    hass: HomeAssistant,
    inputs: _AuthorizationInputs,
    slot: int,
    required_properties: tuple[str, ...],
    *,
    allow_native_empty_text: bool = False,
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
            or (
                not resolution.ready
                and not _native_empty_text_capability(
                    resolution,
                    slot=slot,
                    keymaster_entry_id=inputs.keymaster_entry.entry_id,
                    generation=generation,
                    allow_native_empty_text=allow_native_empty_text,
                )
            )
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
        if (
            not fresh.ready
            and not _native_empty_text_capability(
                fresh,
                slot=slot,
                keymaster_entry_id=inputs.keymaster_entry.entry_id,
                generation=generation,
                allow_native_empty_text=allow_native_empty_text,
            )
        ) or fresh.entity_id != resolution.entity_id:
            return None
        capabilities.append(fresh)
    return tuple(capabilities)


def _native_empty_text_capability(
    resolution: CapabilityResolution,
    *,
    slot: int,
    keymaster_entry_id: str,
    generation: int,
    allow_native_empty_text: bool,
) -> bool:
    """Permit live native ``None`` text only while preparing a clear fence."""
    identity = resolution.identity
    return bool(
        allow_native_empty_text
        and resolution.status is CapabilityStatus.UNKNOWN_STATE
        and identity is not None
        and identity.domain == "text"
        and identity.property_key.rsplit(".", maxsplit=1)[-1] in {"name", "pin"}
        and identity.slot == slot
        and identity.keymaster_entry_id == keymaster_entry_id
        and identity.generation == generation
        and resolution.entity_id is not None
    )


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


def _intent_is_current(
    intent: VerifiedOperationIntent, *, allow_native_empty_text: bool = False
) -> bool:
    """Return whether the exact protected identity snapshot still holds."""
    inputs = _current_intent_inputs(intent)
    if inputs is None:
        return False
    return (
        _current_store_snapshot_matches(intent, inputs)
        and _current_selected_entry_matches(intent, inputs)
        and _current_capabilities_match(
            intent,
            inputs.lifecycle,
            allow_native_empty_text=allow_native_empty_text,
        )
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
    intent: VerifiedOperationIntent,
    lifecycle: Any,
    *,
    allow_native_empty_text: bool,
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
            or (
                not resolution.ready
                and not _native_empty_text_is_current(
                    intent, resolution, allow_native_empty_text
                )
            )
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
        if (
            not fresh.ready
            and not _native_empty_text_is_current(
                intent, fresh, allow_native_empty_text
            )
        ) or fresh.entity_id != previous.entity_id:
            return False
    return True


def _native_empty_text_is_current(
    intent: VerifiedOperationIntent,
    resolution: CapabilityResolution,
    allow_native_empty_text: bool,
) -> bool:
    """Accept only exact live native-empty text for set or clear purposes."""
    identity = resolution.identity
    permitted_fence = (
        intent.fence_type == "allocation" and intent.allow_native_empty_text
    ) or (intent.fence_type == "pending_clear" and allow_native_empty_text)
    return bool(
        permitted_fence
        and resolution.status is CapabilityStatus.UNKNOWN_STATE
        and identity is not None
        and identity.domain == "text"
        and identity.property_key.rsplit(".", maxsplit=1)[-1] in {"name", "pin"}
        and identity.slot == intent.slot
        and identity.keymaster_entry_id == intent.keymaster_entry_id
        and identity.generation == intent.lifecycle_generation
    )


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
