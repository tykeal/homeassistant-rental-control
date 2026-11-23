# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Shared door-code allocator shell and lifecycle state."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import copy
from datetime import UTC
from datetime import datetime
import logging
from time import monotonic
from typing import Any

from homeassistant.components.persistent_notification import async_create
from homeassistant.core import HomeAssistant

from ..const import DOMAIN
from ..const import NAME
from . import adoption
from . import adoption_reports
from . import diagnostics
from . import issuance
from . import protection_gate
from . import recovery
from .allocator_lifecycle import AllocatorLifecycleMixin
from .associations import AssociationTuple
from .models import AdoptionRequest
from .models import AllocationRequest
from .models import AllocationResult
from .models import CycleRequest
from .models import CycleResult
from .models import ObservedLock
from .models import ProtectionLoadState
from .models import ReissuePreview
from .models import ReissuePreviewRequest
from .registry import AllocationRegistry
from .registry_mutation import after_commit
from .registry_mutation import async_commit_staged_registry
from .registry_mutation import clone_allocator
from .registry_mutation import lockless_preflight
from .store import RegistryStore
from .store import code_ref_for

_LOGGER = logging.getLogger(__name__)
_ADOPTION_GATE_WARNING_SECONDS = recovery.ADOPTION_GATE_WARNING_SECONDS
_GATE_NOTIFICATION_ID = f"{DOMAIN}_code_registry_adoption_pending"
_REGISTRY_LOST_NOTICE = (
    " The door code registry was lost or found corrupt, so codes resume "
    "only once every entry with a lock has read its whole slot range "
    "again. A lock that cannot be reached keeps codes withheld until it "
    "is back online; exclude a permanently dead slot with the "
    "rental_control.set_slot_exclusion action. Enable disabled entries so "
    "their locks can be read, or clear their codes from the lock before "
    "deleting an entry."
)


class DoorCodeAllocator(AllocatorLifecycleMixin):
    """System-wide allocator state shared by all Rental Control entries."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the allocator with empty in-memory state."""
        self.hass = hass
        self._registry = AllocationRegistry()
        self._store = RegistryStore(hass)
        self._protection_required = False
        self._lock = asyncio.Lock()
        # Serialize protected read/modify/commit sequences without holding the
        # allocator state lock while storage I/O is in progress.
        self._mutation_lock = asyncio.Lock()
        self._registry_lost = False
        self._pending_adoption = self._seed_pending_adoption()
        self._adopted_locks: dict[str, ObservedLock] = {}
        self._read_pending_locks: dict[
            str, tuple[ObservedLock, frozenset[str], frozenset[int]]
        ] = {}
        self._pending_adoption_slots: dict[str, frozenset[int]] = {}
        self._gate_deadline = (
            monotonic() + _ADOPTION_GATE_WARNING_SECONDS
            if self._pending_adoption
            else 0.0
        )
        self._registry_missing = False
        self._gate_warning_sent = False
        self._reported_identity_mismatches: set[tuple[str, str, str]] = set()
        self._conflict_verdicts: dict[str, adoption_reports.ConflictVerdict] = {}

    async def async_load(self) -> None:
        """Load the persisted registry before the allocator is used."""
        if self._protection_required:
            from .activation import async_activate_protected_store

            await async_activate_protected_store(self)
            return
        result = await self._store.async_load()
        self._registry = result.registry
        self._registry_lost = result.registry_lost
        self._registry_missing = result.registry_missing
        if self._registry_lost:
            self._pending_adoption |= self._seed_pending_adoption()
            if self._pending_adoption and self._gate_deadline == 0.0:
                self._gate_deadline = monotonic() + _ADOPTION_GATE_WARNING_SECONDS

    def _seed_pending_adoption(self) -> set[str]:
        """Return configured lock-backed entries awaiting adoption.

        Disabled entries are included once the registry is lost (#854).
        """
        return recovery.gate_entries(self.hass, include_disabled=self._registry_lost)

    async def async_register_entry(self, entry_id: str) -> None:
        """Mark an entry as awaiting its first allocator cycle.

        A reloaded entry must adopt again before its lock counts as
        observed, so any earlier adoption is forgotten here.
        """
        async with self._lock:
            self._pending_adoption.add(entry_id)
            self._adopted_locks.pop(entry_id, None)
            self._read_pending_locks.pop(entry_id, None)
            if self._gate_deadline == 0.0:
                self._gate_deadline = monotonic() + _ADOPTION_GATE_WARNING_SECONDS
                self._gate_warning_sent = False

    async def async_unregister_entry(self, entry_id: str) -> None:
        """Remove an entry from the pending adoption gate."""
        async with self._lock:
            if recovery.keeps_gate_closed(self, entry_id):
                return
            self._pending_adoption.discard(entry_id)
            self._pending_adoption_slots.pop(entry_id, None)
            self._adopted_locks.pop(entry_id, None)
            if not self._pending_adoption:
                self._gate_deadline = 0.0
                self._gate_warning_sent = False

    def code_ref(self, code: str) -> str:
        """Return a masked diagnostic reference for a plain code."""
        return code_ref_for(code, self._registry.code_ref_salt)

    def has_active_allocations(self, entry_id: str) -> bool:
        """Return whether an entry currently owns registry allocations."""
        return any(
            owner.entry_id == entry_id
            for record in self._registry.records.values()
            for owner in record.owners
        )

    @property
    def diagnostics(self) -> dict[str, Any]:
        """Return allocator diagnostics without exposing any door codes."""
        return diagnostics.allocator_diagnostics(self)

    async def async_resolve_cycle(self, request: CycleRequest) -> CycleResult:
        """Resolve one refresh cycle atomically under the allocator lock."""
        if not isinstance(request, CycleRequest):
            msg = "async_resolve_cycle requires a CycleRequest"
            raise TypeError(msg)
        return await issuance.resolve_cycle(self, request)

    async def async_preview_reissue(
        self, request: ReissuePreviewRequest
    ) -> ReissuePreview:
        """Preview a forced re-issue without mutating allocator state."""
        if not isinstance(request, ReissuePreviewRequest):
            msg = "async_preview_reissue requires a ReissuePreviewRequest"
            raise TypeError(msg)
        return await protection_gate.async_preview_reissue(self, request)

    async def async_adopt(self, request: object) -> AllocationResult:
        """Adopt an observed lock code into the shared registry."""
        if not isinstance(request, AdoptionRequest):
            msg = "async_adopt requires an AdoptionRequest"
            raise TypeError(msg)
        if not await protection_gate.async_operation_ready(self):
            return AllocationResult(reason="protective_unknown")
        if self._protection_required:
            # A direct request contains no verified physical observation.
            return AllocationResult(reason="protective_unknown")
        async with self._lock:
            if not protection_gate.snapshot_ready(self):
                return AllocationResult(reason="protective_unknown")
            result = self._adopt_unlocked(request)
            if not self._registry_lost:
                self._store.async_save(self._registry)
            return result

    def _adopt_unlocked(self, request: AdoptionRequest) -> AllocationResult:
        """Adopt an observed code while the allocator lock is already held."""
        return adoption.adopt_unlocked(self, request)

    def _adopt_associated_alias_unlocked(
        self,
        request: AdoptionRequest,
        associations: Mapping[str, AssociationTuple],
    ) -> AllocationResult:
        """Adopt into an isolated candidate after same-entry alias validation.

        This staged-only boundary must not be used on the live registry; its
        caller must acknowledge the complete protected snapshot first.
        """
        return adoption.adopt_associated_alias_unlocked(self, request, associations)

    async def async_associated_alias_candidates(
        self,
        request: AdoptionRequest,
        associations: Mapping[str, AssociationTuple],
    ) -> tuple[str, ...]:
        """Return a serialized, read-only verified alias-migration plan."""
        async with self._lock:
            return adoption.associated_alias_candidates(self, request, associations)

    async def async_rekey(self, old_key: str, new_key: str) -> bool:
        """Move an existing allocation identity to a new stable key."""
        return await protection_gate.async_rekey(self, old_key, new_key)

    def _rekey_unlocked(self, old_key: str, new_key: str) -> bool:
        """Move an allocation identity while the lock is already held."""
        if not old_key or not new_key or old_key == new_key:
            return False
        if new_key in self._registry.by_identity:
            return False
        record = self._registry.record_for_identity(old_key)
        if record is None:
            return False
        for owner in record.owners:
            if owner.identity_key != old_key:
                continue
            owner.identity_key = new_key
            record.updated_at = datetime.now(UTC).isoformat()
            self._registry.by_identity.pop(old_key, None)
            self._registry.by_identity[new_key] = record.code
            _LOGGER.info("Re-keyed shared allocation ownership")
            return True
        return False

    async def async_allocate(self, request: object) -> AllocationResult:
        """Allocate a unique code for one reservation identity."""
        if not isinstance(request, AllocationRequest):
            msg = "async_allocate requires an AllocationRequest"
            raise TypeError(msg)
        if not await protection_gate.async_operation_ready(self):
            return AllocationResult(reason="protective_unknown")
        if self._protection_required:
            if request.lockname is not None:
                return AllocationResult(reason="protective_unknown")
            async with self._mutation_lock:
                async with self._lock:
                    if not protection_gate.snapshot_ready(self):
                        return AllocationResult(reason="protective_unknown")
                    if lockless_preflight(self, request.entry_id) is not None:
                        return AllocationResult(reason="protective_unknown")
                    baseline = copy.deepcopy(self._registry)
                    staged = clone_allocator(self)
                    staged._warn_if_gate_expired()
                    result = staged._allocate_unlocked(request)
                    if result.code is not None and (
                        not staged._registry_lost
                        or staged._registry_missing
                        or recovery.rebuilt_registry_trusted(staged)
                    ):
                        staged._registry_lost = False
                        staged._registry_missing = False
                committed = await async_commit_staged_registry(
                    self,
                    baseline,
                    staged,
                    event_loop_preflight=lambda: lockless_preflight(
                        self, request.entry_id
                    ),
                    operation_lock_held=True,
                )
                return (
                    result
                    if committed.state is ProtectionLoadState.VALID_V2
                    else AllocationResult(reason="protective_unknown")
                )
        async with self._lock:
            if not protection_gate.snapshot_ready(self):
                return AllocationResult(reason="protective_unknown")
            self._warn_if_gate_expired()
            result = self._allocate_unlocked(request)
            if result.code is not None and (
                not self._registry_lost
                or self._registry_missing
                or recovery.rebuilt_registry_trusted(self)
            ):
                self._store.async_save(self._registry)
                self._registry_lost = False
                self._registry_missing = False
            return result

    def _allocate_unlocked(self, request: AllocationRequest) -> AllocationResult:
        """Allocate a code while the allocator lock is already held."""
        return issuance.allocate_request(self, request)

    def _warn_if_gate_expired(self) -> None:
        """Warn once when the adoption gate remains closed past its deadline."""
        if (
            not self._pending_adoption
            or self._gate_deadline == 0.0
            or self._gate_warning_sent
            or monotonic() < self._gate_deadline
        ):
            return
        pending_count = len(self._pending_adoption)
        _LOGGER.warning(
            "Shared code allocator is still waiting for adoption from "
            "%d entries. New code issuance remains disabled.",
            pending_count,
        )
        message = (
            "Rental Control is still waiting to read the door codes "
            f"already programmed in {pending_count} entries. Those entries "
            "issue no new door codes until that finishes. While any of their "
            "locks still has a slot that has not been read, no other "
            "entry issues new door codes either, because every entry "
            "shares one door code space. Review the Keymaster readiness "
            "status for affected entries."
            + (_REGISTRY_LOST_NOTICE if self._registry_lost else "")
        )
        after_commit(
            self,
            lambda: async_create(
                self.hass,
                message,
                title=f"{NAME} code adoption pending",
                notification_id=_GATE_NOTIFICATION_ID,
            ),
        )
        self._gate_warning_sent = True
