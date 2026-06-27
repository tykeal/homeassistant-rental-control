# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Focused tests for stateless-plan phases."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone

from custom_components.rental_control.reconciliation import ActionKind
from custom_components.rental_control.reconciliation import DesiredReservation
from custom_components.rental_control.reconciliation import ObservedSlot
from custom_components.rental_control.reconciliation import compute_stateless_plan


def test_stateless_plan_assigns_confirmed_empty_slot() -> None:
    """Confirmed-empty slots receive an ASSIGN action for selected stays."""
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    desired = DesiredReservation(
        "id",
        "Guest",
        "RC Guest",
        start,
        start.replace(day=2),
        start,
        start.replace(day=2),
        "1234",
    )
    slot = ObservedSlot(1, True, raw_name="", raw_pin="", empty_confirmed=True)
    plan = compute_stateless_plan([slot], [desired], 1, "plan", start)
    assert plan.selected == {"id": 1}
    assert plan.actions[0].kind is ActionKind.ASSIGN
