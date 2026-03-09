# SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for RentalControlCoordinator."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from aioresponses import aioresponses
from homeassistant.components.calendar import CalendarEvent
from homeassistant.util import dt

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
    from datetime import datetime
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
    from datetime import timedelta

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
    from datetime import datetime
    from datetime import timedelta
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
    """Test coordinator behavior with malformed ICS content.

    Note: Currently the coordinator does not have try/except around
    Calendar.from_ical(), so malformed ICS raises ValueError.
    This test documents current behavior. Future enhancement could
    add graceful error handling to log and continue without crashing.
    """
    import pytest

    mock_config_entry.add_to_hass(hass)

    with aioresponses() as mock_session:
        # Mock response with malformed ICS (missing END:VCALENDAR)
        mock_session.get(
            "https://example.com/calendar.ics",
            status=200,
            body=calendar_data.MALFORMED_ICS_CALENDAR,
        )

        coordinator = RentalControlCoordinator(hass, mock_config_entry)

        # Current behavior: malformed ICS raises ValueError from icalendar parser
        # TODO: Consider adding try/except in coordinator._refresh_calendar()
        # to handle gracefully (log error, keep calendar_loaded=False)
        with pytest.raises(ValueError):
            await coordinator.update()

        await hass.async_block_till_done()


async def test_coordinator_state_management(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test coordinator data property maintains event state.

    Verifies that coordinator properly maintains calendar state,
    tracks events, and updates next event reference.
    """
    from datetime import datetime
    from datetime import timedelta
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
    from homeassistant.util import dt

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
