"""Config flow for Rental Control integration."""
import logging
import re
from typing import Any
from typing import Dict
from typing import Optional
from typing import Union

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.const import CONF_URL
from homeassistant.const import CONF_VERIFY_SSL
from homeassistant.core import callback
from homeassistant.core import HomeAssistant
from voluptuous.schema_builder import ALLOW_EXTRA

from .const import CONF_CHECKIN
from .const import CONF_CHECKOUT
from .const import CONF_DAYS
from .const import CONF_LOCK_ENTRY
from .const import CONF_MAX_EVENTS
from .const import CONF_START_SLOT
from .const import DEFAULT_CHECKIN
from .const import DEFAULT_CHECKOUT
from .const import DEFAULT_DAYS
from .const import DEFAULT_MAX_EVENTS
from .const import DEFAULT_START_SLOT
from .const import DOMAIN
from .const import LOCK_MANAGER

_LOGGER = logging.getLogger(__name__)


@config_entries.HANDLERS.register(DOMAIN)
class RentalControlFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the config flow for Rental Control."""

    VERSION = 1

    DEFAULTS = {
        CONF_CHECKIN: DEFAULT_CHECKIN,
        CONF_CHECKOUT: DEFAULT_CHECKOUT,
        CONF_DAYS: DEFAULT_DAYS,
        CONF_MAX_EVENTS: DEFAULT_MAX_EVENTS,
        CONF_VERIFY_SSL: True,
    }

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        return await _start_config_flow(
            self,
            "user",
            # title?
            user_input,
            self.DEFAULTS,
            # entry_id?
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        """Link the Options Flow."""
        return RentalControlOptionsFlow(config_entry)


class RentalControlOptionsFlow(config_entries.OptionsFlow):
    """Options flow for Rental Control."""

    def __init__(self, config_entry: config_entries.ConfigEntry):
        """Initialize Options Flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self,
        user_input: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        """Handle a flow initialized by the user."""
        return await _start_config_flow(
            self,
            "init",
            # title?
            user_input,
            self.config_entry.data,
            # self.config_entry.entity_id,
        )


def _available_lock_managers(
    hass: HomeAssistant,
    # entry_id: str = None
) -> list:
    """Find lock manager configurations to use."""

    data = ["(none)"]
    if LOCK_MANAGER not in hass.data:
        return data

    for entry in hass.config_entries.async_entries(LOCK_MANAGER):
        data.append(entry.title)

    return data


def _get_schema(
    hass: HomeAssistant,
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
                CONF_LOCK_ENTRY, default=_get_default(CONF_LOCK_ENTRY, "(none)")
            ): vol.In(_available_lock_managers(hass)),
            vol.Required(
                CONF_START_SLOT,
                default=_get_default(CONF_START_SLOT, DEFAULT_START_SLOT),
            ): cv.positive_int,
            vol.Required(
                CONF_MAX_EVENTS,
                default=_get_default(CONF_MAX_EVENTS, DEFAULT_MAX_EVENTS),
            ): cv.positive_int,
            vol.Optional(
                CONF_VERIFY_SSL, default=_get_default(CONF_VERIFY_SSL, True)
            ): cv.boolean,
        },
        extra=ALLOW_EXTRA,
    )


async def _start_config_flow(
    cls: Union[RentalControlFlowHandler, RentalControlOptionsFlow],
    step_id: str,
    # title: str,
    user_input: Dict[str, Any],
    defaults: Dict[str, Any] = None,
    # entry_id: str = None,
):
    """Start a config flow."""
    errors = {}

    if user_input is not None:
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
            return cls.async_create_entry(title=user_input[CONF_NAME], data=user_input)

    schema = _get_schema(cls.hass, user_input, defaults)
    return cls.async_show_form(step_id=step_id, data_schema=schema, errors=errors)
