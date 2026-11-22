# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Structural allocator state contract shared by allocator helper modules."""

from __future__ import annotations

from collections.abc import Collection
from typing import Any
from typing import Protocol

from .models import AllocationOwner
from .models import AllocationRecord
from .models import CycleObservation
from .registry import AllocationRegistry


class AllocatorState(Protocol):
    """Mutable and immutable state required by allocator operations."""

    hass: Any
    _lock: Any
    _mutation_lock: Any
    _registry: AllocationRegistry
    _store: Any
    _protection_required: bool
    _registry_lost: bool
    _registry_missing: bool
    _pending_adoption: set[str]
    _adopted_locks: Any
    _read_pending_locks: Any
    _pending_adoption_slots: dict[str, frozenset[int]]
    _gate_deadline: float
    _gate_warning_sent: bool
    _reported_identity_mismatches: Any
    _conflict_verdicts: Any

    def _release_guard_reason(
        self,
        record: AllocationRecord,
        owners: list[AllocationOwner],
        observations: list[CycleObservation],
        *,
        refresh_observed: bool = True,
        forced_release: Any = None,
        releasing: Collection[str] = (),
    ) -> str | None:
        """Return a protective reason that prevents releasing this owner."""
        ...

    def _rekey_unlocked(self, old_key: str, new_key: str) -> bool:
        """Re-key one allocation identity while the allocator lock is held."""
        ...

    def _warn_if_gate_expired(self) -> None:
        """Emit the one-time warning when the adoption gate expires."""
        ...

    def _adopt_unlocked(self, request: Any) -> Any:
        """Adopt a code under the allocator lock."""
        ...

    def _allocate_unlocked(self, request: Any) -> Any:
        """Allocate a code under the allocator lock."""
        ...

    def _sweep_unlocked(
        self, observation: CycleObservation, active_keys: set[str]
    ) -> Any:
        """Apply one cycle observation while the allocator lock is held."""
        ...

    def _mark_entry_removed_unlocked(self, entry_id: str) -> Any:
        """Retain release holds for owners belonging to a removed entry."""
        ...
