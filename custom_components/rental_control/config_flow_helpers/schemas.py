# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Schema builders for Rental Control config and options flows."""

from __future__ import annotations

import logging
from typing import Any
from zoneinfo import available_timezones

from homeassistant.const import CONF_NAME
from homeassistant.const import CONF_URL
from homeassistant.const import CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.selector import SelectOptionDict
from homeassistant.helpers.selector import SelectSelector
from homeassistant.helpers.selector import SelectSelectorConfig
from homeassistant.helpers.selector import SelectSelectorMode
from homeassistant.util import dt

from ..const import CODE_GENERATORS
from ..const import CONF_CHECKIN
from ..const import CONF_CHECKOUT
from ..const import CONF_CLEANING_WINDOW
from ..const import CONF_CODE_BUFFER_AFTER
from ..const import CONF_CODE_BUFFER_BEFORE
from ..const import CONF_CODE_GENERATION
from ..const import CONF_CODE_LENGTH
from ..const import CONF_DAYS
from ..const import CONF_ENABLE_KEYMASTER_EVENT_DIAGNOSTICS
from ..const import CONF_EVENT_PREFIX
from ..const import CONF_HONOR_EVENT_TIMES
from ..const import CONF_IGNORE_NON_RESERVED
from ..const import CONF_LOCK_ENTRY
from ..const import CONF_MAX_EVENTS
from ..const import CONF_MAX_NAME_LENGTH
from ..const import CONF_REFRESH_FREQUENCY
from ..const import CONF_SHOULD_UPDATE_CODE
from ..const import CONF_START_SLOT
from ..const import CONF_TIMEZONE
from ..const import CONF_TRIM_NAMES
from ..const import DEFAULT_CHECKIN
from ..const import DEFAULT_CHECKOUT
from ..const import DEFAULT_CLEANING_WINDOW
from ..const import DEFAULT_CODE_BUFFER_AFTER
from ..const import DEFAULT_CODE_BUFFER_BEFORE
from ..const import DEFAULT_CODE_GENERATION
from ..const import DEFAULT_CODE_LENGTH
from ..const import DEFAULT_DAYS
from ..const import DEFAULT_ENABLE_KEYMASTER_EVENT_DIAGNOSTICS
from ..const import DEFAULT_EVENT_PREFIX
from ..const import DEFAULT_HONOR_EVENT_TIMES
from ..const import DEFAULT_MAX_EVENTS
from ..const import DEFAULT_MAX_NAME_LENGTH
from ..const import DEFAULT_REFRESH_FREQUENCY
from ..const import DEFAULT_SHOULD_UPDATE_CODE
from ..const import DEFAULT_START_SLOT
from ..const import DEFAULT_TRIM_NAMES
from ..const import LOCK_MANAGER
from ..const import MIN_NAME_LENGTH
from .models import SchemaBuildContext

_LOGGER = logging.getLogger("custom_components.rental_control.config_flow")
SORTED_TZ = sorted(available_timezones())


def available_lock_managers(hass: HomeAssistant) -> list[str]:
    """Find lock manager configurations to use."""
    data = ["(none)"]
    if LOCK_MANAGER not in hass.data:
        return data

    for entry in hass.config_entries.async_entries(LOCK_MANAGER):
        data.append(entry.title)

    return data


def code_generators() -> list[str]:
    """Return list of code generators available."""
    data = []

    for generator in CODE_GENERATORS:
        data.append(generator["description"])

    return data


def generator_convert(ident: str, to_type: bool = True) -> str:
    """Convert between type and description for generators."""
    if to_type:
        return next(item for item in CODE_GENERATORS if item["description"] == ident)[
            "type"
        ]
    return next(item for item in CODE_GENERATORS if item["type"] == ident)[
        "description"
    ]


def lock_entry_convert(hass: HomeAssistant, entry: str, to_entity: bool = True) -> str:
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


def build_config_schema(
    hass: HomeAssistant,
    user_input: dict[str, Any] | None,
    default_dict: dict[str, Any] | None,
    entry_id: str | None = None,
) -> Any:
    """Build a config-flow schema using defaults as a backup."""
    context = SchemaBuildContext(
        hass=hass,
        user_input=user_input or {},
        defaults=_normalize_defaults(hass, default_dict),
        entry_id=entry_id,
    )
    fields = _base_fields(context)
    if entry_id is not None:
        fields.update(_options_fields(context))
    return cv.vol.Schema(fields, extra=cv.vol.schema_builder.ALLOW_EXTRA)


def _normalize_defaults(
    hass: HomeAssistant, defaults: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Normalize lock-entry defaults before schema construction."""
    if defaults is None:
        return None

    if CONF_LOCK_ENTRY in defaults.keys() and defaults[CONF_LOCK_ENTRY] is None:
        check_dict = defaults.copy()
        check_dict.pop(CONF_LOCK_ENTRY, None)
        defaults = check_dict

    if CONF_LOCK_ENTRY in defaults.keys() and defaults[CONF_LOCK_ENTRY] is not None:
        check_dict = defaults.copy()
        convert = lock_entry_convert(hass, defaults[CONF_LOCK_ENTRY], False)
        check_dict[CONF_LOCK_ENTRY] = convert
        defaults = check_dict

    return defaults


def _get_default(
    context: SchemaBuildContext, key: str, fallback: Any = None
) -> Any | None:
    """Get the default value for a schema key."""
    if context.defaults is not None and context.user_input is not None:
        return context.user_input.get(key, context.defaults.get(key, fallback))
    return None


def _base_fields(context: SchemaBuildContext) -> dict[Any, Any]:
    """Build all base config and options schema fields."""
    fields: dict[Any, Any] = {}
    fields.update(_identity_fields(context))
    fields.update(_refresh_time_fields(context))
    fields.update(_day_lock_fields(context))
    fields.update(_slot_code_fields(context))
    fields.update(_behavior_fields(context))
    fields.update(_trim_fields(context))
    return fields


def _identity_fields(context: SchemaBuildContext) -> dict[Any, Any]:
    """Build name and URL schema fields."""
    return {
        cv.vol.Required(CONF_NAME, default=_get_default(context, CONF_NAME)): cv.string,
        cv.vol.Required(CONF_URL, default=_get_default(context, CONF_URL)): cv.string,
    }


def _refresh_time_fields(context: SchemaBuildContext) -> dict[Any, Any]:
    """Build refresh, timezone, prefix, and time schema fields."""
    return {
        cv.vol.Optional(
            CONF_REFRESH_FREQUENCY,
            default=_get_default(
                context, CONF_REFRESH_FREQUENCY, DEFAULT_REFRESH_FREQUENCY
            ),
        ): cv.positive_int,
        cv.vol.Optional(
            CONF_TIMEZONE,
            default=_get_default(context, CONF_TIMEZONE, str(dt.DEFAULT_TIME_ZONE)),
        ): cv.vol.In(SORTED_TZ),
        cv.vol.Optional(
            CONF_EVENT_PREFIX,
            default=_get_default(context, CONF_EVENT_PREFIX, DEFAULT_EVENT_PREFIX),
        ): cv.string,
        cv.vol.Required(
            CONF_CHECKIN, default=_get_default(context, CONF_CHECKIN, DEFAULT_CHECKIN)
        ): cv.string,
        cv.vol.Required(
            CONF_CHECKOUT,
            default=_get_default(context, CONF_CHECKOUT, DEFAULT_CHECKOUT),
        ): cv.string,
    }


def _day_lock_fields(context: SchemaBuildContext) -> dict[Any, Any]:
    """Build day count and lock selector schema fields."""
    return {
        cv.vol.Optional(
            CONF_DAYS, default=_get_default(context, CONF_DAYS, DEFAULT_DAYS)
        ): cv.positive_int,
        cv.vol.Optional(
            CONF_LOCK_ENTRY, default=_get_default(context, CONF_LOCK_ENTRY, "(none)")
        ): SelectSelector(
            SelectSelectorConfig(
                options=[
                    SelectOptionDict(value=v, label=v)
                    for v in available_lock_managers(context.hass)
                ],
                mode=SelectSelectorMode.DROPDOWN,
            )
        ),
    }


def _slot_code_fields(context: SchemaBuildContext) -> dict[Any, Any]:
    """Build slot, event, code, generator, and update schema fields."""
    return {
        cv.vol.Required(
            CONF_START_SLOT,
            default=_get_default(context, CONF_START_SLOT, DEFAULT_START_SLOT),
        ): cv.positive_int,
        cv.vol.Required(
            CONF_MAX_EVENTS,
            default=_get_default(context, CONF_MAX_EVENTS, DEFAULT_MAX_EVENTS),
        ): cv.positive_int,
        cv.vol.Required(
            CONF_CODE_LENGTH,
            default=_get_default(context, CONF_CODE_LENGTH, DEFAULT_CODE_LENGTH),
        ): cv.positive_int,
        cv.vol.Optional(
            CONF_CODE_GENERATION,
            default=generator_convert(
                ident=str(
                    _get_default(context, CONF_CODE_GENERATION, DEFAULT_CODE_GENERATION)
                ),
                to_type=False,
            ),
        ): cv.vol.In(code_generators()),
        cv.vol.Optional(
            CONF_SHOULD_UPDATE_CODE,
            default=_get_default(
                context, CONF_SHOULD_UPDATE_CODE, DEFAULT_SHOULD_UPDATE_CODE
            ),
        ): cv.boolean,
    }


def _behavior_fields(context: SchemaBuildContext) -> dict[Any, Any]:
    """Build behavior toggle schema fields."""
    return {
        cv.vol.Optional(
            CONF_HONOR_EVENT_TIMES,
            default=_get_default(
                context, CONF_HONOR_EVENT_TIMES, DEFAULT_HONOR_EVENT_TIMES
            ),
        ): cv.boolean,
        cv.vol.Optional(
            CONF_IGNORE_NON_RESERVED,
            default=_get_default(context, CONF_IGNORE_NON_RESERVED, True),
        ): cv.boolean,
        cv.vol.Optional(
            CONF_VERIFY_SSL, default=_get_default(context, CONF_VERIFY_SSL, True)
        ): cv.boolean,
        cv.vol.Optional(
            CONF_CLEANING_WINDOW,
            default=_get_default(
                context, CONF_CLEANING_WINDOW, DEFAULT_CLEANING_WINDOW
            ),
        ): cv.vol.All(cv.vol.Coerce(float), cv.vol.Range(min=0.5, max=48.0)),
    }


def _trim_fields(context: SchemaBuildContext) -> dict[Any, Any]:
    """Build trim-name schema fields."""
    return {
        cv.vol.Optional(
            CONF_TRIM_NAMES,
            default=_get_default(context, CONF_TRIM_NAMES, DEFAULT_TRIM_NAMES),
        ): cv.boolean,
        cv.vol.Optional(
            CONF_MAX_NAME_LENGTH,
            default=_get_default(
                context, CONF_MAX_NAME_LENGTH, DEFAULT_MAX_NAME_LENGTH
            ),
        ): cv.vol.All(cv.vol.Coerce(int), cv.vol.Range(min=MIN_NAME_LENGTH)),
    }


def _options_fields(context: SchemaBuildContext) -> dict[Any, Any]:
    """Build options-only schema fields."""
    return {
        cv.vol.Optional(
            CONF_ENABLE_KEYMASTER_EVENT_DIAGNOSTICS,
            default=_get_default(
                context,
                CONF_ENABLE_KEYMASTER_EVENT_DIAGNOSTICS,
                DEFAULT_ENABLE_KEYMASTER_EVENT_DIAGNOSTICS,
            ),
        ): cv.boolean,
        cv.vol.Optional(
            CONF_CODE_BUFFER_BEFORE,
            default=_get_default(
                context, CONF_CODE_BUFFER_BEFORE, DEFAULT_CODE_BUFFER_BEFORE
            ),
        ): cv.vol.All(cv.vol.Coerce(int), cv.vol.Range(min=0)),
        cv.vol.Optional(
            CONF_CODE_BUFFER_AFTER,
            default=_get_default(
                context, CONF_CODE_BUFFER_AFTER, DEFAULT_CODE_BUFFER_AFTER
            ),
        ): cv.vol.All(cv.vol.Coerce(int), cv.vol.Range(min=0)),
    }
