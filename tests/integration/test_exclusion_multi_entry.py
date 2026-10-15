# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""One dead slot freezes every entry until it is excluded (SC-002)."""

from __future__ import annotations

import pytest

from custom_components.rental_control.reconciliation import ManagedSlot
from custom_components.rental_control.reconciliation import Reservation

from tests.integration.exclusion_harness import DEAD_SLOT
from tests.integration.exclusion_harness import World
from tests.integration.exclusion_harness import free_slot
from tests.integration.exclusion_harness import reservation
from tests.integration.exclusion_harness import three_entry_world
from tests.integration.exclusion_harness import unknown_slot

_HEALTHY = {"entry-b": 20, "entry-c": 30}


def _slots(entry_id: str, start: int, dead: bool = True) -> list[ManagedSlot]:
    """Return the managed range for *entry_id*, optionally with a dead slot."""
    if entry_id == "entry-a":
        return [free_slot(10), unknown_slot(DEAD_SLOT) if dead else free_slot(11)]
    return [free_slot(start), free_slot(start + 1)]


async def _run_all(
    world: World, guests: dict[str, Reservation], dead: bool = True
) -> None:
    """Run one refresh cycle for every entry in the world."""
    for entry_id, spec in world.specs.items():
        await world.cycle(
            entry_id,
            _slots(entry_id, spec.start_slot, dead),
            [guests[entry_id]],
        )


async def test_one_dead_slot_freezes_every_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unreadable slot on one lock withholds codes system-wide."""
    world = three_entry_world(monkeypatch)
    guests = {entry_id: reservation(f"identity-{entry_id}") for entry_id in world.specs}

    await _run_all(world, guests)

    assert world.allocator._pending_adoption == {"entry-a"}
    assert all(guest.slot_code is None for guest in guests.values())
    assert all(guest.code_source == "unallocated" for guest in guests.values())


async def test_one_exclusion_releases_every_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Excluding the single dead slot lets all three entries issue again."""
    world = three_entry_world(monkeypatch)
    first = {entry_id: reservation(f"first-{entry_id}") for entry_id in world.specs}
    await _run_all(world, first)

    world.exclude("entry-a", DEAD_SLOT, "relay failed")

    second = {entry_id: reservation(f"second-{entry_id}") for entry_id in world.specs}
    await _run_all(world, second)

    assert world.allocator._pending_adoption == set()
    assert all(guest.slot_code is not None for guest in second.values())
    assert len({guest.slot_code for guest in second.values()}) == 3


async def test_exclusion_survives_a_further_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A restart needs no second operator action to stay unfrozen.

    The adoption sweep that follows a restart still has to complete, which
    it does without the operator touching the exclusion again.
    """
    world = three_entry_world(monkeypatch)
    world.exclude("entry-a", DEAD_SLOT, "relay failed")
    await _run_all(world, {k: reservation(f"pre-{k}") for k in world.specs})

    world.restart()
    assert world.allocator._pending_adoption == set(world.specs)

    await _run_all(world, {k: reservation(f"sweep-{k}") for k in world.specs})
    assert world.allocator._pending_adoption == set()

    guests = {entry_id: reservation(f"post-{entry_id}") for entry_id in world.specs}
    await _run_all(world, guests)

    assert all(guest.slot_code is not None for guest in guests.values())
