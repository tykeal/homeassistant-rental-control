# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Observed-slot accounting for shared allocator refresh cycles."""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import recovery
from .models import CycleObservation
from .models import CycleRequest
from .models import ObservedLock

if TYPE_CHECKING:
    from .allocator import DoorCodeAllocator


def account_for_observation(
    allocator: DoorCodeAllocator, request: CycleRequest
) -> frozenset[int]:
    """Record what this cycle read of the lock; return unaccounted slots."""
    observation = request.observation
    unaccounted = unaccounted_slots(
        allocator, observation, excluded=request.excluded_slots
    )
    track_pending_adoption_slots(
        allocator,
        observation.entry_id,
        observation.unreadable_slots - request.excluded_slots,
    )
    record_adoption(allocator, request, unaccounted=unaccounted)
    return unaccounted


def record_adoption(
    allocator: DoorCodeAllocator,
    request: CycleRequest,
    *,
    unaccounted: frozenset[int],
) -> None:
    """Update what the allocator has observed of this entry's lock.

    A complete cycle with nothing unaccounted records the lock and its
    managed slots. An incomplete cycle withdraws that record because it may
    leave a code in the lock that the registry has not observed.
    """
    observation = request.observation
    entry_id = observation.entry_id
    lockname = observation.lockname
    sound = request.adoption_complete and not unaccounted
    if lockname is not None and not sound:
        allocator._adopted_locks.pop(entry_id, None)
        if allocator._registry_lost:
            recovery.reopen_adoption(allocator, entry_id)
    if sound:
        if lockname is not None:
            allocator._adopted_locks[entry_id] = ObservedLock(
                lockname, observation.managed_slots
            )
        allocator._pending_adoption.discard(entry_id)
        if not allocator._pending_adoption:
            allocator._gate_deadline = 0.0
            allocator._gate_warning_sent = False
    track_read_pending_lock(allocator, request)


def track_read_pending_lock(
    allocator: DoorCodeAllocator, request: CycleRequest
) -> None:
    """Remember codes from a complete read while its lock awaits adoption."""
    observation = request.observation
    entry_id = observation.entry_id
    if (
        observation.lockname is not None
        and entry_id in allocator._pending_adoption
        and not observation.unreadable_slots - request.excluded_slots
    ):
        allocator._read_pending_locks[entry_id] = (
            ObservedLock(observation.lockname, observation.managed_slots),
            frozenset(observation.observed_codes),
            frozenset(observation.unreadable_slots),
        )
    else:
        allocator._read_pending_locks.pop(entry_id, None)


def track_pending_adoption_slots(
    allocator: DoorCodeAllocator, entry_id: str, unreadable: frozenset[int]
) -> None:
    """Record unreadable slots blocking an entry's adoption, if any."""
    tracked = getattr(allocator, "_pending_adoption_slots", None)
    if tracked is None:
        return
    if unreadable:
        tracked[entry_id] = unreadable
    else:
        tracked.pop(entry_id, None)


def claimed_slots(
    allocator: DoorCodeAllocator, entry_id: str, lockname: str
) -> frozenset[int]:
    """Return slots on one lock claimed by one entry's registry owners."""
    return frozenset(
        owner.slot
        for record in allocator._registry.records.values()
        for owner in record.owners
        if owner.entry_id == entry_id
        and owner.lockname == lockname
        and owner.slot is not None
    )


def unaccounted_slots(
    allocator: DoorCodeAllocator,
    observation: CycleObservation,
    *,
    excluded: frozenset[int] = frozenset(),
) -> frozenset[int]:
    """Return unreadable slots not claimed by owners or covered by exclusions."""
    if observation.lockname is None:
        return frozenset()
    claimed = claimed_slots(allocator, observation.entry_id, observation.lockname)
    return frozenset(observation.unreadable_slots - claimed - excluded)
