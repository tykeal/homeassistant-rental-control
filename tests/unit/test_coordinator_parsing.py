# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Tests for the pure calendar-parsing coordinator helpers."""

from __future__ import annotations

from datetime import date
from datetime import datetime
from datetime import time
from datetime import timedelta
from datetime import timezone
from datetime import tzinfo
from typing import Any
from typing import Callable
from typing import Mapping
from zoneinfo import ZoneInfo

from icalendar import Calendar
from icalendar import Event
import pytest

from custom_components.rental_control.const import EVENT_AGE_THRESHOLD_DAYS
from custom_components.rental_control.coordinator_helpers import calendar_parsing
from custom_components.rental_control.coordinator_helpers.models import (
    CalendarParseContext,
)


def _parse_ctx(
    *,
    event_prefix: str | None = None,
    ignore_non_reserved: bool = False,
    honor_event_times: bool = False,
    timezone_: tzinfo = timezone.utc,
    override_lookup: Callable[[str], Mapping[str, Any] | None] | None = None,
) -> CalendarParseContext:
    """Return a minimal calendar parse context for tests."""
    return CalendarParseContext(
        timezone=timezone_,
        checkin=time(16, 0),
        checkout=time(10, 0),
        event_prefix=event_prefix,
        ignore_non_reserved=ignore_non_reserved,
        honor_event_times=honor_event_times,
        code_buffer_before=0,
        code_buffer_after=0,
        override_lookup=override_lookup,
    )


def _calendar_with_event(event: Event) -> Calendar:
    """Return a calendar containing one VEVENT."""
    calendar = Calendar()
    calendar.add_component(event)
    return calendar


def _calendar_from_ics(body: str) -> Calendar:
    """Return a calendar parsed from a synthetic iCalendar payload."""
    return Calendar.from_ical(
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//EN\r\n"
        f"{body}\r\nEND:VCALENDAR\r\n"
    )


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


@pytest.mark.parametrize("honor_event_times", [True, False])
def test_parse_calendar_keeps_tzid_instants_in_calendar_timezone(
    honor_event_times: bool,
) -> None:
    """Selected TZID instants are rendered in configured calendar timezone."""
    calendar = _calendar_from_ics(
        "BEGIN:VEVENT\r\n"
        "DTSTART;TZID=America/New_York:20261122T160000\r\n"
        "DTEND;TZID=America/New_York:20261123T100000\r\n"
        "UID:synthetic-tzid@example.invalid\r\n"
        "SUMMARY:Reservation Guest\r\n"
        "END:VEVENT"
    )
    hawaii = ZoneInfo("Pacific/Honolulu")

    events = calendar_parsing.parse_calendar(
        calendar,
        datetime(2026, 11, 1, tzinfo=timezone.utc),
        datetime(2026, 12, 1, tzinfo=timezone.utc),
        _parse_ctx(timezone_=hawaii, honor_event_times=honor_event_times),
    )

    assert len(events) == 1
    assert events[0].start == datetime(2026, 11, 22, 11, tzinfo=hawaii)
    assert events[0].end == datetime(2026, 11, 23, 5, tzinfo=hawaii)


@pytest.mark.parametrize("honor_event_times", [True, False])
def test_parse_calendar_keeps_utc_instants_across_dates_and_dst(
    honor_event_times: bool,
) -> None:
    """UTC event instants cross dates without reinterpreting their wall time."""
    calendar = _calendar_from_ics(
        "BEGIN:VEVENT\r\n"
        "DTSTART:20261101T053000Z\r\n"
        "DTEND:20261101T073000Z\r\n"
        "UID:synthetic-utc@example.invalid\r\n"
        "SUMMARY:Reservation Guest\r\n"
        "END:VEVENT"
    )
    hawaii = ZoneInfo("Pacific/Honolulu")

    events = calendar_parsing.parse_calendar(
        calendar,
        datetime(2026, 10, 1, tzinfo=timezone.utc),
        datetime(2026, 11, 30, tzinfo=timezone.utc),
        _parse_ctx(timezone_=hawaii, honor_event_times=honor_event_times),
    )

    assert len(events) == 1
    assert events[0].start == datetime(2026, 10, 31, 19, 30, tzinfo=hawaii)
    assert events[0].end == datetime(2026, 10, 31, 21, 30, tzinfo=hawaii)


def test_parse_calendar_keeps_tzid_instants_across_fall_back() -> None:
    """TZID endpoints on either side of DST fall-back retain their offsets."""
    calendar = _calendar_from_ics(
        "BEGIN:VEVENT\r\n"
        "DTSTART;TZID=America/New_York:20261031T233000\r\n"
        "DTEND;TZID=America/New_York:20261101T023000\r\n"
        "UID:synthetic-fall-back@example.invalid\r\n"
        "SUMMARY:Reservation Guest\r\n"
        "END:VEVENT"
    )
    hawaii = ZoneInfo("Pacific/Honolulu")

    events = calendar_parsing.parse_calendar(
        calendar,
        datetime(2026, 10, 1, tzinfo=timezone.utc),
        datetime(2026, 11, 30, tzinfo=timezone.utc),
        _parse_ctx(timezone_=hawaii, honor_event_times=True),
    )

    assert len(events) == 1
    assert events[0].start == datetime(2026, 10, 31, 17, 30, tzinfo=hawaii)
    assert events[0].end == datetime(2026, 10, 31, 21, 30, tzinfo=hawaii)


def test_parse_calendar_filter_uses_local_date_for_aware_start() -> None:
    """A cross-date aware event within the local horizon is not filtered."""
    calendar = _calendar_from_ics(
        "BEGIN:VEVENT\r\n"
        "DTSTART:20260701T020000Z\r\n"
        "DTEND:20260701T030000Z\r\n"
        "UID:synthetic-boundary@example.invalid\r\n"
        "SUMMARY:Reservation Guest\r\n"
        "END:VEVENT"
    )
    hawaii = ZoneInfo("Pacific/Honolulu")

    events = calendar_parsing.parse_calendar(
        calendar,
        datetime(2026, 6, 1, tzinfo=timezone.utc),
        datetime(2026, 6, 30, 23, 59, tzinfo=timezone.utc),
        _parse_ctx(timezone_=hawaii, honor_event_times=True),
    )

    assert len(events) == 1
    assert events[0].start == datetime(2026, 6, 30, 16, tzinfo=hawaii)


def test_parse_calendar_preserves_midnight_end_boundary() -> None:
    """An event ending at the parse boundary's midnight remains excluded."""
    calendar = _calendar_from_ics(
        "BEGIN:VEVENT\r\n"
        "DTSTART:20261122T220000Z\r\n"
        "DTEND:20261123T000000Z\r\n"
        "UID:synthetic-midnight@example.invalid\r\n"
        "SUMMARY:Reservation Guest\r\n"
        "END:VEVENT"
    )

    events = calendar_parsing.parse_calendar(
        calendar,
        datetime(2026, 11, 23, tzinfo=timezone.utc),
        datetime(2026, 12, 1, tzinfo=timezone.utc),
        _parse_ctx(honor_event_times=True),
    )

    assert events == []


def test_parse_calendar_keeps_floating_time_and_override_precedence() -> None:
    """Floating times stay local and a stored override still wins."""
    calendar = _calendar_from_ics(
        "BEGIN:VEVENT\r\n"
        "DTSTART:20261122T160000\r\n"
        "DTEND:20261123T100000\r\n"
        "UID:synthetic-floating@example.invalid\r\n"
        "SUMMARY:Reservation Guest\r\n"
        "END:VEVENT"
    )
    hawaii = ZoneInfo("Pacific/Honolulu")
    override = {
        "start_time": datetime(2026, 11, 22, 20, tzinfo=timezone.utc),
        "end_time": datetime(2026, 11, 23, 18, tzinfo=timezone.utc),
    }

    floating = calendar_parsing.parse_calendar(
        calendar,
        datetime(2026, 11, 1, tzinfo=timezone.utc),
        datetime(2026, 12, 1, tzinfo=timezone.utc),
        _parse_ctx(timezone_=hawaii, honor_event_times=True),
    )
    overridden = calendar_parsing.parse_calendar(
        calendar,
        datetime(2026, 11, 1, tzinfo=timezone.utc),
        datetime(2026, 12, 1, tzinfo=timezone.utc),
        _parse_ctx(
            timezone_=hawaii,
            honor_event_times=False,
            override_lookup=lambda _name: override,
        ),
    )

    assert floating[0].start == datetime(2026, 11, 22, 16, tzinfo=hawaii)
    assert floating[0].end == datetime(2026, 11, 23, 10, tzinfo=hawaii)
    assert overridden[0].start == datetime(2026, 11, 22, 10, tzinfo=hawaii)
    assert overridden[0].end == datetime(2026, 11, 23, 8, tzinfo=hawaii)


def test_vevent_filter_handles_timed_stale_events() -> None:
    """Timed stale events are filtered using their calendar date."""
    from_date = datetime(2026, 6, 30, tzinfo=timezone.utc)
    event = Event()
    event.add("summary", "Alice")
    event.add("dtstart", from_date - timedelta(days=EVENT_AGE_THRESHOLD_DAYS + 2))
    event.add("dtend", from_date - timedelta(days=EVENT_AGE_THRESHOLD_DAYS + 1))

    assert calendar_parsing._vevent_filtered(
        event, from_date, datetime(2026, 7, 30, tzinfo=timezone.utc), _parse_ctx()
    )


def test_vevent_filter_handles_timed_future_events() -> None:
    """Timed future events are filtered using their calendar date."""
    to_date = datetime(2026, 6, 30, tzinfo=timezone.utc)
    event = Event()
    event.add("summary", "Alice")
    event.add("dtstart", to_date + timedelta(days=1))
    event.add("dtend", to_date + timedelta(days=2))

    assert calendar_parsing._vevent_filtered(
        event, datetime(2026, 6, 1, tzinfo=timezone.utc), to_date, _parse_ctx()
    )


def test_vevent_filter_keeps_all_day_boundaries() -> None:
    """All-day stale and future filtering still uses date values."""
    stale = Event()
    stale.add("summary", "Alice")
    stale.add("dtstart", date(2026, 5, 29))
    stale.add("dtend", date(2026, 5, 30))

    future = Event()
    future.add("summary", "Bob")
    future.add("dtstart", date(2026, 7, 1))
    future.add("dtend", date(2026, 7, 2))

    assert calendar_parsing._vevent_filtered(
        stale,
        datetime(2026, 6, 30, tzinfo=timezone.utc),
        datetime(2026, 7, 30, tzinfo=timezone.utc),
        _parse_ctx(),
    )
    assert calendar_parsing._vevent_filtered(
        future,
        datetime(2026, 6, 1, tzinfo=timezone.utc),
        datetime(2026, 6, 30, tzinfo=timezone.utc),
        _parse_ctx(),
    )
