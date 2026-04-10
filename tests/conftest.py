# SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Shared pytest fixtures for Rental Control integration tests."""

from __future__ import annotations

from datetime import time
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from aioresponses import aioresponses
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rental_control.const import CONF_CLEANING_WINDOW
from custom_components.rental_control.const import DEFAULT_CLEANING_WINDOW
from custom_components.rental_control.const import DOMAIN

from tests.fixtures import calendar_data

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


# Enable custom components for testing
pytest_plugins = ("pytest_homeassistant_custom_component",)

_TESTS_ROOT = Path(__file__).parent


def pytest_collection_modifyitems(
    config: pytest.Config,  # noqa: ARG001
    items: list[pytest.Item],
) -> None:
    """Auto-apply 'unit' or 'integration' markers based on test path."""
    for item in items:
        try:
            rel = item.path.relative_to(_TESTS_ROOT)
        except ValueError:
            continue
        parts = rel.parts
        if parts and parts[0] == "unit":
            item.add_marker(pytest.mark.unit)
        elif parts and parts[0] == "integration":
            item.add_marker(pytest.mark.integration)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations for all tests automatically.

    This fixture uses pytest-homeassistant-custom-component's
    enable_custom_integrations fixture and applies it to all tests.
    """
    yield


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a mock config entry with minimal configuration.

    Returns:
        MockConfigEntry: Mock configuration entry for testing.
    """
    return MockConfigEntry(
        domain=DOMAIN,
        title="Test Rental",
        version=7,
        unique_id="test-unique-id",
        data={
            "name": "Test Rental",
            "url": "https://example.com/calendar.ics",
            "timezone": "America/New_York",
            "checkin": "16:00",
            "checkout": "11:00",
            "start_slot": 10,
            "max_events": 3,
            "days": 90,
            "verify_ssl": True,
            "ignore_non_reserved": False,
        },
        options={
            "refresh_frequency": 5,
        },
        entry_id="test_entry_id",
    )


@pytest.fixture
def mock_config_entry_full() -> MockConfigEntry:
    """Return a mock config entry with complete configuration.

    Returns:
        MockConfigEntry: Mock configuration entry with all options.
    """
    return MockConfigEntry(
        domain=DOMAIN,
        title="Complete Rental",
        version=7,
        unique_id="test-full-unique-id",
        data={
            "name": "Complete Rental",
            "url": "https://example.com/calendar.ics",
            "verify_ssl": True,
        },
        options={
            "refresh_frequency": 15,
            "max_events": 5,
            "days": 365,
            "checkin": "16:00",
            "checkout": "11:00",
            "code_generation": "date_based",
            "code_length": 4,
            "start_slot": 10,
            "event_prefix": "Rental",
            "timezone": "America/New_York",
            "ignore_non_reserved": True,
            "should_update_code": True,
        },
        entry_id="test_entry_full_id",
    )


@pytest.fixture
def mock_aiohttp_session() -> aioresponses:
    """Return a mock aiohttp session for testing HTTP requests.

    Returns:
        aioresponses: Mock aiohttp client session context manager.
    """
    with aioresponses() as mock_session:
        yield mock_session


@pytest.fixture
async def setup_integration(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> MockConfigEntry:
    """Set up the Rental Control integration with a mock config entry.

    Args:
        hass: Home Assistant instance.
        mock_config_entry: Mock configuration entry.

    Returns:
        MockConfigEntry: The configured mock entry.
    """
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    return mock_config_entry


@pytest.fixture
def valid_ics_calendar() -> str:
    """Return a valid ICS calendar string for testing.

    Returns:
        str: Valid Airbnb-format ICS calendar with 2 events.
    """
    return calendar_data.AIRBNB_ICS_CALENDAR


@pytest.fixture
def mock_calendar_url(mock_aiohttp_session: aioresponses) -> aioresponses:
    """Mock calendar URL responses using aioresponses.

    Args:
        mock_aiohttp_session: aioresponses mock session.

    Returns:
        aioresponses: Configured mock session for calendar URL.
    """
    mock_aiohttp_session.get(
        "https://example.com/calendar.ics",
        status=200,
        body=calendar_data.AIRBNB_ICS_CALENDAR,
    )
    return mock_aiohttp_session


@pytest.fixture
def mock_checkin_coordinator(
    hass: HomeAssistant,
) -> MagicMock:
    """Return a mock coordinator for checkin sensor tests.

    The coordinator has configurable event data, lockname, start_slot,
    max_events, checkin/checkout times, and unique_id. Event data
    defaults to an empty list.

    Args:
        hass: Home Assistant instance.

    Returns:
        MagicMock: Mock coordinator with sensible defaults.
    """
    coordinator = MagicMock()
    coordinator.hass = hass
    coordinator.data = []
    coordinator.last_update_success = True
    coordinator.lockname = None
    coordinator.monitored_locknames = frozenset()
    coordinator.start_slot = 10
    coordinator.max_events = 3
    coordinator.checkin = time(16, 0)
    coordinator.checkout = time(11, 0)
    coordinator.event_prefix = ""
    coordinator.unique_id = "test-checkin-unique-id"
    coordinator.name = "Test Rental"
    coordinator.device_info = {
        "identifiers": {(DOMAIN, "test-checkin-unique-id")},
        "name": "Test Rental",
        "sw_version": "0.0.0",
    }
    return coordinator


@pytest.fixture
def mock_checkin_config_entry() -> MockConfigEntry:
    """Return a mock config entry with cleaning window in options.

    Returns:
        MockConfigEntry: Mock configuration entry for checkin testing.
    """
    return MockConfigEntry(
        domain=DOMAIN,
        title="Test Rental",
        version=7,
        unique_id="test-checkin-unique-id",
        data={
            "name": "Test Rental",
            "url": "https://example.com/calendar.ics",
            "timezone": "America/New_York",
            "checkin": "16:00",
            "checkout": "11:00",
            "start_slot": 10,
            "max_events": 3,
            "days": 90,
            "verify_ssl": True,
            "ignore_non_reserved": False,
        },
        options={
            "refresh_frequency": 5,
            CONF_CLEANING_WINDOW: DEFAULT_CLEANING_WINDOW,
        },
        entry_id="test_checkin_entry_id",
    )
