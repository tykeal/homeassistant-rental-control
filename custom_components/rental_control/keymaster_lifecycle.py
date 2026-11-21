# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Lifecycle invalidation for dormant, verified Keymaster capabilities."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import replace
from types import MappingProxyType
from typing import Any

from homeassistant.config_entries import SIGNAL_CONFIG_ENTRY_CHANGED
from homeassistant.config_entries import ConfigEntry
from homeassistant.config_entries import ConfigEntryChange
from homeassistant.core import Event
from homeassistant.core import HomeAssistant
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_track_state_change_event

from .allocator.associations import AssociationMode
from .allocator.associations import AssociationTuple
from .const import DOMAIN
from .const import PROTECTED_REGISTRY_STORE
from .helpers import get_entry_data
from .keymaster_identity import CapabilityRegistryIndex
from .keymaster_identity import CapabilityResolution
from .keymaster_identity import CapabilityStatus
from .keymaster_identity import build_capability_identity
from .keymaster_identity import resolve_capability_identity

_EVENT_ENTITY_REGISTRY_UPDATED = er.EVENT_ENTITY_REGISTRY_UPDATED
_ResolutionCallback = Callable[[int, Mapping[str, CapabilityResolution]], None]
_GenerationFactory = Callable[[], int]
_GENERATION_EPOCHS_KEY = "_keymaster_lifecycle_epochs"
_KEYMASTER_LIFECYCLE = "keymaster_lifecycle"


def reserve_keymaster_generation(
    hass: HomeAssistant, rc_entry_id: str, *, minimum: int = 0
) -> int:
    """Reserve a strictly increasing per-entry identity generation."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if not isinstance(domain_data, dict):
        raise ValueError("invalid Rental Control runtime data")
    epochs = domain_data.setdefault(_GENERATION_EPOCHS_KEY, {})
    if not isinstance(epochs, dict):
        raise ValueError("invalid Keymaster generation table")
    current = epochs.get(rc_entry_id, 0)
    if (
        not isinstance(current, int)
        or isinstance(current, bool)
        or current < 0
        or not isinstance(minimum, int)
        or isinstance(minimum, bool)
        or minimum < 0
    ):
        raise ValueError("invalid Keymaster generation epoch")
    generation = max(current + 1, minimum + 1)
    epochs[rc_entry_id] = generation
    return generation


def managed_slot_property_keys(start_slot: int, count: int) -> tuple[str, ...]:
    """Return only exact baseline properties in the configured slot range."""
    if (
        isinstance(start_slot, bool)
        or not isinstance(start_slot, int)
        or start_slot < 1
        or isinstance(count, bool)
        or not isinstance(count, int)
        or count < 1
    ):
        return ()
    suffixes = (
        "text.name",
        "text.pin",
        "button.reset",
        "switch.enabled",
        "switch.accesslimit_date_range_enabled",
        "switch.override_parent",
        "switch.notifications",
        "datetime.accesslimit_date_range_start",
        "datetime.accesslimit_date_range_end",
    )
    return tuple(
        f"{suffix.split('.', maxsplit=1)[0]}.code_slots:{slot}."
        f"{suffix.split('.', maxsplit=1)[1]}"
        for slot in range(start_slot, start_slot + count)
        for suffix in suffixes
    )


def start_keymaster_lifecycle(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    coordinator: Any,
) -> None:
    """Track only an explicitly committed verified association, if present."""
    entry_data = get_entry_data(hass, config_entry.entry_id)
    if entry_data is None:
        return
    stop_keymaster_lifecycle(hass, config_entry.entry_id)
    association = AssociationTuple.from_config(config_entry.data)
    if (
        association is None
        or association.mode is not AssociationMode.VERIFIED
        or association.verified_keymaster_entry_id is None
    ):
        return
    domain_data = hass.data.get(DOMAIN)
    if not isinstance(domain_data, dict):
        return
    store = domain_data.get(PROTECTED_REGISTRY_STORE)
    if store is None:
        return
    try:
        acknowledged_association = store.association_for_entry(config_entry.entry_id)
    except Exception:
        return
    if acknowledged_association != association:
        return
    property_keys = managed_slot_property_keys(
        coordinator.start_slot,
        coordinator.max_events,
    )
    if not property_keys:
        return
    initial_generation = reserve_keymaster_generation(
        hass,
        config_entry.entry_id,
        minimum=max(0, association.association_generation - 1),
    )
    lifecycle = KeymasterEntityLifecycle(
        hass,
        association.verified_keymaster_entry_id,
        property_keys,
        lambda _generation, _resolutions: None,
        initial_generation=initial_generation,
        generation_factory=lambda: reserve_keymaster_generation(
            hass,
            config_entry.entry_id,
            minimum=association.association_generation,
        ),
    )
    lifecycle.start()
    entry_data[_KEYMASTER_LIFECYCLE] = lifecycle


def stop_keymaster_lifecycle(hass: HomeAssistant, rc_entry_id: str) -> None:
    """Idempotently stop and remove a selected-entry lifecycle manager."""
    entry_data = get_entry_data(hass, rc_entry_id)
    if entry_data is None:
        return
    lifecycle = entry_data.pop(_KEYMASTER_LIFECYCLE, None)
    if lifecycle is not None:
        lifecycle.stop()


class KeymasterEntityLifecycle:
    """Track one selected KM entry and invalidate evidence before rebinding.

    This object is intentionally dormant: its resolutions are observations
    only, and the callback cannot authorize hardware operations by itself.
    Consumers must still check the allocator's live protective token at their
    serialized dispatch boundary.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        keymaster_entry_id: str,
        property_keys: Sequence[str],
        on_resolved: _ResolutionCallback,
        *,
        initial_generation: int = 0,
        generation_factory: _GenerationFactory | None = None,
    ) -> None:
        """Create an explicit selected-entry lifecycle boundary."""
        if (
            isinstance(initial_generation, bool)
            or not isinstance(initial_generation, int)
            or initial_generation < 0
        ):
            raise ValueError("invalid initial lifecycle generation")
        self._hass = hass
        self._keymaster_entry_id = keymaster_entry_id
        self._property_keys = tuple(property_keys)
        self._on_resolved = on_resolved
        self._generation = initial_generation
        self._generation_factory = generation_factory
        self._base_identities = {
            key: build_capability_identity(
                self._keymaster_entry_id,
                key,
                generation=1,
            )
            for key in self._property_keys
        }
        self._expected_unique_ids = frozenset(
            identity.unique_id
            for identity in self._base_identities.values()
            if identity is not None
        )
        selected = self._hass.config_entries.async_get_entry(self._keymaster_entry_id)
        parent_entry_id = selected.data.get("parent_entry_id") if selected else None
        self._parent_entry_id = (
            parent_entry_id if isinstance(parent_entry_id, str) else None
        )
        self._resolved_generation: int | None = None
        self._resolutions: Mapping[str, CapabilityResolution] = MappingProxyType({})
        self._bound_entity_ids: frozenset[str] = frozenset()
        self._unsubscribers: list[Callable[[], None]] = []
        self._unsub_state_change: Callable[[], None] | None = None
        self._resolve_scheduled = False
        self._started = False

    def _advance_generation(self) -> None:
        """Advance locally and persist the per-RC epoch when configured."""
        if self._generation_factory is None:
            self._generation += 1
            return
        generation = self._generation_factory()
        if generation <= self._generation:
            raise ValueError("Keymaster generation did not advance")
        self._generation = generation

    @property
    def generation(self) -> int:
        """Return the monotonic invalidation generation."""
        return self._generation

    @property
    def current(self) -> Mapping[str, CapabilityResolution]:
        """Return only a fully resolved snapshot for the current generation."""
        if not self._started or self._resolved_generation != self._generation:
            return MappingProxyType({})
        return self._resolutions

    def start(self) -> None:
        """Subscribe to global and selected-entry changes before snapshotting."""
        if self._started:
            return
        self._started = True
        self._unsubscribers.extend(
            (
                self._hass.bus.async_listen(
                    _EVENT_ENTITY_REGISTRY_UPDATED, self._handle_registry_update
                ),
                async_dispatcher_connect(
                    self._hass,
                    SIGNAL_CONFIG_ENTRY_CHANGED,
                    self._handle_config_entry_change,
                ),
            )
        )
        self._invalidate_and_schedule()

    def stop(self) -> None:
        """Invalidate pending callbacks and release every listener idempotently."""
        if not self._started:
            return
        self._started = False
        self._advance_generation()
        self._resolved_generation = None
        self._resolutions = MappingProxyType({})
        self._bound_entity_ids = frozenset()
        if self._unsub_state_change is not None:
            self._unsub_state_change()
            self._unsub_state_change = None
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()

    @callback
    def _handle_registry_update(self, event: Event[Any]) -> None:
        """Invalidate only when a registry event can affect a selected identity."""
        entity_id = event.data.get("entity_id")
        old_entity_id = event.data.get("old_entity_id")
        if (
            entity_id in self._bound_entity_ids
            or old_entity_id in self._bound_entity_ids
            or self._event_has_selected_identity(entity_id)
        ):
            self._invalidate_and_schedule()

    def _event_has_selected_identity(self, entity_id: object) -> bool:
        """Check the post-event registry entry against exact expected identities."""
        if not isinstance(entity_id, str):
            return False
        registry_entry = er.async_get(self._hass).async_get(entity_id)
        if registry_entry is None:
            return False
        return registry_entry.unique_id in self._expected_unique_ids

    @callback
    def _handle_config_entry_change(
        self, _change: ConfigEntryChange, entry: ConfigEntry
    ) -> None:
        """Invalidate on selected entry data or load-state changes."""
        if entry.entry_id == self._keymaster_entry_id:
            parent_entry_id = entry.data.get("parent_entry_id")
            self._parent_entry_id = (
                parent_entry_id if isinstance(parent_entry_id, str) else None
            )
            self._invalidate_and_schedule()
        elif entry.entry_id == self._parent_entry_id:
            self._invalidate_and_schedule()

    @callback
    def _handle_state_change(self, event: Event[Any]) -> None:
        """Invalidate a live snapshot when one of its exact entities changes."""
        if event.data.get("entity_id") in self._bound_entity_ids:
            self._invalidate_and_schedule()

    def _invalidate_and_schedule(self) -> None:
        """Revoke old evidence immediately and coalesce the next snapshot."""
        if not self._started:
            return
        self._advance_generation()
        self._resolved_generation = None
        if not self._resolve_scheduled:
            self._resolve_scheduled = True
            self._hass.async_create_task(self._async_resolve_latest())

    async def _async_resolve_latest(self) -> None:
        """Resolve after same-turn events have coalesced, then atomically rebind."""
        await asyncio.sleep(0)
        self._resolve_scheduled = False
        if not self._started:
            return
        generation = self._generation
        registry_index = CapabilityRegistryIndex.build(self._hass)
        results: dict[str, CapabilityResolution] = {}
        for property_key in self._property_keys:
            base_identity = self._base_identities[property_key]
            if base_identity is None:
                results[property_key] = CapabilityResolution(
                    None, CapabilityStatus.INVALID_IDENTITY
                )
                continue
            results[property_key] = resolve_capability_identity(
                self._hass,
                replace(base_identity, generation=generation),
                registry_index=registry_index,
            )
        if not self._started or generation != self._generation:
            return

        entity_ids = frozenset(
            result.entity_id
            for result in results.values()
            if result.entity_id is not None
        )
        if self._unsub_state_change is not None:
            self._unsub_state_change()
            self._unsub_state_change = None
        self._bound_entity_ids = entity_ids
        if entity_ids:
            self._unsub_state_change = async_track_state_change_event(
                self._hass,
                list(entity_ids),
                self._handle_state_change,
            )
        self._resolutions = MappingProxyType(results)
        self._resolved_generation = generation
        self._on_resolved(generation, self._resolutions)
