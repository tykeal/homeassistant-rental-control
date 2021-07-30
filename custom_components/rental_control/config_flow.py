"""Config flow for Rental Control integration."""
from __future__ import annotations

import logging
import re
from typing import Any

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant import config_entries
from homeassistant import exceptions
from homeassistant.const import CONF_NAME
from homeassistant.const import CONF_URL
from homeassistant.const import CONF_VERIFY_SSL

from .const import CONF_CHECKIN
from .const import CONF_CHECKOUT
from .const import CONF_DAYS
from .const import CONF_MAX_EVENTS
from .const import DEFAULT_CHECKIN
from .const import DEFAULT_CHECKOUT
from .const import DEFAULT_DAYS
from .const import DEFAULT_MAX_EVENTS
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


def _get_config_schema(input_dict: dict[str, Any] = None) -> vol.Schema:
    """
    Return schema defaults for init step based on user input/config dict.

    Retain info already provided for future form views by setting them as
    defaults in schema.
    """
    if input_dict is None:
        input_dict = {}

    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=input_dict.get(CONF_NAME)): cv.string,
            vol.Required(CONF_URL, default=input_dict.get(CONF_URL)): cv.string,
            vol.Required(
                CONF_CHECKIN, default=input_dict.get(CONF_CHECKIN, DEFAULT_CHECKIN)
            ): cv.string,
            vol.Required(
                CONF_CHECKOUT, default=input_dict.get(CONF_CHECKOUT, DEFAULT_CHECKOUT)
            ): cv.string,
            vol.Optional(
                CONF_DAYS, default=input_dict.get(CONF_DAYS, DEFAULT_DAYS)
            ): cv.positive_int,
            vol.Optional(
                CONF_MAX_EVENTS,
                default=input_dict.get(CONF_MAX_EVENTS, DEFAULT_MAX_EVENTS),
            ): cv.positive_int,
            vol.Optional(
                CONF_VERIFY_SSL, default=input_dict.get(CONF_VERIFY_SSL, True)
            ): cv.boolean,
        },
        extra=vol.REMOVE_EXTRA,
    )


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Rental Control."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize config flow."""
        self._user_schema = None

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}
        if user_input is not None:
            # Store current values in case setup fails and user needs to edit
            self._user_schema = _get_config_schema(user_input)

            # Validate user input
            try:
                cv.url(user_input["url"])
                # We currently only support AirBnB ical at this time
                if not re.search("^https://www.airbnb.com/.*ics", user_input["url"]):
                    errors["base"] = "bad_ics"
            except vol.Invalid as err:
                _LOGGER.exception(err.msg)
                errors["base"] = "invalid_url"

            try:
                cv.time(user_input["checkin"])
                cv.time(user_input["checkout"])
            except vol.Invalid as err:
                _LOGGER.exception(err.msg)
                errors["base"] = "bad_time"

            if not errors:
                return self.async_create_entry(
                    title=user_input[CONF_NAME], data=user_input
                )

        schema = self._user_schema or _get_config_schema()
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)


class BadTime(exceptions.HomeAssistantError):
    """Error with checkin/out time."""


class InvalidUrl(exceptions.HomeAssistantError):
    """Error indicates a malformed URL."""
