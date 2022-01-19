"""Config flow for Rental Control integration."""
import asyncio
import logging
import re
from typing import Any
from typing import Dict
from typing import Optional
from typing import Union

import async_timeout
import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.const import CONF_URL
from homeassistant.const import CONF_VERIFY_SSL
from homeassistant.core import callback
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from voluptuous.schema_builder import ALLOW_EXTRA

from .const import CONF_CHECKIN
from .const import CONF_CHECKOUT
from .const import CONF_DAYS
from .const import CONF_EVENT_PREFIX
from .const import CONF_LOCK_ENTRY
from .const import CONF_MAX_EVENTS
from .const import CONF_START_SLOT
from .const import DEFAULT_CHECKIN
from .const import DEFAULT_CHECKOUT
from .const import DEFAULT_DAYS
from .const import DEFAULT_EVENT_PREFIX
from .const import DEFAULT_MAX_EVENTS
from .const import DEFAULT_START_SLOT
from .const import DOMAIN
from .const import LOCK_MANAGER
from .const import REQUEST_TIMEOUT

_LOGGER = logging.getLogger(__name__)


@config_entries.HANDLERS.register(DOMAIN)
class RentalControlFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the config flow for Rental Control."""

    VERSION = 1

    DEFAULTS = {
        CONF_CHECKIN: DEFAULT_CHECKIN,
        CONF_CHECKOUT: DEFAULT_CHECKOUT,
        CONF_DAYS: DEFAULT_DAYS,
        CONF_EVENT_PREFIX: DEFAULT_EVENT_PREFIX,
        CONF_MAX_EVENTS: DEFAULT_MAX_EVENTS,
        CONF_VERIFY_SSL: True,
    }

    async def _get_unique_name_error(self, user_input) -> Dict[str, str]:
        """Check if name is unique, returning dictionary if so."""
        # Validate that Rental control is unique
        existing_entry = await self.async_set_unique_id(
            user_input[CONF_NAME], raise_on_progress=True
        )
        if existing_entry:
            return {CONF_NAME: "same_name"}
        return {}

    async def async_step_user(self, user_input=None):
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
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        """Link the Options Flow."""
        return RentalControlOptionsFlow(config_entry)


class RentalControlOptionsFlow(config_entries.OptionsFlow):
    """Options flow for Rental Control."""

    def __init__(self, config_entry: config_entries.ConfigEntry):
        """Initialize Options Flow."""
        self.config_entry = config_entry

    def _get_unique_name_error(self, user_input) -> Dict[str, str]:
        """Check if name is unique, returning dictionary if so."""
        # If name has changed, make sure new name isn't already being used
        # otherwise show an error
        if self.config_entry.unique_id != user_input[CONF_NAME]:
            for entry in self.hass.config_entries.async_entries(DOMAIN):
                if entry.unique_id == user_input[CONF_NAME]:
                    return {CONF_NAME: "same_name"}
        return {}

    async def async_step_init(
        self,
        user_input: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        """Handle a flow initialized by the user."""
        return await _start_config_flow(
            self,
            "init",
            "",
            user_input,
            self.config_entry.data,
            self.config_entry.entry_id,
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
    entry_id: str = None,
) -> vol.Schema:
    """Gets a schema using the default_dict as a backup."""
    if user_input is None:
        user_input = {}

    if CONF_LOCK_ENTRY in default_dict.keys() and default_dict[CONF_LOCK_ENTRY] is None:
        check_dict = default_dict.copy()
        check_dict.pop(CONF_LOCK_ENTRY, None)
        default_dict = check_dict

    def _get_default(key: str, fallback_default: Any = None) -> None:
        """Gets default value for key."""
        return user_input.get(key, default_dict.get(key, fallback_default))

    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=_get_default(CONF_NAME)): cv.string,
            vol.Required(CONF_URL, default=_get_default(CONF_URL)): cv.string,
            vol.Optional(
                CONF_EVENT_PREFIX,
                default=_get_default(CONF_EVENT_PREFIX, DEFAULT_EVENT_PREFIX),
            ): cv.string,
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


def _show_config_form(
    cls: Union[RentalControlFlowHandler, RentalControlOptionsFlow],
    step_id: str,
    user_input: Dict[str, Any],
    errors: Dict[str, str],
    description_placeholders: Dict[str, str],
    defaults: Dict[str, Any] = None,
    entry_id: str = None,
) -> Dict[str, Any]:
    """Show the configuration form to edit data."""
    return cls.async_show_form(
        step_id=step_id,
        data_schema=_get_schema(cls.hass, user_input, defaults, entry_id),
        errors=errors,
        description_placeholders=description_placeholders,
    )


async def _start_config_flow(
    cls: Union[RentalControlFlowHandler, RentalControlOptionsFlow],
    step_id: str,
    title: str,
    user_input: Dict[str, Any],
    defaults: Dict[str, Any] = None,
    entry_id: str = None,
):
    """Start a config flow."""
    errors = {}
    description_placeholders = {}

    if user_input is not None:
        # Convert (none) to None
        if user_input[CONF_LOCK_ENTRY] == "(none)":
            user_input[CONF_LOCK_ENTRY] = None

        # Regular flow has an async function, options flow has a sync function
        # so we need to handle them conditionally
        if asyncio.iscoroutinefunction(cls._get_unique_name_error):
            errors.update(await cls._get_unique_name_error(user_input))
        else:
            errors.update(cls._get_unique_name_error(user_input))

        # Validate user input
        try:
            cv.url(user_input["url"])
            # We require that the URL be an SSL URL
            if not re.search("^https://", user_input["url"]):
                errors["base"] = "invalid_url"

            session = async_get_clientsession(
                cls.hass, verify_ssl=user_input["verify_ssl"]
            )
            with async_timeout.timeout(REQUEST_TIMEOUT):
                resp = await session.get(user_input["url"])
            if resp.status != 200:
                _LOGGER.error(
                    "%s returned %s - %s", user_input["url"], resp.status, resp.reason
                )
                errors["base"] = "unknown"
            # We require text/calendar in the content-type header
            if "text/calendar" not in resp.content_type:
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
            return cls.async_create_entry(title=title, data=user_input)

        return _show_config_form(
            cls,
            step_id,
            user_input,
            errors,
            description_placeholders,
            defaults,
            entry_id,
        )

    return _show_config_form(
        cls,
        step_id,
        user_input,
        errors,
        description_placeholders,
        defaults,
        entry_id,
    )
