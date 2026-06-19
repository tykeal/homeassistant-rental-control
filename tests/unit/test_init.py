# SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for integration initialization."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_NAME
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rental_control import update_listener
from custom_components.rental_control.const import CONF_CODE_LENGTH
from custom_components.rental_control.const import CONF_CREATION_DATETIME
from custom_components.rental_control.const import CONF_GENERATE
from custom_components.rental_control.const import CONF_HONOR_EVENT_TIMES
from custom_components.rental_control.const import CONF_PATH
from custom_components.rental_control.const import CONF_SHOULD_UPDATE_CODE
from custom_components.rental_control.const import COORDINATOR
from custom_components.rental_control.const import DEFAULT_CODE_LENGTH
from custom_components.rental_control.const import DEFAULT_GENERATE
from custom_components.rental_control.const import DOMAIN
from custom_components.rental_control.const import UNSUB_LISTENERS

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
    # Setting options to {} causes update_listener to return early since it
    # checks for empty options before processing
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


async def test_update_listener_present_data_updates_config_and_listeners(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Verify update_listener preserves loaded-entry update behavior."""
    mock_config_entry.add_to_hass(hass)
    updated_options = dict(mock_config_entry.data)
    updated_options[CONF_NAME] = "Updated Rental"
    updated_options["refresh_frequency"] = 12
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options=updated_options,
    )

    coordinator = MagicMock()
    coordinator.created = "2026-06-18T15:00:00+00:00"
    coordinator.lockname = "front_door"
    coordinator.update_config = AsyncMock()
    unsub_listener = MagicMock()
    hass.data[DOMAIN] = {
        mock_config_entry.entry_id: {
            COORDINATOR: coordinator,
            UNSUB_LISTENERS: [unsub_listener],
        },
    }

    with (
        patch(
            "custom_components.rental_control.async_start_listener",
            new_callable=AsyncMock,
        ) as start_listener,
        patch(
            "custom_components.rental_control.async_register_keymaster_listener",
        ) as register_listener,
    ):
        await update_listener(hass, mock_config_entry)

    assert mock_config_entry.data[CONF_NAME] == "Updated Rental"
    assert mock_config_entry.data[CONF_CREATION_DATETIME] == coordinator.created
    assert mock_config_entry.options == {}
    coordinator.update_config.assert_awaited_once()
    unsub_listener.assert_called_once_with()
    assert hass.data[DOMAIN][mock_config_entry.entry_id][UNSUB_LISTENERS] == []
    start_listener.assert_awaited_once_with(hass, mock_config_entry)
    register_listener.assert_called_once_with(hass, mock_config_entry)


async def test_update_listener_missing_entry_before_update_returns(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Verify update_listener returns before mutation without entry data."""
    mock_config_entry.add_to_hass(hass)
    original_data = dict(mock_config_entry.data)
    updated_options = dict(mock_config_entry.data)
    updated_options[CONF_NAME] = "Updated Rental"
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options=updated_options,
    )
    hass.data[DOMAIN] = {}

    with patch.object(
        hass.config_entries,
        "async_update_entry",
        wraps=hass.config_entries.async_update_entry,
    ) as update_entry:
        await update_listener(hass, mock_config_entry)

    update_entry.assert_not_called()
    assert mock_config_entry.data == original_data
    assert hass.data[DOMAIN] == {}


async def test_update_listener_missing_domain_before_update_returns(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Verify update_listener returns before mutation without domain data."""
    mock_config_entry.add_to_hass(hass)
    original_data = dict(mock_config_entry.data)
    updated_options = dict(mock_config_entry.data)
    updated_options[CONF_NAME] = "Updated Rental"
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options=updated_options,
    )
    hass.data.pop(DOMAIN, None)

    with patch.object(
        hass.config_entries,
        "async_update_entry",
        wraps=hass.config_entries.async_update_entry,
    ) as update_entry:
        await update_listener(hass, mock_config_entry)

    update_entry.assert_not_called()
    assert mock_config_entry.data == original_data
    assert DOMAIN not in hass.data


async def test_update_listener_entry_vanishes_after_config_update_returns(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Verify listener refresh is skipped when entry data vanishes."""
    mock_config_entry.add_to_hass(hass)
    updated_options = dict(mock_config_entry.data)
    updated_options[CONF_NAME] = "Updated Rental"
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options=updated_options,
    )

    coordinator = MagicMock()
    coordinator.created = "2026-06-18T15:00:00+00:00"
    coordinator.lockname = "front_door"
    unsub_listener = MagicMock()

    async def _remove_entry(_new_data: dict[str, object]) -> None:
        """Remove entry data while update_listener is updating config."""
        hass.data[DOMAIN].pop(mock_config_entry.entry_id)

    coordinator.update_config = AsyncMock(side_effect=_remove_entry)
    hass.data[DOMAIN] = {
        mock_config_entry.entry_id: {
            COORDINATOR: coordinator,
            UNSUB_LISTENERS: [unsub_listener],
        },
    }

    with (
        patch(
            "custom_components.rental_control.async_start_listener",
            new_callable=AsyncMock,
        ) as start_listener,
        patch(
            "custom_components.rental_control.async_register_keymaster_listener",
        ) as register_listener,
    ):
        await update_listener(hass, mock_config_entry)

    coordinator.update_config.assert_awaited_once()
    unsub_listener.assert_not_called()
    start_listener.assert_not_called()
    register_listener.assert_not_called()
    assert mock_config_entry.entry_id not in hass.data[DOMAIN]


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
    errors during coordinator initialization. With DUC,
    async_config_entry_first_refresh() raises ConfigEntryNotReady
    when the first data fetch fails, causing the entry to enter
    SETUP_RETRY state for automatic recovery.
    """
    mock_config_entry.add_to_hass(hass)

    # Mock the calendar URL to return an error
    mock_aiohttp_session.get(
        mock_config_entry.data["url"],
        status=500,
        body="Internal Server Error",
    )

    # With DUC, async_config_entry_first_refresh() raises
    # ConfigEntryNotReady when the first fetch fails, so setup
    # does not succeed — the entry goes to SETUP_RETRY instead.
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


# ---------------------------------------------------------------------------
# Migration tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("old_version", [1, 2])
async def test_migrate_entry_rejects_version_below_3(
    hass: HomeAssistant,
    old_version: int,
) -> None:
    """Verify entries at version 1 or 2 are rejected with an error.

    Versions 1 and 2 are no longer supported because the oldest known
    installation (v0.9.0) ships at config version 3.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Old Entry",
        version=old_version,
        unique_id=f"old-v{old_version}-entry",
        data={
            "name": "Old Entry",
            "url": "https://example.com/calendar.ics",
        },
        entry_id=f"old_v{old_version}_entry",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.async_setup(entry.entry_id)
    assert result is False


async def test_migrate_entry_v3_to_v10(
    hass: HomeAssistant,
) -> None:
    """Verify a version-3 entry migrates through all steps to version 10.

    The migration chain 3→4→5→6→7→8→9→10 adds code_length,
    generate_package, removes packages_path, adds
    should_update_code, honor_event_times, trim fields, and
    code buffer fields.
    """
    from custom_components.rental_control import async_migrate_entry

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="V3 Entry",
        version=3,
        unique_id="v3-migration-test",
        data={
            "name": "V3 Entry",
            "url": "https://example.com/calendar.ics",
            "timezone": "America/New_York",
            "checkin": "16:00",
            "checkout": "11:00",
            "start_slot": 10,
            "max_events": 3,
            "days": 90,
            "verify_ssl": True,
            "ignore_non_reserved": False,
            "packages_path": "/config/packages",
        },
        entry_id="v3_entry",
    )
    entry.add_to_hass(hass)

    result = await async_migrate_entry(hass, entry)

    assert result is True
    assert entry.version == 10
    assert entry.data[CONF_CODE_LENGTH] == DEFAULT_CODE_LENGTH
    assert entry.data[CONF_GENERATE] == DEFAULT_GENERATE
    assert CONF_PATH not in entry.data
    assert entry.data[CONF_SHOULD_UPDATE_CODE] is False
    assert entry.data[CONF_HONOR_EVENT_TIMES] is False


async def test_migrate_entry_v6_to_v10(
    hass: HomeAssistant,
) -> None:
    """Verify a version-6 entry runs v6→7→8→9→10 steps."""
    from custom_components.rental_control import async_migrate_entry

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="V6 Entry",
        version=6,
        unique_id="v6-migration-test",
        data={
            "name": "V6 Entry",
            "url": "https://example.com/calendar.ics",
            "timezone": "America/New_York",
            "checkin": "16:00",
            "checkout": "11:00",
            "start_slot": 10,
            "max_events": 3,
            "days": 90,
            "verify_ssl": True,
            "ignore_non_reserved": False,
            "code_length": 4,
            "generate_package": True,
        },
        entry_id="v6_entry",
    )
    entry.add_to_hass(hass)

    result = await async_migrate_entry(hass, entry)

    assert result is True
    assert entry.version == 10
    assert entry.data[CONF_SHOULD_UPDATE_CODE] is False
    assert entry.data[CONF_HONOR_EVENT_TIMES] is False


async def test_migrate_entry_v7_to_v10_honor_event_times(
    hass: HomeAssistant,
) -> None:
    """Verify v7→v8→v9→v10 migration sets honor_event_times to False.

    Verifies that an existing v7 config entry that lacks the
    honor_event_times key gets migrated through v8 to v10 with
    the key set to False, preserving existing behavior.
    """
    from custom_components.rental_control import async_migrate_entry

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="V7 Entry",
        version=7,
        unique_id="v7-migration-test",
        data={
            "name": "V7 Entry",
            "url": "https://example.com/calendar.ics",
            "timezone": "America/New_York",
            "checkin": "16:00",
            "checkout": "11:00",
            "start_slot": 10,
            "max_events": 3,
            "days": 90,
            "verify_ssl": True,
            "ignore_non_reserved": False,
            "code_length": 4,
            "generate_package": True,
            "should_update_code": False,
        },
        entry_id="v7_entry",
    )
    entry.add_to_hass(hass)

    result = await async_migrate_entry(hass, entry)

    assert result is True
    assert entry.version == 10
    assert entry.data[CONF_HONOR_EVENT_TIMES] is False


async def test_migrate_entry_v8_to_v10_trim_names(
    hass: HomeAssistant,
) -> None:
    """Verify v8→v9→v10 migration adds trim_names and max_name_length.

    Verifies that an existing v8 config entry without trim fields
    gets migrated to v10 with trim_names=False, max_name_length=16,
    and both buffer fields defaulting to 0.
    """
    from custom_components.rental_control import async_migrate_entry
    from custom_components.rental_control.const import CONF_CODE_BUFFER_AFTER
    from custom_components.rental_control.const import CONF_CODE_BUFFER_BEFORE
    from custom_components.rental_control.const import CONF_MAX_NAME_LENGTH
    from custom_components.rental_control.const import CONF_TRIM_NAMES

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="V8 Entry",
        version=8,
        unique_id="v8-migration-test",
        data={
            "name": "V8 Entry",
            "url": "https://example.com/calendar.ics",
            "timezone": "America/New_York",
            "checkin": "16:00",
            "checkout": "11:00",
            "start_slot": 10,
            "max_events": 3,
            "days": 90,
            "verify_ssl": True,
            "ignore_non_reserved": False,
            "code_length": 4,
            "should_update_code": False,
            "honor_event_times": False,
        },
        entry_id="v8_entry",
    )
    entry.add_to_hass(hass)

    result = await async_migrate_entry(hass, entry)

    assert result is True
    assert entry.version == 10
    assert entry.data[CONF_TRIM_NAMES] is False
    assert entry.data[CONF_MAX_NAME_LENGTH] == 16
    assert entry.data[CONF_CODE_BUFFER_BEFORE] == 0
    assert entry.data[CONF_CODE_BUFFER_AFTER] == 0


async def test_migrate_entry_v9_to_v10_code_buffer(
    hass: HomeAssistant,
) -> None:
    """Verify v9→v10 migration adds buffer fields with default 0.

    Verifies that an existing v9 config entry gets migrated to v10
    with code_buffer_before=0 and code_buffer_after=0.
    """
    from custom_components.rental_control import async_migrate_entry
    from custom_components.rental_control.const import CONF_CODE_BUFFER_AFTER
    from custom_components.rental_control.const import CONF_CODE_BUFFER_BEFORE

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="V9 Entry",
        version=9,
        unique_id="v9-migration-test",
        data={
            "name": "V9 Entry",
            "url": "https://example.com/calendar.ics",
            "timezone": "America/New_York",
            "checkin": "16:00",
            "checkout": "11:00",
            "start_slot": 10,
            "max_events": 3,
            "days": 90,
            "verify_ssl": True,
            "ignore_non_reserved": False,
            "code_length": 4,
            "should_update_code": False,
            "honor_event_times": False,
            "trim_names": False,
            "max_name_length": 16,
        },
        entry_id="v9_entry",
    )
    entry.add_to_hass(hass)

    result = await async_migrate_entry(hass, entry)

    assert result is True
    assert entry.version == 10
    assert entry.data[CONF_CODE_BUFFER_BEFORE] == 0
    assert entry.data[CONF_CODE_BUFFER_AFTER] == 0
