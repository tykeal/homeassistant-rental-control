# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""A shut adoption gate always explains itself in the log."""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta
import logging
from types import SimpleNamespace
from typing import Any

from homeassistant.util import dt as dt_util
import pytest

from custom_components.rental_control.coordinator_helpers import adoption_gate
from custom_components.rental_control.coordinator_helpers import code_allocation
from custom_components.rental_control.reconciliation import ManagedSlot
from custom_components.rental_control.reconciliation import Reservation
from custom_components.rental_control.reconciliation import SlotStatus

from tests.unit.test_code_allocation_step import FakeAllocator

_WITHHOLDING = "is withholding ALL door code issuance"


def _reservation(identity_key: str = "identity-a", code: str = "9999") -> Reservation:
    """Build a reservation for adoption gate tests."""
    start = datetime(2026, 9, 17, 16, tzinfo=dt_util.UTC)
    end = start + timedelta(days=3)
    return Reservation(
        identity_key=identity_key,
        start=start,
        end=end,
        buffered_start=start,
        buffered_end=end,
        summary="Reserved: Test Guest",
        slot_name="Test Guest",
        display_slot_name="RC Test Guest",
        slot_code=code,
    )


def _coded_slot(slot: int, code: str, name: str = "RC Test Guest") -> ManagedSlot:
    """Build a readable occupied managed slot."""
    start = datetime(2026, 9, 17, 16, tzinfo=dt_util.UTC)
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


def _unreadable_slot(slot: int) -> ManagedSlot:
    """Build a managed slot that reports no readable state."""
    return ManagedSlot(
        slot=slot,
        managed=True,
        status=SlotStatus.UNKNOWN,
        actual_name=None,
        actual_code=None,
        actual_code_present=False,
    )


async def _resolve(
    monkeypatch: Any,
    managed_slots: list[ManagedSlot],
    reservations: list[Reservation],
    entry_id: str = "entry-a",
) -> FakeAllocator:
    """Run the allocation step against a recording allocator double."""
    allocator = FakeAllocator()
    monkeypatch.setattr(code_allocation, "get_allocator", lambda _hass: allocator)
    await code_allocation.async_resolve_codes(
        SimpleNamespace(),
        entry_id,
        "front",
        4,
        managed_slots,
        reservations,
    )
    return allocator


async def test_unmatched_coded_slot_warns_and_names_the_slot(
    monkeypatch: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A code matching no reservation is reported, not silently withheld."""
    caplog.set_level(logging.WARNING)

    allocator = await _resolve(monkeypatch, [_coded_slot(7, "2222")], [])

    assert allocator.calls == []
    assert _WITHHOLDING in caplog.text
    assert "slot 7 holds a code that matches no current reservation" in caplog.text
    assert "No door codes will be created or updated" in caplog.text


async def test_unreadable_slot_warns_with_its_own_reason(
    monkeypatch: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unreadable slots are named with the reason that fits them."""
    caplog.set_level(logging.WARNING)

    await _resolve(monkeypatch, [_unreadable_slot(3)], [_reservation()])

    assert "slot 3 is unreadable" in caplog.text
    assert "matches no current reservation" not in caplog.text


async def test_open_gate_stays_quiet(
    monkeypatch: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Nothing is withheld, so nothing is reported."""
    caplog.set_level(logging.WARNING)
    reservation = _reservation(code="1111")

    allocator = await _resolve(monkeypatch, [_coded_slot(1, "2222")], [reservation])

    assert allocator.calls
    assert _WITHHOLDING not in caplog.text


async def test_repeated_cycles_warn_once_per_condition(
    monkeypatch: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A standing condition does not re-warn on every refresh cycle."""
    caplog.set_level(logging.WARNING)

    await _resolve(monkeypatch, [_coded_slot(7, "2222")], [])
    await _resolve(monkeypatch, [_coded_slot(7, "2222")], [])

    assert caplog.text.count(_WITHHOLDING) == 1

    await _resolve(monkeypatch, [_coded_slot(7, "2222"), _coded_slot(8, "3333")], [])

    assert caplog.text.count(_WITHHOLDING) == 2


async def test_recovered_gate_reports_a_reopened_gate(
    monkeypatch: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Reopening the entry's own gate is logged, and re-warns if it shuts."""
    caplog.set_level(logging.INFO)
    await _resolve(monkeypatch, [_coded_slot(7, "2222")], [])

    reservation = _reservation(code="1111")
    await _resolve(monkeypatch, [_coded_slot(1, "2222")], [reservation])

    assert "its adoption gate reopened" in caplog.text

    caplog.clear()
    await _resolve(monkeypatch, [_coded_slot(7, "2222")], [])

    assert caplog.text.count(_WITHHOLDING) == 1


def test_forget_entry_drops_the_throttle_state(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unloading an entry lets a returning condition report again."""
    caplog.set_level(logging.WARNING)
    status = adoption_gate.AdoptionGateStatus(
        complete=False, unmatched_slots=frozenset({7})
    )

    adoption_gate.report("entry-a", "front", status)
    adoption_gate.forget_entry("entry-a")
    adoption_gate.report("entry-a", "front", status)

    assert caplog.text.count(_WITHHOLDING) == 2


async def test_missing_allocator_logs_an_error(
    monkeypatch: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A missing allocator is a catastrophic failure, not a silent one."""
    caplog.set_level(logging.ERROR)
    monkeypatch.setattr(code_allocation, "get_allocator", lambda _hass: None)
    reservation = _reservation(code="1111")

    await code_allocation.async_resolve_codes(
        SimpleNamespace(), "entry-a", "front", 4, [], [reservation]
    )

    assert "shared door code allocator is not available" in caplog.text
    assert reservation.slot_code is None
    assert reservation.code_source == "unallocated"
