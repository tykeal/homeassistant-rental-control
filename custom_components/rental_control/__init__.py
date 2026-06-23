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
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import CALLBACK_TYPE
from homeassistant.core import Event
from homeassistant.core import EventStateChangedData
from homeassistant.core import HomeAssistant
from homeassistant.core import State
from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.event import async_call_later
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
from .util import async_reload_package_platforms
from .util import delete_rc_and_base_folder
from .util import get_entry_data
from .util import handle_state_change

_LOGGER = logging.getLogger(__name__)
_STARTUP_READABILITY_REFRESH_DELAY = 1.5
_STARTUP_READABILITY_WATCHDOG = 10 * 60


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

    # Perform first data refresh before platform setup to guarantee
    # coordinator.data is populated when entities are created
    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        raise

    hass.data[DOMAIN][config_entry.entry_id] = {
        COORDINATOR: coordinator,
        UNSUB_LISTENERS: [],
    }

    async_arm_startup_readability_refresh(
        hass,
        config_entry,
        coordinator,
        startup_slots_unreadable=startup_slots_unreadable,
    )

    # Start listeners if needed
    await async_start_listener(hass, config_entry)

    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)

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


def _managed_slot_readability_entity_ids(
    coordinator: RentalControlCoordinator,
) -> list[str]:
    """Return the managed Keymaster entities needed to observe slot state."""
    if not coordinator.lockname:
        return []

    entities: list[str] = []
    for slot in range(
        coordinator.start_slot, coordinator.start_slot + coordinator.max_events
    ):
        entities.extend(
            (
                f"{TEXT}.{coordinator.lockname}_code_slot_{slot}_name",
                f"{TEXT}.{coordinator.lockname}_code_slot_{slot}_pin",
                f"{SWITCH}.{coordinator.lockname}_code_slot_{slot}_enabled",
            )
        )
    return entities


def _is_readable_keymaster_state(state: State | None) -> bool:
    """Return whether a watched Keymaster entity can be read."""
    return state is not None and state.state != STATE_UNAVAILABLE


def _all_managed_slots_readable(
    hass: HomeAssistant,
    entity_ids: list[str],
) -> bool:
    """Return whether all watched managed-slot entities are readable."""
    return all(
        _is_readable_keymaster_state(hass.states.get(entity_id))
        for entity_id in entity_ids
    )


def _needs_startup_readability_refresh(
    hass: HomeAssistant,
    coordinator: RentalControlCoordinator,
) -> tuple[bool, list[str]]:
    """Return whether startup saw unreadable managed Keymaster entities."""
    entity_ids = _managed_slot_readability_entity_ids(coordinator)
    if not entity_ids:
        return False, entity_ids
    return not _all_managed_slots_readable(hass, entity_ids), entity_ids


@callback
def async_arm_startup_readability_refresh(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    coordinator: RentalControlCoordinator,
    *,
    startup_slots_unreadable: bool = False,
) -> None:
    """Arm a one-shot refresh when startup Keymaster slots become readable."""
    needs_refresh, entity_ids = _needs_startup_readability_refresh(hass, coordinator)
    if not needs_refresh and not startup_slots_unreadable:
        return

    done = False
    unsub_state: CALLBACK_TYPE | None = None
    unsub_timer: CALLBACK_TYPE | None = None
    unsub_watchdog: CALLBACK_TYPE | None = None
    refresh_task = None

    @callback
    def _remove_listener_reference() -> None:
        """Remove this watcher from entry listener cleanup."""
        entry_data = get_entry_data(hass, config_entry.entry_id)
        if entry_data is None:
            return
        listeners = entry_data.get(UNSUB_LISTENERS, [])
        if _remove_self in listeners:
            listeners.remove(_remove_self)

    @callback
    def _cancel_watchers() -> None:
        """Unsubscribe state tracking, debounce timer, and watchdog."""
        nonlocal unsub_state, unsub_timer, unsub_watchdog

        if unsub_timer is not None:
            unsub_timer()
            unsub_timer = None
        if unsub_watchdog is not None:
            unsub_watchdog()
            unsub_watchdog = None
        if unsub_state is not None:
            unsub_state()
            unsub_state = None

    @callback
    def _remove_self() -> None:
        """Unsubscribe the startup readability watcher and pending work."""
        nonlocal done, refresh_task

        done = True
        _cancel_watchers()
        if refresh_task is not None and not refresh_task.done():
            refresh_task.cancel()
        refresh_task = None
        _remove_listener_reference()

    @callback
    def _refresh_done(_task) -> None:
        """Drop unload cleanup after the one-shot refresh task finishes."""
        nonlocal refresh_task

        refresh_task = None
        _remove_listener_reference()

    async def _async_refresh_once() -> None:
        """Run the one-shot readability refresh."""
        if get_entry_data(hass, config_entry.entry_id) is None:
            return
        try:
            await coordinator.async_refresh()
        except Exception:
            _LOGGER.exception(
                "Startup readability refresh failed for %s",
                config_entry.entry_id,
            )

    @callback
    def _refresh_if_readable(_now) -> None:
        """Refresh once the watched startup entities have settled."""
        nonlocal done, refresh_task, unsub_timer

        unsub_timer = None
        if done:
            return
        if not _all_managed_slots_readable(hass, entity_ids):
            return

        done = True
        _cancel_watchers()
        refresh_task = config_entry.async_create_task(
            hass,
            _async_refresh_once(),
            name=f"{DOMAIN} startup readability refresh {config_entry.entry_id}",
        )
        refresh_task.add_done_callback(_refresh_done)

    @callback
    def _schedule_refresh(
        event: Event[EventStateChangedData],
    ) -> None:
        """Debounce readable state-change storms into one refresh."""
        nonlocal unsub_timer

        if done:
            return

        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")
        if not _is_readable_keymaster_state(new_state):
            return
        if old_state is not None and _is_readable_keymaster_state(old_state):
            return

        if unsub_timer is not None:
            unsub_timer()
        unsub_timer = async_call_later(
            hass,
            _STARTUP_READABILITY_REFRESH_DELAY,
            _refresh_if_readable,
        )

    @callback
    def _expire(_now) -> None:
        """Give up if Keymaster entities never become readable."""
        _LOGGER.debug(
            "Startup readability watcher expired for %s before slots settled",
            config_entry.entry_id,
        )
        _remove_self()

    unsub_state = async_track_state_change_event(hass, entity_ids, _schedule_refresh)
    unsub_watchdog = async_call_later(hass, _STARTUP_READABILITY_WATCHDOG, _expire)
    hass.data[DOMAIN][config_entry.entry_id][UNSUB_LISTENERS].append(_remove_self)
    if _all_managed_slots_readable(hass, entity_ids):
        unsub_timer = async_call_later(
            hass,
            _STARTUP_READABILITY_REFRESH_DELAY,
            _refresh_if_readable,
        )
    _LOGGER.debug(
        "Armed startup readability refresh for %s watching %d entities",
        config_entry.entry_id,
        len(entity_ids),
    )


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
