# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Integration coverage for allocator adoption behaviour."""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from typing import cast

from homeassistant.util import dt as dt_util
import pytest

from custom_components.rental_control.allocator import allocator as allocator_module
from custom_components.rental_control.allocator.allocator import DoorCodeAllocator
from custom_components.rental_control.allocator.models import AdoptionRequest
from custom_components.rental_control.coordinator_helpers import code_allocation
from custom_components.rental_control.reconciliation import ManagedSlot
from custom_components.rental_control.reconciliation import Reservation
from custom_components.rental_control.reconciliation import SlotStatus


@pytest.fixture
def notifications(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Capture persistent notifications raised by allocator adoption."""
    captured: list[str] = []
    monkeypatch.setattr(
        allocator_module,
        "async_create",
        lambda _hass, message, **_kwargs: captured.append(message),
    )
    return captured


async def test_empty_registry_adopts_coded_slots_without_rotation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty registry adopts observed codes and leaves them unchanged."""
    allocator = _allocator()
    monkeypatch.setattr(
        code_allocation,
        "get_allocator",
        lambda _hass: allocator,
    )
    first = _reservation("identity-a", "Alpha Guest", "9999")
    second = _reservation("identity-b", "Beta Guest", "8888", days_offset=5)
    slots = [
        _slot(1, "1357", "RC Alpha Guest"),
        _slot(2, "2468", "RC Beta Guest", days_offset=5),
    ]

    await code_allocation.async_resolve_codes(
        _fake_hass(),
        "entry-a",
        "front",
        4,
        slots,
        [first, second],
    )

    assert first.slot_code == "1357"
    assert second.slot_code == "2468"
    assert allocator._registry.code_for_identity("identity-a") == "1357"
    assert allocator._registry.code_for_identity("identity-b") == "2468"
    assert [slot.actual_code for slot in slots] == ["1357", "2468"]


async def test_duplicate_adoption_records_conflict_without_rotation(
    notifications: list[str],
) -> None:
    """A live duplicate is reported and neither owner is rotated."""
    allocator = _allocator()

    first = await allocator.async_adopt(
        AdoptionRequest(
            entry_id="entry-a",
            identity_key="identity-a",
            code="1357",
            code_length=4,
            lockname="front",
            slot=1,
        )
    )
    second = await allocator.async_adopt(
        AdoptionRequest(
            entry_id="entry-b",
            identity_key="identity-b",
            code="1357",
            code_length=4,
            lockname="front",
            slot=2,
        )
    )

    record = allocator._registry.records["1357"]
    assert first.code == second.code == "1357"
    assert {(owner.identity_key, owner.slot) for owner in record.owners} == {
        ("identity-a", 1),
        ("identity-b", 2),
    }
    assert notifications
    assert "code_ref" in notifications[0]
    assert "1357" not in notifications[0]
    assert not allocator._registry.is_available("1357", 4, "identity-c")


async def test_duplicate_adoption_is_idempotent(
    notifications: list[str],
) -> None:
    """Repeating duplicate observations records no extra owners or reports."""
    allocator = _allocator()
    duplicate = AdoptionRequest(
        entry_id="entry-b",
        identity_key="identity-b",
        code="1357",
        code_length=4,
        lockname="front",
        slot=2,
    )
    await allocator.async_adopt(
        AdoptionRequest(
            entry_id="entry-a",
            identity_key="identity-a",
            code="1357",
            code_length=4,
            lockname="front",
            slot=1,
        )
    )
    await allocator.async_adopt(duplicate)
    await allocator.async_adopt(duplicate)

    assert len(allocator._registry.records["1357"].owners) == 2
    assert len(notifications) == 1


def _reservation(
    identity_key: str,
    slot_name: str,
    code: str,
    *,
    days_offset: int = 0,
) -> Reservation:
    """Build a reservation matching a managed slot."""
    start = datetime(2026, 9, 17 + days_offset, 16, tzinfo=dt_util.UTC)
    end = start + timedelta(days=3)
    return Reservation(
        identity_key=identity_key,
        start=start,
        end=end,
        buffered_start=start,
        buffered_end=end,
        summary=f"Reserved: {slot_name}",
        slot_name=slot_name,
        display_slot_name=f"RC {slot_name}",
        slot_code=code,
    )


def _slot(
    slot: int,
    code: str,
    name: str,
    *,
    days_offset: int = 0,
) -> ManagedSlot:
    """Build a readable occupied managed slot."""
    start = datetime(2026, 9, 17 + days_offset, 16, tzinfo=dt_util.UTC)
    end = start + timedelta(days=3)
    return ManagedSlot(
        slot=slot,
        managed=True,
        status=SlotStatus.OCCUPIED,
        actual_name=name,
        actual_code=code,
        actual_code_present=True,
        actual_start=start,
        actual_end=end,
    )


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
