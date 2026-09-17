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
from . import issuance
from .models import AdoptionRequest
from .models import AllocationOrigin
from .models import AllocationOwner
from .models import AllocationRecord
from .models import AllocationRequest
from .models import AllocationResult
from .models import CycleObservation
from .models import CycleRequest
from .models import CycleResult
from .models import OrphanCleanupReport
from .models import ReleaseReport
from .registry import AllocationRegistry
from .store import RegistryStore
from .store import code_ref_for

_LOGGER = logging.getLogger(__name__)
_ADOPTION_GATE_WARNING_SECONDS = 300.0
_CONFLICT_NOTIFICATION_ID = f"{DOMAIN}_code_registry_conflict"
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
        self._gate_warning_sent = False
        self._reported_identity_mismatches: set[tuple[str, str, str]] = set()

    async def async_load(self) -> None:
        """Load the persisted registry before the allocator is used."""
        result = await self._store.async_load()
        self._registry = result.registry
        self._registry_lost = result.registry_lost

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

    async def async_resolve_cycle(self, request: CycleRequest) -> CycleResult:
        """Resolve one refresh cycle atomically under the allocator lock."""
        if not isinstance(request, CycleRequest):
            msg = "async_resolve_cycle requires a CycleRequest"
            raise TypeError(msg)
        return await issuance.resolve_cycle(self, request)

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
        now = datetime.now(UTC).isoformat()
        self._coalesce_fingerprint_owner(request)
        existing = self._registry.record_for_identity(request.identity_key)
        if existing is not None and existing.code != request.code:
            observed_ref = self.code_ref(request.code)
            _LOGGER.warning(
                "Identity %s already owns code_ref %s; observed code_ref %s "
                "will be retained on the lock and not rotated",
                request.identity_key,
                existing.code_ref,
                observed_ref,
            )
            self._record_mismatched_observed_owner(request, observed_ref, now)
            self._report_identity_mismatch(request, existing, observed_ref)
            return AllocationResult(
                code=request.code,
                origin=AllocationOrigin.ADOPTED,
                reason="identity_code_mismatch",
            )

        self._release_moved_observed_alias(request)
        owner = AllocationOwner(
            entry_id=request.entry_id,
            identity_key=request.identity_key,
            origin=AllocationOrigin.ADOPTED,
            lockname=request.lockname,
            slot=request.slot,
            lock_observed=True,
            first_seen=now,
            last_seen=now,
        )
        before = self._registry.records.get(request.code)
        before_identities = (
            {owner.identity_key for owner in before.owners}
            if before is not None
            else set()
        )
        record = self._registry.add_owner(
            request.code,
            request.code_length,
            owner,
            self.code_ref(request.code),
            now,
        )
        if (
            before is not None
            and request.identity_key not in before_identities
            and len(record.owners) > 1
        ):
            self._report_adoption_conflict(record)
        return AllocationResult(code=request.code, origin=AllocationOrigin.ADOPTED)

    def _record_mismatched_observed_owner(
        self,
        request: AdoptionRequest,
        observed_ref: str,
        now: str,
    ) -> None:
        """Record observed-code ownership without moving the primary identity."""
        alias_key = issuance.observed_alias_key(request)
        self._release_moved_observed_alias(request)
        owner = AllocationOwner(
            entry_id=request.entry_id,
            identity_key=alias_key,
            origin=AllocationOrigin.ADOPTED,
            lockname=request.lockname,
            slot=request.slot,
            lock_observed=True,
            first_seen=now,
            last_seen=now,
        )
        before = self._registry.records.get(request.code)
        before_identities = (
            {owner.identity_key for owner in before.owners}
            if before is not None
            else set()
        )
        record = self._registry.add_owner(
            request.code,
            request.code_length,
            owner,
            observed_ref,
            now,
        )
        if (
            before is not None
            and alias_key not in before_identities
            and len(record.owners) > 1
        ):
            self._report_adoption_conflict(record)

    def _release_moved_observed_alias(self, request: AdoptionRequest) -> None:
        """Remove this slot's observed alias when it points at another code."""
        alias_key = issuance.observed_alias_key(request)
        owned_code = self._registry.code_for_identity(alias_key)
        if owned_code is not None and owned_code != request.code:
            self._registry.release(alias_key)

    def _coalesce_fingerprint_owner(self, request: AdoptionRequest) -> None:
        """Re-key a historical owner before adoption conflict detection."""
        historical_keys = set(request.fingerprint_history)
        if not historical_keys:
            return
        if request.identity_key in self._registry.by_identity:
            return
        for historical_key in historical_keys:
            code = self._registry.by_identity.get(historical_key)
            if code is None:
                continue
            record = self._registry.records.get(code)
            if record is None:
                self._registry.by_identity.pop(historical_key, None)
                continue
            self._release_historical_observed_alias(request, historical_key)
            self._rekey_historical_owner(
                record,
                historical_key,
                request.identity_key,
                request.code,
            )
            return

    def _rekey_historical_owner(
        self,
        record: AllocationRecord,
        historical_key: str,
        identity_key: str,
        new_code: str,
    ) -> None:
        """Move or remove one historical owner while preserving other owners."""
        for owner in list(record.owners):
            if owner.identity_key != historical_key:
                continue
            self._registry.by_identity.pop(historical_key, None)
            if record.code == new_code:
                owner.identity_key = identity_key
                self._registry.by_identity[identity_key] = record.code
                return
            record.owners.remove(owner)
            if not record.owners:
                self._registry.records.pop(record.code, None)
            return

    def _release_historical_observed_alias(
        self,
        request: AdoptionRequest,
        historical_key: str,
    ) -> None:
        """Remove a historical identity's observed alias for this physical slot."""
        alias_key = issuance.observed_alias_key(request, identity_key=historical_key)
        if self._registry.code_for_identity(alias_key) is not None:
            self._registry.release(alias_key)

    def _report_adoption_conflict(self, record: AllocationRecord) -> None:
        """Log and notify that an observed code has multiple owners."""
        owners = ", ".join(
            f"{owner.entry_id}:{owner.identity_key}@{owner.lockname}:{owner.slot}"
            for owner in record.owners
        )
        message = (
            f"Shared code registry adoption conflict for code_ref "
            f"{record.code_ref}: {owners}. No code was rotated."
        )
        _LOGGER.warning(message)
        async_create(
            self.hass,
            message,
            title=f"{NAME} code registry conflict",
            notification_id=_CONFLICT_NOTIFICATION_ID,
        )

    def _report_identity_mismatch(
        self,
        request: AdoptionRequest,
        existing: AllocationRecord,
        observed_ref: str,
    ) -> None:
        """Log and notify that one identity has conflicting observed code refs."""
        mismatch = (request.entry_id, request.identity_key, observed_ref)
        if mismatch in self._reported_identity_mismatches:
            return
        self._reported_identity_mismatches.add(mismatch)
        message = (
            f"Shared code registry identity mismatch for {request.entry_id}:"
            f"{request.identity_key}: registry code_ref {existing.code_ref}, "
            f"observed code_ref {observed_ref}. The observed lock code was "
            "retained and no code was rotated."
        )
        _LOGGER.warning(message)
        async_create(
            self.hass,
            message,
            title=f"{NAME} code registry identity mismatch",
            notification_id=f"{_CONFLICT_NOTIFICATION_ID}_identity",
        )

    async def async_rekey(self, old_key: str, new_key: str) -> bool:
        """Re-key allocations in a later implementation phase."""
        del old_key, new_key
        return False

    async def async_allocate(self, request: object) -> AllocationResult:
        """Allocate a unique code for one reservation identity."""
        if not isinstance(request, AllocationRequest):
            msg = "async_allocate requires an AllocationRequest"
            raise TypeError(msg)
        async with self._lock:
            self._warn_if_gate_expired()
            result = self._allocate_unlocked(request)
            if result.code is not None and not self._registry_lost:
                self._store.async_save(self._registry)
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
