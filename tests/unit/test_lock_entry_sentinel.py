# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for consistent handling of the ``"(none)"`` lock sentinel."""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rental_control import _entry_has_lock
from custom_components.rental_control.allocator.recovery import configured_lockname
from custom_components.rental_control.allocator.recovery import entry_has_lock
from custom_components.rental_control.const import CONF_LOCK_ENTRY
from custom_components.rental_control.coordinator import RentalControlCoordinator
from custom_components.rental_control.helpers import configured_lock_entry
from custom_components.rental_control.helpers import configured_lock_slug

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

LOCKLESS_VALUES = [None, "", "   ", "(none)", " (none) ", 42]


@pytest.mark.parametrize("value", LOCKLESS_VALUES)
def test_lockless_values_agree_everywhere(value: object) -> None:
    """Every lock-presence check treats sentinel and blank values as no lock."""
    data = MappingProxyType({CONF_LOCK_ENTRY: value})
    entry = MagicMock(data=data)

    assert configured_lock_entry(value) is None
    assert configured_lock_slug(value) is None
    assert entry_has_lock(data) is False
    assert configured_lockname(data) is None
    assert _entry_has_lock(entry) is False


def test_real_lock_values_agree_everywhere() -> None:
    """A real lock name is a lock for every check, slugified consistently."""
    data = MappingProxyType({CONF_LOCK_ENTRY: "Front Door"})
    entry = MagicMock(data=data)

    assert configured_lock_entry("Front Door") == "Front Door"
    assert configured_lock_slug("Front Door") == "front_door"
    assert entry_has_lock(data) is True
    assert configured_lockname(data) == "front_door"
    assert _entry_has_lock(entry) is True


async def test_coordinator_init_treats_sentinel_as_lockless(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A stored ``"(none)"`` makes the coordinator lockless."""
    data = dict(mock_config_entry.data)
    data[CONF_LOCK_ENTRY] = "(none)"
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(mock_config_entry, data=data)

    coordinator = RentalControlCoordinator(hass, mock_config_entry)

    assert coordinator.lockname is None
    assert coordinator.event_overrides is None


async def test_update_config_treats_sentinel_as_lock_removal(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Updating a locked entry to ``"(none)"`` removes the lock."""
    data = dict(mock_config_entry.data)
    data[CONF_LOCK_ENTRY] = "Front Door"
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(mock_config_entry, data=data)
    coordinator = RentalControlCoordinator(hass, mock_config_entry)
    assert coordinator.lockname == "front_door"
    assert coordinator.event_overrides is not None

    config = dict(mock_config_entry.data)
    config.update(mock_config_entry.options)
    config[CONF_LOCK_ENTRY] = "(none)"
    with patch.object(coordinator, "async_request_refresh", new_callable=AsyncMock):
        await coordinator.update_config(config)

    assert coordinator.lockname is None
    assert coordinator.event_overrides is None
