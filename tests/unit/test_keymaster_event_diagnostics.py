# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the keymaster event diagnostics ring buffer.

Covers the opt-in diagnostic facility that records the disposition
of keymaster_lock_state_changed events seen by the listener. See
issue #525 for context.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from custom_components.rental_control import async_register_keymaster_listener
from custom_components.rental_control.const import CHECKIN_SENSOR
from custom_components.rental_control.const import (
    CONF_ENABLE_KEYMASTER_EVENT_DIAGNOSTICS,
)
from custom_components.rental_control.const import COORDINATOR
from custom_components.rental_control.const import DOMAIN
from custom_components.rental_control.const import KEYMASTER_MONITORING_SWITCH
from custom_components.rental_control.const import UNSUB_LISTENERS
from custom_components.rental_control.sensors.checkinsensor import CheckinTrackingSensor

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry


def _setup_entry(
    hass: HomeAssistant,
    coordinator: MagicMock,
    config_entry: MockConfigEntry,
    *,
    monitoring_on: bool = True,
    include_sensor: bool = True,
) -> MagicMock:
    """Wire coordinator/sensor/switch into ``hass.data`` for the listener.

    Returns the mock checkin sensor (so tests can assert forwarding).
    """
    coordinator.keymaster_event_diagnostics = deque(maxlen=10)
    coordinator.lockname = "front_door"
    coordinator.monitored_locknames = frozenset({"front_door"})
    coordinator.start_slot = 10
    coordinator.max_events = 3

    sensor = MagicMock()
    config_entry.add_to_hass(hass)
    hass.data.setdefault(DOMAIN, {})
    entry_data: dict = {
        COORDINATOR: coordinator,
        UNSUB_LISTENERS: [],
        KEYMASTER_MONITORING_SWITCH: MagicMock(is_on=monitoring_on),
    }
    if include_sensor:
        entry_data[CHECKIN_SENSOR] = sensor
    hass.data[DOMAIN][config_entry.entry_id] = entry_data
    return sensor


def _set_diag(config_entry: MockConfigEntry, hass: HomeAssistant, value: bool) -> None:
    """Toggle the diagnostics option on the config entry's data dict."""
    new_data = dict(config_entry.data)
    new_data[CONF_ENABLE_KEYMASTER_EVENT_DIAGNOSTICS] = value
    hass.config_entries.async_update_entry(config_entry, data=new_data)


class TestDiagnosticsBuffer:
    """Tests for the coordinator ring buffer population."""

    async def test_disabled_buffer_not_populated(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Buffer must be empty when option is off (zero-overhead path)."""
        _setup_entry(hass, mock_checkin_coordinator, mock_checkin_config_entry)
        async_register_keymaster_listener(hass, mock_checkin_config_entry)

        hass.bus.async_fire(
            "keymaster_lock_state_changed",
            {"lockname": "front_door", "state": "unlocked", "code_slot_num": 11},
        )
        await hass.async_block_till_done()

        assert len(mock_checkin_coordinator.keymaster_event_diagnostics) == 0

    async def test_accepted_disposition(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """A fully-valid forwarded event is recorded as accepted."""
        sensor = _setup_entry(hass, mock_checkin_coordinator, mock_checkin_config_entry)
        _set_diag(mock_checkin_config_entry, hass, True)
        async_register_keymaster_listener(hass, mock_checkin_config_entry)

        hass.bus.async_fire(
            "keymaster_lock_state_changed",
            {"lockname": "Front Door", "state": "unlocked", "code_slot_num": 11},
        )
        await hass.async_block_till_done()

        sensor.async_handle_keymaster_unlock.assert_called_once()
        buf = list(mock_checkin_coordinator.keymaster_event_diagnostics)
        assert len(buf) == 1
        entry = buf[0]
        assert entry["disposition"] == "accepted"
        assert entry["lockname"] == "Front Door"
        assert entry["lockname_slug"] == "front_door"
        assert entry["state"] == "unlocked"
        assert entry["code_slot_num"] == 11
        # Timestamp is parseable as ISO-8601
        datetime.fromisoformat(entry["timestamp"])

    @pytest.mark.parametrize(
        ("event_data", "expected"),
        [
            (
                {
                    "lockname": "back_door",
                    "state": "unlocked",
                    "code_slot_num": 11,
                },
                "rejected_not_monitored",
            ),
            (
                {
                    "lockname": "front_door",
                    "state": "locked",
                    "code_slot_num": 11,
                },
                "rejected_state",
            ),
            (
                {
                    "lockname": "front_door",
                    "state": "unlocked",
                    "code_slot_num": 0,
                },
                "rejected_slot_zero",
            ),
            (
                {
                    "lockname": "front_door",
                    "state": "unlocked",
                    "code_slot_num": 99,
                },
                "rejected_out_of_range",
            ),
        ],
    )
    async def test_rejected_dispositions(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
        event_data: dict,
        expected: str,
    ) -> None:
        """Each early-return path records the matching disposition."""
        _setup_entry(hass, mock_checkin_coordinator, mock_checkin_config_entry)
        _set_diag(mock_checkin_config_entry, hass, True)
        async_register_keymaster_listener(hass, mock_checkin_config_entry)

        hass.bus.async_fire("keymaster_lock_state_changed", event_data)
        await hass.async_block_till_done()

        buf = list(mock_checkin_coordinator.keymaster_event_diagnostics)
        assert len(buf) == 1
        assert buf[0]["disposition"] == expected

    async def test_rejected_no_checkin_sensor(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Missing CHECKIN_SENSOR records rejected_no_checkin_sensor."""
        _setup_entry(
            hass,
            mock_checkin_coordinator,
            mock_checkin_config_entry,
            include_sensor=False,
        )
        _set_diag(mock_checkin_config_entry, hass, True)
        async_register_keymaster_listener(hass, mock_checkin_config_entry)

        hass.bus.async_fire(
            "keymaster_lock_state_changed",
            {"lockname": "front_door", "state": "unlocked", "code_slot_num": 11},
        )
        await hass.async_block_till_done()

        buf = list(mock_checkin_coordinator.keymaster_event_diagnostics)
        assert len(buf) == 1
        assert buf[0]["disposition"] == "rejected_no_checkin_sensor"

    async def test_rejected_monitoring_off(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Monitoring switch off records rejected_monitoring_off."""
        _setup_entry(
            hass,
            mock_checkin_coordinator,
            mock_checkin_config_entry,
            monitoring_on=False,
        )
        _set_diag(mock_checkin_config_entry, hass, True)
        async_register_keymaster_listener(hass, mock_checkin_config_entry)

        hass.bus.async_fire(
            "keymaster_lock_state_changed",
            {"lockname": "front_door", "state": "unlocked", "code_slot_num": 11},
        )
        await hass.async_block_till_done()

        buf = list(mock_checkin_coordinator.keymaster_event_diagnostics)
        assert len(buf) == 1
        assert buf[0]["disposition"] == "rejected_monitoring_off"

    async def test_ring_buffer_truncates_at_ten(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Twelve events leave exactly the last 10 in the buffer."""
        _setup_entry(hass, mock_checkin_coordinator, mock_checkin_config_entry)
        _set_diag(mock_checkin_config_entry, hass, True)
        async_register_keymaster_listener(hass, mock_checkin_config_entry)

        for i in range(12):
            # Vary lockname so we can identify the dropped ones; all
            # take the rejected_not_monitored path.
            hass.bus.async_fire(
                "keymaster_lock_state_changed",
                {"lockname": f"other_{i}", "state": "unlocked", "code_slot_num": 11},
            )
        await hass.async_block_till_done()

        buf = list(mock_checkin_coordinator.keymaster_event_diagnostics)
        assert len(buf) == 10
        assert buf[0]["lockname"] == "other_2"
        assert buf[-1]["lockname"] == "other_11"

    async def test_toggle_takes_effect_immediately(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Toggling the option mid-flight is observed without re-register."""
        _setup_entry(hass, mock_checkin_coordinator, mock_checkin_config_entry)
        async_register_keymaster_listener(hass, mock_checkin_config_entry)

        # First event with diagnostics off
        hass.bus.async_fire(
            "keymaster_lock_state_changed",
            {"lockname": "front_door", "state": "unlocked", "code_slot_num": 11},
        )
        await hass.async_block_till_done()
        assert len(mock_checkin_coordinator.keymaster_event_diagnostics) == 0

        # Enable diagnostics; do NOT re-register the listener
        _set_diag(mock_checkin_config_entry, hass, True)

        hass.bus.async_fire(
            "keymaster_lock_state_changed",
            {"lockname": "front_door", "state": "unlocked", "code_slot_num": 11},
        )
        await hass.async_block_till_done()
        buf = list(mock_checkin_coordinator.keymaster_event_diagnostics)
        assert len(buf) == 1
        assert buf[0]["disposition"] == "accepted"

    async def test_recording_writes_sensor_state(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Recording a disposition must refresh the sensor's HA state.

        Without ``async_write_ha_state`` the new diagnostic entry would
        not surface in ``hass.states`` until the next coordinator
        refresh, defeating the purpose of the opt-in attribute.
        """
        sensor = _setup_entry(hass, mock_checkin_coordinator, mock_checkin_config_entry)
        _set_diag(mock_checkin_config_entry, hass, True)
        async_register_keymaster_listener(hass, mock_checkin_config_entry)

        # An event taking a rejected path must still write state
        hass.bus.async_fire(
            "keymaster_lock_state_changed",
            {"lockname": "back_door", "state": "unlocked", "code_slot_num": 11},
        )
        await hass.async_block_till_done()
        sensor.async_write_ha_state.assert_called()
        sensor.async_write_ha_state.reset_mock()

        # And the accepted path also writes state
        hass.bus.async_fire(
            "keymaster_lock_state_changed",
            {"lockname": "front_door", "state": "unlocked", "code_slot_num": 11},
        )
        await hass.async_block_till_done()
        sensor.async_write_ha_state.assert_called()

    @pytest.mark.parametrize("bad_lockname", [None, 123])
    async def test_non_string_lockname_defensive(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
        bad_lockname: object,
    ) -> None:
        """Non-string lockname values do not crash and are coerced."""
        _setup_entry(hass, mock_checkin_coordinator, mock_checkin_config_entry)
        _set_diag(mock_checkin_config_entry, hass, True)
        async_register_keymaster_listener(hass, mock_checkin_config_entry)

        hass.bus.async_fire(
            "keymaster_lock_state_changed",
            {
                "lockname": bad_lockname,
                "state": "unlocked",
                "code_slot_num": 11,
            },
        )
        await hass.async_block_till_done()

        buf = list(mock_checkin_coordinator.keymaster_event_diagnostics)
        assert len(buf) == 1
        assert buf[0]["lockname"] == str(bad_lockname)
        assert buf[0]["lockname_slug"] == ""
        assert buf[0]["disposition"] == "rejected_not_monitored"


class TestDiagnosticsAttribute:
    """Tests for the sensor's extra_state_attributes exposure."""

    def _make_sensor(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> CheckinTrackingSensor:
        """Construct a sensor without triggering coordinator side effects."""
        mock_checkin_coordinator.keymaster_event_diagnostics = deque(maxlen=10)
        mock_checkin_config_entry.add_to_hass(hass)
        sensor = CheckinTrackingSensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )
        sensor.entity_id = "sensor.test_rental_checkin"
        return sensor

    async def test_attribute_absent_when_disabled(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Without the option, the attribute key is omitted entirely."""
        sensor = self._make_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )
        assert "keymaster_event_diagnostics" not in sensor.extra_state_attributes

    async def test_attribute_present_when_enabled(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """With the option enabled, the attribute snapshots the buffer."""
        sensor = self._make_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )
        _set_diag(mock_checkin_config_entry, hass, True)
        mock_checkin_coordinator.keymaster_event_diagnostics.append(
            {
                "timestamp": "2026-05-15T13:08:11+00:00",
                "lockname": "Front Door",
                "lockname_slug": "front_door",
                "state": "unlocked",
                "code_slot_num": 11,
                "disposition": "accepted",
            }
        )
        attrs = sensor.extra_state_attributes
        assert "keymaster_event_diagnostics" in attrs
        assert isinstance(attrs["keymaster_event_diagnostics"], list)
        assert attrs["keymaster_event_diagnostics"][0]["disposition"] == "accepted"
