"""Config flow for Rental Control integration."""
import logging
import re
from typing import Any
from typing import Dict
from typing import Optional

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant import config_entries
from homeassistant import exceptions
from homeassistant.const import CONF_NAME
from homeassistant.const import CONF_URL
from homeassistant.const import CONF_VERIFY_SSL
from voluptuous.schema_builder import ALLOW_EXTRA

from .const import CONF_CHECKIN
from .const import CONF_CHECKOUT
from .const import CONF_DAYS
from .const import CONF_MAX_EVENTS
from .const import DEFAULT_CHECKIN
from .const import DEFAULT_CHECKOUT
from .const import DEFAULT_DAYS
from .const import DEFAULT_MAX_EVENTS
from .const import DOMAIN

# from typing import Union

_LOGGER = logging.getLogger(__name__)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Rental Control."""

    VERSION = 1

    DEFAULTS = {
        CONF_CHECKIN: DEFAULT_CHECKIN,
        CONF_CHECKOUT: DEFAULT_CHECKOUT,
        CONF_DAYS: DEFAULT_DAYS,
        CONF_MAX_EVENTS: DEFAULT_MAX_EVENTS,
        CONF_VERIFY_SSL: True,
    }

    def __init__(self) -> None:
        """Initialize config flow."""
        self._user_schema = None

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}
        if user_input is not None:
            # Store current values in case setup fails and user needs to edit
            # self._user_schema = _get_config_schema(user_input)
            self._user_schema = _get_schema(user_input, self.DEFAULTS)

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

        # schema = self._user_schema or _get_config_schema()
        schema = self._user_schema or _get_schema(user_input, self.DEFAULTS)
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)


def _get_schema(
    # hass: HomeAssistant,
    user_input: Optional[Dict[str, Any]],
    default_dict: Dict[str, Any],
    # entry_id: str = None,
) -> vol.Schema:
    """Gets a schema using the default_dict as a backup."""
    if user_input is None:
        user_input = {}

    def _get_default(key: str, fallback_default: Any = None) -> None:
        """Gets default value for key."""
        return user_input.get(key, default_dict.get(key, fallback_default))

    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=_get_default(CONF_NAME)): cv.string,
            vol.Required(CONF_URL, default=_get_default(CONF_URL)): cv.string,
            vol.Required(
                CONF_CHECKIN, default=_get_default(CONF_CHECKIN, DEFAULT_CHECKIN)
            ): cv.string,
            vol.Required(
                CONF_CHECKOUT, default=_get_default(CONF_CHECKOUT, DEFAULT_CHECKOUT)
            ): cv.string,
            vol.Optional(
                CONF_DAYS, default=_get_default(CONF_DAYS, DEFAULT_DAYS)
            ): cv.positive_int,
            vol.Optional(
                CONF_MAX_EVENTS,
                default=_get_default(CONF_MAX_EVENTS, DEFAULT_MAX_EVENTS),
            ): cv.positive_int,
            vol.Optional(
                CONF_VERIFY_SSL, default=_get_default(CONF_VERIFY_SSL, True)
            ): cv.boolean,
        },
        extra=ALLOW_EXTRA,
    )


class BadTime(exceptions.HomeAssistantError):
    """Error with checkin/out time."""


class InvalidUrl(exceptions.HomeAssistantError):
    """Error indicates a malformed URL."""
