# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Adoption helpers for the shared door-code allocator."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
import logging
import sys
from typing import TYPE_CHECKING

from homeassistant.components.persistent_notification import async_create

from ..const import DOMAIN
from ..const import NAME
from . import issuance
from .models import AdoptionRequest
from .models import AllocationOrigin
from .models import AllocationOwner
from .models import AllocationRecord
from .models import AllocationResult

if TYPE_CHECKING:
    from .allocator import DoorCodeAllocator

_LOGGER = logging.getLogger(__name__)
_CONFLICT_NOTIFICATION_ID = f"{DOMAIN}_code_registry_conflict"


def adopt_unlocked(
    allocator: DoorCodeAllocator,
    request: AdoptionRequest,
) -> AllocationResult:
    """Adopt an observed code while the allocator lock is already held."""
    now = datetime.now(UTC).isoformat()
    _coalesce_fingerprint_owner(allocator, request)
    existing = allocator._registry.record_for_identity(request.identity_key)
    if existing is not None and existing.code != request.code:
        observed_ref = allocator.code_ref(request.code)
        _LOGGER.warning(
            "Identity %s already owns code_ref %s; observed code_ref %s "
            "will be retained on the lock and not rotated",
            request.identity_key,
            existing.code_ref,
            observed_ref,
        )
        _record_mismatched_observed_owner(allocator, request, observed_ref, now)
        _report_identity_mismatch(allocator, request, existing, observed_ref)
        return AllocationResult(
            code=request.code,
            origin=AllocationOrigin.ADOPTED,
            reason="identity_code_mismatch",
        )

    _release_moved_observed_alias(allocator, request)
    owner = AllocationOwner(
        entry_id=request.entry_id,
        identity_key=request.identity_key,
        origin=AllocationOrigin.ADOPTED,
        lockname=request.lockname,
        slot=request.slot,
        lock_observed=True,
        first_seen=now,
        last_seen=now,
    )
    before = allocator._registry.records.get(request.code)
    before_identities = (
        {owner.identity_key for owner in before.owners} if before is not None else set()
    )
    record = allocator._registry.add_owner(
        request.code,
        request.code_length,
        owner,
        allocator.code_ref(request.code),
        now,
    )
    if (
        before is not None
        and request.identity_key not in before_identities
        and len(record.owners) > 1
    ):
        _report_adoption_conflict(allocator, record)
    _LOGGER.info(
        "Adopted shared allocation code_ref %s for %s:%s at %s:%s",
        record.code_ref,
        request.entry_id,
        request.identity_key,
        request.lockname,
        request.slot,
    )
    return AllocationResult(code=request.code, origin=AllocationOrigin.ADOPTED)


def _record_mismatched_observed_owner(
    allocator: DoorCodeAllocator,
    request: AdoptionRequest,
    observed_ref: str,
    now: str,
) -> None:
    """Record observed-code ownership without moving the primary identity."""
    alias_key = issuance.observed_alias_key(request)
    _release_moved_observed_alias(allocator, request)
    owner = AllocationOwner(
        entry_id=request.entry_id,
        identity_key=alias_key,
        origin=AllocationOrigin.ADOPTED,
        lockname=request.lockname,
        slot=request.slot,
        lock_observed=True,
        first_seen=now,
        last_seen=now,
    )
    before = allocator._registry.records.get(request.code)
    before_identities = (
        {owner.identity_key for owner in before.owners} if before is not None else set()
    )
    record = allocator._registry.add_owner(
        request.code, request.code_length, owner, observed_ref, now
    )
    if before is not None and alias_key not in before_identities:
        if len(record.owners) > 1:
            _report_adoption_conflict(allocator, record)


def _release_moved_observed_alias(
    allocator: DoorCodeAllocator,
    request: AdoptionRequest,
) -> None:
    """Remove this slot's observed alias when it points at another code."""
    alias_key = issuance.observed_alias_key(request)
    owned_code = allocator._registry.code_for_identity(alias_key)
    if owned_code is not None and owned_code != request.code:
        allocator._registry.release(alias_key)


def _coalesce_fingerprint_owner(
    allocator: DoorCodeAllocator,
    request: AdoptionRequest,
) -> None:
    """Re-key a historical owner before adoption conflict detection."""
    if not request.fingerprint_history:
        return
    if request.identity_key in allocator._registry.by_identity:
        return
    for historical_key in request.fingerprint_history:
        code = allocator._registry.by_identity.get(historical_key)
        if code is None:
            continue
        record = allocator._registry.records.get(code)
        if record is None:
            allocator._registry.by_identity.pop(historical_key, None)
            continue
        _release_historical_observed_alias(allocator, request, historical_key)
        _rekey_historical_owner(
            allocator, record, historical_key, request.identity_key, request.code
        )
        return


def _rekey_historical_owner(
    allocator: DoorCodeAllocator,
    record: AllocationRecord,
    historical_key: str,
    identity_key: str,
    new_code: str,
) -> None:
    """Move or remove one historical owner while preserving other owners."""
    for owner in list(record.owners):
        if owner.identity_key != historical_key:
            continue
        allocator._registry.by_identity.pop(historical_key, None)
        if record.code == new_code:
            owner.identity_key = identity_key
            allocator._registry.by_identity[identity_key] = record.code
            return
        record.owners.remove(owner)
        if not record.owners:
            allocator._registry.records.pop(record.code, None)
        return


def _release_historical_observed_alias(
    allocator: DoorCodeAllocator,
    request: AdoptionRequest,
    historical_key: str,
) -> None:
    """Remove a historical identity's observed alias for this physical slot."""
    alias_key = issuance.observed_alias_key(request, identity_key=historical_key)
    if allocator._registry.code_for_identity(alias_key) is not None:
        allocator._registry.release(alias_key)


def _report_adoption_conflict(
    allocator: DoorCodeAllocator,
    record: AllocationRecord,
) -> None:
    """Log and notify that an observed code has multiple owners."""
    owners = ", ".join(
        f"{owner.entry_id}:{owner.identity_key}@{owner.lockname}:{owner.slot}"
        for owner in record.owners
    )
    message = (
        f"Shared code registry adoption conflict for code_ref "
        f"{record.code_ref}: {owners}. No code was rotated."
    )
    _LOGGER.warning(message)
    _notify(
        allocator,
        message,
        title=f"{NAME} code registry conflict",
        notification_id=_CONFLICT_NOTIFICATION_ID,
    )


def _report_identity_mismatch(
    allocator: DoorCodeAllocator,
    request: AdoptionRequest,
    existing: AllocationRecord,
    observed_ref: str,
) -> None:
    """Log and notify that one identity has conflicting observed code refs."""
    mismatch = (request.entry_id, request.identity_key, observed_ref)
    if mismatch in allocator._reported_identity_mismatches:
        return
    allocator._reported_identity_mismatches.add(mismatch)
    message = (
        f"Shared code registry identity mismatch for {request.entry_id}:"
        f"{request.identity_key}: registry code_ref {existing.code_ref}, "
        f"observed code_ref {observed_ref}. The observed lock code was "
        "retained and no code was rotated."
    )
    _LOGGER.warning(message)
    _notify(
        allocator,
        message,
        title=f"{NAME} code registry identity mismatch",
        notification_id=f"{_CONFLICT_NOTIFICATION_ID}_identity",
    )


def _notify(allocator: DoorCodeAllocator, message: str, **kwargs: str) -> None:
    """Create a notification, honoring tests patched on the allocator module."""
    allocator_module = sys.modules.get(
        "custom_components.rental_control.allocator.allocator"
    )
    create = getattr(allocator_module, "async_create", async_create)
    create(allocator.hass, message, **kwargs)
