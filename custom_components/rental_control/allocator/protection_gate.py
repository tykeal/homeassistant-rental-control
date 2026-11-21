# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Fail-closed boundaries for optional protected allocator state."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from ..const import DOMAIN
from ..const import PROTECTED_REGISTRY_STORE
from . import orphans
from . import reissue
from .models import AllocationResult
from .models import CycleRequest
from .models import CycleResult
from .models import OrphanCleanupReport
from .models import ReissuePreview
from .models import ReissuePreviewRequest
from .models import ReleaseReport

if TYPE_CHECKING:
    from .allocator import DoorCodeAllocator

_INVALID_PROTECTED_STATE = object()


def _protected_store(allocator: DoorCodeAllocator) -> object | None:
    """Return the optional dormant protected store from integration state."""
    domain_data = allocator.hass.data.get(DOMAIN)
    if domain_data is None:
        return None
    if not isinstance(domain_data, dict):
        return _INVALID_PROTECTED_STATE
    return domain_data.get(PROTECTED_REGISTRY_STORE)


def snapshot_ready(allocator: DoorCodeAllocator) -> bool:
    """Require a READY acknowledged snapshot when a protected store exists."""
    store = _protected_store(allocator)
    if store is None:
        return True
    if store is _INVALID_PROTECTED_STATE:
        return False
    readiness = getattr(store, "operation_snapshot_ready", None)
    try:
        return bool(callable(readiness) and readiness())
    except Exception:
        return False


async def async_operation_ready(allocator: DoorCodeAllocator) -> bool:
    """Wait for protected commits and fail closed on missing readiness APIs."""
    store = _protected_store(allocator)
    if store is None:
        return True
    if store is _INVALID_PROTECTED_STATE:
        return False
    waiter = getattr(store, "async_wait_for_commit_completion", None)
    if not callable(waiter):
        return False
    try:
        return bool(await waiter()) and snapshot_ready(allocator)
    except Exception:
        return False


async def async_preview_reissue(
    allocator: DoorCodeAllocator, request: ReissuePreviewRequest
) -> ReissuePreview:
    """Prevent code previews until the protected snapshot is settled."""
    if not await async_operation_ready(allocator):
        return ReissuePreview(reason="protective_unknown")
    async with allocator._lock:
        if not snapshot_ready(allocator):
            return ReissuePreview(reason="protective_unknown")
        from . import reissue_preview

        return reissue_preview.preview_reissue(allocator, request)


async def async_rekey(allocator: DoorCodeAllocator, old_key: str, new_key: str) -> bool:
    """Move an allocation identity only while protected state is READY."""
    if not await async_operation_ready(allocator):
        return False
    async with allocator._lock:
        if not snapshot_ready(allocator):
            return False
        changed = allocator._rekey_unlocked(old_key, new_key)
        if changed:
            allocator._store.async_save(allocator._registry)
        return changed


def protective_unknown_cycle_result(request: CycleRequest) -> CycleResult:
    """Decline allocator mutations while retaining all existing evidence."""
    allocated = {
        allocation.identity_key: AllocationResult(reason="protective_unknown")
        for allocation in request.allocations
    }
    reissues = tuple(
        replace(outcome, retention_reason="protective_unknown")
        for outcome in reissue.fail_closed_forced_reissues(request)
    )
    return CycleResult(
        adopted={},
        allocated=allocated,
        released=ReleaseReport(),
        unaccounted_slots=frozenset(),
        reissues=reissues,
    )


def retention_report(
    allocator: DoorCodeAllocator, entry_id: str | None = None
) -> ReleaseReport:
    """Describe retained owners without mutating allocator state."""
    retained = [
        orphans.build_outcome(record, owner, "protective_unknown")
        for record in allocator._registry.records.values()
        for owner in record.owners
        if entry_id is None or owner.entry_id == entry_id
    ]
    return ReleaseReport(retained=retained)


async def async_retention_report(
    allocator: DoorCodeAllocator, entry_id: str | None = None
) -> ReleaseReport:
    """Snapshot retained owners without racing allocator mutations."""
    async with allocator._lock:
        return retention_report(allocator, entry_id)


async def async_cleanup_report(
    allocator: DoorCodeAllocator, dry_run: bool
) -> OrphanCleanupReport:
    """Return a non-mutating cleanup result while protection is unknown."""
    report = await async_retention_report(allocator)
    return OrphanCleanupReport(dry_run, retained=report.retained)
