# SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for RentalControlCoordinator."""

from __future__ import annotations

import asyncio
from datetime import datetime
from datetime import timedelta
from typing import TYPE_CHECKING
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

from custom_components.rental_control.const import CONF_LOCK_ENTRY
from custom_components.rental_control.const import CONF_MAX_EVENTS
from custom_components.rental_control.const import CONF_REFRESH_FREQUENCY
from custom_components.rental_control.const import DEFAULT_REFRESH_FREQUENCY
from custom_components.rental_control.coordinator import RentalControlCoordinator

from tests.fixtures import calendar_data

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry


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
        """Verify unknown/unavailable states are converted to empty strings."""
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

        mock_update.assert_awaited_once()
        call_args = mock_update.call_args[0]
        assert call_args[1] == ""
        assert call_args[2] == ""


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
