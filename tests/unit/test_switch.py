# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for KeymasterMonitoringSwitch (T021).

Tests cover:
- Entity creation with correct unique_id and entity_id pattern
- async_turn_on / async_turn_off toggle state
- RestoreEntity restores last on/off state
- Default state is off
- Switch is NOT created when coordinator.lockname is empty (FR-026)
- Switch IS created when coordinator.lockname is truthy
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock
from unittest.mock import patch

from custom_components.rental_control.const import COORDINATOR
from custom_components.rental_control.const import DOMAIN
from custom_components.rental_control.const import KEYMASTER_MONITORING_SWITCH
from custom_components.rental_control.const import UNSUB_LISTENERS
from custom_components.rental_control.switch import KeymasterMonitoringSwitch
from custom_components.rental_control.switch import async_setup_entry
from custom_components.rental_control.util import gen_uuid

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_coordinator(
    hass: HomeAssistant,
    lockname: str | None = "test_lock",
) -> MagicMock:
    """Create a mock coordinator for switch tests.

    Args:
        hass: Home Assistant instance.
        lockname: The keymaster lock name. None or empty means no keymaster.

    Returns:
        MagicMock: Mock coordinator with switch-relevant fields.
    """
    coordinator = MagicMock()
    coordinator.hass = hass
    coordinator.lockname = lockname
    coordinator.unique_id = "test-switch-unique-id"
    coordinator.name = "Test Rental"
    coordinator.start_slot = 10
    coordinator.max_events = 3
    coordinator.device_info = {
        "identifiers": {(DOMAIN, "test-switch-unique-id")},
        "name": "Test Rental",
        "sw_version": "0.0.0",
    }
    return coordinator


def _create_switch(
    hass: HomeAssistant,
    coordinator: MagicMock,
    config_entry: MockConfigEntry,
) -> KeymasterMonitoringSwitch:
    """Create a KeymasterMonitoringSwitch for testing without adding to hass.

    Sets up hass.data so async_added_to_hass can store the entity_id.

    Args:
        hass: Home Assistant instance.
        coordinator: Mock coordinator.
        config_entry: Mock config entry.

    Returns:
        KeymasterMonitoringSwitch: The switch entity.
    """
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault(
        config_entry.entry_id,
        {COORDINATOR: coordinator, UNSUB_LISTENERS: []},
    )
    switch = KeymasterMonitoringSwitch(coordinator, config_entry)
    switch.hass = hass
    switch.entity_id = "switch.test_rental_keymaster_monitoring"
    switch.async_write_ha_state = MagicMock()
    return switch


# ===========================================================================
# T021: KeymasterMonitoringSwitch entity tests
# ===========================================================================


class TestKeymasterMonitoringSwitchCreation:
    """Tests for KeymasterMonitoringSwitch entity creation (T021)."""

    async def test_unique_id_uses_gen_uuid(
        self,
        hass: HomeAssistant,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test unique_id is generated via gen_uuid with coordinator unique_id."""
        coordinator = _make_coordinator(hass)
        switch = _create_switch(hass, coordinator, mock_checkin_config_entry)

        expected = gen_uuid(f"{coordinator.unique_id} keymaster_monitoring")
        assert switch.unique_id == expected

    async def test_name_includes_coordinator_name(
        self,
        hass: HomeAssistant,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test name includes the coordinator name."""
        coordinator = _make_coordinator(hass)
        switch = _create_switch(hass, coordinator, mock_checkin_config_entry)

        assert "Test Rental" in switch.name
        assert "Keymaster Monitoring" in switch.name

    async def test_device_info_links_to_existing_device(
        self,
        hass: HomeAssistant,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test device_info links to the coordinator's device."""
        coordinator = _make_coordinator(hass)
        switch = _create_switch(hass, coordinator, mock_checkin_config_entry)

        assert switch.device_info == coordinator.device_info

    async def test_default_state_is_off(
        self,
        hass: HomeAssistant,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test default is_on state is False (off)."""
        coordinator = _make_coordinator(hass)
        switch = _create_switch(hass, coordinator, mock_checkin_config_entry)

        assert switch.is_on is False


class TestKeymasterMonitoringSwitchToggle:
    """Tests for async_turn_on/async_turn_off toggle (T021)."""

    async def test_turn_on_sets_is_on_true(
        self,
        hass: HomeAssistant,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test async_turn_on sets is_on to True."""
        coordinator = _make_coordinator(hass)
        switch = _create_switch(hass, coordinator, mock_checkin_config_entry)

        await switch.async_turn_on()

        assert switch.is_on is True
        switch.async_write_ha_state.assert_called()

    async def test_turn_off_sets_is_on_false(
        self,
        hass: HomeAssistant,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test async_turn_off sets is_on to False."""
        coordinator = _make_coordinator(hass)
        switch = _create_switch(hass, coordinator, mock_checkin_config_entry)

        # First turn on
        await switch.async_turn_on()
        assert switch.is_on is True

        # Then turn off
        await switch.async_turn_off()
        assert switch.is_on is False
        switch.async_write_ha_state.assert_called()

    async def test_turn_on_then_off_cycle(
        self,
        hass: HomeAssistant,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test full on/off/on toggle cycle."""
        coordinator = _make_coordinator(hass)
        switch = _create_switch(hass, coordinator, mock_checkin_config_entry)

        assert switch.is_on is False

        await switch.async_turn_on()
        assert switch.is_on is True

        await switch.async_turn_off()
        assert switch.is_on is False

        await switch.async_turn_on()
        assert switch.is_on is True


class TestKeymasterMonitoringSwitchRestore:
    """Tests for RestoreEntity state restoration (T021)."""

    async def test_restore_on_state(
        self,
        hass: HomeAssistant,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test RestoreEntity restores 'on' state from last known state."""
        coordinator = _make_coordinator(hass)
        switch = _create_switch(hass, coordinator, mock_checkin_config_entry)

        # Simulate RestoreEntity returning "on" state
        mock_state = MagicMock()
        mock_state.state = "on"

        with patch.object(switch, "async_get_last_state", return_value=mock_state):
            await switch.async_added_to_hass()

        assert switch.is_on is True

    async def test_restore_off_state(
        self,
        hass: HomeAssistant,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test RestoreEntity restores 'off' state from last known state."""
        coordinator = _make_coordinator(hass)
        switch = _create_switch(hass, coordinator, mock_checkin_config_entry)

        mock_state = MagicMock()
        mock_state.state = "off"

        with patch.object(switch, "async_get_last_state", return_value=mock_state):
            await switch.async_added_to_hass()

        assert switch.is_on is False

    async def test_restore_no_prior_state_defaults_off(
        self,
        hass: HomeAssistant,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test default is_on=False when no prior state exists."""
        coordinator = _make_coordinator(hass)
        switch = _create_switch(hass, coordinator, mock_checkin_config_entry)

        with patch.object(switch, "async_get_last_state", return_value=None):
            await switch.async_added_to_hass()

        assert switch.is_on is False

    async def test_added_to_hass_stores_entity_reference(
        self,
        hass: HomeAssistant,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test async_added_to_hass stores switch reference in hass.data."""
        coordinator = _make_coordinator(hass)
        switch = _create_switch(hass, coordinator, mock_checkin_config_entry)

        with patch.object(switch, "async_get_last_state", return_value=None):
            await switch.async_added_to_hass()

        stored = hass.data[DOMAIN][mock_checkin_config_entry.entry_id].get(
            KEYMASTER_MONITORING_SWITCH,
        )
        assert stored is switch


class TestSwitchPlatformSetup:
    """Tests for switch platform setup (FR-026 conditional creation)."""

    async def test_switch_not_created_when_lockname_empty(
        self,
        hass: HomeAssistant,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test switch entities are NOT created when coordinator.lockname is empty (FR-026)."""
        coordinator = _make_coordinator(hass, lockname=None)
        mock_checkin_config_entry.add_to_hass(hass)

        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][mock_checkin_config_entry.entry_id] = {
            COORDINATOR: coordinator,
            UNSUB_LISTENERS: [],
        }

        added_entities: list = []

        def mock_add_entities(entities: list) -> None:
            """Capture entities passed to async_add_entities."""
            added_entities.extend(entities)

        await async_setup_entry(hass, mock_checkin_config_entry, mock_add_entities)

        assert len(added_entities) == 0

    async def test_switch_not_created_when_lockname_empty_string(
        self,
        hass: HomeAssistant,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test switch entities are NOT created when coordinator.lockname is empty string."""
        coordinator = _make_coordinator(hass, lockname="")
        mock_checkin_config_entry.add_to_hass(hass)

        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][mock_checkin_config_entry.entry_id] = {
            COORDINATOR: coordinator,
            UNSUB_LISTENERS: [],
        }

        added_entities: list = []

        def mock_add_entities(entities: list) -> None:
            """Capture entities for empty-string lockname test."""
            added_entities.extend(entities)

        await async_setup_entry(hass, mock_checkin_config_entry, mock_add_entities)

        assert len(added_entities) == 0

    async def test_switch_created_when_lockname_truthy(
        self,
        hass: HomeAssistant,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test switch entities ARE created when coordinator.lockname is truthy."""
        coordinator = _make_coordinator(hass, lockname="my_lock")
        mock_checkin_config_entry.add_to_hass(hass)

        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][mock_checkin_config_entry.entry_id] = {
            COORDINATOR: coordinator,
            UNSUB_LISTENERS: [],
        }

        added_entities: list = []

        def mock_add_entities(entities: list) -> None:
            """Capture entities for truthy lockname test."""
            added_entities.extend(entities)

        await async_setup_entry(hass, mock_checkin_config_entry, mock_add_entities)

        # Should have KeymasterMonitoringSwitch (and EarlyCheckoutExpirySwitch
        # will be added in a later phase)
        monitoring_switches = [
            e for e in added_entities if isinstance(e, KeymasterMonitoringSwitch)
        ]
        assert len(monitoring_switches) == 1

    async def test_switch_count_when_lockname_set(
        self,
        hass: HomeAssistant,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test correct number of switch entities when lockname is configured."""
        coordinator = _make_coordinator(hass, lockname="my_lock")
        mock_checkin_config_entry.add_to_hass(hass)

        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][mock_checkin_config_entry.entry_id] = {
            COORDINATOR: coordinator,
            UNSUB_LISTENERS: [],
        }

        added_entities: list = []

        def mock_add_entities(entities: list) -> None:
            """Capture entities for switch count test."""
            added_entities.extend(entities)

        await async_setup_entry(hass, mock_checkin_config_entry, mock_add_entities)

        # At minimum, KeymasterMonitoringSwitch should be present
        assert len(added_entities) >= 1
