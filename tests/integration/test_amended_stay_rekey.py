# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""End-to-end coverage for amended stays on adopted slots (issue #867).

An adopted mapping is written from the lock itself, so it holds the
lock's programmed window and no calendar UID. When the booking's dates
change upstream before the lock is reprogrammed, the lock still shows
the old window. These tests drive the coordinator through calendar
parsing, reservation building, the physical remap and ghost
construction, then plan the cycle, and check that the amended stay
keeps its slot, has its dates reprogrammed, and leaves no ghost behind
that could evict another guest.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta
from typing import TYPE_CHECKING
from typing import Any

from homeassistant.util import dt as dt_util
from icalendar import Calendar
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rental_control.coordinator import RentalControlCoordinator
from custom_components.rental_control.reconciliation import ActionKind
from custom_components.rental_control.reconciliation import compute_desired_plan

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOCK = "test_lock"
_OLD_START = datetime(2026, 8, 10, 16, tzinfo=dt_util.UTC)
_OLD_END = datetime(2026, 8, 14, 11, tzinfo=dt_util.UTC)
_NEW_START = _OLD_START - timedelta(days=1)
_NEW_END = _OLD_END
_PEER_START = datetime(2026, 8, 20, 16, tzinfo=dt_util.UTC)
_PEER_END = datetime(2026, 8, 24, 11, tzinfo=dt_util.UTC)
_PARSE_FROM = datetime(2026, 8, 1, tzinfo=dt_util.UTC)
_PARSE_TO = datetime(2026, 12, 31, tzinfo=dt_util.UTC)

_AIRBNB_URL = "https://example.com/calendar.ics"
_BOOKING_URL = "https://ical.booking.com/v1/export?t=abc123"

_NAMED = {
    "url": _AIRBNB_URL,
    "summary": "Reserved: Smith",
    "uid": "smith-stay@pms.example",
    "peer_summary": "Reserved: Jones",
    "peer_uid": "jones-stay@pms.example",
}
_ANONYMOUS = {
    "url": _BOOKING_URL,
    "summary": "CLOSED - Not available",
    "uid": "7654321@booking.com",
    "peer_summary": "CLOSED - Not available",
    "peer_uid": "8765432@booking.com",
}


def _entry(url: str) -> MockConfigEntry:
    """Return a lock-backed entry managing slots 6 and 7."""
    return MockConfigEntry(
        domain="rental_control",
        title="Amended Rental",
        version=10,
        unique_id="amended-rental",
        data={
            "name": "Amended Rental",
            "url": url,
            "timezone": "UTC",
            "checkin": "16:00",
            "checkout": "11:00",
            "start_slot": 6,
            "max_events": 2,
            "days": 365,
            "verify_ssl": True,
            "ignore_non_reserved": False,
            "honor_event_times": True,
            "keymaster_entry_id": _LOCK,
            "code_buffer_before": 0,
            "code_buffer_after": 0,
        },
        entry_id="amended_entry",
    )


def _ics(feed: dict[str, str], start: datetime, end: datetime) -> Calendar:
    """Return the feed with the target stay at *start*..*end* and a peer."""
    events = (
        (feed["summary"], feed["uid"], start, end),
        (feed["peer_summary"], feed["peer_uid"], _PEER_START, _PEER_END),
    )
    body = "".join(
        "BEGIN:VEVENT\r\n"
        f"DTSTART:{s.strftime('%Y%m%dT%H%M%SZ')}\r\n"
        f"DTEND:{e.strftime('%Y%m%dT%H%M%SZ')}\r\n"
        f"UID:{uid}\r\n"
        f"SUMMARY:{summary}\r\n"
        "END:VEVENT\r\n"
        for summary, uid, s, e in events
    )
    return Calendar.from_ical(
        f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//EN\r\n{body}"
        "END:VCALENDAR\r\n"
    )


def _program_slot(
    hass: HomeAssistant,
    slot: int,
    name: str,
    pin: str,
    start: datetime,
    end: datetime,
) -> None:
    """Set the Keymaster entity states for one programmed slot."""
    base = f"{_LOCK}_code_slot_{slot}"
    hass.states.async_set(f"text.{base}_name", name)
    hass.states.async_set(f"text.{base}_pin", pin)
    hass.states.async_set(f"switch.{base}_enabled", "on")
    hass.states.async_set(f"switch.{base}_use_date_range_limits", "on")
    hass.states.async_set(f"datetime.{base}_date_range_start", start.isoformat())
    hass.states.async_set(f"datetime.{base}_date_range_end", end.isoformat())


async def _prepare(
    hass: HomeAssistant, feed: dict[str, str]
) -> tuple[RentalControlCoordinator, list[Any], list[Any], str, str]:
    """Program the lock from the original feed, then amend the target stay.

    Returns the coordinator, the prepared reservations, the observed slots
    and the target and peer reservations' display names.
    """
    entry = _entry(feed["url"])
    entry.add_to_hass(hass)
    coordinator = RentalControlCoordinator(hass, entry)

    original = await coordinator._ical_parser(
        _ics(feed, _OLD_START, _OLD_END), _PARSE_FROM, _PARSE_TO
    )
    by_uid = {event.uid: event for event in original}
    target_name = coordinator._build_reservations([by_uid[feed["uid"]]])[
        0
    ].display_slot_name
    peer_name = coordinator._build_reservations([by_uid[feed["peer_uid"]]])[
        0
    ].display_slot_name
    _program_slot(hass, 6, target_name, "1357", _OLD_START, _OLD_END)
    _program_slot(hass, 7, peer_name, "2468", _PEER_START, _PEER_END)

    amended = await coordinator._ical_parser(
        _ics(feed, _NEW_START, _NEW_END), _PARSE_FROM, _PARSE_TO
    )
    observed = coordinator._observe_managed_slots()
    reservations = coordinator._prepare_reservations_for_adoption(amended, observed)
    return coordinator, reservations, observed, target_name, peer_name


@pytest.mark.parametrize("feed", [_NAMED, _ANONYMOUS], ids=["named", "anonymous"])
async def test_amended_adopted_stay_is_rekeyed_without_a_ghost(
    hass: HomeAssistant, feed: dict[str, str]
) -> None:
    """The adopted mapping follows the amended stay instead of ghosting.

    Before the fix the remap demanded that the lock's window already equal
    the amended window, so the adopted mapping survived under its old key
    and became a ghost holding slot 6 alongside the live amended stay.
    """
    coordinator, reservations, observed, target_name, _peer = await _prepare(hass, feed)

    ghosts = [res for res in reservations if res.missing_count > 0]
    assert ghosts == []
    target = next(res for res in reservations if res.display_slot_name == target_name)
    assert (target.start, target.end) == (_NEW_START, _NEW_END)
    mappings = coordinator._slot_mappings["mappings"]
    assert mappings[target.identity_key]["slot"] == 6
    assert not any(key.startswith("adopted.") for key in mappings)

    plan = compute_desired_plan(
        reservations=reservations,
        managed_slots=observed,
        max_events=2,
        plan_id="amended-867",
        generated_at=_PARSE_FROM,
        entry_id="amended_entry",
        lockname=_LOCK,
        start_slot=6,
    )

    assert plan.selected[target.identity_key] == 6
    assert plan.overflow == {}
    assert not [action for action in plan.actions if action.kind is ActionKind.CLEAR]
    reprogram = [action for action in plan.actions if action.slot == 6]
    assert [action.kind for action in reprogram] in (
        [ActionKind.UPDATE_TIMES],
        [ActionKind.SET],
    )
    assert reprogram[0].identity_key == target.identity_key
    assert [action for action in plan.actions if action.slot == 7] == []
