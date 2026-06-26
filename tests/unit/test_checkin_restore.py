# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Focused restore decision tests for check-in helpers."""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta

from homeassistant.components.calendar import CalendarEvent
from homeassistant.util import dt as dt_util

from custom_components.rental_control.const import CHECKIN_STATE_AWAITING
from custom_components.rental_control.const import CHECKIN_STATE_CHECKED_IN
from custom_components.rental_control.const import CHECKIN_STATE_CHECKED_OUT
from custom_components.rental_control.const import CHECKIN_STATE_NO_RESERVATION
from custom_components.rental_control.sensors.checkin.models import CheckinStateSnapshot
from custom_components.rental_control.sensors.checkin.models import (
    CoordinatorUpdateContext,
)
from custom_components.rental_control.sensors.checkin.restore_decisions import (
    decide_restore_state,
)

NOW = datetime(2026, 6, 1, 12, tzinfo=dt_util.UTC)


def _event() -> CalendarEvent:
    """Return a future event."""
    return CalendarEvent(
        summary="Reserved - Ada",
        start=NOW + timedelta(days=1),
        end=NOW + timedelta(days=2),
    )


def _ctx(
    snapshot: CheckinStateSnapshot,
    events: list[CalendarEvent] | None = None,
    monitoring: bool = False,
) -> CoordinatorUpdateContext:
    """Build a restore context."""
    return CoordinatorUpdateContext(
        snapshot, events or [], lambda: NOW, True, monitoring, 2.0, "", False, "Rental"
    )


def test_restore_checked_in_ended_event_silent_checkout() -> None:
    """Ended checked-in restore silently checks out and computes linger."""
    snapshot = CheckinStateSnapshot(
        CHECKIN_STATE_CHECKED_IN,
        "Reserved - Ada",
        NOW - timedelta(days=2),
        NOW - timedelta(hours=1),
    )
    decision = decide_restore_state(_ctx(snapshot))

    assert [effect.kind for effect in decision.effects] == [
        "silent_checked_out",
        "compute_linger",
    ]
    assert decision.write_state is True


def test_restore_awaiting_past_start_silent_checkin_checkout() -> None:
    """Awaiting restore catches up both check-in and checkout silently."""
    snapshot = CheckinStateSnapshot(
        CHECKIN_STATE_AWAITING,
        "Reserved - Ada",
        NOW - timedelta(days=2),
        NOW - timedelta(hours=1),
    )
    decision = decide_restore_state(_ctx(snapshot, monitoring=False))

    assert [effect.kind for effect in decision.effects] == [
        "cancel_timer",
        "set_transition_target",
        "silent_checked_in",
        "silent_checked_out",
        "compute_linger",
    ]


def test_restore_checked_out_new_event_hands_off_to_awaiting() -> None:
    """A new relevant event after restore transitions to awaiting."""
    decision = decide_restore_state(
        _ctx(CheckinStateSnapshot(CHECKIN_STATE_CHECKED_OUT), [_event()])
    )

    assert [effect.kind for effect in decision.effects] == ["transition_awaiting"]


def test_restore_no_reservation_recreates_future_followup() -> None:
    """Future FR-006c follow-up day recreates its timer."""
    snapshot = CheckinStateSnapshot(
        CHECKIN_STATE_NO_RESERVATION, next_event_start_day=NOW + timedelta(days=1)
    )
    decision = decide_restore_state(_ctx(snapshot))

    assert [effect.kind for effect in decision.effects] == [
        "cancel_timer",
        "schedule_no_reservation_to_awaiting",
    ]
    assert decision.write_state is True


def test_restore_unknown_resets_to_no_reservation() -> None:
    """Unknown restored state resets through the safe no-reservation path."""
    decision = decide_restore_state(_ctx(CheckinStateSnapshot("corrupt")))

    assert [effect.kind for effect in decision.effects] == ["transition_no_reservation"]
    assert decision.log_records[0].level == "warning"
