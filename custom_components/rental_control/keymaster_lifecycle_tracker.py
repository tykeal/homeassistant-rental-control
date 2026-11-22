# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Lifecycle invalidation for verified Keymaster capabilities."""

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

from .keymaster_identity import CapabilityRegistryIndex
from .keymaster_identity import CapabilityResolution
from .keymaster_identity import CapabilityStatus
from .keymaster_identity import build_capability_identity
from .keymaster_identity import resolve_capability_identity

_EVENT_ENTITY_REGISTRY_UPDATED = er.EVENT_ENTITY_REGISTRY_UPDATED
_ResolutionCallback = Callable[[int, Mapping[str, CapabilityResolution]], None]
_GenerationFactory = Callable[[], int]


class KeymasterEntityLifecycle:
    """Track one selected KM entry and invalidate evidence before rebinding.

    The lifecycle's resolutions are identity observations, not authorization.
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

    @property
    def keymaster_entry_id(self) -> str:
        """Return the explicitly selected Keymaster config-entry identity."""
        return self._keymaster_entry_id

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
        """Invalidate when an exact entity loses its usable live capability."""
        entity_id = event.data.get("entity_id")
        if entity_id not in self._bound_entity_ids:
            return
        current = next(
            (
                resolution
                for resolution in self._resolutions.values()
                if resolution.entity_id == entity_id
            ),
            None,
        )
        new_state = event.data.get("new_state")
        if current is None or new_state is None:
            self._invalidate_and_schedule()
            return
        state_value = str(getattr(new_state, "state", "")).casefold()
        attributes = getattr(new_state, "attributes", {})
        identity = current.identity
        is_slot_text = bool(
            identity is not None
            and identity.domain == "text"
            and identity.property_key.startswith("text.code_slots:")
            and identity.property_key.rsplit(".", maxsplit=1)[-1] in {"name", "pin"}
        )
        current_native_empty = (
            is_slot_text and current.status is CapabilityStatus.UNKNOWN_STATE
        )
        if not current.ready and not current_native_empty:
            self._invalidate_and_schedule()
            return
        native_empty_text = is_slot_text and state_value == "unknown"
        if (
            identity is not None
            and identity.property_key.endswith(".override_parent")
            and current.boolean_state != (state_value == "on")
        ):
            self._invalidate_and_schedule()
            return
        unusable_state = (
            state_value
            in {
                "unknown",
                "unavailable",
                "[redacted]",
                "masked",
                "****",
            }
            and not native_empty_text
        )
        restored_state = (
            isinstance(attributes, Mapping) and attributes.get("restored") is True
        )
        if unusable_state or restored_state:
            self._invalidate_and_schedule()
            return
        if is_slot_text and (current_native_empty or native_empty_text):
            self._refresh_native_text_resolution(current)

    @callback
    def _refresh_native_text_resolution(self, current: CapabilityResolution) -> None:
        """Refresh only an exact live name/PIN empty-value transition in place."""
        identity = current.identity
        if identity is None:
            self._invalidate_and_schedule()
            return
        fresh = resolve_capability_identity(self._hass, identity)
        if fresh.entity_id != current.entity_id or fresh.status not in {
            CapabilityStatus.READY,
            CapabilityStatus.UNKNOWN_STATE,
        }:
            self._invalidate_and_schedule()
            return
        resolutions = dict(self._resolutions)
        resolutions[identity.property_key] = fresh
        self._resolutions = MappingProxyType(resolutions)

    @callback
    def request_refresh(self) -> None:
        """Invalidate and resnapshot when a post-subscription check finds a race."""
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
        previous_unsubscribe = self._unsub_state_change
        replacement_unsubscribe: Callable[[], None] | None = None
        if entity_ids:
            replacement_unsubscribe = async_track_state_change_event(
                self._hass,
                list(entity_ids),
                self._handle_state_change,
            )
        # Listen before taking the final snapshot. Changes after this point
        # are observed by the listener; changes before it are reflected by
        # the second resolution below.
        refreshed: dict[str, CapabilityResolution] = {}
        for property_key in self._property_keys:
            base_identity = self._base_identities[property_key]
            if base_identity is None:
                refreshed[property_key] = CapabilityResolution(
                    None, CapabilityStatus.INVALID_IDENTITY
                )
                continue
            refreshed[property_key] = resolve_capability_identity(
                self._hass,
                replace(base_identity, generation=generation),
                registry_index=registry_index,
            )
        if (
            not self._started
            or generation != self._generation
            or any(
                refreshed[key].identity != results[key].identity
                or refreshed[key].entity_id != results[key].entity_id
                or refreshed[key].status is not results[key].status
                for key in results
            )
        ):
            if replacement_unsubscribe is not None:
                replacement_unsubscribe()
            if self._started and generation == self._generation:
                self._invalidate_and_schedule()
            return
        self._bound_entity_ids = entity_ids
        self._unsub_state_change = replacement_unsubscribe
        if previous_unsubscribe is not None:
            previous_unsubscribe()
        self._resolutions = MappingProxyType(results)
        self._resolved_generation = generation
        self._on_resolved(generation, self._resolutions)
