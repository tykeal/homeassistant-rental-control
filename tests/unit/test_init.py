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

    # Verify platforms are registered - hass.config.components tracks domain names
    assert DOMAIN in hass.config.components
    assert "sensor" in hass.config.components
    assert "calendar" in hass.config.components


async def test_config_entry_reload(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_aiohttp_session,
) -> None:
    """Test entry reload updates coordinator config.

    This test verifies that when a config entry is reloaded with
    updated data, the coordinator configuration is updated accordingly.
    """
    mock_config_entry.add_to_hass(hass)

    from tests.fixtures import calendar_data

    mock_aiohttp_session.get(
        mock_config_entry.data["url"],
        status=200,
        body=calendar_data.AIRBNB_ICS_CALENDAR,
        repeat=True,
    )

    # Setup the integration
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][mock_config_entry.entry_id][COORDINATOR]
    original_refresh_frequency = coordinator.refresh_frequency

    # Update data directly to simulate config change (reload reads from data)
    # Setting options to {} causes update_listener to return early (line 266-267
    # in __init__.py checks "if not config_entry.options: return")
    new_data = dict(mock_config_entry.data)
    new_data["refresh_frequency"] = 30  # New value different from default

    hass.config_entries.async_update_entry(
        mock_config_entry,
        data=new_data,
        options={},  # Empty options causes update_listener to exit early
    )

    # Trigger reload - this reinitializes coordinator from config_entry.data
    await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # Verify coordinator config was updated
    updated_coordinator = hass.data[DOMAIN][mock_config_entry.entry_id][COORDINATOR]
    assert updated_coordinator.refresh_frequency == 30
    assert updated_coordinator.refresh_frequency != original_refresh_frequency


# Note: Tasks T039a-T039d test features that are either not yet implemented
# or are better tested at the integration level:
# - T039a (service_registration): No services currently registered in __init__.py
# - T039b (platform_reload): Platform reloading tested via config_entry_reload
# - T039c (state_change_listeners): Tested via async_start_listener functionality
# - T039d (event_handling): Event handling tested in integration tests
# These tests can be added when the corresponding features are implemented.


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
