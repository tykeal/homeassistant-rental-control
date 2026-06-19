# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>

"""Configuration entry migrations for Rental Control."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_CODE_BUFFER_AFTER
from .const import CONF_CODE_BUFFER_BEFORE
from .const import CONF_CODE_LENGTH
from .const import CONF_GENERATE
from .const import CONF_HONOR_EVENT_TIMES
from .const import CONF_MAX_NAME_LENGTH
from .const import CONF_PATH
from .const import CONF_SHOULD_UPDATE_CODE
from .const import CONF_TRIM_NAMES
from .const import DEFAULT_CODE_BUFFER_AFTER
from .const import DEFAULT_CODE_BUFFER_BEFORE
from .const import DEFAULT_CODE_LENGTH
from .const import DEFAULT_GENERATE
from .const import DEFAULT_MAX_NAME_LENGTH

_LOGGER = logging.getLogger(__name__)


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate configuration."""
    version = config_entry.version

    # Versions 1 and 2 are no longer supported (oldest supported: v0.9.0 = version 3)
    if version < 3:
        _LOGGER.error(
            "Config entry version %s is too old to migrate; "
            "please remove and re-add the integration",
            version,
        )
        return False

    if version == 3:
        version = _migrate_v3_to_v4(hass, config_entry, version)

    if version == 4:
        version = _migrate_v4_to_v5(hass, config_entry, version)

    if version == 5:
        version = _migrate_v5_to_v6(hass, config_entry, version)

    if version == 6:
        version = _migrate_v6_to_v7(hass, config_entry, version)

    if version == 7:
        version = _migrate_v7_to_v8(hass, config_entry, version)

    if version == 8:
        version = _migrate_v8_to_v9(hass, config_entry, version)

    if version == 9:
        version = _migrate_v9_to_v10(hass, config_entry, version)

    return True


def _migrate_v3_to_v4(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    version: int,
) -> int:
    """Migrate version 3 entries to version 4."""
    _LOGGER.debug("Migrating from version %s", version)
    if CONF_CODE_LENGTH not in config_entry.data:
        data = config_entry.data.copy()
        data[CONF_CODE_LENGTH] = DEFAULT_CODE_LENGTH
        hass.config_entries.async_update_entry(
            entry=config_entry,
            unique_id=config_entry.unique_id,
            data=data,
            version=4,
        )

    version = 4
    _LOGGER.debug("Migration to version %s complete", config_entry.version)
    return version


def _migrate_v4_to_v5(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    version: int,
) -> int:
    """Migrate version 4 entries to version 5."""
    _LOGGER.debug("Migrating from version %s", version)

    data = config_entry.data.copy()
    data[CONF_GENERATE] = DEFAULT_GENERATE
    hass.config_entries.async_update_entry(
        entry=config_entry,
        unique_id=config_entry.unique_id,
        data=data,
        version=5,
    )

    version = 5
    _LOGGER.debug("Migration to version %s complete", config_entry.version)
    return version


def _migrate_v5_to_v6(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    version: int,
) -> int:
    """Migrate version 5 entries to version 6."""
    _LOGGER.debug("Migrating from version %s", version)

    data = config_entry.data.copy()
    data.pop(CONF_PATH, None)
    hass.config_entries.async_update_entry(
        entry=config_entry,
        unique_id=config_entry.unique_id,
        data=data,
        version=6,
    )

    version = 6
    _LOGGER.debug("Migration to version %s complete", config_entry.version)
    return version


def _migrate_v6_to_v7(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    version: int,
) -> int:
    """Migrate version 6 entries to version 7."""
    _LOGGER.debug("Migrating from version %s", version)

    data = config_entry.data.copy()
    # Default to False since prior versions didn't have this
    # new setups will default to True
    data[CONF_SHOULD_UPDATE_CODE] = False
    hass.config_entries.async_update_entry(
        entry=config_entry,
        unique_id=config_entry.unique_id,
        data=data,
        version=7,
    )

    version = 7
    _LOGGER.debug("Migration to version %s complete", config_entry.version)
    return version


def _migrate_v7_to_v8(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    version: int,
) -> int:
    """Migrate version 7 entries to version 8."""
    _LOGGER.debug("Migrating from version %s", version)

    data = config_entry.data.copy()
    if CONF_HONOR_EVENT_TIMES not in data:
        data[CONF_HONOR_EVENT_TIMES] = False
    hass.config_entries.async_update_entry(
        entry=config_entry,
        unique_id=config_entry.unique_id,
        data=data,
        version=8,
    )

    version = 8
    _LOGGER.debug("Migration to version %s complete", config_entry.version)
    return version


def _migrate_v8_to_v9(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    version: int,
) -> int:
    """Migrate version 8 entries to version 9."""
    _LOGGER.debug("Migrating from version %s", version)

    data = config_entry.data.copy()
    data[CONF_TRIM_NAMES] = False
    data[CONF_MAX_NAME_LENGTH] = DEFAULT_MAX_NAME_LENGTH
    hass.config_entries.async_update_entry(
        entry=config_entry,
        unique_id=config_entry.unique_id,
        data=data,
        version=9,
    )

    version = 9
    _LOGGER.debug("Migration to version %s complete", config_entry.version)
    return version


def _migrate_v9_to_v10(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    version: int,
) -> int:
    """Migrate version 9 entries to version 10."""
    _LOGGER.debug("Migrating from version %s", version)

    data = config_entry.data.copy()
    data.setdefault(CONF_CODE_BUFFER_BEFORE, DEFAULT_CODE_BUFFER_BEFORE)
    data.setdefault(CONF_CODE_BUFFER_AFTER, DEFAULT_CODE_BUFFER_AFTER)
    hass.config_entries.async_update_entry(
        entry=config_entry,
        unique_id=config_entry.unique_id,
        data=data,
        version=10,
    )

    version = 10
    _LOGGER.debug("Migration to version %s complete", config_entry.version)
    return version
