# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for startup readability helpers."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import State
import homeassistant.util.dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.rental_control.const import COORDINATOR
from custom_components.rental_control.const import DOMAIN
from custom_components.rental_control.const import UNSUB_LISTENERS
from custom_components.rental_control.startup_readability import (
    _all_managed_slots_readable,
)
from custom_components.rental_control.startup_readability import (
    _is_readable_keymaster_state,
)
from custom_components.rental_control.startup_readability import (
    _managed_slot_readability_entity_ids,
)
from custom_components.rental_control.startup_readability import (
    _needs_startup_readability_refresh,
)
from custom_components.rental_control.startup_readability import (
    async_arm_startup_readability_refresh,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


def _lock_config_entry(entry_id: str) -> MockConfigEntry:
    """Return a config entry for startup readability tests."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Startup Rental",
        version=10,
        unique_id=f"{entry_id}-unique",
        data={
            "name": "Startup Rental",
            "url": "https://example.com/calendar.ics",
            "timezone": "UTC",
            "checkin": "16:00",
            "checkout": "11:00",
            "start_slot": 10,
            "max_events": 1,
            "days": 90,
            "verify_ssl": True,
            "ignore_non_reserved": False,
            "honor_event_times": False,
            "lock_entry": "front_door",
            "refresh_frequency": 30,
            "code_buffer_before": 0,
            "code_buffer_after": 0,
        },
        entry_id=entry_id,
    )


def _coordinator(
    lockname: str | None = "front_door",
    *,
    start_slot: int = 10,
    max_events: int = 1,
) -> MagicMock:
    """Return a coordinator mock with startup readability attributes."""
    coordinator = MagicMock()
    coordinator.lockname = lockname
    coordinator.start_slot = start_slot
    coordinator.max_events = max_events
    coordinator.async_refresh = AsyncMock()
    return coordinator


def _prepare_entry(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    coordinator: MagicMock,
) -> None:
    """Store the coordinator and listener cleanup list in hass data."""
    entry.add_to_hass(hass)
    hass.data[DOMAIN] = {
        entry.entry_id: {
            COORDINATOR: coordinator,
            UNSUB_LISTENERS: [],
        },
    }


def _set_slot_readability_states(
    hass: HomeAssistant,
    *,
    name: str = STATE_UNKNOWN,
    pin: str = STATE_UNKNOWN,
    enabled: str = "off",
) -> None:
    """Set watched Keymaster entity states for one managed slot."""
    hass.states.async_set("text.front_door_code_slot_10_name", name)
    hass.states.async_set("text.front_door_code_slot_10_pin", pin)
    hass.states.async_set("switch.front_door_code_slot_10_enabled", enabled)


def test_managed_slot_readability_entity_ids() -> None:
    """Verify lockless entries and managed slot ranges produce the same IDs."""
    assert _managed_slot_readability_entity_ids(_coordinator(None)) == []

    assert _managed_slot_readability_entity_ids(
        _coordinator(start_slot=10, max_events=2)
    ) == [
        "text.front_door_code_slot_10_name",
        "text.front_door_code_slot_10_pin",
        "switch.front_door_code_slot_10_enabled",
        "text.front_door_code_slot_11_name",
        "text.front_door_code_slot_11_pin",
        "switch.front_door_code_slot_11_enabled",
    ]


def test_readable_keymaster_state_rules() -> None:
    """Verify missing and unavailable states remain the only unreadable states."""
    assert not _is_readable_keymaster_state(None)
    assert not _is_readable_keymaster_state(
        State("text.front_door_code_slot_10_name", STATE_UNAVAILABLE)
    )
    assert _is_readable_keymaster_state(
        State("text.front_door_code_slot_10_name", STATE_UNKNOWN)
    )
    assert _is_readable_keymaster_state(
        State("switch.front_door_code_slot_10_enabled", "off")
    )


def test_startup_readability_decision(hass: HomeAssistant) -> None:
    """Verify all-readable, unreadable, no-entity, and lockless decisions."""
    coordinator = _coordinator()
    entity_ids = _managed_slot_readability_entity_ids(coordinator)

    assert not _all_managed_slots_readable(hass, entity_ids)
    needs_refresh, watched = _needs_startup_readability_refresh(hass, coordinator)
    assert needs_refresh
    assert watched == entity_ids

    _set_slot_readability_states(hass)
    assert _all_managed_slots_readable(hass, entity_ids)
    needs_refresh, watched = _needs_startup_readability_refresh(hass, coordinator)
    assert not needs_refresh
    assert watched == entity_ids

    hass.states.async_set("text.front_door_code_slot_10_pin", STATE_UNAVAILABLE)
    needs_refresh, watched = _needs_startup_readability_refresh(hass, coordinator)
    assert needs_refresh
    assert watched == entity_ids

    lockless_refresh, lockless_watched = _needs_startup_readability_refresh(
        hass, _coordinator(None)
    )
    assert not lockless_refresh
    assert lockless_watched == []


async def test_direct_arm_missed_transition_refreshes(
    hass: HomeAssistant,
) -> None:
    """Verify startup-unreadable slots refresh when already readable at arm."""
    entry = _lock_config_entry("startup_readable_entry")
    coordinator = _coordinator()
    _prepare_entry(hass, entry, coordinator)
    _set_slot_readability_states(hass)

    async_arm_startup_readability_refresh(
        hass,
        entry,
        coordinator,
        startup_slots_unreadable=True,
    )

    assert len(hass.data[DOMAIN][entry.entry_id][UNSUB_LISTENERS]) == 1

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=3))
    await hass.async_block_till_done()

    coordinator.async_refresh.assert_awaited_once()
    assert hass.data[DOMAIN][entry.entry_id][UNSUB_LISTENERS] == []


async def test_readable_transitions_debounce_once(
    hass: HomeAssistant,
) -> None:
    """Verify readable transitions schedule debounce without readable storms."""
    entry = _lock_config_entry("transition_entry")
    coordinator = _coordinator()
    _prepare_entry(hass, entry, coordinator)
    _set_slot_readability_states(
        hass,
        name=STATE_UNAVAILABLE,
        pin=STATE_UNAVAILABLE,
        enabled=STATE_UNAVAILABLE,
    )
    watchdog_unsub = MagicMock()
    first_timer_unsub = MagicMock()
    second_timer_unsub = MagicMock()

    with patch(
        "custom_components.rental_control.startup_readability.async_call_later",
        side_effect=[watchdog_unsub, first_timer_unsub, second_timer_unsub],
    ) as call_later:
        async_arm_startup_readability_refresh(hass, entry, coordinator)
        hass.states.async_set("text.front_door_code_slot_10_name", STATE_UNKNOWN)
        await hass.async_block_till_done()
        hass.states.async_set("text.front_door_code_slot_10_name", "Guest")
        await hass.async_block_till_done()
        hass.states.async_set("text.front_door_code_slot_10_pin", STATE_UNKNOWN)
        await hass.async_block_till_done()
        hass.data[DOMAIN][entry.entry_id][UNSUB_LISTENERS][0]()

    assert call_later.call_count == 3
    first_timer_unsub.assert_called_once_with()
    second_timer_unsub.assert_called_once_with()
    watchdog_unsub.assert_called_once_with()


async def test_watchdog_expiry_removes_watcher(
    hass: HomeAssistant,
    caplog,
) -> None:
    """Verify watchdog expiration logs and removes cleanup references."""
    entry = _lock_config_entry("watchdog_entry")
    coordinator = _coordinator()
    _prepare_entry(hass, entry, coordinator)
    _set_slot_readability_states(
        hass,
        name=STATE_UNAVAILABLE,
        pin=STATE_UNAVAILABLE,
        enabled=STATE_UNAVAILABLE,
    )
    caplog.set_level(logging.DEBUG, logger="custom_components.rental_control")

    async_arm_startup_readability_refresh(hass, entry, coordinator)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=11))
    await hass.async_block_till_done()

    coordinator.async_refresh.assert_not_awaited()
    assert hass.data[DOMAIN][entry.entry_id][UNSUB_LISTENERS] == []
    assert (
        "Startup readability watcher expired for watchdog_entry before slots settled"
        in caplog.text
    )


async def test_missing_entry_data_skips_refresh(
    hass: HomeAssistant,
) -> None:
    """Verify entry removal before the refresh coroutine skips coordinator refresh."""
    entry = _lock_config_entry("missing_entry_data")
    coordinator = _coordinator()
    _prepare_entry(hass, entry, coordinator)
    _set_slot_readability_states(hass)

    async_arm_startup_readability_refresh(
        hass,
        entry,
        coordinator,
        startup_slots_unreadable=True,
    )
    hass.data[DOMAIN].pop(entry.entry_id)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=3))
    await hass.async_block_till_done()

    coordinator.async_refresh.assert_not_awaited()


async def test_refresh_exception_is_logged(
    hass: HomeAssistant,
    caplog,
) -> None:
    """Verify coordinator refresh exceptions remain logged and non-fatal."""
    entry = _lock_config_entry("refresh_exception_entry")
    coordinator = _coordinator()
    coordinator.async_refresh.side_effect = RuntimeError("boom")
    _prepare_entry(hass, entry, coordinator)
    _set_slot_readability_states(hass)
    caplog.set_level(logging.ERROR, logger="custom_components.rental_control")

    async_arm_startup_readability_refresh(
        hass,
        entry,
        coordinator,
        startup_slots_unreadable=True,
    )
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=3))
    await hass.async_block_till_done()

    coordinator.async_refresh.assert_awaited_once()
    assert (
        "Startup readability refresh failed for refresh_exception_entry" in caplog.text
    )
