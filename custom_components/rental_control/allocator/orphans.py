# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Orphan cleanup helpers for shared door-code allocations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import reissue
from .models import AllocationOwner
from .models import AllocationRecord
from .models import CycleObservation
from .models import OrphanCleanupReport
from .models import OrphanOutcome

if TYPE_CHECKING:
    from .allocator import DoorCodeAllocator


def clear_orphans(
    allocator: DoorCodeAllocator,
    known_entry_ids: set[str],
    observations: list[CycleObservation],
    *,
    dry_run: bool,
    force_reissued_holds: bool,
) -> OrphanCleanupReport:
    """Clear orphaned owners and explicitly reclaimed forced holds."""
    cleared = []
    retained = []
    for record in list(allocator._registry.records.values()):
        owners = _cleanup_candidates(
            record.owners, known_entry_ids, force_reissued_holds
        )
        for owner in owners:
            force_reclaim = _is_forced_reclaim(
                owner, known_entry_ids, force_reissued_holds
            )
            if force_reclaim:
                reason = reissue.forced_hold_retention_reason(
                    allocator, record, owner, observations
                )
                item = build_outcome(record, owner, reason)
                cleared.append(item)
                if not dry_run:
                    allocator._registry.release(owner.identity_key)
                continue
            reason = allocator._release_guard_reason(
                record,
                [owner],
                observations,
                refresh_observed=not dry_run,
            )
            item = build_outcome(record, owner, reason)
            if reason is None:
                cleared.append(item)
                if not dry_run:
                    allocator._registry.release(owner.identity_key)
            else:
                retained.append(item)
    return OrphanCleanupReport(dry_run=dry_run, cleared=cleared, retained=retained)


def _cleanup_candidates(
    owners: list[AllocationOwner], known_entry_ids: set[str], force_reissued_holds: bool
) -> list[AllocationOwner]:
    """Return ordinary orphans plus explicitly requested live hold owners."""
    candidates = [
        owner for owner in list(owners) if owner.entry_id not in known_entry_ids
    ]
    if force_reissued_holds:
        candidates.extend(
            owner
            for owner in list(owners)
            if owner.entry_id in known_entry_ids
            and reissue.is_forced_release_hold(owner.identity_key)
        )
    return candidates


def _is_forced_reclaim(
    owner: AllocationOwner, known_entry_ids: set[str], force_reissued_holds: bool
) -> bool:
    """Return whether the operator override applies to one owner."""
    return (
        force_reissued_holds
        and owner.entry_id in known_entry_ids
        and reissue.is_forced_release_hold(owner.identity_key)
    )


def build_outcome(
    record: AllocationRecord, owner: AllocationOwner, reason: str | None
) -> OrphanOutcome:
    """Build a release or cleanup report row."""
    return OrphanOutcome(
        code_ref=record.code_ref,
        entry_id=owner.entry_id,
        identity_key=owner.identity_key,
        reason=reason,
        lockname=owner.lockname,
        slot=owner.slot,
    )
