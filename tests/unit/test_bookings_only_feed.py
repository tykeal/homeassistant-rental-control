# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Tests for bookings-only calendar feeds (issue #830)."""

from __future__ import annotations

from datetime import date
from datetime import datetime
from datetime import time
from datetime import timezone
from types import MappingProxyType
from typing import Any

from icalendar import Calendar
from icalendar import Event

from custom_components.rental_control.bookings_only import feed_is_bookings_only
from custom_components.rental_control.coordinator_helpers import calendar_parsing
from custom_components.rental_control.coordinator_helpers import reservations
from custom_components.rental_control.coordinator_helpers.models import (
    CalendarParseContext,
)
from custom_components.rental_control.coordinator_helpers.models import (
    ReservationBuildContext,
)
from custom_components.rental_control.reconciliation import make_reservation_fingerprint
from custom_components.rental_control.sensors.calsensor_helpers import slots
from custom_components.rental_control.sensors.calsensor_helpers.models import (
    SlotReadContext,
)
from custom_components.rental_control.sensors.checkin.event_selection import (
    extract_slot_name,
)
from custom_components.rental_control.util import get_slot_name

BOOKING_URL = "https://ical.booking.com/v1/export?t=abc123"
AIRBNB_URL = "https://www.airbnb.com/calendar/ical/12345.ics?s=abc"


def _parse_ctx(
    *, ignore_non_reserved: bool = False, bookings_only_feed: bool = False
) -> CalendarParseContext:
    """Return a calendar parse context for bookings-only tests."""
    return CalendarParseContext(
        timezone=timezone.utc,
        checkin=time(16, 0),
        checkout=time(10, 0),
        event_prefix=None,
        ignore_non_reserved=ignore_non_reserved,
        honor_event_times=False,
        code_buffer_before=0,
        code_buffer_after=0,
        bookings_only_feed=bookings_only_feed,
    )


def _build_ctx(*, bookings_only_feed: bool = False) -> ReservationBuildContext:
    """Return a reservation build context for bookings-only tests."""
    return ReservationBuildContext(
        entry_id="entry-1",
        timezone=timezone.utc,
        event_prefix=None,
        trim_names=False,
        max_name_length=0,
        code_buffer_before=0,
        code_buffer_after=0,
        should_update_code=False,
        code_generator="date",
        code_length=4,
        active_windows_for_name=lambda _name: set(),
        bookings_only_feed=bookings_only_feed,
    )


def _closed_event(
    start: date, end: date, summary: str = "CLOSED - Not available"
) -> Event:
    """Return a Booking.com shaped VEVENT for the given stay."""
    event = Event()
    event.add("summary", summary)
    event.add("dtstart", start)
    event.add("dtend", end)
    event.add("uid", f"{summary}-{start}-{end}")
    return event


def _parse(events: list[Event], ctx: CalendarParseContext) -> list[Any]:
    """Parse VEVENTs into CalendarEvent objects."""
    calendar = Calendar()
    for event in events:
        calendar.add_component(event)
    return calendar_parsing.parse_calendar(
        calendar,
        datetime(2026, 10, 1, tzinfo=timezone.utc),
        datetime(2027, 12, 31, tzinfo=timezone.utc),
        ctx,
    )


def test_booking_com_event_gets_synthesized_name_and_code() -> None:
    """A nameless Booking.com stay becomes a coded reservation."""
    calendar = _parse(
        [_closed_event(date(2026, 10, 23), date(2026, 10, 25))], _parse_ctx()
    )

    built = reservations.build_reservations(
        calendar, None, _build_ctx(bookings_only_feed=True)
    )

    assert len(built) == 1
    assert built[0].slot_name == "Booking 23 Oct - 25 Oct 2026"
    assert built[0].display_slot_name == "Booking 23 Oct - 25 Oct 2026"
    assert built[0].slot_code is not None
    assert len(built[0].slot_code) == 4


def test_booking_com_event_still_discarded_when_disabled() -> None:
    """Without the opt-in a blocked-looking event issues no code."""
    calendar = _parse(
        [_closed_event(date(2026, 10, 23), date(2026, 10, 25))], _parse_ctx()
    )

    assert reservations.build_reservations(calendar, None, _build_ctx()) == []


def test_ignore_non_reserved_yields_to_bookings_only() -> None:
    """Bookings-only overrides the non-reserved summary filter."""
    events = [_closed_event(date(2026, 10, 23), date(2026, 10, 25))]

    filtered = _parse(events, _parse_ctx(ignore_non_reserved=True))
    kept = _parse(events, _parse_ctx(ignore_non_reserved=True, bookings_only_feed=True))

    assert filtered == []
    built = reservations.build_reservations(
        kept, None, _build_ctx(bookings_only_feed=True)
    )
    assert len(built) == 1
    assert built[0].slot_code


def test_synthesized_identity_is_stable_across_refreshes() -> None:
    """Rebuilding the same calendar yields the same reservation identity."""
    ctx = _build_ctx(bookings_only_feed=True)
    calendar = _parse(
        [_closed_event(date(2026, 10, 23), date(2026, 10, 25))], _parse_ctx()
    )

    first = reservations.build_reservations(calendar, None, ctx)
    second = reservations.build_reservations(calendar, None, ctx)

    assert [res.identity_key for res in first] == [res.identity_key for res in second]
    assert first[0].slot_code == second[0].slot_code


def test_same_dates_in_different_years_do_not_collide() -> None:
    """The synthesized name carries the year so stays stay distinct."""
    calendar = _parse(
        [
            _closed_event(date(2026, 10, 23), date(2026, 10, 25)),
            _closed_event(date(2027, 10, 23), date(2027, 10, 25)),
        ],
        _parse_ctx(),
    )

    built = reservations.build_reservations(
        calendar, None, _build_ctx(bookings_only_feed=True)
    )

    names = {res.slot_name for res in built}
    assert names == {
        "Booking 23 Oct - 25 Oct 2026",
        "Booking 23 Oct - 25 Oct 2027",
    }


def test_named_closed_event_keeps_its_guest_name() -> None:
    """``CLOSED - Smith`` still resolves to ``Smith`` either way."""
    assert get_slot_name("CLOSED - Smith", "", "") == "Smith"
    calendar = _parse(
        [_closed_event(date(2026, 10, 23), date(2026, 10, 25), "CLOSED - Smith")],
        _parse_ctx(),
    )

    for bookings_only in (False, True):
        built = reservations.build_reservations(
            calendar, None, _build_ctx(bookings_only_feed=bookings_only)
        )
        assert [res.slot_name for res in built] == ["Smith"]


def test_default_derives_from_booking_com_host() -> None:
    """A Booking.com entry with no stored choice defaults on."""
    assert feed_is_bookings_only(MappingProxyType({"url": BOOKING_URL})) is True
    assert feed_is_bookings_only({"url": "https://booking.com/export.ics"}) is True
    assert feed_is_bookings_only({"url": AIRBNB_URL}) is False


def test_explicit_choice_overrides_host_default() -> None:
    """An operator exporting closed dates can keep the filter."""
    config = MappingProxyType({"url": BOOKING_URL, "bookings_only_feed": False})

    assert feed_is_bookings_only(config) is False
    assert (
        feed_is_bookings_only({"url": AIRBNB_URL, "bookings_only_feed": True}) is True
    )


def test_unusable_url_fails_safe() -> None:
    """A missing or malformed URL never enables bookings-only."""
    assert feed_is_bookings_only({}) is False
    assert feed_is_bookings_only({"url": None}) is False
    assert feed_is_bookings_only({"url": "not a url"}) is False
    assert feed_is_bookings_only(None) is False


def test_host_match_is_not_a_substring_match() -> None:
    """``booking.com`` in a path or query does not enable the default."""
    assert feed_is_bookings_only(
        {"url": "https://pms.example.com/booking.com.ics"}
    ) is (False)
    assert (
        feed_is_bookings_only({"url": "https://example.com/ics?src=ical.booking.com"})
        is False
    )
    assert feed_is_bookings_only({"url": "https://booking.com.evil.net/x.ics"}) is False


def test_calendar_sensor_reads_synthesized_slot() -> None:
    """The event sensor publishes the code the coordinator programmed."""
    event = _parse(
        [_closed_event(date(2026, 10, 23), date(2026, 10, 25))], _parse_ctx()
    )[0]
    built = reservations.build_reservations(
        [event], None, _build_ctx(bookings_only_feed=True)
    )
    identity_key = built[0].identity_key

    class _Coordinator:
        """Minimal coordinator exposing reconciliation lookups."""

        def get_slot_assignment(self, key: str) -> int | None:
            """Return the slot assigned to an identity key."""
            return 3 if key == identity_key else None

        def get_slot_code(self, key: str) -> str | None:
            """Return the code issued for an identity key."""
            return built[0].slot_code if key == identity_key else None

    context = SlotReadContext(
        entry_id="entry-1",
        summary=event.summary,
        description=event.description,
        event_prefix="",
        start=event.start,
        end=event.end,
        get_slot_name=get_slot_name,
        make_reservation_fingerprint=make_reservation_fingerprint,
        bookings_only_feed=True,
    )

    result = slots.read_slot(context, _Coordinator())

    assert result.slot_name == built[0].slot_name
    assert result.slot_number == 3
    assert result.slot_code == built[0].slot_code


def test_checkin_slot_name_matches_the_coordinator() -> None:
    """Check-in tracking names an anonymous stay like the coordinator."""
    event = _parse(
        [_closed_event(date(2026, 10, 23), date(2026, 10, 25))], _parse_ctx()
    )[0]
    built = reservations.build_reservations(
        [event], None, _build_ctx(bookings_only_feed=True)
    )

    assert extract_slot_name(event, "", True) == built[0].slot_name
    assert extract_slot_name(event, "", False) is None
