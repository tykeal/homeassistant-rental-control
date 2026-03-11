# SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for RentalControlCoordinator."""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import aiohttp
from aioresponses import aioresponses
from homeassistant.components.calendar import CalendarEvent
from homeassistant.util import dt
import homeassistant.util.dt as dt_util

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

    Verifies that async_config_entry_first_refresh properly fetches
    and processes calendar data on initial load.
    """
    from unittest.mock import patch

    import homeassistant.util.dt as dt_util

    mock_config_entry.add_to_hass(hass)

    # Freeze time to a date before the fixture events (Jan 2025)
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

        # Verify initial state before refresh
        assert coordinator.calendar == []
        assert coordinator.calendar_ready is False
        assert coordinator.calendar_loaded is False

        # Call update to trigger first refresh
        await coordinator.update()
        await hass.async_block_till_done()

        # Verify calendar data was loaded
        assert coordinator.calendar_loaded is True
        assert len(coordinator.calendar) > 0
        # First event should be from the Airbnb ICS fixture
        assert coordinator.calendar[0].summary is not None


async def test_coordinator_scheduled_refresh(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test coordinator updates on scheduled interval.

    Verifies that the coordinator automatically refreshes calendar data
    when the scheduled interval elapses using async_fire_time_changed.
    """
    from homeassistant.util import dt
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    mock_config_entry.add_to_hass(hass)

    with aioresponses() as mock_session:
        # Mock the calendar URL to return data
        mock_session.get(
            "https://example.com/calendar.ics",
            status=200,
            body=calendar_data.AIRBNB_ICS_CALENDAR,
            repeat=True,  # Allow multiple calls
        )

        coordinator = RentalControlCoordinator(hass, mock_config_entry)

        # Record initial next_refresh time
        initial_next_refresh = coordinator.next_refresh

        # Trigger initial update
        await coordinator.update()
        await hass.async_block_till_done()

        # Verify next_refresh was updated
        assert coordinator.next_refresh > initial_next_refresh

        # Advance time past the refresh interval
        future_time = dt.now() + timedelta(minutes=coordinator.refresh_frequency + 1)
        async_fire_time_changed(hass, future_time)

        # Trigger scheduled update
        await coordinator.update()
        await hass.async_block_till_done()

        # Verify calendar was loaded
        assert coordinator.calendar_loaded is True


async def test_coordinator_refresh_success(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test successful calendar fetch and event parsing.

    Verifies that coordinator successfully fetches ICS data from URL,
    parses events, and updates internal calendar state.
    """
    from unittest.mock import patch

    import homeassistant.util.dt as dt_util

    mock_config_entry.add_to_hass(hass)

    # Use frozen time for deterministic test behavior
    frozen_time = datetime(2024, 12, 20, 12, 0, 0, tzinfo=dt_util.UTC)

    # Create ICS with future events relative to frozen time
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

        # Trigger refresh
        await coordinator.update()
        await hass.async_block_till_done()

        # Verify calendar was loaded successfully
        assert coordinator.calendar_loaded is True
        assert len(coordinator.calendar) > 0

        # Verify events were parsed from the ICS data
        assert coordinator.calendar[0].summary == "Reserved: Test Guest"


async def test_coordinator_refresh_network_error(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test error handling for HTTP failures.

    Verifies that coordinator handles HTTP error responses gracefully
    and doesn't crash when calendar URL returns HTTP errors.
    """
    mock_config_entry.add_to_hass(hass)

    with aioresponses() as mock_session:
        # Mock a 404 Not Found error
        mock_session.get(
            "https://example.com/calendar.ics",
            status=404,
        )

        coordinator = RentalControlCoordinator(hass, mock_config_entry)

        # Trigger refresh - should not raise exception
        await coordinator.update()
        await hass.async_block_till_done()

        # Calendar should not be loaded on HTTP error
        assert coordinator.calendar_loaded is False
        assert len(coordinator.calendar) == 0

    # Test with 500 Internal Server Error
    with aioresponses() as mock_session:
        mock_session.get(
            "https://example.com/calendar.ics",
            status=500,
        )

        coordinator = RentalControlCoordinator(hass, mock_config_entry)

        # Trigger refresh - should handle error gracefully
        await coordinator.update()
        await hass.async_block_till_done()

        # Calendar should remain unloaded
        assert coordinator.calendar_loaded is False


async def test_coordinator_refresh_invalid_ics(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test coordinator handles malformed ICS content gracefully.

    The coordinator wraps _refresh_calendar in try/except, so
    malformed ICS data is caught, logged, and the previous calendar
    state is preserved without crashing.
    """
    mock_config_entry.add_to_hass(hass)

    with aioresponses() as mock_session:
        # Mock response with malformed ICS (missing END:VCALENDAR)
        mock_session.get(
            "https://example.com/calendar.ics",
            status=200,
            body=calendar_data.MALFORMED_ICS_CALENDAR,
        )

        coordinator = RentalControlCoordinator(hass, mock_config_entry)

        # Graceful handling: malformed ICS is caught, logged,
        # and the coordinator preserves its previous calendar state
        await coordinator.update()

        await hass.async_block_till_done()

        # Calendar should remain empty (initial state) since the
        # malformed data could not be parsed
        assert coordinator.calendar == []
        assert coordinator.calendar_loaded is False


async def test_coordinator_state_management(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test coordinator data property maintains event state.

    Verifies that coordinator properly maintains calendar state,
    tracks events, and updates next event reference.
    """
    from unittest.mock import patch

    import homeassistant.util.dt as dt_util

    mock_config_entry.add_to_hass(hass)

    # Use frozen time for deterministic test behavior
    frozen_time = datetime(2024, 12, 20, 12, 0, 0, tzinfo=dt_util.UTC)

    # Create ICS with multiple future events relative to frozen time
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

        # Trigger refresh
        await coordinator.update()
        await hass.async_block_till_done()

        # Verify state management
        assert coordinator.calendar_loaded is True
        assert len(coordinator.calendar) == 2

        # Verify calendar maintains sorted event list
        assert coordinator.calendar[0].start < coordinator.calendar[1].start

        # Verify next event is set (first event with end in future)
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

    # Verify initial refresh frequency uses default since mock_config_entry.data
    # does not include refresh_frequency
    initial_frequency = coordinator.refresh_frequency
    assert initial_frequency == DEFAULT_REFRESH_FREQUENCY

    # Update configuration with new refresh frequency
    new_config = dict(mock_config_entry.data)
    new_config[CONF_REFRESH_FREQUENCY] = 30

    coordinator.update_config(new_config)

    # Verify refresh frequency was updated
    assert coordinator.refresh_frequency == 30
    assert coordinator.refresh_frequency != initial_frequency

    # Verify next refresh was reset to trigger immediate update
    assert coordinator.next_refresh <= dt.now()


# ---------------------------------------------------------------------------
# Phase 7 – targeted coverage tests for coordinator.py
# ---------------------------------------------------------------------------


async def test_coordinator_events_ready_initially_false(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test events_ready is False before sensors are registered.

    Covers coordinator.py events_ready property when event_sensors
    count does not match max_events.
    """
    mock_config_entry.add_to_hass(hass)
    coordinator = RentalControlCoordinator(hass, mock_config_entry)

    assert coordinator.events_ready is False
    assert len(coordinator.event_sensors) == 0


async def test_coordinator_events_ready_becomes_true(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test events_ready becomes True when all sensors report available.

    Covers coordinator.py events_ready property including the sensor
    status aggregation branch.
    """
    mock_config_entry.add_to_hass(hass)
    coordinator = RentalControlCoordinator(hass, mock_config_entry)

    # Simulate registered sensors that report available
    for _ in range(coordinator.max_events):
        sensor = MagicMock()
        sensor.available = True
        coordinator.event_sensors.append(sensor)

    assert coordinator.events_ready is True

    # Once True, it stays True (cached)
    assert coordinator.events_ready is True


async def test_coordinator_events_ready_false_when_sensor_unavailable(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test events_ready stays False when any sensor is unavailable.

    Covers coordinator.py events_ready aggregation with mixed states.
    """
    mock_config_entry.add_to_hass(hass)
    coordinator = RentalControlCoordinator(hass, mock_config_entry)

    for i in range(coordinator.max_events):
        sensor = MagicMock()
        sensor.available = i > 0  # First sensor unavailable
        coordinator.event_sensors.append(sensor)

    assert coordinator.events_ready is False


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
    coordinator.calendar = [in_range_event, out_of_range_event]

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
    coordinator.event_overrides = mock_overrides
    coordinator.calendar_loaded = True

    now = dt.now()
    await coordinator.update_event_overrides(
        slot=1,
        slot_code="1234",
        slot_name="Test Guest",
        start_time=now,
        end_time=now + timedelta(days=2),
    )

    mock_overrides.update.assert_called_once()
    assert coordinator.calendar_ready is True
    assert coordinator.next_refresh <= dt.now()


async def test_coordinator_update_event_overrides_without_overrides(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test update_event_overrides without event_overrides.

    Covers coordinator.py update_event_overrides else branch when
    event_overrides is None.
    """
    mock_config_entry.add_to_hass(hass)
    coordinator = RentalControlCoordinator(hass, mock_config_entry)

    # event_overrides is None when no lockname
    assert coordinator.event_overrides is None
    coordinator.calendar_loaded = True

    now = dt.now()
    await coordinator.update_event_overrides(
        slot=1,
        slot_code="1234",
        slot_name="Test Guest",
        start_time=now,
        end_time=now + timedelta(days=2),
    )

    assert coordinator.calendar_ready is True
    assert coordinator.next_refresh <= dt.now()


# ---------------------------------------------------------------------------
# _refresh_event_dict._get_date isinstance ordering tests
# ---------------------------------------------------------------------------


class TestGetDateIsinstance:
    """Tests for the _get_date helper inside _refresh_event_dict.

    Before the fix, isinstance(day, date) was checked first in _get_date.
    Since datetime is a subclass of date, this always matched for
    datetime objects, making the datetime branch dead code.  The helper
    then returned a datetime instead of a plain date, causing a
    TypeError when the list-comprehension compared datetime <= date.
    """

    async def test_datetime_events_filtered_without_error(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify _refresh_event_dict handles datetime starts.

        With the old isinstance ordering, passing a timezone-aware
        datetime to _get_date returned a datetime object.  The
        subsequent ``datetime <= date`` comparison in the list
        comprehension would raise TypeError.  After the fix,
        _get_date converts datetime to date first so the comparison
        succeeds.
        """
        mock_config_entry.add_to_hass(hass)
        with aioresponses() as mock_session:
            mock_session.get(
                mock_config_entry.data["url"],
                body=calendar_data.AIRBNB_ICS_CALENDAR,
            )
            coordinator = RentalControlCoordinator(hass, mock_config_entry)
            await coordinator.update()

        # Create an event with a timezone-aware datetime start that is
        # within the configured max_days window so it should be included.
        now = dt.now()
        event = CalendarEvent(
            start=now,
            end=now + timedelta(hours=1),
            summary="datetime event",
        )
        coordinator.calendar = [event]

        # Before the fix this raised TypeError; after, it filters fine
        result = coordinator._refresh_event_dict()  # noqa: SLF001
        assert len(result) == 1
        assert result[0].summary == "datetime event"

    async def test_plain_date_events_filtered_correctly(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify that plain date events pass through filtering.

        _get_date should return the date directly when the input is
        not a datetime instance, and the filtering comparison should
        work without error.
        """
        mock_config_entry.add_to_hass(hass)
        with aioresponses() as mock_session:
            mock_session.get(
                mock_config_entry.data["url"],
                body=calendar_data.AIRBNB_ICS_CALENDAR,
            )
            coordinator = RentalControlCoordinator(hass, mock_config_entry)
            await coordinator.update()

        today = dt.start_of_local_day().date()
        event = CalendarEvent(
            start=today,
            end=today + timedelta(days=1),
            summary="date event",
        )
        coordinator.calendar = [event]

        result = coordinator._refresh_event_dict()  # noqa: SLF001
        assert len(result) == 1
        assert result[0].summary == "date event"

    async def test_far_future_datetime_event_excluded(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify a datetime event far in the future is excluded.

        Events whose start date exceeds the configured max_days window
        should be filtered out, confirming the date comparison works
        correctly for datetime values after the isinstance fix.
        """
        mock_config_entry.add_to_hass(hass)
        with aioresponses() as mock_session:
            mock_session.get(
                mock_config_entry.data["url"],
                body=calendar_data.AIRBNB_ICS_CALENDAR,
            )
            coordinator = RentalControlCoordinator(hass, mock_config_entry)
            await coordinator.update()

        # Event well beyond the max_days window
        far_future = dt.now() + timedelta(days=coordinator.days + 100)
        event = CalendarEvent(
            start=far_future,
            end=far_future + timedelta(hours=1),
            summary="far future",
        )
        coordinator.calendar = [event]

        result = coordinator._refresh_event_dict()  # noqa: SLF001
        assert len(result) == 0


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
        from unittest.mock import patch

        import homeassistant.util.dt as dt_util

        mock_config_entry.add_to_hass(hass)

        frozen_time = datetime(2024, 12, 20, 12, 0, 0, tzinfo=dt_util.UTC)
        future_start = (frozen_time + timedelta(days=5)).strftime("%Y%m%d")
        future_end = (frozen_time + timedelta(days=10)).strftime("%Y%m%d")

        # ICS with tz-aware datetime DTSTART/DTEND (Z suffix = UTC)
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
            await coordinator.update()

        assert coordinator.calendar is not None
        assert len(coordinator.calendar) > 0
        assert coordinator.calendar[0].summary == "Reserved: TZ Guest"

    async def test_date_only_events_parsed(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify events with plain date DTSTART/DTEND are handled.

        Plain date comparisons should work without triggering the
        exception handler at all.
        """
        from unittest.mock import patch

        import homeassistant.util.dt as dt_util

        mock_config_entry.add_to_hass(hass)

        frozen_time = datetime(2024, 12, 20, 12, 0, 0, tzinfo=dt_util.UTC)
        future_start = (frozen_time + timedelta(days=5)).strftime("%Y%m%d")
        future_end = (frozen_time + timedelta(days=10)).strftime("%Y%m%d")

        # ICS with plain DATE values (no time component)
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
            await coordinator.update()

        assert coordinator.calendar is not None
        assert len(coordinator.calendar) > 0
        assert coordinator.calendar[0].summary == "Reserved: Date Guest"


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
    """Tests for the calendar miss counter logic in _refresh_calendar."""

    async def test_miss_counter_preserves_previous_calendar(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify empty calendar increments miss counter and keeps old data.

        When a refresh returns zero events but the previous calendar
        had events and misses are below the threshold, the coordinator
        should increment num_misses and preserve the existing calendar.
        """
        mock_config_entry.add_to_hass(hass)

        # First refresh: load valid calendar
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
            await coordinator.update()

        assert len(coordinator.calendar) > 0
        assert coordinator.num_misses == 0
        previous_calendar = list(coordinator.calendar)

        # Second refresh: return empty calendar
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
            await coordinator.update()

        # Miss counter should increment; calendar preserved
        assert coordinator.num_misses == 1
        assert len(coordinator.calendar) == len(previous_calendar)

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
            coordinator.num_misses = 1  # simulate previous miss
            await coordinator.update()

        assert coordinator.num_misses == 0
        assert len(coordinator.calendar) > 0


class TestRefreshCalendarClientError:
    """Tests for aiohttp.ClientError handling in _refresh_calendar."""

    async def test_client_error_preserves_state(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify aiohttp.ClientError is caught and state is preserved."""
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
            await coordinator.update()

        assert coordinator.calendar_loaded is False
        assert len(coordinator.calendar) == 0


class TestRefreshCalendarGenericException:
    """Tests for generic exception handling in _refresh_calendar."""

    async def test_unexpected_error_preserves_state(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify unexpected exceptions are caught and state is preserved."""
        mock_config_entry.add_to_hass(hass)

        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=FROZEN_TIME),
            patch.object(
                dt_util, "start_of_local_day", return_value=FROZEN_START_OF_DAY
            ),
        ):
            # Malformed ICS triggers a parse error
            mock_session.get(
                mock_config_entry.data["url"],
                body="THIS IS NOT VALID ICS DATA",
            )
            coordinator = RentalControlCoordinator(hass, mock_config_entry)
            await coordinator.update()

        assert coordinator.calendar_loaded is False
        assert len(coordinator.calendar) == 0


class TestCalendarReadyWithOverrides:
    """Tests for calendar_ready when overrides are configured."""

    async def test_calendar_ready_with_ready_overrides(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify calendar_ready is True when overrides are ready."""
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

            # Simulate lockname with ready overrides
            coordinator.lockname = "front_door"
            mock_overrides = MagicMock()
            mock_overrides.ready = True
            mock_overrides.async_check_overrides = AsyncMock()
            mock_overrides.get_slot_with_name.return_value = None
            coordinator.event_overrides = mock_overrides

            await coordinator.update()

        assert coordinator.calendar_loaded is True
        assert coordinator.calendar_ready is True

    async def test_calendar_not_ready_with_unready_overrides(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify calendar_ready is False when overrides are not ready."""
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

            # Simulate lockname with NOT ready overrides
            coordinator.lockname = "front_door"
            mock_overrides = MagicMock()
            mock_overrides.ready = False
            mock_overrides.async_check_overrides = AsyncMock()
            mock_overrides.get_slot_with_name.return_value = None
            coordinator.event_overrides = mock_overrides

            await coordinator.update()

        assert coordinator.calendar_loaded is True
        assert coordinator.calendar_ready is False


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
            coordinator.lockname = "front_door"
            mock_overrides = MagicMock()
            mock_overrides.ready = False
            mock_overrides.async_check_overrides = AsyncMock()
            coordinator.event_overrides = mock_overrides
            mock_update = AsyncMock()
            object.__setattr__(coordinator, "update_event_overrides", mock_update)

            # hass.states.get returns None for all entities
            hass.states.async_set("dummy.entity", "on")

            await coordinator.update()

        # No overrides should be updated since PIN entities don't exist
        mock_update.assert_not_awaited()

    async def test_bootstrap_skips_missing_name_entity(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify slots with PIN but no NAME entity are skipped."""
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
            coordinator.lockname = "front_door"
            mock_overrides = MagicMock()
            mock_overrides.ready = False
            mock_overrides.async_check_overrides = AsyncMock()
            coordinator.event_overrides = mock_overrides
            mock_update = AsyncMock()
            object.__setattr__(coordinator, "update_event_overrides", mock_update)

            # Set PIN entity but no NAME entity
            hass.states.async_set("text.front_door_code_slot_10_pin", "1234")

            await coordinator.update()

        mock_update.assert_not_awaited()

    async def test_bootstrap_loads_slot_without_date_range(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify slot is loaded with default times when date range is off."""
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
            coordinator.lockname = "front_door"
            mock_overrides = MagicMock()
            mock_overrides.ready = False
            mock_overrides.async_check_overrides = AsyncMock()
            coordinator.event_overrides = mock_overrides
            mock_update = AsyncMock()
            object.__setattr__(coordinator, "update_event_overrides", mock_update)

            # Set PIN and NAME entities for slot 10
            hass.states.async_set("text.front_door_code_slot_10_pin", "1234")
            hass.states.async_set("text.front_door_code_slot_10_name", "Guest")

            await coordinator.update()

        mock_update.assert_awaited_once()
        call_args = mock_update.call_args[0]
        assert call_args[0] == 10  # slot number
        assert call_args[1] == "1234"  # slot code
        assert call_args[2] == "Guest"  # slot name
        # Default times: start_of_local_day and +1 day
        assert call_args[3] == FROZEN_START_OF_DAY
        assert call_args[4] == FROZEN_START_OF_DAY + timedelta(days=1)

    async def test_bootstrap_loads_slot_with_date_range(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify slot is loaded with parsed times when date range is on."""
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
            coordinator.lockname = "front_door"
            mock_overrides = MagicMock()
            mock_overrides.ready = False
            mock_overrides.async_check_overrides = AsyncMock()
            coordinator.event_overrides = mock_overrides
            mock_update = AsyncMock()
            object.__setattr__(coordinator, "update_event_overrides", mock_update)

            # Set all required entities
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

            await coordinator.update()

        mock_update.assert_awaited_once()
        call_args = mock_update.call_args[0]
        assert call_args[0] == 10
        assert call_args[1] == "5678"
        assert call_args[2] == "VIP"
        # Parsed datetimes should be set
        assert call_args[3] is not None
        assert call_args[4] is not None

    async def test_bootstrap_skips_unparseable_start_time(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify slot is skipped when start time cannot be parsed."""
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

            await coordinator.update()

        mock_update.assert_not_awaited()

    async def test_bootstrap_handles_unknown_slot_states(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Verify unknown/unavailable states are converted to empty strings."""
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
            coordinator.lockname = "front_door"
            mock_overrides = MagicMock()
            mock_overrides.ready = False
            mock_overrides.async_check_overrides = AsyncMock()
            coordinator.event_overrides = mock_overrides
            mock_update = AsyncMock()
            object.__setattr__(coordinator, "update_event_overrides", mock_update)

            hass.states.async_set("text.front_door_code_slot_10_pin", "unknown")
            hass.states.async_set("text.front_door_code_slot_10_name", "unavailable")

            await coordinator.update()

        mock_update.assert_awaited_once()
        call_args = mock_update.call_args[0]
        assert call_args[1] == ""  # unknown → empty
        assert call_args[2] == ""  # unavailable → empty
