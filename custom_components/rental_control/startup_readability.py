# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Startup readability refresh helpers for Rental Control."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.switch import DOMAIN as SWITCH
from homeassistant.components.text import DOMAIN as TEXT
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import CALLBACK_TYPE
from homeassistant.core import Event
from homeassistant.core import EventStateChangedData
from homeassistant.core import HomeAssistant
from homeassistant.core import State
from homeassistant.core import callback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.event import async_track_state_change_event

from .const import DOMAIN
from .const import UNSUB_LISTENERS
from .util import get_entry_data

if TYPE_CHECKING:
    import asyncio

    from .coordinator import RentalControlCoordinator

_LOGGER = logging.getLogger(__package__)
_STARTUP_READABILITY_REFRESH_DELAY = 1.5
_STARTUP_READABILITY_WATCHDOG = 10 * 60


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


class _StartupReadabilityWatcher:
    """Watch startup Keymaster readability and run a one-shot refresh."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        coordinator: RentalControlCoordinator,
        entity_ids: list[str],
    ) -> None:
        """Initialize the watcher lifecycle state."""
        self.hass = hass
        self.config_entry = config_entry
        self.coordinator = coordinator
        self.entity_ids = entity_ids
        self.done = False
        self.unsub_state: CALLBACK_TYPE | None = None
        self.unsub_timer: CALLBACK_TYPE | None = None
        self.unsub_watchdog: CALLBACK_TYPE | None = None
        self.refresh_task: asyncio.Task[None] | None = None

    @callback
    def arm(self) -> None:
        """Subscribe callbacks and schedule startup readability checks."""
        self.unsub_state = async_track_state_change_event(
            self.hass, self.entity_ids, self.schedule_refresh
        )
        self.unsub_watchdog = async_call_later(
            self.hass, _STARTUP_READABILITY_WATCHDOG, self.expire
        )
        self.hass.data[DOMAIN][self.config_entry.entry_id][UNSUB_LISTENERS].append(
            self.remove_self
        )
        if _all_managed_slots_readable(self.hass, self.entity_ids):
            self.unsub_timer = async_call_later(
                self.hass,
                _STARTUP_READABILITY_REFRESH_DELAY,
                self.refresh_if_readable,
            )
        _LOGGER.debug(
            "Armed startup readability refresh for %s watching %d entities",
            self.config_entry.entry_id,
            len(self.entity_ids),
        )

    @callback
    def remove_listener_reference(self) -> None:
        """Remove this watcher from entry listener cleanup."""
        entry_data = get_entry_data(self.hass, self.config_entry.entry_id)
        if entry_data is None:
            return
        listeners = entry_data.get(UNSUB_LISTENERS, [])
        if self.remove_self in listeners:
            listeners.remove(self.remove_self)

    @callback
    def cancel_watchers(self) -> None:
        """Unsubscribe state tracking, debounce timer, and watchdog."""
        if self.unsub_timer is not None:
            self.unsub_timer()
            self.unsub_timer = None
        if self.unsub_watchdog is not None:
            self.unsub_watchdog()
            self.unsub_watchdog = None
        if self.unsub_state is not None:
            self.unsub_state()
            self.unsub_state = None

    @callback
    def remove_self(self) -> None:
        """Unsubscribe the startup readability watcher and pending work."""
        self.done = True
        self.cancel_watchers()
        if self.refresh_task is not None and not self.refresh_task.done():
            self.refresh_task.cancel()
        self.refresh_task = None
        self.remove_listener_reference()

    @callback
    def refresh_done(self, _task: asyncio.Task[None]) -> None:
        """Drop unload cleanup after the one-shot refresh task finishes."""
        self.refresh_task = None
        self.remove_listener_reference()

    async def async_refresh_once(self) -> None:
        """Run the one-shot readability refresh."""
        if get_entry_data(self.hass, self.config_entry.entry_id) is None:
            return
        try:
            await self.coordinator.async_refresh()
        except Exception:
            _LOGGER.exception(
                "Startup readability refresh failed for %s",
                self.config_entry.entry_id,
            )

    @callback
    def refresh_if_readable(self, _now) -> None:
        """Refresh once the watched startup entities have settled."""
        self.unsub_timer = None
        if self.done:
            return
        if not _all_managed_slots_readable(self.hass, self.entity_ids):
            return

        self.done = True
        self.cancel_watchers()
        self.refresh_task = self.config_entry.async_create_task(
            self.hass,
            self.async_refresh_once(),
            name=f"{DOMAIN} startup readability refresh {self.config_entry.entry_id}",
        )
        self.refresh_task.add_done_callback(self.refresh_done)

    @callback
    def schedule_refresh(
        self,
        event: Event[EventStateChangedData],
    ) -> None:
        """Debounce readable state-change storms into one refresh."""
        if self.done:
            return

        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")
        if not _is_readable_keymaster_state(new_state):
            return
        if old_state is not None and _is_readable_keymaster_state(old_state):
            return

        if self.unsub_timer is not None:
            self.unsub_timer()
        self.unsub_timer = async_call_later(
            self.hass,
            _STARTUP_READABILITY_REFRESH_DELAY,
            self.refresh_if_readable,
        )

    @callback
    def expire(self, _now) -> None:
        """Give up if Keymaster entities never become readable."""
        _LOGGER.debug(
            "Startup readability watcher expired for %s before slots settled",
            self.config_entry.entry_id,
        )
        self.remove_self()


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

    _StartupReadabilityWatcher(hass, config_entry, coordinator, entity_ids).arm()
