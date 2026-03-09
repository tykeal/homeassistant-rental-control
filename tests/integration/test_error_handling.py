# SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Integration tests for error handling in Rental Control.

These tests verify that the integration handles network errors, malformed
ICS data, HTTP failures, timeouts, and recovery scenarios gracefully
without crashing or leaving the system in an inconsistent state.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING
from unittest.mock import patch

from aioresponses import aioresponses
import homeassistant.util.dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rental_control.const import COORDINATOR
from custom_components.rental_control.const import DOMAIN

from tests.fixtures import calendar_data
from tests.integration.helpers import FROZEN_TIME
from tests.integration.helpers import future_ics

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


# ---------------------------------------------------------------------------
# T119 – network error handling
# ---------------------------------------------------------------------------


async def test_network_error_handling(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Verify integration handles HTTP failures gracefully.

    When the calendar URL returns HTTP 500, the integration should still
    set up successfully (coordinator is created), but the calendar should
    not be marked as loaded.
    """
    mock_config_entry.add_to_hass(hass)

    with aioresponses() as mock_session:
        mock_session.get(
            mock_config_entry.data["url"],
            status=500,
            repeat=True,
        )

        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][mock_config_entry.entry_id][COORDINATOR]
    assert coordinator.calendar_loaded is False
    assert len(coordinator.calendar) == 0


# ---------------------------------------------------------------------------
# T120 – invalid ICS handling
# ---------------------------------------------------------------------------


async def test_invalid_ics_handling(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Verify integration handles ICS data with missing fields.

    ICS events that lack DTSTART/DTEND should be skipped during parsing.
    The coordinator should still load without raising an exception.
    """
    mock_config_entry.add_to_hass(hass)

    with (
        aioresponses() as mock_session,
        patch.object(dt_util, "now", return_value=FROZEN_TIME),
        patch.object(dt_util, "start_of_local_day", return_value=FROZEN_TIME),
    ):
        mock_session.get(
            mock_config_entry.data["url"],
            status=200,
            body=calendar_data.ICS_MISSING_FIELDS,
            repeat=True,
        )

        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][mock_config_entry.entry_id][COORDINATOR]
    # Missing-fields events are skipped; calendar_loaded may be True
    # but the calendar list should be empty
    assert len(coordinator.calendar) == 0


# ---------------------------------------------------------------------------
# T121 – missing calendar (404)
# ---------------------------------------------------------------------------


async def test_missing_calendar_handling(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Verify integration handles 404 responses.

    A 404 means the calendar URL is not found. The coordinator should
    handle this gracefully without crashing.
    """
    mock_config_entry.add_to_hass(hass)

    with aioresponses() as mock_session:
        mock_session.get(
            mock_config_entry.data["url"],
            status=404,
            repeat=True,
        )

        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][mock_config_entry.entry_id][COORDINATOR]
    assert coordinator.calendar_loaded is False
    assert len(coordinator.calendar) == 0


# ---------------------------------------------------------------------------
# T122 – timeout handling
# ---------------------------------------------------------------------------


async def test_timeout_handling(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Verify integration handles request timeouts.

    When the HTTP request raises a TimeoutError the coordinator should
    not crash the integration setup.
    """
    mock_config_entry.add_to_hass(hass)

    with aioresponses() as mock_session:
        mock_session.get(
            mock_config_entry.data["url"],
            exception=asyncio.TimeoutError(),
            repeat=True,
        )

        # Setup may succeed (coordinator created) even if fetch times out.
        # The important thing is it doesn't raise an unhandled exception.
        result = await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    # If setup returned True, coordinator should exist but calendar not loaded
    if result:
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id][COORDINATOR]
        assert coordinator.calendar_loaded is False


# ---------------------------------------------------------------------------
# T123 – sensor availability on error
# ---------------------------------------------------------------------------


async def test_sensor_availability_on_error(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Verify sensors are created and show no-reservation state on error.

    When the calendar fails to load, sensors should still be created
    and show "No reservation" as their state.
    """
    mock_config_entry.add_to_hass(hass)

    with aioresponses() as mock_session:
        mock_session.get(
            mock_config_entry.data["url"],
            status=500,
            repeat=True,
        )

        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][mock_config_entry.entry_id][COORDINATOR]
    assert len(coordinator.event_sensors) == mock_config_entry.data["max_events"]

    for sensor in coordinator.event_sensors:
        assert "No reservation" in sensor.state


# ---------------------------------------------------------------------------
# T124 – recovery after error
# ---------------------------------------------------------------------------


async def test_recovery_after_error(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Verify integration recovers when calendar becomes available again.

    First update returns an error; second update returns valid data.
    The coordinator should transition from not-loaded to loaded.
    """
    mock_config_entry.add_to_hass(hass)

    # Freeze time during setup so next_refresh is predictable
    with (
        aioresponses() as mock_session,
        patch.object(dt_util, "now", return_value=FROZEN_TIME),
        patch.object(dt_util, "start_of_local_day", return_value=FROZEN_TIME),
    ):
        mock_session.get(
            mock_config_entry.data["url"],
            status=500,
            repeat=True,
        )

        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][mock_config_entry.entry_id][COORDINATOR]
    assert coordinator.calendar_loaded is False

    # Advance past refresh interval and provide valid data
    future = FROZEN_TIME + timedelta(minutes=coordinator.refresh_frequency + 1)

    with (
        aioresponses() as mock_session,
        patch.object(dt_util, "now", return_value=future),
        patch.object(dt_util, "start_of_local_day", return_value=future),
    ):
        mock_session.get(
            mock_config_entry.data["url"],
            status=200,
            body=future_ics(base_time=future),
            repeat=True,
        )

        await coordinator.update()
        await hass.async_block_till_done()

    assert coordinator.calendar_loaded is True
    assert len(coordinator.calendar) > 0


# ---------------------------------------------------------------------------
# T125 – coordinator error state tracking
# ---------------------------------------------------------------------------


async def test_coordinator_error_state(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Verify coordinator maintains error state correctly.

    After a failed fetch the coordinator should keep calendar_loaded as
    False and calendar_ready as False. After a successful fetch both
    should become True (ready depends on overrides, which are not
    configured here so overrides_loaded defaults to True on first load).
    """
    mock_config_entry.add_to_hass(hass)

    # Freeze time during setup so next_refresh is predictable
    with (
        aioresponses() as mock_session,
        patch.object(dt_util, "now", return_value=FROZEN_TIME),
        patch.object(dt_util, "start_of_local_day", return_value=FROZEN_TIME),
    ):
        mock_session.get(
            mock_config_entry.data["url"],
            status=500,
            repeat=True,
        )

        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][mock_config_entry.entry_id][COORDINATOR]
    assert coordinator.calendar_loaded is False
    assert coordinator.calendar_ready is False

    # Now succeed with time advanced past next_refresh
    future = FROZEN_TIME + timedelta(minutes=coordinator.refresh_frequency + 1)
    with (
        aioresponses() as mock_session,
        patch.object(dt_util, "now", return_value=future),
        patch.object(dt_util, "start_of_local_day", return_value=future),
    ):
        mock_session.get(
            mock_config_entry.data["url"],
            status=200,
            body=future_ics(base_time=future),
            repeat=True,
        )

        await coordinator.update()
        await hass.async_block_till_done()

    assert coordinator.calendar_loaded is True
    assert coordinator.calendar_ready is True
