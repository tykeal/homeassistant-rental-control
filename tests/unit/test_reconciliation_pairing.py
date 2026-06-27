# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Focused tests for reconciliation pairing helpers."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone

from custom_components.rental_control.reconciliation import ManagedSlot
from custom_components.rental_control.reconciliation import Reservation
from custom_components.rental_control.reconciliation import SlotStatus
from custom_components.rental_control.reconciliation.pairing import (
    _pair_partial_managed,
)


def _dt(day: int) -> datetime:
    """Return a UTC July 2026 datetime."""
    return datetime(2026, 7, day, tzinfo=timezone.utc)


def _reservation(identity: str, start_day: int) -> Reservation:
    """Build a reservation with duplicate stable name for pairing tests."""
    return Reservation(
        identity,
        _dt(start_day),
        _dt(start_day + 1),
        _dt(start_day),
        _dt(start_day + 1),
        identity,
        "Guest",
        "RC Guest",
        "1234",
    )


def test_partial_managed_pairing_preserves_start_order() -> None:
    """Partial pairing selects the nearest ordered desired reservations."""
    slots = [
        ManagedSlot(
            1, True, SlotStatus.OCCUPIED, actual_start=_dt(3), actual_end=_dt(4)
        )
    ]
    desired = [_reservation("a", 1), _reservation("b", 3)]
    assert _pair_partial_managed(slots, desired) == [(slots[0], desired[1])]
