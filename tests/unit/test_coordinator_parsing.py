# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Tests for the pure calendar-parsing coordinator helpers."""

from __future__ import annotations

from datetime import date
from datetime import datetime
from datetime import time
from datetime import timezone

from icalendar import Calendar
from icalendar import Event

from custom_components.rental_control.coordinator_helpers import calendar_parsing
from custom_components.rental_control.coordinator_helpers.models import (
    CalendarParseContext,
)


def _parse_ctx(
    *,
    event_prefix: str | None = None,
    ignore_non_reserved: bool = False,
    honor_event_times: bool = False,
) -> CalendarParseContext:
    """Return a minimal calendar parse context for tests."""
    return CalendarParseContext(
        timezone=timezone.utc,
        checkin=time(16, 0),
        checkout=time(10, 0),
        event_prefix=event_prefix,
        ignore_non_reserved=ignore_non_reserved,
        honor_event_times=honor_event_times,
        code_buffer_before=0,
        code_buffer_after=0,
    )


def _calendar_with_event(event: Event) -> Calendar:
    """Return a calendar containing one VEVENT."""
    calendar = Calendar()
    calendar.add_component(event)
    return calendar


def test_datetimes_match_true_for_equal_instants() -> None:
    """Equal instants in different zones still match."""
    left = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    right = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    assert calendar_parsing.datetimes_match(left, right, timezone.utc) is True


def test_datetimes_match_false_for_different_instants() -> None:
    """Different instants do not match."""
    left = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    right = datetime(2026, 1, 1, 13, 0, tzinfo=timezone.utc)
    assert calendar_parsing.datetimes_match(left, right, timezone.utc) is False


def test_combine_event_time_returns_datetime() -> None:
    """Combining a date with a time produces an aware datetime."""
    base = datetime(2026, 6, 15, tzinfo=timezone.utc)
    result = calendar_parsing.combine_event_time(base, time(11, 30), timezone.utc)
    assert isinstance(result, datetime)
    assert result.hour == 11
    assert result.minute == 30


def test_parse_calendar_tolerates_missing_summary() -> None:
    """A VEVENT without SUMMARY is handled as an empty-summary event."""
    event = Event()
    event.add("dtstart", date(2026, 6, 15))
    event.add("dtend", date(2026, 6, 16))

    events = calendar_parsing.parse_calendar(
        _calendar_with_event(event),
        datetime(2026, 6, 1, tzinfo=timezone.utc),
        datetime(2026, 6, 30, tzinfo=timezone.utc),
        _parse_ctx(),
    )

    assert len(events) == 1
    assert events[0].summary == ""


def test_parse_calendar_prefixes_missing_summary_without_trailing_space() -> None:
    """A missing-summary VEVENT with a prefix has no trailing space."""
    event = Event()
    event.add("dtstart", date(2026, 6, 15))
    event.add("dtend", date(2026, 6, 16))

    events = calendar_parsing.parse_calendar(
        _calendar_with_event(event),
        datetime(2026, 6, 1, tzinfo=timezone.utc),
        datetime(2026, 6, 30, tzinfo=timezone.utc),
        _parse_ctx(event_prefix="RC"),
    )

    assert len(events) == 1
    assert events[0].summary == "RC"


def test_select_event_times_defaults_without_dtend() -> None:
    """A timed VEVENT without DTEND uses configured default times."""
    event = Event()
    event.add("dtstart", datetime(2026, 6, 15, 12, tzinfo=timezone.utc))

    assert calendar_parsing._select_event_times(event, None, _parse_ctx()) == (
        time(16, 0),
        time(10, 0),
    )


def test_parse_calendar_preserves_missing_dtend_zero_length() -> None:
    """A timed VEVENT without DTEND keeps the existing zero-length stay."""
    event = Event()
    event.add("summary", "Alice")
    event.add("dtstart", datetime(2026, 6, 15, 12, tzinfo=timezone.utc))

    events = calendar_parsing.parse_calendar(
        _calendar_with_event(event),
        datetime(2026, 6, 1, tzinfo=timezone.utc),
        datetime(2026, 6, 30, tzinfo=timezone.utc),
        _parse_ctx(),
    )

    assert len(events) == 1
    assert events[0].end == events[0].start
