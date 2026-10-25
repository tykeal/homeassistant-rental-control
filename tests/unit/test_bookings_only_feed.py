# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Tests for bookings-only calendar feeds (issues #830 and #864)."""

from __future__ import annotations

from datetime import date
from datetime import datetime
from datetime import time
from datetime import timedelta
from datetime import timezone
from types import MappingProxyType
from types import SimpleNamespace
from typing import Any

from homeassistant.const import CONF_URL
from icalendar import Calendar
from icalendar import Event

from custom_components.rental_control.bookings_only import feed_is_bookings_only
from custom_components.rental_control.bookings_only import synthesized_slot_name
from custom_components.rental_control.bookings_only import uid_slot_name
from custom_components.rental_control.bookings_only import uid_slot_token
from custom_components.rental_control.config_flow_helpers.validation import (
    apply_successful_conversions,
)
from custom_components.rental_control.const import CONF_BOOKINGS_ONLY_FEED
from custom_components.rental_control.const import CONF_LOCK_ENTRY
from custom_components.rental_control.coordinator_helpers import calendar_parsing
from custom_components.rental_control.coordinator_helpers import reservations
from custom_components.rental_control.coordinator_helpers.models import (
    CalendarParseContext,
)
from custom_components.rental_control.coordinator_helpers.models import (
    ReservationBuildContext,
)
from custom_components.rental_control.reconciliation import ManagedSlot
from custom_components.rental_control.reconciliation import SlotStatus
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
    start: date,
    end: date,
    summary: str = "CLOSED - Not available",
    uid: str | None = "7654321@booking.com",
) -> Event:
    """Return a Booking.com shaped VEVENT for the given stay.

    ``uid`` is explicit because the slot name of an anonymous stay is
    derived from it; ``None`` models a feed that publishes no UID.
    """
    event = Event()
    event.add("summary", summary)
    event.add("dtstart", start)
    event.add("dtend", end)
    if uid is not None:
        event.add("uid", uid)
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

    expected = uid_slot_name("7654321@booking.com")
    assert len(built) == 1
    assert built[0].slot_name == expected
    assert built[0].display_slot_name == expected
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
    """The date-derived fallback carries the year so stays stay distinct."""
    calendar = _parse(
        [
            _closed_event(date(2026, 10, 23), date(2026, 10, 25), uid=None),
            _closed_event(date(2027, 10, 23), date(2027, 10, 25), uid=None),
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
    assert all(res.slot_code for res in built)


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
        uid=event.uid,
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


def _capture_queries(monkeypatch: Any) -> list[Any]:
    """Record every observed-slot query the reservation builder makes."""
    captured: list[Any] = []
    real = reservations.find_observed_slot

    def _spy(query: Any) -> Any:
        """Record a query and defer to the real matcher."""
        captured.append(query)
        return real(query)

    monkeypatch.setattr(reservations, "find_observed_slot", _spy)
    return captured


def _managed_slot(
    slot: int, name: str, start: datetime, end: datetime, code: str
) -> ManagedSlot:
    """Return an observed, managed Keymaster slot."""
    return ManagedSlot(
        slot=slot,
        managed=True,
        status=SlotStatus.OCCUPIED,
        actual_name=name,
        actual_code=code,
        actual_code_present=True,
        actual_start=start,
        actual_end=end,
    )


def test_anonymous_reservation_carries_its_date_windows(monkeypatch: Any) -> None:
    """An anonymous stay matches against its own reserved date windows."""
    event = _parse(
        [_closed_event(date(2026, 10, 23), date(2026, 10, 25))], _parse_ctx()
    )[0]
    name = uid_slot_name("7654321@booking.com")
    assert name is not None
    observed = [_managed_slot(1, name, event.start, event.end, "1111")]
    captured = _capture_queries(monkeypatch)

    reservations.build_reservations(
        [event], observed, _build_ctx(bookings_only_feed=True)
    )

    assert len(captured) == 1
    assert captured[0].reserved_date_windows == {(event.start, event.end)}
    assert captured[0].ordered_date_windows == [(event.start, event.end)]


def test_duplicate_anonymous_stays_require_a_date_match(monkeypatch: Any) -> None:
    """Two stays sharing a synthesized name are counted and date-matched.

    A feed can repeat a UID across two VEVENTs, and a feed publishing no
    UID at all names both stays after the same dates, so anonymous names
    can still collide and the duplicate-handling path must survive.
    """
    events = _parse(
        [
            _closed_event(date(2026, 10, 23), date(2026, 10, 25)),
            _closed_event(
                date(2026, 10, 23), date(2026, 10, 25), "CLOSED - Not available "
            ),
        ],
        _parse_ctx(),
    )
    name = uid_slot_name("7654321@booking.com")
    assert name is not None
    observed = [
        _managed_slot(1, name, events[0].start, events[0].end, "1111"),
        _managed_slot(2, name, events[0].start, events[0].end, "2222"),
    ]
    captured = _capture_queries(monkeypatch)

    reservations.build_reservations(
        events, observed, _build_ctx(bookings_only_feed=True)
    )

    assert len(captured) == 2
    assert all(query.require_date_match for query in captured)
    assert [query.expected_name_count for query in captured] == [2, 2]


def test_anonymous_stay_does_not_adopt_a_reserved_window_pin() -> None:
    """Known windows fence an anonymous stay off a stale slot's PIN."""
    event = _parse(
        [_closed_event(date(2026, 10, 23), date(2026, 10, 25))], _parse_ctx()
    )[0]
    name = uid_slot_name("7654321@booking.com")
    assert name is not None
    stale_start = datetime(2026, 10, 1, 16, 0, tzinfo=timezone.utc)
    stale_end = datetime(2026, 10, 3, 10, 0, tzinfo=timezone.utc)
    observed = [_managed_slot(1, name, stale_start, stale_end, "1111")]

    ctx = _build_ctx(bookings_only_feed=True)
    ctx.active_windows_for_name = lambda _name: {(stale_start, stale_end)}

    built = reservations.build_reservations([event], observed, ctx)

    assert len(built) == 1
    assert built[0].slot_code != "1111"
    assert built[0].code_source == "generated"


def _capture_matches(monkeypatch: Any) -> list[Any]:
    """Record every physical slot the reservation builder matched."""
    matched: list[Any] = []
    real = reservations.find_observed_slot

    def _spy(query: Any) -> Any:
        """Record the matched slot and defer to the real matcher."""
        result = real(query)
        matched.append(result)
        return result

    monkeypatch.setattr(reservations, "find_observed_slot", _spy)
    return matched


def test_amended_anonymous_stay_keeps_its_slot_and_pin(monkeypatch: Any) -> None:
    """Amending an anonymous checkout keeps the slot and the code.

    The slot name is derived from the stable VEVENT UID, so both the
    name-based matching layers still recognise the amended stay as the
    reservation already programmed into the lock (issue #864).
    """
    uid = "7654321@booking.com"
    original = _parse(
        [_closed_event(date(2026, 10, 23), date(2026, 10, 25), uid=uid)], _parse_ctx()
    )[0]
    amended = _parse(
        [_closed_event(date(2026, 10, 23), date(2026, 10, 26), uid=uid)], _parse_ctx()
    )[0]
    name = uid_slot_name(uid)
    assert name is not None
    observed = [_managed_slot(1, name, original.start, original.end, "1111")]
    matched = _capture_matches(monkeypatch)

    built = reservations.build_reservations(
        [amended], observed, _build_ctx(bookings_only_feed=True)
    )

    assert len(built) == 1
    assert built[0].slot_name == name
    assert matched[0] is not None
    assert matched[0].slot == 1
    assert built[0].slot_code == "1111"


def test_uid_token_is_deterministic() -> None:
    """A fixed UID always renders the same Crockford base32 token.

    The token names a lock slot, so any change to the derivation would
    silently reissue every anonymous guest's PIN.
    """
    assert uid_slot_token("7654321@booking.com") == "AGBN1Z"
    assert uid_slot_token("  7654321@booking.com  ") == "AGBN1Z"
    assert uid_slot_name("7654321@booking.com") == "Booking AGBN1Z"
    assert uid_slot_token(None) is None
    assert uid_slot_token("   ") is None


def test_uid_less_anonymous_event_falls_back_to_dates() -> None:
    """A feed with no UID still names and codes an anonymous stay."""
    calendar = _parse(
        [_closed_event(date(2026, 10, 23), date(2026, 10, 25), uid=None)], _parse_ctx()
    )

    built = reservations.build_reservations(
        calendar, None, _build_ctx(bookings_only_feed=True)
    )

    assert len(built) == 1
    assert built[0].slot_name == synthesized_slot_name(
        calendar[0].start, calendar[0].end
    )
    assert built[0].slot_code


def test_uid_name_follows_the_uid_not_the_dates() -> None:
    """The name tracks the UID: dates may move, a new UID may not."""
    uid = "7654321@booking.com"
    first = _parse(
        [_closed_event(date(2026, 10, 23), date(2026, 10, 25), uid=uid)], _parse_ctx()
    )
    moved = _parse(
        [_closed_event(date(2026, 11, 3), date(2026, 11, 9), uid=uid)], _parse_ctx()
    )
    other = _parse(
        [_closed_event(date(2026, 10, 23), date(2026, 10, 25), uid="9999@booking.com")],
        _parse_ctx(),
    )
    ctx = _build_ctx(bookings_only_feed=True)

    names = [
        reservations.build_reservations(events, None, ctx)[0].slot_name
        for events in (first, moved, other)
    ]

    assert names[0] == names[1]
    assert names[0] != names[2]


def _override(start: datetime, end: datetime) -> dict[str, Any]:
    """Return a Keymaster physical time override."""
    return {"start_time": start, "end_time": end}


def test_time_override_applies_to_an_anonymous_stay() -> None:
    """A UID-named anonymous stay can be looked up for time overrides.

    The UID is on the raw VEVENT and depends on neither the dates nor
    the UTC conversion, so synthesizing the name here cannot disagree
    with the name the reservation builder computes.
    """
    uid = "7654321@booking.com"
    expected_name = uid_slot_name(uid)
    looked_up: list[str] = []

    def _lookup(name: str) -> dict[str, Any] | None:
        """Return the override registered for the anonymous stay."""
        looked_up.append(name)
        if name != expected_name:
            return None
        return _override(
            datetime(2026, 10, 23, 14, 0, tzinfo=timezone.utc),
            datetime(2026, 10, 25, 11, 0, tzinfo=timezone.utc),
        )

    ctx = _parse_ctx(bookings_only_feed=True)
    ctx.override_lookup = _lookup

    parsed = _parse(
        [_closed_event(date(2026, 10, 23), date(2026, 10, 25), uid=uid)], ctx
    )

    assert looked_up == [expected_name]
    assert parsed[0].start == datetime(2026, 10, 23, 14, 0, tzinfo=timezone.utc)
    assert parsed[0].end == datetime(2026, 10, 25, 11, 0, tzinfo=timezone.utc)


def test_uid_less_anonymous_stay_skips_the_override_lookup() -> None:
    """Without a UID no name is synthesized for the override lookup.

    The date-derived fallback is still date-dependent, and the override
    decides the check-in time that decides the UTC start, so using it
    here could produce a different name from the reservation builder.
    """
    looked_up: list[str] = []

    def _lookup(name: str) -> dict[str, Any] | None:
        """Record a lookup and register no override."""
        looked_up.append(name)
        return None

    ctx = _parse_ctx(bookings_only_feed=True)
    ctx.override_lookup = _lookup

    _parse([_closed_event(date(2026, 10, 23), date(2026, 10, 25), uid=None)], ctx)

    assert looked_up == []


def test_parsing_and_reservations_agree_across_a_day_shift() -> None:
    """Both name resolution sites agree when UTC shifts the local day."""
    uid = "7654321@booking.com"
    shifting = CalendarParseContext(
        timezone=timezone(-timedelta(hours=10)),
        checkin=time(16, 0),
        checkout=time(10, 0),
        event_prefix=None,
        ignore_non_reserved=False,
        honor_event_times=False,
        code_buffer_before=0,
        code_buffer_after=0,
        bookings_only_feed=True,
    )
    parse_names: list[str] = []

    def _record(name: str) -> None:
        """Record the name the parser resolved; register no override."""
        parse_names.append(name)
        return None

    shifting.override_lookup = _record

    parsed = _parse(
        [_closed_event(date(2026, 10, 23), date(2026, 10, 25), uid=uid)], shifting
    )

    built = reservations.build_reservations(
        parsed, None, _build_ctx(bookings_only_feed=True)
    )

    assert parsed[0].start.astimezone(timezone.utc).date() != parsed[0].start.date()
    assert parse_names == [built[0].slot_name]


def test_named_reservation_is_unaffected_by_uid_naming() -> None:
    """A named feed's reservation behaves exactly as before."""
    calendar = _parse(
        [_closed_event(date(2026, 10, 23), date(2026, 10, 25), "Smith")], _parse_ctx()
    )

    built = reservations.build_reservations(
        calendar, None, _build_ctx(bookings_only_feed=True)
    )

    assert [res.slot_name for res in built] == ["Smith"]


class _CreationFlow:
    """Stub of the entry-creation handler, which stamps ``created``."""

    def __init__(self) -> None:
        """Initialize the creation-flow stub."""
        self.created = "2026-10-24T00:00:00+00:00"


class _OptionsFlow:
    """Stub of the options handler, which owns a config entry."""

    def __init__(self) -> None:
        """Initialize the options-flow stub."""
        self.config_entry = SimpleNamespace(entry_id="entry-1")


def _save(flow: Any, url: str, bookings_only: bool) -> dict[str, Any]:
    """Return the config entry data a flow would store for a submission."""
    user_input: dict[str, Any] = {
        CONF_URL: url,
        CONF_BOOKINGS_ONLY_FEED: bookings_only,
        CONF_LOCK_ENTRY: "(none)",
    }
    apply_successful_conversions(flow, user_input)
    return user_input


def test_new_booking_com_entry_defaults_to_bookings_only() -> None:
    """A new Booking.com entry is bookings-only without operator action.

    The creation form renders before the URL is known, so an unticked
    box there expresses nothing and must not pin the entry to False.
    """
    stored = _save(_CreationFlow(), BOOKING_URL, False)

    assert CONF_BOOKINGS_ONLY_FEED not in stored
    assert feed_is_bookings_only(MappingProxyType(stored)) is True


def test_new_non_booking_com_entry_stays_off() -> None:
    """A new entry on any other host keeps its stored False."""
    stored = _save(_CreationFlow(), AIRBNB_URL, False)

    assert stored[CONF_BOOKINGS_ONLY_FEED] is False
    assert feed_is_bookings_only(MappingProxyType(stored)) is False


def test_options_flow_preserves_an_explicit_booking_com_false() -> None:
    """An operator exporting closed dates can turn the option off.

    The options form renders a URL-aware default, so a False saved
    there is a real choice and the creation-time rule must not reach it.
    """
    stored = _save(_OptionsFlow(), BOOKING_URL, False)

    assert stored[CONF_BOOKINGS_ONLY_FEED] is False
    assert feed_is_bookings_only(MappingProxyType(stored)) is False


def test_opting_in_on_another_host_is_recorded() -> None:
    """Ticking the box on a non-Booking.com host stores a real True."""
    stored = _save(_CreationFlow(), AIRBNB_URL, True)

    assert stored[CONF_BOOKINGS_ONLY_FEED] is True
    assert feed_is_bookings_only(MappingProxyType(stored)) is True
