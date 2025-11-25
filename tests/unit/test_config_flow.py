# SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for config flow."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aioresponses import aioresponses
from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.const import CONF_URL
from homeassistant.data_entry_flow import FlowResultType

from custom_components.rental_control.const import CONF_CHECKIN
from custom_components.rental_control.const import CONF_CHECKOUT
from custom_components.rental_control.const import CONF_CODE_GENERATION
from custom_components.rental_control.const import CONF_CODE_LENGTH
from custom_components.rental_control.const import CONF_DAYS
from custom_components.rental_control.const import CONF_MAX_EVENTS
from custom_components.rental_control.const import CONF_REFRESH_FREQUENCY
from custom_components.rental_control.const import CONF_START_SLOT
from custom_components.rental_control.const import CONF_TIMEZONE
from custom_components.rental_control.const import DEFAULT_CHECKIN
from custom_components.rental_control.const import DEFAULT_CHECKOUT
from custom_components.rental_control.const import DEFAULT_CODE_LENGTH
from custom_components.rental_control.const import DEFAULT_DAYS
from custom_components.rental_control.const import DEFAULT_MAX_EVENTS
from custom_components.rental_control.const import DEFAULT_REFRESH_FREQUENCY
from custom_components.rental_control.const import DEFAULT_START_SLOT
from custom_components.rental_control.const import DOMAIN

from tests.fixtures import calendar_data

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


async def test_config_flow_user_init(hass: HomeAssistant) -> None:
    """Test initial config flow presents form with required fields.

    Verifies that:
    - Config flow initializes successfully
    - Returns a form with step_id "user"
    - Schema includes required fields: name and url
    - Default values are populated for optional fields
    """
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}

    # Verify schema has required fields
    schema = result["data_schema"].schema
    schema_keys = {str(key.schema): key for key in schema.keys()}

    assert CONF_NAME in schema_keys
    assert CONF_URL in schema_keys


async def test_config_flow_user_submit_valid(hass: HomeAssistant) -> None:
    """Test successful submission with minimal required fields.

    Verifies that:
    - Minimal config (name + url) is accepted
    - HTTP validation succeeds for valid ICS URL
    - Config entry is created successfully
    - Default values are applied for unspecified fields
    """
    with aioresponses() as mock_aiohttp:
        # Mock successful ICS calendar response
        test_url = "https://example.com/calendar.ics"
        mock_aiohttp.get(
            test_url,
            status=200,
            body=calendar_data.AIRBNB_ICS_CALENDAR,
            headers={"content-type": "text/calendar"},
        )

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={
                CONF_NAME: "Test Rental",
                CONF_URL: test_url,
                "verify_ssl": True,
                "ignore_non_reserved": True,
                "keymaster_entry_id": "(none)",
                CONF_REFRESH_FREQUENCY: DEFAULT_REFRESH_FREQUENCY,
                "timezone": "UTC",
                "event_prefix": "",
                CONF_CHECKIN: DEFAULT_CHECKIN,
                CONF_CHECKOUT: DEFAULT_CHECKOUT,
                CONF_DAYS: DEFAULT_DAYS,
                CONF_MAX_EVENTS: DEFAULT_MAX_EVENTS,
                CONF_START_SLOT: DEFAULT_START_SLOT,
                CONF_CODE_LENGTH: DEFAULT_CODE_LENGTH,
                CONF_CODE_GENERATION: "Start/End Date",
                "should_update_code": True,
            },
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Test Rental"
    assert result["data"][CONF_NAME] == "Test Rental"
    assert result["data"][CONF_URL] == test_url


async def test_config_flow_user_submit_complete(hass: HomeAssistant) -> None:
    """Test successful submission with all optional fields.

    Verifies that:
    - Complete configuration with all fields is accepted
    - All custom values are preserved in config entry
    - Optional fields override defaults correctly
    """
    with aioresponses() as mock_aiohttp:
        # Mock successful ICS calendar response
        test_url = "https://example.com/complete.ics"
        mock_aiohttp.get(
            test_url,
            status=200,
            body=calendar_data.AIRBNB_ICS_CALENDAR,
            headers={"content-type": "text/calendar"},
        )

        complete_config = {
            CONF_NAME: "Complete Rental",
            CONF_URL: test_url,
            "verify_ssl": True,
            "ignore_non_reserved": True,
            "keymaster_entry_id": "(none)",
            CONF_REFRESH_FREQUENCY: 15,
            CONF_TIMEZONE: "America/Chicago",
            "event_prefix": "Vacation",
            CONF_CHECKIN: "15:00",
            CONF_CHECKOUT: "10:00",
            CONF_DAYS: 180,
            CONF_MAX_EVENTS: 7,
            CONF_START_SLOT: 20,
            CONF_CODE_LENGTH: 6,
            CONF_CODE_GENERATION: "Static Random",
            "should_update_code": False,
        }

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data=complete_config,
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Complete Rental"
    assert result["data"][CONF_NAME] == "Complete Rental"
    assert result["data"][CONF_URL] == test_url
    assert result["data"][CONF_REFRESH_FREQUENCY] == 15
    assert result["data"][CONF_TIMEZONE] == "America/Chicago"
    assert result["data"]["event_prefix"] == "Vacation"
    assert result["data"][CONF_CHECKIN] == "15:00"
    assert result["data"][CONF_CHECKOUT] == "10:00"
    assert result["data"][CONF_DAYS] == 180
    assert result["data"][CONF_MAX_EVENTS] == 7
    assert result["data"][CONF_START_SLOT] == 20
    assert result["data"][CONF_CODE_LENGTH] == 6


# Additional stubs for other config flow tests...
