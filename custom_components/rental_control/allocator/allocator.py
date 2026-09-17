# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Shared door-code allocator shell and lifecycle state."""

from __future__ import annotations

import asyncio
from datetime import UTC
from datetime import datetime
import logging
from time import monotonic
from typing import Any

from homeassistant.components.persistent_notification import async_create
from homeassistant.core import HomeAssistant

from ..const import CONF_LOCK_ENTRY
from ..const import DOMAIN
from ..const import NAME
from . import adoption
from . import diagnostics
from . import issuance
from . import orphans
from . import reissue
from . import services
from .models import AdoptionRequest
from .models import AllocationOwner
from .models import AllocationRecord
from .models import AllocationRequest
from .models import AllocationResult
from .models import CycleObservation
from .models import CycleRequest
from .models import CycleResult
from .models import OrphanCleanupReport
from .models import ReissuePreview
from .models import ReissuePreviewRequest
from .models import ReleaseReport
from .registry import AllocationRegistry
from .store import RegistryStore
from .store import code_ref_for

_LOGGER = logging.getLogger(__name__)
_ADOPTION_GATE_WARNING_SECONDS = 300.0
_GATE_NOTIFICATION_ID = f"{DOMAIN}_code_registry_adoption_pending"


def _entry_has_lock(data: object) -> bool:
    """Return whether config entry data references a Keymaster lock."""
    if not isinstance(data, dict):
        return False
    lock_entry = data.get(CONF_LOCK_ENTRY)
    return (
        isinstance(lock_entry, str)
        and bool(lock_entry.strip())
        and lock_entry.strip() != "(none)"
    )


class DoorCodeAllocator:
    """System-wide allocator state shared by all Rental Control entries."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the allocator with empty in-memory state."""
        self.hass = hass
        self._registry = AllocationRegistry()
        self._store = RegistryStore(hass)
        self._lock = asyncio.Lock()
        self._pending_adoption = self._seed_pending_adoption()
        self._gate_deadline = (
            monotonic() + _ADOPTION_GATE_WARNING_SECONDS
            if self._pending_adoption
            else 0.0
        )
        self._registry_lost = False
        self._registry_missing = False
        self._gate_warning_sent = False
        self._reported_identity_mismatches: set[tuple[str, str, str]] = set()

    async def async_load(self) -> None:
        """Load the persisted registry before the allocator is used."""
        result = await self._store.async_load()
        self._registry = result.registry
        self._registry_lost = result.registry_lost
        self._registry_missing = result.registry_missing

    def _seed_pending_adoption(self) -> set[str]:
        """Return currently configured Rental Control entries awaiting adoption."""
        return {
            entry.entry_id
            for entry in self.hass.config_entries.async_entries(DOMAIN)
            if getattr(entry, "disabled_by", None) is None
            and _entry_has_lock(getattr(entry, "data", {}))
        }

    async def async_register_entry(self, entry_id: str) -> None:
        """Mark an entry as awaiting its first allocator cycle."""
        async with self._lock:
            self._pending_adoption.add(entry_id)
            if self._gate_deadline == 0.0:
                self._gate_deadline = monotonic() + _ADOPTION_GATE_WARNING_SECONDS
                self._gate_warning_sent = False

    async def async_unregister_entry(self, entry_id: str) -> None:
        """Remove an entry from the pending adoption gate."""
        async with self._lock:
            self._pending_adoption.discard(entry_id)
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
        async with self._lock:
            from . import reissue_preview

            return reissue_preview.preview_reissue(self, request)

    async def async_adopt(self, request: object) -> AllocationResult:
        """Adopt an observed lock code into the shared registry."""
        if not isinstance(request, AdoptionRequest):
            msg = "async_adopt requires an AdoptionRequest"
            raise TypeError(msg)
        async with self._lock:
            result = self._adopt_unlocked(request)
            if not self._registry_lost:
                self._store.async_save(self._registry)
            return result

    def _adopt_unlocked(self, request: AdoptionRequest) -> AllocationResult:
        """Adopt an observed code while the allocator lock is already held."""
        return adoption.adopt_unlocked(self, request)

    async def async_rekey(self, old_key: str, new_key: str) -> bool:
        """Move an existing allocation identity to a new stable key."""
        async with self._lock:
            changed = self._rekey_unlocked(old_key, new_key)
            if changed:
                self._store.async_save(self._registry)
            return changed

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
            _LOGGER.info(
                "Re-keyed shared allocation code_ref %s from %s to %s",
                record.code_ref,
                old_key,
                new_key,
            )
            return True
        return False

    async def async_allocate(self, request: object) -> AllocationResult:
        """Allocate a unique code for one reservation identity."""
        if not isinstance(request, AllocationRequest):
            msg = "async_allocate requires an AllocationRequest"
            raise TypeError(msg)
        async with self._lock:
            self._warn_if_gate_expired()
            result = self._allocate_unlocked(request)
            if result.code is not None and (
                not self._registry_lost or self._registry_missing
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
        pending = ", ".join(sorted(self._pending_adoption))
        message = (
            "Shared code allocator is still waiting for adoption from "
            f"entries: {pending}. New code issuance remains disabled."
        )
        _LOGGER.warning(message)
        async_create(
            self.hass,
            message,
            title=f"{NAME} code adoption pending",
            notification_id=_GATE_NOTIFICATION_ID,
        )
        self._gate_warning_sent = True

    async def async_sweep(
        self, observation: CycleObservation, active_keys: set[str]
    ) -> ReleaseReport:
        """Release inactive allocations proven absent from the observed lock."""
        async with self._lock:
            report = self._sweep_unlocked(observation, active_keys)
            if report.released:
                self._store.async_save(self._registry)
            return report

    def _sweep_unlocked(
        self, observation: CycleObservation, active_keys: set[str]
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
                reason = self._release_guard_reason(
                    record,
                    [owner],
                    observations,
                )
                outcome = orphans.build_outcome(record, owner, reason)
                if reason is None:
                    self._registry.release(owner.identity_key)
                    released.append(outcome)
                    _LOGGER.info(
                        "Released shared allocation code_ref %s for %s:%s",
                        record.code_ref,
                        owner.entry_id,
                        owner.identity_key,
                    )
                else:
                    retained.append(outcome)
        return ReleaseReport(released=released, retained=retained)

    async def async_mark_entry_removed(self, entry_id: str) -> ReleaseReport:
        """Release lockless removed owners and retain unverifiable lock owners."""
        async with self._lock:
            self._pending_adoption.discard(entry_id)
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
            if released or retained:
                self._store.async_save(self._registry)
            return ReleaseReport(released=released, retained=retained)

    async def async_clear_orphans(
        self,
        known_entry_ids: set[str],
        observations: list[CycleObservation],
        dry_run: bool = False,
        force_reissued_holds: bool = False,
    ) -> OrphanCleanupReport:
        """Clear orphaned allocations that the shared guard proves safe."""
        async with self._lock:
            report = orphans.clear_orphans(
                self,
                known_entry_ids,
                observations,
                dry_run=dry_run,
                force_reissued_holds=force_reissued_holds,
            )
            if report.cleared and not dry_run:
                self._store.async_save(self._registry)
            services.report_orphan_cleanup(self.hass, report)
            return report

    def _release_guard_reason(
        self,
        record: AllocationRecord,
        owners: list[AllocationOwner],
        observations: list[CycleObservation],
        *,
        refresh_observed: bool = True,
        forced_release: reissue.ForcedReleaseExemption | None = None,
    ) -> str | None:
        """Return why owners must be retained, or None when safe to release."""
        if len(record.owners) > 1 and not reissue._conflict_exempt(
            record, owners, forced_release
        ):
            return "adoption_conflict"
        for owner in owners:
            covered_observations = [
                observation
                for observation in observations
                if observation.lockname == owner.lockname
                and owner.slot in observation.managed_slots
            ]
            if owner.lockname is not None and not covered_observations:
                return "unverifiable_lock"
            if self._owner_still_programmed(
                record,
                owner,
                covered_observations,
                refresh_observed=refresh_observed,
            ):
                return "code_still_programmed"
        return None

    def _owner_still_programmed(
        self,
        record: AllocationRecord,
        owner: AllocationOwner,
        observations: list[CycleObservation],
        *,
        refresh_observed: bool = True,
    ) -> bool:
        """Refresh and return whether an owner may still be programmed."""
        if owner.lockname is None:
            if refresh_observed:
                owner.lock_observed = False
            return False
        covered = False
        for observation in observations:
            if observation.lockname != owner.lockname:
                continue
            covered = True
            programmed = (
                record.code in observation.observed_codes
                or owner.slot in observation.unreadable_slots
            )
            if programmed:
                if refresh_observed:
                    owner.lock_observed = True
                return True
        if covered:
            if refresh_observed:
                owner.lock_observed = False
            return False
        return owner.lock_observed

    def _refresh_owner_observed(
        self,
        record: AllocationRecord,
        owner: AllocationOwner,
        observations: list[CycleObservation],
    ) -> None:
        """Refresh observed state without making a release decision."""
        self._owner_still_programmed(record, owner, observations)
