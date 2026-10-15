# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Forced re-issue recovers alongside issuance (FR-019, SC-011).

The gate logic under test is real: only the Keymaster observation and the
calendar-derived reservations are supplied directly, because they belong to
the coordinator rather than to the gate.
"""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.rental_control.allocator import reissue_preview
from custom_components.rental_control.coordinator_helpers import reissue
from custom_components.rental_control.reconciliation import ManagedSlot

from tests.integration.exclusion_harness import DEAD_SLOT
from tests.integration.exclusion_harness import World
from tests.integration.exclusion_harness import free_slot
from tests.integration.exclusion_harness import occupied_slot
from tests.integration.exclusion_harness import reservation
from tests.integration.exclusion_harness import single_entry_world
from tests.integration.exclusion_harness import unknown_slot

_LIVE_CODE = "4321"


def _slots() -> list[ManagedSlot]:
    """Return a range with one live guest and one permanently dead slot."""
    return [occupied_slot(10, _LIVE_CODE), unknown_slot(DEAD_SLOT)]


def _coordinator(world: World) -> Any:
    """Build the minimal coordinator surface the preview path reads."""

    class _Coordinator:
        """Coordinator stand-in exposing only preview-relevant state."""

        def __init__(self) -> None:
            """Record the harness hass and the configured code length."""
            self.hass = world.hass
            self.code_length = 4
            self.lockname = "front"

    return _Coordinator()


def _target(slot: int) -> reissue.ReissueTarget:
    """Build a slot-scoped forced re-issue target."""
    return reissue.ReissueTarget(
        entry_id="entry-a",
        identity_key=None,
        lockname="front",
        slot=slot,
        target_key=f"front:{slot}",
        checked_in=False,
    )


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> World:
    """Build a single-entry world with the coordinator inputs stubbed."""
    built = single_entry_world(monkeypatch)
    monkeypatch.setattr(
        reissue_preview,
        "observe_managed_slots_without_mutation",
        lambda _coordinator: _slots(),
    )
    monkeypatch.setattr(
        reissue_preview,
        "preview_reservations",
        lambda *_args, **_kwargs: [reservation("identity-live", _LIVE_CODE)],
    )
    return built


async def test_force_reissue_is_refused_while_the_slot_is_not_excluded(
    world: World,
) -> None:
    """The dead slot blocks forced re-issue exactly as it blocks issuance."""
    await world.cycle(
        "entry-a",
        [occupied_slot(10, _LIVE_CODE), free_slot(DEAD_SLOT)],
        [reservation("identity-live", _LIVE_CODE)],
    )

    _, preview = await reissue_preview.preview_request_and_result(
        world.allocator, _coordinator(world), _target(10)
    )

    assert preview.reason == "unaccounted_slots"


async def test_force_reissue_succeeds_on_a_non_excluded_slot(
    world: World,
) -> None:
    """Excluding the dead slot restores forced re-issue on the live slot."""
    await world.cycle("entry-a", [free_slot(10)], [reservation("identity-live")])
    world.exclude("entry-a", DEAD_SLOT, "relay failed")

    _, preview = await reissue_preview.preview_request_and_result(
        world.allocator, _coordinator(world), _target(10)
    )

    assert preview.reason not in {"unaccounted_slots", "adoption_pending"}


async def test_force_reissue_still_refuses_the_excluded_slot_itself(
    world: World,
) -> None:
    """An excluded slot holds no allocation, so there is nothing to re-issue."""
    await world.cycle("entry-a", [free_slot(10)], [reservation("identity-live")])
    world.exclude("entry-a", DEAD_SLOT, "relay failed")

    _, preview = await reissue_preview.preview_request_and_result(
        world.allocator, _coordinator(world), _target(DEAD_SLOT)
    )

    assert preview.reason == "clear_only"
