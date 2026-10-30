# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Pin that an event stays published for its whole checkout day.

Lockless consumers (issue #872) delete door codes from automations that
fire hours after a stay's end, so the event must still exist then.  That
holds only because the calendar fetch window opens at local midnight
rather than at ``now()``; these tests fail if that window start moves.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock
from unittest.mock import patch

from aioresponses import aioresponses
from freezegun import freeze_time
from homeassistant.util import dt as dt_util
import pytest

from custom_components.rental_control.coordinator import RentalControlCoordinator

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

_URL = "https://example.com/calendar.ics"
_TZ = "America/New_York"
_SUMMARY = "Reserved - Alice"

# All-day stay; the entry's 12:00 checkout puts its end at noon on
# 2026-06-15 local time.
_ICS = (
    "BEGIN:VCALENDAR\r\n"
    "VERSION:2.0\r\n"
    "PRODID:-//Test//EN\r\n"
    "BEGIN:VEVENT\r\n"
    "DTSTART;VALUE=DATE:20260612\r\n"
    "DTEND;VALUE=DATE:20260615\r\n"
    "UID:checkout-day@example.com\r\n"
    f"SUMMARY:{_SUMMARY}\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)


async def _fetch_at(
    hass: HomeAssistant, entry: MockConfigEntry, local_now: str
) -> list[str]:
    """Run the real calendar fetch at *local_now* and return summaries."""
    await hass.config.async_set_time_zone(_TZ)
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, "timezone": _TZ, "checkout": "12:00"}
    )
    with freeze_time(local_now), aioresponses() as mock_session:
        mock_session.get(_URL, status=200, body=_ICS)
        coordinator = RentalControlCoordinator(hass, entry)
        events = await coordinator._async_fetch_calendar()
    return [event.summary for event in events]


@pytest.mark.parametrize(
    "local_now",
    [
        "2026-06-15T08:00:00-04:00",
        "2026-06-15T15:00:00-04:00",
        "2026-06-15T23:59:00-04:00",
    ],
    ids=["before-checkout", "after-checkout", "last-minute"],
)
async def test_event_retained_through_checkout_day(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, local_now: str
) -> None:
    """A stay ending at 12:00 is still published until local midnight."""
    assert await _fetch_at(hass, mock_config_entry, local_now) == [_SUMMARY]


async def test_event_dropped_after_local_midnight(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """The stay is gone once the day after checkout begins."""
    summaries = await _fetch_at(hass, mock_config_entry, "2026-06-16T00:30:00-04:00")
    assert summaries == []


async def test_fetch_window_opens_at_start_of_local_day(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """The parser receives local midnight, not ``now()``, as ``from_date``."""
    await hass.config.async_set_time_zone(_TZ)
    mock_config_entry.add_to_hass(hass)
    with freeze_time("2026-06-15T15:00:00-04:00"), aioresponses() as mock_session:
        mock_session.get(_URL, status=200, body=_ICS)
        coordinator = RentalControlCoordinator(hass, mock_config_entry)
        with patch.object(
            coordinator, "_ical_parser", AsyncMock(return_value=[])
        ) as parser:
            await coordinator._async_fetch_calendar()

    expected = datetime(2026, 6, 15, tzinfo=dt_util.get_default_time_zone())
    assert parser.await_args is not None
    assert parser.await_args.args[1] == expected
