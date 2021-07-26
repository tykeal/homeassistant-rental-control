"""Config flow for Rental Control integration."""
import logging

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant import config_entries
from homeassistant import core
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

# Configure the DATA_SCHEMA
DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): cv.string,
        vol.Required(CONF_URL): cv.string,
        vol.Required(CONF_CHECKIN, default=DEFAULT_CHECKIN): cv.string,
        vol.Required(CONF_CHECKOUT, default=DEFAULT_CHECKOUT): cv.string,
        vol.Optional(CONF_DAYS, default=DEFAULT_DAYS): cv.positive_int,
        vol.Optional(CONF_MAX_EVENTS, default=DEFAULT_MAX_EVENTS): cv.positive_int,
        vol.Optional(CONF_VERIFY_SSL, default=True): cv.boolean,
    }
)


async def validate_input(hass: core.HomeAssistant, data):
    """Validate the user input allows us to connect.

    Data has the keys from DATA_SCHEMA with values provided by the user.
    """

    cv.url(data["url"])
    cv.time(data["checkin"])
    cv.time(data["checkout"])

    # Return info that you want to store in the config entry.
    return {"title": data[CONF_NAME], "url": data[CONF_URL]}


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Rental Control."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}
        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)

                return self.async_create_entry(title=info["title"], data=user_input)
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user", data_schema=DATA_SCHEMA, errors=errors
        )
