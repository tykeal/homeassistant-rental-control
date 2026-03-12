# SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Integration tests for error handling in Rental Control.

These tests verify that the integration handles network errors, malformed
ICS data, HTTP failures, timeouts, and recovery scenarios gracefully
without crashing or leaving the system in an inconsistent state.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import patch

from aioresponses import aioresponses
from homeassistant.config_entries import ConfigEntryState
import homeassistant.util.dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rental_control.const import COORDINATOR
from custom_components.rental_control.const import DOMAIN

from tests.fixtures import calendar_data
from tests.integration.helpers import FROZEN_START_OF_DAY
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

    When the calendar URL returns HTTP 500,
    async_config_entry_first_refresh() raises ConfigEntryNotReady,
    causing the config entry to enter SETUP_RETRY state. The
    integration will auto-retry later.
    """
    mock_config_entry.add_to_hass(hass)

    with aioresponses() as mock_session:
        mock_session.get(
            mock_config_entry.data["url"],
            status=500,
            repeat=True,
        )

        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


# ---------------------------------------------------------------------------
# T120 – invalid ICS handling
# ---------------------------------------------------------------------------


async def test_invalid_ics_handling(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Verify integration handles ICS data with missing fields.

    ICS events that lack DTSTART/DTEND cause a parsing error in the
    coordinator. With DUC, this raises UpdateFailed which becomes
    ConfigEntryNotReady, putting the entry into SETUP_RETRY state.
    """
    mock_config_entry.add_to_hass(hass)

    with (
        aioresponses() as mock_session,
        patch.object(dt_util, "now", return_value=FROZEN_TIME),
        patch.object(dt_util, "start_of_local_day", return_value=FROZEN_START_OF_DAY),
    ):
        mock_session.get(
            mock_config_entry.data["url"],
            status=200,
            body=calendar_data.ICS_MISSING_FIELDS,
            repeat=True,
        )

        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


# ---------------------------------------------------------------------------
# T121 – missing calendar (404)
# ---------------------------------------------------------------------------


async def test_missing_calendar_handling(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Verify integration handles 404 responses.

    A 404 means the calendar URL is not found. With DUC, this raises
    UpdateFailed which becomes ConfigEntryNotReady, putting the entry
    into SETUP_RETRY state for automatic recovery.
    """
    mock_config_entry.add_to_hass(hass)

    with aioresponses() as mock_session:
        mock_session.get(
            mock_config_entry.data["url"],
            status=404,
            repeat=True,
        )

        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


# ---------------------------------------------------------------------------
# T122 – timeout handling
# ---------------------------------------------------------------------------


async def test_timeout_handling(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Verify integration handles request timeouts.

    When the HTTP request raises a TimeoutError, the coordinator raises
    UpdateFailed which becomes ConfigEntryNotReady, putting the entry
    into SETUP_RETRY state for automatic recovery.
    """
    mock_config_entry.add_to_hass(hass)

    with aioresponses() as mock_session:
        mock_session.get(
            mock_config_entry.data["url"],
            exception=asyncio.TimeoutError(),
            repeat=True,
        )

        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


# ---------------------------------------------------------------------------
# T123 – sensor availability on error
# ---------------------------------------------------------------------------


async def test_sensor_availability_on_error(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Verify setup enters SETUP_RETRY on HTTP error.

    With DUC, when the calendar URL returns HTTP 500, the first refresh
    fails and the config entry goes to SETUP_RETRY state. Sensors are
    not created until setup succeeds on a subsequent retry.
    """
    mock_config_entry.add_to_hass(hass)

    with aioresponses() as mock_session:
        mock_session.get(
            mock_config_entry.data["url"],
            status=500,
            repeat=True,
        )

        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


# ---------------------------------------------------------------------------
# T124 – recovery after error
# ---------------------------------------------------------------------------


async def test_recovery_after_error(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Verify coordinator recovers after a failed update.

    With DUC, the first refresh succeeds during setup. Subsequent
    update failures are handled gracefully by the DUC framework,
    and the coordinator recovers when valid data is available again.
    """
    mock_config_entry.add_to_hass(hass)

    # Setup with valid data
    with (
        aioresponses() as mock_session,
        patch.object(dt_util, "now", return_value=FROZEN_TIME),
        patch.object(dt_util, "start_of_local_day", return_value=FROZEN_START_OF_DAY),
    ):
        mock_session.get(
            mock_config_entry.data["url"],
            status=200,
            body=future_ics(base_time=FROZEN_TIME),
            repeat=True,
        )

        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][mock_config_entry.entry_id][COORDINATOR]
    assert coordinator.last_update_success is True
    assert len(coordinator.data) > 0

    # Trigger an update that fails — DUC handles gracefully
    with aioresponses() as mock_session:
        mock_session.get(
            mock_config_entry.data["url"],
            status=500,
            repeat=True,
        )

        await coordinator.async_refresh()
        await hass.async_block_till_done()

    assert coordinator.last_update_success is False

    # Recover with valid data
    with (
        aioresponses() as mock_session,
        patch.object(dt_util, "now", return_value=FROZEN_TIME),
        patch.object(dt_util, "start_of_local_day", return_value=FROZEN_START_OF_DAY),
    ):
        mock_session.get(
            mock_config_entry.data["url"],
            status=200,
            body=future_ics(base_time=FROZEN_TIME),
            repeat=True,
        )

        await coordinator.async_refresh()
        await hass.async_block_till_done()

    assert coordinator.last_update_success is True
    assert len(coordinator.data) > 0


# ---------------------------------------------------------------------------
# T125 – coordinator error state tracking
# ---------------------------------------------------------------------------


async def test_coordinator_error_state(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Verify coordinator tracks error and recovery state correctly.

    With DUC, successful setup loads calendar data. A subsequent
    failed update is tracked via last_update_success. Recovery on
    the next successful update restores calendar_loaded and
    calendar_ready to True.
    """
    mock_config_entry.add_to_hass(hass)

    # Setup with valid data
    with (
        aioresponses() as mock_session,
        patch.object(dt_util, "now", return_value=FROZEN_TIME),
        patch.object(dt_util, "start_of_local_day", return_value=FROZEN_START_OF_DAY),
    ):
        mock_session.get(
            mock_config_entry.data["url"],
            status=200,
            body=future_ics(base_time=FROZEN_TIME),
            repeat=True,
        )

        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][mock_config_entry.entry_id][COORDINATOR]
    assert coordinator.last_update_success is True

    # Trigger a failed update — DUC handles gracefully
    with aioresponses() as mock_session:
        mock_session.get(
            mock_config_entry.data["url"],
            status=500,
            repeat=True,
        )

        await coordinator.async_refresh()
        await hass.async_block_till_done()

    assert coordinator.last_update_success is False

    # Recover with valid data
    with (
        aioresponses() as mock_session,
        patch.object(dt_util, "now", return_value=FROZEN_TIME),
        patch.object(dt_util, "start_of_local_day", return_value=FROZEN_START_OF_DAY),
    ):
        mock_session.get(
            mock_config_entry.data["url"],
            status=200,
            body=future_ics(base_time=FROZEN_TIME),
            repeat=True,
        )

        await coordinator.async_refresh()
        await hass.async_block_till_done()

    assert coordinator.last_update_success is True
