# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Boundary behaviour of standing slot exclusions (FR-017, SC-004)."""

from __future__ import annotations

from pathlib import Path

import pytest

from custom_components.rental_control.const import CONF_EXCLUDED_SLOTS
from custom_components.rental_control.coordinator_helpers import slot_exclusions
from custom_components.rental_control.reconciliation import ActionKind
from custom_components.rental_control.reconciliation import compute_desired_plan

from tests.integration.exclusion_harness import DEAD_SLOT
from tests.integration.exclusion_harness import START
from tests.integration.exclusion_harness import EntrySpec
from tests.integration.exclusion_harness import World
from tests.integration.exclusion_harness import free_slot
from tests.integration.exclusion_harness import reservation
from tests.integration.exclusion_harness import single_entry_world
from tests.integration.exclusion_harness import unknown_slot


async def test_excluding_every_slot_reports_ordinary_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fully excluded range satisfies the gate but has nowhere to write."""
    world = single_entry_world(monkeypatch)
    world.exclude("entry-a", 10, "dead")
    world.exclude("entry-a", DEAD_SLOT, "dead")
    slots = [unknown_slot(10), unknown_slot(DEAD_SLOT)]
    guest = reservation("identity-a")

    await world.cycle("entry-a", slots, [guest])
    plan = compute_desired_plan([guest], slots, 2, "plan-1", START)

    assert world.allocator._pending_adoption == set()
    assert guest.slot_code is None
    assert plan.overflow["identity-a"] == "code_unavailable"
    assert {action.kind for action in plan.actions} == {ActionKind.BLOCKED}


async def test_overlapping_ranges_do_not_share_an_exclusion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two entries on one parent lock keep separate exclusion records."""
    world = World(
        [
            EntrySpec("entry-a", "front", 10),
            EntrySpec("entry-b", "front", 10),
        ],
        monkeypatch,
    )
    world.exclude("entry-a", DEAD_SLOT, "relay failed")

    assert slot_exclusions.load(world.hass, "entry-a") == {DEAD_SLOT: "relay failed"}
    assert slot_exclusions.load(world.hass, "entry-b") == {}

    guest = reservation("identity-b")
    await world.cycle("entry-b", [free_slot(10), unknown_slot(DEAD_SLOT)], [guest])

    assert guest.slot_code is None


async def test_an_options_save_returns_the_entry_to_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exclusions live in entry data, so an options save drops them."""
    world = single_entry_world(monkeypatch)
    world.exclude("entry-a", DEAD_SLOT, "relay failed")
    slots = [free_slot(10), unknown_slot(DEAD_SLOT)]

    before = reservation("identity-a")
    await world.cycle("entry-a", slots, [before])
    assert before.slot_code is not None

    world.save_options("entry-a")

    assert CONF_EXCLUDED_SLOTS not in world.entry("entry-a").data
    after = reservation("identity-b")
    await world.cycle("entry-a", slots, [after])
    assert after.slot_code is None


async def test_reconciliation_still_blocks_the_excluded_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The planner classifies the slot unchanged, and was not modified."""
    world = single_entry_world(monkeypatch)
    world.exclude("entry-a", DEAD_SLOT, "relay failed")
    slots = [free_slot(10), unknown_slot(DEAD_SLOT)]
    guest = reservation("identity-a")

    await world.cycle("entry-a", slots, [guest])
    plan = compute_desired_plan([guest], slots, 2, "plan-1", START)

    blocked = [action for action in plan.actions if action.slot == DEAD_SLOT]
    assert [(action.kind, action.reason) for action in blocked] == [
        (ActionKind.BLOCKED, "unreadable")
    ]
    assert plan.selected["identity-a"] == 10


def test_no_reconciliation_module_knows_about_slot_exclusions() -> None:
    """Enforcement is a gate concern; the planner stays untouched."""
    sources = Path("custom_components/rental_control/reconciliation").glob("*.py")

    assert not [
        path.name
        for path in sources
        if "excluded_slots" in path.read_text(encoding="utf-8")
    ]
