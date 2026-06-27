# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Focused tests for desired-plan phases."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone

from custom_components.rental_control.reconciliation import ManagedSlot
from custom_components.rental_control.reconciliation import Reservation
from custom_components.rental_control.reconciliation import SlotStatus
from custom_components.rental_control.reconciliation import compute_desired_plan


def test_desired_plan_updates_existing_name_match_in_place() -> None:
    """A changed reservation remains bound to the same physical slot."""
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    end = datetime(2026, 7, 4, tzinfo=timezone.utc)
    reservation = Reservation(
        "id", start, end, start, end, "Guest", "Guest", "RC Guest", "9999"
    )
    slot = ManagedSlot(
        3,
        True,
        SlotStatus.OCCUPIED,
        "RC Guest",
        actual_code="1234",
        actual_code_present=True,
        actual_start=start,
        actual_end=end,
        persisted_identity_key="id",
    )
    plan = compute_desired_plan([reservation], [slot], 1, "plan", start)
    assert plan.selected == {"id": 3}
    assert [action.slot for action in plan.actions] == [3]
