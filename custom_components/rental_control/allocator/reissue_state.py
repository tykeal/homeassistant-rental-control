# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Internal state helpers for forced door-code re-issue."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
from datetime import UTC
from datetime import datetime
from typing import TYPE_CHECKING
from typing import Callable

from .models import AllocationOwner
from .models import AllocationRecord
from .models import ForcedReissueDirective

if TYPE_CHECKING:
    from .allocator import DoorCodeAllocator
    from .models import ReissueOutcome


@dataclass(frozen=True, slots=True)
class RemovedOwner:
    """Track a stale owner removed while staging an alias-backed hold."""

    record: AllocationRecord
    owner: AllocationOwner


@dataclass(frozen=True, slots=True)
class StagedReissue:
    """Track one owner re-homed during the current locked cycle."""

    entry_id: str
    identity_key: str | None
    allocation_identity_key: str | None
    original_identity_key: str
    hold_key: str
    lockname: str | None
    slot: int | None
    replaced_code_ref: str
    removed_owners: tuple[RemovedOwner, ...] = ()


def select_reissue_owner(
    allocator: DoorCodeAllocator,
    directive: ForcedReissueDirective,
    rekeys: list[tuple[str, str]],
) -> tuple[AllocationRecord | None, AllocationOwner | None]:
    """Select the physical owner to retain as the release hold."""
    primary_record = None
    primary_owner = None
    historical_key = None
    if directive.identity_key is not None:
        primary_record = allocator._registry.record_for_identity(directive.identity_key)
        if primary_record is None:
            historical_key = _historical_key_for_directive(directive, rekeys)
            if historical_key is not None:
                primary_record = allocator._registry.record_for_identity(historical_key)
        if primary_record is not None:
            primary_owner = owner_for_identity(
                primary_record,
                directive.identity_key,
            ) or (
                owner_for_identity(primary_record, historical_key)
                if historical_key is not None
                else None
            )
    if directive.identity_key is not None and primary_record is None:
        return None, None
    alias_record, alias_owner = find_observed_alias(allocator, directive)
    if alias_record is not None and alias_owner is not None:
        return alias_record, alias_owner
    if primary_record is not None and primary_owner is not None:
        return primary_record, primary_owner
    if directive.identity_key is not None:
        return None, None
    return find_slot_owner(allocator, directive)


def existing_hold_for_directive(
    allocator: DoorCodeAllocator,
    directive: ForcedReissueDirective,
    hold_predicate: Callable[[str], bool],
    hold_key_factory: Callable[[str, str, str | None, int | None], str],
) -> tuple[AllocationRecord | None, AllocationOwner | None]:
    """Find an outstanding forced-release hold for a directive."""
    expected = (
        hold_key_factory(
            directive.identity_key,
            directive.entry_id,
            directive.lockname,
            directive.slot,
        )
        if directive.identity_key is not None
        else None
    )
    for record in allocator._registry.records.values():
        for owner in record.owners:
            if owner.entry_id != directive.entry_id or not hold_predicate(
                owner.identity_key
            ):
                continue
            if expected is not None and owner.identity_key == expected:
                return record, owner
            if (
                expected is None
                and owner.lockname == directive.lockname
                and owner.slot == directive.slot
            ):
                return record, owner
    return None, None


def _historical_key_for_directive(
    directive: ForcedReissueDirective, rekeys: list[tuple[str, str]]
) -> str | None:
    """Return a historical key being rekeyed onto the directive identity."""
    if directive.identity_key is None:
        return None
    return next(
        (old_key for old_key, new_key in rekeys if new_key == directive.identity_key),
        None,
    )


def owner_for_identity(
    record: AllocationRecord, identity_key: str
) -> AllocationOwner | None:
    """Return the owner with the requested identity from one record."""
    return next(
        (owner for owner in record.owners if owner.identity_key == identity_key), None
    )


def find_observed_alias(
    allocator: DoorCodeAllocator,
    directive: ForcedReissueDirective,
) -> tuple[AllocationRecord | None, AllocationOwner | None]:
    """Find an observed alias for the directive's physical slot."""
    for record in allocator._registry.records.values():
        for owner in record.owners:
            if is_matching_observed_alias(owner, directive):
                return record, owner
    return None, None


def find_slot_owner(
    allocator: DoorCodeAllocator,
    directive: ForcedReissueDirective,
) -> tuple[AllocationRecord | None, AllocationOwner | None]:
    """Find an existing owner for a bare lock and slot target."""
    for record in allocator._registry.records.values():
        for owner in record.owners:
            if is_structural_hold(owner.identity_key):
                continue
            if (
                owner.entry_id == directive.entry_id
                and owner.lockname == directive.lockname
                and owner.slot == directive.slot
            ):
                return record, owner
    return None, None


def is_matching_observed_alias(
    owner: AllocationOwner, directive: ForcedReissueDirective
) -> bool:
    """Return whether an owner is an observed alias for the target slot."""
    parts = owner.identity_key.split(":")
    if len(parts) != 5 or parts[1] != "observed":
        return False
    return (
        owner.entry_id == directive.entry_id
        and owner.lockname == directive.lockname
        and owner.slot == directive.slot
        and parts[2] == directive.entry_id
        and parts[3] == (directive.lockname or "None")
        and parts[4] == str(directive.slot)
    )


def is_structural_hold(identity_key: str) -> bool:
    """Return whether an identity key is shaped as a forced-release hold."""
    parts = identity_key.split(":")
    return len(parts) == 5 and parts[1] == "reissued"


def remove_stale_target_owner(
    allocator: DoorCodeAllocator,
    directive: ForcedReissueDirective,
    retained_owner: AllocationOwner,
) -> list[RemovedOwner]:
    """Remove a stale primary target owner collapsed into a held alias."""
    if directive.identity_key is None:
        return []
    stale_code = allocator._registry.by_identity.get(directive.identity_key)
    if stale_code is None:
        return []
    stale_record = allocator._registry.records.get(stale_code)
    if stale_record is None:
        allocator._registry.by_identity.pop(directive.identity_key, None)
        return []
    for owner in list(stale_record.owners):
        if owner.identity_key != directive.identity_key or owner is retained_owner:
            continue
        stale_record.owners.remove(owner)
        allocator._registry.by_identity.pop(directive.identity_key, None)
        stale_record.updated_at = datetime.now(UTC).isoformat()
        return [RemovedOwner(record=stale_record, owner=replace(owner))]
    return []


def restore_staged_owner(allocator: DoorCodeAllocator, stage: StagedReissue) -> None:
    """Move a staged hold owner back to its original identity."""
    record = allocator._registry.record_for_identity(stage.hold_key)
    if record is None:
        return
    owner = owner_for_identity(record, stage.hold_key)
    if owner is None:
        return
    allocator._registry.by_identity.pop(stage.hold_key, None)
    owner.identity_key = stage.original_identity_key
    record.updated_at = datetime.now(UTC).isoformat()
    allocator._registry.by_identity[stage.original_identity_key] = record.code
    for removed in stage.removed_owners:
        if removed.record.code not in allocator._registry.records:
            allocator._registry.records[removed.record.code] = removed.record
        if all(
            existing.identity_key != removed.owner.identity_key
            for existing in removed.record.owners
        ):
            removed.record.owners.append(replace(removed.owner))
        allocator._registry.by_identity[removed.owner.identity_key] = (
            removed.record.code
        )


def discard_removed_records(allocator: DoorCodeAllocator, stage: StagedReissue) -> None:
    """Delete transient empty records after a staged replacement succeeds."""
    for removed in stage.removed_owners:
        if removed.record.owners:
            continue
        stored = allocator._registry.records.get(removed.record.code)
        if stored is removed.record:
            allocator._registry.records.pop(removed.record.code, None)


def stage_for_outcome(
    staged: list[StagedReissue], outcome: ReissueOutcome
) -> StagedReissue | None:
    """Return the staged re-issue matching an outcome row."""
    return next(
        (
            stage
            for stage in staged
            if stage.entry_id == outcome.entry_id
            and stage.identity_key == outcome.identity_key
            and stage.lockname == outcome.lockname
            and stage.slot == outcome.slot
        ),
        None,
    )


def hold_base_identity(hold_key: str) -> str:
    """Return the original identity encoded in a hold key."""
    return hold_key.split(":", 1)[0]


def observed_alias_base_identity(identity_key: str) -> str | None:
    """Return the base identity encoded in an observed alias."""
    parts = identity_key.split(":")
    if len(parts) == 5 and parts[1] == "observed":
        return parts[0]
    return None
