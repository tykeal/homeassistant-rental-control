# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Coordinator shell mixins for behavior-preserving delegation."""

# mypy: disable-error-code="attr-defined, has-type, var-annotated, misc, no-redef"

from __future__ import annotations

import importlib
import logging
from typing import Any

from homeassistant.util import dt

from ..const import STORE_FILE_VERSION
from ..const import STORE_SCHEMA_VERSION
from ..const import STORE_SLOT_MAPPINGS_KEY
from ..keymaster_identity import resolve_capability
from ..keymaster_lifecycle import get_keymaster_lifecycle
from ..keymaster_operations import protected_registry_active
from ..keymaster_runtime import coordinator_keymaster_runtime_enabled
from ..keymaster_runtime import protected_legacy_unresolved
from ..reconciliation import DesiredPlan as _DesiredPlan
from ..reconciliation import ManagedSlot as _ManagedSlot
from ..reconciliation import Reservation as _Reservation
from ..reconciliation import SlotStatus as _SlotStatus
from ..util import OperationResult
from . import store_sync
from .models import _adopted_slot_placeholder
from .models import _format_display_slot_name

_LOGGER = logging.getLogger(__name__)


def _coordinator_module() -> Any:
    """Return the public coordinator module for patched compatibility."""
    return importlib.import_module("custom_components.rental_control.coordinator")


class CoordinatorStoreMixin:
    """Provide extracted coordinator shell behavior."""

    def _observed_pin(self, slot_number: int) -> str | None:
        """Return the PIN the lock currently reports for a slot."""
        if not coordinator_keymaster_runtime_enabled(
            self
        ) or protected_legacy_unresolved(self):
            return None
        config_entry = getattr(self, "config_entry", None)
        entry_id = getattr(config_entry, "entry_id", None) or getattr(
            self, "_entry_id", None
        )
        lifecycle = (
            get_keymaster_lifecycle(self.hass, entry_id)
            if isinstance(entry_id, str)
            else None
        )
        if lifecycle is not None:
            resolution = lifecycle.current.get(f"text.code_slots:{slot_number}.pin")
            if (
                resolution is None
                or not resolution.ready
                or resolution.identity is None
                or resolution.identity.slot != slot_number
                or resolution.identity.keymaster_entry_id
                != lifecycle.keymaster_entry_id
                or resolution.identity.generation != lifecycle.generation
                or resolution.entity_id is None
            ):
                return None
            fresh = resolve_capability(
                self.hass,
                lifecycle.keymaster_entry_id,
                f"text.code_slots:{slot_number}.pin",
                generation=lifecycle.generation,
            )
            if not fresh.ready or fresh.entity_id != resolution.entity_id:
                return None
            state = self.hass.states.get(resolution.entity_id)
            return state.state if state is not None else None
        if protected_registry_active(self.hass):
            return None
        state = self.hass.states.get(
            f"text.{self.lockname}_code_slot_{slot_number}_pin"
        )
        return state.state if state is not None else None

    async def _async_load_slot_store_impl(self) -> None:
        """Load cache-only slot metadata from the HA Store."""
        self._store = _coordinator_module().Store(
            self.hass,
            STORE_FILE_VERSION,
            f"{STORE_SLOT_MAPPINGS_KEY}.{self._entry_id}",
        )
        try:
            raw: dict[str, Any] | None = await self._store.async_load()
        except Exception:
            _LOGGER.warning("Ignoring unreadable Rental Control slot cache")
            self._slot_mappings = self._empty_slot_cache("cache_load_failed")
            return
        if raw is None:
            self._slot_mappings = self._empty_slot_cache("cache_missing")
            return
        try:
            normalized = store_sync.normalize_loaded_store(raw, self._store_meta())
        except Exception:
            normalized = None
        self._slot_mappings = (
            normalized
            if normalized is not None
            else self._empty_slot_cache("cache_corrupt")
        )

    def _store_meta(self) -> tuple[str, str | None, int, int, str]:
        """Return the Store metadata tuple for payload construction."""
        return (
            self._entry_id,
            self.lockname,
            self.start_slot,
            self.max_events,
            dt.now().isoformat(),
        )

    def _empty_slot_cache(self, note: str) -> dict[str, Any]:
        """Return an empty cache-only Store payload with a migration note."""
        return store_sync.empty_slot_cache(self._entry_id, self.lockname, note)

    async def _async_save_slot_store_impl(self) -> None:
        """Best-effort save of cache-only slot metadata to the HA Store."""
        if self._store is None:
            return
        data = store_sync.build_save_payload(self._slot_mappings, self._store_meta())
        try:
            await self._store.async_save(data)
        except Exception:
            _LOGGER.warning("Failed to save Rental Control slot cache")

    def _adopt_observed_coded_slots(self, managed_slots: list[_ManagedSlot]) -> None:
        """Adopt readable coded slots that have no persisted mapping yet.

        A coded slot adopted on an earlier refresh is attached to its
        existing adopted mapping instead, so the physical remap can still
        move that mapping to the reservation now on the lock.
        """
        persisted: dict[str, Any] = self._slot_mappings.setdefault("mappings", {})
        existing_slots = {
            mapping.get("slot")
            for mapping in persisted.values()
            if isinstance(mapping, dict)
        }
        now_str = dt.now().isoformat()
        prefix = f"{self.event_prefix} " if self.event_prefix else ""
        adopted = False

        for ms in managed_slots:
            if (
                not ms.managed
                or ms.persisted_identity_key is not None
                or ms.status is not _SlotStatus.OCCUPIED
                or ms.actual_code_present is not True
            ):
                continue

            identity_key = (
                f"{store_sync.ADOPTED_KEY_PREFIX}{self._entry_id}.slot{ms.slot}"
            )
            if identity_key in persisted:
                ms.persisted_identity_key = identity_key
                continue
            if ms.slot in existing_slots:
                continue

            slot_name = ms.actual_name or _adopted_slot_placeholder(ms.slot)
            if prefix and slot_name.startswith(prefix):
                slot_name = slot_name[len(prefix) :]

            persisted[identity_key] = store_sync.build_adopted_mapping(
                ms, identity_key, slot_name, now_str
            )
            ms.persisted_identity_key = identity_key
            existing_slots.add(ms.slot)
            adopted = True

        if adopted:
            self._slot_mappings.update(
                {
                    "schema_version": STORE_SCHEMA_VERSION,
                    "entry_id": self._entry_id,
                    "lockname": self.lockname,
                    "start_slot": self.start_slot,
                    "max_slots": self.max_events,
                    "updated_at": now_str,
                    "blocked_slots": self._slot_mappings.get("blocked_slots", {}),
                }
            )
            if self.event_overrides is not None:
                self.event_overrides.load_persisted_mappings(persisted)

    def _merge_observed_slots_into_mappings(
        self, managed_slots: list[_ManagedSlot]
    ) -> None:
        """Refresh persisted actual snapshots from current physical slots.

        Store mappings loaded on restart may contain stale
        ``last_observed_actual`` snapshots.  Before rematching current
        calendar reservations, physical Keymaster state must be allowed to
        win over those stale snapshots so populated slots can be reclaimed
        rather than stale-cleared.  Readable coded slots with no mapping
        are adopted here so deleted or missing stores recover on any
        refresh once Keymaster entities settle.
        """
        self._adopt_observed_coded_slots(managed_slots)
        persisted = self._slot_mappings.get("mappings", {})
        keys_by_slot: dict[int, list[str]] = {}
        for key, mapping in persisted.items():
            if isinstance(mapping, dict) and isinstance(mapping.get("slot"), int):
                keys_by_slot.setdefault(mapping["slot"], []).append(key)
        for ms in managed_slots:
            keys = keys_by_slot.get(ms.slot, [])
            if ms.persisted_identity_key is None and len(keys) == 1:
                mapping = persisted[keys[0]]
                identity = mapping.get("identity", {})
                name = identity.get("slot_name") if isinstance(identity, dict) else None
                if (
                    isinstance(name, str)
                    and name
                    and self._physical_slot_name_matches_name(
                        ms.actual_name,
                        name,
                        _format_display_slot_name(
                            name,
                            f"{self.event_prefix} " if self.event_prefix else "",
                            self.trim_names,
                            self.max_name_length,
                        ),
                    )
                ):
                    ms.persisted_identity_key = keys[0]
            if ms.persisted_identity_key is None:
                continue
            mapping = persisted.get(ms.persisted_identity_key)
            if mapping is None:
                continue
            mapping["last_observed_actual"] = store_sync.observed_actual_snapshot(ms)

    def _sync_slot_store_from_plan(
        self,
        plan: _DesiredPlan,
        res_by_key: dict[str, _Reservation],
        operation_results: list[OperationResult],
    ) -> None:
        """Synchronize cache-only alias and diagnostic metadata from a plan."""
        mappings: dict[str, Any] = self._slot_mappings.setdefault("mappings", {})
        now_str = dt.now().isoformat()
        actual_by_slot: dict[int, dict[str, Any]] = {}
        if self.event_overrides is not None:
            for slot in plan.selected.values():
                actual_by_slot[slot] = self.event_overrides.get_actual_state(slot) or {}
        sync_plan = store_sync.build_store_sync_plan(
            plan,
            res_by_key,
            operation_results,
            actual_by_slot,
            self._slot_mappings,
            (self._entry_id, self.lockname, self.start_slot, self.max_events, now_str),
        )
        for stale_key in sync_plan.remove_identity_keys:
            mappings.pop(stale_key, None)
        mappings.update(sync_plan.upsert_mappings)
        self._slot_mappings.update(sync_plan.metadata)

        if self.event_overrides is not None:
            self.event_overrides.load_persisted_mappings(mappings)
