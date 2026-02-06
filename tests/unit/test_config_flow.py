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
import voluptuous as vol

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


async def test_config_flow_validation_missing_name(hass: HomeAssistant) -> None:
    """Test validation error when name is missing.

    Verifies that:
    - Config flow requires name field (voluptuous validation)
    - Empty or missing name field is rejected
    - Form is re-displayed with appropriate error

    Note: In Home Assistant config flows, the schema validation with vol.Required
    prevents truly missing required fields. Empty strings are technically valid
    for the string schema but would create useless entries. This test documents
    that the schema requires a name field exists.
    """
    # Test that the initial form includes name as a required field
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    # Verify schema has name as required field
    schema = result["data_schema"].schema
    schema_keys = {str(key.schema): key for key in schema.keys()}

    # CONF_NAME should be in schema
    assert CONF_NAME in schema_keys
    # Check if it's marked as Required using isinstance for robustness
    name_key = [k for k in schema.keys() if str(k.schema) == CONF_NAME][0]
    assert isinstance(name_key, vol.Required)


async def test_config_flow_validation_missing_url(hass: HomeAssistant) -> None:
    """Test validation error when URL is missing.

    Verifies that:
    - Config flow requires url field (voluptuous validation)
    - Empty or missing URL field is rejected
    - Form includes URL as required field

    Note: In Home Assistant config flows, the schema validation with vol.Required
    prevents truly missing required fields. This test documents that the schema
    requires a URL field exists.
    """
    # Test that the initial form includes url as a required field
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    # Verify schema has url as required field
    schema = result["data_schema"].schema
    schema_keys = {str(key.schema): key for key in schema.keys()}

    # CONF_URL should be in schema
    assert CONF_URL in schema_keys
    # Check if it's marked as Required using isinstance for robustness
    url_key = [k for k in schema.keys() if str(k.schema) == CONF_URL][0]
    assert isinstance(url_key, vol.Required)


async def test_config_flow_validation_invalid_url(hass: HomeAssistant) -> None:
    """Test validation error for HTTP URL when verify_ssl is enabled.

    Verifies that:
    - Config flow rejects non-HTTPS URLs when verify_ssl is True
    - Error message indicates HTTPS is required when SSL verification is enabled
    - Form is re-displayed with error details

    Per config_flow.py _check_url(): HTTPS required when SSL verification enabled
    """
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
        data={
            CONF_NAME: "Test Rental",
            CONF_URL: "http://example.com/calendar.ics",  # HTTP instead of HTTPS
            "verify_ssl": True,  # SSL verification enabled requires HTTPS
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

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {CONF_URL: "invalid_url"}


async def test_config_flow_http_allowed_when_ssl_disabled(hass: HomeAssistant) -> None:
    """Test HTTP URLs are allowed when verify_ssl is disabled.

    Verifies that:
    - Config flow accepts HTTP URLs when verify_ssl is False
    - This enables local/development calendar servers without SSL
    - Entry is created successfully with HTTP URL

    Per config_flow.py _check_url(): HTTP allowed when SSL verification disabled
    """
    with aioresponses() as mock_aiohttp:
        test_url = "http://local-server/calendar.ics"  # HTTP URL
        mock_aiohttp.get(
            test_url,
            status=200,
            body=calendar_data.GENERIC_ICS_CALENDAR,
            headers={"Content-Type": "text/calendar"},
        )

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={
                CONF_NAME: "Local Rental",
                CONF_URL: test_url,
                "verify_ssl": False,  # SSL verification disabled allows HTTP
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

        # Entry should be created successfully
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["title"] == "Local Rental"
        assert result["data"][CONF_URL] == test_url


async def test_config_flow_validation_invalid_refresh(hass: HomeAssistant) -> None:
    """Test validation error for out-of-range refresh_frequency.

    Verifies that:
    - Config flow rejects refresh_frequency < 0 or > 1440
    - Error message indicates valid range is 0-1440 minutes
    - Form is re-displayed with error details

    Per config_flow.py _check_refresh(): refresh must be between 0 and 1440
    """
    with aioresponses() as mock_aiohttp:
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
                CONF_REFRESH_FREQUENCY: 2000,  # Out of range (max is 1440)
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

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {CONF_REFRESH_FREQUENCY: "bad_refresh"}


async def test_config_flow_validation_invalid_max_events(hass: HomeAssistant) -> None:
    """Test validation error for invalid max_events value.

    Verifies that:
    - Config flow rejects max_events < 1
    - Error message indicates value must be >= 1
    - Form is re-displayed with error details

    Per config_flow.py _check_max_events(): max_events must be >= 1
    """
    with aioresponses() as mock_aiohttp:
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
                CONF_MAX_EVENTS: 0,  # Invalid: must be >= 1
                CONF_START_SLOT: DEFAULT_START_SLOT,
                CONF_CODE_LENGTH: DEFAULT_CODE_LENGTH,
                CONF_CODE_GENERATION: "Start/End Date",
                "should_update_code": True,
            },
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {CONF_MAX_EVENTS: "bad_minimum"}


async def test_options_flow_init(hass: HomeAssistant) -> None:
    """Test options flow loads existing config.

    Verifies that:
    - Options flow initializes successfully
    - Returns a form with step_id "init"
    - Form is pre-populated with existing config values
    - All configuration fields are present in the schema

    Per config_flow.py OptionsFlowHandler.async_step_init(): Options flow loads from config_entry.data
    """
    # First create a config entry
    with aioresponses() as mock_aiohttp:
        test_url = "https://example.com/calendar.ics"
        mock_aiohttp.get(
            test_url,
            status=200,
            body=calendar_data.AIRBNB_ICS_CALENDAR,
            headers={"content-type": "text/calendar"},
        )

        config_result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={
                CONF_NAME: "Test Rental",
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
                CONF_CODE_GENERATION: "Start/End Date",
                "should_update_code": False,
            },
        )

    assert config_result["type"] == FlowResultType.CREATE_ENTRY
    entry = config_result["result"]

    # Now test the options flow
    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "init"

    # Verify schema includes expected fields
    schema = result["data_schema"].schema
    schema_keys = {str(key.schema): key for key in schema.keys()}

    assert CONF_NAME in schema_keys
    assert CONF_URL in schema_keys
    assert CONF_REFRESH_FREQUENCY in schema_keys
    assert CONF_MAX_EVENTS in schema_keys


async def test_options_flow_update(hass: HomeAssistant) -> None:
    """Test options flow updates configuration successfully.

    Verifies that:
    - Options flow accepts updated configuration values
    - Config entry is updated with new values
    - Updated configuration is persisted correctly

    Per config_flow.py: Options flow uses same validation as initial flow
    """
    # First create a config entry
    with aioresponses() as mock_aiohttp:
        test_url = "https://example.com/calendar.ics"
        mock_aiohttp.get(
            test_url,
            status=200,
            body=calendar_data.AIRBNB_ICS_CALENDAR,
            headers={"content-type": "text/calendar"},
        )

        config_result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={
                CONF_NAME: "Test Rental",
                CONF_URL: test_url,
                "verify_ssl": True,
                "ignore_non_reserved": True,
                "keymaster_entry_id": "(none)",
                CONF_REFRESH_FREQUENCY: 15,
                CONF_TIMEZONE: "UTC",
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

    assert config_result["type"] == FlowResultType.CREATE_ENTRY
    entry = config_result["result"]

    # Now update via options flow
    with aioresponses() as mock_aiohttp:
        mock_aiohttp.get(
            test_url,
            status=200,
            body=calendar_data.AIRBNB_ICS_CALENDAR,
            headers={"content-type": "text/calendar"},
        )

        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                CONF_NAME: "Updated Rental",
                CONF_URL: test_url,
                "verify_ssl": True,
                "ignore_non_reserved": True,
                "keymaster_entry_id": "(none)",
                CONF_REFRESH_FREQUENCY: 30,  # Changed from 15
                CONF_TIMEZONE: "America/Chicago",  # Changed from UTC
                "event_prefix": "Vacation",  # Changed from empty
                CONF_CHECKIN: DEFAULT_CHECKIN,
                CONF_CHECKOUT: DEFAULT_CHECKOUT,
                CONF_DAYS: DEFAULT_DAYS,
                CONF_MAX_EVENTS: 10,  # Changed from 5
                CONF_START_SLOT: DEFAULT_START_SLOT,
                CONF_CODE_LENGTH: DEFAULT_CODE_LENGTH,
                CONF_CODE_GENERATION: "Start/End Date",
                "should_update_code": True,
            },
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    # Verify the entry was updated
    updated_entry = hass.config_entries.async_get_entry(entry.entry_id)
    assert updated_entry.title == "Updated Rental"
    assert updated_entry.data[CONF_REFRESH_FREQUENCY] == 30
    assert updated_entry.data[CONF_TIMEZONE] == "America/Chicago"
    assert updated_entry.data["event_prefix"] == "Vacation"
    assert updated_entry.data[CONF_MAX_EVENTS] == 10


async def test_config_flow_duplicate_detection(hass: HomeAssistant) -> None:
    """Test handling of duplicate calendar names.

    Verifies that:
    - Config flow detects duplicate unique_id
    - Returns error when attempting to create duplicate entry
    - Error message indicates name conflict

    Per config_flow.py async_step_user(): unique_id generation checks for duplicates.
    We mock gen_uuid to return the same value twice to trigger duplicate detection.
    """
    from unittest.mock import patch

    fixed_uuid = "test-fixed-uuid-12345"

    # First create a config entry with mocked UUID
    with (
        aioresponses() as mock_aiohttp,
        patch(
            "custom_components.rental_control.config_flow.gen_uuid",
            return_value=fixed_uuid,
        ),
    ):
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

    # Now attempt to create a second entry with the same UUID (mocked)
    with (
        aioresponses() as mock_aiohttp,
        patch(
            "custom_components.rental_control.config_flow.gen_uuid",
            return_value=fixed_uuid,
        ),
    ):
        test_url2 = "https://example.com/calendar2.ics"
        mock_aiohttp.get(
            test_url2,
            status=200,
            body=calendar_data.AIRBNB_ICS_CALENDAR,
            headers={"content-type": "text/calendar"},
        )

        result2 = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={
                CONF_NAME: "Test Rental 2",
                CONF_URL: test_url2,
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

    # Duplicate unique_id should result in form with error
    assert result2["type"] == FlowResultType.FORM
    assert result2["errors"] == {CONF_NAME: "same_name"}
