"""Config flow for Rental Control integration."""
import datetime
import logging

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant import config_entries
from homeassistant import core
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

# Get time objects for default checkin/out
DEFAULT_CHECKIN_TIME = datetime.time(DEFAULT_CHECKIN, 0)
DEFAULT_CHECKOUT_TIME = datetime.time(DEFAULT_CHECKOUT, 0)

# Configure the DATA_SCHEMA
DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): cv.string,
        vol.Required(CONF_URL): cv.string,
        vol.Required(CONF_CHECKIN, default=DEFAULT_CHECKIN_TIME): cv.time,
        vol.Required(CONF_CHECKOUT, default=DEFAULT_CHECKOUT_TIME): cv.time,
        vol.Required(CONF_DAYS, default=DEFAULT_DAYS): cv.positive_int,
        vol.Required(CONF_MAX_EVENTS, default=DEFAULT_MAX_EVENTS): cv.positive_int,
        vol.Required(CONF_VERIFY_SSL, default=True): cv.boolean,
    }
)


class PlaceholderHub:
    """Placeholder class to make tests pass.

    TODO Remove this placeholder class and replace with things from your PyPI package.
    """

    def __init__(self, host):
        """Initialize."""
        self.host = host

    async def authenticate(self, username, password) -> bool:
        """Test if we can authenticate with the host."""
        return True


async def validate_input(hass: core.HomeAssistant, data):
    """Validate the user input allows us to connect.

    Data has the keys from DATA_SCHEMA with values provided by the user.
    """
    # TODO validate the data can be used to set up a connection.

    # If your PyPI package is not built with async, pass your methods
    # to the executor:
    # await hass.async_add_executor_job(
    #     your_validate_func, data["username"], data["password"]
    # )

    hub = PlaceholderHub(data["name"])

    if not await hub.authenticate(data["url"], data["url"]):
        raise InvalidAuth

    # If you cannot connect:
    # throw CannotConnect
    # If the authentication is wrong:
    # InvalidAuth

    # Return info that you want to store in the config entry.
    return {"title": data[CONF_NAME], "url": data[CONF_URL]}


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Rental Control."""

    VERSION = 1
    # TODO pick one of the available connection classes in homeassistant/config_entries.py
    CONNECTION_CLASS = config_entries.CONN_CLASS_UNKNOWN

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}
        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)

                return self.async_create_entry(title=info["title"], data=user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user", data_schema=DATA_SCHEMA, errors=errors
        )


class CannotConnect(exceptions.HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(exceptions.HomeAssistantError):
    """Error to indicate there is invalid auth."""
