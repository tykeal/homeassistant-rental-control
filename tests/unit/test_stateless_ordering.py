# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Stateless planner ordering tests for repeated local hours."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
from zoneinfo import ZoneInfo

from custom_components.rental_control.reconciliation import DesiredReservation
from custom_components.rental_control.reconciliation import ObservedSlot
from custom_components.rental_control.reconciliation.stateless import (
    StatelessPlanRequest,
)
from custom_components.rental_control.reconciliation.stateless import StatelessPlanState
from custom_components.rental_control.reconciliation.stateless import (
    _observed_physical_group,
)
from custom_components.rental_control.reconciliation.stateless import (
    _pair_observed_group,
)
from custom_components.rental_control.reconciliation.stateless import (
    group_stateless_reservations_by_name,
)
from custom_components.rental_control.reconciliation.stateless import (
    select_stateless_reservations,
)
from custom_components.rental_control.reconciliation.stateless_models import (
    StatelessPlan,
)


def _reservation(desired_id: str, start: datetime, end: datetime) -> DesiredReservation:
    """Build a desired stay with matching unbuffered and buffered windows."""
    return DesiredReservation(
        desired_id=desired_id,
        stable_slot_name="Synthetic Guest",
        display_slot_name="Synthetic Guest",
        start=start,
        end=end,
        buffered_start=start,
        buffered_end=end,
        slot_code="1357",
    )


def test_stateless_capacity_and_grouping_use_utc_order() -> None:
    """Same-name stays retain instant order during selection and pairing."""
    ny_timezone = ZoneInfo("America/New_York")
    edt = _reservation(
        "edt",
        datetime(2026, 11, 1, 1, 45, tzinfo=ny_timezone, fold=0),
        datetime(2026, 11, 1, 1, 59, tzinfo=ny_timezone, fold=0),
    )
    est = _reservation(
        "est",
        datetime(2026, 11, 1, 1, 15, tzinfo=ny_timezone, fold=1),
        datetime(2026, 11, 1, 1, 30, tzinfo=ny_timezone, fold=1),
    )
    request = StatelessPlanRequest(
        observed_slots=[],
        desired_reservations=[est, edt],
        max_events=1,
        plan_id="dst-fold-stateless",
        generated_at=datetime(2026, 11, 1, tzinfo=ny_timezone),
    )
    plan = StatelessPlan(
        plan_id=request.plan_id,
        generated_at=request.generated_at,
    )

    selected = select_stateless_reservations(request, plan)

    assert [reservation.desired_id for reservation in selected] == ["edt"]
    assert plan.overflow == {"est": "capacity"}
    assert [
        reservation.desired_id
        for reservation in group_stateless_reservations_by_name([est, edt])[
            "synthetic guest"
        ]
    ] == ["edt", "est"]

    grouped = group_stateless_reservations_by_name([est, edt])["synthetic guest"]
    physical = [
        ObservedSlot(
            slot=7,
            managed=True,
            raw_name="Synthetic Guest",
            raw_pin="1357",
            actual_start=est.start.astimezone(timezone.utc),
            actual_end=est.end.astimezone(timezone.utc),
        ),
        ObservedSlot(
            slot=6,
            managed=True,
            raw_name="Synthetic Guest",
            raw_pin="1357",
            actual_start=edt.start.astimezone(timezone.utc),
            actual_end=edt.end.astimezone(timezone.utc),
        ),
    ]
    state = StatelessPlanState(
        request,
        StatelessPlan(plan_id=request.plan_id, generated_at=request.generated_at),
        grouped,
        {},
        set(),
        set(),
    )
    ordered_physical = _observed_physical_group(grouped, physical, state)
    pairs, _, _ = _pair_observed_group(ordered_physical, grouped)

    assert {desired.desired_id: slot.slot for slot, desired in pairs} == {
        "edt": 6,
        "est": 7,
    }
