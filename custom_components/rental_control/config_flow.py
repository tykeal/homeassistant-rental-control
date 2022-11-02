"""Config flow for Rental Control integration."""
import logging
import os
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
from homeassistant.util import dt
from pytz import common_timezones
from voluptuous.schema_builder import ALLOW_EXTRA

from .const import CODE_GENERATORS
from .const import CONF_CHECKIN
from .const import CONF_CHECKOUT
from .const import CONF_CODE_GENERATION
from .const import CONF_CODE_LENGTH
from .const import CONF_CREATION_DATETIME
from .const import CONF_DAYS
from .const import CONF_EVENT_PREFIX
from .const import CONF_GENERATE
from .const import CONF_IGNORE_NON_RESERVED
from .const import CONF_LOCK_ENTRY
from .const import CONF_MAX_EVENTS
from .const import CONF_PATH
from .const import CONF_REFRESH_FREQUENCY
from .const import CONF_START_SLOT
from .const import CONF_TIMEZONE
from .const import DEFAULT_CHECKIN
from .const import DEFAULT_CHECKOUT
from .const import DEFAULT_CODE_GENERATION
from .const import DEFAULT_CODE_LENGTH
from .const import DEFAULT_DAYS
from .const import DEFAULT_EVENT_PREFIX
from .const import DEFAULT_GENERATE
from .const import DEFAULT_MAX_EVENTS
from .const import DEFAULT_PATH
from .const import DEFAULT_REFRESH_FREQUENCY
from .const import DEFAULT_START_SLOT
from .const import DOMAIN
from .const import LOCK_MANAGER
from .const import REQUEST_TIMEOUT
from .util import gen_uuid

_LOGGER = logging.getLogger(__name__)

sorted_tz = common_timezones
sorted_tz.sort()


@config_entries.HANDLERS.register(DOMAIN)
class RentalControlFlowHandler(config_entries.ConfigFlow):
    """Handle the config flow for Rental Control."""

    VERSION = 3

    DEFAULTS = {
        CONF_CHECKIN: DEFAULT_CHECKIN,
        CONF_CHECKOUT: DEFAULT_CHECKOUT,
        CONF_DAYS: DEFAULT_DAYS,
        CONF_IGNORE_NON_RESERVED: True,
        CONF_EVENT_PREFIX: DEFAULT_EVENT_PREFIX,
        CONF_MAX_EVENTS: DEFAULT_MAX_EVENTS,
        CONF_REFRESH_FREQUENCY: DEFAULT_REFRESH_FREQUENCY,
        CONF_TIMEZONE: str(dt.DEFAULT_TIME_ZONE),
        CONF_VERIFY_SSL: True,
    }

    def __init__(self):
        """Setup the RentalControlFlowHandler."""
        self.created = str(dt.now())

    async def _get_unique_id(self, user_input) -> Dict[str, str]:
        """Generate the unique_id."""
        existing_entry = await self.async_set_unique_id(
            gen_uuid(self.created), raise_on_progress=True
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

    async def async_step_init(
        self,
        user_input: Dict[str, Any] = None,
    ) -> Any:
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


def _code_generators() -> list:
    """Return list of code genrators available."""

    data = []

    for generator in CODE_GENERATORS:
        data.append(generator["description"])

    return data


def _generator_convert(ident: str, to_type: bool = True) -> str:
    """Convert between type and description for generators."""

    if to_type:
        return next(item for item in CODE_GENERATORS if item["description"] == ident)[
            "type"
        ]
    else:
        return next(item for item in CODE_GENERATORS if item["type"] == ident)[
            "description"
        ]


def _lock_entry_convert(hass: HomeAssistant, entry: str, to_entity: bool = True) -> str:
    """Convert between name and entity for lock entries."""

    _LOGGER.debug(
        "In _lock_entry_convert, entry: '%s', to_entity: '%s'", entry, to_entity
    )

    if to_entity:
        _LOGGER.debug("to entity")
        for lock_entry in hass.config_entries.async_entries(LOCK_MANAGER):
            if entry == lock_entry.title:
                _LOGGER.debug("'%s' becomes '%s'", entry, lock_entry.data["lockname"])
                return str(lock_entry.data["lockname"])
    else:
        _LOGGER.debug("from entity")
        for lock_entry in hass.config_entries.async_entries(LOCK_MANAGER):
            if entry == lock_entry.data["lockname"]:
                _LOGGER.debug("'%s' becomes '%s'", entry, lock_entry.title)
                return str(lock_entry.title)

    _LOGGER.debug("no conversion done")
    return entry


def _get_schema(
    hass: HomeAssistant,
    user_input: Optional[Dict[str, Any]],
    default_dict: Optional[Dict[str, Any]],
    entry_id: str = None,
) -> vol.Schema:
    """Gets a schema using the default_dict as a backup."""
    if user_input is None:
        user_input = {}

    if default_dict is not None:
        if (
            CONF_LOCK_ENTRY in default_dict.keys()
            and default_dict[CONF_LOCK_ENTRY] is None
        ):
            check_dict = default_dict.copy()
            check_dict.pop(CONF_LOCK_ENTRY, None)
            default_dict = check_dict

        if (
            CONF_LOCK_ENTRY in default_dict.keys()
            and default_dict[CONF_LOCK_ENTRY] is not None
        ):
            check_dict = default_dict.copy()
            convert = _lock_entry_convert(hass, default_dict[CONF_LOCK_ENTRY], False)
            check_dict[CONF_LOCK_ENTRY] = convert
            default_dict = check_dict

    def _get_default(key: str, fallback_default: Any = None) -> None:
        """Gets default value for key."""
        if default_dict is not None and user_input is not None:
            return user_input.get(key, default_dict.get(key, fallback_default))
        else:
            return None

    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=_get_default(CONF_NAME)): cv.string,
            vol.Required(CONF_URL, default=_get_default(CONF_URL)): cv.string,
            vol.Optional(
                CONF_REFRESH_FREQUENCY,
                default=_get_default(CONF_REFRESH_FREQUENCY, DEFAULT_REFRESH_FREQUENCY),
            ): cv.positive_int,
            vol.Optional(
                CONF_TIMEZONE,
                default=_get_default(CONF_TIMEZONE, str(dt.DEFAULT_TIME_ZONE)),
            ): vol.In(sorted_tz),
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
            vol.Required(
                CONF_CODE_LENGTH,
                default=_get_default(CONF_CODE_LENGTH, DEFAULT_CODE_LENGTH),
            ): cv.positive_int,
            vol.Optional(
                CONF_CODE_GENERATION,
                default=_generator_convert(
                    ident=str(
                        _get_default(CONF_CODE_GENERATION, DEFAULT_CODE_GENERATION)
                    ),
                    to_type=False,
                ),
            ): vol.In(_code_generators()),
            vol.Required(
                CONF_PATH, default=_get_default(CONF_PATH, DEFAULT_PATH)
            ): cv.string,
            vol.Optional(
                CONF_IGNORE_NON_RESERVED,
                default=_get_default(CONF_IGNORE_NON_RESERVED, True),
            ): cv.boolean,
            vol.Optional(
                CONF_VERIFY_SSL, default=_get_default(CONF_VERIFY_SSL, True)
            ): cv.boolean,
        },
        extra=ALLOW_EXTRA,
    )


def _show_config_form(
    cls: Union[RentalControlFlowHandler, RentalControlOptionsFlow],
    step_id: str,
    user_input: Optional[Dict[str, Any]],
    errors: Dict[str, str],
    description_placeholders: Dict[str, str],
    defaults: Dict[str, Any] = None,
    entry_id: str = None,
) -> Any:
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
    user_input: Optional[Dict[str, Any]],
    defaults: Dict[str, Any] = None,
    entry_id: str = None,
):
    """Start a config flow."""
    errors = {}
    description_placeholders: Dict[str, str] = {}

    if user_input is not None:
        # Regular flow has an async function
        if hasattr(cls, "_get_unique_id"):
            errors.update(await cls._get_unique_id(user_input))

        # Validate user input
        try:
            cv.url(user_input["url"])
            # We require that the URL be an SSL URL
            if not re.search("^https://", user_input[CONF_URL]):
                errors[CONF_URL] = "invalid_url"
            else:
                session = async_get_clientsession(
                    cls.hass, verify_ssl=user_input[CONF_VERIFY_SSL]
                )
                with async_timeout.timeout(REQUEST_TIMEOUT):
                    resp = await session.get(user_input[CONF_URL])
                if resp.status != 200:
                    _LOGGER.error(
                        "%s returned %s - %s",
                        user_input[CONF_URL],
                        resp.status,
                        resp.reason,
                    )
                    errors[CONF_URL] = "unknown"
                else:
                    # We require text/calendar in the content-type header
                    if "text/calendar" not in resp.content_type:
                        errors[CONF_URL] = "bad_ics"
        except vol.Invalid as err:
            _LOGGER.exception(err.msg)
            errors[CONF_URL] = "invalid_url"

        if (
            user_input[CONF_REFRESH_FREQUENCY] < 0
            or user_input[CONF_REFRESH_FREQUENCY] > 1440
        ):
            errors[CONF_REFRESH_FREQUENCY] = "bad_refresh"

        try:
            cv.time(user_input[CONF_CHECKIN])
        except vol.Invalid as err:
            _LOGGER.exception(err.msg)
            errors[CONF_CHECKIN] = "bad_time"

        try:
            cv.time(user_input[CONF_CHECKOUT])
        except vol.Invalid as err:
            _LOGGER.exception(err.msg)
            errors[CONF_CHECKOUT] = "bad_time"

        if user_input[CONF_DAYS] < 1:
            errors[CONF_DAYS] = "bad_minimum"

        if user_input[CONF_MAX_EVENTS] < 1:
            errors[CONF_MAX_EVENTS] = "bad_minimum"

        if (
            user_input[CONF_CODE_LENGTH] < DEFAULT_CODE_LENGTH
            or (user_input[CONF_CODE_LENGTH] % 2) != 0
        ):
            errors[CONF_CODE_LENGTH] = "bad_code_length"

        # Convert code generator to proper type
        user_input[CONF_CODE_GENERATION] = _generator_convert(
            ident=user_input[CONF_CODE_GENERATION], to_type=True
        )

        # Validate that path is relative
        if os.path.isabs(user_input[CONF_PATH]):
            errors[CONF_PATH] = "invalid_path"

        if not errors:
            # Only do this conversion if there are no errors and it needs to be
            # done. Doing this before the errors check will lead to later
            # validation issues should the user not reset the lock entry
            # Convert (none) to None
            if user_input[CONF_LOCK_ENTRY] == "(none)":
                user_input[CONF_LOCK_ENTRY] = None

            if user_input[CONF_LOCK_ENTRY] is not None:
                user_input[CONF_LOCK_ENTRY] = _lock_entry_convert(
                    cls.hass, user_input[CONF_LOCK_ENTRY], True
                )

            if hasattr(cls, "created"):
                user_input[CONF_CREATION_DATETIME] = cls.created

            if user_input[CONF_LOCK_ENTRY]:
                user_input[CONF_GENERATE] = DEFAULT_GENERATE

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
