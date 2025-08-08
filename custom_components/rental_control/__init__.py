# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2021 Andrew Grimberg <tykeal@bardicgrove.org>
##############################################################################
# COPYRIGHT 2021 Andrew Grimberg
#
# All rights reserved. This program and the accompanying materials
# are made available under the terms of the Apache 2.0 License
# which accompanies this distribution, and is available at
# https://www.apache.org/licenses/LICENSE-2.0
#
# Contributors:
#   Andrew Grimberg - Initial implementation
##############################################################################
"""The Rental Control integration."""

from __future__ import annotations

import asyncio
import functools
import logging

from homeassistant.components.button import DOMAIN as BUTTON
from homeassistant.components.datetime import DOMAIN as DATETIME
from homeassistant.components.text import DOMAIN as TEXT
from homeassistant.components.switch import DOMAIN as SWITCH
from homeassistant.components.persistent_notification import async_create
from homeassistant.components.persistent_notification import async_dismiss
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt
import voluptuous as vol

from .config_flow import _lock_entry_convert as lock_entry_convert
from .const import CONF_CODE_LENGTH
from .const import CONF_CREATION_DATETIME
from .const import CONF_GENERATE
from .const import CONF_LOCK_ENTRY
from .const import CONF_PATH
from .const import CONF_SHOULD_UPDATE_CODE
from .const import COORDINATOR
from .const import DEFAULT_CODE_LENGTH
from .const import DEFAULT_GENERATE
from .const import DOMAIN
from .const import NAME
from .const import PLATFORMS
from .const import UNSUB_LISTENERS
from .coordinator import RentalControlCoordinator
from .util import async_reload_package_platforms
from .util import delete_rc_and_base_folder
from .util import gen_uuid
from .util import handle_state_change

_LOGGER = logging.getLogger(__name__)


CONFIG_SCHEMA = vol.Schema({DOMAIN: vol.Schema({})}, extra=vol.ALLOW_EXTRA)


def setup(hass, config):  # pylint: disable=unused-argument
    """Set up this integration with config flow."""
    return True


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Set up Rental Control from a config entry."""
    config = config_entry.data
    _LOGGER.debug(
        "Running init async_setup_entry for calendar %s", config.get(CONF_NAME)
    )

    should_generate_package = config.get(CONF_GENERATE)

    updated_config = config.copy()
    updated_config.pop(CONF_GENERATE, None)
    if updated_config != config_entry.data:
        hass.config_entries.async_update_entry(config_entry, data=updated_config)

    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}

    coordinator = RentalControlCoordinator(
        hass=hass,
        config_entry=config_entry,
    )

    hass.data[DOMAIN][config_entry.entry_id] = {
        COORDINATOR: coordinator,
        UNSUB_LISTENERS: [],
    }

    # Start listeners if needed
    await async_start_listener(hass, config_entry)

    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)

    config_entry.add_update_listener(update_listener)

    # remove files if needed
    if should_generate_package:
        delete_rc_and_base_folder(hass, config_entry)

    return True


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    """Handle removal of an entry."""
    config = config_entry.data
    rc_name = config.get(CONF_NAME)
    _LOGGER.debug("Running async_unload_entry for rental_control %s", rc_name)

    notification_id = f"{DOMAIN}_{rc_name}_unload"
    async_create(
        hass,
        (
            f"Removing `{rc_name}` and all of the files that were generated for "
            "it. This may take some time so don't panic. This message will "
            "automatically clear when removal is complete."
        ),
        title=f"{NAME} - Removing `{rc_name}`",
        notification_id=notification_id,
    )

    unload_ok = all(
        await asyncio.gather(
            *[
                hass.config_entries.async_forward_entry_unload(config_entry, component)
                for component in PLATFORMS
            ]
        )
    )

    if unload_ok:
        # Remove all package files and the base folder if needed
        await hass.async_add_executor_job(delete_rc_and_base_folder, hass, config_entry)

        await async_reload_package_platforms(hass)

        # Unsubscribe from any listeners
        for unsub_listener in hass.data[DOMAIN][config_entry.entry_id].get(
            UNSUB_LISTENERS, []
        ):
            unsub_listener()
        hass.data[DOMAIN][config_entry.entry_id].get(UNSUB_LISTENERS, []).clear()

        hass.data[DOMAIN].pop(config_entry.entry_id)

    async_dismiss(hass, notification_id)

    return unload_ok


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate configuration."""

    version = config_entry.version

    # 1 -> 2: Migrate keys
    if version == 1:
        _LOGGER.debug("Migrating from version %s", version)
        data = config_entry.data.copy()

        data[CONF_CREATION_DATETIME] = str(dt.now())
        hass.config_entries.async_update_entry(
            entry=config_entry,
            unique_id=gen_uuid(data[CONF_CREATION_DATETIME]),
            data=data,
            version=2,
        )
        version = 2
        _LOGGER.debug("Migration to version %s complete", config_entry.version)

    # 2 -> 3: Migrate lock
    if version == 2:
        _LOGGER.debug("Migrating from version %s", version)
        if (
            CONF_LOCK_ENTRY in config_entry.data
            and config_entry.data[CONF_LOCK_ENTRY] is not None
        ):
            data = config_entry.data.copy()
            convert = lock_entry_convert(hass, config_entry.data[CONF_LOCK_ENTRY], True)
            data[CONF_LOCK_ENTRY] = convert
            hass.config_entries.async_update_entry(
                entry=config_entry,
                unique_id=config_entry.unique_id,
                data=data,
                version=3,
            )

        version = 3
        _LOGGER.debug("Migration to version %s complete", config_entry.version)

    # 3 -> 4: Migrate code length
    if version == 3:
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

    # 4 -> 5: Drop startup automation
    if version == 4:
        _LOGGER.debug(f"Migrating from version {version}")

        data = config_entry.data.copy()
        data[CONF_GENERATE] = DEFAULT_GENERATE
        hass.config_entries.async_update_entry(
            entry=config_entry,
            unique_id=config_entry.unique_id,
            data=data,
            version=5,
        )

        version = 5
        _LOGGER.debug(f"Migration to version {config_entry.version} complete")

    # 5 -> 6: Drop package_path from configuration
    if version == 5:
        _LOGGER.debug(f"Migrating from version {version}")

        data = config_entry.data.copy()
        data.pop(CONF_PATH, None)
        hass.config_entries.async_update_entry(
            entry=config_entry,
            unique_id=config_entry.unique_id,
            data=data,
            version=6,
        )

        version = 6
        _LOGGER.debug(f"Migration to version {config_entry.version} complete")

    # 6 -> 7: Add should_update_code to configuration
    if version == 6:
        _LOGGER.debug(f"Migrating from version {version}")

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
        _LOGGER.debug(f"Migration to version {config_entry.version} complete")

    return True


async def update_listener(hass: HomeAssistant, config_entry: ConfigEntry) -> None:
    """Update listener."""
    # No need to update if the options match the data
    if not config_entry.options:
        return

    new_data = config_entry.options.copy()
    new_data.pop(CONF_GENERATE, None)

    coordinator = hass.data[DOMAIN][config_entry.entry_id][COORDINATOR]

    # do not update the creation datetime if it already exists (which it should)
    new_data[CONF_CREATION_DATETIME] = coordinator.created

    hass.config_entries.async_update_entry(
        entry=config_entry,
        unique_id=config_entry.unique_id,
        data=new_data,
        title=new_data[CONF_NAME],
        options={},
    )

    # Update the calendar config
    coordinator.update_config(new_data)

    # Unsubscribe to any listeners so we can create new ones
    for unsub_listener in hass.data[DOMAIN][config_entry.entry_id].get(
        UNSUB_LISTENERS, []
    ):
        unsub_listener()
    hass.data[DOMAIN][config_entry.entry_id].get(UNSUB_LISTENERS, []).clear()

    if coordinator.lockname:
        await async_start_listener(hass, config_entry)
    else:
        _LOGGER.debug("Skipping re-adding listeners")


async def async_start_listener(hass: HomeAssistant, config_entry: ConfigEntry) -> None:
    """Start tracking updates to entities."""
    entities: list[str] = []

    _LOGGER.debug(f"entry_id = '{config_entry.unique_id}'")

    coordinator = hass.data[DOMAIN][config_entry.entry_id][COORDINATOR]
    lockname = coordinator.lockname

    _LOGGER.debug(f"lockname = '{lockname}'")

    for i in range(
        coordinator.start_slot, coordinator.start_slot + coordinator.max_events
    ):
        entities.append(f"{SWITCH}.{lockname}_code_slot_{i}_enabled")
        entities.append(f"{TEXT}.{lockname}_code_slot_{i}_pin")
        entities.append(f"{TEXT}.{lockname}_code_slot_{i}_name")
        entities.append(f"{DATETIME}.{lockname}_code_slot_{i}_date_range_start")
        entities.append(f"{DATETIME}.{lockname}_code_slot_{i}_date_range_end")
        entities.append(f"{BUTTON}.{lockname}_code_slot_{i}_reset")

    hass.data[DOMAIN][config_entry.entry_id][UNSUB_LISTENERS].append(
        async_track_state_change_event(
            hass,
            [entity for entity in entities],
            functools.partial(handle_state_change, hass, config_entry),
        )
    )
