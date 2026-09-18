# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Forced re-issue hold helpers for allocator state."""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from . import reissue_state
from .models import AllocationOrigin
from .models import AllocationOwner
from .models import AllocationRecord
from .models import CycleRequest
from .models import ForcedReleaseExemption
from .models import ReissueOutcome

if TYPE_CHECKING:
    from .allocator import DoorCodeAllocator


def forced_release_hold_key(
    identity_key: str, entry_id: str, lockname: str | None, slot: int | None
) -> str:
    """Return the reserved hold identity for one forced-release target."""
    lock_part = lockname if lockname is not None else "none"
    slot_part = str(slot) if slot is not None else "none"
    return f"{identity_key}:reissued:{entry_id}:{lock_part}:{slot_part}"


def is_forced_release_hold(key: str) -> bool:
    """Return whether an identity key is in the forced-release hold namespace."""
    parts = key.split(":")
    return len(parts) == 5 and parts[1] == "reissued"


def release_forced_holds(
    allocator: DoorCodeAllocator, request: CycleRequest
) -> list[ReissueOutcome]:
    """Release this entry's forced-release holds when the guard permits it."""
    outcomes: list[ReissueOutcome] = []
    observations = [request.observation]
    for record in list(allocator._registry.records.values()):
        for owner in list(record.owners):
            if owner.entry_id != request.observation.entry_id:
                continue
            if not is_forced_release_hold(owner.identity_key):
                continue
            exemption = ForcedReleaseExemption(
                record.code, owner.entry_id, owner.identity_key
            )
            reason = allocator._release_guard_reason(
                record, [owner], observations, forced_release=exemption
            )
            if reason is None:
                allocator._registry.release(owner.identity_key)
            disposition = "released" if reason is None else "held_pending_release"
            outcomes.append(
                ReissueOutcome(
                    owner.entry_id,
                    reissue_state.hold_base_identity(owner.identity_key),
                    owner.lockname,
                    owner.slot,
                    record.code_ref,
                    *_replacement_metadata_for_hold(allocator, owner),
                    disposition,
                    reason,
                )
            )
    return outcomes


def merge_release_outcomes(
    outcomes: list[ReissueOutcome], releases: list[ReissueOutcome]
) -> list[ReissueOutcome]:
    """Merge hold release verdicts into the directive outcome rows."""
    merged = list(outcomes)
    for release in releases:
        for index, outcome in enumerate(merged):
            if not _same_target(outcome, release):
                continue
            merged[index] = ReissueOutcome(
                outcome.entry_id,
                outcome.identity_key,
                outcome.lockname,
                outcome.slot,
                outcome.replaced_code_ref,
                outcome.replacement_code_ref,
                outcome.origin,
                release.disposition,
                release.retention_reason,
            )
            break
        else:
            merged.append(release)
    return merged


def forced_hold_matches_slot(
    allocator: DoorCodeAllocator,
    entry_id: str,
    lockname: str,
    slot: int,
) -> bool:
    """Return whether a forced-release hold exists for one physical slot."""
    return any(
        owner.entry_id == entry_id
        and owner.lockname == lockname
        and owner.slot == slot
        and is_forced_release_hold(owner.identity_key)
        for record in allocator._registry.records.values()
        for owner in record.owners
    )


def forced_hold_retention_reason(
    allocator: DoorCodeAllocator,
    record: AllocationRecord,
    owner: AllocationOwner,
    observations: list[Any],
) -> str | None:
    """Return the visible guard reason for a forced-release hold."""
    exemption = ForcedReleaseExemption(record.code, owner.entry_id, owner.identity_key)
    return allocator._release_guard_reason(
        record,
        [owner],
        observations,
        refresh_observed=False,
        forced_release=exemption,
    )


def _replacement_metadata_for_hold(
    allocator: DoorCodeAllocator, owner: AllocationOwner
) -> tuple[str | None, AllocationOrigin | None]:
    """Return the replacement code metadata for an outstanding hold."""
    identity_key = reissue_state.hold_base_identity(owner.identity_key)
    replacement = allocator._registry.record_for_identity(identity_key)
    if replacement is None:
        return None, None
    replacement_owner = next(
        (
            candidate
            for candidate in replacement.owners
            if candidate.identity_key == identity_key
        ),
        None,
    )
    return (
        replacement.code_ref,
        replacement_owner.origin if replacement_owner is not None else None,
    )


def _same_target(first: ReissueOutcome, second: ReissueOutcome) -> bool:
    """Return whether two outcome rows describe the same forced target."""
    return (
        first.entry_id == second.entry_id
        and first.identity_key == second.identity_key
        and first.lockname == second.lockname
        and first.slot == second.slot
        and first.replaced_code_ref == second.replaced_code_ref
    )
