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
from . import entry_info
from . import issuance
from . import reissue
from .models import AdoptionRequest
from .models import AllocationOrigin
from .models import AllocationOwner
from .models import AllocationRecord
from .models import AllocationResult

if TYPE_CHECKING:
    from .allocator import DoorCodeAllocator

_LOGGER = logging.getLogger(__name__)
_CONFLICT_NOTIFICATION_ID = f"{DOMAIN}_code_registry_conflict"


def conflict_notification_id(code_ref: str) -> str:
    """Return the persistent notification id for one code's conflict.

    Each conflicting code gets its own notification. A single shared id
    let one code's report overwrite another's, so only the newest was
    ever visible and dismissing it hid every other open conflict.
    """
    return f"{_CONFLICT_NOTIFICATION_ID}_{code_ref}"


def adopt_unlocked(
    allocator: DoorCodeAllocator,
    request: AdoptionRequest,
) -> AllocationResult:
    """Adopt an observed code while the allocator lock is already held."""
    now = datetime.now(UTC).isoformat()
    _coalesce_fingerprint_owner(allocator, request)
    existing = allocator._registry.record_for_identity(request.identity_key)
    if existing is not None and existing.code != request.code:
        if reissue.forced_hold_matches_slot(
            allocator, request.entry_id, request.lockname, request.slot
        ):
            _LOGGER.info(
                "Skipped observed code_ref %s for %s:%s at %s:%s while a "
                "forced re-issue hold is pending",
                allocator.code_ref(request.code),
                request.entry_id,
                request.identity_key,
                request.lockname,
                request.slot,
            )
            owner = next(
                owner
                for owner in existing.owners
                if owner.identity_key == request.identity_key
            )
            return AllocationResult(
                code=existing.code,
                origin=owner.origin,
                reason="reissue_pending",
            )
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

    _release_observed_alias(allocator, request)
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


def reserve_observed_code(
    allocator: DoorCodeAllocator,
    request: AdoptionRequest,
) -> None:
    """Reserve a suppressed slot's observed code under an alias identity.

    The code is recorded at its own length so that it is unavailable to
    every entry sharing the lock, without assigning it to any
    reservation. A code that already has a registry record is left alone,
    because an existing record already keeps it out of issuance and
    adding an owner would create a spurious adoption conflict.
    """
    if not request.code.isdecimal():
        return
    alias_key = issuance.observed_alias_key(request)
    _release_moved_observed_alias(allocator, request)
    record = allocator._registry.records.get(request.code)
    if record is not None and not any(
        owner.identity_key == alias_key for owner in record.owners
    ):
        return
    now = datetime.now(UTC).isoformat()
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
    reserved = allocator._registry.add_owner(
        request.code,
        request.code_length,
        owner,
        allocator.code_ref(request.code),
        now,
    )
    if record is None:
        _LOGGER.info(
            "Reserved observed code_ref %s for %s at %s:%s because its "
            "slot is exempt from adoption this cycle",
            reserved.code_ref,
            request.entry_id,
            request.lockname,
            request.slot,
        )


def _record_mismatched_observed_owner(
    allocator: DoorCodeAllocator,
    request: AdoptionRequest,
    observed_ref: str,
    now: str,
) -> None:
    """Record observed-code ownership without moving the primary identity."""
    alias_key = issuance.observed_alias_key(request)
    _release_moved_observed_alias(allocator, request)
    _release_historical_slot_aliases(allocator, request)
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


def _release_historical_slot_aliases(
    allocator: DoorCodeAllocator,
    request: AdoptionRequest,
) -> None:
    """Remove earlier fingerprints' aliases for this physical slot.

    Only one identity can stand for a slot at a time, so an alias left
    by a prior fingerprint of the same booking is stale. Dropping it
    keeps the two from landing on one record, which would read as an
    adoption conflict and block the code from ever being reclaimed.
    """
    for historical_key in request.fingerprint_history:
        alias_key = issuance.observed_alias_key(request, identity_key=historical_key)
        if allocator._registry.code_for_identity(alias_key) is not None:
            allocator._registry.release(alias_key)


def _observed_alias_keys(
    allocator: DoorCodeAllocator,
    request: AdoptionRequest,
    identity_key: str,
) -> list[str]:
    """Return one identity's observed aliases on this entry's lock."""
    prefix = f"{identity_key}:observed:{request.entry_id}:{request.lockname}:"
    return [key for key in allocator._registry.by_identity if key.startswith(prefix)]


def _release_observed_alias(
    allocator: DoorCodeAllocator,
    request: AdoptionRequest,
) -> None:
    """Remove the aliases this adoption makes redundant.

    An alias is a placeholder for a code the booking holds physically
    but does not own. Adoption records that ownership properly, so a
    placeholder for the same slot must give way even when it names the
    same code, and so must one that names the adopted code from another
    slot the code has since moved away from. Leaving either on the
    record would read as an adoption conflict and block the release of
    the code for good. Nothing is lost by releasing them, because the
    new owner is retained for as long as the lock reports its code
    anywhere in this entry's managed slots.

    Earlier fingerprints of the same booking leave placeholders too, and
    ``_coalesce_fingerprint_owner`` only clears them when the historical
    identity also held a real allocation. Sweep the whole history here so
    a suppressed slot that changes fingerprint before adoption resumes
    cannot leave a stale placeholder behind.
    """
    this_slot_suffix = f":{request.slot}"
    for identity_key in (request.identity_key, *request.fingerprint_history):
        for alias_key in _observed_alias_keys(allocator, request, identity_key):
            if alias_key.endswith(this_slot_suffix) or (
                allocator._registry.code_for_identity(alias_key) == request.code
            ):
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


def _conflict_is_expected(
    allocator: DoorCodeAllocator,
    record: AllocationRecord,
) -> bool:
    """Return whether every owner of a shared code generates last_four.

    Under the ``last_four`` generator the code *is* the guest's last
    four phone digits, so two owners holding one code means one phone
    number, which in practice means one guest moving between units at
    the same property. That is the generator working as specified, and
    the report says "no code was rotated" because there is nothing to
    act on, so it is suppressed to an INFO log line.

    Suppression requires *every* owner's entry to use ``last_four``.
    A record shared between a ``last_four`` entry and a ``date_based``
    one is not explained by the generator: the date_based entry picked
    that code from the shared code space, which is the allocator
    problem the notification exists to surface, so the mixed case still
    notifies. An entry whose generator cannot be determined at all -
    deleted, unloaded or carrying no configuration - counts as "not
    known to be last_four", so an undeterminable owner also notifies.
    Silence is only ever chosen on positive evidence.
    """
    return bool(record.owners) and all(
        entry_info.uses_last_four(allocator.hass, owner.entry_id)
        for owner in record.owners
    )


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
    if _conflict_is_expected(allocator, record):
        _LOGGER.info(
            "Shared code_ref %s has multiple owners (%s), which is expected "
            "under the last_four generator because the code is the guest's "
            "phone digits. No code was rotated and no notification was "
            "raised.",
            record.code_ref,
            owners,
        )
        return
    _LOGGER.warning(message)
    _notify(
        allocator,
        message,
        title=f"{NAME} code registry conflict",
        notification_id=conflict_notification_id(record.code_ref),
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
