# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Tests for config-flow schema helper parity."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from homeassistant.const import CONF_NAME
from homeassistant.const import CONF_URL
from homeassistant.const import CONF_VERIFY_SSL
from pytest_homeassistant_custom_component.common import MockConfigEntry
import voluptuous as vol
from voluptuous.schema_builder import ALLOW_EXTRA

from custom_components.rental_control.config_flow import RentalControlFlowHandler
from custom_components.rental_control.config_flow import _get_schema
from custom_components.rental_control.config_flow import _show_config_form
from custom_components.rental_control.config_flow_helpers.models import (
    ConfigFormContext,
)
from custom_components.rental_control.config_flow_helpers.models import (
    FlowTransitionRequest,
)
from custom_components.rental_control.config_flow_helpers.models import (
    FlowValidationResult,
)
from custom_components.rental_control.config_flow_helpers.models import (
    SchemaBuildContext,
)
from custom_components.rental_control.config_flow_helpers.models import (
    URLValidationResult,
)
from custom_components.rental_control.const import CODE_GENERATORS
from custom_components.rental_control.const import CONF_CHECKIN
from custom_components.rental_control.const import CONF_CHECKOUT
from custom_components.rental_control.const import CONF_CODE_BUFFER_AFTER
from custom_components.rental_control.const import CONF_CODE_BUFFER_BEFORE
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
from custom_components.rental_control.const import CONF_MAX_NAME_LENGTH
from custom_components.rental_control.const import CONF_REFRESH_FREQUENCY
from custom_components.rental_control.const import CONF_SHOULD_UPDATE_CODE
from custom_components.rental_control.const import CONF_START_SLOT
from custom_components.rental_control.const import CONF_TIMEZONE
from custom_components.rental_control.const import CONF_TRIM_NAMES
from custom_components.rental_control.const import DEFAULT_CHECKIN
from custom_components.rental_control.const import DEFAULT_CODE_LENGTH
from custom_components.rental_control.const import DEFAULT_REFRESH_FREQUENCY
from custom_components.rental_control.const import LOCK_MANAGER

if False:
    from homeassistant.core import HomeAssistant


class _FormFlow:
    """Small flow stub that records form render values."""

    def __init__(self, hass: "HomeAssistant") -> None:
        """Initialize the stub with Home Assistant."""
        self.hass = hass

    def async_show_form(self, **kwargs: Any) -> dict[str, Any]:
        """Return the rendered form kwargs."""
        return kwargs


def _schema_key(schema: vol.Schema, name: str) -> Any:
    """Return a voluptuous schema marker by name."""
    return next(key for key in schema.schema if str(key.schema) == name)


def _schema_names(schema: vol.Schema) -> set[str]:
    """Return schema marker names."""
    return {str(key.schema) for key in schema.schema}


def _select_options(selector: Any) -> list[str]:
    """Return selector option values."""
    return [option["value"] for option in selector.config["options"]]


def _add_lock_manager(hass: "HomeAssistant") -> None:
    """Register a mock lock-manager config entry."""
    hass.data[LOCK_MANAGER] = {}
    MockConfigEntry(
        domain=LOCK_MANAGER,
        title="Front Door",
        data={"lockname": "lock.front_door"},
        entry_id="lock-entry",
    ).add_to_hass(hass)


def test_helper_models_preserve_values(hass: "HomeAssistant") -> None:
    """Test helper model construction keeps exact values."""
    errors = {CONF_NAME: "same_name"}
    placeholders = {"url": "https://example.com"}
    defaults = {CONF_NAME: "Existing"}
    user_input = {CONF_NAME: "Submitted"}

    def renderer(flow: Any, context: ConfigFormContext) -> ConfigFormContext:
        """Return the context passed by the step helper."""
        return context

    form = ConfigFormContext("user", user_input, errors, placeholders, defaults, "id1")
    schema = SchemaBuildContext(hass, user_input, defaults, "id1")
    url = URLValidationResult("unknown", 500, "Nope", "text/plain")
    flow = FlowValidationResult(user_input, errors, placeholders, False)
    request = FlowTransitionRequest(
        SimpleNamespace(hass=hass),
        "user",
        "Title",
        user_input,
        defaults,
        "id1",
        renderer,
    )

    assert form.errors is errors
    assert form.description_placeholders is placeholders
    assert schema.defaults is defaults
    assert url.status == 500
    assert flow.user_input is user_input
    assert request.form_renderer is renderer


def test_initial_schema_preserves_fields_defaults(hass: "HomeAssistant") -> None:
    """Test initial config schema fields, defaults, choices, and ALLOW_EXTRA."""
    schema = _get_schema(hass, {}, RentalControlFlowHandler.DEFAULTS)
    names = _schema_names(schema)

    assert schema.extra == ALLOW_EXTRA
    for name in {
        CONF_NAME,
        CONF_URL,
        CONF_REFRESH_FREQUENCY,
        CONF_TIMEZONE,
        CONF_EVENT_PREFIX,
        CONF_CHECKIN,
        CONF_CHECKOUT,
        CONF_DAYS,
        CONF_LOCK_ENTRY,
        CONF_START_SLOT,
        CONF_MAX_EVENTS,
        CONF_CODE_LENGTH,
        CONF_CODE_GENERATION,
        CONF_SHOULD_UPDATE_CODE,
        CONF_HONOR_EVENT_TIMES,
        CONF_IGNORE_NON_RESERVED,
        CONF_VERIFY_SSL,
        CONF_TRIM_NAMES,
        CONF_MAX_NAME_LENGTH,
    }:
        assert name in names

    assert (
        _schema_key(schema, CONF_REFRESH_FREQUENCY).default()
        == DEFAULT_REFRESH_FREQUENCY
    )
    assert _schema_key(schema, CONF_CHECKIN).default() == DEFAULT_CHECKIN
    assert _schema_key(schema, CONF_CODE_LENGTH).default() == DEFAULT_CODE_LENGTH
    assert _schema_key(schema, CONF_CODE_GENERATION).default() == "Start/End Date"
    assert isinstance(_schema_key(schema, CONF_NAME), vol.Required)


def test_options_schema_preserves_extra_fields_and_precedence(
    hass: "HomeAssistant",
) -> None:
    """Test options schema adds options-only fields and entered values win."""
    defaults = dict(RentalControlFlowHandler.DEFAULTS)
    defaults[CONF_NAME] = "Default Name"
    defaults[CONF_CODE_BUFFER_BEFORE] = 5
    user_input = {CONF_NAME: "Entered Name"}

    schema = _get_schema(hass, user_input, defaults, "entry-id")
    names = _schema_names(schema)

    assert CONF_ENABLE_KEYMASTER_EVENT_DIAGNOSTICS in names
    assert CONF_CODE_BUFFER_BEFORE in names
    assert CONF_CODE_BUFFER_AFTER in names
    assert _schema_key(schema, CONF_NAME).default() == "Entered Name"
    assert _schema_key(schema, CONF_CODE_BUFFER_BEFORE).default() == 5


def test_lock_selector_and_default_conversion(hass: "HomeAssistant") -> None:
    """Test lock selector order and stored lock entity display conversion."""
    _add_lock_manager(hass)
    defaults = dict(RentalControlFlowHandler.DEFAULTS)
    defaults[CONF_LOCK_ENTRY] = "lock.front_door"

    schema = _get_schema(hass, {}, defaults, "entry-id")
    selector = schema.schema[_schema_key(schema, CONF_LOCK_ENTRY)]

    assert _select_options(selector)[:2] == ["(none)", "Front Door"]
    assert _schema_key(schema, CONF_LOCK_ENTRY).default() == "Front Door"


def test_code_generator_descriptions_are_schema_choices(hass: "HomeAssistant") -> None:
    """Test code-generator descriptions remain the visible choices."""
    schema = _get_schema(hass, {}, RentalControlFlowHandler.DEFAULTS)
    generator_validator = schema.schema[_schema_key(schema, CONF_CODE_GENERATION)]

    assert set(generator_validator.container) == {
        generator["description"] for generator in CODE_GENERATORS
    }


def test_show_config_form_accepts_context_and_legacy(
    hass: "HomeAssistant",
) -> None:
    """Test form rendering accepts grouped context and legacy arguments."""
    flow = _FormFlow(hass)
    context = ConfigFormContext(
        step_id="init",
        user_input={CONF_NAME: "Submitted"},
        errors={CONF_URL: "bad_ics"},
        description_placeholders={"reason": "plain"},
        defaults=RentalControlFlowHandler.DEFAULTS,
        entry_id="entry-id",
    )

    grouped = _show_config_form(flow, context)
    legacy = _show_config_form(
        flow,
        "init",
        context.user_input,
        context.errors,
        context.description_placeholders,
        context.defaults,
        context.entry_id,
    )

    assert grouped["step_id"] == "init"
    assert grouped["errors"] == context.errors
    assert grouped["description_placeholders"] == context.description_placeholders
    assert _schema_names(grouped["data_schema"]) == _schema_names(legacy["data_schema"])
