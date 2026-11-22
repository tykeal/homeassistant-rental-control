# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Lifecycle-driven state feedback listener binding."""

from __future__ import annotations

from dataclasses import replace
import functools
import logging
from typing import Any

from homeassistant.components.button import DOMAIN as BUTTON
from homeassistant.components.datetime import DOMAIN as DATETIME
from homeassistant.components.switch import DOMAIN as SWITCH
from homeassistant.components.text import DOMAIN as TEXT
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_state_change_event

from .const import COORDINATOR
from .const import DOMAIN
from .const import UNSUB_LISTENERS
from .keymaster_identity import CapabilityRegistryIndex
from .keymaster_identity import resolve_capability_identity
from .keymaster_lifecycle import get_keymaster_lifecycle
from .startup_readability import async_arm_startup_readability_refresh
from .util import get_entry_data
from .util import handle_state_change

_LOGGER = logging.getLogger(__package__)
_LISTENER_UNSUB_KEY = "keymaster_state_listener_unsub"


def lifecycle_listener_callback(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    coordinator: Any,
):
    """Rebind feedback tracking only for the current lifecycle generation."""

    def resolved(generation, _resolutions) -> None:
        """Schedule refreshed listeners after the current snapshot resolves."""
        lifecycle = get_keymaster_lifecycle(hass, config_entry.entry_id)
        entry_data = get_entry_data(hass, config_entry.entry_id)
        if (
            lifecycle is None
            or lifecycle.generation != generation
            or entry_data is None
            or entry_data.get(COORDINATOR) is not coordinator
        ):
            return

        async def rebind() -> None:
            """Replace address subscriptions and arm the readability watcher."""
            if (
                get_keymaster_lifecycle(hass, config_entry.entry_id) is not lifecycle
                or lifecycle.generation != generation
                or get_entry_data(hass, config_entry.entry_id) is not entry_data
                or entry_data.get(COORDINATOR) is not coordinator
            ):
                return
            refresh_topology = getattr(coordinator, "_refresh_child_locks", None)
            if callable(refresh_topology):
                refresh_topology()
            await async_start_listener(
                hass,
                config_entry,
                expected_coordinator=coordinator,
                expected_lifecycle=lifecycle,
                expected_generation=generation,
            )
            if (
                get_keymaster_lifecycle(hass, config_entry.entry_id) is not lifecycle
                or lifecycle.generation != generation
                or entry_data.get(COORDINATOR) is not coordinator
                or coordinator is None
            ):
                return
            async_arm_startup_readability_refresh(
                hass,
                config_entry,
                coordinator,
                startup_slots_unreadable=True,
            )

        config_entry.async_create_task(
            hass,
            rebind(),
            name=f"{DOMAIN} Keymaster listener rebind {config_entry.entry_id}",
        )

    return resolved


async def async_start_listener(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    *,
    expected_coordinator: Any | None = None,
    expected_lifecycle: Any | None = None,
    expected_generation: int | None = None,
) -> None:
    """Start tracking current verified or legacy Keymaster entities."""
    entry_data = get_entry_data(hass, config_entry.entry_id)
    if entry_data is None:
        return
    coordinator = hass.data[DOMAIN][config_entry.entry_id][COORDINATOR]
    if expected_coordinator is not None and coordinator is not expected_coordinator:
        return
    lockname = coordinator.lockname
    lifecycle = get_keymaster_lifecycle(hass, config_entry.entry_id)
    if expected_lifecycle is not None and lifecycle is not expected_lifecycle:
        return

    if lifecycle is not None:
        binding = _verified_listener_binding(lifecycle, expected_generation)
        if binding is None:
            return
        generation, resolutions, entities = binding
    else:
        entities = _legacy_listener_entities(coordinator, lockname)
        if entities is None:
            return
        generation = None
        resolutions = ()

    # Keep the prior subscription until the replacement is installed. There
    # are no awaits between subscribe and swap, so the overlap cannot dispatch
    # duplicate callbacks on Home Assistant's event loop.
    unsubscribe = (
        async_track_state_change_event(
            hass,
            entities,
            functools.partial(handle_state_change, hass, config_entry),
        )
        if entities
        else None
    )

    if lifecycle is not None:
        assert generation is not None
        if not _verified_binding_is_current(
            hass, config_entry, lifecycle, generation, resolutions
        ):
            if unsubscribe is not None:
                unsubscribe()
            return

    previous_unsubscribe = entry_data.pop(_LISTENER_UNSUB_KEY, None)
    if previous_unsubscribe is not None:
        previous_unsubscribe()
        listeners = entry_data.get(UNSUB_LISTENERS, [])
        if previous_unsubscribe in listeners:
            listeners.remove(previous_unsubscribe)
    if unsubscribe is not None:
        entry_data[_LISTENER_UNSUB_KEY] = unsubscribe
        entry_data[UNSUB_LISTENERS].append(unsubscribe)
    _LOGGER.debug("Registering Keymaster state feedback listener")


def _verified_listener_binding(lifecycle: Any, expected_generation: int | None):
    """Return ready verified addresses only for the expected live generation."""
    generation = lifecycle.generation
    if expected_generation is not None and generation != expected_generation:
        return None
    resolutions = tuple(
        resolution
        for resolution in lifecycle.current.values()
        if resolution.ready
        and resolution.identity is not None
        and resolution.entity_id is not None
    )
    entities = sorted(
        {resolution.entity_id for resolution in resolutions if resolution.entity_id}
    )
    return generation, resolutions, entities


def _legacy_listener_entities(coordinator: Any, lockname: str | None):
    """Build legacy addresses only when no verified lifecycle is active."""
    if not lockname:
        return None
    entities: list[str] = []
    for slot in range(
        coordinator.start_slot, coordinator.start_slot + coordinator.max_events
    ):
        entities.extend(
            (
                f"{SWITCH}.{lockname}_code_slot_{slot}_enabled",
                f"{TEXT}.{lockname}_code_slot_{slot}_pin",
                f"{TEXT}.{lockname}_code_slot_{slot}_name",
                f"{DATETIME}.{lockname}_code_slot_{slot}_date_range_start",
                f"{DATETIME}.{lockname}_code_slot_{slot}_date_range_end",
                f"{BUTTON}.{lockname}_code_slot_{slot}_reset",
            )
        )
    return entities


def _verified_binding_is_current(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    lifecycle: Any,
    generation: int,
    resolutions: tuple[Any, ...],
) -> bool:
    """Revalidate all addresses after subscribing and before swapping listeners."""
    registry_index = CapabilityRegistryIndex.build(hass)
    for resolution in resolutions:
        identity = resolution.identity
        if identity is None:
            return False
        refreshed = resolve_capability_identity(
            hass,
            replace(identity, generation=generation),
            registry_index=registry_index,
        )
        if (
            not refreshed.ready
            or refreshed.entity_id != resolution.entity_id
            or refreshed.identity != identity
        ):
            active_lifecycle = get_keymaster_lifecycle(hass, config_entry.entry_id)
            if active_lifecycle is lifecycle and active_lifecycle is not None:
                if active_lifecycle.generation == generation:
                    active_lifecycle.request_refresh()
            return False
    active_lifecycle = get_keymaster_lifecycle(hass, config_entry.entry_id)
    return (
        active_lifecycle is lifecycle
        and active_lifecycle is not None
        and (active_lifecycle.generation == generation)
    )
