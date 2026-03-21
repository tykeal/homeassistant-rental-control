# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Test fixture data for check-in tracking sensor tests.

Provides helper functions that return sample coordinator event data as
``list[CalendarEvent]`` matching ``RentalControlCoordinator.data`` for
various test scenarios.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta

from homeassistant.components.calendar import CalendarEvent
from homeassistant.util import dt as dt_util


def single_future_event(
    hours_until_start: int = 24,
    duration_hours: int = 120,
    summary: str = "Reserved - John Smith",
    description: str = "Guest: John Smith\nPhone: +1234567890",
) -> list[CalendarEvent]:
    """Return coordinator data with a single future event.

    Args:
        hours_until_start: Hours from now until event starts.
        duration_hours: Duration of the event in hours.
        summary: Event summary text.
        description: Event description text.

    Returns:
        List containing one CalendarEvent starting in the future.
    """
    now = dt_util.now()
    start = now + timedelta(hours=hours_until_start)
    end = start + timedelta(hours=duration_hours)
    return [
        CalendarEvent(
            summary=summary,
            start=start,
            end=end,
            description=description,
        )
    ]


def active_event(
    started_hours_ago: int = 2,
    ends_in_hours: int = 48,
    summary: str = "Reserved - Jane Doe",
    description: str = "Guest: Jane Doe\nPhone: +0987654321",
) -> list[CalendarEvent]:
    """Return coordinator data with an active event (started, not ended).

    Args:
        started_hours_ago: Hours since the event started.
        ends_in_hours: Hours until the event ends.
        summary: Event summary text.
        description: Event description text.

    Returns:
        List containing one active CalendarEvent.
    """
    now = dt_util.now()
    start = now - timedelta(hours=started_hours_ago)
    end = now + timedelta(hours=ends_in_hours)
    return [
        CalendarEvent(
            summary=summary,
            start=start,
            end=end,
            description=description,
        )
    ]


def past_event(
    ended_hours_ago: int = 6,
    duration_hours: int = 120,
    summary: str = "Reserved - Past Guest",
    description: str = "Guest: Past Guest",
) -> list[CalendarEvent]:
    """Return coordinator data with a past event (already ended).

    Args:
        ended_hours_ago: Hours since the event ended.
        duration_hours: Duration of the event in hours.
        summary: Event summary text.
        description: Event description text.

    Returns:
        List containing one past CalendarEvent.
    """
    now = dt_util.now()
    end = now - timedelta(hours=ended_hours_ago)
    start = end - timedelta(hours=duration_hours)
    return [
        CalendarEvent(
            summary=summary,
            start=start,
            end=end,
            description=description,
        )
    ]


def same_day_turnover_pair(
    first_ends_in_hours: int = 2,
    gap_hours: int = 4,
    first_summary: str = "Reserved - Alice",
    second_summary: str = "Reserved - Bob",
) -> list[CalendarEvent]:
    """Return coordinator data with a same-day turnover pair.

    Event 0 ends today; event 1 starts the same day.

    Args:
        first_ends_in_hours: Hours until the first event ends.
        gap_hours: Hours between first event end and second event start.
        first_summary: Summary for the first event.
        second_summary: Summary for the second event.

    Returns:
        List of two CalendarEvents representing a same-day turnover.
    """
    now = dt_util.now()
    first_start = now - timedelta(hours=48)
    first_end = now + timedelta(hours=first_ends_in_hours)
    second_start = first_end + timedelta(hours=gap_hours)
    second_end = second_start + timedelta(hours=120)
    return [
        CalendarEvent(
            summary=first_summary,
            start=first_start,
            end=first_end,
            description="Guest: Alice",
        ),
        CalendarEvent(
            summary=second_summary,
            start=second_start,
            end=second_end,
            description="Guest: Bob",
        ),
    ]


def different_day_followon_pair(
    first_ends_in_hours: int = 2,
    days_until_second: int = 3,
    first_summary: str = "Reserved - Carol",
    second_summary: str = "Reserved - Dave",
) -> list[CalendarEvent]:
    """Return coordinator data with a different-day follow-on pair.

    Event 0 ends today; event 1 starts on a different day.

    Args:
        first_ends_in_hours: Hours until the first event ends.
        days_until_second: Days until the second event starts.
        first_summary: Summary for the first event.
        second_summary: Summary for the second event.

    Returns:
        List of two CalendarEvents on different days.
    """
    now = dt_util.now()
    first_start = now - timedelta(hours=48)
    first_end = now + timedelta(hours=first_ends_in_hours)
    second_start = first_end + timedelta(days=days_until_second)
    second_end = second_start + timedelta(hours=120)
    return [
        CalendarEvent(
            summary=first_summary,
            start=first_start,
            end=first_end,
            description="Guest: Carol",
        ),
        CalendarEvent(
            summary=second_summary,
            start=second_start,
            end=second_end,
            description="Guest: Dave",
        ),
    ]


def no_events() -> list[CalendarEvent]:
    """Return empty coordinator data (no events).

    Returns:
        Empty list representing no calendar events.
    """
    return []


def event_at_times(
    start: datetime,
    end: datetime,
    summary: str = "Reserved - Test Guest",
    description: str = "Guest: Test Guest",
) -> list[CalendarEvent]:
    """Return coordinator data with an event at specific times.

    Args:
        start: Exact start datetime for the event.
        end: Exact end datetime for the event.
        summary: Event summary text.
        description: Event description text.

    Returns:
        List containing one CalendarEvent at the specified times.
    """
    return [
        CalendarEvent(
            summary=summary,
            start=start,
            end=end,
            description=description,
        )
    ]
