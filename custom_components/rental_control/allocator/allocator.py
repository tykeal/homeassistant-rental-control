# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Shared door-code allocator shell and lifecycle state."""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.core import HomeAssistant

from .models import AllocationResult
from .models import CycleObservation
from .models import CycleResult
from .models import OrphanCleanupReport
from .models import ReleaseReport
from .registry import AllocationRegistry
from .store import RegistryStore
from .store import code_ref_for


class DoorCodeAllocator:
    """System-wide allocator state shared by all Rental Control entries."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the allocator with empty in-memory state."""
        self.hass = hass
        self._registry = AllocationRegistry()
        self._store = RegistryStore(hass)
        self._lock = asyncio.Lock()
        self._pending_adoption: set[str] = set()
        self._gate_deadline = 0.0
        self._registry_lost = False

    async def async_load(self) -> None:
        """Load the persisted registry before the allocator is used."""
        result = await self._store.async_load()
        self._registry = result.registry
        self._registry_lost = result.registry_lost

    async def async_register_entry(self, entry_id: str) -> None:
        """Mark an entry as awaiting its first allocator cycle."""
        async with self._lock:
            self._pending_adoption.add(entry_id)

    async def async_unregister_entry(self, entry_id: str) -> None:
        """Remove an entry from the pending adoption gate."""
        async with self._lock:
            self._pending_adoption.discard(entry_id)

    def code_ref(self, code: str) -> str:
        """Return a masked diagnostic reference for a plain code."""
        return code_ref_for(code, self._registry.code_ref_salt)

    @property
    def diagnostics(self) -> dict[str, Any]:
        """Return allocator diagnostics without exposing any door codes."""
        return {
            "record_count": len(self._registry.records),
            "owner_count": sum(
                len(record.owners) for record in self._registry.records.values()
            ),
            "conflict_count": len(self._registry.conflicts()),
            "pending_adoption": sorted(self._pending_adoption),
            "gate_deadline": self._gate_deadline,
            "registry_lost": self._registry_lost,
        }

    async def async_resolve_cycle(self, request: object) -> CycleResult:
        """Resolve a refresh cycle in a later implementation phase."""
        del request
        return CycleResult(
            adopted={},
            allocated={},
            released=ReleaseReport(),
            unaccounted_slots=frozenset(),
        )

    async def async_adopt(self, request: object) -> AllocationResult:
        """Adopt observed codes in a later implementation phase."""
        del request
        return AllocationResult(code=None, reason="adoption_pending")

    async def async_rekey(self, old_key: str, new_key: str) -> bool:
        """Re-key allocations in a later implementation phase."""
        del old_key, new_key
        return False

    async def async_allocate(self, request: object) -> AllocationResult:
        """Allocate new codes in a later implementation phase."""
        del request
        return AllocationResult(code=None, reason="adoption_pending")

    async def async_sweep(
        self, observation: CycleObservation, active_keys: set[str]
    ) -> ReleaseReport:
        """Sweep releasable allocations in a later implementation phase."""
        del observation, active_keys
        return ReleaseReport()

    async def async_mark_entry_removed(self, entry_id: str) -> ReleaseReport:
        """Handle entry removal in a later implementation phase."""
        await self.async_unregister_entry(entry_id)
        return ReleaseReport()

    async def async_clear_orphans(
        self,
        known_entry_ids: set[str],
        observations: list[CycleObservation],
        dry_run: bool = False,
    ) -> OrphanCleanupReport:
        """Clear orphaned allocations in a later implementation phase."""
        del known_entry_ids, observations
        return OrphanCleanupReport(dry_run=dry_run)
