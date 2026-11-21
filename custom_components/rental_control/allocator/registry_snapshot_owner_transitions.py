# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Owner-location transitions for dormant protected registry snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
import secrets
from typing import Any

from homeassistant.core import HomeAssistant

from ..const import DOMAIN
from .association_references import ledger_owner_reference
from .models import ProtectionLoadResult
from .models import ProtectionLoadState
from .registry_safety_models import resolve_fence_to_tombstone
from .registry_safety_models import validate_pending_fence


@dataclass(frozen=True, slots=True)
class OwnerTransitionLock:
    """One exact original-Keymaster slot affected by an owner release."""

    rc_entry_id: str
    slot: int
    keymaster_entry_id: str
    pending: dict[int, int] | None = None


def owner_transition_slots(
    allocator: Any,
    prior_data: dict[str, Any],
    target_data: dict[str, Any],
) -> list[OwnerTransitionLock] | ProtectionLoadResult:
    """Lock each released or moved owner's exact original allocation slot."""
    try:
        prior = ledger_owner_references(prior_data)
        target = ledger_owner_references(target_data)
    except Exception:
        return _unavailable("owner_transition_invalid")
    released = prior.keys() - target.keys()
    prior_by_identity = {(item[0], item[1]): ref for ref, item in prior.items()}
    target_by_identity = {(item[0], item[1]): ref for ref, item in target.items()}
    moved = prior_by_identity.keys() & target_by_identity.keys()
    old_refs = {
        prior_by_identity[key]
        for key in moved
        if prior_by_identity[key] != target_by_identity[key]
    }
    new_refs = {
        target_by_identity[key]
        for key in moved
        if prior_by_identity[key] != target_by_identity[key]
    }
    if not released and not old_refs:
        return []
    hass = getattr(allocator, "hass", None)
    if not isinstance(hass, HomeAssistant):
        return []
    domain_data = hass.data.get(DOMAIN)
    if not isinstance(domain_data, dict):
        domain_data = {}
    prior_fences = prior_data.get("pending_fences")
    target_fences = target_data.get("pending_fences")
    if not isinstance(prior_fences, list) or not isinstance(target_fences, list):
        return _unavailable("owner_transition_invalid")
    locks: dict[tuple[str, int, str], OwnerTransitionLock] = {}
    for fence in prior_fences:
        if _is_transition_fence(fence, released | old_refs):
            result = _add_transition_lock(locks, domain_data, fence)
            if result is not None:
                return result
    for fence in target_fences:
        if _is_transition_fence(fence, new_refs):
            result = _add_transition_lock(locks, domain_data, fence)
            if result is not None:
                return result
    return [locks[key] for key in sorted(locks)]


def _is_transition_fence(fence: Any, references: set[str]) -> bool:
    """Return whether a valid allocation fence belongs to a transitioning owner."""
    return (
        validate_pending_fence(fence)
        and fence["fence_type"] == "allocation"
        and fence["owner_ref"] in references
    )


def _add_transition_lock(
    locks: dict[tuple[str, int, str], OwnerTransitionLock],
    domain_data: dict[str, Any],
    fence: dict[str, Any],
) -> ProtectionLoadResult | None:
    """Add the fence's exact slot lock, rejecting malformed pending metadata."""
    entry_data = domain_data.get(fence["rc_entry_id"])
    coordinator = (
        entry_data.get("coordinator") if isinstance(entry_data, dict) else None
    )
    pending = getattr(
        getattr(coordinator, "event_overrides", None),
        "_slot_operations_pending",
        None,
    )
    if pending is not None and not isinstance(pending, dict):
        return _unavailable("owner_transition_lock_unavailable")
    key = (
        fence["original_km_entry_id"],
        fence["slot"],
        fence["rc_entry_id"],
    )
    locks.setdefault(
        key,
        OwnerTransitionLock(
            fence["rc_entry_id"],
            fence["slot"],
            fence["original_km_entry_id"],
            pending,
        ),
    )
    return None


def resolve_released_owner_fences(
    prior_data: dict[str, Any],
    target_data: dict[str, Any],
    *,
    transaction_id: str,
    generation: int,
) -> None:
    """Tombstone allocation fences only when their exact owner leaves ledger."""
    prior = ledger_owner_references(prior_data)
    target = ledger_owner_references(target_data)
    released = prior.keys() - target.keys()
    if not released:
        return
    fences = target_data.get("pending_fences")
    tombstones = target_data.get("tombstones")
    if not isinstance(fences, list) or not isinstance(tombstones, list):
        raise ValueError("invalid safety history")
    existing_ids = {
        tombstone.get("fence_id")
        for tombstone in tombstones
        if isinstance(tombstone, dict)
    }
    remaining: list[dict[str, Any]] = []
    retained_at = datetime.now(UTC).isoformat()
    for fence in fences:
        if not _is_transition_fence(fence, released):
            remaining.append(fence)
            continue
        if fence["fence_id"] in existing_ids:
            raise ValueError("allocation fence already resolved")
        tombstones.append(
            resolve_fence_to_tombstone(
                fence,
                tombstone_id=secrets.token_hex(16),
                resolved_transaction_id=transaction_id,
                resolved_generation=generation,
                resolution_kind="owner_released",
                retained_at=retained_at,
            )
        )
    target_data["pending_fences"] = remaining


def ledger_owner_references(data: dict[str, Any]) -> dict[str, tuple[str, str, int]]:
    """Map exact serialized owner references to their current ledger location."""
    ledger = data.get("ledger")
    records = ledger.get("records") if isinstance(ledger, dict) else None
    if not isinstance(records, list):
        raise ValueError("invalid ledger")
    owners: dict[str, tuple[str, str, int]] = {}
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("owners"), list):
            raise ValueError("invalid ledger owner")
        for owner in record["owners"]:
            if not isinstance(owner, dict):
                raise ValueError("invalid ledger owner")
            reference = ledger_owner_reference(data, record, owner)
            slot = owner.get("slot")
            if (
                reference is None
                or reference in owners
                or not isinstance(owner.get("entry_id"), str)
                or not isinstance(owner.get("identity_key"), str)
                or not isinstance(slot, int)
                or isinstance(slot, bool)
            ):
                raise ValueError("invalid ledger owner reference")
            owners[reference] = (
                owner["entry_id"],
                owner["identity_key"],
                slot,
            )
    return owners


def _unavailable(code: str) -> ProtectionLoadResult:
    """Create a sanitized unavailable result with one fixed failure code."""
    return ProtectionLoadResult(ProtectionLoadState.UNAVAILABLE, failure_code=code)
