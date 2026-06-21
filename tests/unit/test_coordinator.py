# SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for RentalControlCoordinator."""

from __future__ import annotations

import asyncio
from datetime import datetime
from datetime import timedelta
from typing import TYPE_CHECKING
from typing import Any
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import aiohttp
from aioresponses import aioresponses
from homeassistant.components.calendar import CalendarEvent
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt
import homeassistant.util.dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rental_control.const import CONF_LOCK_ENTRY
from custom_components.rental_control.const import CONF_MAX_EVENTS
from custom_components.rental_control.const import CONF_REFRESH_FREQUENCY
from custom_components.rental_control.const import DEFAULT_REFRESH_FREQUENCY
from custom_components.rental_control.coordinator import RentalControlCoordinator

from tests.fixtures import calendar_data

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


async def test_coordinator_initialization(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test that coordinator initializes with correct configuration.

    Verifies that RentalControlCoordinator properly initializes with
    configuration from a config entry.
    """
    mock_config_entry.add_to_hass(hass)
    coordinator = RentalControlCoordinator(hass, mock_config_entry)

    assert coordinator.hass == hass
    assert coordinator.config_entry == mock_config_entry
    assert coordinator._name == "Test Rental"
    assert coordinator.url == "https://example.com/calendar.ics"


async def test_coordinator_first_refresh(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test coordinator first refresh fetches calendar data.

    Verifies that _async_update_data properly fetches and processes
    calendar data on initial load.
    """
    mock_config_entry.add_to_hass(hass)

    frozen_time = datetime(2024, 12, 20, 12, 0, 0, tzinfo=dt_util.UTC)

    with (
        aioresponses() as mock_session,
        patch.object(dt_util, "now", return_value=frozen_time),
        patch.object(dt_util, "start_of_local_day", return_value=frozen_time),
    ):
        mock_session.get(
            "https://example.com/calendar.ics",
            status=200,
            body=calendar_data.AIRBNB_ICS_CALENDAR,
        )

        coordinator = RentalControlCoordinator(hass, mock_config_entry)

        assert coordinator.data is None

        result = await coordinator._async_update_data()

        assert len(result) > 0
        assert result[0].summary is not None


async def test_coordinator_refresh_success(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test successful calendar fetch and event parsing.

    Verifies that coordinator successfully fetches ICS data from URL,
    parses events, and returns parsed calendar data.
    """
    mock_config_entry.add_to_hass(hass)

    frozen_time = datetime(2024, 12, 20, 12, 0, 0, tzinfo=dt_util.UTC)

    future_start = (frozen_time + timedelta(days=5)).strftime("%Y%m%d")
    future_end = (frozen_time + timedelta(days=10)).strftime("%Y%m%d")

    future_ics = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test Calendar//EN
BEGIN:VEVENT
DTSTART:{future_start}T140000Z
DTEND:{future_end}T110000Z
UID:test-event@example.com
SUMMARY:Reserved: Test Guest
DESCRIPTION:Email: test@example.com
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR
"""

    with (
        aioresponses() as mock_session,
        patch.object(dt_util, "now", return_value=frozen_time),
        patch.object(dt_util, "start_of_local_day", return_value=frozen_time),
    ):
        mock_session.get(
            "https://example.com/calendar.ics",
            status=200,
            body=future_ics,
        )

        coordinator = RentalControlCoordinator(hass, mock_config_entry)

        result = await coordinator._async_update_data()

        assert len(result) > 0
        assert result[0].summary == "Reserved: Test Guest"
        assert result[0].uid == "test-event@example.com"


async def test_coordinator_populates_uid_from_ical(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test that CalendarEvent.uid is populated from the iCal UID.

    Verifies that events with a UID property have it passed through
    to the CalendarEvent, and events missing a UID default to None.
    """
    mock_config_entry.add_to_hass(hass)

    frozen_time = datetime(2024, 12, 20, 12, 0, 0, tzinfo=dt_util.UTC)

    future_start = (frozen_time + timedelta(days=5)).strftime("%Y%m%d")
    future_end = (frozen_time + timedelta(days=10)).strftime("%Y%m%d")
    future_start2 = (frozen_time + timedelta(days=15)).strftime("%Y%m%d")
    future_end2 = (frozen_time + timedelta(days=20)).strftime("%Y%m%d")

    ics_data = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test Calendar//EN
BEGIN:VEVENT
DTSTART:{future_start}T140000Z
DTEND:{future_end}T110000Z
UID:stable-id-123@booking.example
SUMMARY:Reserved: Guest With UID
STATUS:CONFIRMED
END:VEVENT
BEGIN:VEVENT
DTSTART:{future_start2}T140000Z
DTEND:{future_end2}T110000Z
SUMMARY:Reserved: Guest No UID
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR
"""

    with (
        aioresponses() as mock_session,
        patch.object(dt_util, "now", return_value=frozen_time),
        patch.object(dt_util, "start_of_local_day", return_value=frozen_time),
    ):
        mock_session.get(
            "https://example.com/calendar.ics",
            status=200,
            body=ics_data,
        )

        coordinator = RentalControlCoordinator(hass, mock_config_entry)

        result = await coordinator._async_update_data()

        assert len(result) == 2
        assert result[0].uid == "stable-id-123@booking.example"
        assert result[1].uid is None


async def test_coordinator_refresh_network_error(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test error handling for HTTP failures.

    Verifies that _async_update_data raises UpdateFailed when
    calendar URL returns HTTP errors.
    """
    mock_config_entry.add_to_hass(hass)

    with aioresponses() as mock_session:
        mock_session.get(
            "https://example.com/calendar.ics",
            status=404,
        )

        coordinator = RentalControlCoordinator(hass, mock_config_entry)

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    with aioresponses() as mock_session:
        mock_session.get(
            "https://example.com/calendar.ics",
            status=500,
        )

        coordinator = RentalControlCoordinator(hass, mock_config_entry)

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()


async def test_coordinator_refresh_invalid_ics(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test coordinator handles malformed ICS content by raising UpdateFailed.

    The _async_update_data method raises UpdateFailed when ICS data
    cannot be parsed, allowing the DUC to handle error state.
    """
    mock_config_entry.add_to_hass(hass)

    with aioresponses() as mock_session:
        mock_session.get(
            "https://example.com/calendar.ics",
            status=200,
            body=calendar_data.MALFORMED_ICS_CALENDAR,
        )

        coordinator = RentalControlCoordinator(hass, mock_config_entry)

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()


async def test_coordinator_state_management(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test coordinator data property maintains event state.

    Verifies that _async_update_data properly parses events,
    maintains sort order, and updates next event reference.
    """
    mock_config_entry.add_to_hass(hass)

    frozen_time = datetime(2024, 12, 20, 12, 0, 0, tzinfo=dt_util.UTC)

    event1_start = (frozen_time + timedelta(days=2)).strftime("%Y%m%d")
    event1_end = (frozen_time + timedelta(days=5)).strftime("%Y%m%d")
    event2_start = (frozen_time + timedelta(days=7)).strftime("%Y%m%d")
    event2_end = (frozen_time + timedelta(days=10)).strftime("%Y%m%d")

    multi_event_ics = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
DTSTART:{event1_start}T140000Z
DTEND:{event1_end}T110000Z
UID:event1@example.com
SUMMARY:Reserved: First Guest
STATUS:CONFIRMED
END:VEVENT
BEGIN:VEVENT
DTSTART:{event2_start}T140000Z
DTEND:{event2_end}T110000Z
UID:event2@example.com
SUMMARY:Reserved: Second Guest
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR
"""

    with (
        aioresponses() as mock_session,
        patch.object(dt_util, "now", return_value=frozen_time),
        patch.object(dt_util, "start_of_local_day", return_value=frozen_time),
    ):
        mock_session.get(
            "https://example.com/calendar.ics",
            status=200,
            body=multi_event_ics,
        )

        coordinator = RentalControlCoordinator(hass, mock_config_entry)

        result = await coordinator._async_update_data()

        assert len(result) == 2

        assert result[0].start < result[1].start

        assert coordinator.event is not None
        assert coordinator.event.summary == "Reserved: First Guest"


async def test_coordinator_update_interval_change(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test coordinator respects interval changes.

    Verifies that coordinator updates refresh interval when
    configuration is changed via update_config.
    """
    mock_config_entry.add_to_hass(hass)

    coordinator = RentalControlCoordinator(hass, mock_config_entry)
    coordinator.async_request_refresh = AsyncMock()

    initial_frequency = coordinator.refresh_frequency
    assert initial_frequency == DEFAULT_REFRESH_FREQUENCY

    new_config = dict(mock_config_entry.data)
    new_config[CONF_REFRESH_FREQUENCY] = 30

    await coordinator.update_config(new_config)

    assert coordinator.refresh_frequency == 30
    assert coordinator.refresh_frequency != initial_frequency
    assert coordinator.update_interval == timedelta(minutes=30)
    assert coordinator.async_request_refresh.called


# ---------------------------------------------------------------------------
# Phase 7 – targeted coverage tests for coordinator.py
# ---------------------------------------------------------------------------


async def test_coordinator_async_get_events_empty_calendar(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test async_get_events returns empty list with no calendar data.

    Covers coordinator.py async_get_events empty calendar branch.
    """
    mock_config_entry.add_to_hass(hass)
    coordinator = RentalControlCoordinator(hass, mock_config_entry)

    start = dt.now()
    end = start + timedelta(days=30)
    events = await coordinator.async_get_events(hass, start, end)

    assert events == []


async def test_coordinator_async_get_events_filters_by_date(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test async_get_events filters events by date range.

    Covers coordinator.py async_get_events date comparison logic.
    """
    mock_config_entry.add_to_hass(hass)
    coordinator = RentalControlCoordinator(hass, mock_config_entry)

    now = dt.now()
    in_range_event = CalendarEvent(
        start=now,
        end=now + timedelta(days=2),
        summary="In Range",
    )
    out_of_range_event = CalendarEvent(
        start=now + timedelta(days=60),
        end=now + timedelta(days=62),
        summary="Out of Range",
    )
    coordinator.data = [in_range_event, out_of_range_event]

    start = now - timedelta(days=1)
    end = now + timedelta(days=30)
    events = await coordinator.async_get_events(hass, start, end)

    assert len(events) == 1
    assert events[0].summary == "In Range"


async def test_coordinator_update_event_overrides_with_overrides(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test update_event_overrides delegates to EventOverrides.

    Covers coordinator.py update_event_overrides with event_overrides set.
    """
    mock_config_entry.add_to_hass(hass)

    # Create coordinator and directly inject a mock event_overrides
    coordinator = RentalControlCoordinator(hass, mock_config_entry)

    mock_overrides = MagicMock()
    mock_overrides.ready = True
    mock_overrides.async_update = AsyncMock()
    coordinator.event_overrides = mock_overrides
    coordinator.async_request_refresh = AsyncMock()

    now = dt.now()
    await coordinator.update_event_overrides(
        slot=1,
        slot_code="1234",
        slot_name="Test Guest",
        start_time=now,
        end_time=now + timedelta(days=2),
    )

    mock_overrides.async_update.assert_awaited_once()
    assert coordinator.async_request_refresh.called


async def test_coordinator_update_event_overrides_without_overrides(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test update_event_overrides without event_overrides.

    Covers coordinator.py update_event_overrides else branch when
    event_overrides is None.
    """
    mock_config_entry.add_to_hass(hass)
    coordinator = RentalControlCoordinator(hass, mock_config_entry)

    assert coordinator.event_overrides is None
    coordinator.async_request_refresh = AsyncMock()

    now = dt.now()
    await coordinator.update_event_overrides(
        slot=1,
        slot_code="1234",
        slot_name="Test Guest",
        start_time=now,
        end_time=now + timedelta(days=2),
    )

    assert coordinator.async_request_refresh.called


# ---------------------------------------------------------------------------
# _ical_parser exception handling tests
# ---------------------------------------------------------------------------


class TestIcalParserExceptionHandling:
    """Tests for narrowed exception handling in _ical_parser.

    The _ical_parser method catches (AttributeError, TypeError) when
    comparing event DTSTART/DTEND dates to the filtering range. This
    ensures events with problematic date types are still processed
    rather than crashing the parser.
    """

    async def test_tz_aware_datetime_events_parsed(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify events with tz-aware datetime dates are handled.

        Comparing a tz-aware datetime to a plain date via operators
        raises TypeError in Python 3. The exception handler should
        catch this and still include the event.
        """
        mock_config_entry.add_to_hass(hass)

        frozen_time = datetime(2024, 12, 20, 12, 0, 0, tzinfo=dt_util.UTC)
        future_start = (frozen_time + timedelta(days=5)).strftime("%Y%m%d")
        future_end = (frozen_time + timedelta(days=10)).strftime("%Y%m%d")

        tz_aware_ics = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
DTSTART:{future_start}T140000Z
DTEND:{future_end}T110000Z
UID:tz-test@example.com
SUMMARY:Reserved: TZ Guest
DESCRIPTION:Email: tz@example.com
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR
"""

        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=frozen_time),
            patch.object(dt_util, "start_of_local_day", return_value=frozen_time),
        ):
            mock_session.get(
                mock_config_entry.data["url"],
                body=tz_aware_ics,
            )
            coordinator = RentalControlCoordinator(hass, mock_config_entry)
            result = await coordinator._async_update_data()

        assert result is not None
        assert len(result) > 0
        assert result[0].summary == "Reserved: TZ Guest"

    async def test_date_only_events_parsed(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify events with plain date DTSTART/DTEND are handled.

        Plain date comparisons should work without triggering the
        exception handler at all.
        """
        mock_config_entry.add_to_hass(hass)

        frozen_time = datetime(2024, 12, 20, 12, 0, 0, tzinfo=dt_util.UTC)
        future_start = (frozen_time + timedelta(days=5)).strftime("%Y%m%d")
        future_end = (frozen_time + timedelta(days=10)).strftime("%Y%m%d")

        date_only_ics = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
DTSTART;VALUE=DATE:{future_start}
DTEND;VALUE=DATE:{future_end}
UID:date-test@example.com
SUMMARY:Reserved: Date Guest
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR
"""

        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=frozen_time),
            patch.object(dt_util, "start_of_local_day", return_value=frozen_time),
        ):
            mock_session.get(
                mock_config_entry.data["url"],
                body=date_only_ics,
            )
            coordinator = RentalControlCoordinator(hass, mock_config_entry)
            result = await coordinator._async_update_data()

        assert result is not None
        assert len(result) > 0
        assert result[0].summary == "Reserved: Date Guest"


# ---------------------------------------------------------------------------
# Calendar error scenario tests (T022)
# ---------------------------------------------------------------------------

FROZEN_TIME = datetime(2024, 12, 20, 12, 0, 0, tzinfo=dt_util.UTC)
FROZEN_START_OF_DAY = datetime(2024, 12, 20, 0, 0, 0, tzinfo=dt_util.UTC)


def _future_ics(
    summary: str = "Reserved: Test Guest",
    days_ahead: int = 5,
    duration: int = 5,
    *,
    base_time: datetime = FROZEN_TIME,
) -> str:
    """Build a single-event ICS with dates relative to base_time."""
    start = (base_time + timedelta(days=days_ahead)).strftime("%Y%m%d")
    end = (base_time + timedelta(days=days_ahead + duration)).strftime("%Y%m%d")
    return (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//Test//EN\r\n"
        "BEGIN:VEVENT\r\n"
        f"DTSTART:{start}T160000Z\r\n"
        f"DTEND:{end}T110000Z\r\n"
        "UID:future-test@example.com\r\n"
        f"SUMMARY:{summary}\r\n"
        "DESCRIPTION:Email: test@example.com\r\n"
        "STATUS:CONFIRMED\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )


class TestRefreshCalendarMissCounter:
    """Tests for the calendar miss counter logic in _async_update_data."""

    async def test_miss_counter_preserves_previous_calendar(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify empty calendar increments miss counter and keeps old data.

        When _async_update_data returns zero events but the previous
        data had events and misses are below the threshold, it should
        increment num_misses and return the existing data.
        """
        mock_config_entry.add_to_hass(hass)

        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=FROZEN_TIME),
            patch.object(
                dt_util, "start_of_local_day", return_value=FROZEN_START_OF_DAY
            ),
        ):
            mock_session.get(
                mock_config_entry.data["url"],
                body=_future_ics(),
            )
            coordinator = RentalControlCoordinator(hass, mock_config_entry)
            result = await coordinator._async_update_data()
            coordinator.data = result

        assert len(coordinator.data) > 0
        assert coordinator.num_misses == 0
        previous_data = list(coordinator.data)

        future = FROZEN_TIME + timedelta(minutes=coordinator.refresh_frequency + 1)
        empty_ics = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//EN\r\nEND:VCALENDAR\r\n"
        )

        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=future),
            patch.object(
                dt_util,
                "start_of_local_day",
                return_value=future.replace(hour=0, minute=0, second=0, microsecond=0),
            ),
        ):
            mock_session.get(
                mock_config_entry.data["url"],
                body=empty_ics,
            )
            result = await coordinator._async_update_data()

        assert coordinator.num_misses == 1
        assert len(result) == len(previous_data)

    async def test_miss_counter_resets_on_successful_refresh(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify miss counter resets to zero after a successful refresh."""
        mock_config_entry.add_to_hass(hass)

        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=FROZEN_TIME),
            patch.object(
                dt_util, "start_of_local_day", return_value=FROZEN_START_OF_DAY
            ),
        ):
            mock_session.get(
                mock_config_entry.data["url"],
                body=_future_ics(),
            )
            coordinator = RentalControlCoordinator(hass, mock_config_entry)
            coordinator.num_misses = 1
            result = await coordinator._async_update_data()

        assert coordinator.num_misses == 0
        assert len(result) > 0


class TestRefreshCalendarClientError:
    """Tests for aiohttp.ClientError handling in _async_update_data."""

    async def test_client_error_raises_update_failed(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify aiohttp.ClientError raises UpdateFailed."""
        mock_config_entry.add_to_hass(hass)

        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=FROZEN_TIME),
            patch.object(
                dt_util, "start_of_local_day", return_value=FROZEN_START_OF_DAY
            ),
        ):
            mock_session.get(
                mock_config_entry.data["url"],
                exception=aiohttp.ClientError("Connection refused"),
            )
            coordinator = RentalControlCoordinator(hass, mock_config_entry)
            with pytest.raises(UpdateFailed):
                await coordinator._async_update_data()


class TestRefreshCalendarGenericException:
    """Tests for generic exception handling in _async_update_data."""

    async def test_unexpected_error_raises_update_failed(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify unexpected exceptions raise UpdateFailed."""
        mock_config_entry.add_to_hass(hass)

        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=FROZEN_TIME),
            patch.object(
                dt_util, "start_of_local_day", return_value=FROZEN_START_OF_DAY
            ),
        ):
            mock_session.get(
                mock_config_entry.data["url"],
                body="THIS IS NOT VALID ICS DATA",
            )
            coordinator = RentalControlCoordinator(hass, mock_config_entry)
            with pytest.raises(UpdateFailed):
                await coordinator._async_update_data()


class TestFetchFailureCachedDataFallback:
    """Tests that fetch failures fall back to cached data when available."""

    async def _setup_coordinator_with_cache(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> RentalControlCoordinator:
        """Create a coordinator and populate it with cached data."""
        mock_config_entry.add_to_hass(hass)

        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=FROZEN_TIME),
            patch.object(
                dt_util, "start_of_local_day", return_value=FROZEN_START_OF_DAY
            ),
        ):
            mock_session.get(
                mock_config_entry.data["url"],
                body=_future_ics(),
            )
            coordinator = RentalControlCoordinator(hass, mock_config_entry)
            result = await coordinator._async_update_data()
            coordinator.data = result

        assert len(coordinator.data) > 0
        return coordinator

    async def test_client_error_returns_cached_data(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify aiohttp.ClientError returns cached data."""
        coordinator = await self._setup_coordinator_with_cache(hass, mock_config_entry)
        cached_count = len(coordinator.data)

        with aioresponses() as mock_session:
            mock_session.get(
                mock_config_entry.data["url"],
                exception=aiohttp.ClientError("Connection refused"),
            )
            result = await coordinator._async_update_data()

        assert len(result) == cached_count

    async def test_http_error_returns_cached_data(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify HTTP 500 returns cached data."""
        coordinator = await self._setup_coordinator_with_cache(hass, mock_config_entry)
        cached_count = len(coordinator.data)

        with aioresponses() as mock_session:
            mock_session.get(
                mock_config_entry.data["url"],
                status=500,
            )
            result = await coordinator._async_update_data()

        assert len(result) == cached_count

    async def test_parse_error_returns_cached_data(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify malformed ICS returns cached data."""
        coordinator = await self._setup_coordinator_with_cache(hass, mock_config_entry)
        cached_count = len(coordinator.data)

        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=FROZEN_TIME),
            patch.object(
                dt_util, "start_of_local_day", return_value=FROZEN_START_OF_DAY
            ),
        ):
            mock_session.get(
                mock_config_entry.data["url"],
                body="THIS IS NOT VALID ICS DATA",
            )
            result = await coordinator._async_update_data()

        assert len(result) == cached_count

    async def test_timeout_returns_cached_data(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify timeout returns cached data."""
        coordinator = await self._setup_coordinator_with_cache(hass, mock_config_entry)
        cached_count = len(coordinator.data)

        with aioresponses() as mock_session:
            mock_session.get(
                mock_config_entry.data["url"],
                exception=asyncio.TimeoutError(),
            )
            result = await coordinator._async_update_data()

        assert len(result) == cached_count

    async def test_no_cache_still_raises_update_failed(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify UpdateFailed raised when no cached data exists."""
        mock_config_entry.add_to_hass(hass)

        with aioresponses() as mock_session:
            mock_session.get(
                mock_config_entry.data["url"],
                exception=aiohttp.ClientError("Connection refused"),
            )
            coordinator = RentalControlCoordinator(hass, mock_config_entry)
            with pytest.raises(UpdateFailed):
                await coordinator._async_update_data()

    async def test_empty_cache_returns_empty_list(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify empty cached data is returned on fetch failure.

        When the previous fetch returned zero events (valid "no
        reservations" state), a subsequent fetch failure should return
        the empty list rather than raising UpdateFailed.
        """
        mock_config_entry.add_to_hass(hass)

        coordinator = RentalControlCoordinator(hass, mock_config_entry)
        coordinator.data = []

        with aioresponses() as mock_session:
            mock_session.get(
                mock_config_entry.data["url"],
                exception=aiohttp.ClientError("Connection refused"),
            )
            result = await coordinator._async_update_data()

        assert result == []


# ---------------------------------------------------------------------------
# Slot bootstrapping tests (T023)
# ---------------------------------------------------------------------------


class TestSlotBootstrapping:
    """Tests for Keymaster entity discovery and slot initialization."""

    async def test_bootstrap_skips_missing_pin_entity(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify slots with no PIN entity are skipped during bootstrap."""
        mock_config_entry.add_to_hass(hass)

        coordinator = RentalControlCoordinator(hass, mock_config_entry)
        coordinator.lockname = "front_door"
        mock_overrides = MagicMock()
        mock_overrides.ready = False
        mock_overrides.async_check_overrides = AsyncMock()
        coordinator.event_overrides = mock_overrides
        mock_update = AsyncMock()
        object.__setattr__(coordinator, "update_event_overrides", mock_update)

        hass.states.async_set("dummy.entity", "on")

        await coordinator.async_setup_keymaster_overrides()

        mock_update.assert_not_awaited()

    async def test_bootstrap_skips_missing_name_entity(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify slots with PIN but no NAME entity are skipped."""
        mock_config_entry.add_to_hass(hass)

        coordinator = RentalControlCoordinator(hass, mock_config_entry)
        coordinator.lockname = "front_door"
        mock_overrides = MagicMock()
        mock_overrides.ready = False
        mock_overrides.async_check_overrides = AsyncMock()
        coordinator.event_overrides = mock_overrides
        mock_update = AsyncMock()
        object.__setattr__(coordinator, "update_event_overrides", mock_update)

        hass.states.async_set("text.front_door_code_slot_10_pin", "1234")

        await coordinator.async_setup_keymaster_overrides()

        mock_update.assert_not_awaited()

    async def test_bootstrap_loads_slot_without_date_range(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify slot is loaded with default times when date range is off."""
        mock_config_entry.add_to_hass(hass)

        with patch.object(
            dt_util, "start_of_local_day", return_value=FROZEN_START_OF_DAY
        ):
            coordinator = RentalControlCoordinator(hass, mock_config_entry)
            coordinator.lockname = "front_door"
            mock_overrides = MagicMock()
            mock_overrides.ready = False
            mock_overrides.async_check_overrides = AsyncMock()
            coordinator.event_overrides = mock_overrides
            mock_update = AsyncMock()
            object.__setattr__(coordinator, "update_event_overrides", mock_update)

            hass.states.async_set("text.front_door_code_slot_10_pin", "1234")
            hass.states.async_set("text.front_door_code_slot_10_name", "Guest")

            await coordinator.async_setup_keymaster_overrides()

        mock_update.assert_awaited_once()
        call_args = mock_update.call_args[0]
        assert call_args[0] == 10
        assert call_args[1] == "1234"
        assert call_args[2] == "Guest"
        assert call_args[3] == FROZEN_START_OF_DAY
        assert call_args[4] == FROZEN_START_OF_DAY + timedelta(days=1)

    async def test_bootstrap_loads_slot_with_date_range(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify slot is loaded with parsed times when date range is on."""
        mock_config_entry.add_to_hass(hass)

        coordinator = RentalControlCoordinator(hass, mock_config_entry)
        coordinator.lockname = "front_door"
        mock_overrides = MagicMock()
        mock_overrides.ready = False
        mock_overrides.async_check_overrides = AsyncMock()
        coordinator.event_overrides = mock_overrides
        mock_update = AsyncMock()
        object.__setattr__(coordinator, "update_event_overrides", mock_update)

        hass.states.async_set("text.front_door_code_slot_10_pin", "5678")
        hass.states.async_set("text.front_door_code_slot_10_name", "VIP")
        hass.states.async_set(
            "switch.front_door_code_slot_10_use_date_range_limits", "on"
        )
        hass.states.async_set(
            "datetime.front_door_code_slot_10_date_range_start",
            "2024-12-25T16:00:00+00:00",
        )
        hass.states.async_set(
            "datetime.front_door_code_slot_10_date_range_end",
            "2024-12-30T11:00:00+00:00",
        )

        await coordinator.async_setup_keymaster_overrides()

        mock_update.assert_awaited_once()
        call_args = mock_update.call_args[0]
        assert call_args[0] == 10
        assert call_args[1] == "5678"
        assert call_args[2] == "VIP"
        assert call_args[3] is not None
        assert call_args[4] is not None

    async def test_bootstrap_skips_unparseable_start_time(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify slot is skipped when start time cannot be parsed."""
        mock_config_entry.add_to_hass(hass)

        coordinator = RentalControlCoordinator(hass, mock_config_entry)
        coordinator.lockname = "front_door"
        mock_overrides = MagicMock()
        mock_overrides.ready = False
        mock_overrides.async_check_overrides = AsyncMock()
        coordinator.event_overrides = mock_overrides
        mock_update = AsyncMock()
        object.__setattr__(coordinator, "update_event_overrides", mock_update)

        hass.states.async_set("text.front_door_code_slot_10_pin", "1234")
        hass.states.async_set("text.front_door_code_slot_10_name", "Guest")
        hass.states.async_set(
            "switch.front_door_code_slot_10_use_date_range_limits", "on"
        )
        hass.states.async_set(
            "datetime.front_door_code_slot_10_date_range_start",
            "not-a-datetime",
        )
        hass.states.async_set(
            "datetime.front_door_code_slot_10_date_range_end",
            "2024-12-30T11:00:00+00:00",
        )

        await coordinator.async_setup_keymaster_overrides()

        mock_update.assert_not_awaited()

    async def test_bootstrap_handles_unknown_slot_states(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify unknown slot states are converted to empty strings."""
        mock_config_entry.add_to_hass(hass)

        with patch.object(
            dt_util, "start_of_local_day", return_value=FROZEN_START_OF_DAY
        ):
            coordinator = RentalControlCoordinator(hass, mock_config_entry)
            coordinator.lockname = "front_door"
            mock_overrides = MagicMock()
            mock_overrides.ready = False
            mock_overrides.async_check_overrides = AsyncMock()
            coordinator.event_overrides = mock_overrides
            mock_update = AsyncMock()
            object.__setattr__(coordinator, "update_event_overrides", mock_update)

            hass.states.async_set("text.front_door_code_slot_10_pin", "unknown")
            hass.states.async_set("text.front_door_code_slot_10_name", "unknown")

            await coordinator.async_setup_keymaster_overrides()

        mock_update.assert_awaited_once()
        call_args = mock_update.call_args[0]
        assert call_args[1] == ""
        assert call_args[2] == ""

    async def test_bootstrap_skips_unavailable_slot_states(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify unavailable slot states are not assumed empty."""
        mock_config_entry.add_to_hass(hass)

        with patch.object(
            dt_util, "start_of_local_day", return_value=FROZEN_START_OF_DAY
        ):
            coordinator = RentalControlCoordinator(hass, mock_config_entry)
            coordinator.lockname = "front_door"
            mock_overrides = MagicMock()
            mock_overrides.ready = False
            mock_overrides.async_check_overrides = AsyncMock()
            coordinator.event_overrides = mock_overrides
            mock_update = AsyncMock()
            object.__setattr__(coordinator, "update_event_overrides", mock_update)

            hass.states.async_set("text.front_door_code_slot_10_pin", "unknown")
            hass.states.async_set("text.front_door_code_slot_10_name", "unavailable")

            await coordinator.async_setup_keymaster_overrides()

        mock_update.assert_not_awaited()

    async def test_bootstrap_marks_unnamed_real_pin_occupied(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify a real PIN with cleared name is not bootstrapped as free."""
        mock_config_entry.add_to_hass(hass)

        with patch.object(
            dt_util, "start_of_local_day", return_value=FROZEN_START_OF_DAY
        ):
            coordinator = RentalControlCoordinator(hass, mock_config_entry)
            coordinator.lockname = "front_door"
            mock_overrides = MagicMock()
            mock_overrides.ready = False
            mock_overrides.async_check_overrides = AsyncMock()
            coordinator.event_overrides = mock_overrides
            mock_update = AsyncMock()
            object.__setattr__(coordinator, "update_event_overrides", mock_update)

            hass.states.async_set("text.front_door_code_slot_10_pin", "9876")
            hass.states.async_set("text.front_door_code_slot_10_name", "unknown")

            await coordinator.async_setup_keymaster_overrides()

        mock_update.assert_awaited_once()
        call_args = mock_update.call_args[0]
        assert call_args[1] == "9876"
        assert call_args[2] == "Adopted Slot 10"


# ---------------------------------------------------------------------------
# Partial slot reset detection tests
# ---------------------------------------------------------------------------


class TestPartialSlotResetDetection:
    """Tests for detecting and resetting partially-cleared slots."""

    async def test_startup_detects_partial_reset_and_forces_clear(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify slot with name but no code triggers force-reset."""
        mock_config_entry.add_to_hass(hass)

        with patch.object(
            dt_util, "start_of_local_day", return_value=FROZEN_START_OF_DAY
        ):
            coordinator = RentalControlCoordinator(hass, mock_config_entry)
            coordinator.lockname = "front_door"
            mock_overrides = MagicMock()
            mock_overrides.ready = False
            mock_overrides.async_check_overrides = AsyncMock()
            coordinator.event_overrides = mock_overrides
            mock_update = AsyncMock()
            object.__setattr__(coordinator, "update_event_overrides", mock_update)

            hass.states.async_set("text.front_door_code_slot_10_pin", "")
            hass.states.async_set("text.front_door_code_slot_10_name", "Ghost")

            with patch(
                "custom_components.rental_control.coordinator.async_fire_clear_code",
                new_callable=AsyncMock,
            ) as mock_clear:
                await coordinator.async_setup_keymaster_overrides()

        mock_clear.assert_awaited_once_with(coordinator, 10)
        # Slot is registered as empty so ready can become True
        mock_update.assert_awaited_once()
        call_args = mock_update.call_args[0]
        assert call_args[1] == ""
        assert call_args[2] == ""

    async def test_startup_skips_fully_empty_slots(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify fully empty slots do not trigger force-reset."""
        mock_config_entry.add_to_hass(hass)

        with patch.object(
            dt_util,
            "start_of_local_day",
            return_value=FROZEN_START_OF_DAY,
        ):
            coordinator = RentalControlCoordinator(hass, mock_config_entry)
            coordinator.lockname = "front_door"
            mock_overrides = MagicMock()
            mock_overrides.ready = False
            mock_overrides.async_check_overrides = AsyncMock()
            coordinator.event_overrides = mock_overrides
            mock_update = AsyncMock()
            object.__setattr__(coordinator, "update_event_overrides", mock_update)

            hass.states.async_set("text.front_door_code_slot_10_pin", "")
            hass.states.async_set("text.front_door_code_slot_10_name", "")

            with patch(
                "custom_components.rental_control.coordinator.async_fire_clear_code",
                new_callable=AsyncMock,
            ) as mock_clear:
                await coordinator.async_setup_keymaster_overrides()

        mock_clear.assert_not_awaited()

    async def test_startup_loads_normal_slots(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify slots with both name and code load normally."""
        mock_config_entry.add_to_hass(hass)

        with patch.object(
            dt_util,
            "start_of_local_day",
            return_value=FROZEN_START_OF_DAY,
        ):
            coordinator = RentalControlCoordinator(hass, mock_config_entry)
            coordinator.lockname = "front_door"
            mock_overrides = MagicMock()
            mock_overrides.ready = False
            mock_overrides.async_check_overrides = AsyncMock()
            coordinator.event_overrides = mock_overrides
            mock_update = AsyncMock()
            object.__setattr__(coordinator, "update_event_overrides", mock_update)

            hass.states.async_set("text.front_door_code_slot_10_pin", "1234")
            hass.states.async_set("text.front_door_code_slot_10_name", "Guest")

            with patch(
                "custom_components.rental_control.coordinator.async_fire_clear_code",
                new_callable=AsyncMock,
            ) as mock_clear:
                await coordinator.async_setup_keymaster_overrides()

        mock_clear.assert_not_awaited()
        mock_update.assert_awaited_once()
        call_args = mock_update.call_args[0]
        assert call_args[1] == "1234"
        assert call_args[2] == "Guest"

    async def test_startup_handles_force_reset_failure(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify force-reset failure is logged but does not crash."""
        mock_config_entry.add_to_hass(hass)

        with patch.object(
            dt_util, "start_of_local_day", return_value=FROZEN_START_OF_DAY
        ):
            coordinator = RentalControlCoordinator(hass, mock_config_entry)
            coordinator.lockname = "front_door"
            mock_overrides = MagicMock()
            mock_overrides.ready = False
            mock_overrides.async_check_overrides = AsyncMock()
            coordinator.event_overrides = mock_overrides
            mock_update = AsyncMock()
            object.__setattr__(coordinator, "update_event_overrides", mock_update)

            hass.states.async_set("text.front_door_code_slot_10_pin", "")
            hass.states.async_set("text.front_door_code_slot_10_name", "Ghost")

            with patch(
                "custom_components.rental_control.coordinator.async_fire_clear_code",
                new_callable=AsyncMock,
                side_effect=RuntimeError("lock offline"),
            ) as mock_clear:
                await coordinator.async_setup_keymaster_overrides()

        mock_clear.assert_awaited_once_with(coordinator, 10)
        # Slot still registered as empty despite reset failure
        mock_update.assert_awaited_once()
        call_args = mock_update.call_args[0]
        assert call_args[1] == ""
        assert call_args[2] == ""

    async def test_startup_resets_when_pin_unknown(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify retained name with unknown PIN triggers force-reset."""
        mock_config_entry.add_to_hass(hass)

        with patch.object(
            dt_util, "start_of_local_day", return_value=FROZEN_START_OF_DAY
        ):
            coordinator = RentalControlCoordinator(hass, mock_config_entry)
            coordinator.lockname = "front_door"
            mock_overrides = MagicMock()
            mock_overrides.ready = False
            mock_overrides.async_check_overrides = AsyncMock()
            coordinator.event_overrides = mock_overrides
            mock_update = AsyncMock()
            object.__setattr__(coordinator, "update_event_overrides", mock_update)

            hass.states.async_set("text.front_door_code_slot_10_pin", "unknown")
            hass.states.async_set("text.front_door_code_slot_10_name", "Guest")

            with patch(
                "custom_components.rental_control.coordinator.async_fire_clear_code",
                new_callable=AsyncMock,
            ) as mock_clear:
                await coordinator.async_setup_keymaster_overrides()

        mock_clear.assert_awaited_once_with(coordinator, 10)
        # Normal load path with normalized empty code after the reset.
        mock_update.assert_awaited_once()


# ---------------------------------------------------------------------------
# Lockname slugification tests
# ---------------------------------------------------------------------------


class TestLocknameSlugification:
    """Verify lockname is slugified when assigned to coordinator."""

    async def test_init_slugifies_lockname_with_spaces(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify lockname with spaces is slugified during init."""
        mock_config_entry.add_to_hass(hass)

        data = dict(mock_config_entry.data)
        data[CONF_LOCK_ENTRY] = "Front Door"
        hass.config_entries.async_update_entry(mock_config_entry, data=data)

        coordinator = RentalControlCoordinator(hass, mock_config_entry)

        assert coordinator.lockname == "front_door"

    async def test_init_slugifies_lockname_with_mixed_case(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify mixed-case lockname is lowered and slugified."""
        mock_config_entry.add_to_hass(hass)

        data = dict(mock_config_entry.data)
        data[CONF_LOCK_ENTRY] = "Dining Room Lock"
        hass.config_entries.async_update_entry(mock_config_entry, data=data)

        coordinator = RentalControlCoordinator(hass, mock_config_entry)

        assert coordinator.lockname == "dining_room_lock"

    async def test_init_preserves_none_lockname(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify None lockname stays None after init."""
        mock_config_entry.add_to_hass(hass)

        coordinator = RentalControlCoordinator(hass, mock_config_entry)

        assert coordinator.lockname is None

    async def test_init_treats_whitespace_lockname_as_none(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify whitespace-only lockname becomes None."""
        data = dict(mock_config_entry.data)
        data[CONF_LOCK_ENTRY] = "   "
        mock_config_entry.add_to_hass(hass)
        hass.config_entries.async_update_entry(mock_config_entry, data=data)

        coordinator = RentalControlCoordinator(hass, mock_config_entry)

        assert coordinator.lockname is None

    async def test_init_preserves_already_slugified_lockname(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify pre-slugified lockname passes through unchanged."""
        mock_config_entry.add_to_hass(hass)

        data = dict(mock_config_entry.data)
        data[CONF_LOCK_ENTRY] = "front_door"
        hass.config_entries.async_update_entry(mock_config_entry, data=data)

        coordinator = RentalControlCoordinator(hass, mock_config_entry)

        assert coordinator.lockname == "front_door"

    async def test_update_config_slugifies_lockname(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify lockname is slugified during options update."""
        mock_config_entry.add_to_hass(hass)

        coordinator = RentalControlCoordinator(hass, mock_config_entry)
        assert coordinator.lockname is None

        config = dict(mock_config_entry.data)
        config.update(mock_config_entry.options)
        config[CONF_LOCK_ENTRY] = "Back Patio Door"

        with patch.object(
            coordinator,
            "async_request_refresh",
            new_callable=AsyncMock,
        ):
            await coordinator.update_config(config)

        assert coordinator.lockname == "back_patio_door"

    async def test_update_config_creates_event_overrides_when_lock_added(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify event_overrides is created when lockname is set."""
        mock_config_entry.add_to_hass(hass)

        coordinator = RentalControlCoordinator(hass, mock_config_entry)
        assert coordinator.lockname is None
        assert coordinator.event_overrides is None

        config = dict(mock_config_entry.data)
        config.update(mock_config_entry.options)
        config[CONF_LOCK_ENTRY] = "Front Door"

        with (
            patch.object(
                coordinator,
                "async_request_refresh",
                new_callable=AsyncMock,
            ),
            patch.object(
                coordinator,
                "async_setup_keymaster_overrides",
                new_callable=AsyncMock,
            ),
        ):
            await coordinator.update_config(config)

        assert coordinator.event_overrides is not None

    async def test_update_config_clears_event_overrides_when_lock_removed(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify event_overrides is cleared when lockname removed."""
        mock_config_entry.add_to_hass(hass)

        data = dict(mock_config_entry.data)
        data[CONF_LOCK_ENTRY] = "Front Door"
        hass.config_entries.async_update_entry(mock_config_entry, data=data)

        coordinator = RentalControlCoordinator(hass, mock_config_entry)
        assert coordinator.lockname == "front_door"
        assert coordinator.event_overrides is not None

        config = dict(mock_config_entry.data)
        config.update(mock_config_entry.options)
        config.pop(CONF_LOCK_ENTRY, None)

        with patch.object(
            coordinator,
            "async_request_refresh",
            new_callable=AsyncMock,
        ):
            await coordinator.update_config(config)

        assert coordinator.lockname is None
        assert coordinator.event_overrides is None

    async def test_update_config_recreates_overrides_on_max_events_change(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify event_overrides is recreated when max_events changes."""
        data = dict(mock_config_entry.data)
        data[CONF_LOCK_ENTRY] = "Front Door"
        mock_config_entry.add_to_hass(hass)
        hass.config_entries.async_update_entry(mock_config_entry, data=data)

        coordinator = RentalControlCoordinator(hass, mock_config_entry)
        assert coordinator.event_overrides is not None
        original_overrides = coordinator.event_overrides

        config = dict(mock_config_entry.data)
        config.update(mock_config_entry.options)
        config[CONF_MAX_EVENTS] = coordinator.max_events + 1

        with (
            patch.object(
                coordinator,
                "async_request_refresh",
                new_callable=AsyncMock,
            ),
            patch.object(
                coordinator,
                "async_setup_keymaster_overrides",
                new_callable=AsyncMock,
            ),
        ):
            await coordinator.update_config(config)

        assert coordinator.event_overrides is not original_overrides

    async def test_update_config_reloads_persisted_mappings_after_recreation(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify recreated overrides retain persisted slot ownership."""
        data = dict(mock_config_entry.data)
        data[CONF_LOCK_ENTRY] = "Front Door"
        mock_config_entry.add_to_hass(hass)
        hass.config_entries.async_update_entry(mock_config_entry, data=data)

        coordinator = RentalControlCoordinator(hass, mock_config_entry)
        identity_key = "persisted-active"
        coordinator._slot_mappings = {
            "mappings": {
                identity_key: {
                    "slot": 10,
                    "status": "occupied",
                    "identity": {
                        "identity_key": identity_key,
                        "summary": "Active Guest",
                        "slot_name": "Active Guest",
                        "uid_aliases": [],
                        "booking_aliases": [],
                    },
                    "fingerprint_history": [],
                }
            }
        }

        config = dict(mock_config_entry.data)
        config.update(mock_config_entry.options)
        config[CONF_MAX_EVENTS] = coordinator.max_events + 1

        with (
            patch.object(
                coordinator,
                "async_request_refresh",
                new_callable=AsyncMock,
            ),
            patch.object(
                coordinator,
                "async_setup_keymaster_overrides",
                new_callable=AsyncMock,
            ),
        ):
            await coordinator.update_config(config)

        assert coordinator.event_overrides is not None
        assert identity_key in coordinator.event_overrides.persisted_mappings

    async def test_update_config_handles_invalid_persisted_mappings(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify config updates do not fail on duplicate persisted slots."""
        data = dict(mock_config_entry.data)
        data[CONF_LOCK_ENTRY] = "Front Door"
        mock_config_entry.add_to_hass(hass)
        hass.config_entries.async_update_entry(mock_config_entry, data=data)

        coordinator = RentalControlCoordinator(hass, mock_config_entry)
        coordinator._slot_mappings = {
            "mappings": {
                "dup-a": {"slot": 10, "status": "occupied"},
                "dup-b": {"slot": 10, "status": "occupied"},
            }
        }

        config = dict(mock_config_entry.data)
        config.update(mock_config_entry.options)
        config[CONF_MAX_EVENTS] = coordinator.max_events + 1

        with (
            patch.object(
                coordinator,
                "async_request_refresh",
                new_callable=AsyncMock,
            ),
            patch.object(
                coordinator,
                "async_setup_keymaster_overrides",
                new_callable=AsyncMock,
            ),
        ):
            await coordinator.update_config(config)

        assert coordinator.event_overrides is not None
        assert coordinator.event_overrides.persisted_mappings == {}

    async def test_update_config_bootstraps_overrides_after_recreation(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify overrides are bootstrapped from HA state after recreation."""
        mock_config_entry.add_to_hass(hass)

        coordinator = RentalControlCoordinator(hass, mock_config_entry)

        config = dict(mock_config_entry.data)
        config.update(mock_config_entry.options)
        config[CONF_LOCK_ENTRY] = "Front Door"

        with (
            patch.object(
                coordinator,
                "async_request_refresh",
                new_callable=AsyncMock,
            ),
            patch.object(
                coordinator,
                "async_setup_keymaster_overrides",
                new_callable=AsyncMock,
            ) as mock_bootstrap,
        ):
            await coordinator.update_config(config)

        mock_bootstrap.assert_called_once()

    async def test_bootstrap_uses_slugified_lockname_for_entity_ids(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify entity ID lookups use slugified lockname."""
        mock_config_entry.add_to_hass(hass)

        data = dict(mock_config_entry.data)
        data[CONF_LOCK_ENTRY] = "Front Door"
        hass.config_entries.async_update_entry(mock_config_entry, data=data)

        coordinator = RentalControlCoordinator(hass, mock_config_entry)
        mock_overrides = MagicMock()
        mock_overrides.ready = False
        mock_overrides.async_check_overrides = AsyncMock()
        coordinator.event_overrides = mock_overrides
        mock_update = AsyncMock()
        object.__setattr__(coordinator, "update_event_overrides", mock_update)

        # Set states using slugified name (what HA would actually have)
        hass.states.async_set("text.front_door_code_slot_10_pin", "1234")
        hass.states.async_set("text.front_door_code_slot_10_name", "Guest")

        await coordinator.async_setup_keymaster_overrides()

        mock_update.assert_awaited_once()
        call_args = mock_update.call_args[0]
        assert call_args[1] == "1234"
        assert call_args[2] == "Guest"


class TestChildLockDiscovery:
    """Tests for child lock discovery (spec 006, T001/T002)."""

    async def test_find_parent_entry_id_matches_lockname(
        self, hass: HomeAssistant
    ) -> None:
        """Test _find_parent_entry_id returns correct entry_id.

        When a keymaster config entry has a lockname matching the
        coordinator's lockname, its entry_id is returned.
        """
        parent_entry = MockConfigEntry(
            domain="keymaster",
            data={"lockname": "front_door"},
            entry_id="km_parent_123",
        )
        parent_entry.add_to_hass(hass)

        rc_entry = MockConfigEntry(
            domain="rental_control",
            title="Test Rental",
            version=8,
            unique_id="test-child-lock-id",
            data={
                "name": "Test Rental",
                "url": "https://example.com/calendar.ics",
                "timezone": "America/New_York",
                "checkin": "16:00",
                "checkout": "11:00",
                "start_slot": 10,
                "max_events": 3,
                "days": 90,
                "verify_ssl": True,
                "ignore_non_reserved": False,
                CONF_LOCK_ENTRY: "front_door",
            },
            entry_id="rc_test_entry",
        )
        rc_entry.add_to_hass(hass)
        coordinator = RentalControlCoordinator(hass, rc_entry)

        assert coordinator.lockname == "front_door"
        assert coordinator._parent_entry_id == "km_parent_123"

    async def test_find_parent_entry_id_non_slugified(
        self, hass: HomeAssistant
    ) -> None:
        """Test _find_parent_entry_id normalizes locknames.

        When a keymaster config entry has a non-slugified lockname that
        normalizes to the coordinator's lockname, its entry_id is returned.
        """
        parent_entry = MockConfigEntry(
            domain="keymaster",
            data={"lockname": "Front Door"},
            entry_id="km_nonslugged",
        )
        parent_entry.add_to_hass(hass)

        rc_entry = MockConfigEntry(
            domain="rental_control",
            title="Test Rental",
            version=8,
            unique_id="test-nonslugged-id",
            data={
                "name": "Test Rental",
                "url": "https://example.com/calendar.ics",
                "timezone": "America/New_York",
                "checkin": "16:00",
                "checkout": "11:00",
                "start_slot": 10,
                "max_events": 3,
                "days": 90,
                "verify_ssl": True,
                "ignore_non_reserved": False,
                CONF_LOCK_ENTRY: "front_door",
            },
            entry_id="rc_nonslugged",
        )
        rc_entry.add_to_hass(hass)
        coordinator = RentalControlCoordinator(hass, rc_entry)

        assert coordinator.lockname == "front_door"
        assert coordinator._parent_entry_id == "km_nonslugged"

    async def test_find_parent_entry_id_no_match(self, hass: HomeAssistant) -> None:
        """Test _find_parent_entry_id returns None when no match.

        When no keymaster entry has a matching lockname, None is
        returned and child discovery produces an empty set.
        """
        rc_entry = MockConfigEntry(
            domain="rental_control",
            title="Test Rental",
            version=8,
            unique_id="test-no-match-id",
            data={
                "name": "Test Rental",
                "url": "https://example.com/calendar.ics",
                "timezone": "America/New_York",
                "checkin": "16:00",
                "checkout": "11:00",
                "start_slot": 10,
                "max_events": 3,
                "days": 90,
                "verify_ssl": True,
                "ignore_non_reserved": False,
                CONF_LOCK_ENTRY: "nonexistent_lock",
            },
            entry_id="rc_no_match",
        )
        rc_entry.add_to_hass(hass)
        coordinator = RentalControlCoordinator(hass, rc_entry)

        assert coordinator.lockname == "nonexistent_lock"
        assert coordinator._parent_entry_id is None
        assert coordinator._child_locknames == set()

    async def test_discover_child_locks_finds_children(
        self, hass: HomeAssistant
    ) -> None:
        """Test _discover_child_locks finds entries with parent_entry_id.

        Child keymaster entries referencing the parent entry_id are
        discovered and their locknames collected.
        """
        parent_entry = MockConfigEntry(
            domain="keymaster",
            data={"lockname": "front_door"},
            entry_id="km_parent_456",
        )
        parent_entry.add_to_hass(hass)

        child_entry = MockConfigEntry(
            domain="keymaster",
            data={
                "lockname": "back_door",
                "parent_entry_id": "km_parent_456",
            },
            entry_id="km_child_789",
        )
        child_entry.add_to_hass(hass)

        rc_entry = MockConfigEntry(
            domain="rental_control",
            title="Test Rental",
            version=8,
            unique_id="test-child-disc-id",
            data={
                "name": "Test Rental",
                "url": "https://example.com/calendar.ics",
                "timezone": "America/New_York",
                "checkin": "16:00",
                "checkout": "11:00",
                "start_slot": 10,
                "max_events": 3,
                "days": 90,
                "verify_ssl": True,
                "ignore_non_reserved": False,
                CONF_LOCK_ENTRY: "front_door",
            },
            entry_id="rc_disc_entry",
        )
        rc_entry.add_to_hass(hass)
        coordinator = RentalControlCoordinator(hass, rc_entry)

        children = coordinator._discover_child_locks()
        assert children == {"back_door"}

    async def test_discover_child_locks_multiple_children(
        self, hass: HomeAssistant
    ) -> None:
        """Test _discover_child_locks handles multiple children.

        Multiple keymaster entries referencing the same parent are
        all discovered.
        """
        parent_entry = MockConfigEntry(
            domain="keymaster",
            data={"lockname": "front_door"},
            entry_id="km_parent_multi",
        )
        parent_entry.add_to_hass(hass)

        for name, eid in [
            ("back_door", "km_child_a"),
            ("garage_door", "km_child_b"),
        ]:
            child = MockConfigEntry(
                domain="keymaster",
                data={
                    "lockname": name,
                    "parent_entry_id": "km_parent_multi",
                },
                entry_id=eid,
            )
            child.add_to_hass(hass)

        rc_entry = MockConfigEntry(
            domain="rental_control",
            title="Test Rental",
            version=8,
            unique_id="test-multi-child-id",
            data={
                "name": "Test Rental",
                "url": "https://example.com/calendar.ics",
                "timezone": "America/New_York",
                "checkin": "16:00",
                "checkout": "11:00",
                "start_slot": 10,
                "max_events": 3,
                "days": 90,
                "verify_ssl": True,
                "ignore_non_reserved": False,
                CONF_LOCK_ENTRY: "front_door",
            },
            entry_id="rc_multi_entry",
        )
        rc_entry.add_to_hass(hass)
        coordinator = RentalControlCoordinator(hass, rc_entry)

        children = coordinator._discover_child_locks()
        assert children == {"back_door", "garage_door"}

    async def test_discover_child_locks_no_children(self, hass: HomeAssistant) -> None:
        """Test _discover_child_locks returns empty when no children.

        When no keymaster entries reference the parent, an empty set
        is returned.
        """
        parent_entry = MockConfigEntry(
            domain="keymaster",
            data={"lockname": "front_door"},
            entry_id="km_parent_alone",
        )
        parent_entry.add_to_hass(hass)

        rc_entry = MockConfigEntry(
            domain="rental_control",
            title="Test Rental",
            version=8,
            unique_id="test-no-child-id",
            data={
                "name": "Test Rental",
                "url": "https://example.com/calendar.ics",
                "timezone": "America/New_York",
                "checkin": "16:00",
                "checkout": "11:00",
                "start_slot": 10,
                "max_events": 3,
                "days": 90,
                "verify_ssl": True,
                "ignore_non_reserved": False,
                CONF_LOCK_ENTRY: "front_door",
            },
            entry_id="rc_no_child_entry",
        )
        rc_entry.add_to_hass(hass)
        coordinator = RentalControlCoordinator(hass, rc_entry)

        children = coordinator._discover_child_locks()
        assert children == set()

    async def test_monitored_locknames_includes_parent_and_children(
        self, hass: HomeAssistant
    ) -> None:
        """Test monitored_locknames returns parent + children.

        The frozenset should include the parent lockname and all
        discovered child locknames.
        """
        parent_entry = MockConfigEntry(
            domain="keymaster",
            data={"lockname": "front_door"},
            entry_id="km_parent_mon",
        )
        parent_entry.add_to_hass(hass)

        child_entry = MockConfigEntry(
            domain="keymaster",
            data={
                "lockname": "side_door",
                "parent_entry_id": "km_parent_mon",
            },
            entry_id="km_child_mon",
        )
        child_entry.add_to_hass(hass)

        rc_entry = MockConfigEntry(
            domain="rental_control",
            title="Test Rental",
            version=8,
            unique_id="test-mon-id",
            data={
                "name": "Test Rental",
                "url": "https://example.com/calendar.ics",
                "timezone": "America/New_York",
                "checkin": "16:00",
                "checkout": "11:00",
                "start_slot": 10,
                "max_events": 3,
                "days": 90,
                "verify_ssl": True,
                "ignore_non_reserved": False,
                CONF_LOCK_ENTRY: "front_door",
            },
            entry_id="rc_mon_entry",
        )
        rc_entry.add_to_hass(hass)
        coordinator = RentalControlCoordinator(hass, rc_entry)

        result = coordinator.monitored_locknames
        assert isinstance(result, frozenset)
        assert result == frozenset({"front_door", "side_door"})

    async def test_monitored_locknames_no_lock_configured(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Test monitored_locknames returns empty when no lock.

        When no keymaster lock is configured, the property returns
        an empty frozenset.
        """
        mock_config_entry.add_to_hass(hass)
        coordinator = RentalControlCoordinator(hass, mock_config_entry)

        assert coordinator.lockname is None
        assert coordinator.monitored_locknames == frozenset()

    async def test_monitored_locknames_parent_only(self, hass: HomeAssistant) -> None:
        """Test monitored_locknames with parent only (no children).

        When only the parent lock exists, the frozenset contains
        just the parent lockname.
        """
        parent_entry = MockConfigEntry(
            domain="keymaster",
            data={"lockname": "front_door"},
            entry_id="km_parent_only",
        )
        parent_entry.add_to_hass(hass)

        rc_entry = MockConfigEntry(
            domain="rental_control",
            title="Test Rental",
            version=8,
            unique_id="test-parent-only-id",
            data={
                "name": "Test Rental",
                "url": "https://example.com/calendar.ics",
                "timezone": "America/New_York",
                "checkin": "16:00",
                "checkout": "11:00",
                "start_slot": 10,
                "max_events": 3,
                "days": 90,
                "verify_ssl": True,
                "ignore_non_reserved": False,
                CONF_LOCK_ENTRY: "front_door",
            },
            entry_id="rc_parent_only_entry",
        )
        rc_entry.add_to_hass(hass)
        coordinator = RentalControlCoordinator(hass, rc_entry)

        assert coordinator.monitored_locknames == frozenset({"front_door"})

    async def test_child_discovery_refresh_in_update_data(
        self, hass: HomeAssistant
    ) -> None:
        """Test child locks are re-discovered each update cycle.

        When _async_update_data runs, child locknames are refreshed
        and changes are detected.
        """
        parent_entry = MockConfigEntry(
            domain="keymaster",
            data={"lockname": "front_door"},
            entry_id="km_parent_refresh",
        )
        parent_entry.add_to_hass(hass)

        rc_entry = MockConfigEntry(
            domain="rental_control",
            title="Test Rental",
            version=8,
            unique_id="test-refresh-id",
            data={
                "name": "Test Rental",
                "url": "https://example.com/calendar.ics",
                "timezone": "America/New_York",
                "checkin": "16:00",
                "checkout": "11:00",
                "start_slot": 10,
                "max_events": 3,
                "days": 90,
                "verify_ssl": True,
                "ignore_non_reserved": False,
                CONF_LOCK_ENTRY: "front_door",
            },
            entry_id="rc_refresh_entry",
        )
        rc_entry.add_to_hass(hass)
        coordinator = RentalControlCoordinator(hass, rc_entry)

        # Initially no children
        assert coordinator._child_locknames == set()

        frozen_time = datetime(2024, 12, 20, 12, 0, 0, tzinfo=dt_util.UTC)

        # Add a child lock entry dynamically
        child_entry = MockConfigEntry(
            domain="keymaster",
            data={
                "lockname": "patio_door",
                "parent_entry_id": "km_parent_refresh",
            },
            entry_id="km_child_dynamic",
        )
        child_entry.add_to_hass(hass)

        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=frozen_time),
            patch.object(dt_util, "start_of_local_day", return_value=frozen_time),
        ):
            mock_session.get(
                "https://example.com/calendar.ics",
                status=200,
                body=calendar_data.AIRBNB_ICS_CALENDAR,
            )

            await coordinator._async_update_data()

        assert coordinator._child_locknames == {"patio_door"}
        assert coordinator.monitored_locknames == frozenset(
            {"front_door", "patio_door"}
        )


# ===========================================================================
# Spec 006 Phase 5: Dynamic child lock lifecycle
# =============================================================


class TestChildLockDynamicLifecycle:
    """Tests for dynamic add/remove of child locks at runtime."""

    async def test_child_lock_added_at_runtime(self, hass: HomeAssistant) -> None:
        """Test new child lock discovered after next coordinator refresh.

        Starts with parent-only, adds a child lock entry, refreshes,
        and verifies monitored_locknames includes the new child.
        """
        parent_entry = MockConfigEntry(
            domain="keymaster",
            data={"lockname": "front_door"},
            entry_id="km_parent_add",
        )
        parent_entry.add_to_hass(hass)

        rc_entry = MockConfigEntry(
            domain="rental_control",
            title="Test Rental",
            version=8,
            unique_id="test-add-lifecycle",
            data={
                "name": "Test Rental",
                "url": "https://example.com/calendar.ics",
                "timezone": "America/New_York",
                "checkin": "16:00",
                "checkout": "11:00",
                "start_slot": 10,
                "max_events": 3,
                "days": 90,
                "verify_ssl": True,
                "ignore_non_reserved": False,
                CONF_LOCK_ENTRY: "front_door",
            },
            entry_id="rc_add_lifecycle",
        )
        rc_entry.add_to_hass(hass)
        coordinator = RentalControlCoordinator(hass, rc_entry)

        frozen_time = datetime(2024, 12, 20, 12, 0, 0, tzinfo=dt_util.UTC)

        # First refresh — parent only
        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=frozen_time),
            patch.object(
                dt_util,
                "start_of_local_day",
                return_value=frozen_time,
            ),
        ):
            mock_session.get(
                "https://example.com/calendar.ics",
                status=200,
                body=calendar_data.AIRBNB_ICS_CALENDAR,
            )
            await coordinator._async_update_data()

        assert coordinator.monitored_locknames == frozenset({"front_door"})

        # Add a child lock dynamically
        child_entry = MockConfigEntry(
            domain="keymaster",
            data={
                "lockname": "garage_door",
                "parent_entry_id": "km_parent_add",
            },
            entry_id="km_child_add_lifecycle",
        )
        child_entry.add_to_hass(hass)

        # Second refresh — should discover the new child
        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=frozen_time),
            patch.object(
                dt_util,
                "start_of_local_day",
                return_value=frozen_time,
            ),
        ):
            mock_session.get(
                "https://example.com/calendar.ics",
                status=200,
                body=calendar_data.AIRBNB_ICS_CALENDAR,
            )
            await coordinator._async_update_data()

        assert coordinator.monitored_locknames == frozenset(
            {"front_door", "garage_door"}
        )

    async def test_child_lock_removed_at_runtime(self, hass: HomeAssistant) -> None:
        """Test removed child lock disappears after next refresh.

        Starts with parent + child, removes the child entry,
        refreshes, and verifies monitored_locknames reverts to
        parent-only.
        """
        parent_entry = MockConfigEntry(
            domain="keymaster",
            data={"lockname": "front_door"},
            entry_id="km_parent_remove",
        )
        parent_entry.add_to_hass(hass)

        child_entry = MockConfigEntry(
            domain="keymaster",
            data={
                "lockname": "patio_door",
                "parent_entry_id": "km_parent_remove",
            },
            entry_id="km_child_remove_lifecycle",
        )
        child_entry.add_to_hass(hass)

        rc_entry = MockConfigEntry(
            domain="rental_control",
            title="Test Rental",
            version=8,
            unique_id="test-remove-lifecycle",
            data={
                "name": "Test Rental",
                "url": "https://example.com/calendar.ics",
                "timezone": "America/New_York",
                "checkin": "16:00",
                "checkout": "11:00",
                "start_slot": 10,
                "max_events": 3,
                "days": 90,
                "verify_ssl": True,
                "ignore_non_reserved": False,
                CONF_LOCK_ENTRY: "front_door",
            },
            entry_id="rc_remove_lifecycle",
        )
        rc_entry.add_to_hass(hass)
        coordinator = RentalControlCoordinator(hass, rc_entry)

        frozen_time = datetime(2024, 12, 20, 12, 0, 0, tzinfo=dt_util.UTC)

        # First refresh — parent + child
        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=frozen_time),
            patch.object(
                dt_util,
                "start_of_local_day",
                return_value=frozen_time,
            ),
        ):
            mock_session.get(
                "https://example.com/calendar.ics",
                status=200,
                body=calendar_data.AIRBNB_ICS_CALENDAR,
            )
            await coordinator._async_update_data()

        assert coordinator.monitored_locknames == frozenset(
            {"front_door", "patio_door"}
        )

        # Remove the child lock entry
        await hass.config_entries.async_remove(child_entry.entry_id)
        await hass.async_block_till_done()

        # Second refresh — should no longer include child
        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=frozen_time),
            patch.object(
                dt_util,
                "start_of_local_day",
                return_value=frozen_time,
            ),
        ):
            mock_session.get(
                "https://example.com/calendar.ics",
                status=200,
                body=calendar_data.AIRBNB_ICS_CALENDAR,
            )
            await coordinator._async_update_data()

        assert coordinator.monitored_locknames == frozenset({"front_door"})

    async def test_second_child_added_preserves_first(
        self, hass: HomeAssistant
    ) -> None:
        """Test adding a second child preserves the first.

        Starts with parent + one child, adds a second child,
        refreshes, and verifies all three are in monitored_locknames.
        """
        parent_entry = MockConfigEntry(
            domain="keymaster",
            data={"lockname": "front_door"},
            entry_id="km_parent_multi",
        )
        parent_entry.add_to_hass(hass)

        child1 = MockConfigEntry(
            domain="keymaster",
            data={
                "lockname": "side_door",
                "parent_entry_id": "km_parent_multi",
            },
            entry_id="km_child1_multi",
        )
        child1.add_to_hass(hass)

        rc_entry = MockConfigEntry(
            domain="rental_control",
            title="Test Rental",
            version=8,
            unique_id="test-multi-lifecycle",
            data={
                "name": "Test Rental",
                "url": "https://example.com/calendar.ics",
                "timezone": "America/New_York",
                "checkin": "16:00",
                "checkout": "11:00",
                "start_slot": 10,
                "max_events": 3,
                "days": 90,
                "verify_ssl": True,
                "ignore_non_reserved": False,
                CONF_LOCK_ENTRY: "front_door",
            },
            entry_id="rc_multi_lifecycle",
        )
        rc_entry.add_to_hass(hass)
        coordinator = RentalControlCoordinator(hass, rc_entry)

        frozen_time = datetime(2024, 12, 20, 12, 0, 0, tzinfo=dt_util.UTC)

        # First refresh — parent + child1
        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=frozen_time),
            patch.object(
                dt_util,
                "start_of_local_day",
                return_value=frozen_time,
            ),
        ):
            mock_session.get(
                "https://example.com/calendar.ics",
                status=200,
                body=calendar_data.AIRBNB_ICS_CALENDAR,
            )
            await coordinator._async_update_data()

        assert coordinator.monitored_locknames == frozenset({"front_door", "side_door"})

        # Add second child
        child2 = MockConfigEntry(
            domain="keymaster",
            data={
                "lockname": "garage_door",
                "parent_entry_id": "km_parent_multi",
            },
            entry_id="km_child2_multi",
        )
        child2.add_to_hass(hass)

        # Second refresh — all three
        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=frozen_time),
            patch.object(
                dt_util,
                "start_of_local_day",
                return_value=frozen_time,
            ),
        ):
            mock_session.get(
                "https://example.com/calendar.ics",
                status=200,
                body=calendar_data.AIRBNB_ICS_CALENDAR,
            )
            await coordinator._async_update_data()

        assert coordinator.monitored_locknames == frozenset(
            {"front_door", "side_door", "garage_door"}
        )


# ---------------------------------------------------------------------------
# Honor event times tests (spec 007)
# ---------------------------------------------------------------------------


class TestHonorEventTimesTimedEvents:
    """Tests for honor_event_times with timed (datetime) events."""

    async def test_honor_true_timed_event_with_override_uses_calendar_times(
        self, hass: HomeAssistant
    ) -> None:
        """Test honor=True + timed event + override → calendar times.

        When honor_event_times is True and the event has explicit times
        and an override exists, _ical_parser builds CalendarEvent with
        calendar times (not override times).
        """
        frozen_time = datetime(2024, 12, 20, 12, 0, 0, tzinfo=dt_util.UTC)
        future_start = (frozen_time + timedelta(days=5)).strftime("%Y%m%d")
        future_end = (frozen_time + timedelta(days=10)).strftime("%Y%m%d")

        # Calendar event with explicit times: 15:00 checkin, 10:00 checkout
        timed_ics = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
DTSTART:{future_start}T150000
DTEND:{future_end}T100000
UID:honor-test-1@example.com
SUMMARY:Reserved: Honor Test Guest
DESCRIPTION:Email: test@example.com
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR
"""

        # Create config entry with honor_event_times=True and a lock entry
        entry = MockConfigEntry(
            domain="rental_control",
            title="Honor Test",
            version=8,
            unique_id="honor-test-1",
            data={
                "name": "Honor Test",
                "url": "https://example.com/calendar.ics",
                "timezone": "America/New_York",
                "checkin": "16:00",
                "checkout": "11:00",
                "start_slot": 10,
                "max_events": 3,
                "days": 90,
                "verify_ssl": True,
                "ignore_non_reserved": False,
                "honor_event_times": True,
                CONF_LOCK_ENTRY: "front_door",
            },
            entry_id="honor_test_1",
        )
        entry.add_to_hass(hass)

        # Set up keymaster entry so lockname resolves
        km_entry = MockConfigEntry(
            domain="keymaster",
            data={"lockname": "front_door"},
            entry_id="km_honor_1",
        )
        km_entry.add_to_hass(hass)

        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=frozen_time),
            patch.object(dt_util, "start_of_local_day", return_value=frozen_time),
        ):
            mock_session.get(
                "https://example.com/calendar.ics",
                status=200,
                body=timed_ics,
            )

            coordinator = RentalControlCoordinator(hass, entry)

            # Inject override with DIFFERENT times (18:00 / 09:00 in UTC)
            from custom_components.rental_control.event_overrides import EventOverrides

            coordinator.event_overrides = EventOverrides(10, 3)
            coordinator.event_overrides._overrides[10] = {
                "slot_name": "Reserved: Honor Test Guest",
                "slot_code": "1234",
                "start_time": datetime(2024, 12, 25, 18, 0, 0, tzinfo=dt_util.UTC),
                "end_time": datetime(2024, 12, 30, 9, 0, 0, tzinfo=dt_util.UTC),
            }
            coordinator.event_overrides.async_check_overrides = AsyncMock()  # type: ignore[method-assign]

            result = await coordinator._async_update_data()

        assert len(result) == 1
        event = result[0]
        # Calendar times are 15:00 and 10:00 America/New_York
        # These get combined with the event date and converted to UTC
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("America/New_York")
        expected_start = dt.as_utc(
            datetime.combine(
                datetime(2024, 12, 25).date(),
                datetime(2024, 12, 25, 15, 0, 0).time(),
                tz,
            )
        )
        expected_end = dt.as_utc(
            datetime.combine(
                datetime(2024, 12, 30).date(),
                datetime(2024, 12, 30, 10, 0, 0).time(),
                tz,
            )
        )
        assert event.start == expected_start
        assert event.end == expected_end

    async def test_honor_false_with_override_uses_override_times(
        self, hass: HomeAssistant
    ) -> None:
        """Test honor=False + override → override times (FR-005).

        When honor_event_times is False and an override exists,
        _ical_parser builds CalendarEvent with override times,
        preserving current behavior.
        """
        frozen_time = datetime(2024, 12, 20, 12, 0, 0, tzinfo=dt_util.UTC)
        future_start = (frozen_time + timedelta(days=5)).strftime("%Y%m%d")
        future_end = (frozen_time + timedelta(days=10)).strftime("%Y%m%d")

        timed_ics = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
DTSTART:{future_start}T150000
DTEND:{future_end}T100000
UID:honor-test-2@example.com
SUMMARY:Reserved: Honor Test Guest
DESCRIPTION:Email: test@example.com
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR
"""

        entry = MockConfigEntry(
            domain="rental_control",
            title="Honor Test",
            version=8,
            unique_id="honor-test-2",
            data={
                "name": "Honor Test",
                "url": "https://example.com/calendar.ics",
                "timezone": "America/New_York",
                "checkin": "16:00",
                "checkout": "11:00",
                "start_slot": 10,
                "max_events": 3,
                "days": 90,
                "verify_ssl": True,
                "ignore_non_reserved": False,
                "honor_event_times": False,
                CONF_LOCK_ENTRY: "front_door",
            },
            entry_id="honor_test_2",
        )
        entry.add_to_hass(hass)

        km_entry = MockConfigEntry(
            domain="keymaster",
            data={"lockname": "front_door"},
            entry_id="km_honor_2",
        )
        km_entry.add_to_hass(hass)

        # Override times: 18:00 UTC checkin, 14:00 UTC checkout
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("America/New_York")
        override_start_utc = datetime(2024, 12, 25, 18, 0, 0, tzinfo=dt_util.UTC)
        override_end_utc = datetime(2024, 12, 30, 14, 0, 0, tzinfo=dt_util.UTC)

        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=frozen_time),
            patch.object(dt_util, "start_of_local_day", return_value=frozen_time),
        ):
            mock_session.get(
                "https://example.com/calendar.ics",
                status=200,
                body=timed_ics,
            )

            coordinator = RentalControlCoordinator(hass, entry)

            from custom_components.rental_control.event_overrides import EventOverrides

            coordinator.event_overrides = EventOverrides(10, 3)
            coordinator.event_overrides._overrides[10] = {
                "slot_name": "Reserved: Honor Test Guest",
                "slot_code": "1234",
                "start_time": override_start_utc,
                "end_time": override_end_utc,
            }
            coordinator.event_overrides.async_check_overrides = AsyncMock()  # type: ignore[method-assign]

            result = await coordinator._async_update_data()

        assert len(result) == 1
        event = result[0]
        # Override times: 18:00 UTC → 13:00 EST, 14:00 UTC → 09:00 EST
        override_start_local = override_start_utc.astimezone(tz)
        override_end_local = override_end_utc.astimezone(tz)
        expected_start = dt.as_utc(
            datetime.combine(
                datetime(2024, 12, 25).date(),
                override_start_local.time(),
                tz,
            )
        )
        expected_end = dt.as_utc(
            datetime.combine(
                datetime(2024, 12, 30).date(),
                override_end_local.time(),
                tz,
            )
        )
        assert event.start == expected_start
        assert event.end == expected_end

    async def test_honor_true_timed_event_no_override_uses_calendar_times(
        self, hass: HomeAssistant
    ) -> None:
        """Test honor=True + timed event + no override → calendar times.

        When honor_event_times is True and the event has explicit times
        but no override exists, calendar times are used.
        """
        frozen_time = datetime(2024, 12, 20, 12, 0, 0, tzinfo=dt_util.UTC)
        future_start = (frozen_time + timedelta(days=5)).strftime("%Y%m%d")
        future_end = (frozen_time + timedelta(days=10)).strftime("%Y%m%d")

        timed_ics = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
DTSTART:{future_start}T150000
DTEND:{future_end}T100000
UID:honor-test-3@example.com
SUMMARY:Reserved: Honor Test Guest
DESCRIPTION:Email: test@example.com
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR
"""

        entry = MockConfigEntry(
            domain="rental_control",
            title="Honor Test",
            version=8,
            unique_id="honor-test-3",
            data={
                "name": "Honor Test",
                "url": "https://example.com/calendar.ics",
                "timezone": "America/New_York",
                "checkin": "16:00",
                "checkout": "11:00",
                "start_slot": 10,
                "max_events": 3,
                "days": 90,
                "verify_ssl": True,
                "ignore_non_reserved": False,
                "honor_event_times": True,
            },
            entry_id="honor_test_3",
        )
        entry.add_to_hass(hass)

        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=frozen_time),
            patch.object(dt_util, "start_of_local_day", return_value=frozen_time),
        ):
            mock_session.get(
                "https://example.com/calendar.ics",
                status=200,
                body=timed_ics,
            )

            coordinator = RentalControlCoordinator(hass, entry)

            result = await coordinator._async_update_data()

        assert len(result) == 1
        event = result[0]
        # Calendar times: 15:00 / 10:00 in America/New_York
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("America/New_York")
        expected_start = dt.as_utc(
            datetime.combine(
                datetime(2024, 12, 25).date(),
                datetime(2024, 12, 25, 15, 0, 0).time(),
                tz,
            )
        )
        expected_end = dt.as_utc(
            datetime.combine(
                datetime(2024, 12, 30).date(),
                datetime(2024, 12, 30, 10, 0, 0).time(),
                tz,
            )
        )
        assert event.start == expected_start
        assert event.end == expected_end

    async def test_honor_true_matching_times_no_unnecessary_update(
        self, hass: HomeAssistant
    ) -> None:
        """Test honor=True + times match → no unnecessary update (FR-007).

        When honor_event_times is True and calendar times match stored
        override times, the CalendarEvent uses calendar times (which
        happen to match), ensuring no unnecessary time-update event fires
        downstream.
        """
        frozen_time = datetime(2024, 12, 20, 12, 0, 0, tzinfo=dt_util.UTC)
        future_start = (frozen_time + timedelta(days=5)).strftime("%Y%m%d")
        future_end = (frozen_time + timedelta(days=10)).strftime("%Y%m%d")

        # Calendar event with times 15:00 / 10:00 (America/New_York)
        timed_ics = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
DTSTART:{future_start}T150000
DTEND:{future_end}T100000
UID:honor-test-4@example.com
SUMMARY:Reserved: Honor Test Guest
DESCRIPTION:Email: test@example.com
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR
"""

        entry = MockConfigEntry(
            domain="rental_control",
            title="Honor Test",
            version=8,
            unique_id="honor-test-4",
            data={
                "name": "Honor Test",
                "url": "https://example.com/calendar.ics",
                "timezone": "America/New_York",
                "checkin": "16:00",
                "checkout": "11:00",
                "start_slot": 10,
                "max_events": 3,
                "days": 90,
                "verify_ssl": True,
                "ignore_non_reserved": False,
                "honor_event_times": True,
                CONF_LOCK_ENTRY: "front_door",
            },
            entry_id="honor_test_4",
        )
        entry.add_to_hass(hass)

        km_entry = MockConfigEntry(
            domain="keymaster",
            data={"lockname": "front_door"},
            entry_id="km_honor_4",
        )
        km_entry.add_to_hass(hass)

        # Override times MATCHING calendar times
        # 15:00 EST = 20:00 UTC, 10:00 EST = 15:00 UTC
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("America/New_York")
        override_start_utc = dt.as_utc(
            datetime.combine(
                datetime(2024, 12, 25).date(),
                datetime(2024, 12, 25, 15, 0, 0).time(),
                tz,
            )
        )
        override_end_utc = dt.as_utc(
            datetime.combine(
                datetime(2024, 12, 30).date(),
                datetime(2024, 12, 30, 10, 0, 0).time(),
                tz,
            )
        )

        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=frozen_time),
            patch.object(dt_util, "start_of_local_day", return_value=frozen_time),
        ):
            mock_session.get(
                "https://example.com/calendar.ics",
                status=200,
                body=timed_ics,
            )

            coordinator = RentalControlCoordinator(hass, entry)

            from custom_components.rental_control.event_overrides import EventOverrides

            coordinator.event_overrides = EventOverrides(10, 3)
            coordinator.event_overrides._overrides[10] = {
                "slot_name": "Reserved: Honor Test Guest",
                "slot_code": "1234",
                "start_time": override_start_utc,
                "end_time": override_end_utc,
            }
            coordinator.event_overrides.async_check_overrides = AsyncMock()  # type: ignore[method-assign]

            result = await coordinator._async_update_data()

        assert len(result) == 1
        event = result[0]
        # Calendar times and override times match, so result should
        # be the same regardless — times used are calendar times
        assert event.start == override_start_utc
        assert event.end == override_end_utc


class TestHonorEventTimesAllDayEvents:
    """Tests for honor_event_times with all-day (date-only) events."""

    async def test_honor_true_allday_no_override_uses_defaults(
        self, hass: HomeAssistant
    ) -> None:
        """Test honor=True + all-day + no override → default times.

        When honor_event_times is True and the event is all-day
        (date-only DTSTART) with no override, configured default
        checkin/checkout times are used.
        """
        frozen_time = datetime(2024, 12, 20, 12, 0, 0, tzinfo=dt_util.UTC)
        future_start = (frozen_time + timedelta(days=5)).strftime("%Y%m%d")
        future_end = (frozen_time + timedelta(days=10)).strftime("%Y%m%d")

        # All-day event — DTSTART/DTEND are dates, not datetimes
        allday_ics = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
DTSTART;VALUE=DATE:{future_start}
DTEND;VALUE=DATE:{future_end}
UID:honor-allday-1@example.com
SUMMARY:Reserved: Allday Guest
DESCRIPTION:Email: test@example.com
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR
"""

        entry = MockConfigEntry(
            domain="rental_control",
            title="Honor Test",
            version=8,
            unique_id="honor-allday-1",
            data={
                "name": "Honor Test",
                "url": "https://example.com/calendar.ics",
                "timezone": "America/New_York",
                "checkin": "16:00",
                "checkout": "11:00",
                "start_slot": 10,
                "max_events": 3,
                "days": 90,
                "verify_ssl": True,
                "ignore_non_reserved": False,
                "honor_event_times": True,
            },
            entry_id="honor_allday_1",
        )
        entry.add_to_hass(hass)

        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=frozen_time),
            patch.object(dt_util, "start_of_local_day", return_value=frozen_time),
        ):
            mock_session.get(
                "https://example.com/calendar.ics",
                status=200,
                body=allday_ics,
            )

            coordinator = RentalControlCoordinator(hass, entry)

            result = await coordinator._async_update_data()

        assert len(result) == 1
        event = result[0]
        # Default times: 16:00 checkin, 11:00 checkout in America/New_York
        from datetime import time
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("America/New_York")
        expected_start = dt.as_utc(
            datetime.combine(
                datetime(2024, 12, 25).date(),
                time(16, 0),
                tz,
            )
        )
        expected_end = dt.as_utc(
            datetime.combine(
                datetime(2024, 12, 30).date(),
                time(11, 0),
                tz,
            )
        )
        assert event.start == expected_start
        assert event.end == expected_end

    async def test_honor_true_allday_with_override_uses_override_times(
        self, hass: HomeAssistant
    ) -> None:
        """Test honor=True + all-day + override → override times.

        When honor_event_times is True and the event is all-day
        but an override exists, override times are used (not defaults).
        """
        frozen_time = datetime(2024, 12, 20, 12, 0, 0, tzinfo=dt_util.UTC)
        future_start = (frozen_time + timedelta(days=5)).strftime("%Y%m%d")
        future_end = (frozen_time + timedelta(days=10)).strftime("%Y%m%d")

        allday_ics = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
DTSTART;VALUE=DATE:{future_start}
DTEND;VALUE=DATE:{future_end}
UID:honor-allday-2@example.com
SUMMARY:Reserved: Allday Guest
DESCRIPTION:Email: test@example.com
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR
"""

        entry = MockConfigEntry(
            domain="rental_control",
            title="Honor Test",
            version=8,
            unique_id="honor-allday-2",
            data={
                "name": "Honor Test",
                "url": "https://example.com/calendar.ics",
                "timezone": "America/New_York",
                "checkin": "16:00",
                "checkout": "11:00",
                "start_slot": 10,
                "max_events": 3,
                "days": 90,
                "verify_ssl": True,
                "ignore_non_reserved": False,
                "honor_event_times": True,
                CONF_LOCK_ENTRY: "front_door",
            },
            entry_id="honor_allday_2",
        )
        entry.add_to_hass(hass)

        km_entry = MockConfigEntry(
            domain="keymaster",
            data={"lockname": "front_door"},
            entry_id="km_allday_2",
        )
        km_entry.add_to_hass(hass)

        # Override times: 18:00 UTC checkin, 14:00 UTC checkout
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("America/New_York")
        override_start_utc = datetime(2024, 12, 25, 18, 0, 0, tzinfo=dt_util.UTC)
        override_end_utc = datetime(2024, 12, 30, 14, 0, 0, tzinfo=dt_util.UTC)

        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=frozen_time),
            patch.object(dt_util, "start_of_local_day", return_value=frozen_time),
        ):
            mock_session.get(
                "https://example.com/calendar.ics",
                status=200,
                body=allday_ics,
            )

            coordinator = RentalControlCoordinator(hass, entry)

            from custom_components.rental_control.event_overrides import EventOverrides

            coordinator.event_overrides = EventOverrides(10, 3)
            coordinator.event_overrides._overrides[10] = {
                "slot_name": "Reserved: Allday Guest",
                "slot_code": "1234",
                "start_time": override_start_utc,
                "end_time": override_end_utc,
            }
            coordinator.event_overrides.async_check_overrides = AsyncMock()  # type: ignore[method-assign]

            result = await coordinator._async_update_data()

        assert len(result) == 1
        event = result[0]
        # Override times: 18:00 UTC → 13:00 EST, 14:00 UTC → 09:00 EST
        override_start_local = override_start_utc.astimezone(tz)
        override_end_local = override_end_utc.astimezone(tz)
        expected_start = dt.as_utc(
            datetime.combine(
                datetime(2024, 12, 25).date(),
                override_start_local.time(),
                tz,
            )
        )
        expected_end = dt.as_utc(
            datetime.combine(
                datetime(2024, 12, 30).date(),
                override_end_local.time(),
                tz,
            )
        )
        assert event.start == expected_start
        assert event.end == expected_end

    async def test_honor_false_allday_no_override_uses_defaults(
        self, hass: HomeAssistant
    ) -> None:
        """Test honor=False + all-day + no override → default times.

        When honor_event_times is False and the event is all-day with
        no override, configured default checkin/checkout times are used
        (existing behavior preserved).
        """
        frozen_time = datetime(2024, 12, 20, 12, 0, 0, tzinfo=dt_util.UTC)
        future_start = (frozen_time + timedelta(days=5)).strftime("%Y%m%d")
        future_end = (frozen_time + timedelta(days=10)).strftime("%Y%m%d")

        allday_ics = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
DTSTART;VALUE=DATE:{future_start}
DTEND;VALUE=DATE:{future_end}
UID:honor-allday-3@example.com
SUMMARY:Reserved: Allday Guest
DESCRIPTION:Email: test@example.com
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR
"""

        entry = MockConfigEntry(
            domain="rental_control",
            title="Honor Test",
            version=8,
            unique_id="honor-allday-3",
            data={
                "name": "Honor Test",
                "url": "https://example.com/calendar.ics",
                "timezone": "America/New_York",
                "checkin": "16:00",
                "checkout": "11:00",
                "start_slot": 10,
                "max_events": 3,
                "days": 90,
                "verify_ssl": True,
                "ignore_non_reserved": False,
                "honor_event_times": False,
            },
            entry_id="honor_allday_3",
        )
        entry.add_to_hass(hass)

        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=frozen_time),
            patch.object(dt_util, "start_of_local_day", return_value=frozen_time),
        ):
            mock_session.get(
                "https://example.com/calendar.ics",
                status=200,
                body=allday_ics,
            )

            coordinator = RentalControlCoordinator(hass, entry)

            result = await coordinator._async_update_data()

        assert len(result) == 1
        event = result[0]
        # Default times: 16:00 checkin, 11:00 checkout in America/New_York
        from datetime import time
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("America/New_York")
        expected_start = dt.as_utc(
            datetime.combine(
                datetime(2024, 12, 25).date(),
                time(16, 0),
                tz,
            )
        )
        expected_end = dt.as_utc(
            datetime.combine(
                datetime(2024, 12, 30).date(),
                time(11, 0),
                tz,
            )
        )
        assert event.start == expected_start
        assert event.end == expected_end


class TestHonorEventTimesEdgeCases:
    """Edge case tests for honor_event_times feature."""

    async def test_honor_true_timed_to_allday_transition(
        self, hass: HomeAssistant
    ) -> None:
        """Test transitioning event from timed to all-day with honor=True.

        Verifies that when an event changes from having explicit times
        to being all-day between refreshes, the system gracefully falls
        back to default times.
        """
        frozen_time = datetime(2024, 12, 20, 12, 0, 0, tzinfo=dt_util.UTC)
        future_start = (frozen_time + timedelta(days=5)).strftime("%Y%m%d")
        future_end = (frozen_time + timedelta(days=10)).strftime("%Y%m%d")

        # All-day event (date only — no times)
        allday_ics = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
DTSTART;VALUE=DATE:{future_start}
DTEND;VALUE=DATE:{future_end}
UID:transition-test@example.com
SUMMARY:Reserved: Transition Guest
DESCRIPTION:Email: test@example.com
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR
"""

        entry = MockConfigEntry(
            domain="rental_control",
            title="Transition Test",
            version=8,
            unique_id="transition-test-1",
            data={
                "name": "Transition Test",
                "url": "https://example.com/calendar.ics",
                "timezone": "America/New_York",
                "checkin": "16:00",
                "checkout": "11:00",
                "start_slot": 10,
                "max_events": 3,
                "days": 90,
                "verify_ssl": True,
                "ignore_non_reserved": False,
                "honor_event_times": True,
            },
            entry_id="transition_test_1",
        )
        entry.add_to_hass(hass)

        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=frozen_time),
            patch.object(dt_util, "start_of_local_day", return_value=frozen_time),
        ):
            mock_session.get(
                "https://example.com/calendar.ics",
                status=200,
                body=allday_ics,
            )

            coordinator = RentalControlCoordinator(hass, entry)

            result = await coordinator._async_update_data()

        assert len(result) == 1
        event = result[0]
        # All-day event with honor=True and no override -> default times
        from datetime import time as time_cls
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("America/New_York")
        expected_start = dt.as_utc(
            datetime.combine(
                datetime(2024, 12, 25).date(),
                time_cls(16, 0),
                tz,
            )
        )
        assert event.start == expected_start

    async def test_honor_enable_mid_session_via_update_config(
        self, hass: HomeAssistant
    ) -> None:
        """Test enabling honor_event_times mid-session via update_config.

        Verifies that calling update_config with honor_event_times=True
        updates the coordinator's attribute so the next refresh uses
        calendar times for timed events.
        """
        entry = MockConfigEntry(
            domain="rental_control",
            title="Mid-session Test",
            version=8,
            unique_id="mid-session-test-1",
            data={
                "name": "Mid-session Test",
                "url": "https://example.com/calendar.ics",
                "timezone": "America/New_York",
                "checkin": "16:00",
                "checkout": "11:00",
                "start_slot": 10,
                "max_events": 3,
                "days": 90,
                "verify_ssl": True,
                "ignore_non_reserved": False,
                "honor_event_times": False,
                "refresh_frequency": 2,
            },
            entry_id="mid_session_test_1",
        )
        entry.add_to_hass(hass)

        coordinator = RentalControlCoordinator(hass, entry)
        coordinator.async_request_refresh = AsyncMock()

        # Initially disabled
        assert coordinator.honor_event_times is False

        # Update config to enable
        new_config = dict(entry.data)
        new_config["honor_event_times"] = True

        await coordinator.update_config(new_config)

        # Now enabled
        assert coordinator.honor_event_times is True
        assert coordinator.async_request_refresh.called


# ---------------------------------------------------------------------------
# Description-based time extraction integration tests (spec 007)
# ---------------------------------------------------------------------------


class TestHonorEventTimesDescriptionExtraction:
    """Integration tests for description-based time extraction."""

    async def test_honor_true_allday_description_times_no_override(
        self, hass: HomeAssistant
    ) -> None:
        """T008/T013: honor=True + all-day + description times → extracted.

        When honor_event_times is True and the event is all-day with
        check-in/out times in the description and no override, the
        description times are used.
        """
        frozen_time = datetime(2024, 12, 20, 12, 0, 0, tzinfo=dt_util.UTC)
        future_start = (frozen_time + timedelta(days=5)).strftime("%Y%m%d")
        future_end = (frozen_time + timedelta(days=10)).strftime("%Y%m%d")

        allday_ics = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
DTSTART;VALUE=DATE:{future_start}
DTEND;VALUE=DATE:{future_end}
UID:desc-test-1@example.com
SUMMARY:Reserved: Desc Guest
DESCRIPTION:Check-in time: 15\\nCheck-out time: 10\\nEmail: test@example.com
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR
"""

        entry = MockConfigEntry(
            domain="rental_control",
            title="Desc Test",
            version=8,
            unique_id="desc-test-1",
            data={
                "name": "Desc Test",
                "url": "https://example.com/calendar.ics",
                "timezone": "America/New_York",
                "checkin": "16:00",
                "checkout": "11:00",
                "start_slot": 10,
                "max_events": 3,
                "days": 90,
                "verify_ssl": True,
                "ignore_non_reserved": False,
                "honor_event_times": True,
            },
            entry_id="desc_test_1",
        )
        entry.add_to_hass(hass)

        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=frozen_time),
            patch.object(dt_util, "start_of_local_day", return_value=frozen_time),
        ):
            mock_session.get(
                "https://example.com/calendar.ics",
                status=200,
                body=allday_ics,
            )

            coordinator = RentalControlCoordinator(hass, entry)
            result = await coordinator._async_update_data()

        assert len(result) == 1
        event = result[0]
        from datetime import time as time_cls
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("America/New_York")
        # Description times: 15:00 checkin, 10:00 checkout
        expected_start = dt.as_utc(
            datetime.combine(
                datetime(2024, 12, 25).date(),
                time_cls(15, 0),
                tz,
            )
        )
        expected_end = dt.as_utc(
            datetime.combine(
                datetime(2024, 12, 30).date(),
                time_cls(10, 0),
                tz,
            )
        )
        assert event.start == expected_start
        assert event.end == expected_end

    async def test_honor_true_allday_description_checkin_only_no_override(
        self, hass: HomeAssistant
    ) -> None:
        """T014: honor=True + all-day + only checkin in description.

        When only check-in time is in the description, check-out
        falls back to configured default.
        """
        frozen_time = datetime(2024, 12, 20, 12, 0, 0, tzinfo=dt_util.UTC)
        future_start = (frozen_time + timedelta(days=5)).strftime("%Y%m%d")
        future_end = (frozen_time + timedelta(days=10)).strftime("%Y%m%d")

        allday_ics = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
DTSTART;VALUE=DATE:{future_start}
DTEND;VALUE=DATE:{future_end}
UID:desc-test-2@example.com
SUMMARY:Reserved: Desc Guest
DESCRIPTION:Check-in time: 15\\nEmail: test@example.com
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR
"""

        entry = MockConfigEntry(
            domain="rental_control",
            title="Desc Test",
            version=8,
            unique_id="desc-test-2",
            data={
                "name": "Desc Test",
                "url": "https://example.com/calendar.ics",
                "timezone": "America/New_York",
                "checkin": "16:00",
                "checkout": "11:00",
                "start_slot": 10,
                "max_events": 3,
                "days": 90,
                "verify_ssl": True,
                "ignore_non_reserved": False,
                "honor_event_times": True,
            },
            entry_id="desc_test_2",
        )
        entry.add_to_hass(hass)

        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=frozen_time),
            patch.object(dt_util, "start_of_local_day", return_value=frozen_time),
        ):
            mock_session.get(
                "https://example.com/calendar.ics",
                status=200,
                body=allday_ics,
            )

            coordinator = RentalControlCoordinator(hass, entry)
            result = await coordinator._async_update_data()

        assert len(result) == 1
        event = result[0]
        from datetime import time as time_cls
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("America/New_York")
        # Description checkin: 15:00, checkout falls back to default 11:00
        expected_start = dt.as_utc(
            datetime.combine(
                datetime(2024, 12, 25).date(),
                time_cls(15, 0),
                tz,
            )
        )
        expected_end = dt.as_utc(
            datetime.combine(
                datetime(2024, 12, 30).date(),
                time_cls(11, 0),
                tz,
            )
        )
        assert event.start == expected_start
        assert event.end == expected_end

    async def test_honor_true_allday_no_description_times_no_override(
        self, hass: HomeAssistant
    ) -> None:
        """T015: honor=True + all-day + no desc times + no override.

        Falls back to configured default checkin/checkout times.
        """
        frozen_time = datetime(2024, 12, 20, 12, 0, 0, tzinfo=dt_util.UTC)
        future_start = (frozen_time + timedelta(days=5)).strftime("%Y%m%d")
        future_end = (frozen_time + timedelta(days=10)).strftime("%Y%m%d")

        allday_ics = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
DTSTART;VALUE=DATE:{future_start}
DTEND;VALUE=DATE:{future_end}
UID:desc-test-3@example.com
SUMMARY:Reserved: Desc Guest
DESCRIPTION:Email: test@example.com\\nNo times here
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR
"""

        entry = MockConfigEntry(
            domain="rental_control",
            title="Desc Test",
            version=8,
            unique_id="desc-test-3",
            data={
                "name": "Desc Test",
                "url": "https://example.com/calendar.ics",
                "timezone": "America/New_York",
                "checkin": "16:00",
                "checkout": "11:00",
                "start_slot": 10,
                "max_events": 3,
                "days": 90,
                "verify_ssl": True,
                "ignore_non_reserved": False,
                "honor_event_times": True,
            },
            entry_id="desc_test_3",
        )
        entry.add_to_hass(hass)

        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=frozen_time),
            patch.object(dt_util, "start_of_local_day", return_value=frozen_time),
        ):
            mock_session.get(
                "https://example.com/calendar.ics",
                status=200,
                body=allday_ics,
            )

            coordinator = RentalControlCoordinator(hass, entry)
            result = await coordinator._async_update_data()

        assert len(result) == 1
        event = result[0]
        from datetime import time as time_cls
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("America/New_York")
        # No description times, falls back to defaults: 16:00 / 11:00
        expected_start = dt.as_utc(
            datetime.combine(
                datetime(2024, 12, 25).date(),
                time_cls(16, 0),
                tz,
            )
        )
        expected_end = dt.as_utc(
            datetime.combine(
                datetime(2024, 12, 30).date(),
                time_cls(11, 0),
                tz,
            )
        )
        assert event.start == expected_start
        assert event.end == expected_end

    async def test_honor_true_allday_description_times_with_override(
        self, hass: HomeAssistant
    ) -> None:
        """T022: honor=True + all-day + desc times + override.

        Description times take priority over override times.
        """
        frozen_time = datetime(2024, 12, 20, 12, 0, 0, tzinfo=dt_util.UTC)
        future_start = (frozen_time + timedelta(days=5)).strftime("%Y%m%d")
        future_end = (frozen_time + timedelta(days=10)).strftime("%Y%m%d")

        allday_ics = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
DTSTART;VALUE=DATE:{future_start}
DTEND;VALUE=DATE:{future_end}
UID:desc-test-4@example.com
SUMMARY:Reserved: Desc Guest
DESCRIPTION:Check-in time: 15\\nCheck-out time: 10\\nEmail: test@example.com
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR
"""

        entry = MockConfigEntry(
            domain="rental_control",
            title="Desc Test",
            version=8,
            unique_id="desc-test-4",
            data={
                "name": "Desc Test",
                "url": "https://example.com/calendar.ics",
                "timezone": "America/New_York",
                "checkin": "16:00",
                "checkout": "11:00",
                "start_slot": 10,
                "max_events": 3,
                "days": 90,
                "verify_ssl": True,
                "ignore_non_reserved": False,
                "honor_event_times": True,
                CONF_LOCK_ENTRY: "front_door",
            },
            entry_id="desc_test_4",
        )
        entry.add_to_hass(hass)

        km_entry = MockConfigEntry(
            domain="keymaster",
            data={"lockname": "front_door"},
            entry_id="km_desc_4",
        )
        km_entry.add_to_hass(hass)

        override_start_utc = datetime(2024, 12, 25, 21, 0, 0, tzinfo=dt_util.UTC)
        override_end_utc = datetime(2024, 12, 30, 14, 0, 0, tzinfo=dt_util.UTC)

        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=frozen_time),
            patch.object(dt_util, "start_of_local_day", return_value=frozen_time),
        ):
            mock_session.get(
                "https://example.com/calendar.ics",
                status=200,
                body=allday_ics,
            )

            coordinator = RentalControlCoordinator(hass, entry)

            from custom_components.rental_control.event_overrides import EventOverrides

            coordinator.event_overrides = EventOverrides(10, 3)
            coordinator.event_overrides._overrides[10] = {
                "slot_name": "Reserved: Desc Guest",
                "slot_code": "1234",
                "start_time": override_start_utc,
                "end_time": override_end_utc,
            }
            coordinator.event_overrides.async_check_overrides = AsyncMock()  # type: ignore[method-assign]

            result = await coordinator._async_update_data()

        assert len(result) == 1
        event = result[0]
        from datetime import time as time_cls
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("America/New_York")
        # Description times: 15:00 checkin, 10:00 checkout (override ignored)
        expected_start = dt.as_utc(
            datetime.combine(
                datetime(2024, 12, 25).date(),
                time_cls(15, 0),
                tz,
            )
        )
        expected_end = dt.as_utc(
            datetime.combine(
                datetime(2024, 12, 30).date(),
                time_cls(10, 0),
                tz,
            )
        )
        assert event.start == expected_start
        assert event.end == expected_end

    async def test_honor_true_allday_partial_desc_with_override_fallback(
        self, hass: HomeAssistant
    ) -> None:
        """T023: honor=True + all-day + partial desc + override fallback.

        When only check-in is in the description, check-out falls
        back to override time (not default).
        """
        frozen_time = datetime(2024, 12, 20, 12, 0, 0, tzinfo=dt_util.UTC)
        future_start = (frozen_time + timedelta(days=5)).strftime("%Y%m%d")
        future_end = (frozen_time + timedelta(days=10)).strftime("%Y%m%d")

        allday_ics = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
DTSTART;VALUE=DATE:{future_start}
DTEND;VALUE=DATE:{future_end}
UID:desc-test-5@example.com
SUMMARY:Reserved: Desc Guest
DESCRIPTION:Check-in time: 15\\nEmail: test@example.com
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR
"""

        entry = MockConfigEntry(
            domain="rental_control",
            title="Desc Test",
            version=8,
            unique_id="desc-test-5",
            data={
                "name": "Desc Test",
                "url": "https://example.com/calendar.ics",
                "timezone": "America/New_York",
                "checkin": "16:00",
                "checkout": "11:00",
                "start_slot": 10,
                "max_events": 3,
                "days": 90,
                "verify_ssl": True,
                "ignore_non_reserved": False,
                "honor_event_times": True,
                CONF_LOCK_ENTRY: "front_door",
            },
            entry_id="desc_test_5",
        )
        entry.add_to_hass(hass)

        km_entry = MockConfigEntry(
            domain="keymaster",
            data={"lockname": "front_door"},
            entry_id="km_desc_5",
        )
        km_entry.add_to_hass(hass)

        # Override: 18:00 UTC start, 14:00 UTC end
        # In America/New_York: 13:00 start, 09:00 end
        override_start_utc = datetime(2024, 12, 25, 18, 0, 0, tzinfo=dt_util.UTC)
        override_end_utc = datetime(2024, 12, 30, 14, 0, 0, tzinfo=dt_util.UTC)

        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=frozen_time),
            patch.object(dt_util, "start_of_local_day", return_value=frozen_time),
        ):
            mock_session.get(
                "https://example.com/calendar.ics",
                status=200,
                body=allday_ics,
            )

            coordinator = RentalControlCoordinator(hass, entry)

            from custom_components.rental_control.event_overrides import EventOverrides

            coordinator.event_overrides = EventOverrides(10, 3)
            coordinator.event_overrides._overrides[10] = {
                "slot_name": "Reserved: Desc Guest",
                "slot_code": "1234",
                "start_time": override_start_utc,
                "end_time": override_end_utc,
            }
            coordinator.event_overrides.async_check_overrides = AsyncMock()  # type: ignore[method-assign]

            result = await coordinator._async_update_data()

        assert len(result) == 1
        event = result[0]
        from datetime import time as time_cls
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("America/New_York")
        # Checkin from description: 15:00
        # Checkout falls back to override: 14:00 UTC → 09:00 EST
        override_end_local = override_end_utc.astimezone(tz)
        expected_start = dt.as_utc(
            datetime.combine(
                datetime(2024, 12, 25).date(),
                time_cls(15, 0),
                tz,
            )
        )
        expected_end = dt.as_utc(
            datetime.combine(
                datetime(2024, 12, 30).date(),
                override_end_local.time(),
                tz,
            )
        )
        assert event.start == expected_start
        assert event.end == expected_end

    async def test_honor_true_allday_no_desc_times_with_override_fallback(
        self, hass: HomeAssistant
    ) -> None:
        """T024: honor=True + all-day + no desc times + override.

        When no description times are found, falls back to override.
        """
        frozen_time = datetime(2024, 12, 20, 12, 0, 0, tzinfo=dt_util.UTC)
        future_start = (frozen_time + timedelta(days=5)).strftime("%Y%m%d")
        future_end = (frozen_time + timedelta(days=10)).strftime("%Y%m%d")

        allday_ics = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
DTSTART;VALUE=DATE:{future_start}
DTEND;VALUE=DATE:{future_end}
UID:desc-test-6@example.com
SUMMARY:Reserved: Desc Guest
DESCRIPTION:Email: test@example.com\\nNo times here
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR
"""

        entry = MockConfigEntry(
            domain="rental_control",
            title="Desc Test",
            version=8,
            unique_id="desc-test-6",
            data={
                "name": "Desc Test",
                "url": "https://example.com/calendar.ics",
                "timezone": "America/New_York",
                "checkin": "16:00",
                "checkout": "11:00",
                "start_slot": 10,
                "max_events": 3,
                "days": 90,
                "verify_ssl": True,
                "ignore_non_reserved": False,
                "honor_event_times": True,
                CONF_LOCK_ENTRY: "front_door",
            },
            entry_id="desc_test_6",
        )
        entry.add_to_hass(hass)

        km_entry = MockConfigEntry(
            domain="keymaster",
            data={"lockname": "front_door"},
            entry_id="km_desc_6",
        )
        km_entry.add_to_hass(hass)

        # Override: 18:00 UTC start, 14:00 UTC end
        override_start_utc = datetime(2024, 12, 25, 18, 0, 0, tzinfo=dt_util.UTC)
        override_end_utc = datetime(2024, 12, 30, 14, 0, 0, tzinfo=dt_util.UTC)

        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=frozen_time),
            patch.object(dt_util, "start_of_local_day", return_value=frozen_time),
        ):
            mock_session.get(
                "https://example.com/calendar.ics",
                status=200,
                body=allday_ics,
            )

            coordinator = RentalControlCoordinator(hass, entry)

            from custom_components.rental_control.event_overrides import EventOverrides

            coordinator.event_overrides = EventOverrides(10, 3)
            coordinator.event_overrides._overrides[10] = {
                "slot_name": "Reserved: Desc Guest",
                "slot_code": "1234",
                "start_time": override_start_utc,
                "end_time": override_end_utc,
            }
            coordinator.event_overrides.async_check_overrides = AsyncMock()  # type: ignore[method-assign]

            result = await coordinator._async_update_data()

        assert len(result) == 1
        event = result[0]
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("America/New_York")
        # No desc times → falls back to override times
        override_start_local = override_start_utc.astimezone(tz)
        override_end_local = override_end_utc.astimezone(tz)
        expected_start = dt.as_utc(
            datetime.combine(
                datetime(2024, 12, 25).date(),
                override_start_local.time(),
                tz,
            )
        )
        expected_end = dt.as_utc(
            datetime.combine(
                datetime(2024, 12, 30).date(),
                override_end_local.time(),
                tz,
            )
        )
        assert event.start == expected_start
        assert event.end == expected_end

    async def test_honor_false_allday_description_times_ignored(
        self, hass: HomeAssistant
    ) -> None:
        """T037: honor=False + all-day + description times → ignored.

        When honor_event_times is False, description times are not
        extracted; default times are used.
        """
        frozen_time = datetime(2024, 12, 20, 12, 0, 0, tzinfo=dt_util.UTC)
        future_start = (frozen_time + timedelta(days=5)).strftime("%Y%m%d")
        future_end = (frozen_time + timedelta(days=10)).strftime("%Y%m%d")

        allday_ics = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
DTSTART;VALUE=DATE:{future_start}
DTEND;VALUE=DATE:{future_end}
UID:desc-test-7@example.com
SUMMARY:Reserved: Desc Guest
DESCRIPTION:Check-in time: 15\\nCheck-out time: 10\\nEmail: test@example.com
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR
"""

        entry = MockConfigEntry(
            domain="rental_control",
            title="Desc Test",
            version=8,
            unique_id="desc-test-7",
            data={
                "name": "Desc Test",
                "url": "https://example.com/calendar.ics",
                "timezone": "America/New_York",
                "checkin": "16:00",
                "checkout": "11:00",
                "start_slot": 10,
                "max_events": 3,
                "days": 90,
                "verify_ssl": True,
                "ignore_non_reserved": False,
                "honor_event_times": False,
            },
            entry_id="desc_test_7",
        )
        entry.add_to_hass(hass)

        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=frozen_time),
            patch.object(dt_util, "start_of_local_day", return_value=frozen_time),
        ):
            mock_session.get(
                "https://example.com/calendar.ics",
                status=200,
                body=allday_ics,
            )

            coordinator = RentalControlCoordinator(hass, entry)
            result = await coordinator._async_update_data()

        assert len(result) == 1
        event = result[0]
        from datetime import time as time_cls
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("America/New_York")
        # honor=False → description times ignored, uses defaults 16/11
        expected_start = dt.as_utc(
            datetime.combine(
                datetime(2024, 12, 25).date(),
                time_cls(16, 0),
                tz,
            )
        )
        expected_end = dt.as_utc(
            datetime.combine(
                datetime(2024, 12, 30).date(),
                time_cls(11, 0),
                tz,
            )
        )
        assert event.start == expected_start
        assert event.end == expected_end

    async def test_honor_true_timed_event_description_times_ignored(
        self, hass: HomeAssistant
    ) -> None:
        """T038: honor=True + timed event + desc times → PMS wins.

        When the event has explicit times (datetime), PMS times take
        priority even if description also has times.
        """
        frozen_time = datetime(2024, 12, 20, 12, 0, 0, tzinfo=dt_util.UTC)
        future_start = (frozen_time + timedelta(days=5)).strftime("%Y%m%d")
        future_end = (frozen_time + timedelta(days=10)).strftime("%Y%m%d")

        # Timed event with 15:00 / 10:00 but description says 14/09
        timed_ics = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
DTSTART:{future_start}T150000
DTEND:{future_end}T100000
UID:desc-test-8@example.com
SUMMARY:Reserved: Desc Guest
DESCRIPTION:Check-in time: 14\\nCheck-out time: 9\\nEmail: test@example.com
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR
"""

        entry = MockConfigEntry(
            domain="rental_control",
            title="Desc Test",
            version=8,
            unique_id="desc-test-8",
            data={
                "name": "Desc Test",
                "url": "https://example.com/calendar.ics",
                "timezone": "America/New_York",
                "checkin": "16:00",
                "checkout": "11:00",
                "start_slot": 10,
                "max_events": 3,
                "days": 90,
                "verify_ssl": True,
                "ignore_non_reserved": False,
                "honor_event_times": True,
            },
            entry_id="desc_test_8",
        )
        entry.add_to_hass(hass)

        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=frozen_time),
            patch.object(dt_util, "start_of_local_day", return_value=frozen_time),
        ):
            mock_session.get(
                "https://example.com/calendar.ics",
                status=200,
                body=timed_ics,
            )

            coordinator = RentalControlCoordinator(hass, entry)
            result = await coordinator._async_update_data()

        assert len(result) == 1
        event = result[0]
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("America/New_York")
        # PMS/calendar times: 15:00 / 10:00 (not description 14/09)
        expected_start = dt.as_utc(
            datetime.combine(
                datetime(2024, 12, 25).date(),
                datetime(2024, 12, 25, 15, 0, 0).time(),
                tz,
            )
        )
        expected_end = dt.as_utc(
            datetime.combine(
                datetime(2024, 12, 30).date(),
                datetime(2024, 12, 30, 10, 0, 0).time(),
                tz,
            )
        )
        assert event.start == expected_start
        assert event.end == expected_end

    async def test_honor_true_allday_12h_description_times(
        self, hass: HomeAssistant
    ) -> None:
        """T039: honor=True + all-day + 12h description times.

        12-hour format times in descriptions are correctly parsed.
        """
        frozen_time = datetime(2024, 12, 20, 12, 0, 0, tzinfo=dt_util.UTC)
        future_start = (frozen_time + timedelta(days=5)).strftime("%Y%m%d")
        future_end = (frozen_time + timedelta(days=10)).strftime("%Y%m%d")

        allday_ics = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
DTSTART;VALUE=DATE:{future_start}
DTEND;VALUE=DATE:{future_end}
UID:desc-test-9@example.com
SUMMARY:Reserved: Desc Guest
DESCRIPTION:Check-in: 4 PM\\nCheck-out: 11 AM\\nEmail: test@example.com
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR
"""

        entry = MockConfigEntry(
            domain="rental_control",
            title="Desc Test",
            version=8,
            unique_id="desc-test-9",
            data={
                "name": "Desc Test",
                "url": "https://example.com/calendar.ics",
                "timezone": "America/New_York",
                "checkin": "14:00",
                "checkout": "10:00",
                "start_slot": 10,
                "max_events": 3,
                "days": 90,
                "verify_ssl": True,
                "ignore_non_reserved": False,
                "honor_event_times": True,
            },
            entry_id="desc_test_9",
        )
        entry.add_to_hass(hass)

        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=frozen_time),
            patch.object(dt_util, "start_of_local_day", return_value=frozen_time),
        ):
            mock_session.get(
                "https://example.com/calendar.ics",
                status=200,
                body=allday_ics,
            )

            coordinator = RentalControlCoordinator(hass, entry)
            result = await coordinator._async_update_data()

        assert len(result) == 1
        event = result[0]
        from datetime import time as time_cls
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("America/New_York")
        # Description: "4 PM" → 16:00, "11 AM" → 11:00
        expected_start = dt.as_utc(
            datetime.combine(
                datetime(2024, 12, 25).date(),
                time_cls(16, 0),
                tz,
            )
        )
        expected_end = dt.as_utc(
            datetime.combine(
                datetime(2024, 12, 30).date(),
                time_cls(11, 0),
                tz,
            )
        )
        assert event.start == expected_start
        assert event.end == expected_end


# ---------------------------------------------------------------------------
# TestStoreFirstUpgradeMigration (T010 / T011)
# ---------------------------------------------------------------------------


class TestStoreFirstUpgradeMigration:
    """Tests for first-upgrade keymaster slot adoption (T010/T011)."""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_lock_entry(
        self,
        entry_id: str = "lock_test_entry",
        lockname: str = "test_lock",
        start_slot: int = 10,
        max_events: int = 3,
        event_prefix: str = "",
    ) -> MockConfigEntry:
        """Create a config entry with a keymaster lockname."""
        data: dict = {
            "name": "Lock Rental",
            "url": "https://example.com/calendar.ics",
            "timezone": "America/New_York",
            "checkin": "16:00",
            "checkout": "11:00",
            "start_slot": start_slot,
            "max_events": max_events,
            "days": 90,
            "verify_ssl": True,
            "ignore_non_reserved": False,
            "honor_event_times": False,
            "keymaster_entry_id": lockname,
        }
        if event_prefix:
            data["event_prefix"] = event_prefix
        return MockConfigEntry(
            domain="rental_control",
            title="Lock Rental",
            version=10,
            unique_id=f"lock-{entry_id}",
            data=data,
            entry_id=entry_id,
        )

    # ------------------------------------------------------------------
    # T010-1: populated slot is adopted with has_code=True
    # ------------------------------------------------------------------

    async def test_first_upgrade_adopts_populated_keymaster_slot(
        self, hass: HomeAssistant
    ) -> None:
        """T010-1: Slot with name AND code is adopted; has_code=True, no PIN."""
        from custom_components.rental_control.const import SLOT_STATUS_OCCUPIED

        entry = self._make_lock_entry()
        entry.add_to_hass(hass)

        hass.states.async_set("text.test_lock_code_slot_10_name", "Jane Doe")
        hass.states.async_set("text.test_lock_code_slot_10_pin", "1234")
        hass.states.async_set("text.test_lock_code_slot_11_name", "")
        hass.states.async_set("text.test_lock_code_slot_11_pin", "")
        hass.states.async_set("text.test_lock_code_slot_12_name", "")
        hass.states.async_set("text.test_lock_code_slot_12_pin", "")

        coordinator = RentalControlCoordinator(hass, entry)
        await coordinator.async_adopt_keymaster_slots()

        mappings = coordinator._slot_mappings.get("mappings", {})
        assert len(mappings) == 1

        mapping = next(iter(mappings.values()))
        assert mapping["slot"] == 10
        assert mapping["status"] == SLOT_STATUS_OCCUPIED

        last_obs = mapping["last_observed_actual"]
        assert last_obs["has_code"] is True
        assert "pin" not in last_obs
        assert "code" not in last_obs
        assert "slot_code" not in last_obs

    async def test_adoption_loads_event_override_persistence(
        self, hass: HomeAssistant
    ) -> None:
        """T010-1a: Adopted mappings are visible to first reconciliation."""
        entry = self._make_lock_entry(entry_id="lock_adopt_load_entry")
        entry.add_to_hass(hass)

        hass.states.async_set("text.test_lock_code_slot_10_name", "Adopt Me")
        hass.states.async_set("text.test_lock_code_slot_10_pin", "2468")
        hass.states.async_set("text.test_lock_code_slot_11_name", "")
        hass.states.async_set("text.test_lock_code_slot_11_pin", "")
        hass.states.async_set("text.test_lock_code_slot_12_name", "")
        hass.states.async_set("text.test_lock_code_slot_12_pin", "")

        coordinator = RentalControlCoordinator(hass, entry)
        await coordinator.async_adopt_keymaster_slots()

        assert coordinator.event_overrides is not None
        persisted = coordinator.event_overrides.persisted_mappings
        assert len(persisted) == 1
        assert next(iter(persisted.values()))["slot"] == 10

    # ------------------------------------------------------------------
    # T010-2: working code is NOT wiped during adoption
    # ------------------------------------------------------------------

    async def test_first_upgrade_does_not_wipe_working_codes(
        self, hass: HomeAssistant
    ) -> None:
        """T010-2: async_adopt_keymaster_slots does not wipe Keymaster state."""
        entry = self._make_lock_entry(entry_id="lock_nowipe_entry")
        entry.add_to_hass(hass)

        hass.states.async_set("text.test_lock_code_slot_10_name", "Bob Smith")
        hass.states.async_set("text.test_lock_code_slot_10_pin", "5678")
        hass.states.async_set("text.test_lock_code_slot_11_name", "")
        hass.states.async_set("text.test_lock_code_slot_11_pin", "")
        hass.states.async_set("text.test_lock_code_slot_12_name", "")
        hass.states.async_set("text.test_lock_code_slot_12_pin", "")

        coordinator = RentalControlCoordinator(hass, entry)
        await coordinator.async_adopt_keymaster_slots()

        # Verify the PIN entity was NOT wiped — adoption is read-only
        pin_state = hass.states.get("text.test_lock_code_slot_10_pin")
        assert pin_state is not None
        assert pin_state.state == "5678", "PIN should not be wiped during adoption"
        name_state = hass.states.get("text.test_lock_code_slot_10_name")
        assert name_state is not None
        assert name_state.state == "Bob Smith", "Name should not be wiped"

    # ------------------------------------------------------------------
    # T010-3: empty slot produces no mapping
    # ------------------------------------------------------------------

    async def test_first_upgrade_empty_slot_not_adopted(
        self, hass: HomeAssistant
    ) -> None:
        """T010-3: Slot with empty name and code produces no mapping."""
        entry = self._make_lock_entry(entry_id="lock_empty_entry")
        entry.add_to_hass(hass)

        hass.states.async_set("text.test_lock_code_slot_10_name", "")
        hass.states.async_set("text.test_lock_code_slot_10_pin", "")
        hass.states.async_set("text.test_lock_code_slot_11_name", "")
        hass.states.async_set("text.test_lock_code_slot_11_pin", "")
        hass.states.async_set("text.test_lock_code_slot_12_name", "")
        hass.states.async_set("text.test_lock_code_slot_12_pin", "")

        coordinator = RentalControlCoordinator(hass, entry)
        await coordinator.async_adopt_keymaster_slots()

        mappings = coordinator._slot_mappings.get("mappings", {})
        assert len(mappings) == 0

    # ------------------------------------------------------------------
    # T011-4: prefixed name is stripped in adoption identity
    # ------------------------------------------------------------------

    async def test_first_upgrade_prefixed_name_adopted(
        self, hass: HomeAssistant
    ) -> None:
        """T011-4: Slot name 'VR Jane Doe' stores slot_name 'Jane Doe'."""
        entry = self._make_lock_entry(
            entry_id="lock_prefix_entry",
            lockname="vr_lock",
            event_prefix="VR",
        )
        entry.add_to_hass(hass)

        hass.states.async_set("text.vr_lock_code_slot_10_name", "VR Jane Doe")
        hass.states.async_set("text.vr_lock_code_slot_10_pin", "9999")
        hass.states.async_set("text.vr_lock_code_slot_11_name", "")
        hass.states.async_set("text.vr_lock_code_slot_11_pin", "")
        hass.states.async_set("text.vr_lock_code_slot_12_name", "")
        hass.states.async_set("text.vr_lock_code_slot_12_pin", "")

        coordinator = RentalControlCoordinator(hass, entry)
        await coordinator.async_adopt_keymaster_slots()

        mappings = coordinator._slot_mappings.get("mappings", {})
        assert len(mappings) == 1

        mapping = next(iter(mappings.values()))
        identity = mapping["identity"]
        assert identity["slot_name"] == "Jane Doe"
        assert identity["summary"] == "Jane Doe"
        # Raw name_state retains the full prefixed value
        assert mapping["last_observed_actual"]["name_state"] == "VR Jane Doe"

    # ------------------------------------------------------------------
    # T011-5: phantom slot (name without code) → pending_clear
    # ------------------------------------------------------------------

    async def test_first_upgrade_phantom_slot_pending_clear(
        self, hass: HomeAssistant
    ) -> None:
        """T011-5: Name present but empty code produces pending_clear mapping."""
        from custom_components.rental_control.const import SLOT_STATUS_PENDING_CLEAR

        entry = self._make_lock_entry(entry_id="lock_phantom_entry")
        entry.add_to_hass(hass)

        hass.states.async_set("text.test_lock_code_slot_10_name", "Ghost Guest")
        hass.states.async_set("text.test_lock_code_slot_10_pin", "")
        hass.states.async_set("text.test_lock_code_slot_11_name", "")
        hass.states.async_set("text.test_lock_code_slot_11_pin", "")
        hass.states.async_set("text.test_lock_code_slot_12_name", "")
        hass.states.async_set("text.test_lock_code_slot_12_pin", "")

        coordinator = RentalControlCoordinator(hass, entry)
        await coordinator.async_adopt_keymaster_slots()

        mappings = coordinator._slot_mappings.get("mappings", {})
        assert len(mappings) == 1

        mapping = next(iter(mappings.values()))
        assert mapping["slot"] == 10
        assert mapping["status"] == SLOT_STATUS_PENDING_CLEAR
        assert mapping["last_observed_actual"]["has_code"] is False
        assert mapping["pending_clear_since"] is not None

    # ------------------------------------------------------------------
    # T011-6: two occupied slots → both adopted, neither wiped
    # ------------------------------------------------------------------

    async def test_first_upgrade_ambiguous_slots_blocked(
        self, hass: HomeAssistant
    ) -> None:
        """T011-6: Two populated slots are both adopted without being wiped."""
        from custom_components.rental_control.const import SLOT_STATUS_OCCUPIED

        entry = self._make_lock_entry(entry_id="lock_ambig_entry")
        entry.add_to_hass(hass)

        hass.states.async_set("text.test_lock_code_slot_10_name", "Alice")
        hass.states.async_set("text.test_lock_code_slot_10_pin", "1111")
        hass.states.async_set("text.test_lock_code_slot_11_name", "Bob")
        hass.states.async_set("text.test_lock_code_slot_11_pin", "2222")
        hass.states.async_set("text.test_lock_code_slot_12_name", "")
        hass.states.async_set("text.test_lock_code_slot_12_pin", "")

        coordinator = RentalControlCoordinator(hass, entry)
        await coordinator.async_adopt_keymaster_slots()

        mappings = coordinator._slot_mappings.get("mappings", {})
        assert len(mappings) == 2, "Both slots should be adopted"

        for mapping in mappings.values():
            assert mapping["status"] == SLOT_STATUS_OCCUPIED

        # Verify neither slot was wiped — entity state is unchanged
        for entity_id, expected in [
            ("text.test_lock_code_slot_10_pin", "1111"),
            ("text.test_lock_code_slot_10_name", "Alice"),
            ("text.test_lock_code_slot_11_pin", "2222"),
            ("text.test_lock_code_slot_11_name", "Bob"),
        ]:
            state = hass.states.get(entity_id)
            assert state is not None
            assert state.state == expected, (
                f"{entity_id} should not be modified during adoption"
            )

    # ------------------------------------------------------------------
    # T011-7: pending_clear fence survives restart (reload)
    # ------------------------------------------------------------------

    def test_pending_clear_restart_fence(self) -> None:
        """T011-7: pending_clear fence survives simulated restart."""
        from custom_components.rental_control.const import SLOT_STATUS_PENDING_CLEAR
        from custom_components.rental_control.event_overrides import EventOverrides

        phantom_mapping = {
            "slot": 10,
            "status": SLOT_STATUS_PENDING_CLEAR,
            "operation_id": "op-fence-1",
            "operation_kind": None,
            "identity": {
                "identity_key": "phantom-1",
                "summary": "Ghost",
                "slot_name": "Ghost",
                "uid_aliases": [],
                "booking_aliases": [],
            },
            "missing_count": 0,
            "pending_set_since": None,
            "pending_clear_since": "2025-06-01T00:00:00+00:00",
            "fingerprint_history": [],
            "updated_at": "2025-06-01T00:00:00+00:00",
            "last_observed_actual": {
                "slot": 10,
                "classification": "adopted",
                "name_state": "Ghost",
                "has_code": False,
                "start_state": None,
                "end_state": None,
                "use_date_range": None,
                "enabled": None,
            },
        }
        stored_mappings = {"phantom-1": phantom_mapping}

        # Initial load
        eo = EventOverrides(start_slot=10, max_slots=3)
        eo.load_persisted_mappings(stored_mappings)
        assert 10 in eo.pending_clear_slots

        # Simulate restart: create fresh EventOverrides, reload same data
        eo2 = EventOverrides(start_slot=10, max_slots=3)
        eo2.load_persisted_mappings(stored_mappings)
        assert 10 in eo2.pending_clear_slots, "Fence should be restored after restart"


class TestStaleStorePhysicalReconciliation:
    """Regression tests for stale Store mappings over real Keymaster state."""

    def _make_lock_entry(
        self,
        entry_id: str = "stale_store_entry",
        lockname: str = "stale_lock",
        start_slot: int = 6,
        max_events: int = 2,
        event_prefix: str | None = None,
        trim_names: bool = False,
        max_name_length: int = 40,
    ) -> MockConfigEntry:
        """Create a config entry with a Keymaster lock for stale-store tests."""
        data = {
            "name": "Stale Store Rental",
            "url": "https://example.com/calendar.ics",
            "timezone": "UTC",
            "checkin": "16:00",
            "checkout": "11:00",
            "start_slot": start_slot,
            "max_events": max_events,
            "days": 90,
            "verify_ssl": True,
            "ignore_non_reserved": False,
            "honor_event_times": False,
            "keymaster_entry_id": lockname,
            "code_buffer_before": 0,
            "code_buffer_after": 0,
            "trim_names": trim_names,
            "max_name_length": max_name_length,
        }
        if event_prefix is not None:
            data["event_prefix"] = event_prefix
        return MockConfigEntry(
            domain="rental_control",
            title="Stale Store Rental",
            version=10,
            unique_id=f"stale-store-{entry_id}",
            data=data,
            entry_id=entry_id,
        )

    @staticmethod
    def _set_physical_slot(
        hass: HomeAssistant,
        *,
        lockname: str = "stale_lock",
        slot: int,
        name: str,
        pin: str,
        start: datetime,
        end: datetime,
    ) -> None:
        """Set Keymaster entity states exactly as HA exposes real slots."""
        hass.states.async_set(f"text.{lockname}_code_slot_{slot}_name", name)
        hass.states.async_set(f"text.{lockname}_code_slot_{slot}_pin", pin)
        hass.states.async_set(
            f"switch.{lockname}_code_slot_{slot}_use_date_range_limits", "on"
        )
        hass.states.async_set(f"switch.{lockname}_code_slot_{slot}_enabled", "on")
        hass.states.async_set(
            f"datetime.{lockname}_code_slot_{slot}_date_range_start",
            start.isoformat(),
        )
        hass.states.async_set(
            f"datetime.{lockname}_code_slot_{slot}_date_range_end",
            end.isoformat(),
        )

    @staticmethod
    def _stale_mapping(
        *,
        identity_key: str,
        slot: int,
        stale_name: str,
        missing_count: int = 0,
    ) -> dict[str, Any]:
        """Build a stale persisted mapping whose observed state is not useful."""
        return {
            "slot": slot,
            "status": "occupied",
            "operation_id": None,
            "operation_kind": None,
            "identity": {
                "identity_key": identity_key,
                "summary": stale_name,
                "slot_name": stale_name,
                "uid_aliases": [],
                "booking_aliases": [],
            },
            "missing_count": missing_count,
            "pending_set_since": None,
            "pending_clear_since": None,
            "fingerprint_history": [],
            "updated_at": "2026-06-01T00:00:00+00:00",
            "last_observed_actual": {
                "slot": slot,
                "classification": "occupied",
                "name_state": stale_name,
                "has_code": True,
                "start_state": None,
                "end_state": None,
                "use_date_range": True,
                "enabled": True,
            },
        }

    @staticmethod
    async def _confirmed_clear(_coordinator: Any, slot: int, **_kwargs: Any) -> Any:
        """Return a confirmed clear OperationResult for patched service calls."""
        from custom_components.rental_control.util import OperationResult

        return OperationResult(kind="clear", slot=slot, confirmed=True)

    async def test_stale_store_does_not_wipe_populated_slots_on_load(
        self, hass: HomeAssistant
    ) -> None:
        """Stale Store identities must not clear populated physical slots."""
        from custom_components.rental_control.reconciliation import (
            make_reservation_fingerprint,
        )

        entry = self._make_lock_entry()
        entry.add_to_hass(hass)

        start_a = datetime(2026, 7, 1, 16, 0, tzinfo=dt_util.UTC)
        end_a = datetime(2026, 7, 5, 11, 0, tzinfo=dt_util.UTC)
        start_b = datetime(2026, 7, 6, 16, 0, tzinfo=dt_util.UTC)
        end_b = datetime(2026, 7, 10, 11, 0, tzinfo=dt_util.UTC)
        self._set_physical_slot(
            hass, slot=6, name="Alice Guest", pin="1234", start=start_a, end=end_a
        )
        self._set_physical_slot(
            hass, slot=7, name="Bob Guest", pin="5678", start=start_b, end=end_b
        )

        coordinator = RentalControlCoordinator(hass, entry)
        coordinator._slot_mappings = {
            "schema_version": 1,
            "entry_id": entry.entry_id,
            "mappings": {
                "stale-alice": self._stale_mapping(
                    identity_key="stale-alice",
                    slot=6,
                    stale_name="Former Alice",
                ),
                "stale-bob": self._stale_mapping(
                    identity_key="stale-bob",
                    slot=7,
                    stale_name="Former Bob",
                ),
            },
            "blocked_slots": {},
        }
        assert coordinator.event_overrides is not None
        coordinator.event_overrides.load_persisted_mappings(
            coordinator._slot_mappings["mappings"]
        )
        await coordinator.async_setup_keymaster_overrides()

        events = [
            CalendarEvent(start=start_a, end=end_a, summary="Alice Guest", uid="uid-a"),
            CalendarEvent(start=start_b, end=end_b, summary="Bob Guest", uid="uid-b"),
        ]
        alice_key = make_reservation_fingerprint(
            entry.entry_id, "Alice Guest", start_a, end_a
        )
        bob_key = make_reservation_fingerprint(
            entry.entry_id, "Bob Guest", start_b, end_b
        )

        with (
            patch.object(
                dt_util, "now", return_value=datetime(2026, 6, 21, tzinfo=dt_util.UTC)
            ),
            patch.object(
                dt_util,
                "start_of_local_day",
                return_value=datetime(2026, 6, 21, tzinfo=dt_util.UTC),
            ),
            patch.object(
                coordinator, "_async_fetch_calendar", new=AsyncMock(return_value=events)
            ),
            patch(
                "custom_components.rental_control.event_overrides.async_fire_clear_code",
                new=AsyncMock(side_effect=self._confirmed_clear),
            ) as clear_mock,
            patch(
                "custom_components.rental_control.event_overrides.async_fire_set_code",
                new=AsyncMock(),
            ) as set_mock,
        ):
            await coordinator._async_update_data()

        cleared_slots = [call.args[1] for call in clear_mock.await_args_list]
        assert 6 not in cleared_slots
        assert 7 not in cleared_slots
        set_mock.assert_not_awaited()
        assert coordinator._latest_plan is not None
        assert coordinator._latest_plan.overflow == {}
        assert coordinator._latest_plan.selected[alice_key] == 6
        assert coordinator._latest_plan.selected[bob_key] == 7
        assert hass.states.get("text.stale_lock_code_slot_6_pin").state == "1234"  # type: ignore[union-attr]
        assert hass.states.get("text.stale_lock_code_slot_7_pin").state == "5678"  # type: ignore[union-attr]

    async def test_stale_store_current_reservations_reclaim_slots(
        self, hass: HomeAssistant
    ) -> None:
        """Current reservations reclaim the slots their codes occupy."""
        from custom_components.rental_control.reconciliation import (
            make_reservation_fingerprint,
        )

        entry = self._make_lock_entry(entry_id="stale_store_reclaim")
        entry.add_to_hass(hass)

        start = datetime(2026, 8, 1, 16, 0, tzinfo=dt_util.UTC)
        end = datetime(2026, 8, 4, 11, 0, tzinfo=dt_util.UTC)
        self._set_physical_slot(
            hass, slot=6, name="Carol Guest", pin="2468", start=start, end=end
        )
        self._set_physical_slot(
            hass,
            slot=7,
            name="Other Occupant",
            pin="1357",
            start=start + timedelta(days=5),
            end=end + timedelta(days=5),
        )

        coordinator = RentalControlCoordinator(hass, entry)
        coordinator._slot_mappings = {
            "schema_version": 1,
            "entry_id": entry.entry_id,
            "mappings": {
                "stale-carol": self._stale_mapping(
                    identity_key="stale-carol",
                    slot=6,
                    stale_name="Old Carol",
                ),
                "stale-other": self._stale_mapping(
                    identity_key="stale-other",
                    slot=7,
                    stale_name="Old Other",
                ),
            },
            "blocked_slots": {},
        }
        assert coordinator.event_overrides is not None
        coordinator.event_overrides.load_persisted_mappings(
            coordinator._slot_mappings["mappings"]
        )
        await coordinator.async_setup_keymaster_overrides()

        event = CalendarEvent(start=start, end=end, summary="Carol Guest", uid="uid-c")
        current_key = make_reservation_fingerprint(
            entry.entry_id, "Carol Guest", start, end
        )

        with (
            patch.object(
                dt_util, "now", return_value=datetime(2026, 7, 1, tzinfo=dt_util.UTC)
            ),
            patch.object(
                dt_util,
                "start_of_local_day",
                return_value=datetime(2026, 7, 1, tzinfo=dt_util.UTC),
            ),
            patch.object(
                coordinator,
                "_async_fetch_calendar",
                new=AsyncMock(return_value=[event]),
            ),
            patch(
                "custom_components.rental_control.event_overrides.async_fire_clear_code",
                new=AsyncMock(side_effect=self._confirmed_clear),
            ) as clear_mock,
            patch(
                "custom_components.rental_control.event_overrides.async_fire_set_code",
                new=AsyncMock(),
            ) as set_mock,
        ):
            await coordinator._async_update_data()

        clear_mock.assert_not_awaited()
        set_mock.assert_not_awaited()
        assert coordinator._latest_plan is not None
        assert coordinator._latest_plan.selected[current_key] == 6
        mappings = coordinator._slot_mappings["mappings"]
        assert mappings[current_key]["slot"] == 6
        assert "stale-carol" not in mappings

    async def test_genuine_departure_still_clears_after_miss_tolerance(
        self, hass: HomeAssistant
    ) -> None:
        """A truly departed persisted mapping still clears on the third miss."""
        entry = self._make_lock_entry(entry_id="stale_store_departed", max_events=1)
        entry.add_to_hass(hass)

        start = datetime(2026, 9, 1, 16, 0, tzinfo=dt_util.UTC)
        end = datetime(2026, 9, 5, 11, 0, tzinfo=dt_util.UTC)
        self._set_physical_slot(
            hass, slot=6, name="Departed Guest", pin="7777", start=start, end=end
        )

        mapping = self._stale_mapping(
            identity_key="departed-key",
            slot=6,
            stale_name="Departed Guest",
            missing_count=2,
        )
        mapping["last_observed_actual"]["start_state"] = start.isoformat()
        mapping["last_observed_actual"]["end_state"] = end.isoformat()

        coordinator = RentalControlCoordinator(hass, entry)
        coordinator._slot_mappings = {
            "schema_version": 1,
            "entry_id": entry.entry_id,
            "mappings": {"departed-key": mapping},
            "blocked_slots": {},
        }
        assert coordinator.event_overrides is not None
        coordinator.event_overrides.load_persisted_mappings(
            coordinator._slot_mappings["mappings"]
        )
        await coordinator.async_setup_keymaster_overrides()

        with (
            patch.object(
                dt_util, "now", return_value=datetime(2026, 8, 1, tzinfo=dt_util.UTC)
            ),
            patch.object(
                dt_util,
                "start_of_local_day",
                return_value=datetime(2026, 8, 1, tzinfo=dt_util.UTC),
            ),
            patch.object(
                coordinator, "_async_fetch_calendar", new=AsyncMock(return_value=[])
            ),
            patch(
                "custom_components.rental_control.event_overrides.async_fire_clear_code",
                new=AsyncMock(side_effect=self._confirmed_clear),
            ) as clear_mock,
        ):
            await coordinator._async_update_data()

        clear_mock.assert_awaited()
        assert any(call.args[1] == 6 for call in clear_mock.await_args_list)

    async def test_coded_slot_is_not_reassigned_to_different_reservation(
        self, hass: HomeAssistant
    ) -> None:
        """A coded slot is never handed to a different reservation."""
        from custom_components.rental_control.reconciliation import (
            make_reservation_fingerprint,
        )

        entry = self._make_lock_entry(entry_id="stale_store_no_double", max_events=1)
        entry.add_to_hass(hass)

        occupied_start = datetime(2026, 10, 1, 16, 0, tzinfo=dt_util.UTC)
        occupied_end = datetime(2026, 10, 5, 11, 0, tzinfo=dt_util.UTC)
        self._set_physical_slot(
            hass,
            slot=6,
            name="Occupied Stranger",
            pin="8888",
            start=occupied_start,
            end=occupied_end,
        )

        new_start = datetime(2026, 10, 6, 16, 0, tzinfo=dt_util.UTC)
        new_end = datetime(2026, 10, 10, 11, 0, tzinfo=dt_util.UTC)
        coordinator = RentalControlCoordinator(hass, entry)
        coordinator._slot_mappings = {
            "schema_version": 1,
            "entry_id": entry.entry_id,
            "mappings": {
                "stale-stranger": self._stale_mapping(
                    identity_key="stale-stranger",
                    slot=6,
                    stale_name="Old Stranger",
                )
            },
            "blocked_slots": {},
        }
        assert coordinator.event_overrides is not None
        coordinator.event_overrides.load_persisted_mappings(
            coordinator._slot_mappings["mappings"]
        )
        await coordinator.async_setup_keymaster_overrides()

        event = CalendarEvent(start=new_start, end=new_end, summary="New Guest")
        new_key = make_reservation_fingerprint(
            entry.entry_id, "New Guest", new_start, new_end
        )

        with (
            patch.object(
                dt_util, "now", return_value=datetime(2026, 9, 1, tzinfo=dt_util.UTC)
            ),
            patch.object(
                dt_util,
                "start_of_local_day",
                return_value=datetime(2026, 9, 1, tzinfo=dt_util.UTC),
            ),
            patch.object(
                coordinator,
                "_async_fetch_calendar",
                new=AsyncMock(return_value=[event]),
            ),
            patch(
                "custom_components.rental_control.event_overrides.async_fire_clear_code",
                new=AsyncMock(side_effect=self._confirmed_clear),
            ) as clear_mock,
            patch(
                "custom_components.rental_control.event_overrides.async_fire_set_code",
                new=AsyncMock(),
            ) as set_mock,
        ):
            await coordinator._async_update_data()

        clear_mock.assert_not_awaited()
        set_mock.assert_not_awaited()
        assert coordinator._latest_plan is not None
        assert coordinator._latest_plan.selected.get(new_key) is None
        assert coordinator._latest_plan.overflow[new_key] == "no_free_slot"

    async def test_exact_store_identity_yields_to_conflicting_physical_slot(
        self, hass: HomeAssistant
    ) -> None:
        """Fresh physical names win over exact but stale Store slot claims."""
        from custom_components.rental_control.reconciliation import (
            make_reservation_fingerprint,
        )

        entry = self._make_lock_entry(entry_id="stale_store_exact_swap")
        entry.add_to_hass(hass)

        alice_start = datetime(2026, 11, 1, 16, 0, tzinfo=dt_util.UTC)
        alice_end = datetime(2026, 11, 5, 11, 0, tzinfo=dt_util.UTC)
        bob_start = datetime(2026, 11, 6, 16, 0, tzinfo=dt_util.UTC)
        bob_end = datetime(2026, 11, 10, 11, 0, tzinfo=dt_util.UTC)
        alice_key = make_reservation_fingerprint(
            entry.entry_id, "Alice Guest", alice_start, alice_end
        )
        bob_key = make_reservation_fingerprint(
            entry.entry_id, "Bob Guest", bob_start, bob_end
        )

        self._set_physical_slot(
            hass, slot=6, name="Bob Guest", pin="5678", start=bob_start, end=bob_end
        )
        self._set_physical_slot(
            hass,
            slot=7,
            name="Alice Guest",
            pin="1234",
            start=alice_start,
            end=alice_end,
        )

        coordinator = RentalControlCoordinator(hass, entry)
        coordinator._slot_mappings = {
            "schema_version": 1,
            "entry_id": entry.entry_id,
            "mappings": {
                alice_key: self._stale_mapping(
                    identity_key=alice_key,
                    slot=6,
                    stale_name="Alice Guest",
                ),
                bob_key: self._stale_mapping(
                    identity_key=bob_key,
                    slot=7,
                    stale_name="Bob Guest",
                ),
            },
            "blocked_slots": {},
        }
        assert coordinator.event_overrides is not None
        coordinator.event_overrides.load_persisted_mappings(
            coordinator._slot_mappings["mappings"]
        )
        await coordinator.async_setup_keymaster_overrides()

        events = [
            CalendarEvent(
                start=alice_start, end=alice_end, summary="Alice Guest", uid="uid-a"
            ),
            CalendarEvent(
                start=bob_start, end=bob_end, summary="Bob Guest", uid="uid-b"
            ),
        ]
        with (
            patch.object(
                dt_util,
                "now",
                return_value=datetime(2026, 10, 1, tzinfo=dt_util.UTC),
            ),
            patch.object(
                dt_util,
                "start_of_local_day",
                return_value=datetime(2026, 10, 1, tzinfo=dt_util.UTC),
            ),
            patch.object(
                coordinator, "_async_fetch_calendar", new=AsyncMock(return_value=events)
            ),
            patch(
                "custom_components.rental_control.event_overrides.async_fire_clear_code",
                new=AsyncMock(side_effect=self._confirmed_clear),
            ) as clear_mock,
            patch(
                "custom_components.rental_control.event_overrides.async_fire_set_code",
                new=AsyncMock(),
            ) as set_mock,
        ):
            await coordinator._async_update_data()

        clear_mock.assert_not_awaited()
        set_mock.assert_not_awaited()
        assert coordinator._latest_plan is not None
        assert coordinator._latest_plan.selected[alice_key] == 7
        assert coordinator._latest_plan.selected[bob_key] == 6

    async def test_exact_conflict_without_rematch_is_quarantined(
        self, hass: HomeAssistant
    ) -> None:
        """Exact Store key with different physical occupant is not overwritten."""
        from custom_components.rental_control.reconciliation import (
            make_reservation_fingerprint,
        )

        entry = self._make_lock_entry(entry_id="stale_store_exact_conflict")
        entry.add_to_hass(hass)

        alice_start = datetime(2027, 2, 1, 16, 0, tzinfo=dt_util.UTC)
        alice_end = datetime(2027, 2, 5, 11, 0, tzinfo=dt_util.UTC)
        stranger_start = datetime(2027, 2, 6, 16, 0, tzinfo=dt_util.UTC)
        stranger_end = datetime(2027, 2, 10, 11, 0, tzinfo=dt_util.UTC)
        alice_key = make_reservation_fingerprint(
            entry.entry_id, "Alice Guest", alice_start, alice_end
        )
        self._set_physical_slot(
            hass,
            slot=6,
            name="Occupied Stranger",
            pin="8888",
            start=stranger_start,
            end=stranger_end,
        )
        hass.states.async_set("text.stale_lock_code_slot_7_name", "")
        hass.states.async_set("text.stale_lock_code_slot_7_pin", "")

        coordinator = RentalControlCoordinator(hass, entry)
        coordinator._slot_mappings = {
            "schema_version": 1,
            "entry_id": entry.entry_id,
            "mappings": {
                alice_key: self._stale_mapping(
                    identity_key=alice_key,
                    slot=6,
                    stale_name="Alice Guest",
                )
            },
            "blocked_slots": {},
        }
        assert coordinator.event_overrides is not None
        coordinator.event_overrides.load_persisted_mappings(
            coordinator._slot_mappings["mappings"]
        )
        await coordinator.async_setup_keymaster_overrides()

        event = CalendarEvent(start=alice_start, end=alice_end, summary="Alice Guest")
        with (
            patch.object(
                dt_util,
                "now",
                return_value=datetime(2027, 1, 1, tzinfo=dt_util.UTC),
            ),
            patch.object(
                dt_util,
                "start_of_local_day",
                return_value=datetime(2027, 1, 1, tzinfo=dt_util.UTC),
            ),
            patch.object(
                coordinator,
                "_async_fetch_calendar",
                new=AsyncMock(return_value=[event]),
            ),
            patch(
                "custom_components.rental_control.event_overrides.async_fire_clear_code",
                new=AsyncMock(side_effect=self._confirmed_clear),
            ) as clear_mock,
            patch(
                "custom_components.rental_control.event_overrides.async_fire_set_code",
                new=AsyncMock(),
            ) as set_mock,
        ):
            await coordinator._async_update_data()

        clear_mock.assert_not_awaited()
        set_slots = [call.args[1] for call in set_mock.await_args_list]
        assert 6 not in set_slots
        assert coordinator._latest_plan is not None
        assert coordinator._latest_plan.selected[alice_key] == 7
        assert any(
            mapping["slot"] == 6 and key.startswith("observed.")
            for key, mapping in coordinator._slot_mappings["mappings"].items()
        )

    async def test_physical_reclaim_replaces_stale_empty_exact_mapping(
        self, hass: HomeAssistant
    ) -> None:
        """Fresh occupied physical mapping replaces same-key stale empty slot."""
        from custom_components.rental_control.reconciliation import (
            make_reservation_fingerprint,
        )

        entry = self._make_lock_entry(entry_id="stale_store_empty_target")
        entry.add_to_hass(hass)

        start = datetime(2026, 12, 1, 16, 0, tzinfo=dt_util.UTC)
        end = datetime(2026, 12, 5, 11, 0, tzinfo=dt_util.UTC)
        current_key = make_reservation_fingerprint(
            entry.entry_id, "Alice Guest", start, end
        )
        self._set_physical_slot(
            hass, slot=6, name="Alice Guest", pin="1234", start=start, end=end
        )
        hass.states.async_set("text.stale_lock_code_slot_7_name", "")
        hass.states.async_set("text.stale_lock_code_slot_7_pin", "")

        coordinator = RentalControlCoordinator(hass, entry)
        coordinator._slot_mappings = {
            "schema_version": 1,
            "entry_id": entry.entry_id,
            "mappings": {
                "stale-alice": self._stale_mapping(
                    identity_key="stale-alice",
                    slot=6,
                    stale_name="Old Alice",
                ),
                current_key: self._stale_mapping(
                    identity_key=current_key,
                    slot=7,
                    stale_name="Alice Guest",
                ),
            },
            "blocked_slots": {},
        }
        assert coordinator.event_overrides is not None
        coordinator.event_overrides.load_persisted_mappings(
            coordinator._slot_mappings["mappings"]
        )
        await coordinator.async_setup_keymaster_overrides()

        event = CalendarEvent(start=start, end=end, summary="Alice Guest")
        with (
            patch.object(
                dt_util,
                "now",
                return_value=datetime(2026, 11, 1, tzinfo=dt_util.UTC),
            ),
            patch.object(
                dt_util,
                "start_of_local_day",
                return_value=datetime(2026, 11, 1, tzinfo=dt_util.UTC),
            ),
            patch.object(
                coordinator,
                "_async_fetch_calendar",
                new=AsyncMock(return_value=[event]),
            ),
            patch(
                "custom_components.rental_control.event_overrides.async_fire_clear_code",
                new=AsyncMock(side_effect=self._confirmed_clear),
            ) as clear_mock,
            patch(
                "custom_components.rental_control.event_overrides.async_fire_set_code",
                new=AsyncMock(),
            ) as set_mock,
        ):
            await coordinator._async_update_data()

        clear_mock.assert_not_awaited()
        set_mock.assert_not_awaited()
        assert coordinator._latest_plan is not None
        assert coordinator._latest_plan.selected[current_key] == 6
        assert coordinator._slot_mappings["mappings"][current_key]["slot"] == 6
        assert "stale-alice" not in coordinator._slot_mappings["mappings"]

    async def test_rematch_preserves_conflicting_fresh_target_mapping(
        self, hass: HomeAssistant
    ) -> None:
        """Rematch does not orphan a fresh occupied target-key mapping."""
        from custom_components.rental_control.reconciliation import (
            make_reservation_fingerprint,
        )

        entry = self._make_lock_entry(entry_id="stale_store_fresh_target")
        entry.add_to_hass(hass)

        alice_start = datetime(2027, 1, 1, 16, 0, tzinfo=dt_util.UTC)
        alice_end = datetime(2027, 1, 5, 11, 0, tzinfo=dt_util.UTC)
        charlie_start = datetime(2027, 1, 6, 16, 0, tzinfo=dt_util.UTC)
        charlie_end = datetime(2027, 1, 10, 11, 0, tzinfo=dt_util.UTC)
        alice_key = make_reservation_fingerprint(
            entry.entry_id, "Alice Guest", alice_start, alice_end
        )
        self._set_physical_slot(
            hass,
            slot=6,
            name="Alice Guest",
            pin="1234",
            start=alice_start,
            end=alice_end,
        )
        self._set_physical_slot(
            hass,
            slot=7,
            name="Charlie Guest",
            pin="9999",
            start=charlie_start,
            end=charlie_end,
        )

        coordinator = RentalControlCoordinator(hass, entry)
        coordinator._slot_mappings = {
            "schema_version": 1,
            "entry_id": entry.entry_id,
            "mappings": {
                "stale-alice": self._stale_mapping(
                    identity_key="stale-alice",
                    slot=6,
                    stale_name="Old Alice",
                ),
                alice_key: self._stale_mapping(
                    identity_key=alice_key,
                    slot=7,
                    stale_name="Alice Guest",
                ),
            },
            "blocked_slots": {},
        }
        assert coordinator.event_overrides is not None
        coordinator.event_overrides.load_persisted_mappings(
            coordinator._slot_mappings["mappings"]
        )
        await coordinator.async_setup_keymaster_overrides()

        event = CalendarEvent(start=alice_start, end=alice_end, summary="Alice Guest")
        with (
            patch.object(
                dt_util,
                "now",
                return_value=datetime(2026, 12, 1, tzinfo=dt_util.UTC),
            ),
            patch.object(
                dt_util,
                "start_of_local_day",
                return_value=datetime(2026, 12, 1, tzinfo=dt_util.UTC),
            ),
            patch.object(
                coordinator,
                "_async_fetch_calendar",
                new=AsyncMock(return_value=[event]),
            ),
            patch(
                "custom_components.rental_control.event_overrides.async_fire_clear_code",
                new=AsyncMock(side_effect=self._confirmed_clear),
            ) as clear_mock,
            patch(
                "custom_components.rental_control.event_overrides.async_fire_set_code",
                new=AsyncMock(),
            ) as set_mock,
        ):
            await coordinator._async_update_data()

        clear_mock.assert_not_awaited()
        set_mock.assert_not_awaited()
        assert coordinator._latest_plan is not None
        assert coordinator._latest_plan.selected[alice_key] == 6
        mappings = coordinator._slot_mappings["mappings"]
        assert mappings[alice_key]["slot"] == 6
        assert any(
            mapping["slot"] == 7 and key.startswith("observed.")
            for key, mapping in mappings.items()
        )

    def test_trimmed_prefixed_physical_name_does_not_skip_ghost(
        self, hass: HomeAssistant
    ) -> None:
        """Trimmed Keymaster display names do not look like ghost conflicts."""
        entry = self._make_lock_entry(
            entry_id="stale_store_trimmed_ghost",
            event_prefix="RC",
            trim_names=True,
            max_name_length=12,
        )
        entry.add_to_hass(hass)
        coordinator = RentalControlCoordinator(hass, entry)
        mapping = self._stale_mapping(
            identity_key="trimmed-key",
            slot=6,
            stale_name="Alexandria Longguest",
        )
        mapping["last_observed_actual"]["name_state"] = "RC Alexandri"
        mapping["last_observed_actual"]["start_state"] = "2027-03-01T16:00:00+00:00"
        mapping["last_observed_actual"]["end_state"] = "2027-03-05T11:00:00+00:00"
        persisted = {"trimmed-key": mapping}

        ghosts = coordinator._build_ghost_reservations(
            set(),
            persisted,
            "RC ",
            {"trimmed-key"},
        )

        assert len(ghosts) == 1
        assert ghosts[0].identity_key == "trimmed-key"

    def test_observed_datetime_values_normalize_to_utc(self) -> None:
        """Naive observed datetimes normalize before remap comparisons."""
        parsed = RentalControlCoordinator._observed_value_as_datetime(
            "2027-03-01T16:00:00"
        )

        assert parsed == datetime(2027, 3, 1, 16, 0, tzinfo=dt_util.UTC)


class TestCoordinatorReconciliation:
    """Tests for coordinator-owned reconciliation (T028/T043)."""

    def _make_reconcile_entry(
        self,
        entry_id: str = "reconcile_test_entry",
        lockname: str = "test_lock",
        start_slot: int = 10,
        max_events: int = 3,
    ) -> MockConfigEntry:
        """Create a config entry with Keymaster lockname for reconciliation tests."""
        return MockConfigEntry(
            domain="rental_control",
            title="Reconcile Rental",
            version=10,
            unique_id=f"reconcile-{entry_id}",
            data={
                "name": "Reconcile Rental",
                "url": "https://example.com/calendar.ics",
                "timezone": "America/New_York",
                "checkin": "16:00",
                "checkout": "11:00",
                "start_slot": start_slot,
                "max_events": max_events,
                "days": 90,
                "verify_ssl": True,
                "ignore_non_reserved": False,
                "honor_event_times": False,
                "keymaster_entry_id": lockname,
                "code_buffer_before": 0,
                "code_buffer_after": 0,
            },
            entry_id=entry_id,
        )

    @staticmethod
    def _make_mock_plan(plan_id: str = "test-plan-id") -> "MagicMock":
        """Build a minimal DesiredPlan stand-in for coordinator tests."""
        mock_plan = MagicMock()
        mock_plan.plan_id = plan_id
        mock_plan.selected = {}
        mock_plan.overflow = {}
        mock_plan.actions = []
        mock_plan.diagnostics = {}
        mock_plan.validate.return_value = []
        return mock_plan

    # ------------------------------------------------------------------
    # T028-1: one DesiredPlan computed per refresh
    # ------------------------------------------------------------------

    async def test_refresh_computes_one_desired_plan(self, hass: HomeAssistant) -> None:
        """T028-1: _async_update_data computes exactly one DesiredPlan per call."""
        from unittest.mock import patch

        from custom_components.rental_control.coordinator import (
            RentalControlCoordinator,
        )

        entry = self._make_reconcile_entry()
        entry.add_to_hass(hass)
        coordinator = RentalControlCoordinator(hass, entry)
        assert coordinator.event_overrides is not None
        eo = coordinator.event_overrides

        frozen_time = datetime(2025, 6, 20, 12, 0, 0, tzinfo=dt_util.UTC)
        empty_ics = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//EN\r\nEND:VCALENDAR\r\n"
        )
        mock_plan = self._make_mock_plan("test-plan-id")

        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=frozen_time),
            patch.object(dt_util, "start_of_local_day", return_value=frozen_time),
            patch(
                "custom_components.rental_control.coordinator.compute_desired_plan",
                return_value=mock_plan,
            ) as mock_compute,
            patch.object(eo, "async_apply_plan", new=AsyncMock(return_value=[])),
            patch.object(coordinator, "async_save_slot_store", new_callable=AsyncMock),
        ):
            mock_session.get("https://example.com/calendar.ics", body=empty_ics)
            await coordinator._async_update_data()

        mock_compute.assert_called_once()
        assert coordinator.latest_plan is mock_plan

    # ------------------------------------------------------------------
    # T028-2: apply_plan called once per refresh
    # ------------------------------------------------------------------

    async def test_refresh_calls_apply_plan_once(self, hass: HomeAssistant) -> None:
        """T028-2: async_apply_plan is called exactly once per _async_update_data."""
        from unittest.mock import patch

        from custom_components.rental_control.coordinator import (
            RentalControlCoordinator,
        )

        entry = self._make_reconcile_entry()
        entry.add_to_hass(hass)
        coordinator = RentalControlCoordinator(hass, entry)
        assert coordinator.event_overrides is not None
        eo = coordinator.event_overrides

        frozen_time = datetime(2025, 6, 20, 12, 0, 0, tzinfo=dt_util.UTC)
        empty_ics = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//EN\r\nEND:VCALENDAR\r\n"
        )
        mock_plan = self._make_mock_plan("apply-plan-test")
        mock_apply = AsyncMock(return_value=[])

        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=frozen_time),
            patch.object(dt_util, "start_of_local_day", return_value=frozen_time),
            patch(
                "custom_components.rental_control.coordinator.compute_desired_plan",
                return_value=mock_plan,
            ),
            patch.object(eo, "async_apply_plan", new=mock_apply),
            patch.object(coordinator, "async_save_slot_store", new_callable=AsyncMock),
        ):
            mock_session.get("https://example.com/calendar.ics", body=empty_ics)
            await coordinator._async_update_data()

        mock_apply.assert_called_once()
        call_args = mock_apply.call_args
        assert call_args.args[0] is coordinator
        assert call_args.args[1] is mock_plan

    # ------------------------------------------------------------------
    # T028-3: latest_plan published before return
    # ------------------------------------------------------------------

    async def test_latest_plan_published_after_apply(self, hass: HomeAssistant) -> None:
        """T028-3: latest_plan is set after apply_plan completes."""
        from unittest.mock import patch

        from custom_components.rental_control.coordinator import (
            RentalControlCoordinator,
        )

        entry = self._make_reconcile_entry()
        entry.add_to_hass(hass)
        coordinator = RentalControlCoordinator(hass, entry)
        assert coordinator.latest_plan is None
        assert coordinator.event_overrides is not None
        eo = coordinator.event_overrides

        frozen_time = datetime(2025, 6, 20, 12, 0, 0, tzinfo=dt_util.UTC)
        empty_ics = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//EN\r\nEND:VCALENDAR\r\n"
        )
        mock_plan = self._make_mock_plan("publish-test")
        mock_plan.selected = {"key-a": 10}
        mock_plan.overflow = {"key-b": "capacity"}

        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=frozen_time),
            patch.object(dt_util, "start_of_local_day", return_value=frozen_time),
            patch(
                "custom_components.rental_control.coordinator.compute_desired_plan",
                return_value=mock_plan,
            ),
            patch.object(eo, "async_apply_plan", new=AsyncMock(return_value=[])),
            patch.object(coordinator, "async_save_slot_store", new_callable=AsyncMock),
        ):
            mock_session.get("https://example.com/calendar.ics", body=empty_ics)
            await coordinator._async_update_data()

        assert coordinator.latest_plan is mock_plan
        assert coordinator.get_slot_assignment("key-a") == 10
        assert coordinator.get_overflow_reason("key-b") == "capacity"
        assert coordinator.latest_overflow == {"key-b": "capacity"}

    # ------------------------------------------------------------------
    # T028-4: no reconciliation when event_overrides is None
    # ------------------------------------------------------------------

    async def test_no_reconciliation_without_lockname(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """T028-4: No plan computed when coordinator has no lockname."""
        from unittest.mock import patch

        mock_config_entry.add_to_hass(hass)
        from custom_components.rental_control.coordinator import (
            RentalControlCoordinator,
        )

        coordinator = RentalControlCoordinator(hass, mock_config_entry)
        assert coordinator.event_overrides is None

        frozen_time = datetime(2025, 6, 20, 12, 0, 0, tzinfo=dt_util.UTC)
        empty_ics = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//EN\r\nEND:VCALENDAR\r\n"
        )

        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=frozen_time),
            patch.object(dt_util, "start_of_local_day", return_value=frozen_time),
            patch(
                "custom_components.rental_control.coordinator.compute_desired_plan"
            ) as mock_compute,
            patch.object(coordinator, "async_save_slot_store", new_callable=AsyncMock),
        ):
            mock_session.get("https://example.com/calendar.ics", body=empty_ics)
            await coordinator._async_update_data()

        mock_compute.assert_not_called()
        assert coordinator.latest_plan is None

    # ------------------------------------------------------------------
    # T028-5: parser filtering / max_events preserved
    # ------------------------------------------------------------------

    async def test_max_events_passed_to_planner(self, hass: HomeAssistant) -> None:
        """T028-5: compute_desired_plan receives coordinator.max_events."""
        from unittest.mock import patch

        from custom_components.rental_control.coordinator import (
            RentalControlCoordinator,
        )

        entry = self._make_reconcile_entry(max_events=2)
        entry.add_to_hass(hass)
        coordinator = RentalControlCoordinator(hass, entry)
        assert coordinator.event_overrides is not None
        eo = coordinator.event_overrides

        frozen_time = datetime(2025, 6, 20, 12, 0, 0, tzinfo=dt_util.UTC)
        empty_ics = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//EN\r\nEND:VCALENDAR\r\n"
        )
        mock_plan = self._make_mock_plan("max-events-test")

        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=frozen_time),
            patch.object(dt_util, "start_of_local_day", return_value=frozen_time),
            patch(
                "custom_components.rental_control.coordinator.compute_desired_plan",
                return_value=mock_plan,
            ) as mock_compute,
            patch.object(eo, "async_apply_plan", new=AsyncMock(return_value=[])),
            patch.object(coordinator, "async_save_slot_store", new_callable=AsyncMock),
        ):
            mock_session.get("https://example.com/calendar.ics", body=empty_ics)
            await coordinator._async_update_data()

        call_kwargs = mock_compute.call_args.kwargs
        assert call_kwargs["max_events"] == 2

    # ------------------------------------------------------------------
    # T043-1: active checked-in guest marked protected
    # ------------------------------------------------------------------

    async def test_checkin_protection_marks_active_guest(
        self, hass: HomeAssistant
    ) -> None:
        """T043-1: checked_in sensor state marks matching reservation protected_active."""
        from unittest.mock import patch

        from custom_components.rental_control.const import CHECKIN_SENSOR
        from custom_components.rental_control.const import CHECKIN_STATE_CHECKED_IN
        from custom_components.rental_control.const import DOMAIN
        from custom_components.rental_control.coordinator import (
            RentalControlCoordinator,
        )

        entry = self._make_reconcile_entry()
        entry.add_to_hass(hass)
        coordinator = RentalControlCoordinator(hass, entry)
        assert coordinator.event_overrides is not None
        eo = coordinator.event_overrides

        mock_checkin = MagicMock()
        mock_checkin.state = CHECKIN_STATE_CHECKED_IN
        mock_checkin.extra_state_attributes = {"guest_name": "Alice Smith"}
        hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
            CHECKIN_SENSOR: mock_checkin,
        }

        frozen_time = datetime(2025, 6, 20, 12, 0, 0, tzinfo=dt_util.UTC)
        future_start = frozen_time + timedelta(days=5)
        future_end = frozen_time + timedelta(days=10)
        ics_body = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//EN\r\n"
            "BEGIN:VEVENT\r\n"
            f"DTSTART;VALUE=DATE:{future_start.strftime('%Y%m%d')}\r\n"
            f"DTEND;VALUE=DATE:{future_end.strftime('%Y%m%d')}\r\n"
            "SUMMARY:Alice Smith\r\nUID:alice-test-uid\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        mock_plan = self._make_mock_plan("protect-test")

        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=frozen_time),
            patch.object(dt_util, "start_of_local_day", return_value=frozen_time),
            patch(
                "custom_components.rental_control.coordinator.compute_desired_plan",
                return_value=mock_plan,
            ) as mock_compute,
            patch.object(eo, "async_apply_plan", new=AsyncMock(return_value=[])),
            patch.object(coordinator, "async_save_slot_store", new_callable=AsyncMock),
        ):
            mock_session.get("https://example.com/calendar.ics", body=ics_body)
            await coordinator._async_update_data()

        reservations = mock_compute.call_args.kwargs["reservations"]
        assert len(reservations) == 1
        assert reservations[0].slot_name == "Alice Smith"
        assert reservations[0].protected_active is True

    # ------------------------------------------------------------------
    # T043-2: no protection when no checkin sensor
    # ------------------------------------------------------------------

    async def test_no_protection_without_checkin_sensor(
        self, hass: HomeAssistant
    ) -> None:
        """T043-2: No reservations protected when no CheckinTrackingSensor present."""
        from unittest.mock import patch

        from custom_components.rental_control.coordinator import (
            RentalControlCoordinator,
        )

        entry = self._make_reconcile_entry()
        entry.add_to_hass(hass)
        coordinator = RentalControlCoordinator(hass, entry)
        assert coordinator.event_overrides is not None
        eo = coordinator.event_overrides

        frozen_time = datetime(2025, 6, 20, 12, 0, 0, tzinfo=dt_util.UTC)
        future_start = frozen_time + timedelta(days=5)
        future_end = frozen_time + timedelta(days=10)
        ics_body = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//EN\r\n"
            "BEGIN:VEVENT\r\n"
            f"DTSTART;VALUE=DATE:{future_start.strftime('%Y%m%d')}\r\n"
            f"DTEND;VALUE=DATE:{future_end.strftime('%Y%m%d')}\r\n"
            "SUMMARY:Bob Jones\r\nUID:bob-test-uid\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        mock_plan = self._make_mock_plan("no-protect-test")

        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=frozen_time),
            patch.object(dt_util, "start_of_local_day", return_value=frozen_time),
            patch(
                "custom_components.rental_control.coordinator.compute_desired_plan",
                return_value=mock_plan,
            ) as mock_compute,
            patch.object(eo, "async_apply_plan", new=AsyncMock(return_value=[])),
            patch.object(coordinator, "async_save_slot_store", new_callable=AsyncMock),
        ):
            mock_session.get("https://example.com/calendar.ics", body=ics_body)
            await coordinator._async_update_data()

        reservations = mock_compute.call_args.kwargs["reservations"]
        assert all(not r.protected_active for r in reservations)

    # ------------------------------------------------------------------
    # T043-3: checked_out guest marked checked_out not protected
    # ------------------------------------------------------------------

    async def test_checkin_protection_marks_checked_out_guest(
        self, hass: HomeAssistant
    ) -> None:
        """T043-3: checked_out sensor state sets checked_out flag, not protected."""
        from unittest.mock import patch

        from custom_components.rental_control.const import CHECKIN_SENSOR
        from custom_components.rental_control.const import CHECKIN_STATE_CHECKED_OUT
        from custom_components.rental_control.const import DOMAIN
        from custom_components.rental_control.coordinator import (
            RentalControlCoordinator,
        )

        entry = self._make_reconcile_entry()
        entry.add_to_hass(hass)
        coordinator = RentalControlCoordinator(hass, entry)
        assert coordinator.event_overrides is not None
        eo = coordinator.event_overrides

        mock_checkin = MagicMock()
        mock_checkin.state = CHECKIN_STATE_CHECKED_OUT
        mock_checkin.extra_state_attributes = {"guest_name": "Carol White"}
        hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
            CHECKIN_SENSOR: mock_checkin,
        }

        frozen_time = datetime(2025, 6, 20, 12, 0, 0, tzinfo=dt_util.UTC)
        future_start = frozen_time + timedelta(days=3)
        future_end = frozen_time + timedelta(days=8)
        ics_body = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//EN\r\n"
            "BEGIN:VEVENT\r\n"
            f"DTSTART;VALUE=DATE:{future_start.strftime('%Y%m%d')}\r\n"
            f"DTEND;VALUE=DATE:{future_end.strftime('%Y%m%d')}\r\n"
            "SUMMARY:Carol White\r\nUID:carol-test-uid\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        mock_plan = self._make_mock_plan("checkout-test")

        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=frozen_time),
            patch.object(dt_util, "start_of_local_day", return_value=frozen_time),
            patch(
                "custom_components.rental_control.coordinator.compute_desired_plan",
                return_value=mock_plan,
            ) as mock_compute,
            patch.object(eo, "async_apply_plan", new=AsyncMock(return_value=[])),
            patch.object(coordinator, "async_save_slot_store", new_callable=AsyncMock),
        ):
            mock_session.get("https://example.com/calendar.ics", body=ics_body)
            await coordinator._async_update_data()

        reservations = mock_compute.call_args.kwargs["reservations"]
        assert len(reservations) == 1
        assert reservations[0].slot_name == "Carol White"
        assert reservations[0].checked_out is True
        assert reservations[0].protected_active is False


# ---------------------------------------------------------------------------
# T087: Coordinator rehydrates persisted state on startup
# ---------------------------------------------------------------------------


class TestCoordinatorRehydration:
    """T087: Coordinator rehydrates mappings, aliases, missing_count, pending
    fences, and last observed actual state from the HA Store during setup.
    """

    def _make_rehydrate_entry(
        self,
        entry_id: str = "rehydrate_entry",
        lockname: str = "test_lock",
        start_slot: int = 10,
        max_events: int = 2,
    ) -> "MockConfigEntry":
        """Config entry with a Keymaster lockname for rehydration tests."""
        return MockConfigEntry(
            domain="rental_control",
            title="Rehydrate Rental",
            version=10,
            unique_id=f"rehydrate-{entry_id}",
            data={
                "name": "Rehydrate Rental",
                "url": "https://example.com/calendar.ics",
                "timezone": "UTC",
                "checkin": "16:00",
                "checkout": "11:00",
                "start_slot": start_slot,
                "max_events": max_events,
                "days": 90,
                "verify_ssl": True,
                "ignore_non_reserved": False,
                "honor_event_times": False,
                "keymaster_entry_id": lockname,
                "code_buffer_before": 0,
                "code_buffer_after": 0,
            },
            entry_id=entry_id,
        )

    def _make_occupied_mapping(
        self,
        identity_key: str,
        slot_name: str,
        slot: int,
        *,
        missing_count: int = 0,
        uid_aliases: list[str] | None = None,
        booking_aliases: list[str] | None = None,
        start_state: str | None = "2026-07-01T14:00:00+00:00",
        end_state: str | None = "2026-07-08T11:00:00+00:00",
    ) -> dict:
        """Build a v1 occupied slot mapping dict for rehydration tests."""
        return {
            "slot": slot,
            "status": "occupied",
            "operation_id": None,
            "operation_kind": None,
            "identity": {
                "identity_key": identity_key,
                "summary": slot_name,
                "slot_name": slot_name,
                "uid_aliases": uid_aliases or [],
                "booking_aliases": booking_aliases or [],
            },
            "missing_count": missing_count,
            "pending_set_since": None,
            "pending_clear_since": None,
            "fingerprint_history": [],
            "updated_at": "2026-01-01T00:00:00+00:00",
            "last_observed_actual": {
                "slot": slot,
                "classification": "occupied",
                "name_state": slot_name,
                "has_code": True,
                "start_state": start_state,
                "end_state": end_state,
                "use_date_range": True,
                "enabled": True,
            },
        }

    # ------------------------------------------------------------------
    # T087-1: persisted mappings loaded into event_overrides on startup
    # ------------------------------------------------------------------

    async def test_rehydration_loads_mappings_into_event_overrides(
        self, hass: "HomeAssistant"
    ) -> None:
        """T087-1: async_load_slot_store + load_persisted_mappings rehydrates mappings.

        Simulates the init sequence:
          1. Store data is pre-populated (mocking async_load).
          2. async_load_slot_store() reads and populates _slot_mappings.
          3. load_persisted_mappings() injects into event_overrides.
          4. persisted_mappings on event_overrides contains the rehydrated key.
        """
        from custom_components.rental_control.coordinator import (
            RentalControlCoordinator,
        )

        entry = self._make_rehydrate_entry()
        entry.add_to_hass(hass)

        fp = "cafebabe" + "0" * 56  # stand-in fingerprint (64 hex chars)
        mapping = self._make_occupied_mapping(fp, "Alice Guest", 10)
        store_data: dict[str, Any] = {
            "schema_version": 1,
            "entry_id": "rehydrate_entry",
            "lockname": "test_lock",
            "start_slot": 10,
            "max_slots": 2,
            "updated_at": "2026-01-01T00:00:00+00:00",
            "mappings": {fp: mapping},
            "blocked_slots": {},
        }

        coordinator = RentalControlCoordinator(hass, entry)
        assert coordinator.event_overrides is not None

        # Directly populate _slot_mappings to simulate a loaded Store.
        coordinator._slot_mappings = store_data
        coordinator.event_overrides.load_persisted_mappings(store_data["mappings"])

        pm = coordinator.event_overrides.persisted_mappings
        assert fp in pm
        assert pm[fp]["slot"] == 10
        assert pm[fp]["status"] == "occupied"

    # ------------------------------------------------------------------
    # T087-2: missing_count rehydrated from Store mapping
    # ------------------------------------------------------------------

    async def test_rehydration_preserves_missing_count(
        self, hass: "HomeAssistant"
    ) -> None:
        """T087-2: Rehydrated mapping preserves non-zero missing_count.

        When the Store recorded missing_count=2 for an absent reservation,
        that value must survive the load so the planner increments to 3 (not 1)
        on the next feed miss.
        """
        from custom_components.rental_control.coordinator import (
            RentalControlCoordinator,
        )

        entry = self._make_rehydrate_entry()
        entry.add_to_hass(hass)

        fp = "deadbeef" + "0" * 56
        mapping = self._make_occupied_mapping(fp, "Bob Guest", 10, missing_count=2)
        store_data: dict[str, Any] = {
            "schema_version": 1,
            "entry_id": "rehydrate_entry",
            "mappings": {fp: mapping},
            "blocked_slots": {},
        }

        coordinator = RentalControlCoordinator(hass, entry)
        assert coordinator.event_overrides is not None

        coordinator._slot_mappings = store_data
        coordinator.event_overrides.load_persisted_mappings(store_data["mappings"])

        pm = coordinator.event_overrides.persisted_mappings
        assert pm[fp]["missing_count"] == 2

    # ------------------------------------------------------------------
    # T087-3: pending_clear_slots rehydrated as pending fences
    # ------------------------------------------------------------------

    async def test_rehydration_restores_pending_clear_slots(
        self, hass: "HomeAssistant"
    ) -> None:
        """T087-3: Pending-clear mappings rehydrate as pending_clear_slots fences.

        A mapping with status=pending_clear must be restored as a pending
        fence so the coordinator re-attempts the clear rather than
        treating the slot as free.
        """
        from custom_components.rental_control.coordinator import (
            RentalControlCoordinator,
        )

        entry = self._make_rehydrate_entry()
        entry.add_to_hass(hass)

        fp = "fedcba98" + "0" * 56
        pending_mapping: dict = {
            **self._make_occupied_mapping(fp, "Carol Guest", 10),
            "status": "pending_clear",
            "operation_id": "op-abc123",
            "pending_clear_since": "2026-01-01T00:00:00+00:00",
        }
        store_data: dict[str, Any] = {
            "schema_version": 1,
            "entry_id": "rehydrate_entry",
            "mappings": {fp: pending_mapping},
            "blocked_slots": {},
        }

        coordinator = RentalControlCoordinator(hass, entry)
        assert coordinator.event_overrides is not None

        coordinator._slot_mappings = store_data
        coordinator.event_overrides.load_persisted_mappings(store_data["mappings"])

        # pending_clear_slots maps slot→operation_id
        pcs = coordinator.event_overrides.pending_clear_slots
        assert 10 in pcs
        assert pcs[10] == "op-abc123"

    # ------------------------------------------------------------------
    # T087-4: uid_aliases and booking_aliases rehydrated from identity dict
    # ------------------------------------------------------------------

    async def test_rehydration_preserves_identity_aliases(
        self, hass: "HomeAssistant"
    ) -> None:
        """T087-4: Identity aliases (uid_aliases, booking_aliases) survive load.

        These aliases are used by find_reservation_rematch for UID-churn
        and booking-platform alias matching after a restart.
        """
        from custom_components.rental_control.coordinator import (
            RentalControlCoordinator,
        )

        entry = self._make_rehydrate_entry()
        entry.add_to_hass(hass)

        fp = "aabbccdd" + "0" * 56
        mapping = self._make_occupied_mapping(
            fp,
            "Dave Guest",
            10,
            uid_aliases=["uid-dave-001", "uid-dave-002"],
            booking_aliases=["HMABCDE1234"],
        )
        store_data: dict[str, Any] = {
            "schema_version": 1,
            "entry_id": "rehydrate_entry",
            "mappings": {fp: mapping},
            "blocked_slots": {},
        }

        coordinator = RentalControlCoordinator(hass, entry)
        assert coordinator.event_overrides is not None

        coordinator._slot_mappings = store_data
        coordinator.event_overrides.load_persisted_mappings(store_data["mappings"])

        pm = coordinator.event_overrides.persisted_mappings
        assert pm[fp]["identity"]["uid_aliases"] == ["uid-dave-001", "uid-dave-002"]
        assert pm[fp]["identity"]["booking_aliases"] == ["HMABCDE1234"]

    # ------------------------------------------------------------------
    # T087-5: no-raw-PIN invariant maintained after rehydration
    # ------------------------------------------------------------------

    async def test_rehydration_maintains_no_raw_pin_invariant(
        self, hass: "HomeAssistant"
    ) -> None:
        """T087-5: Rehydrated last_observed_actual never contains raw PINs.

        The Store load path strips pin/code/slot_code keys from
        last_observed_actual.  After async_load_slot_store(), none of
        those keys should appear in the loaded mapping.
        """
        from unittest.mock import AsyncMock
        from unittest.mock import patch

        from custom_components.rental_control.coordinator import (
            RentalControlCoordinator,
        )

        entry = self._make_rehydrate_entry()
        entry.add_to_hass(hass)

        fp = "11223344" + "0" * 56
        mapping = self._make_occupied_mapping(fp, "Eve Guest", 10)
        # Simulate a poorly-written store that leaked PIN fields.
        mapping["last_observed_actual"]["pin"] = "9876"
        mapping["last_observed_actual"]["slot_code"] = "1234"

        store_data: dict[str, Any] = {
            "schema_version": 1,
            "entry_id": "rehydrate_entry",
            "mappings": {fp: mapping},
            "blocked_slots": {},
        }

        coordinator = RentalControlCoordinator(hass, entry)
        assert coordinator.event_overrides is not None

        # async_load_slot_store strips PIN fields during load.
        with patch("custom_components.rental_control.coordinator.Store") as MockStore:
            mock_store_instance = AsyncMock()
            mock_store_instance.async_load = AsyncMock(return_value=store_data)
            mock_store_instance.async_save = AsyncMock()
            MockStore.return_value = mock_store_instance
            coordinator._store = mock_store_instance
            await coordinator.async_load_slot_store()

        loaded = coordinator._slot_mappings
        for v in loaded.get("mappings", {}).values():
            last_obs = v.get("last_observed_actual", {})
            assert "pin" not in last_obs
            assert "code" not in last_obs
            assert "slot_code" not in last_obs


# ---------------------------------------------------------------------------
# T088: Coordinator updates _slot_mappings across refresh cycles
# ---------------------------------------------------------------------------


class TestCoordinatorPersistenceUpdate:
    """T088: _slot_mappings is updated after each refresh cycle so that
    missing_count increments (and resets) survive across restarts via the
    HA Store.
    """

    def _make_persist_entry(
        self,
        entry_id: str = "persist_entry",
        lockname: str = "test_lock",
        start_slot: int = 10,
        max_events: int = 2,
    ) -> "MockConfigEntry":
        """Build a minimal config entry for persistence update tests."""
        return MockConfigEntry(
            domain="rental_control",
            title="Persist Rental",
            version=10,
            unique_id=f"persist-{entry_id}",
            data={
                "name": "Persist Rental",
                "url": "https://example.com/calendar.ics",
                "timezone": "UTC",
                "checkin": "16:00",
                "checkout": "11:00",
                "start_slot": start_slot,
                "max_events": max_events,
                "days": 90,
                "verify_ssl": True,
                "ignore_non_reserved": False,
                "honor_event_times": False,
                "keymaster_entry_id": lockname,
                "code_buffer_before": 0,
                "code_buffer_after": 0,
            },
            entry_id=entry_id,
        )

    def _make_occupied_mapping(
        self,
        identity_key: str,
        slot_name: str,
        slot: int,
        *,
        missing_count: int = 0,
        start_state: str | None = "2026-07-01T14:00:00+00:00",
        end_state: str | None = "2026-07-08T11:00:00+00:00",
    ) -> dict:
        """Build a v1 occupied slot mapping dict for persistence update tests."""
        return {
            "slot": slot,
            "status": "occupied",
            "operation_id": None,
            "operation_kind": None,
            "identity": {
                "identity_key": identity_key,
                "summary": slot_name,
                "slot_name": slot_name,
                "uid_aliases": [],
                "booking_aliases": [],
            },
            "missing_count": missing_count,
            "pending_set_since": None,
            "pending_clear_since": None,
            "fingerprint_history": [],
            "updated_at": "2026-01-01T00:00:00+00:00",
            "last_observed_actual": {
                "slot": slot,
                "classification": "occupied",
                "name_state": slot_name,
                "has_code": True,
                "start_state": start_state,
                "end_state": end_state,
                "use_date_range": True,
                "enabled": True,
            },
        }

    async def test_no_wipe_when_adopted_slots_fail_rematch(
        self, hass: "HomeAssistant"
    ) -> None:
        """3.5.0 repro: unrematched adopted occupied slots are preserved."""
        from custom_components.rental_control.reconciliation import ActionKind
        from custom_components.rental_control.reconciliation import compute_desired_plan
        from custom_components.rental_control.util import OperationResult

        entry = self._make_persist_entry(max_events=3)
        entry.add_to_hass(hass)
        coordinator = RentalControlCoordinator(hass, entry)
        coordinator.event_prefix = "RC"
        coordinator.trim_names = True
        coordinator.max_name_length = 16
        assert coordinator.event_overrides is not None

        adopted_names = {
            10: "RC Alexander",
            11: "RC Repeat Guest",
            12: "RC Repeat Guest",
        }
        for slot, name in adopted_names.items():
            hass.states.async_set(f"text.test_lock_code_slot_{slot}_name", name)
            hass.states.async_set(f"text.test_lock_code_slot_{slot}_pin", "1234")

        await coordinator.async_adopt_keymaster_slots()

        events = []
        for summary, start_day in (
            ("RC Alexander Very Long Guest Name", 1),
            ("RC Repeat Guest Family A", 10),
            ("RC Repeat Guest Family B", 20),
        ):
            event = MagicMock()
            event.summary = summary
            event.description = ""
            event.start = datetime(2026, 8, start_day, 16, 0, tzinfo=dt_util.UTC)
            event.end = event.start + timedelta(days=4)
            event.uid = None
            events.append(event)

        reservations = coordinator._build_reservations(events)
        observed = coordinator._observe_managed_slots()
        plan = compute_desired_plan(
            reservations,
            observed,
            max_events=coordinator.max_events,
            plan_id="adopted-no-wipe",
            generated_at=datetime(2026, 8, 1, tzinfo=dt_util.UTC),
        )

        assert not [
            action
            for action in plan.actions
            if action.kind is ActionKind.CLEAR and action.slot in adopted_names
        ]

        with patch(
            "custom_components.rental_control.event_overrides.async_fire_clear_code",
            new_callable=AsyncMock,
            return_value=OperationResult(kind="clear", slot=10, confirmed=True),
        ) as mock_clear:
            await coordinator.event_overrides.async_apply_plan(
                coordinator, plan, {res.identity_key: res for res in reservations}
            )

        mock_clear.assert_not_called()

    def test_pending_clear_self_heals_when_physically_empty(
        self, hass: "HomeAssistant"
    ) -> None:
        """3.5.0 repro: physically empty pending-clear slots become free."""
        from custom_components.rental_control.reconciliation import Reservation
        from custom_components.rental_control.reconciliation import SlotStatus
        from custom_components.rental_control.reconciliation import compute_desired_plan

        entry = self._make_persist_entry(max_events=2)
        entry.add_to_hass(hass)
        start = datetime(2026, 9, 1, 16, 0, tzinfo=dt_util.UTC)
        end = datetime(2026, 9, 5, 11, 0, tzinfo=dt_util.UTC)
        store_mappings = {}
        for slot in (10, 11):
            key = f"wedged-{slot}"
            mapping = self._make_occupied_mapping(key, f"Old Guest {slot}", slot)
            mapping["status"] = "pending_clear"
            mapping["operation_id"] = f"clear-token-{slot}"
            mapping["operation_kind"] = "clear"
            mapping["pending_clear_since"] = "2026-08-01T00:00:00+00:00"
            store_mappings[key] = mapping
            hass.states.async_set(f"text.test_lock_code_slot_{slot}_name", "")
            hass.states.async_set(f"text.test_lock_code_slot_{slot}_pin", "")

        reservations = [
            Reservation(
                identity_key=f"new-{slot}",
                start=start + timedelta(days=index * 7),
                end=end + timedelta(days=index * 7),
                buffered_start=start + timedelta(days=index * 7),
                buffered_end=end + timedelta(days=index * 7),
                summary=f"New Guest {slot}",
                slot_name=f"New Guest {slot}",
                display_slot_name=f"New Guest {slot}",
                slot_code="1234",
            )
            for index, slot in enumerate((10, 11))
        ]

        for _restart in range(2):
            coordinator = RentalControlCoordinator(hass, entry)
            coordinator._slot_mappings = {
                "schema_version": 1,
                "entry_id": entry.entry_id,
                "mappings": {key: dict(value) for key, value in store_mappings.items()},
                "blocked_slots": {},
            }
            assert coordinator.event_overrides is not None
            coordinator.event_overrides.load_persisted_mappings(
                coordinator._slot_mappings["mappings"]
            )

            observed = coordinator._observe_managed_slots()
            assert all(slot.status is SlotStatus.FREE for slot in observed)

            plan = compute_desired_plan(
                reservations,
                observed,
                max_events=2,
                plan_id=f"pending-clear-self-heal-{_restart}",
                generated_at=start,
            )

            assert plan.overflow == {}
            assert set(plan.selected) == {"new-10", "new-11"}
            assert sorted(plan.selected.values()) == [10, 11]

    async def test_wedge_self_heals_when_slots_are_unknown(
        self, hass: "HomeAssistant"
    ) -> None:
        """Keymaster Null reset states self-heal across coordinator restarts."""
        from custom_components.rental_control.reconciliation import SlotStatus
        from custom_components.rental_control.reconciliation import compute_desired_plan
        from custom_components.rental_control.util import OperationResult

        entry = self._make_persist_entry(max_events=2)
        entry.add_to_hass(hass)
        start = datetime(2026, 9, 1, 16, 0, tzinfo=dt_util.UTC)
        end = datetime(2026, 9, 5, 11, 0, tzinfo=dt_util.UTC)
        store_mappings = {}
        for slot in (10, 11):
            key = f"wedged-unknown-{slot}"
            mapping = self._make_occupied_mapping(key, f"Old Guest {slot}", slot)
            mapping["status"] = "pending_clear"
            mapping["operation_id"] = f"clear-token-{slot}"
            mapping["operation_kind"] = "clear"
            mapping["pending_clear_since"] = "2026-08-01T00:00:00+00:00"
            store_mappings[key] = mapping
            hass.states.async_set(f"text.test_lock_code_slot_{slot}_name", "unknown")
            hass.states.async_set(f"text.test_lock_code_slot_{slot}_pin", "unknown")

        events = []
        for index, (slot, guest) in enumerate(
            ((10, "Recovered Unknown A"), (11, "Recovered Unknown B"))
        ):
            event = MagicMock()
            event.summary = guest
            event.description = ""
            event.start = start + timedelta(days=index * 7)
            event.end = end + timedelta(days=index * 7)
            event.uid = f"unknown-uid-{slot}"
            events.append(event)

        for restart in range(2):
            coordinator = RentalControlCoordinator(hass, entry)
            coordinator._slot_mappings = {
                "schema_version": 1,
                "entry_id": entry.entry_id,
                "mappings": {key: dict(value) for key, value in store_mappings.items()},
                "blocked_slots": {},
            }
            assert coordinator.event_overrides is not None
            coordinator.event_overrides.load_persisted_mappings(
                coordinator._slot_mappings["mappings"]
            )
            reservations = coordinator._build_reservations(events)
            observed = coordinator._observe_managed_slots()

            assert all(slot.status is SlotStatus.FREE for slot in observed)
            assert coordinator.event_overrides.pending_clear_slots == {}

            plan = compute_desired_plan(
                reservations,
                observed,
                max_events=2,
                plan_id=f"unknown-reset-recovery-{restart}",
                generated_at=start,
            )
            assert plan.overflow == {}
            assert sorted(plan.selected.values()) == [10, 11]

            with patch(
                "custom_components.rental_control.event_overrides.async_fire_set_code",
                new_callable=AsyncMock,
                return_value=OperationResult(kind="set", slot=10, confirmed=True),
            ) as mock_set:
                await coordinator.event_overrides.async_apply_plan(
                    coordinator, plan, {res.identity_key: res for res in reservations}
                )

            assert mock_set.call_count == 2

    def test_wedge_self_heals_when_unknown_state_mixed_case(
        self, hass: "HomeAssistant"
    ) -> None:
        """Mixed-case Keymaster unknown reset states self-heal to free."""
        from custom_components.rental_control.reconciliation import Reservation
        from custom_components.rental_control.reconciliation import SlotStatus
        from custom_components.rental_control.reconciliation import compute_desired_plan

        entry = self._make_persist_entry(max_events=1)
        entry.add_to_hass(hass)
        coordinator = RentalControlCoordinator(hass, entry)
        start = datetime(2026, 9, 1, 16, 0, tzinfo=dt_util.UTC)
        end = datetime(2026, 9, 5, 11, 0, tzinfo=dt_util.UTC)
        mapping = self._make_occupied_mapping(
            "wedged-mixed-case-unknown", "Old Guest", 10
        )
        mapping["status"] = "pending_clear"
        mapping["operation_id"] = "clear-token"
        mapping["operation_kind"] = "clear"
        mapping["pending_clear_since"] = "2026-08-01T00:00:00+00:00"
        coordinator._slot_mappings = {
            "schema_version": 1,
            "entry_id": entry.entry_id,
            "mappings": {"wedged-mixed-case-unknown": mapping},
            "blocked_slots": {},
        }
        hass.states.async_set("text.test_lock_code_slot_10_name", "Unknown")
        hass.states.async_set("text.test_lock_code_slot_10_pin", "UNKNOWN")
        assert coordinator.event_overrides is not None
        coordinator.event_overrides.load_persisted_mappings(
            coordinator._slot_mappings["mappings"]
        )
        reservation = Reservation(
            identity_key="new-mixed-case-unknown",
            start=start,
            end=end,
            buffered_start=start,
            buffered_end=end,
            summary="New Guest",
            slot_name="New Guest",
            display_slot_name="New Guest",
            slot_code="1234",
        )

        observed = coordinator._observe_managed_slots()
        slot10 = next(slot for slot in observed if slot.slot == 10)
        plan = compute_desired_plan(
            [reservation],
            observed,
            max_events=1,
            plan_id="mixed-case-unknown-reset",
            generated_at=start,
        )

        assert slot10.status is SlotStatus.FREE
        assert coordinator.event_overrides.pending_clear_slots == {}
        assert plan.overflow == {}
        assert plan.selected == {"new-mixed-case-unknown": 10}

    async def test_wedge_self_heals_with_mixed_unknown_and_empty(
        self, hass: "HomeAssistant"
    ) -> None:
        """Mixed Keymaster Null and blank reset states all become free."""
        from custom_components.rental_control.reconciliation import SlotStatus
        from custom_components.rental_control.reconciliation import compute_desired_plan
        from custom_components.rental_control.util import OperationResult

        entry = self._make_persist_entry(max_events=3)
        entry.add_to_hass(hass)
        coordinator = RentalControlCoordinator(hass, entry)
        start = datetime(2026, 9, 1, 16, 0, tzinfo=dt_util.UTC)
        end = datetime(2026, 9, 5, 11, 0, tzinfo=dt_util.UTC)
        coordinator._slot_mappings = {
            "schema_version": 1,
            "entry_id": entry.entry_id,
            "mappings": {},
            "blocked_slots": {},
        }
        reset_states = {
            10: ("unknown", "unknown"),
            11: ("", ""),
            12: ("unknown", ""),
        }
        for slot, (name_state, pin_state) in reset_states.items():
            key = f"wedged-mixed-{slot}"
            mapping = self._make_occupied_mapping(key, f"Old Guest {slot}", slot)
            mapping["status"] = "pending_clear"
            mapping["operation_id"] = f"clear-token-{slot}"
            mapping["operation_kind"] = "clear"
            mapping["pending_clear_since"] = "2026-08-01T00:00:00+00:00"
            coordinator._slot_mappings["mappings"][key] = mapping
            hass.states.async_set(f"text.test_lock_code_slot_{slot}_name", name_state)
            hass.states.async_set(f"text.test_lock_code_slot_{slot}_pin", pin_state)
        assert coordinator.event_overrides is not None
        coordinator.event_overrides.load_persisted_mappings(
            coordinator._slot_mappings["mappings"]
        )

        events = []
        for index, slot in enumerate(reset_states):
            event = MagicMock()
            event.summary = f"Mixed Reset Guest {slot}"
            event.description = ""
            event.start = start + timedelta(days=index * 7)
            event.end = end + timedelta(days=index * 7)
            event.uid = f"mixed-reset-{slot}"
            events.append(event)

        reservations = coordinator._build_reservations(events)
        observed = coordinator._observe_managed_slots()

        assert all(slot.status is SlotStatus.FREE for slot in observed)
        assert coordinator.event_overrides.pending_clear_slots == {}

        plan = compute_desired_plan(
            reservations,
            observed,
            max_events=3,
            plan_id="mixed-reset-recovery",
            generated_at=start,
        )
        assert plan.overflow == {}
        assert sorted(plan.selected.values()) == [10, 11, 12]

        with patch(
            "custom_components.rental_control.event_overrides.async_fire_set_code",
            new_callable=AsyncMock,
            return_value=OperationResult(kind="set", slot=10, confirmed=True),
        ) as mock_set:
            await coordinator.event_overrides.async_apply_plan(
                coordinator, plan, {res.identity_key: res for res in reservations}
            )

        assert mock_set.call_count == 3

    def test_slot_with_unknown_pin_not_treated_as_occupied(
        self, hass: "HomeAssistant"
    ) -> None:
        """A Null-reset slot is free, not occupied, when PIN is unknown."""
        from custom_components.rental_control.reconciliation import Reservation
        from custom_components.rental_control.reconciliation import SlotStatus
        from custom_components.rental_control.reconciliation import compute_desired_plan

        entry = self._make_persist_entry(max_events=1)
        entry.add_to_hass(hass)
        coordinator = RentalControlCoordinator(hass, entry)
        start = datetime(2026, 9, 1, 16, 0, tzinfo=dt_util.UTC)
        end = datetime(2026, 9, 5, 11, 0, tzinfo=dt_util.UTC)
        hass.states.async_set("text.test_lock_code_slot_10_name", "unknown")
        hass.states.async_set("text.test_lock_code_slot_10_pin", "unknown")

        observed = coordinator._observe_managed_slots()
        slot10 = next(slot for slot in observed if slot.slot == 10)

        assert slot10.status is SlotStatus.FREE
        assert slot10.actual_code_present is False
        assert coordinator.event_overrides is not None
        actual_state = coordinator.event_overrides.get_actual_state(10)
        assert actual_state is not None
        assert actual_state["has_code"] is False

        reservation = Reservation(
            identity_key="new-unknown-pin",
            start=start,
            end=end,
            buffered_start=start,
            buffered_end=end,
            summary="New Guest",
            slot_name="New Guest",
            display_slot_name="New Guest",
            slot_code="1234",
        )
        plan = compute_desired_plan(
            [reservation],
            observed,
            max_events=1,
            plan_id="unknown-pin-free",
            generated_at=start,
        )

        assert plan.overflow == {}
        assert plan.selected == {"new-unknown-pin": 10}

    def test_slot_with_real_pin_stays_fenced(self, hass: "HomeAssistant") -> None:
        """A pending-clear slot with a real PIN remains fenced."""
        from custom_components.rental_control.reconciliation import Reservation
        from custom_components.rental_control.reconciliation import SlotStatus
        from custom_components.rental_control.reconciliation import compute_desired_plan

        entry = self._make_persist_entry(max_events=1)
        entry.add_to_hass(hass)
        coordinator = RentalControlCoordinator(hass, entry)
        start = datetime(2026, 9, 1, 16, 0, tzinfo=dt_util.UTC)
        end = datetime(2026, 9, 5, 11, 0, tzinfo=dt_util.UTC)
        mapping = self._make_occupied_mapping("real-pin", "Old Guest", 10)
        mapping["status"] = "pending_clear"
        mapping["operation_id"] = "clear-token"
        mapping["operation_kind"] = "clear"
        coordinator._slot_mappings = {
            "schema_version": 1,
            "entry_id": entry.entry_id,
            "mappings": {"real-pin": mapping},
            "blocked_slots": {},
        }
        hass.states.async_set("text.test_lock_code_slot_10_name", "unknown")
        hass.states.async_set("text.test_lock_code_slot_10_pin", "9876")
        assert coordinator.event_overrides is not None
        coordinator.event_overrides.load_persisted_mappings(
            coordinator._slot_mappings["mappings"]
        )

        observed = coordinator._observe_managed_slots()
        slot10 = next(slot for slot in observed if slot.slot == 10)

        assert slot10.status is SlotStatus.PENDING_CLEAR
        assert slot10.actual_code_present is True

        reservation = Reservation(
            identity_key="new-real-pin",
            start=start,
            end=end,
            buffered_start=start,
            buffered_end=end,
            summary="New Guest",
            slot_name="New Guest",
            display_slot_name="New Guest",
            slot_code="1234",
        )
        plan = compute_desired_plan(
            [reservation],
            observed,
            max_events=1,
            plan_id="real-pin-fenced",
            generated_at=start,
        )

        assert plan.selected == {}
        assert plan.overflow == {"new-real-pin": "no_free_slot"}

    async def test_adoption_fences_unnamed_real_pin(
        self, hass: "HomeAssistant"
    ) -> None:
        """Adoption records a real PIN with cleared name as occupied."""
        entry = self._make_persist_entry(max_events=1)
        entry.add_to_hass(hass)
        coordinator = RentalControlCoordinator(hass, entry)
        coordinator._slot_mappings = {}
        hass.states.async_set("text.test_lock_code_slot_10_name", "unknown")
        hass.states.async_set("text.test_lock_code_slot_10_pin", "9876")

        await coordinator.async_adopt_keymaster_slots()

        mappings = coordinator._slot_mappings["mappings"]
        mapping = mappings[f"adopted.{entry.entry_id}.slot10"]
        assert mapping["status"] == "occupied"
        assert mapping["identity"]["slot_name"] == "Adopted Slot 10"
        assert mapping["last_observed_actual"]["has_code"] is True

    def test_unavailable_slot_not_freed(self, hass: "HomeAssistant") -> None:
        """Unavailable pending-clear slots stay fenced until reset states load."""
        from custom_components.rental_control.reconciliation import Reservation
        from custom_components.rental_control.reconciliation import SlotStatus
        from custom_components.rental_control.reconciliation import compute_desired_plan

        entry = self._make_persist_entry(max_events=1)
        entry.add_to_hass(hass)
        coordinator = RentalControlCoordinator(hass, entry)
        start = datetime(2026, 9, 1, 16, 0, tzinfo=dt_util.UTC)
        end = datetime(2026, 9, 5, 11, 0, tzinfo=dt_util.UTC)
        mapping = self._make_occupied_mapping("unavailable-slot", "Old Guest", 10)
        mapping["status"] = "pending_clear"
        mapping["operation_id"] = "clear-token"
        mapping["operation_kind"] = "clear"
        coordinator._slot_mappings = {
            "schema_version": 1,
            "entry_id": entry.entry_id,
            "mappings": {"unavailable-slot": mapping},
            "blocked_slots": {},
        }
        assert coordinator.event_overrides is not None
        coordinator.event_overrides.load_persisted_mappings(
            coordinator._slot_mappings["mappings"]
        )
        reservation = Reservation(
            identity_key="new-after-unavailable",
            start=start,
            end=end,
            buffered_start=start,
            buffered_end=end,
            summary="New Guest",
            slot_name="New Guest",
            display_slot_name="New Guest",
            slot_code="1234",
        )

        hass.states.async_set("text.test_lock_code_slot_10_name", "Unavailable")
        hass.states.async_set("text.test_lock_code_slot_10_pin", "UNAVAILABLE")
        observed = coordinator._observe_managed_slots()
        slot10 = next(slot for slot in observed if slot.slot == 10)
        assert slot10.status is SlotStatus.UNKNOWN
        plan = compute_desired_plan(
            [reservation],
            observed,
            max_events=1,
            plan_id="unavailable-stays-fenced",
            generated_at=start,
        )
        assert plan.selected == {}
        assert plan.overflow == {"new-after-unavailable": "no_free_slot"}
        from custom_components.rental_control.reconciliation import ActionKind

        assert plan.slots[10].action is ActionKind.BLOCKED

        hass.states.async_set("text.test_lock_code_slot_10_name", "Unknown")
        hass.states.async_set("text.test_lock_code_slot_10_pin", "")
        observed = coordinator._observe_managed_slots()
        slot10 = next(slot for slot in observed if slot.slot == 10)
        assert slot10.status is SlotStatus.FREE
        plan = compute_desired_plan(
            [reservation],
            observed,
            max_events=1,
            plan_id="unavailable-recovers",
            generated_at=start,
        )
        assert plan.overflow == {}
        assert plan.selected == {"new-after-unavailable": 10}

    def test_pending_clear_stays_fenced_when_state_unreadable(
        self, hass: "HomeAssistant"
    ) -> None:
        """Pending-clear slots are not freed from unavailable states."""
        from custom_components.rental_control.reconciliation import ActionKind
        from custom_components.rental_control.reconciliation import Reservation
        from custom_components.rental_control.reconciliation import SlotStatus
        from custom_components.rental_control.reconciliation import compute_desired_plan

        entry = self._make_persist_entry(max_events=1)
        entry.add_to_hass(hass)
        coordinator = RentalControlCoordinator(hass, entry)
        start = datetime(2026, 9, 1, 16, 0, tzinfo=dt_util.UTC)
        end = datetime(2026, 9, 5, 11, 0, tzinfo=dt_util.UTC)
        mapping = self._make_occupied_mapping("wedged-unknown", "Old Guest", 10)
        mapping["status"] = "pending_clear"
        mapping["operation_id"] = "clear-token"
        mapping["operation_kind"] = "clear"
        coordinator._slot_mappings = {
            "schema_version": 1,
            "entry_id": entry.entry_id,
            "mappings": {"wedged-unknown": mapping},
            "blocked_slots": {},
        }
        hass.states.async_set("text.test_lock_code_slot_10_name", "Unknown")
        hass.states.async_set("text.test_lock_code_slot_10_pin", "Unavailable")
        assert coordinator.event_overrides is not None
        coordinator.event_overrides.load_persisted_mappings(
            coordinator._slot_mappings["mappings"]
        )

        observed = coordinator._observe_managed_slots()
        slot10 = next(slot for slot in observed if slot.slot == 10)
        assert slot10.status is SlotStatus.UNKNOWN

        reservation = Reservation(
            identity_key="new-unreadable",
            start=start,
            end=end,
            buffered_start=start,
            buffered_end=end,
            summary="New Guest",
            slot_name="New Guest",
            display_slot_name="New Guest",
            slot_code="1234",
        )
        plan = compute_desired_plan(
            [reservation],
            observed,
            max_events=1,
            plan_id="pending-clear-unreadable",
            generated_at=start,
        )

        assert plan.selected == {}
        assert plan.overflow == {"new-unreadable": "no_free_slot"}
        assert plan.slots[10].action is ActionKind.BLOCKED

    def test_adoption_rematch_uses_buffered_observed_dates(
        self, hass: "HomeAssistant"
    ) -> None:
        """Ambiguous trimmed adopted names use observed buffered dates."""
        from custom_components.rental_control.reconciliation import (
            make_reservation_fingerprint,
        )

        entry = self._make_persist_entry(max_events=2)
        entry.add_to_hass(hass)
        coordinator = RentalControlCoordinator(hass, entry)
        coordinator.event_prefix = "RC"
        coordinator.trim_names = True
        coordinator.max_name_length = 16
        coordinator.code_buffer_before = 60
        coordinator.code_buffer_after = 120
        start_a = datetime(2026, 8, 1, 16, 0, tzinfo=dt_util.UTC)
        end_a = datetime(2026, 8, 5, 11, 0, tzinfo=dt_util.UTC)
        start_b = datetime(2026, 8, 10, 16, 0, tzinfo=dt_util.UTC)
        end_b = datetime(2026, 8, 14, 11, 0, tzinfo=dt_util.UTC)
        adopted_a = f"adopted.{entry.entry_id}.slot10"
        adopted_b = f"adopted.{entry.entry_id}.slot11"
        mapping_a = self._make_occupied_mapping(adopted_a, "Repeat Guest", 10)
        mapping_b = self._make_occupied_mapping(adopted_b, "Repeat Guest", 11)
        mapping_a["last_observed_actual"]["name_state"] = "RC Repeat Guest"
        mapping_b["last_observed_actual"]["name_state"] = "RC Repeat Guest"
        mapping_a["last_observed_actual"]["start_state"] = (
            start_a - timedelta(minutes=60)
        ).isoformat()
        mapping_a["last_observed_actual"]["end_state"] = (
            end_a + timedelta(minutes=120)
        ).isoformat()
        mapping_b["last_observed_actual"]["start_state"] = (
            start_b - timedelta(minutes=60)
        ).isoformat()
        mapping_b["last_observed_actual"]["end_state"] = (
            end_b + timedelta(minutes=120)
        ).isoformat()
        coordinator._slot_mappings = {
            "schema_version": 1,
            "entry_id": entry.entry_id,
            "mappings": {adopted_a: mapping_a, adopted_b: mapping_b},
            "blocked_slots": {},
        }

        events = []
        for summary, start, end in (
            ("RC Repeat Guest Family A", start_a, end_a),
            ("RC Repeat Guest Family B", start_b, end_b),
        ):
            event = MagicMock()
            event.summary = summary
            event.description = ""
            event.start = start
            event.end = end
            event.uid = None
            events.append(event)

        coordinator._build_reservations(events)

        expected_a = make_reservation_fingerprint(
            entry.entry_id, "Repeat Guest Family A", start_a, end_a
        )
        expected_b = make_reservation_fingerprint(
            entry.entry_id, "Repeat Guest Family B", start_b, end_b
        )
        mappings = coordinator._slot_mappings["mappings"]
        assert mappings[expected_a]["slot"] == 10
        assert mappings[expected_b]["slot"] == 11
        assert adopted_a not in mappings
        assert adopted_b not in mappings

    def test_display_name_trims_full_name_when_prefix_consumes_limit(self) -> None:
        """Display-name formatting avoids negative guest-name trim lengths."""
        from custom_components.rental_control.coordinator import (
            _format_display_slot_name,
        )

        display_name = _format_display_slot_name(
            "Guest Name",
            "VeryLongPrefix ",
            trim_names=True,
            max_name_length=8,
        )

        assert display_name == "VeryLong"

    async def test_wedged_350_store_recovers_on_load(
        self, hass: "HomeAssistant"
    ) -> None:
        """3.5.0 repro: a wedged Store self-heals and programs codes."""
        from custom_components.rental_control.reconciliation import compute_desired_plan
        from custom_components.rental_control.util import OperationResult

        entry = self._make_persist_entry(max_events=2)
        entry.add_to_hass(hass)
        coordinator = RentalControlCoordinator(hass, entry)
        start = datetime(2026, 10, 1, 16, 0, tzinfo=dt_util.UTC)
        end = datetime(2026, 10, 5, 11, 0, tzinfo=dt_util.UTC)
        coordinator._slot_mappings = {
            "schema_version": 1,
            "entry_id": entry.entry_id,
            "mappings": {},
            "blocked_slots": {},
        }
        for slot in (10, 11):
            key = f"adopted.{entry.entry_id}.slot{slot}"
            mapping = self._make_occupied_mapping(key, f"Wedged Guest {slot}", slot)
            mapping["status"] = "pending_clear"
            mapping["operation_id"] = f"clear-token-{slot}"
            mapping["operation_kind"] = "clear"
            mapping["pending_clear_since"] = "2026-08-01T00:00:00+00:00"
            coordinator._slot_mappings["mappings"][key] = mapping
            hass.states.async_set(f"text.test_lock_code_slot_{slot}_name", "")
            hass.states.async_set(f"text.test_lock_code_slot_{slot}_pin", "")
        assert coordinator.event_overrides is not None
        coordinator.event_overrides.load_persisted_mappings(
            coordinator._slot_mappings["mappings"]
        )

        events = []
        for slot, guest in ((10, "Recovered Guest A"), (11, "Recovered Guest B")):
            event = MagicMock()
            event.summary = guest
            event.description = ""
            event.start = start + timedelta(days=(slot - 10) * 7)
            event.end = end + timedelta(days=(slot - 10) * 7)
            event.uid = f"uid-{slot}"
            events.append(event)

        reservations = coordinator._build_reservations(events)
        observed = coordinator._observe_managed_slots()
        plan = compute_desired_plan(
            reservations,
            observed,
            max_events=2,
            plan_id="wedged-store-recovers",
            generated_at=start,
        )

        with patch(
            "custom_components.rental_control.event_overrides.async_fire_set_code",
            new_callable=AsyncMock,
            return_value=OperationResult(kind="set", slot=10, confirmed=True),
        ) as mock_set:
            await coordinator.event_overrides.async_apply_plan(
                coordinator, plan, {res.identity_key: res for res in reservations}
            )

        assert plan.overflow == {}
        assert sorted(plan.selected.values()) == [10, 11]
        assert mock_set.call_count == 2

    # ------------------------------------------------------------------
    # T088-1: missing_count incremented for absent occupied slot after cycle
    # ------------------------------------------------------------------

    async def test_missing_count_incremented_for_absent_occupied_slot(
        self, hass: "HomeAssistant"
    ) -> None:
        """T088-1: _slot_mappings missing_count incremented when reservation absent.

        When an occupied persisted mapping is not present in the current
        calendar feed, the coordinator's _build_ghost_reservations()
        increments missing_count in _slot_mappings so that the new value
        is persisted by async_save_slot_store at cycle end.
        """
        from unittest.mock import AsyncMock
        from unittest.mock import MagicMock
        from unittest.mock import patch

        from custom_components.rental_control.coordinator import (
            RentalControlCoordinator,
        )

        entry = self._make_persist_entry()
        entry.add_to_hass(hass)

        fp = "aabbccddeeff0011" + "0" * 48
        mapping = self._make_occupied_mapping(fp, "Alice Ghost", 10, missing_count=0)
        store_data: dict[str, Any] = {
            "schema_version": 1,
            "entry_id": "persist_entry",
            "mappings": {fp: mapping},
            "blocked_slots": {},
        }

        frozen_time = datetime(2026, 7, 15, 12, 0, 0, tzinfo=dt_util.UTC)
        coordinator = RentalControlCoordinator(hass, entry)
        coordinator._slot_mappings = store_data
        assert coordinator.event_overrides is not None
        eo = coordinator.event_overrides

        # Empty calendar: Alice Ghost absent from feed
        empty_ics = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//EN\r\nEND:VCALENDAR\r\n"
        )

        mock_plan = MagicMock()
        mock_plan.plan_id = "t088-test"
        mock_plan.selected = {}
        mock_plan.overflow = {}
        mock_plan.actions = []
        mock_plan.diagnostics = {}
        mock_plan.validate.return_value = []

        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=frozen_time),
            patch.object(dt_util, "start_of_local_day", return_value=frozen_time),
            patch(
                "custom_components.rental_control.coordinator.compute_desired_plan",
                return_value=mock_plan,
            ),
            patch.object(eo, "async_apply_plan", new=AsyncMock(return_value=[])),
            patch.object(coordinator, "async_save_slot_store", new_callable=AsyncMock),
        ):
            mock_session.get("https://example.com/calendar.ics", body=empty_ics)
            await coordinator._async_update_data()

        # missing_count in _slot_mappings must have been incremented to 1
        updated_mc = coordinator._slot_mappings["mappings"][fp]["missing_count"]
        assert updated_mc == 1, (
            f"Expected missing_count=1 after one missed cycle, got {updated_mc}"
        )

    def test_pending_set_with_dates_uses_missing_lifecycle(
        self, hass: "HomeAssistant"
    ) -> None:
        """T088-11: Vanished pending SET with dates fences then clears."""
        from custom_components.rental_control.reconciliation import ActionKind
        from custom_components.rental_control.reconciliation import ManagedSlot
        from custom_components.rental_control.reconciliation import Reservation
        from custom_components.rental_control.reconciliation import SlotStatus
        from custom_components.rental_control.reconciliation import compute_desired_plan

        entry = self._make_persist_entry()
        entry.add_to_hass(hass)
        coordinator = RentalControlCoordinator(hass, entry)
        pending_key = "pending-dates." + "a" * 50
        mapping = self._make_occupied_mapping(
            pending_key,
            "Pending Dates",
            10,
            missing_count=0,
        )
        mapping["status"] = "pending_set"
        mapping["operation_id"] = "set-token"
        mapping["operation_kind"] = "set"
        mapping["pending_set_since"] = "2026-08-01T00:00:00+00:00"
        coordinator._slot_mappings = {
            "schema_version": 1,
            "entry_id": entry.entry_id,
            "mappings": {pending_key: mapping},
            "blocked_slots": {},
        }

        reservations = coordinator._build_reservations([])

        assert mapping["missing_count"] == 1
        ghost = next(res for res in reservations if res.identity_key == pending_key)
        assert ghost.missing_count == 1

        start = datetime(2026, 9, 1, 16, 0, tzinfo=dt_util.UTC)
        end = datetime(2026, 9, 5, 11, 0, tzinfo=dt_util.UTC)
        new_key = "new-reservation." + "b" * 48
        new_reservation = Reservation(
            identity_key=new_key,
            start=start,
            end=end,
            buffered_start=start,
            buffered_end=end,
            summary="New Guest",
            slot_name="New Guest",
            display_slot_name="New Guest",
            slot_code="1234",
        )
        plan = compute_desired_plan(
            reservations + [new_reservation],
            [
                ManagedSlot(
                    slot=10,
                    managed=True,
                    status=SlotStatus.OCCUPIED,
                    persisted_identity_key=pending_key,
                ),
                ManagedSlot(slot=11, managed=True, status=SlotStatus.FREE),
            ],
            max_events=2,
            plan_id="pending-dates",
            generated_at=start,
        )

        assert plan.selected[pending_key] == 10
        assert plan.selected[new_key] == 11

        mapping["missing_count"] = 2
        mapping["status"] = "pending_set"
        reservations = coordinator._build_reservations([])

        assert pending_key not in {res.identity_key for res in reservations}
        assert mapping["missing_count"] == 3
        assert mapping["status"] == "pending_clear"
        assert mapping["operation_id"] is None
        assert mapping["operation_kind"] == "clear"
        assert mapping["pending_set_since"] is None

        assert coordinator.event_overrides is not None
        coordinator.event_overrides.load_persisted_mappings(
            coordinator._slot_mappings["mappings"]
        )
        observed = [
            ManagedSlot(
                slot=10,
                managed=True,
                status=SlotStatus.PENDING_CLEAR,
                persisted_identity_key=pending_key,
            )
        ]
        clear_plan = compute_desired_plan(
            [],
            observed,
            max_events=2,
            plan_id="pending-dates-clear",
            generated_at=start,
        )

        assert any(
            action.kind is ActionKind.RETRY_CLEAR and action.slot == 10
            for action in clear_plan.actions
        )

    def test_pending_set_without_dates_blocks_then_clears(
        self, hass: "HomeAssistant"
    ) -> None:
        """T088-12: Vanished pending SET without dates cannot orphan."""
        from custom_components.rental_control.reconciliation import ActionKind
        from custom_components.rental_control.reconciliation import Reservation
        from custom_components.rental_control.reconciliation import SlotStatus
        from custom_components.rental_control.reconciliation import compute_desired_plan

        entry = self._make_persist_entry()
        entry.add_to_hass(hass)
        coordinator = RentalControlCoordinator(hass, entry)
        pending_key = "pending-nodates." + "c" * 49
        mapping = self._make_occupied_mapping(
            pending_key,
            "Pending No Dates",
            10,
            missing_count=0,
            start_state=None,
            end_state=None,
        )
        mapping["status"] = "pending_set"
        mapping["operation_id"] = "set-token"
        mapping["operation_kind"] = "set"
        mapping["pending_set_since"] = "2026-08-01T00:00:00+00:00"
        coordinator._slot_mappings = {
            "schema_version": 1,
            "entry_id": entry.entry_id,
            "mappings": {pending_key: mapping},
            "blocked_slots": {},
        }

        reservations = coordinator._build_reservations([])

        assert reservations == []
        assert mapping["missing_count"] == 1
        assert mapping["status"] == "pending_set"

        hass.states.async_set("text.test_lock_code_slot_10_name", "")
        hass.states.async_set("text.test_lock_code_slot_10_pin", "")
        hass.states.async_set("text.test_lock_code_slot_11_name", "")
        hass.states.async_set("text.test_lock_code_slot_11_pin", "")
        assert coordinator.event_overrides is not None
        coordinator.event_overrides.load_persisted_mappings(
            coordinator._slot_mappings["mappings"]
        )
        observed = coordinator._observe_managed_slots()
        slot10 = next(slot for slot in observed if slot.slot == 10)
        slot11 = next(slot for slot in observed if slot.slot == 11)

        assert slot10.status is SlotStatus.BLOCKED
        assert slot10.blocked_reason == "pending_set"
        assert slot11.status is SlotStatus.FREE

        start = datetime(2026, 9, 1, 16, 0, tzinfo=dt_util.UTC)
        end = datetime(2026, 9, 5, 11, 0, tzinfo=dt_util.UTC)
        new_key = "new-nodates." + "d" * 52
        new_reservation = Reservation(
            identity_key=new_key,
            start=start,
            end=end,
            buffered_start=start,
            buffered_end=end,
            summary="New Guest",
            slot_name="New Guest",
            display_slot_name="New Guest",
            slot_code="1234",
        )
        plan = compute_desired_plan(
            [new_reservation],
            observed,
            max_events=2,
            plan_id="pending-nodates",
            generated_at=start,
        )

        assert plan.selected[new_key] == 11
        assert 10 not in set(plan.selected.values())

        mapping["missing_count"] = 2
        reservations = coordinator._build_reservations([])

        assert reservations == []
        assert mapping["missing_count"] == 3
        assert mapping["status"] == "pending_clear"
        assert mapping["operation_id"] is None
        assert mapping["operation_kind"] == "clear"

        assert coordinator.event_overrides is not None
        coordinator.event_overrides.load_persisted_mappings(
            coordinator._slot_mappings["mappings"]
        )
        observed = coordinator._observe_managed_slots()
        slot10 = next(slot for slot in observed if slot.slot == 10)
        clear_plan = compute_desired_plan(
            [],
            observed,
            max_events=2,
            plan_id="pending-nodates-clear",
            generated_at=start,
        )

        assert slot10.status is SlotStatus.FREE
        assert not any(
            action.kind is ActionKind.RETRY_CLEAR and action.slot == 10
            for action in clear_plan.actions
        )

    # ------------------------------------------------------------------
    # T088-2: missing_count reset to 0 when reservation reappears
    # ------------------------------------------------------------------

    async def test_missing_count_reset_when_reservation_reappears(
        self, hass: "HomeAssistant"
    ) -> None:
        """T088-2: _slot_mappings missing_count reset when reservation returns.

        After one miss (missing_count=1), if the reservation reappears
        in the feed, _build_reservations() resets missing_count to 0 in
        _slot_mappings so it is correctly saved.
        """
        from unittest.mock import AsyncMock
        from unittest.mock import MagicMock
        from unittest.mock import patch

        from custom_components.rental_control.coordinator import (
            RentalControlCoordinator,
        )
        from custom_components.rental_control.reconciliation import (
            make_reservation_fingerprint,
        )

        entry = self._make_persist_entry()
        entry.add_to_hass(hass)

        frozen_time = datetime(2026, 7, 15, 12, 0, 0, tzinfo=dt_util.UTC)
        # Dates that produce a fingerprint matching the ICS event below.
        start_dt = datetime(2026, 7, 20, 16, 0, 0, tzinfo=dt_util.UTC)
        end_dt = datetime(2026, 7, 25, 11, 0, 0, tzinfo=dt_util.UTC)
        fp = make_reservation_fingerprint(
            "persist_entry", "Alice Ghost", start_dt, end_dt
        )

        mapping = self._make_occupied_mapping(
            fp,
            "Alice Ghost",
            10,
            missing_count=1,  # previously missed once
            start_state=start_dt.isoformat(),
            end_state=end_dt.isoformat(),
        )
        store_data: dict[str, Any] = {
            "schema_version": 1,
            "entry_id": "persist_entry",
            "mappings": {fp: mapping},
            "blocked_slots": {},
        }

        coordinator = RentalControlCoordinator(hass, entry)
        coordinator._slot_mappings = store_data
        assert coordinator.event_overrides is not None
        eo = coordinator.event_overrides

        # ICS with Alice Ghost — same name and dates as the persisted mapping
        ics_body = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//EN\r\n"
            "BEGIN:VEVENT\r\n"
            "DTSTART;VALUE=DATE:20260720\r\n"
            "DTEND;VALUE=DATE:20260725\r\n"
            "SUMMARY:Alice Ghost\r\nUID:alice-ghost-uid\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
        )

        mock_plan = MagicMock()
        mock_plan.plan_id = "t088-2"
        mock_plan.selected = {}
        mock_plan.overflow = {}
        mock_plan.actions = []
        mock_plan.diagnostics = {}
        mock_plan.validate.return_value = []

        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=frozen_time),
            patch.object(dt_util, "start_of_local_day", return_value=frozen_time),
            patch(
                "custom_components.rental_control.coordinator.compute_desired_plan",
                return_value=mock_plan,
            ),
            patch.object(eo, "async_apply_plan", new=AsyncMock(return_value=[])),
            patch.object(coordinator, "async_save_slot_store", new_callable=AsyncMock),
        ):
            mock_session.get("https://example.com/calendar.ics", body=ics_body)
            await coordinator._async_update_data()

        updated_mc = coordinator._slot_mappings["mappings"][fp]["missing_count"]
        assert updated_mc == 0, (
            f"Expected missing_count=0 after reappearance, got {updated_mc}"
        )

    def test_sync_store_records_confirmed_selected_mapping(
        self, hass: "HomeAssistant"
    ) -> None:
        """T088-3: Confirmed selected slots are written to live Store mappings."""
        from custom_components.rental_control.reconciliation import DesiredPlan
        from custom_components.rental_control.reconciliation import Reservation
        from custom_components.rental_control.util import OperationResult

        entry = self._make_persist_entry()
        entry.add_to_hass(hass)
        coordinator = RentalControlCoordinator(hass, entry)

        start = datetime(2026, 8, 1, 16, 0, tzinfo=dt_util.UTC)
        end = datetime(2026, 8, 5, 11, 0, tzinfo=dt_util.UTC)
        identity_key = "selected." + "a" * 56
        reservation = Reservation(
            identity_key=identity_key,
            start=start,
            end=end,
            buffered_start=start,
            buffered_end=end,
            summary="Alice Selected",
            slot_name="Alice Selected",
            display_slot_name="Alice Selected",
            slot_code="1234",
            uid_aliases={"uid-a"},
            booking_aliases={"book-a"},
        )
        plan = DesiredPlan(plan_id="persist-set", generated_at=start)
        plan.selected = {identity_key: 10}

        coordinator._sync_slot_store_from_plan(
            plan,
            {identity_key: reservation},
            [OperationResult(kind="set", slot=10, confirmed=True)],
        )

        mapping = coordinator._slot_mappings["mappings"][identity_key]
        assert mapping["slot"] == 10
        assert mapping["status"] == "occupied"
        assert mapping["operation_id"] is None
        assert mapping["operation_kind"] is None
        assert mapping["pending_set_since"] is None
        assert mapping["identity"]["slot_name"] == "Alice Selected"
        assert mapping["identity"]["uid_aliases"] == ["uid-a"]
        assert "slot_code" not in mapping
        assert "pin" not in mapping["last_observed_actual"]
        assert coordinator.event_overrides is not None
        assert identity_key in coordinator.event_overrides.persisted_mappings

    def test_sync_store_removes_confirmed_clear_mapping(
        self, hass: "HomeAssistant"
    ) -> None:
        """T088-4: Confirmed clear results remove the cleared slot mapping."""
        from custom_components.rental_control.reconciliation import DesiredPlan
        from custom_components.rental_control.util import OperationResult

        entry = self._make_persist_entry()
        entry.add_to_hass(hass)
        coordinator = RentalControlCoordinator(hass, entry)
        identity_key = "cleared." + "b" * 56
        coordinator._slot_mappings = {
            "schema_version": 1,
            "entry_id": entry.entry_id,
            "mappings": {
                identity_key: self._make_occupied_mapping(
                    identity_key, "Cleared Guest", 10
                )
            },
            "blocked_slots": {},
        }
        assert coordinator.event_overrides is not None
        coordinator.event_overrides.load_persisted_mappings(
            coordinator._slot_mappings["mappings"]
        )
        plan = DesiredPlan(plan_id="persist-clear", generated_at=dt_util.now())

        coordinator._sync_slot_store_from_plan(
            plan,
            {},
            [OperationResult(kind="clear", slot=10, confirmed=True)],
        )

        assert coordinator._slot_mappings["mappings"] == {}
        assert coordinator.event_overrides.persisted_mappings == {}

    def test_sync_store_preserves_unconfirmed_set_mapping(
        self, hass: "HomeAssistant"
    ) -> None:
        """T088-5: Unconfirmed SET results keep reservation-slot mapping."""
        from custom_components.rental_control.reconciliation import DesiredPlan
        from custom_components.rental_control.reconciliation import Reservation
        from custom_components.rental_control.util import OperationResult

        entry = self._make_persist_entry()
        entry.add_to_hass(hass)
        coordinator = RentalControlCoordinator(hass, entry)
        start = datetime(2026, 8, 1, 16, 0, tzinfo=dt_util.UTC)
        end = datetime(2026, 8, 5, 11, 0, tzinfo=dt_util.UTC)
        identity_key = "unconfirmed." + "c" * 52
        reservation = Reservation(
            identity_key=identity_key,
            start=start,
            end=end,
            buffered_start=start,
            buffered_end=end,
            summary="Unconfirmed Guest",
            slot_name="Unconfirmed Guest",
            display_slot_name="Unconfirmed Guest",
            slot_code="1234",
        )
        plan = DesiredPlan(plan_id="persist-unconfirmed", generated_at=start)
        plan.selected = {identity_key: 10}

        coordinator._sync_slot_store_from_plan(
            plan,
            {identity_key: reservation},
            [OperationResult(kind="set", slot=10, unconfirmed=True)],
        )

        mapping = coordinator._slot_mappings["mappings"][identity_key]
        assert mapping["slot"] == 10
        assert mapping["status"] == "pending_set"
        assert mapping["operation_id"] == "persist-unconfirmed"
        assert mapping["operation_kind"] == "set"
        assert mapping["pending_set_since"] is not None

    def test_sync_store_skips_failed_set_mapping(self, hass: "HomeAssistant") -> None:
        """T088-5: Failed SET results are not persisted as occupied."""
        from custom_components.rental_control.reconciliation import DesiredPlan
        from custom_components.rental_control.reconciliation import Reservation
        from custom_components.rental_control.util import OperationResult

        entry = self._make_persist_entry()
        entry.add_to_hass(hass)
        coordinator = RentalControlCoordinator(hass, entry)
        start = datetime(2026, 8, 1, 16, 0, tzinfo=dt_util.UTC)
        end = datetime(2026, 8, 5, 11, 0, tzinfo=dt_util.UTC)
        identity_key = "failed." + "c" * 52
        reservation = Reservation(
            identity_key=identity_key,
            start=start,
            end=end,
            buffered_start=start,
            buffered_end=end,
            summary="Failed Guest",
            slot_name="Failed Guest",
            display_slot_name="Failed Guest",
            slot_code="1234",
        )
        plan = DesiredPlan(plan_id="persist-failed", generated_at=start)
        plan.selected = {identity_key: 10}

        coordinator._sync_slot_store_from_plan(
            plan,
            {identity_key: reservation},
            [OperationResult(kind="set", slot=10, failed=True)],
        )

        assert identity_key not in coordinator._slot_mappings["mappings"]

    def test_sync_store_serializes_actual_datetimes(
        self, hass: "HomeAssistant"
    ) -> None:
        """T088-13: Store last-observed datetimes as ISO strings."""
        from custom_components.rental_control.reconciliation import DesiredPlan
        from custom_components.rental_control.reconciliation import Reservation
        from custom_components.rental_control.util import OperationResult

        entry = self._make_persist_entry()
        entry.add_to_hass(hass)
        coordinator = RentalControlCoordinator(hass, entry)
        assert coordinator.event_overrides is not None
        start = datetime(2026, 8, 1, 16, 0, tzinfo=dt_util.UTC)
        end = datetime(2026, 8, 5, 11, 0, tzinfo=dt_util.UTC)
        actual_start = start - timedelta(minutes=30)
        actual_end = end + timedelta(minutes=30)
        identity_key = "datetime." + "e" * 55
        reservation = Reservation(
            identity_key=identity_key,
            start=start,
            end=end,
            buffered_start=actual_start,
            buffered_end=actual_end,
            summary="Datetime Guest",
            slot_name="Datetime Guest",
            display_slot_name="Datetime Guest",
            slot_code="1234",
        )
        coordinator.event_overrides.update_actual_state(
            10,
            {
                "slot": 10,
                "classification": "occupied",
                "name_state": "Datetime Guest",
                "has_code": True,
                "start_state": actual_start,
                "end_state": actual_end,
                "use_date_range": True,
                "enabled": True,
            },
        )
        plan = DesiredPlan(plan_id="datetime-store", generated_at=start)
        plan.selected = {identity_key: 10}

        coordinator._sync_slot_store_from_plan(
            plan,
            {identity_key: reservation},
            [OperationResult(kind="set", slot=10, confirmed=True)],
        )

        observed = coordinator._slot_mappings["mappings"][identity_key][
            "last_observed_actual"
        ]
        assert observed["start_state"] == actual_start.isoformat()
        assert observed["end_state"] == actual_end.isoformat()

    def test_sync_store_does_not_readd_confirmed_clear_selection(
        self, hass: "HomeAssistant"
    ) -> None:
        """T088-6: Confirmed CLEAR wins over stale selected mappings."""
        from custom_components.rental_control.reconciliation import DesiredPlan
        from custom_components.rental_control.reconciliation import Reservation
        from custom_components.rental_control.util import OperationResult

        entry = self._make_persist_entry()
        entry.add_to_hass(hass)
        coordinator = RentalControlCoordinator(hass, entry)
        start = datetime(2026, 8, 1, 16, 0, tzinfo=dt_util.UTC)
        end = datetime(2026, 8, 5, 11, 0, tzinfo=dt_util.UTC)
        identity_key = "clear-selected." + "d" * 49
        coordinator._slot_mappings = {
            "schema_version": 1,
            "entry_id": entry.entry_id,
            "mappings": {
                identity_key: self._make_occupied_mapping(
                    identity_key, "Clear Selected", 10
                )
            },
            "blocked_slots": {},
        }
        reservation = Reservation(
            identity_key=identity_key,
            start=start,
            end=end,
            buffered_start=start,
            buffered_end=end,
            summary="Clear Selected",
            slot_name="Clear Selected",
            display_slot_name="Clear Selected",
            slot_code="",
        )
        plan = DesiredPlan(plan_id="persist-clear-selected", generated_at=start)
        plan.selected = {identity_key: 10}

        coordinator._sync_slot_store_from_plan(
            plan,
            {identity_key: reservation},
            [OperationResult(kind="clear", slot=10, confirmed=True)],
        )

        assert identity_key not in coordinator._slot_mappings["mappings"]

    def test_build_reservations_rematches_uid_date_shift(
        self, hass: "HomeAssistant"
    ) -> None:
        """T088-7: UID rematch migrates a shifted fingerprint in place."""
        from custom_components.rental_control.reconciliation import (
            make_reservation_fingerprint,
        )

        entry = self._make_persist_entry()
        entry.add_to_hass(hass)
        coordinator = RentalControlCoordinator(hass, entry)
        old_start = datetime(2026, 8, 1, 16, 0, tzinfo=dt_util.UTC)
        old_end = datetime(2026, 8, 5, 11, 0, tzinfo=dt_util.UTC)
        new_start = datetime(2026, 8, 2, 16, 0, tzinfo=dt_util.UTC)
        new_end = datetime(2026, 8, 6, 11, 0, tzinfo=dt_util.UTC)
        old_key = make_reservation_fingerprint(
            entry.entry_id, "Shift Guest", old_start, old_end
        )
        new_key = make_reservation_fingerprint(
            entry.entry_id, "Shift Guest", new_start, new_end
        )
        mapping = self._make_occupied_mapping(
            old_key,
            "Shift Guest",
            10,
            start_state=old_start.isoformat(),
            end_state=old_end.isoformat(),
        )
        mapping["identity"]["uid_aliases"] = ["stable-uid"]
        mapping["identity"]["start"] = old_start.isoformat()
        mapping["identity"]["end"] = old_end.isoformat()
        coordinator._slot_mappings = {
            "schema_version": 1,
            "entry_id": entry.entry_id,
            "mappings": {old_key: mapping},
            "blocked_slots": {},
        }

        event = MagicMock()
        event.summary = "Shift Guest"
        event.description = ""
        event.start = new_start
        event.end = new_end
        event.uid = "stable-uid"

        reservations = coordinator._build_reservations([event])

        assert [res.identity_key for res in reservations] == [new_key]
        assert old_key not in coordinator._slot_mappings["mappings"]
        migrated = coordinator._slot_mappings["mappings"][new_key]
        assert migrated["slot"] == 10
        assert old_key in migrated["fingerprint_history"]
        assert migrated["identity"]["identity_key"] == new_key

    def test_observe_slots_uses_rematched_store_identity(
        self, hass: "HomeAssistant"
    ) -> None:
        """T084-3: First refresh observes rematched adopted ownership."""
        from custom_components.rental_control.reconciliation import (
            make_reservation_fingerprint,
        )

        entry = self._make_persist_entry()
        entry.add_to_hass(hass)
        coordinator = RentalControlCoordinator(hass, entry)
        coordinator.event_prefix = "RC"
        start = datetime(2026, 8, 1, 16, 0, tzinfo=dt_util.UTC)
        end = datetime(2026, 8, 5, 11, 0, tzinfo=dt_util.UTC)
        adopted_key = f"adopted.{entry.entry_id}.slot10"
        fingerprint = make_reservation_fingerprint(
            entry.entry_id, "Adopt Guest", start, end
        )
        coordinator._slot_mappings = {
            "schema_version": 1,
            "entry_id": entry.entry_id,
            "mappings": {
                adopted_key: self._make_occupied_mapping(
                    adopted_key,
                    "Adopt Guest",
                    10,
                )
            },
            "blocked_slots": {},
        }
        coordinator._slot_mappings["mappings"][adopted_key]["last_observed_actual"][
            "name_state"
        ] = "RC Adopt Guest"
        assert coordinator.event_overrides is not None
        coordinator.event_overrides.load_persisted_mappings(
            coordinator._slot_mappings["mappings"]
        )
        hass.states.async_set("text.test_lock_code_slot_10_name", "RC Adopt Guest")
        hass.states.async_set("text.test_lock_code_slot_10_pin", "1234")

        event = MagicMock()
        event.summary = "RC Adopt Guest"
        event.description = ""
        event.start = start
        event.end = end
        event.uid = None

        coordinator._build_reservations([event])
        coordinator.event_overrides.load_persisted_mappings(
            coordinator._slot_mappings["mappings"]
        )
        observed = coordinator._observe_managed_slots()

        slot10 = next(slot for slot in observed if slot.slot == 10)
        assert slot10.persisted_identity_key == fingerprint

    def test_build_reservations_uses_full_set_for_ambiguous_rematch(
        self, hass: "HomeAssistant"
    ) -> None:
        """T088-10: Rematch ambiguity considers all current reservations."""
        entry = self._make_persist_entry()
        entry.add_to_hass(hass)
        coordinator = RentalControlCoordinator(hass, entry)
        adopted_key = f"adopted.{entry.entry_id}.slot10"
        coordinator._slot_mappings = {
            "schema_version": 1,
            "entry_id": entry.entry_id,
            "mappings": {
                adopted_key: self._make_occupied_mapping(
                    adopted_key,
                    "Repeat Guest",
                    10,
                )
            },
            "blocked_slots": {},
        }

        first = MagicMock()
        first.summary = "Repeat Guest"
        first.description = ""
        first.start = datetime(2026, 8, 1, 16, 0, tzinfo=dt_util.UTC)
        first.end = datetime(2026, 8, 5, 11, 0, tzinfo=dt_util.UTC)
        first.uid = None
        second = MagicMock()
        second.summary = "Repeat Guest"
        second.description = ""
        second.start = datetime(2026, 8, 10, 16, 0, tzinfo=dt_util.UTC)
        second.end = datetime(2026, 8, 15, 11, 0, tzinfo=dt_util.UTC)
        second.uid = None

        reservations = coordinator._build_reservations([first, second])

        current_reservations = [
            reservation
            for reservation in reservations
            if reservation.start in {first.start, second.start}
        ]
        assert len(current_reservations) == 2
        assert all(
            reservation.identity_key != adopted_key
            for reservation in current_reservations
        )
        assert adopted_key in coordinator._slot_mappings["mappings"]

    def test_diagnostics_deep_scrubs_raw_code_keys(self, hass: "HomeAssistant") -> None:
        """T098-2: Diagnostics recursively omit raw code-bearing fields."""
        entry = self._make_persist_entry()
        entry.add_to_hass(hass)
        coordinator = RentalControlCoordinator(hass, entry)
        plan = MagicMock()
        plan.diagnostics = {
            "slot_code": "1234",
            "nested": {"pin": "5678", "safe": "ok"},
            "items": [{"code": "9999", "slot": 10}],
        }
        coordinator._latest_plan = plan
        coordinator.event_overrides = MagicMock()
        coordinator.event_overrides.diagnostics_snapshot = {
            "slot": {"slot_code": "0000", "state": "occupied"}
        }

        diagnostics = coordinator.latest_reconciliation_diagnostics

        assert "slot_code" not in diagnostics
        assert "pin" not in diagnostics["nested"]
        assert "code" not in diagnostics["items"][0]
        assert "slot_code" not in diagnostics["event_overrides"]["slot"]
        assert diagnostics["nested"]["safe"] == "ok"

    def test_observe_unknown_keymaster_state_blocks_slot(
        self, hass: "HomeAssistant"
    ) -> None:
        """T088-9: Unknown Keymaster entity states are not treated as free."""
        from custom_components.rental_control.reconciliation import SlotStatus

        entry = self._make_persist_entry()
        entry.add_to_hass(hass)
        coordinator = RentalControlCoordinator(hass, entry)
        hass.states.async_set("text.test_lock_code_slot_10_name", "unavailable")
        hass.states.async_set("text.test_lock_code_slot_10_pin", "1234")

        observed = coordinator._observe_managed_slots()

        slot10 = next(slot for slot in observed if slot.slot == 10)
        assert slot10.status is SlotStatus.UNKNOWN

    def test_build_reservations_honors_last_four_code_generation(
        self, hass: "HomeAssistant"
    ) -> None:
        """T103-5: Coordinator-owned writes preserve last_four PIN generation."""
        entry = self._make_persist_entry()
        entry.add_to_hass(hass)
        coordinator = RentalControlCoordinator(hass, entry)
        coordinator.code_generator = "last_four"
        coordinator.code_length = 4
        start = datetime(2026, 8, 1, 16, 0, tzinfo=dt_util.UTC)
        end = datetime(2026, 8, 5, 11, 0, tzinfo=dt_util.UTC)
        event = MagicMock()
        event.summary = "Code Guest"
        event.description = "Last 4 Digits: 2468"
        event.start = start
        event.end = end
        event.uid = "code-guest"

        reservations = coordinator._build_reservations([event])

        assert reservations[0].slot_code == "2468"

    def test_build_reservations_honors_static_random_generation(
        self, hass: "HomeAssistant"
    ) -> None:
        """T103-6: Coordinator-owned writes preserve static_random generation."""
        import random

        entry = self._make_persist_entry()
        entry.add_to_hass(hass)
        coordinator = RentalControlCoordinator(hass, entry)
        coordinator.code_generator = "static_random"
        coordinator.code_length = 4
        start = datetime(2026, 8, 1, 16, 0, tzinfo=dt_util.UTC)
        end = datetime(2026, 8, 5, 11, 0, tzinfo=dt_util.UTC)
        event = MagicMock()
        event.summary = "Random Guest"
        event.description = "Phone: 555 123 4567"
        event.start = start
        event.end = end
        event.uid = "stable-random-uid"

        reservations = coordinator._build_reservations([event])

        expected = str(random.Random("stable-random-uid").randrange(1, 9999, 4)).zfill(
            4
        )
        assert reservations[0].slot_code == expected

    def test_sync_store_writes_identity_start_and_end(
        self, hass: "HomeAssistant"
    ) -> None:
        """T088-8: Store identities include unbuffered start/end for rematch."""
        from custom_components.rental_control.reconciliation import DesiredPlan
        from custom_components.rental_control.reconciliation import Reservation

        entry = self._make_persist_entry()
        entry.add_to_hass(hass)
        coordinator = RentalControlCoordinator(hass, entry)
        start = datetime(2026, 8, 1, 16, 0, tzinfo=dt_util.UTC)
        end = datetime(2026, 8, 5, 11, 0, tzinfo=dt_util.UTC)
        identity_key = "start-end." + "e" * 54
        reservation = Reservation(
            identity_key=identity_key,
            start=start,
            end=end,
            buffered_start=start - timedelta(hours=1),
            buffered_end=end + timedelta(hours=1),
            summary="Start End Guest",
            slot_name="Start End Guest",
            display_slot_name="Start End Guest",
            slot_code="1234",
        )
        plan = DesiredPlan(plan_id="persist-start-end", generated_at=start)
        plan.selected = {identity_key: 10}

        coordinator._sync_slot_store_from_plan(plan, {identity_key: reservation}, [])

        identity = coordinator._slot_mappings["mappings"][identity_key]["identity"]
        assert identity["start"] == start.isoformat()
        assert identity["end"] == end.isoformat()

    def test_sync_store_evicts_stale_mapping_for_reused_slot(
        self, hass: "HomeAssistant"
    ) -> None:
        """T089-4: Reusing a free slot drops its stale occupied mapping."""
        from custom_components.rental_control.reconciliation import DesiredPlan
        from custom_components.rental_control.reconciliation import Reservation

        entry = self._make_persist_entry()
        entry.add_to_hass(hass)
        coordinator = RentalControlCoordinator(hass, entry)
        old_key = "old-stale." + "f" * 54
        new_key = "new-active." + "a" * 53
        coordinator._slot_mappings = {
            "schema_version": 1,
            "entry_id": entry.entry_id,
            "mappings": {
                old_key: self._make_occupied_mapping(old_key, "Old Guest", 10)
            },
            "blocked_slots": {},
        }

        start = datetime(2026, 8, 1, 16, 0, tzinfo=dt_util.UTC)
        end = datetime(2026, 8, 5, 11, 0, tzinfo=dt_util.UTC)
        reservation = Reservation(
            identity_key=new_key,
            start=start,
            end=end,
            buffered_start=start,
            buffered_end=end,
            summary="New Guest",
            slot_name="New Guest",
            display_slot_name="New Guest",
            slot_code="1234",
        )
        plan = DesiredPlan(plan_id="persist-reuse", generated_at=start)
        plan.selected = {new_key: 10}

        coordinator._sync_slot_store_from_plan(plan, {new_key: reservation}, [])

        mappings = coordinator._slot_mappings["mappings"]
        assert old_key not in mappings
        assert mappings[new_key]["slot"] == 10


class TestHonorPMSTimesRegression:
    """T103 regression: honor_event_times semantics preserved.

    These tests pin four honour-PMS-times branches that must survive the
    reconciliation refactor: timed PMS events take calendar times, all-day
    events use description times, override fallback, and configured defaults.
    """

    @staticmethod
    def _expected_utc(day: int, hour: int, minute: int = 0) -> datetime:
        """Return an America/New_York local time converted to UTC."""
        from datetime import time as time_cls
        from zoneinfo import ZoneInfo

        result: datetime = dt.as_utc(
            datetime.combine(
                datetime(2024, 12, day).date(),
                time_cls(hour, minute),
                ZoneInfo("America/New_York"),
            )
        )
        return result

    @staticmethod
    def _make_entry(
        entry_id: str,
        *,
        checkin: str = "16:00",
        checkout: str = "11:00",
        lockname: str | None = None,
    ) -> MockConfigEntry:
        """Build a minimal config entry for honor-event-time regression tests."""
        data = {
            "name": "Honor Regression",
            "url": "https://example.com/calendar.ics",
            "timezone": "America/New_York",
            "checkin": checkin,
            "checkout": checkout,
            "start_slot": 10,
            "max_events": 3,
            "days": 90,
            "verify_ssl": True,
            "ignore_non_reserved": False,
            "honor_event_times": True,
        }
        if lockname is not None:
            data[CONF_LOCK_ENTRY] = lockname
        return MockConfigEntry(
            domain="rental_control",
            title="Honor Regression",
            version=8,
            unique_id=entry_id,
            data=data,
            entry_id=entry_id,
        )

    async def test_timed_event_uses_pms_times(self, hass: HomeAssistant) -> None:
        """Timed events keep their explicit PMS calendar times."""
        frozen_time = datetime(2024, 12, 20, 12, 0, 0, tzinfo=dt_util.UTC)
        timed_ics = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
DTSTART:20241225T150000
DTEND:20241230T100000
UID:t103-reg-1@example.com
SUMMARY:Reserved - Timed Guest
DESCRIPTION:Email: timed@example.com
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR
"""
        entry = self._make_entry("t103_reg_1")
        entry.add_to_hass(hass)

        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=frozen_time),
            patch.object(dt_util, "start_of_local_day", return_value=frozen_time),
        ):
            mock_session.get(
                "https://example.com/calendar.ics", status=200, body=timed_ics
            )
            coordinator = RentalControlCoordinator(hass, entry)
            result = await coordinator._async_update_data()

        assert len(result) == 1
        assert result[0].start == self._expected_utc(25, 15)
        assert result[0].end == self._expected_utc(30, 10)

    async def test_allday_description_times_used(self, hass: HomeAssistant) -> None:
        """All-day events prefer description-derived times over overrides."""
        from custom_components.rental_control.event_overrides import EventOverrides

        frozen_time = datetime(2024, 12, 20, 12, 0, 0, tzinfo=dt_util.UTC)
        allday_ics = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
DTSTART;VALUE=DATE:20241225
DTEND;VALUE=DATE:20241230
UID:t103-reg-2@example.com
SUMMARY:Reserved - Desc Guest
DESCRIPTION:Check-in: 3:00 PM\\nCheck-out: 11:00 AM
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR
"""
        entry = self._make_entry("t103_reg_2", lockname="front_door")
        entry.add_to_hass(hass)
        MockConfigEntry(
            domain="keymaster",
            data={"lockname": "front_door"},
            entry_id="km_t103_reg_2",
        ).add_to_hass(hass)

        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=frozen_time),
            patch.object(dt_util, "start_of_local_day", return_value=frozen_time),
        ):
            mock_session.get(
                "https://example.com/calendar.ics", status=200, body=allday_ics
            )
            coordinator = RentalControlCoordinator(hass, entry)
            coordinator.event_overrides = EventOverrides(10, 3)
            coordinator.event_overrides._overrides[10] = {
                "slot_name": "Desc Guest",
                "slot_code": "1234",
                "start_time": datetime(2024, 12, 25, 18, 0, 0, tzinfo=dt_util.UTC),
                "end_time": datetime(2024, 12, 30, 14, 0, 0, tzinfo=dt_util.UTC),
            }
            result = await coordinator._async_update_data()

        assert len(result) == 1
        assert result[0].start == self._expected_utc(25, 15)
        assert result[0].end == self._expected_utc(30, 11)

    async def test_allday_override_fallback_when_no_description_times(
        self,
        hass: HomeAssistant,
    ) -> None:
        """All-day events fall back to override times when descriptions lack them."""
        from custom_components.rental_control.event_overrides import EventOverrides

        frozen_time = datetime(2024, 12, 20, 12, 0, 0, tzinfo=dt_util.UTC)
        allday_ics = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
DTSTART;VALUE=DATE:20241225
DTEND;VALUE=DATE:20241230
UID:t103-reg-3@example.com
SUMMARY:Reserved - Override Guest
DESCRIPTION:Email: override@example.com
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR
"""
        entry = self._make_entry("t103_reg_3", lockname="front_door")
        entry.add_to_hass(hass)
        MockConfigEntry(
            domain="keymaster",
            data={"lockname": "front_door"},
            entry_id="km_t103_reg_3",
        ).add_to_hass(hass)

        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=frozen_time),
            patch.object(dt_util, "start_of_local_day", return_value=frozen_time),
        ):
            mock_session.get(
                "https://example.com/calendar.ics", status=200, body=allday_ics
            )
            coordinator = RentalControlCoordinator(hass, entry)
            coordinator.event_overrides = EventOverrides(10, 3)
            coordinator.event_overrides._overrides[10] = {
                "slot_name": "Override Guest",
                "slot_code": "1234",
                "start_time": datetime(2024, 12, 25, 18, 0, 0, tzinfo=dt_util.UTC),
                "end_time": datetime(2024, 12, 30, 14, 0, 0, tzinfo=dt_util.UTC),
            }
            result = await coordinator._async_update_data()

        assert len(result) == 1
        assert result[0].start == self._expected_utc(25, 13)
        assert result[0].end == self._expected_utc(30, 9)

    async def test_allday_configured_defaults_when_no_description_no_override(
        self,
        hass: HomeAssistant,
    ) -> None:
        """All-day events fall back to configured defaults without overrides."""
        frozen_time = datetime(2024, 12, 20, 12, 0, 0, tzinfo=dt_util.UTC)
        allday_ics = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
DTSTART;VALUE=DATE:20241225
DTEND;VALUE=DATE:20241230
UID:t103-reg-4@example.com
SUMMARY:Reserved - Default Guest
DESCRIPTION:Email: default@example.com
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR
"""
        entry = self._make_entry(
            "t103_reg_4",
            checkin="15:00",
            checkout="10:00",
        )
        entry.add_to_hass(hass)

        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=frozen_time),
            patch.object(dt_util, "start_of_local_day", return_value=frozen_time),
        ):
            mock_session.get(
                "https://example.com/calendar.ics", status=200, body=allday_ics
            )
            coordinator = RentalControlCoordinator(hass, entry)
            result = await coordinator._async_update_data()

        assert len(result) == 1
        assert result[0].start == self._expected_utc(25, 15)
        assert result[0].end == self._expected_utc(30, 10)
