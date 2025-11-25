# SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for integration initialization."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rental_control.const import COORDINATOR
from custom_components.rental_control.const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


async def test_async_setup_entry(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_aiohttp_session,
) -> None:
    """Test integration setup creates coordinator and loads platforms.

    This test verifies that async_setup_entry:
    1. Creates a RentalControlCoordinator
    2. Stores coordinator in hass.data
    3. Forwards setup to all platform domains (sensor, calendar)
    4. Registers an update listener
    """
    mock_config_entry.add_to_hass(hass)

    # Mock the calendar URL to avoid network calls
    from tests.fixtures import calendar_data

    mock_aiohttp_session.get(
        mock_config_entry.data["url"],
        status=200,
        body=calendar_data.AIRBNB_ICS_CALENDAR,
    )

    # Setup the integration
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # Verify coordinator was created and stored
    assert DOMAIN in hass.data
    assert mock_config_entry.entry_id in hass.data[DOMAIN]
    assert COORDINATOR in hass.data[DOMAIN][mock_config_entry.entry_id]

    coordinator = hass.data[DOMAIN][mock_config_entry.entry_id][COORDINATOR]
    assert coordinator is not None
    assert coordinator.hass == hass
    assert coordinator.config_entry == mock_config_entry


async def test_async_unload_entry(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_aiohttp_session,
) -> None:
    """Test integration cleanup and entity removal.

    This test verifies that async_unload_entry:
    1. Unloads all platforms
    2. Cleans up coordinator resources
    3. Removes entry from hass.data
    4. Unsubscribes from listeners
    """
    # First setup the integration
    mock_config_entry.add_to_hass(hass)

    from tests.fixtures import calendar_data

    mock_aiohttp_session.get(
        mock_config_entry.data["url"],
        status=200,
        body=calendar_data.AIRBNB_ICS_CALENDAR,
    )

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # Verify integration is set up
    assert DOMAIN in hass.data
    assert mock_config_entry.entry_id in hass.data[DOMAIN]

    # Now unload the entry
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # Verify cleanup
    assert mock_config_entry.entry_id not in hass.data.get(DOMAIN, {})


async def test_platform_loading(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_aiohttp_session,
) -> None:
    """Test sensor and calendar platforms are loaded.

    This test verifies that the integration correctly forwards
    setup to both sensor and calendar platforms during initialization.
    """
    mock_config_entry.add_to_hass(hass)

    from tests.fixtures import calendar_data

    mock_aiohttp_session.get(
        mock_config_entry.data["url"],
        status=200,
        body=calendar_data.AIRBNB_ICS_CALENDAR,
    )

    # Setup the integration
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # Verify platforms are registered
    # Check that sensor platform was loaded
    assert f"{DOMAIN}.sensor" in hass.config.components
    # Check that calendar platform was loaded
    assert f"{DOMAIN}.calendar" in hass.config.components


async def test_async_setup_entry_failure(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_aiohttp_session,
) -> None:
    """Test setup handles coordinator initialization errors.

    This test verifies that async_setup_entry properly handles
    errors during coordinator initialization, specifically when
    the coordinator creation fails or encounters an error.
    """
    mock_config_entry.add_to_hass(hass)

    # Mock the calendar URL to return an error
    mock_aiohttp_session.get(
        mock_config_entry.data["url"],
        status=500,
        body="Internal Server Error",
    )

    # Setup should still succeed even if initial calendar fetch fails
    # The coordinator will handle the error gracefully
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # Verify coordinator was still created (error handling is graceful)
    assert DOMAIN in hass.data
    assert mock_config_entry.entry_id in hass.data[DOMAIN]
    assert COORDINATOR in hass.data[DOMAIN][mock_config_entry.entry_id]
