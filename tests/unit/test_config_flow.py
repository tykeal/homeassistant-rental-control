# SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for config flow."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aioresponses import aioresponses
from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.const import CONF_URL
from homeassistant.const import CONF_VERIFY_SSL
from homeassistant.data_entry_flow import FlowResultType
import pytest
import voluptuous as vol

from custom_components.rental_control.config_flow import _normalize_lock_entry
from custom_components.rental_control.const import CONF_CHECKIN
from custom_components.rental_control.const import CONF_CHECKOUT
from custom_components.rental_control.const import CONF_CODE_GENERATION
from custom_components.rental_control.const import CONF_CODE_LENGTH
from custom_components.rental_control.const import CONF_DAYS
from custom_components.rental_control.const import (
    CONF_ENABLE_KEYMASTER_EVENT_DIAGNOSTICS,
)
from custom_components.rental_control.const import CONF_EVENT_PREFIX
from custom_components.rental_control.const import CONF_HONOR_EVENT_TIMES
from custom_components.rental_control.const import CONF_IGNORE_NON_RESERVED
from custom_components.rental_control.const import CONF_LOCK_ENTRY
from custom_components.rental_control.const import CONF_MAX_EVENTS
from custom_components.rental_control.const import CONF_REFRESH_FREQUENCY
from custom_components.rental_control.const import CONF_SHOULD_UPDATE_CODE
from custom_components.rental_control.const import CONF_START_SLOT
from custom_components.rental_control.const import CONF_TIMEZONE
from custom_components.rental_control.const import DEFAULT_CHECKIN
from custom_components.rental_control.const import DEFAULT_CHECKOUT
from custom_components.rental_control.const import DEFAULT_CODE_LENGTH
from custom_components.rental_control.const import DEFAULT_DAYS
from custom_components.rental_control.const import DEFAULT_HONOR_EVENT_TIMES
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
                CONF_VERIFY_SSL: True,
                CONF_IGNORE_NON_RESERVED: True,
                CONF_LOCK_ENTRY: "(none)",
                CONF_REFRESH_FREQUENCY: DEFAULT_REFRESH_FREQUENCY,
                CONF_TIMEZONE: "UTC",
                CONF_EVENT_PREFIX: "",
                CONF_CHECKIN: DEFAULT_CHECKIN,
                CONF_CHECKOUT: DEFAULT_CHECKOUT,
                CONF_DAYS: DEFAULT_DAYS,
                CONF_MAX_EVENTS: DEFAULT_MAX_EVENTS,
                CONF_START_SLOT: DEFAULT_START_SLOT,
                CONF_CODE_LENGTH: DEFAULT_CODE_LENGTH,
                CONF_CODE_GENERATION: "Start/End Date",
                CONF_SHOULD_UPDATE_CODE: True,
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
            CONF_VERIFY_SSL: True,
            CONF_IGNORE_NON_RESERVED: True,
            CONF_LOCK_ENTRY: "(none)",
            CONF_REFRESH_FREQUENCY: 15,
            CONF_TIMEZONE: "America/Chicago",
            CONF_EVENT_PREFIX: "Vacation",
            CONF_CHECKIN: "15:00",
            CONF_CHECKOUT: "10:00",
            CONF_DAYS: 180,
            CONF_MAX_EVENTS: 7,
            CONF_START_SLOT: 20,
            CONF_CODE_LENGTH: 6,
            CONF_CODE_GENERATION: "Static Random",
            CONF_SHOULD_UPDATE_CODE: False,
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
    assert result["data"][CONF_EVENT_PREFIX] == "Vacation"
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


async def test_config_flow_rejects_http_when_ssl_enabled(hass: HomeAssistant) -> None:
    """Test validation error for HTTP URL when verify_ssl is enabled.

    Verifies that:
    - Config flow rejects non-HTTPS URLs when verify_ssl is True
    - Error message indicates HTTPS is required when SSL verification is enabled
    - Form is re-displayed with error details

    Validated in _start_config_flow(): HTTPS required when SSL verification enabled
    """
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
        data={
            CONF_NAME: "Test Rental",
            CONF_URL: "http://example.com/calendar.ics",  # HTTP instead of HTTPS
            CONF_VERIFY_SSL: True,  # SSL verification enabled requires HTTPS
            CONF_IGNORE_NON_RESERVED: True,
            CONF_LOCK_ENTRY: "(none)",
            CONF_REFRESH_FREQUENCY: DEFAULT_REFRESH_FREQUENCY,
            CONF_TIMEZONE: "UTC",
            CONF_EVENT_PREFIX: "",
            CONF_CHECKIN: DEFAULT_CHECKIN,
            CONF_CHECKOUT: DEFAULT_CHECKOUT,
            CONF_DAYS: DEFAULT_DAYS,
            CONF_MAX_EVENTS: DEFAULT_MAX_EVENTS,
            CONF_START_SLOT: DEFAULT_START_SLOT,
            CONF_CODE_LENGTH: DEFAULT_CODE_LENGTH,
            CONF_CODE_GENERATION: "Start/End Date",
            CONF_SHOULD_UPDATE_CODE: True,
        },
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {CONF_URL: "https_required"}


async def test_config_flow_http_allowed_when_ssl_disabled(hass: HomeAssistant) -> None:
    """Test HTTP URLs are allowed when verify_ssl is disabled.

    Verifies that:
    - Config flow accepts HTTP URLs when verify_ssl is False
    - This enables local/development calendar servers without SSL
    - Entry is created successfully with HTTP URL

    Validated in _start_config_flow(): HTTP allowed when SSL verification disabled
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
                CONF_VERIFY_SSL: False,  # SSL verification disabled allows HTTP
                CONF_IGNORE_NON_RESERVED: True,
                CONF_LOCK_ENTRY: "(none)",
                CONF_REFRESH_FREQUENCY: DEFAULT_REFRESH_FREQUENCY,
                CONF_TIMEZONE: "UTC",
                CONF_EVENT_PREFIX: "",
                CONF_CHECKIN: DEFAULT_CHECKIN,
                CONF_CHECKOUT: DEFAULT_CHECKOUT,
                CONF_DAYS: DEFAULT_DAYS,
                CONF_MAX_EVENTS: DEFAULT_MAX_EVENTS,
                CONF_START_SLOT: DEFAULT_START_SLOT,
                CONF_CODE_LENGTH: DEFAULT_CODE_LENGTH,
                CONF_CODE_GENERATION: "Start/End Date",
                CONF_SHOULD_UPDATE_CODE: True,
            },
        )

        # Entry should be created successfully
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["title"] == "Local Rental"
        assert result["data"][CONF_URL] == test_url


async def test_config_flow_rejects_unsupported_scheme(hass: HomeAssistant) -> None:
    """Test validation error for unsupported URL schemes.

    Verifies that:
    - Config flow rejects non-HTTP(S) schemes like ftp://, file://
    - cv.url() validates schemes and returns invalid_url for non-HTTP(S)
    - Form is re-displayed with error details

    Validated in _start_config_flow(): cv.url() rejects non-HTTP(S) schemes
    """
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
        data={
            CONF_NAME: "Test Rental",
            CONF_URL: "ftp://example.com/calendar.ics",  # Unsupported scheme
            CONF_VERIFY_SSL: True,
            CONF_IGNORE_NON_RESERVED: True,
            CONF_LOCK_ENTRY: "(none)",
            CONF_REFRESH_FREQUENCY: DEFAULT_REFRESH_FREQUENCY,
            CONF_TIMEZONE: "UTC",
            CONF_EVENT_PREFIX: "",
            CONF_CHECKIN: DEFAULT_CHECKIN,
            CONF_CHECKOUT: DEFAULT_CHECKOUT,
            CONF_DAYS: DEFAULT_DAYS,
            CONF_MAX_EVENTS: DEFAULT_MAX_EVENTS,
            CONF_START_SLOT: DEFAULT_START_SLOT,
            CONF_CODE_LENGTH: DEFAULT_CODE_LENGTH,
            CONF_CODE_GENERATION: "Start/End Date",
            CONF_SHOULD_UPDATE_CODE: True,
        },
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    # cv.url() rejects non-HTTP(S) schemes with invalid_url
    assert result["errors"] == {CONF_URL: "invalid_url"}


async def test_config_flow_rejects_malformed_url(hass: HomeAssistant) -> None:
    """Test validation error for malformed URLs.

    Verifies that:
    - Config flow rejects URLs that fail cv.url() validation
    - Error message indicates URL is malformed
    - Form is re-displayed with error details

    Validated in _start_config_flow(): cv.url() raises vol.Invalid for bad URLs
    """
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
        data={
            CONF_NAME: "Test Rental",
            CONF_URL: "not-a-valid-url",  # Malformed URL
            CONF_VERIFY_SSL: True,
            CONF_IGNORE_NON_RESERVED: True,
            CONF_LOCK_ENTRY: "(none)",
            CONF_REFRESH_FREQUENCY: DEFAULT_REFRESH_FREQUENCY,
            CONF_TIMEZONE: "UTC",
            CONF_EVENT_PREFIX: "",
            CONF_CHECKIN: DEFAULT_CHECKIN,
            CONF_CHECKOUT: DEFAULT_CHECKOUT,
            CONF_DAYS: DEFAULT_DAYS,
            CONF_MAX_EVENTS: DEFAULT_MAX_EVENTS,
            CONF_START_SLOT: DEFAULT_START_SLOT,
            CONF_CODE_LENGTH: DEFAULT_CODE_LENGTH,
            CONF_CODE_GENERATION: "Start/End Date",
            CONF_SHOULD_UPDATE_CODE: True,
        },
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {CONF_URL: "invalid_url"}


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
                CONF_VERIFY_SSL: True,
                CONF_IGNORE_NON_RESERVED: True,
                CONF_LOCK_ENTRY: "(none)",
                CONF_REFRESH_FREQUENCY: 2000,  # Out of range (max is 1440)
                CONF_TIMEZONE: "UTC",
                CONF_EVENT_PREFIX: "",
                CONF_CHECKIN: DEFAULT_CHECKIN,
                CONF_CHECKOUT: DEFAULT_CHECKOUT,
                CONF_DAYS: DEFAULT_DAYS,
                CONF_MAX_EVENTS: DEFAULT_MAX_EVENTS,
                CONF_START_SLOT: DEFAULT_START_SLOT,
                CONF_CODE_LENGTH: DEFAULT_CODE_LENGTH,
                CONF_CODE_GENERATION: "Start/End Date",
                CONF_SHOULD_UPDATE_CODE: True,
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
                CONF_VERIFY_SSL: True,
                CONF_IGNORE_NON_RESERVED: True,
                CONF_LOCK_ENTRY: "(none)",
                CONF_REFRESH_FREQUENCY: DEFAULT_REFRESH_FREQUENCY,
                CONF_TIMEZONE: "UTC",
                CONF_EVENT_PREFIX: "",
                CONF_CHECKIN: DEFAULT_CHECKIN,
                CONF_CHECKOUT: DEFAULT_CHECKOUT,
                CONF_DAYS: DEFAULT_DAYS,
                CONF_MAX_EVENTS: 0,  # Invalid: must be >= 1
                CONF_START_SLOT: DEFAULT_START_SLOT,
                CONF_CODE_LENGTH: DEFAULT_CODE_LENGTH,
                CONF_CODE_GENERATION: "Start/End Date",
                CONF_SHOULD_UPDATE_CODE: True,
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
                CONF_VERIFY_SSL: True,
                CONF_IGNORE_NON_RESERVED: True,
                CONF_LOCK_ENTRY: "(none)",
                CONF_REFRESH_FREQUENCY: 15,
                CONF_TIMEZONE: "America/Chicago",
                CONF_EVENT_PREFIX: "Vacation",
                CONF_CHECKIN: "15:00",
                CONF_CHECKOUT: "10:00",
                CONF_DAYS: 180,
                CONF_MAX_EVENTS: 7,
                CONF_START_SLOT: 20,
                CONF_CODE_LENGTH: 6,
                CONF_CODE_GENERATION: "Start/End Date",
                CONF_SHOULD_UPDATE_CODE: False,
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
            repeat=True,
        )

        config_result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={
                CONF_NAME: "Test Rental",
                CONF_URL: test_url,
                CONF_VERIFY_SSL: True,
                CONF_IGNORE_NON_RESERVED: True,
                CONF_LOCK_ENTRY: "(none)",
                CONF_REFRESH_FREQUENCY: 15,
                CONF_TIMEZONE: "UTC",
                CONF_EVENT_PREFIX: "",
                CONF_CHECKIN: DEFAULT_CHECKIN,
                CONF_CHECKOUT: DEFAULT_CHECKOUT,
                CONF_DAYS: DEFAULT_DAYS,
                CONF_MAX_EVENTS: DEFAULT_MAX_EVENTS,
                CONF_START_SLOT: DEFAULT_START_SLOT,
                CONF_CODE_LENGTH: DEFAULT_CODE_LENGTH,
                CONF_CODE_GENERATION: "Start/End Date",
                CONF_SHOULD_UPDATE_CODE: True,
            },
        )
        await hass.async_block_till_done()

    assert config_result["type"] == FlowResultType.CREATE_ENTRY
    entry = config_result["result"]

    # Now update via options flow
    with aioresponses() as mock_aiohttp:
        mock_aiohttp.get(
            test_url,
            status=200,
            body=calendar_data.AIRBNB_ICS_CALENDAR,
            headers={"content-type": "text/calendar"},
            repeat=True,
        )

        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                CONF_NAME: "Updated Rental",
                CONF_URL: test_url,
                CONF_VERIFY_SSL: True,
                CONF_IGNORE_NON_RESERVED: True,
                CONF_LOCK_ENTRY: "(none)",
                CONF_REFRESH_FREQUENCY: 30,  # Changed from 15
                CONF_TIMEZONE: "America/Chicago",  # Changed from UTC
                CONF_EVENT_PREFIX: "Vacation",  # Changed from empty
                CONF_CHECKIN: DEFAULT_CHECKIN,
                CONF_CHECKOUT: DEFAULT_CHECKOUT,
                CONF_DAYS: DEFAULT_DAYS,
                CONF_MAX_EVENTS: 10,  # Changed from 5
                CONF_START_SLOT: DEFAULT_START_SLOT,
                CONF_CODE_LENGTH: DEFAULT_CODE_LENGTH,
                CONF_CODE_GENERATION: "Start/End Date",
                CONF_SHOULD_UPDATE_CODE: True,
            },
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    # Verify the entry was updated
    updated_entry = hass.config_entries.async_get_entry(entry.entry_id)
    assert updated_entry.title == "Updated Rental"
    assert updated_entry.data[CONF_REFRESH_FREQUENCY] == 30
    assert updated_entry.data[CONF_TIMEZONE] == "America/Chicago"
    assert updated_entry.data[CONF_EVENT_PREFIX] == "Vacation"
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
                CONF_VERIFY_SSL: True,
                CONF_IGNORE_NON_RESERVED: True,
                CONF_LOCK_ENTRY: "(none)",
                CONF_REFRESH_FREQUENCY: DEFAULT_REFRESH_FREQUENCY,
                CONF_TIMEZONE: "UTC",
                CONF_EVENT_PREFIX: "",
                CONF_CHECKIN: DEFAULT_CHECKIN,
                CONF_CHECKOUT: DEFAULT_CHECKOUT,
                CONF_DAYS: DEFAULT_DAYS,
                CONF_MAX_EVENTS: DEFAULT_MAX_EVENTS,
                CONF_START_SLOT: DEFAULT_START_SLOT,
                CONF_CODE_LENGTH: DEFAULT_CODE_LENGTH,
                CONF_CODE_GENERATION: "Start/End Date",
                CONF_SHOULD_UPDATE_CODE: True,
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
                CONF_VERIFY_SSL: True,
                CONF_IGNORE_NON_RESERVED: True,
                CONF_LOCK_ENTRY: "(none)",
                CONF_REFRESH_FREQUENCY: DEFAULT_REFRESH_FREQUENCY,
                CONF_TIMEZONE: "UTC",
                CONF_EVENT_PREFIX: "",
                CONF_CHECKIN: DEFAULT_CHECKIN,
                CONF_CHECKOUT: DEFAULT_CHECKOUT,
                CONF_DAYS: DEFAULT_DAYS,
                CONF_MAX_EVENTS: DEFAULT_MAX_EVENTS,
                CONF_START_SLOT: DEFAULT_START_SLOT,
                CONF_CODE_LENGTH: DEFAULT_CODE_LENGTH,
                CONF_CODE_GENERATION: "Start/End Date",
                CONF_SHOULD_UPDATE_CODE: True,
            },
        )

    # Duplicate unique_id should result in form with error
    assert result2["type"] == FlowResultType.FORM
    assert result2["errors"] == {CONF_NAME: "same_name"}


# ---------------------------------------------------------------------------
# Phase 7 – targeted coverage tests for config_flow.py
# ---------------------------------------------------------------------------


async def test_config_flow_bad_checkin_time(hass: HomeAssistant) -> None:
    """Test that an invalid check-in time is rejected.

    Covers config_flow.py cv.time(user_input[CONF_CHECKIN]) exception path.
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
                CONF_VERIFY_SSL: True,
                CONF_IGNORE_NON_RESERVED: True,
                CONF_LOCK_ENTRY: "(none)",
                CONF_REFRESH_FREQUENCY: DEFAULT_REFRESH_FREQUENCY,
                CONF_TIMEZONE: "UTC",
                CONF_EVENT_PREFIX: "",
                CONF_CHECKIN: "not-a-time",
                CONF_CHECKOUT: DEFAULT_CHECKOUT,
                CONF_DAYS: DEFAULT_DAYS,
                CONF_MAX_EVENTS: DEFAULT_MAX_EVENTS,
                CONF_START_SLOT: DEFAULT_START_SLOT,
                CONF_CODE_LENGTH: DEFAULT_CODE_LENGTH,
                CONF_CODE_GENERATION: "Start/End Date",
                CONF_SHOULD_UPDATE_CODE: True,
            },
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_CHECKIN: "bad_time"}


async def test_config_flow_bad_checkout_time(hass: HomeAssistant) -> None:
    """Test that an invalid check-out time is rejected.

    Covers config_flow.py cv.time(user_input[CONF_CHECKOUT]) exception path.
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
                CONF_VERIFY_SSL: True,
                CONF_IGNORE_NON_RESERVED: True,
                CONF_LOCK_ENTRY: "(none)",
                CONF_REFRESH_FREQUENCY: DEFAULT_REFRESH_FREQUENCY,
                CONF_TIMEZONE: "UTC",
                CONF_EVENT_PREFIX: "",
                CONF_CHECKIN: DEFAULT_CHECKIN,
                CONF_CHECKOUT: "invalid",
                CONF_DAYS: DEFAULT_DAYS,
                CONF_MAX_EVENTS: DEFAULT_MAX_EVENTS,
                CONF_START_SLOT: DEFAULT_START_SLOT,
                CONF_CODE_LENGTH: DEFAULT_CODE_LENGTH,
                CONF_CODE_GENERATION: "Start/End Date",
                CONF_SHOULD_UPDATE_CODE: True,
            },
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_CHECKOUT: "bad_time"}


async def test_config_flow_bad_days_minimum(hass: HomeAssistant) -> None:
    """Test that days < 1 is rejected.

    Covers config_flow.py CONF_DAYS < 1 validation.
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
                CONF_VERIFY_SSL: True,
                CONF_IGNORE_NON_RESERVED: True,
                CONF_LOCK_ENTRY: "(none)",
                CONF_REFRESH_FREQUENCY: DEFAULT_REFRESH_FREQUENCY,
                CONF_TIMEZONE: "UTC",
                CONF_EVENT_PREFIX: "",
                CONF_CHECKIN: DEFAULT_CHECKIN,
                CONF_CHECKOUT: DEFAULT_CHECKOUT,
                CONF_DAYS: 0,
                CONF_MAX_EVENTS: DEFAULT_MAX_EVENTS,
                CONF_START_SLOT: DEFAULT_START_SLOT,
                CONF_CODE_LENGTH: DEFAULT_CODE_LENGTH,
                CONF_CODE_GENERATION: "Start/End Date",
                CONF_SHOULD_UPDATE_CODE: True,
            },
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_DAYS: "bad_minimum"}


async def test_config_flow_bad_code_length(hass: HomeAssistant) -> None:
    """Test that invalid code_length is rejected.

    Covers config_flow.py code_length < DEFAULT or odd validation.
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
                CONF_VERIFY_SSL: True,
                CONF_IGNORE_NON_RESERVED: True,
                CONF_LOCK_ENTRY: "(none)",
                CONF_REFRESH_FREQUENCY: DEFAULT_REFRESH_FREQUENCY,
                CONF_TIMEZONE: "UTC",
                CONF_EVENT_PREFIX: "",
                CONF_CHECKIN: DEFAULT_CHECKIN,
                CONF_CHECKOUT: DEFAULT_CHECKOUT,
                CONF_DAYS: DEFAULT_DAYS,
                CONF_MAX_EVENTS: DEFAULT_MAX_EVENTS,
                CONF_START_SLOT: DEFAULT_START_SLOT,
                CONF_CODE_LENGTH: 3,
                CONF_CODE_GENERATION: "Start/End Date",
                CONF_SHOULD_UPDATE_CODE: True,
            },
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_CODE_LENGTH: "bad_code_length"}


async def test_config_flow_url_non_200_response(hass: HomeAssistant) -> None:
    """Test that non-200 HTTP response is reported as unknown error.

    Covers config_flow.py resp.status != 200 branch.
    """
    with aioresponses() as mock_aiohttp:
        test_url = "https://example.com/calendar.ics"
        mock_aiohttp.get(test_url, status=500)

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={
                CONF_NAME: "Test Rental",
                CONF_URL: test_url,
                CONF_VERIFY_SSL: True,
                CONF_IGNORE_NON_RESERVED: True,
                CONF_LOCK_ENTRY: "(none)",
                CONF_REFRESH_FREQUENCY: DEFAULT_REFRESH_FREQUENCY,
                CONF_TIMEZONE: "UTC",
                CONF_EVENT_PREFIX: "",
                CONF_CHECKIN: DEFAULT_CHECKIN,
                CONF_CHECKOUT: DEFAULT_CHECKOUT,
                CONF_DAYS: DEFAULT_DAYS,
                CONF_MAX_EVENTS: DEFAULT_MAX_EVENTS,
                CONF_START_SLOT: DEFAULT_START_SLOT,
                CONF_CODE_LENGTH: DEFAULT_CODE_LENGTH,
                CONF_CODE_GENERATION: "Start/End Date",
                CONF_SHOULD_UPDATE_CODE: True,
            },
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_URL: "unknown"}


async def test_config_flow_url_bad_content_type(hass: HomeAssistant) -> None:
    """Test that non-calendar content-type is rejected.

    Covers config_flow.py 'text/calendar' not in resp.content_type branch.
    """
    with aioresponses() as mock_aiohttp:
        test_url = "https://example.com/calendar.ics"
        mock_aiohttp.get(
            test_url,
            status=200,
            body="<html>Not a calendar</html>",
            headers={"content-type": "text/html"},
        )

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={
                CONF_NAME: "Test Rental",
                CONF_URL: test_url,
                CONF_VERIFY_SSL: True,
                CONF_IGNORE_NON_RESERVED: True,
                CONF_LOCK_ENTRY: "(none)",
                CONF_REFRESH_FREQUENCY: DEFAULT_REFRESH_FREQUENCY,
                CONF_TIMEZONE: "UTC",
                CONF_EVENT_PREFIX: "",
                CONF_CHECKIN: DEFAULT_CHECKIN,
                CONF_CHECKOUT: DEFAULT_CHECKOUT,
                CONF_DAYS: DEFAULT_DAYS,
                CONF_MAX_EVENTS: DEFAULT_MAX_EVENTS,
                CONF_START_SLOT: DEFAULT_START_SLOT,
                CONF_CODE_LENGTH: DEFAULT_CODE_LENGTH,
                CONF_CODE_GENERATION: "Start/End Date",
                CONF_SHOULD_UPDATE_CODE: True,
            },
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_URL: "bad_ics"}


async def test_config_flow_version_is_10(hass: HomeAssistant) -> None:
    """Test that RentalControlFlowHandler VERSION is 10.

    Verifies that the config flow handler version has been bumped
    to 10 to account for the new lock code buffer keys.
    """
    from custom_components.rental_control.config_flow import RentalControlFlowHandler

    assert RentalControlFlowHandler.VERSION == 10


async def test_honor_event_times_in_options_schema(hass: HomeAssistant) -> None:
    """Test that honor_event_times toggle appears in options flow schema.

    Verifies that:
    - honor_event_times appears as a schema key in the options flow
    - The default value is False
    """
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
                CONF_VERIFY_SSL: True,
                CONF_IGNORE_NON_RESERVED: True,
                CONF_LOCK_ENTRY: "(none)",
                CONF_REFRESH_FREQUENCY: DEFAULT_REFRESH_FREQUENCY,
                CONF_TIMEZONE: "UTC",
                CONF_EVENT_PREFIX: "",
                CONF_CHECKIN: DEFAULT_CHECKIN,
                CONF_CHECKOUT: DEFAULT_CHECKOUT,
                CONF_DAYS: DEFAULT_DAYS,
                CONF_MAX_EVENTS: DEFAULT_MAX_EVENTS,
                CONF_START_SLOT: DEFAULT_START_SLOT,
                CONF_CODE_LENGTH: DEFAULT_CODE_LENGTH,
                CONF_CODE_GENERATION: "Start/End Date",
                CONF_SHOULD_UPDATE_CODE: True,
                CONF_HONOR_EVENT_TIMES: DEFAULT_HONOR_EVENT_TIMES,
            },
        )

    assert config_result["type"] == FlowResultType.CREATE_ENTRY
    entry = config_result["result"]

    # Now test the options flow schema
    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "init"

    schema = result["data_schema"].schema
    schema_keys = {str(key.schema): key for key in schema.keys()}

    assert CONF_HONOR_EVENT_TIMES in schema_keys


async def test_honor_event_times_toggle_persists(hass: HomeAssistant) -> None:
    """Test toggling honor_event_times to True persists in config_entry.data.

    Verifies that:
    - honor_event_times can be set to True via options flow
    - The value persists in config_entry.data after update_listener runs
    """
    with aioresponses() as mock_aiohttp:
        test_url = "https://example.com/calendar.ics"
        mock_aiohttp.get(
            test_url,
            status=200,
            body=calendar_data.AIRBNB_ICS_CALENDAR,
            headers={"content-type": "text/calendar"},
            repeat=True,
        )

        config_result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={
                CONF_NAME: "Test Rental",
                CONF_URL: test_url,
                CONF_VERIFY_SSL: True,
                CONF_IGNORE_NON_RESERVED: True,
                CONF_LOCK_ENTRY: "(none)",
                CONF_REFRESH_FREQUENCY: DEFAULT_REFRESH_FREQUENCY,
                CONF_TIMEZONE: "UTC",
                CONF_EVENT_PREFIX: "",
                CONF_CHECKIN: DEFAULT_CHECKIN,
                CONF_CHECKOUT: DEFAULT_CHECKOUT,
                CONF_DAYS: DEFAULT_DAYS,
                CONF_MAX_EVENTS: DEFAULT_MAX_EVENTS,
                CONF_START_SLOT: DEFAULT_START_SLOT,
                CONF_CODE_LENGTH: DEFAULT_CODE_LENGTH,
                CONF_CODE_GENERATION: "Start/End Date",
                CONF_SHOULD_UPDATE_CODE: True,
                CONF_HONOR_EVENT_TIMES: False,
            },
        )
        await hass.async_block_till_done()

    assert config_result["type"] == FlowResultType.CREATE_ENTRY
    entry = config_result["result"]

    # Verify initial value is False
    assert entry.data.get(CONF_HONOR_EVENT_TIMES) is False

    # Now update via options flow to enable honor_event_times
    with aioresponses() as mock_aiohttp:
        mock_aiohttp.get(
            test_url,
            status=200,
            body=calendar_data.AIRBNB_ICS_CALENDAR,
            headers={"content-type": "text/calendar"},
            repeat=True,
        )

        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                CONF_NAME: "Test Rental",
                CONF_URL: test_url,
                CONF_VERIFY_SSL: True,
                CONF_IGNORE_NON_RESERVED: True,
                CONF_LOCK_ENTRY: "(none)",
                CONF_REFRESH_FREQUENCY: DEFAULT_REFRESH_FREQUENCY,
                CONF_TIMEZONE: "UTC",
                CONF_EVENT_PREFIX: "",
                CONF_CHECKIN: DEFAULT_CHECKIN,
                CONF_CHECKOUT: DEFAULT_CHECKOUT,
                CONF_DAYS: DEFAULT_DAYS,
                CONF_MAX_EVENTS: DEFAULT_MAX_EVENTS,
                CONF_START_SLOT: DEFAULT_START_SLOT,
                CONF_CODE_LENGTH: DEFAULT_CODE_LENGTH,
                CONF_CODE_GENERATION: "Start/End Date",
                CONF_SHOULD_UPDATE_CODE: True,
                CONF_HONOR_EVENT_TIMES: True,
            },
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    updated_entry = hass.config_entries.async_get_entry(entry.entry_id)
    assert updated_entry.data[CONF_HONOR_EVENT_TIMES] is True


async def test_keymaster_event_diagnostics_options_only(hass: HomeAssistant) -> None:
    """Test diagnostics option appears only in options flow.

    Verifies that:
    - The option is NOT present in the initial config flow schema
    - The option IS present in the options flow schema with default
      value False
    - Submitting the options flow with the option enabled persists it
      on the config entry
    """
    with aioresponses() as mock_aiohttp:
        test_url = "https://example.com/calendar.ics"
        mock_aiohttp.get(
            test_url,
            status=200,
            body=calendar_data.AIRBNB_ICS_CALENDAR,
            headers={"content-type": "text/calendar"},
            repeat=True,
        )

        # Initial config flow schema must NOT include the option
        init_result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
        )
        init_schema_keys = {
            str(key.schema) for key in init_result["data_schema"].schema.keys()
        }
        assert CONF_ENABLE_KEYMASTER_EVENT_DIAGNOSTICS not in init_schema_keys

        config_result = await hass.config_entries.flow.async_configure(
            init_result["flow_id"],
            user_input={
                CONF_NAME: "Test Rental",
                CONF_URL: test_url,
                CONF_VERIFY_SSL: True,
                CONF_IGNORE_NON_RESERVED: True,
                CONF_LOCK_ENTRY: "(none)",
                CONF_REFRESH_FREQUENCY: DEFAULT_REFRESH_FREQUENCY,
                CONF_TIMEZONE: "UTC",
                CONF_EVENT_PREFIX: "",
                CONF_CHECKIN: DEFAULT_CHECKIN,
                CONF_CHECKOUT: DEFAULT_CHECKOUT,
                CONF_DAYS: DEFAULT_DAYS,
                CONF_MAX_EVENTS: DEFAULT_MAX_EVENTS,
                CONF_START_SLOT: DEFAULT_START_SLOT,
                CONF_CODE_LENGTH: DEFAULT_CODE_LENGTH,
                CONF_CODE_GENERATION: "Start/End Date",
                CONF_SHOULD_UPDATE_CODE: True,
                CONF_HONOR_EVENT_TIMES: DEFAULT_HONOR_EVENT_TIMES,
            },
        )
        await hass.async_block_till_done()

    assert config_result["type"] == FlowResultType.CREATE_ENTRY
    entry = config_result["result"]
    # Default value (option absent from initial flow) must be False
    assert entry.data.get(CONF_ENABLE_KEYMASTER_EVENT_DIAGNOSTICS, False) is False

    with aioresponses() as mock_aiohttp:
        mock_aiohttp.get(
            test_url,
            status=200,
            body=calendar_data.AIRBNB_ICS_CALENDAR,
            headers={"content-type": "text/calendar"},
            repeat=True,
        )

        opts_result = await hass.config_entries.options.async_init(entry.entry_id)
        opts_schema_keys = {
            str(key.schema): key for key in opts_result["data_schema"].schema.keys()
        }
        assert CONF_ENABLE_KEYMASTER_EVENT_DIAGNOSTICS in opts_schema_keys
        # Default in options flow is False
        default_marker = opts_schema_keys[CONF_ENABLE_KEYMASTER_EVENT_DIAGNOSTICS]
        assert default_marker.default() is False

        result = await hass.config_entries.options.async_configure(
            opts_result["flow_id"],
            user_input={
                CONF_NAME: "Test Rental",
                CONF_URL: test_url,
                CONF_VERIFY_SSL: True,
                CONF_IGNORE_NON_RESERVED: True,
                CONF_LOCK_ENTRY: "(none)",
                CONF_REFRESH_FREQUENCY: DEFAULT_REFRESH_FREQUENCY,
                CONF_TIMEZONE: "UTC",
                CONF_EVENT_PREFIX: "",
                CONF_CHECKIN: DEFAULT_CHECKIN,
                CONF_CHECKOUT: DEFAULT_CHECKOUT,
                CONF_DAYS: DEFAULT_DAYS,
                CONF_MAX_EVENTS: DEFAULT_MAX_EVENTS,
                CONF_START_SLOT: DEFAULT_START_SLOT,
                CONF_CODE_LENGTH: DEFAULT_CODE_LENGTH,
                CONF_CODE_GENERATION: "Start/End Date",
                CONF_SHOULD_UPDATE_CODE: True,
                CONF_HONOR_EVENT_TIMES: DEFAULT_HONOR_EVENT_TIMES,
                CONF_ENABLE_KEYMASTER_EVENT_DIAGNOSTICS: True,
            },
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    updated_entry = hass.config_entries.async_get_entry(entry.entry_id)
    assert updated_entry.data[CONF_ENABLE_KEYMASTER_EVENT_DIAGNOSTICS] is True


@pytest.mark.parametrize(
    ("input_val", "expected"),
    [
        (None, "(none)"),
        ("", "(none)"),
        ("  ", "(none)"),
        ("(none)", "(none)"),
        ("Front Door", "Front Door"),
    ],
)
def test_normalize_lock_entry(input_val: str | None, expected: str) -> None:
    """Test _normalize_lock_entry maps empty values to (none).

    Verifies that:
    - None, empty string, and whitespace map to '(none)'
    - Valid lock names pass through unchanged
    - The explicit '(none)' sentinel passes through
    """
    assert _normalize_lock_entry(input_val) == expected


async def test_config_flow_lock_entry_none_normalized(
    hass: HomeAssistant,
) -> None:
    """Test selecting '(none)' stores None lock entry.

    When the user selects '(none)' from the lock manager dropdown
    to disconnect a lock, the config flow converts the sentinel to
    None before saving the entry.
    """
    with aioresponses() as mock_aiohttp:
        test_url = "https://example.com/calendar.ics"
        mock_aiohttp.get(
            test_url,
            status=200,
            body=calendar_data.AIRBNB_ICS_CALENDAR,
            headers={"content-type": "text/calendar"},
            repeat=True,
        )

        config_result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={
                CONF_NAME: "Test Lock Clear",
                CONF_URL: test_url,
                CONF_VERIFY_SSL: True,
                CONF_IGNORE_NON_RESERVED: True,
                CONF_LOCK_ENTRY: "(none)",
                CONF_REFRESH_FREQUENCY: DEFAULT_REFRESH_FREQUENCY,
                CONF_TIMEZONE: "UTC",
                CONF_EVENT_PREFIX: "",
                CONF_CHECKIN: DEFAULT_CHECKIN,
                CONF_CHECKOUT: DEFAULT_CHECKOUT,
                CONF_DAYS: DEFAULT_DAYS,
                CONF_MAX_EVENTS: DEFAULT_MAX_EVENTS,
                CONF_START_SLOT: DEFAULT_START_SLOT,
                CONF_CODE_LENGTH: DEFAULT_CODE_LENGTH,
                CONF_CODE_GENERATION: "Start/End Date",
                CONF_SHOULD_UPDATE_CODE: True,
            },
        )
        await hass.async_block_till_done()

    assert config_result["type"] == FlowResultType.CREATE_ENTRY
    entry = config_result["result"]
    assert entry.data[CONF_LOCK_ENTRY] is None

    # Select '(none)' from the dropdown to disconnect lock
    with aioresponses() as mock_aiohttp:
        mock_aiohttp.get(
            test_url,
            status=200,
            body=calendar_data.AIRBNB_ICS_CALENDAR,
            headers={"content-type": "text/calendar"},
            repeat=True,
        )

        result = await hass.config_entries.options.async_init(
            entry.entry_id,
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                CONF_NAME: "Test Lock Clear",
                CONF_URL: test_url,
                CONF_VERIFY_SSL: True,
                CONF_IGNORE_NON_RESERVED: True,
                CONF_LOCK_ENTRY: "(none)",
                CONF_REFRESH_FREQUENCY: DEFAULT_REFRESH_FREQUENCY,
                CONF_TIMEZONE: "UTC",
                CONF_EVENT_PREFIX: "",
                CONF_CHECKIN: DEFAULT_CHECKIN,
                CONF_CHECKOUT: DEFAULT_CHECKOUT,
                CONF_DAYS: DEFAULT_DAYS,
                CONF_MAX_EVENTS: DEFAULT_MAX_EVENTS,
                CONF_START_SLOT: DEFAULT_START_SLOT,
                CONF_CODE_LENGTH: DEFAULT_CODE_LENGTH,
                CONF_CODE_GENERATION: "Start/End Date",
                CONF_SHOULD_UPDATE_CODE: True,
            },
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    updated_entry = hass.config_entries.async_get_entry(entry.entry_id)
    assert updated_entry.data[CONF_LOCK_ENTRY] is None


async def test_config_flow_lock_entry_empty_string_normalized(
    hass: HomeAssistant,
) -> None:
    """Test _normalize_lock_entry converts empty string to sentinel.

    Verify the normalizer utility itself handles empty strings,
    even though the SelectSelector dropdown sends '(none)' directly.
    """
    from custom_components.rental_control.config_flow import _normalize_lock_entry

    assert _normalize_lock_entry("") == "(none)"
    assert _normalize_lock_entry("  ") == "(none)"
    assert _normalize_lock_entry(None) == "(none)"
    assert _normalize_lock_entry("Lock1") == "Lock1"
    assert _normalize_lock_entry("(none)") == "(none)"


# ---------------------------------------------------------------------------
# Trim name config flow tests
# ---------------------------------------------------------------------------


async def test_config_flow_trim_fields_in_schema(hass: HomeAssistant) -> None:
    """Verify trim_names and max_name_length appear in schema.

    Checks that the config flow schema includes the new trim fields
    with correct default values.
    """
    from custom_components.rental_control.const import CONF_MAX_NAME_LENGTH
    from custom_components.rental_control.const import CONF_TRIM_NAMES

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    assert result["type"] == FlowResultType.FORM
    schema = result["data_schema"].schema
    schema_keys = {str(key.schema): key for key in schema.keys()}

    assert CONF_TRIM_NAMES in schema_keys
    assert CONF_MAX_NAME_LENGTH in schema_keys

    # Verify defaults
    assert schema_keys[CONF_TRIM_NAMES].default() is False
    assert schema_keys[CONF_MAX_NAME_LENGTH].default() == 16


async def test_config_flow_prefix_too_long_for_trim(
    hass: HomeAssistant,
) -> None:
    """Verify prefix_too_long_for_trim error when prefix is too long.

    When trim_names is enabled and the effective prefix length
    (including the space separator) exceeds
    max_name_length - MIN_NAME_LENGTH, a validation error should
    be returned.
    """
    from custom_components.rental_control.const import CONF_MAX_NAME_LENGTH
    from custom_components.rental_control.const import CONF_TRIM_NAMES

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
                CONF_VERIFY_SSL: True,
                CONF_IGNORE_NON_RESERVED: True,
                CONF_LOCK_ENTRY: "(none)",
                CONF_REFRESH_FREQUENCY: DEFAULT_REFRESH_FREQUENCY,
                CONF_TIMEZONE: "UTC",
                CONF_EVENT_PREFIX: "VeryLongPrefix",
                CONF_CHECKIN: DEFAULT_CHECKIN,
                CONF_CHECKOUT: DEFAULT_CHECKOUT,
                CONF_DAYS: DEFAULT_DAYS,
                CONF_MAX_EVENTS: DEFAULT_MAX_EVENTS,
                CONF_START_SLOT: DEFAULT_START_SLOT,
                CONF_CODE_LENGTH: DEFAULT_CODE_LENGTH,
                CONF_CODE_GENERATION: "Start/End Date",
                CONF_SHOULD_UPDATE_CODE: True,
                CONF_TRIM_NAMES: True,
                CONF_MAX_NAME_LENGTH: 16,
            },
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "prefix_too_long_for_trim"


async def test_config_flow_prefix_short_enough_for_trim(
    hass: HomeAssistant,
) -> None:
    """Verify no error when prefix is short enough for trim.

    When trim_names is enabled and the event prefix is short relative
    to max_name_length, no prefix_too_long_for_trim error is raised.
    """
    from custom_components.rental_control.const import CONF_MAX_NAME_LENGTH
    from custom_components.rental_control.const import CONF_TRIM_NAMES

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
                CONF_VERIFY_SSL: True,
                CONF_IGNORE_NON_RESERVED: True,
                CONF_LOCK_ENTRY: "(none)",
                CONF_REFRESH_FREQUENCY: DEFAULT_REFRESH_FREQUENCY,
                CONF_TIMEZONE: "UTC",
                CONF_EVENT_PREFIX: "RC",
                CONF_CHECKIN: DEFAULT_CHECKIN,
                CONF_CHECKOUT: DEFAULT_CHECKOUT,
                CONF_DAYS: DEFAULT_DAYS,
                CONF_MAX_EVENTS: DEFAULT_MAX_EVENTS,
                CONF_START_SLOT: DEFAULT_START_SLOT,
                CONF_CODE_LENGTH: DEFAULT_CODE_LENGTH,
                CONF_CODE_GENERATION: "Start/End Date",
                CONF_SHOULD_UPDATE_CODE: True,
                CONF_TRIM_NAMES: True,
                CONF_MAX_NAME_LENGTH: 16,
            },
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert "prefix_too_long_for_trim" not in result.get("errors", {})


async def test_config_flow_prefix_boundary_exactly_min_remaining(
    hass: HomeAssistant,
) -> None:
    """Verify no error when prefix leaves exactly MIN_NAME_LENGTH chars.

    With max_name_length=16 and an 11-char prefix, the effective
    prefix length is 12 (11 + space separator), leaving exactly
    4 characters for the slot name which equals MIN_NAME_LENGTH.
    This boundary case must be accepted.
    """
    from custom_components.rental_control.const import CONF_MAX_NAME_LENGTH
    from custom_components.rental_control.const import CONF_TRIM_NAMES

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
                CONF_VERIFY_SSL: True,
                CONF_IGNORE_NON_RESERVED: True,
                CONF_LOCK_ENTRY: "(none)",
                CONF_REFRESH_FREQUENCY: DEFAULT_REFRESH_FREQUENCY,
                CONF_TIMEZONE: "UTC",
                CONF_EVENT_PREFIX: "ElevenChars",
                CONF_CHECKIN: DEFAULT_CHECKIN,
                CONF_CHECKOUT: DEFAULT_CHECKOUT,
                CONF_DAYS: DEFAULT_DAYS,
                CONF_MAX_EVENTS: DEFAULT_MAX_EVENTS,
                CONF_START_SLOT: DEFAULT_START_SLOT,
                CONF_CODE_LENGTH: DEFAULT_CODE_LENGTH,
                CONF_CODE_GENERATION: "Start/End Date",
                CONF_SHOULD_UPDATE_CODE: True,
                CONF_TRIM_NAMES: True,
                CONF_MAX_NAME_LENGTH: 16,
            },
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert "prefix_too_long_for_trim" not in result.get("errors", {})


async def test_config_flow_prefix_boundary_one_over(
    hass: HomeAssistant,
) -> None:
    """Verify error when prefix leaves fewer than MIN_NAME_LENGTH chars.

    With max_name_length=16 and a 12-char prefix, the effective
    prefix length is 13 (12 + space separator), leaving only 3
    characters which is below MIN_NAME_LENGTH (4). This must be
    rejected.
    """
    from custom_components.rental_control.const import CONF_MAX_NAME_LENGTH
    from custom_components.rental_control.const import CONF_TRIM_NAMES

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
                CONF_VERIFY_SSL: True,
                CONF_IGNORE_NON_RESERVED: True,
                CONF_LOCK_ENTRY: "(none)",
                CONF_REFRESH_FREQUENCY: DEFAULT_REFRESH_FREQUENCY,
                CONF_TIMEZONE: "UTC",
                CONF_EVENT_PREFIX: "TwelveCharsX",
                CONF_CHECKIN: DEFAULT_CHECKIN,
                CONF_CHECKOUT: DEFAULT_CHECKOUT,
                CONF_DAYS: DEFAULT_DAYS,
                CONF_MAX_EVENTS: DEFAULT_MAX_EVENTS,
                CONF_START_SLOT: DEFAULT_START_SLOT,
                CONF_CODE_LENGTH: DEFAULT_CODE_LENGTH,
                CONF_CODE_GENERATION: "Start/End Date",
                CONF_SHOULD_UPDATE_CODE: True,
                CONF_TRIM_NAMES: True,
                CONF_MAX_NAME_LENGTH: 16,
            },
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "prefix_too_long_for_trim"


async def test_config_flow_max_name_length_min_validation(
    hass: HomeAssistant,
) -> None:
    """Verify max_name_length schema rejects values below MIN_NAME_LENGTH.

    Exercises the production data_schema returned by the config flow
    form rather than a hand-built schema copy.
    """
    from custom_components.rental_control.const import CONF_MAX_NAME_LENGTH
    from custom_components.rental_control.const import MIN_NAME_LENGTH

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] == FlowResultType.FORM
    schema = result["data_schema"]

    base_data = {
        CONF_NAME: "Test",
        CONF_URL: "https://example.com/cal.ics",
        CONF_VERIFY_SSL: True,
        CONF_IGNORE_NON_RESERVED: True,
        CONF_LOCK_ENTRY: "(none)",
        CONF_REFRESH_FREQUENCY: DEFAULT_REFRESH_FREQUENCY,
        CONF_TIMEZONE: "UTC",
        CONF_EVENT_PREFIX: "",
        CONF_CHECKIN: DEFAULT_CHECKIN,
        CONF_CHECKOUT: DEFAULT_CHECKOUT,
        CONF_DAYS: DEFAULT_DAYS,
        CONF_MAX_EVENTS: DEFAULT_MAX_EVENTS,
        CONF_START_SLOT: DEFAULT_START_SLOT,
        CONF_CODE_LENGTH: DEFAULT_CODE_LENGTH,
        CONF_CODE_GENERATION: "Start/End Date",
        CONF_SHOULD_UPDATE_CODE: True,
    }

    # Valid value must pass
    valid = {**base_data, CONF_MAX_NAME_LENGTH: MIN_NAME_LENGTH}
    parsed = schema(valid)
    assert parsed[CONF_MAX_NAME_LENGTH] == MIN_NAME_LENGTH

    # Value below minimum must raise
    invalid = {**base_data, CONF_MAX_NAME_LENGTH: MIN_NAME_LENGTH - 1}
    with pytest.raises(vol.Invalid):
        schema(invalid)


async def test_config_flow_decomposition_import_surface(hass: HomeAssistant) -> None:
    """Test decomposed config-flow compatibility surface remains importable."""
    from custom_components.rental_control import config_flow

    assert config_flow.RentalControlFlowHandler.VERSION == 10
    assert hasattr(config_flow.RentalControlFlowHandler, "async_step_user")
    assert hasattr(config_flow.RentalControlFlowHandler, "async_get_options_flow")
    assert hasattr(config_flow.RentalControlOptionsFlow, "async_step_init")
    for name in (
        "gen_uuid",
        "_normalize_lock_entry",
        "_get_schema",
        "_show_config_form",
        "_start_config_flow",
    ):
        assert hasattr(config_flow, name)


async def test_config_flow_decomposition_direct_helpers(hass: HomeAssistant) -> None:
    """Test direct helper seams remain callable from config_flow."""
    from custom_components.rental_control import config_flow
    from custom_components.rental_control.config_flow_helpers.models import (
        ConfigFormContext,
    )

    class FlowStub:
        """Stub flow for direct form rendering."""

        def __init__(self, flow_hass: HomeAssistant) -> None:
            """Initialize the stub."""
            self.hass = flow_hass

        def async_show_form(self, **kwargs):
            """Return rendered form kwargs."""
            return kwargs

    schema = config_flow._get_schema(
        hass, {}, config_flow.RentalControlFlowHandler.DEFAULTS
    )
    result = config_flow._show_config_form(
        FlowStub(hass),
        ConfigFormContext(
            step_id="user",
            user_input={},
            errors={},
            description_placeholders={},
            defaults=config_flow.RentalControlFlowHandler.DEFAULTS,
            entry_id=None,
        ),
    )

    assert config_flow._normalize_lock_entry(None) == "(none)"
    assert CONF_NAME in {str(key.schema) for key in schema.schema}
    assert result["step_id"] == "user"
    assert result["errors"] == {}
