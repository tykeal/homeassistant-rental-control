# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Cross-entry allocation integration coverage."""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from typing import cast

from homeassistant.components.calendar import CalendarEvent
from homeassistant.util import dt as dt_util
import pytest

from custom_components.rental_control.allocator.allocator import DoorCodeAllocator
from custom_components.rental_control.allocator.models import AllocationOrigin
from custom_components.rental_control.coordinator_helpers import code_allocation
from custom_components.rental_control.reconciliation import ManagedSlot
from custom_components.rental_control.reconciliation import Reservation
from custom_components.rental_control.reconciliation import SlotStatus


@pytest.mark.parametrize(
    ("generator", "description", "uid"),
    [
        ("date_based", "Reserved: Same Dates", "uid-a"),
        ("static_random", "Reserved: Same Static Seed", "same-static-seed"),
        ("last_four", "Reserved: Same Phone\nPhone (last 4): 4242", "uid-a"),
    ],
)
async def test_identical_preferred_codes_are_unique_across_entries(
    monkeypatch: pytest.MonkeyPatch,
    generator: str,
    description: str,
    uid: str,
) -> None:
    """Entries sharing one lock receive distinct codes for every generator."""
    allocator = _allocator()
    monkeypatch.setattr(code_allocation, "get_allocator", lambda _hass: allocator)
    first = _event("Reserved: Alpha", description, uid)
    second = _event("Reserved: Bravo", description, uid)

    first_reservations = [_reservation("entry-a", first, generator)]
    second_reservations = [_reservation("entry-b", second, generator)]
    await code_allocation.async_resolve_codes(
        _fake_hass(),
        "entry-a",
        "front",
        4,
        [_free_slot(1)],
        first_reservations,
    )
    await code_allocation.async_resolve_codes(
        _fake_hass(),
        "entry-b",
        "front",
        4,
        [_free_slot(2)],
        second_reservations,
    )

    first_code = first_reservations[0].slot_code
    second_code = second_reservations[0].slot_code
    assert first_code is not None
    assert second_code is not None
    assert first_code != second_code
    assert len(allocator._registry.records) == 2
    assert len(set(allocator._registry.by_identity.values())) == 2
    assert {
        owner.origin
        for record in allocator._registry.records.values()
        for owner in record.owners
    } == {AllocationOrigin.PREFERRED, AllocationOrigin.COLLISION_RESOLVED}


async def test_full_code_space_is_shared_not_partitioned() -> None:
    """Every entry can use any code not already held by another identity."""
    allocator = _allocator()
    first = await allocator.async_allocate(
        code_allocation.AllocationRequest(
            entry_id="entry-a",
            identity_key="identity-a",
            preferred_code="1234",
            code_length=4,
        )
    )
    second = await allocator.async_allocate(
        code_allocation.AllocationRequest(
            entry_id="entry-b",
            identity_key="identity-b",
            preferred_code="5678",
            code_length=4,
        )
    )

    assert first.code == "1234"
    assert second.code == "5678"
    assert allocator._registry.code_for_identity("identity-a") == "1234"
    assert allocator._registry.code_for_identity("identity-b") == "5678"


def _event(summary: str, description: str, uid: str) -> CalendarEvent:
    """Build a calendar event with identical reservation dates."""
    start = datetime(2026, 9, 17, 16, tzinfo=dt_util.UTC)
    return CalendarEvent(
        summary=summary,
        description=description,
        start=start,
        end=start + timedelta(days=3),
        uid=uid,
    )


def _reservation(entry_id: str, event: CalendarEvent, generator: str) -> Reservation:
    """Build a reservation using the same generator path as production."""
    from custom_components.rental_control.coordinator_helpers.models import (
        ReservationBuildContext,
    )
    from custom_components.rental_control.coordinator_helpers.reservations import (
        build_reservations,
    )

    return build_reservations(
        [event],
        [],
        ReservationBuildContext(
            entry_id=entry_id,
            timezone=dt_util.UTC,
            event_prefix=None,
            trim_names=False,
            max_name_length=40,
            code_buffer_before=0,
            code_buffer_after=0,
            should_update_code=True,
            code_generator=generator,
            code_length=4,
            active_windows_for_name=lambda _name: set(),
        ),
    )[0]


def _free_slot(slot: int) -> ManagedSlot:
    """Build a confirmed-empty managed slot."""
    return ManagedSlot(slot=slot, managed=True, status=SlotStatus.FREE)


def _fake_hass() -> Any:
    """Build a minimal hass object for allocator integration tests."""
    return SimpleNamespace(
        data={},
        config=SimpleNamespace(config_dir="."),
        config_entries=SimpleNamespace(async_entries=lambda _domain=None: []),
    )


def _allocator() -> DoorCodeAllocator:
    """Build an allocator with persistence disabled for integration tests."""
    allocator = DoorCodeAllocator(_fake_hass())
    allocator._store = cast(Any, SimpleNamespace(async_save=lambda _registry: None))
    return allocator
