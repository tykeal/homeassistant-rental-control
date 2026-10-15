# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""The whole recovery arc driven by a single operator action.

Covers SC-001, SC-003, SC-004 and SC-012 for user stories 1 and 3.
"""

from __future__ import annotations

import pytest

from custom_components.rental_control.coordinator_helpers import code_allocation
from custom_components.rental_control.coordinator_helpers import slot_exclusions
from custom_components.rental_control.reconciliation import ActionKind
from custom_components.rental_control.reconciliation import compute_desired_plan

from tests.integration.exclusion_harness import DEAD_SLOT
from tests.integration.exclusion_harness import START
from tests.integration.exclusion_harness import free_slot
from tests.integration.exclusion_harness import reservation
from tests.integration.exclusion_harness import single_entry_world
from tests.integration.exclusion_harness import unknown_slot


async def test_single_exclusion_carries_the_whole_arc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One service call unfreezes, self-clears, and refreezes on relapse."""
    world = single_entry_world(monkeypatch)
    dead = [free_slot(10), unknown_slot(DEAD_SLOT)]
    healthy = [free_slot(10), free_slot(DEAD_SLOT)]

    frozen = reservation("identity-a")
    result = await world.cycle("entry-a", dead, [frozen])
    assert frozen.slot_code is None
    assert frozen.code_source == "unallocated"
    assert DEAD_SLOT in result.observation.unreadable_slots
    assert code_allocation.unreadable_slots_notification_id("entry-a") in set(
        world.notifications
    )

    world.exclude("entry-a", DEAD_SLOT, "relay failed")

    guest = reservation("identity-b")
    await world.cycle("entry-a", dead, [guest])
    assert guest.slot_code is not None
    assert guest.code_source in {"allocated", "collision_resolved"}
    assert set(world.notifications) == {
        slot_exclusions.excluded_slots_notification_id("entry-a")
    }

    recovered = reservation("identity-c")
    await world.cycle("entry-a", healthy, [recovered])
    assert world.exclusions("entry-a") is None
    assert world.notifications == {}
    assert recovered.slot_code is not None

    relapsed = reservation("identity-d")
    await world.cycle("entry-a", dead, [relapsed])
    assert relapsed.slot_code is None
    assert code_allocation.unreadable_slots_notification_id("entry-a") in set(
        world.notifications
    )


async def test_excluded_slot_is_never_written_or_offered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The excluded slot stays blocked and out of the free pool."""
    world = single_entry_world(monkeypatch)
    world.exclude("entry-a", DEAD_SLOT, "relay failed")
    slots = [free_slot(10), unknown_slot(DEAD_SLOT)]
    guests = [reservation("identity-a"), reservation("identity-b")]

    await world.cycle("entry-a", slots, guests)
    plan = compute_desired_plan(guests, slots, 2, "plan-1", START)

    excluded = [action for action in plan.actions if action.slot == DEAD_SLOT]
    assert excluded
    assert {action.kind for action in excluded} == {ActionKind.BLOCKED}
    assert {action.reason for action in excluded} == {"unreadable"}
    assert guests[0].slot_code is not None
    assert guests[1].desired_slot != DEAD_SLOT
