# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Focused coordinator decision tests for check-in helper modules."""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta

from homeassistant.components.calendar import CalendarEvent
from homeassistant.util import dt as dt_util

from custom_components.rental_control.const import CHECKIN_STATE_AWAITING
from custom_components.rental_control.const import CHECKIN_STATE_CHECKED_IN
from custom_components.rental_control.const import CHECKIN_STATE_CHECKED_OUT
from custom_components.rental_control.const import CHECKIN_STATE_NO_RESERVATION
from custom_components.rental_control.sensors.checkin.event_selection import event_key
from custom_components.rental_control.sensors.checkin.event_selection import (
    extract_slot_name,
)
from custom_components.rental_control.sensors.checkin.event_selection import (
    find_followon_event,
)
from custom_components.rental_control.sensors.checkin.event_selection import (
    find_tracked_event,
)
from custom_components.rental_control.sensors.checkin.event_selection import (
    get_relevant_event,
)
from custom_components.rental_control.sensors.checkin.models import CheckinStateSnapshot
from custom_components.rental_control.sensors.checkin.models import (
    CoordinatorUpdateContext,
)
from custom_components.rental_control.sensors.checkin.transition_decisions import (
    decide_coordinator_update,
)

NOW = datetime(2026, 6, 1, 12, tzinfo=dt_util.UTC)


def _event(summary: str, start: datetime, end: datetime) -> CalendarEvent:
    """Create a calendar event."""
    return CalendarEvent(
        summary=summary, start=start, end=end, description="Guest: Ada"
    )


def _ctx(
    snapshot: CheckinStateSnapshot,
    events: list[CalendarEvent],
    monitoring: bool = False,
) -> CoordinatorUpdateContext:
    """Build a coordinator update context."""
    return CoordinatorUpdateContext(
        snapshot, events, lambda: NOW, True, monitoring, 2.0, "", False, "Rental"
    )


def test_event_selection_preserves_identity_order_and_slot_extraction() -> None:
    """Event helper behavior matches the entity helper contract."""
    old = _event("Old", NOW - timedelta(days=2), NOW - timedelta(days=1))
    current = _event(
        "Reserved - Ada", NOW + timedelta(hours=1), NOW + timedelta(days=1)
    )
    later = _event("Later", NOW + timedelta(days=2), NOW + timedelta(days=3))

    assert event_key(current.summary, current.start).startswith("Reserved - Ada|")
    assert get_relevant_event([old, current, later], NOW) == current
    assert (
        find_tracked_event([later, current], current.summary, current.start) == current
    )
    assert (
        find_followon_event(
            [current, later], NOW, event_key(current.summary, current.start)
        )
        == later
    )
    assert extract_slot_name(current, "") == "Ada"


def test_no_reservation_with_event_transitions_to_awaiting() -> None:
    """A relevant event produces an awaiting transition effect."""
    event = _event("Reserved - Ada", NOW + timedelta(hours=1), NOW + timedelta(days=1))
    decision = decide_coordinator_update(_ctx(CheckinStateSnapshot(), [event]))

    assert [effect.kind for effect in decision.effects] == ["transition_awaiting"]
    assert decision.effects[0].event == event


def test_awaiting_past_start_checks_in_when_monitoring_off() -> None:
    """Awaiting updates silently model automatic check-in eligibility."""
    event = _event("Reserved - Ada", NOW - timedelta(hours=1), NOW + timedelta(days=1))
    snapshot = CheckinStateSnapshot(CHECKIN_STATE_AWAITING, event.summary, event.start)
    decision = decide_coordinator_update(_ctx(snapshot, [event], monitoring=False))

    assert [effect.kind for effect in decision.effects] == [
        "update_tracked_event",
        "transition_checked_in",
    ]


def test_checked_in_changed_end_reschedules_checkout() -> None:
    """Changed tracked end produces cancel-before-reschedule effects."""
    event = _event("Reserved - Ada", NOW - timedelta(hours=1), NOW + timedelta(days=2))
    snapshot = CheckinStateSnapshot(
        CHECKIN_STATE_CHECKED_IN, event.summary, event.start, NOW + timedelta(days=1)
    )
    decision = decide_coordinator_update(_ctx(snapshot, [event]))

    assert [effect.kind for effect in decision.effects][-2:] == [
        "cancel_timer",
        "schedule_auto_checkout",
    ]
    assert decision.write_state is True


def test_checked_out_without_timer_recomputes_linger() -> None:
    """Checked-out state recomputes linger when no active timer exists."""
    snapshot = CheckinStateSnapshot(CHECKIN_STATE_CHECKED_OUT, checkout_time=NOW)
    decision = decide_coordinator_update(_ctx(snapshot, []))

    assert [effect.kind for effect in decision.effects] == ["compute_linger"]
    assert decision.write_state is True


def test_no_event_in_no_reservation_writes_only() -> None:
    """No-reservation with no event requests only a state write."""
    decision = decide_coordinator_update(
        _ctx(CheckinStateSnapshot(CHECKIN_STATE_NO_RESERVATION), [])
    )

    assert decision.effects == ()
    assert decision.write_state is True
