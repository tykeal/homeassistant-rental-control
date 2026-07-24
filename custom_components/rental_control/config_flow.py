# SPDX-FileCopyrightText: 2021 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Config flow for Rental Control integration."""

from __future__ import annotations

from typing import Any

from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.const import CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.core import callback
from homeassistant.util import dt
import voluptuous as vol

from .config_flow_helpers import schemas as _schemas
from .config_flow_helpers.models import ConfigFormContext
from .config_flow_helpers.models import FlowTransitionRequest
from .config_flow_helpers.steps import start_config_flow as _helper_start_config_flow
from .config_flow_helpers.validation import (
    normalize_lock_entry as _helper_normalize_lock_entry,
)
from .const import CONF_CHECKIN
from .const import CONF_CHECKOUT
from .const import CONF_CODE_BUFFER_AFTER
from .const import CONF_CODE_BUFFER_BEFORE
from .const import CONF_DATE_ONLY  # ADDED
from .const import CONF_DAYS
from .const import CONF_EVENT_PREFIX
from .const import CONF_HONOR_EVENT_TIMES
from .const import CONF_IGNORE_NON_RESERVED
from .const import CONF_MAX_EVENTS
from .const import CONF_MAX_NAME_LENGTH
from .const import CONF_REFRESH_FREQUENCY
from .const import CONF_SHOULD_UPDATE_CODE
from .const import CONF_TIMEZONE
from .const import CONF_TRIM_NAMES
from .const import DEFAULT_CHECKIN
from .const import DEFAULT_CHECKOUT
from .const import DEFAULT_CODE_BUFFER_AFTER
from .const import DEFAULT_CODE_BUFFER_BEFORE
from .const import DEFAULT_DATE_ONLY  # ADDED
from .const import DEFAULT_DAYS
from .const import DEFAULT_EVENT_PREFIX
from .const import DEFAULT_HONOR_EVENT_TIMES
from .const import DEFAULT_MAX_EVENTS
from .const import DEFAULT_MAX_NAME_LENGTH
from .const import DEFAULT_REFRESH_FREQUENCY
from .const import DEFAULT_SHOULD_UPDATE_CODE
from .const import DEFAULT_TRIM_NAMES
from .const import DOMAIN
from .util import gen_uuid

# aislop-ignore-file ai-slop/hallucinated-import -- Provided by Home Assistant runtime.

sorted_tz = _schemas.SORTED_TZ
_available_lock_managers = _schemas.available_lock_managers
_code_generators = _schemas.code_generators
_generator_convert = _schemas.generator_convert
_lock_entry_convert = _schemas.lock_entry_convert


class RentalControlFlowHandler(  # type: ignore[call-arg]
    config_entries.ConfigFlow, domain=DOMAIN
):
    """Handle the config flow for Rental Control."""

    VERSION = 10

    DEFAULTS = {
        CONF_CHECKIN: DEFAULT_CHECKIN,
        CONF_CHECKOUT: DEFAULT_CHECKOUT,
        CONF_DATE_ONLY: DEFAULT_DATE_ONLY,  # ADDED
        CONF_DAYS: DEFAULT_DAYS,
        CONF_IGNORE_NON_RESERVED: True,
        CONF_EVENT_PREFIX: DEFAULT_EVENT_PREFIX,
        CONF_MAX_EVENTS: DEFAULT_MAX_EVENTS,
        CONF_REFRESH_FREQUENCY: DEFAULT_REFRESH_FREQUENCY,
        CONF_SHOULD_UPDATE_CODE: DEFAULT_SHOULD_UPDATE_CODE,
        CONF_HONOR_EVENT_TIMES: DEFAULT_HONOR_EVENT_TIMES,
        CONF_TRIM_NAMES: DEFAULT_TRIM_NAMES,
        CONF_MAX_NAME_LENGTH: DEFAULT_MAX_NAME_LENGTH,
        CONF_CODE_BUFFER_BEFORE: DEFAULT_CODE_BUFFER_BEFORE,
        CONF_CODE_BUFFER_AFTER: DEFAULT_CODE_BUFFER_AFTER,
        CONF_TIMEZONE: str(dt.DEFAULT_TIME_ZONE),
        CONF_VERIFY_SSL: True,
    }

    def __init__(self):
        """Set up the RentalControlFlowHandler."""
        self.created = str(dt.now())

    async def _get_unique_id(self, user_input: dict[str, Any]) -> dict[str, str]:
        """Generate the unique_id."""
        existing_entry = await self.async_set_unique_id(
            gen_uuid(self.created), raise_on_progress=True
        )
        if existing_entry:
            return {CONF_NAME: "same_name"}
        return {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> Any:
        """Handle the initial step."""
        return await _start_config_flow(
            self,
            "user",
            user_input[CONF_NAME] if user_input else None,
            user_input,
            self.DEFAULTS,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> Any:
        """Link the Options Flow."""
        return RentalControlOptionsFlow()


class RentalControlOptionsFlow(config_entries.OptionsFlow):
    """Options flow for Rental Control."""

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> Any:
        """Handle a flow initialized by the user."""
        flow_data: dict[str, Any] | None = None
        if self.config_entry.data:
            flow_data = dict(self.config_entry.data)
        return await _start_config_flow(
            self,
            "init",
            "",
            user_input,
            flow_data,
            self.config_entry.entry_id,
        )


def _normalize_lock_entry(value: Any) -> str:
    """Normalize cleared lock entry values to '(none)'."""
    return _helper_normalize_lock_entry(value)


def _get_schema(
    hass: HomeAssistant,
    user_input: dict[str, Any] | None,
    default_dict: dict[str, Any] | None,
    entry_id: str | None = None,
) -> vol.Schema:
    """Get a schema using the default_dict as a backup."""
    return _schemas.build_config_schema(hass, user_input, default_dict, entry_id)


def _show_config_form(
    cls: Any,
    context: ConfigFormContext | str | None = None,
    *legacy: Any,
    **kwargs: Any,
) -> Any:
    """Show the configuration form to edit data."""
    form_context = _coerce_form_context(context, legacy, kwargs)
    return cls.async_show_form(
        step_id=form_context.step_id,
        data_schema=_get_schema(
            cls.hass,
            form_context.user_input,
            form_context.defaults,
            form_context.entry_id,
        ),
        errors=form_context.errors,
        description_placeholders=form_context.description_placeholders,
    )


def _coerce_form_context(
    context: ConfigFormContext | str | None,
    legacy: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> ConfigFormContext:
    """Coerce grouped or legacy form arguments into context."""
    if isinstance(context, ConfigFormContext):
        return context

    if context is None:
        context = kwargs["step_id"]
        user_input = kwargs.get("user_input")
        errors = kwargs["errors"]
        placeholders = kwargs["description_placeholders"]
        defaults = kwargs.get("defaults")
        entry_id = kwargs.get("entry_id")
    else:
        user_input = kwargs.get("user_input", legacy[0] if len(legacy) > 0 else None)
        errors = kwargs.get("errors", legacy[1] if len(legacy) > 1 else {})
        placeholders = kwargs.get(
            "description_placeholders", legacy[2] if len(legacy) > 2 else {}
        )
        defaults = kwargs.get("defaults", legacy[3] if len(legacy) > 3 else None)
        entry_id = kwargs.get("entry_id", legacy[4] if len(legacy) > 4 else None)

    return ConfigFormContext(
        step_id=str(context),
        user_input=user_input,
        errors=errors,
        description_placeholders=placeholders,
        defaults=defaults,
        entry_id=entry_id,
    )


async def _start_config_flow(
    cls: Any,
    step_id: str,
    title: Any,
    user_input: dict[str, Any] | None,
    defaults: dict[str, Any] | None = None,
    entry_id: str | None = None,
) -> Any:
    """Start a config flow."""
    return await _helper_start_config_flow(
        FlowTransitionRequest(
            flow=cls,
            step_id=step_id,
            title=title,
            user_input=user_input,
            defaults=defaults,
            entry_id=entry_id,
            form_renderer=_show_config_form,
        )
    )
