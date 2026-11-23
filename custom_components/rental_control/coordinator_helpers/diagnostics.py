# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Read-only diagnostics projection helpers.

The coordinator shell owns the latest desired plan and the event-override
diagnostics snapshot. This module combines and scrubs those projections
and summarizes current Home Assistant status. Raw PIN / slot-code values
are always removed before diagnostics leave the coordinator.
"""

from __future__ import annotations

from collections import Counter
from typing import Any
from typing import cast

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from ..allocator.associations import AssociationMode
from ..allocator.associations import AssociationTuple
from ..allocator.models import ProtectionAuthorizationState
from ..const import DOMAIN
from ..const import PROTECTED_REGISTRY_STORE
from ..helpers import configured_lock_entry
from ..keymaster_identity import CapabilityStatus
from ..keymaster_lifecycle import get_keymaster_lifecycle
from ..keymaster_version import KeymasterVersionStatus
from ..keymaster_version import diagnose_keymaster_version
from ..reconciliation import DesiredPlan

_CODE_KEYS = frozenset({"slot_code", "pin", "code"})


def scrub_codes(value: Any) -> Any:
    """Return a copy of *value* with raw code-bearing keys removed."""
    if isinstance(value, dict):
        return {
            key: scrub_codes(item)
            for key, item in value.items()
            if key not in _CODE_KEYS
        }
    if isinstance(value, list):
        return [scrub_codes(item) for item in value]
    return value


def build_reconciliation_diagnostics(
    plan: DesiredPlan | None,
    event_overrides_snapshot: dict[str, Any] | None,
    allocator_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the combined, scrubbed reconciliation diagnostics snapshot.

    Args:
        plan: The most recently computed desired plan, or ``None``.
        event_overrides_snapshot: The :class:`EventOverrides` diagnostics
            snapshot, or ``None`` when overrides are not configured.
        allocator_snapshot: The shared allocator diagnostics snapshot, or
            ``None`` when the allocator is not available.

    Returns:
        Combined diagnostics dict with raw codes scrubbed. When no plan
        has been computed, this contains only the available event-override
        diagnostics snapshot or is empty.
    """
    result: dict[str, Any] = {}
    if plan is not None:
        result.update(plan.diagnostics)
    if event_overrides_snapshot is not None:
        result["event_overrides"] = event_overrides_snapshot
    if allocator_snapshot is not None:
        result["allocator"] = allocator_snapshot
    return cast("dict[str, Any]", scrub_codes(result))


def keymaster_status_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return separate allowlisted association, capability, version, and store status."""
    return {
        "association": _association_status_diagnostics(hass, entry),
        "capabilities": _capability_status_diagnostics(hass, entry),
        "version": _version_status_diagnostics(hass),
        "protection": _protection_status_diagnostics(hass),
    }


def _protected_store_for(hass: HomeAssistant) -> Any | None:
    """Return the already-created protected registry store."""
    domain_data = hass.data.get(DOMAIN)
    if not isinstance(domain_data, dict):
        return None
    return domain_data.get(PROTECTED_REGISTRY_STORE)


def _association_status_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, str]:
    """Return a sanitized association status and a fixed next step."""
    association = AssociationTuple.from_config(entry.data)
    store = _protected_store_for(hass)
    if association is None:
        return {"status": "invalid", "next_step": "review_association_and_repair"}
    if association.mode is AssociationMode.EXPLICIT_LOCKLESS:
        return {
            "status": association.mode.value,
            "next_step": "calendar_only_no_lock_operations",
        }
    if association.mode is AssociationMode.LEGACY_UNRESOLVED:
        return {
            "status": association.mode.value,
            "next_step": "select_keymaster_entry",
        }
    if association.mode is not AssociationMode.VERIFIED:
        return {
            "status": association.mode.value,
            "next_step": "review_association_and_repair",
        }
    if configured_lock_entry(association.legacy_lock_selection_raw) is None:
        return {
            "status": "verified_selector_missing",
            "next_step": "restore_legacy_lock_selector",
        }
    reader = getattr(store, "association_for_entry", None)
    try:
        acknowledged = callable(reader) and reader(entry.entry_id) == association
    except Exception:
        acknowledged = False
    if not acknowledged:
        return {
            "status": "awaiting_protected_confirmation",
            "next_step": "wait_for_protected_registry_confirmation",
        }
    return {"status": "verified", "next_step": "wait_for_keymaster_entities"}


def _capability_status_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return aggregate capability status counts and fixed repair guidance."""
    lifecycle = get_keymaster_lifecycle(hass, entry.entry_id)
    if lifecycle is None:
        return {
            "status_counts": {},
            "next_step": "wait_for_selected_keymaster",
        }
    resolutions = lifecycle.current
    if not resolutions:
        return {
            "status_counts": {},
            "next_step": "wait_for_live_entity_snapshot",
        }
    counts = Counter(result.status.value for result in resolutions.values())
    status_counts = dict(sorted(counts.items()))
    blocked_statuses = (
        status
        for status, count in status_counts.items()
        if count and status != CapabilityStatus.READY.value
    )
    next_step = next(
        (_capability_action(status) for status in blocked_statuses),
        "none",
    )
    return {"status_counts": status_counts, "next_step": next_step}


def _version_status_diagnostics(hass: HomeAssistant) -> dict[str, Any]:
    """Project the loaded-only version status as informational evidence."""
    version = diagnose_keymaster_version(hass)
    next_step = {
        KeymasterVersionStatus.SUPPORTED: "none",
        KeymasterVersionStatus.DEVELOPMENT: "verify_development_build",
        KeymasterVersionStatus.OUTSIDE_SUPPORT: (
            "consider_supported_keymaster_release"
        ),
        KeymasterVersionStatus.UNKNOWN: "verify_loaded_keymaster_version",
    }[version.status]
    return {
        "status": version.status.value,
        "version": version.version,
        "support_floor": version.support_floor,
        "reason": version.reason,
        "informational_only": True,
        "next_step": next_step,
    }


def _protection_status_diagnostics(hass: HomeAssistant) -> dict[str, str]:
    """Return the protected-store authorization state and safe operator action."""
    store = _protected_store_for(hass)
    protection = getattr(store, "authorization_state", None)
    protection_status = getattr(protection, "value", None)
    if not isinstance(protection_status, str):
        protection_status = "unavailable"
    if protection_status == ProtectionAuthorizationState.PROTECTIVE_UNKNOWN.value:
        protection_action = "preserve_files_no_supported_restore_or_override"
    elif protection_status == ProtectionAuthorizationState.READY.value:
        protection_action = "none"
    elif store is None:
        protection_action = "wait_for_registry_initialization"
    else:
        protection_action = "wait_for_registry_verification"
    return {"status": protection_status, "next_step": protection_action}


def _capability_action(status: str) -> str:
    """Map only known capability outcomes to fixed operator guidance."""
    return {
        CapabilityStatus.MISSING.value: "restore_or_reselect_keymaster_entity",
        CapabilityStatus.AMBIGUOUS.value: "resolve_duplicate_keymaster_entities",
        CapabilityStatus.DISABLED.value: "enable_required_keymaster_entity",
        CapabilityStatus.STATE_MISSING.value: "wait_for_live_keymaster_state",
        CapabilityStatus.RESTORED.value: "wait_for_live_keymaster_state",
        CapabilityStatus.UNAVAILABLE.value: "restore_keymaster_entity_availability",
        CapabilityStatus.UNKNOWN_STATE.value: "wait_for_live_keymaster_state",
        CapabilityStatus.MASKED.value: "unmask_required_keymaster_state",
        CapabilityStatus.WRONG_DOMAIN.value: "repair_keymaster_entity_registry",
        CapabilityStatus.WRONG_PLATFORM.value: "repair_keymaster_entity_registry",
        CapabilityStatus.WRONG_CONFIG_ENTRY.value: "reselect_keymaster_entry",
        CapabilityStatus.KEYMASTER_NOT_FOUND.value: "reload_or_reselect_keymaster",
        CapabilityStatus.KEYMASTER_NOT_LOADED.value: "load_selected_keymaster_entry",
        CapabilityStatus.INHERITANCE_UNVERIFIED.value: "verify_child_inheritance",
        CapabilityStatus.INHERITED_UNAVAILABLE.value: "enable_child_override_or_parent",
        CapabilityStatus.INVALID_IDENTITY.value: "repair_keymaster_entity_registry",
    }.get(status, "inspect_keymaster_capability_status")
