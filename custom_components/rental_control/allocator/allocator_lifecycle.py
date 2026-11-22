# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Lifecycle and guarded-release operations for the shared allocator."""

from __future__ import annotations

from abc import ABC
from abc import abstractmethod
import asyncio
from collections.abc import Collection
import copy
import logging
from typing import Any

from homeassistant.core import HomeAssistant

from ..const import DOMAIN
from ..const import PROTECTED_REGISTRY_STORE
from . import adoption_reports
from . import orphans
from . import protection_gate
from . import reissue
from . import release_guard
from . import services
from .association_references import ledger_allocation_references
from .models import AllocationOwner
from .models import AllocationRecord
from .models import CycleObservation
from .models import ObservedLock
from .models import OrphanCleanupReport
from .models import ProtectionLoadState
from .models import ReleaseReport
from .registry import AllocationRegistry
from .registry_mutation import async_commit_staged_registry
from .registry_mutation import clone_allocator
from .registry_mutation import observation_preflight
from .registry_mutation import owner_transition_authorizer
from .registry_safety_models import validate_pending_fence
from .store import RegistryStore

_LOGGER = logging.getLogger(__name__)


class AllocatorLifecycleMixin(ABC):
    """Provide guarded release, cleanup, and observation lifecycle methods."""

    hass: HomeAssistant
    _lock: asyncio.Lock
    _registry: AllocationRegistry
    _store: RegistryStore
    _mutation_lock: Any
    _protection_required: bool
    _registry_lost: bool
    _registry_missing: bool
    _pending_adoption: set[str]
    _pending_adoption_slots: dict[str, frozenset[int]]
    _adopted_locks: dict[str, ObservedLock]
    _read_pending_locks: dict[str, tuple[ObservedLock, frozenset[str], frozenset[int]]]
    _gate_deadline: float
    _gate_warning_sent: bool
    _reported_identity_mismatches: set[tuple[str, str, str]]
    _conflict_verdicts: dict[str, adoption_reports.ConflictVerdict]

    @abstractmethod
    def _rekey_unlocked(self, old_key: str, new_key: str) -> bool:
        """Re-key one allocation identity while the allocator lock is held."""
        ...

    @abstractmethod
    def _warn_if_gate_expired(self) -> None:
        """Emit the one-time warning when the adoption gate expires."""
        ...

    @abstractmethod
    def _adopt_unlocked(self, request: Any) -> Any:
        """Adopt a code under the allocator lock."""
        ...

    @abstractmethod
    def _allocate_unlocked(self, request: Any) -> Any:
        """Allocate a code under the allocator lock."""
        ...

    async def async_sweep(
        self: AllocatorLifecycleMixin,
        observation: CycleObservation,
        active_keys: set[str],
    ) -> ReleaseReport:
        """Release inactive allocations proven absent from the observed lock."""
        if not await protection_gate.async_operation_ready(self):
            return await protection_gate.async_retention_report(
                self, observation.entry_id
            )
        if self._protection_required:
            from ..keymaster_operations import async_refence_observed_legacy_owners

            await async_refence_observed_legacy_owners(self.hass, observation)
            async with self._mutation_lock:
                async with self._lock:
                    if not protection_gate.snapshot_ready(self):
                        return protection_gate.retention_report(
                            self, observation.entry_id
                        )
                    baseline = copy.deepcopy(self._registry)
                    staged = clone_allocator(self)
                    report = staged._sweep_unlocked(observation, active_keys)
                committed = await async_commit_staged_registry(
                    self,
                    baseline,
                    staged,
                    authorize_owner_transition=owner_transition_authorizer(
                        self, [observation]
                    ),
                    event_loop_preflight=lambda: observation_preflight(
                        self, [observation]
                    ),
                    operation_lock_held=True,
                )
                return (
                    report
                    if committed.state is ProtectionLoadState.VALID_V2
                    else protection_gate.retention_report(self, observation.entry_id)
                )
        async with self._lock:
            if not protection_gate.snapshot_ready(self):
                return protection_gate.retention_report(self, observation.entry_id)
            report = self._sweep_unlocked(observation, active_keys)
            if report.released:
                self._store.async_save(self._registry)
            return report

    def _sweep_unlocked(
        self: AllocatorLifecycleMixin,
        observation: CycleObservation,
        active_keys: set[str],
    ) -> ReleaseReport:
        """Sweep inactive owners while the allocator lock is already held."""
        released = []
        retained = []
        observations = [observation]
        for record in list(self._registry.records.values()):
            for owner in list(record.owners):
                if owner.entry_id != observation.entry_id:
                    continue
                if reissue.is_forced_release_hold(owner.identity_key):
                    continue
                if owner.identity_key in active_keys:
                    self._refresh_owner_observed(record, owner, observations)
                    continue
                reason = self._release_guard_reason(record, [owner], observations)
                outcome = orphans.build_outcome(record, owner, reason)
                if reason is None:
                    self._registry.release(owner.identity_key)
                    released.append(outcome)
                    _LOGGER.info("Released shared allocation ownership")
                else:
                    retained.append(outcome)
        return ReleaseReport(released=released, retained=retained)

    async def async_mark_entry_removed(
        self: AllocatorLifecycleMixin, entry_id: str
    ) -> ReleaseReport:
        """Release lockless removed owners and retain unverifiable lock owners."""
        if not await protection_gate.async_operation_ready(self):
            return await protection_gate.async_retention_report(self, entry_id)
        if self._protection_required:
            async with self._mutation_lock:
                async with self._lock:
                    if not protection_gate.snapshot_ready(self):
                        return protection_gate.retention_report(self, entry_id)
                    baseline = copy.deepcopy(self._registry)
                    staged = clone_allocator(self)
                    report = staged._mark_entry_removed_unlocked(entry_id)
                committed = await async_commit_staged_registry(
                    self,
                    baseline,
                    staged,
                    authorize_owner_transition=owner_transition_authorizer(self, []),
                    operation_lock_held=True,
                )
                return (
                    report
                    if committed.state is ProtectionLoadState.VALID_V2
                    else protection_gate.retention_report(self, entry_id)
                )
        async with self._lock:
            if not protection_gate.snapshot_ready(self):
                return protection_gate.retention_report(self, entry_id)
            report = self._mark_entry_removed_unlocked(entry_id)
            if report.released or report.retained:
                self._store.async_save(self._registry)
            return report

    def _mark_entry_removed_unlocked(
        self: AllocatorLifecycleMixin, entry_id: str
    ) -> ReleaseReport:
        """Update removal state while the allocator lock is already held."""
        self._pending_adoption.discard(entry_id)
        self._pending_adoption_slots.pop(entry_id, None)
        self._adopted_locks.pop(entry_id, None)
        if not self._pending_adoption:
            self._gate_deadline = 0.0
            self._gate_warning_sent = False
        released = []
        retained = []
        for record in list(self._registry.records.values()):
            for owner in list(record.owners):
                if owner.entry_id != entry_id:
                    continue
                reason = self._release_guard_reason(record, [owner], [])
                outcome = orphans.build_outcome(record, owner, reason)
                if reason is None:
                    self._registry.release(owner.identity_key)
                    released.append(outcome)
                else:
                    retained.append(outcome)
        return ReleaseReport(released=released, retained=retained)

    async def async_clear_orphans(
        self: AllocatorLifecycleMixin,
        known_entry_ids: set[str],
        observations: list[CycleObservation],
        dry_run: bool = False,
        force_reissued_holds: bool = False,
        loaded_entry_ids: set[str] | None = None,
    ) -> OrphanCleanupReport:
        """Clear orphaned allocations that the shared guard proves safe."""
        if not await protection_gate.async_operation_ready(self):
            return await protection_gate.async_cleanup_report(self, dry_run)
        if self._protection_required:
            if not dry_run:
                from ..keymaster_operations import async_refence_observed_legacy_owners

                for observation in observations:
                    await async_refence_observed_legacy_owners(self.hass, observation)
            async with self._mutation_lock:
                async with self._lock:
                    if not protection_gate.snapshot_ready(self):
                        return OrphanCleanupReport(
                            dry_run,
                            retained=protection_gate.retention_report(self).retained,
                        )
                    baseline = copy.deepcopy(self._registry)
                    staged = clone_allocator(self)
                    report = orphans.clear_orphans(
                        staged,
                        known_entry_ids,
                        observations,
                        dry_run=dry_run,
                        force_reissued_holds=force_reissued_holds,
                        loaded_entry_ids=loaded_entry_ids or set(),
                    )
                if not dry_run:
                    committed = await async_commit_staged_registry(
                        self,
                        baseline,
                        staged,
                        authorize_owner_transition=owner_transition_authorizer(
                            self, observations
                        ),
                        event_loop_preflight=lambda: observation_preflight(
                            self, observations
                        ),
                        operation_lock_held=True,
                    )
                    if committed.state is not ProtectionLoadState.VALID_V2:
                        report = OrphanCleanupReport(
                            False,
                            retained=protection_gate.retention_report(self).retained,
                        )
                services.report_orphan_cleanup(self.hass, report)
                if not dry_run and (report.cleared or report.retained):
                    services.report_forced_hold_deferrals(self, (), observations)
                return report
        async with self._lock:
            if not protection_gate.snapshot_ready(self):
                return OrphanCleanupReport(
                    dry_run,
                    retained=protection_gate.retention_report(self).retained,
                )
            report = orphans.clear_orphans(
                self,
                known_entry_ids,
                observations,
                dry_run=dry_run,
                force_reissued_holds=force_reissued_holds,
                loaded_entry_ids=loaded_entry_ids or set(),
            )
            if report.cleared and not dry_run:
                self._store.async_save(self._registry)
            services.report_orphan_cleanup(self.hass, report)
            if not dry_run and (report.cleared or report.retained):
                services.report_forced_hold_deferrals(self, (), observations)
            return report

    def _release_guard_reason(
        self: AllocatorLifecycleMixin,
        record: AllocationRecord,
        owners: list[AllocationOwner],
        observations: list[CycleObservation],
        *,
        refresh_observed: bool = True,
        forced_release: reissue.ForcedReleaseExemption | None = None,
        releasing: Collection[str] = (),
    ) -> str | None:
        """Return why owners must be retained, or None when safe to release."""
        required_keymaster_identities = self._required_keymaster_identities(owners)
        return release_guard.release_guard_reason(
            record,
            owners,
            observations,
            refresh_observed=refresh_observed,
            forced_release=forced_release,
            releasing=releasing,
            required_keymaster_identities=required_keymaster_identities,
        )

    def _required_keymaster_identities(
        self: AllocatorLifecycleMixin, owners: list[AllocationOwner]
    ) -> dict[tuple[str, str], tuple[str, int] | None] | None:
        """Bind protected-owner release evidence to its acknowledged KM epoch.

        A missing protected store means the legacy allocator path is still in
        use. Once protected state exists, every owner must have one exact
        allocation fence and a live observation from that fence's original
        selected Keymaster lifecycle.
        """
        domain_data = self.hass.data.get(DOMAIN)
        store = (
            domain_data.get(PROTECTED_REGISTRY_STORE)
            if isinstance(domain_data, dict)
            else None
        )
        if store is None:
            if not self._protection_required:
                return None
            return {(owner.entry_id, owner.identity_key): None for owner in owners}

        from ..coordinator_helpers.cycle_requests import verified_observation_identity
        from .models import ProtectionAuthorizationState
        from .models import ProtectionLoadState

        loaded = getattr(store, "loaded_result", None)
        required: dict[tuple[str, str], tuple[str, int] | None] = {}
        if (
            getattr(store, "authorization_state", None)
            is not ProtectionAuthorizationState.READY
            or loaded is None
            or loaded.state is not ProtectionLoadState.VALID_V2
            or loaded.data is None
        ):
            return {(owner.entry_id, owner.identity_key): None for owner in owners}

        for owner in owners:
            key = (owner.entry_id, owner.identity_key)
            references = ledger_allocation_references(
                loaded.data, owner.entry_id, owner.identity_key
            )
            if references is None or references[2] != owner.slot:
                required[key] = None
                continue
            owner_reference = references[0]
            fences = [
                fence
                for fence in loaded.data.get("pending_fences", [])
                if validate_pending_fence(fence)
                and fence["fence_type"] == "allocation"
                and fence["rc_entry_id"] == owner.entry_id
                and fence["owner_ref"] == owner_reference
                and fence["slot"] == owner.slot
            ]
            identity = verified_observation_identity(self.hass, owner.entry_id)
            if (
                len(fences) != 1
                or identity is None
                or fences[0]["original_km_entry_id"] != identity[0]
            ):
                required[key] = None
                continue
            required[key] = identity
        return required

    def _refresh_owner_observed(
        self: AllocatorLifecycleMixin,
        record: AllocationRecord,
        owner: AllocationOwner,
        observations: list[CycleObservation],
    ) -> None:
        """Refresh observed state without making a release decision."""
        release_guard.owner_still_programmed(record, owner, observations)
