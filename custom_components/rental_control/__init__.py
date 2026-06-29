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

import functools
import logging

from homeassistant.components.button import DOMAIN as BUTTON
from homeassistant.components.datetime import DOMAIN as DATETIME
from homeassistant.components.persistent_notification import async_create
from homeassistant.components.persistent_notification import async_dismiss
from homeassistant.components.switch import DOMAIN as SWITCH
from homeassistant.components.text import DOMAIN as TEXT
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.event import async_track_state_change_event

from .const import CONF_CREATION_DATETIME
from .const import CONF_GENERATE
from .const import COORDINATOR
from .const import DOMAIN
from .const import NAME
from .const import PLATFORMS
from .const import UNSUB_LISTENERS
from .coordinator import RentalControlCoordinator
from .listeners import (
    async_register_keymaster_listener as async_register_keymaster_listener,
)
from .migrations import async_migrate_entry as async_migrate_entry
from .startup_readability import (
    _needs_startup_readability_refresh as _needs_startup_readability_refresh,
)
from .startup_readability import (
    async_arm_startup_readability_refresh as async_arm_startup_readability_refresh,
)
from .util import async_reload_package_platforms
from .util import delete_rc_and_base_folder
from .util import get_entry_data
from .util import handle_state_change

_LOGGER = logging.getLogger(__name__)


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

    # Load Store before Keymaster bootstrap (ordering fix for #597)
    await coordinator.async_load_slot_store()

    # Inject cache-only mappings. Physical Keymaster state is re-read during
    # every coordinator refresh, so missing cache never triggers adoption.
    persisted = coordinator.get_persisted_slot_mappings()
    if persisted and coordinator.event_overrides is not None:
        coordinator.event_overrides.load_persisted_mappings(persisted)

    # Bootstrap Keymaster slot overrides from current HA state before
    # first refresh so overrides are checked against the initial data
    await coordinator.async_setup_keymaster_overrides()

    startup_slots_unreadable, _ = _needs_startup_readability_refresh(hass, coordinator)

    hass.data[DOMAIN][config_entry.entry_id] = {
        COORDINATOR: coordinator,
        UNSUB_LISTENERS: [],
    }
    coordinator._checkin_restore_pending = True

    # Perform first data refresh before platform setup to guarantee
    # coordinator.data is populated when entities are created
    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        hass.data[DOMAIN].pop(config_entry.entry_id, None)
        raise

    async_arm_startup_readability_refresh(
        hass,
        config_entry,
        coordinator,
        startup_slots_unreadable=startup_slots_unreadable,
    )

    # Start listeners if needed
    await async_start_listener(hass, config_entry)

    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)
    coordinator._checkin_restore_pending = False

    # Register keymaster event bus listener after platform setup
    # so the checkin sensor reference is available (T024/T026)
    if coordinator.lockname:
        async_register_keymaster_listener(hass, config_entry)

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

    unload_ok = await hass.config_entries.async_unload_platforms(
        config_entry, PLATFORMS
    )

    if unload_ok:
        # Remove all package files and the base folder if needed
        await hass.async_add_executor_job(delete_rc_and_base_folder, hass, config_entry)

        await async_reload_package_platforms(hass)

        # Unsubscribe from any listeners
        for unsub_listener in list(
            hass.data[DOMAIN][config_entry.entry_id].get(UNSUB_LISTENERS, [])
        ):
            unsub_listener()
        hass.data[DOMAIN][config_entry.entry_id].get(UNSUB_LISTENERS, []).clear()

        hass.data[DOMAIN].pop(config_entry.entry_id)

    async_dismiss(hass, notification_id)

    return unload_ok


async def update_listener(hass: HomeAssistant, config_entry: ConfigEntry) -> None:
    """Update listener."""
    # No need to update if the options match the data
    if not config_entry.options:
        return

    # Guard against listener firing after entry has been unloaded
    entry_data = get_entry_data(hass, config_entry.entry_id)
    if entry_data is None:
        return

    new_data = config_entry.options.copy()
    new_data.pop(CONF_GENERATE, None)

    coordinator = entry_data[COORDINATOR]

    # do not update the creation datetime if it already exists (which it should)
    new_data[CONF_CREATION_DATETIME] = coordinator.created

    hass.config_entries.async_update_entry(
        entry=config_entry,
        unique_id=config_entry.unique_id,
        data=new_data,
        title=new_data[CONF_NAME],
        options={},
    )

    await coordinator.update_config(new_data)

    entry_data = get_entry_data(hass, config_entry.entry_id)
    if entry_data is None:
        return
    coordinator = entry_data[COORDINATOR]

    # Unsubscribe to any listeners so we can create new ones
    for unsub_listener in list(entry_data.get(UNSUB_LISTENERS, [])):
        unsub_listener()
    entry_data.get(UNSUB_LISTENERS, []).clear()

    if coordinator.lockname:
        await async_start_listener(hass, config_entry)
        async_register_keymaster_listener(hass, config_entry)
    else:
        _LOGGER.debug("Skipping re-adding listeners")


async def async_start_listener(hass: HomeAssistant, config_entry: ConfigEntry) -> None:
    """Start tracking updates to entities."""
    entities: list[str] = []

    _LOGGER.debug("entry_id = '%s'", config_entry.unique_id)

    coordinator = hass.data[DOMAIN][config_entry.entry_id][COORDINATOR]
    lockname = coordinator.lockname

    _LOGGER.debug("lockname = '%s'", lockname)

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
            list(entities),
            functools.partial(handle_state_change, hass, config_entry),
        )
    )
