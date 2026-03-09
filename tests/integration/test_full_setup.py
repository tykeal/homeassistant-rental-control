# SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Integration tests for full setup and teardown of Rental Control.

These tests verify the end-to-end integration lifecycle: loading with
various configurations, verifying entity and device creation, platform
forwarding, and clean unload behavior.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from aioresponses import aioresponses
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rental_control.const import COORDINATOR
from custom_components.rental_control.const import DOMAIN
from custom_components.rental_control.const import PLATFORMS
from custom_components.rental_control.const import UNSUB_LISTENERS

from tests.fixtures import calendar_data

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_calendar_response(
    mock_session: aioresponses,
    url: str = "https://example.com/calendar.ics",
    body: str = calendar_data.AIRBNB_ICS_CALENDAR,
    *,
    repeat: bool = True,
) -> None:
    """Register a mock GET response for a calendar URL."""
    mock_session.get(url, status=200, body=body, repeat=repeat)


# ---------------------------------------------------------------------------
# T104 – minimal configuration
# ---------------------------------------------------------------------------


async def test_integration_setup_minimal_config(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Verify integration loads with minimal required configuration.

    Uses the ``mock_config_entry`` fixture which carries only the
    mandatory fields (name, url, timezone, checkin/checkout, start_slot,
    max_events, days, verify_ssl, ignore_non_reserved).
    """
    mock_config_entry.add_to_hass(hass)

    with aioresponses() as mock_session:
        _mock_calendar_response(mock_session, mock_config_entry.data["url"])

        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert DOMAIN in hass.data
    assert mock_config_entry.entry_id in hass.data[DOMAIN]
    assert COORDINATOR in hass.data[DOMAIN][mock_config_entry.entry_id]


# ---------------------------------------------------------------------------
# T105 – complete configuration
# ---------------------------------------------------------------------------


async def test_integration_setup_complete_config(
    hass: HomeAssistant,
) -> None:
    """Verify integration loads with all configuration options set.

    Creates a config entry with all fields in ``data`` (as the
    coordinator reads from ``config_entry.data``, not ``options``).
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Complete Rental",
        version=7,
        unique_id="test-complete-unique-id",
        data={
            "name": "Complete Rental",
            "url": "https://example.com/calendar.ics",
            "timezone": "America/New_York",
            "checkin": "16:00",
            "checkout": "11:00",
            "start_slot": 10,
            "max_events": 5,
            "days": 365,
            "verify_ssl": True,
            "ignore_non_reserved": True,
            "code_generation": "date_based",
            "code_length": 4,
            "should_update_code": True,
            "event_prefix": "Rental",
        },
        entry_id="test_entry_full_id",
    )
    entry.add_to_hass(hass)

    with aioresponses() as mock_session:
        _mock_calendar_response(mock_session, entry.data["url"])

        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id][COORDINATOR]
    assert coordinator.code_generator == "date_based"
    assert coordinator.code_length == 4
    assert coordinator.ignore_non_reserved is True


# ---------------------------------------------------------------------------
# T106 – entity creation
# ---------------------------------------------------------------------------


async def test_entities_created(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Verify all expected entities appear in the entity registry.

    With max_events=3 the integration should create 3 sensor entities
    and 1 calendar entity (4 entities total).
    """
    from homeassistant.helpers import entity_registry as er

    mock_config_entry.add_to_hass(hass)

    with aioresponses() as mock_session:
        _mock_calendar_response(mock_session, mock_config_entry.data["url"])

        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    ent_reg = er.async_get(hass)
    entities = er.async_entries_for_config_entry(ent_reg, mock_config_entry.entry_id)

    # 1 calendar + max_events (3) sensors
    assert len(entities) == 4

    domains = {e.domain for e in entities}
    assert "calendar" in domains
    assert "sensor" in domains

    sensor_entities = [e for e in entities if e.domain == "sensor"]
    assert len(sensor_entities) == 3


# ---------------------------------------------------------------------------
# T107 – coordinator accessible
# ---------------------------------------------------------------------------


async def test_coordinator_initialized(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Verify coordinator is created and accessible via hass.data."""
    from custom_components.rental_control.coordinator import RentalControlCoordinator

    mock_config_entry.add_to_hass(hass)

    with aioresponses() as mock_session:
        _mock_calendar_response(mock_session, mock_config_entry.data["url"])

        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][mock_config_entry.entry_id][COORDINATOR]
    assert isinstance(coordinator, RentalControlCoordinator)
    assert coordinator.name == "Test Rental"
    assert coordinator.url == mock_config_entry.data["url"]
    assert coordinator.max_events == 3


# ---------------------------------------------------------------------------
# T108 – platforms loaded
# ---------------------------------------------------------------------------


async def test_platforms_loaded(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Verify sensor and calendar platforms are loaded."""
    mock_config_entry.add_to_hass(hass)

    with aioresponses() as mock_session:
        _mock_calendar_response(mock_session, mock_config_entry.data["url"])

        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert DOMAIN in hass.config.components
    for platform in PLATFORMS:
        assert platform in hass.config.components


# ---------------------------------------------------------------------------
# T109 – device registry
# ---------------------------------------------------------------------------


async def test_device_registry_entry(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Verify a device is registered for the integration entry."""
    from homeassistant.helpers import device_registry as dr

    mock_config_entry.add_to_hass(hass)

    with aioresponses() as mock_session:
        _mock_calendar_response(mock_session, mock_config_entry.data["url"])

        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    dev_reg = dr.async_get(hass)
    devices = dr.async_entries_for_config_entry(dev_reg, mock_config_entry.entry_id)

    assert len(devices) == 1
    device = devices[0]
    assert device.name == "Test Rental"
    assert (DOMAIN, mock_config_entry.unique_id) in device.identifiers


# ---------------------------------------------------------------------------
# T110 – clean unload
# ---------------------------------------------------------------------------


async def test_integration_unload(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Verify clean unload removes entry from hass.data."""

    mock_config_entry.add_to_hass(hass)

    with aioresponses() as mock_session:
        _mock_calendar_response(mock_session, mock_config_entry.data["url"])

        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    # Verify entry exists before unload
    assert mock_config_entry.entry_id in hass.data[DOMAIN]

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # Entry should be removed from hass.data
    assert mock_config_entry.entry_id not in hass.data.get(DOMAIN, {})


async def test_integration_unload_clears_listeners(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Verify unload clears the listener subscriptions list."""
    mock_config_entry.add_to_hass(hass)

    with aioresponses() as mock_session:
        _mock_calendar_response(mock_session, mock_config_entry.data["url"])

        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    # Listener list is present before unload
    assert UNSUB_LISTENERS in hass.data[DOMAIN][mock_config_entry.entry_id]

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.entry_id not in hass.data.get(DOMAIN, {})
