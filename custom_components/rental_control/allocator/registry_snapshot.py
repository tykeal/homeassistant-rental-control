# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Dormant staged commits for complete, protected allocator snapshots."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Mapping
import copy
import secrets
from typing import Any

from ..const import CONF_EXCLUDED_SLOTS
from ..const import STORE_CODE_REGISTRY_KEY
from .models import ProtectionAuthorizationState
from .models import ProtectionLoadResult
from .models import ProtectionLoadState
from .protected_store import ProtectedRegistryStore
from .registry import AllocationRegistry
from .registry_validation import valid_exclusions_section
from .registry_validation import validate_v2_envelope_shape
from .store import serialize_registry_ledger


def _unavailable(code: str) -> ProtectionLoadResult:
    """Create one fixed-code failure without retaining caller error details."""
    return ProtectionLoadResult(ProtectionLoadState.UNAVAILABLE, failure_code=code)


def serialize_exclusion_snapshots(entries: Iterable[Any]) -> list[dict[str, Any]]:
    """Capture every current RC exclusion without silently dropping bad data."""
    snapshots: list[dict[str, Any]] = []
    seen_entries: set[str] = set()
    for entry in entries:
        entry_id = getattr(entry, "entry_id", None)
        data = getattr(entry, "data", None)
        if (
            not isinstance(entry_id, str)
            or not entry_id
            or entry_id in seen_entries
            or not isinstance(data, Mapping)
        ):
            raise ValueError("invalid exclusion source")
        seen_entries.add(entry_id)
        raw_slots = data.get(CONF_EXCLUDED_SLOTS, [])
        if not isinstance(raw_slots, list):
            raise ValueError("invalid exclusion list")
        by_slot: dict[int, str | None] = {}
        for item in raw_slots:
            if (
                not isinstance(item, dict)
                or set(item) - {"slot", "reason"}
                or "slot" not in item
            ):
                raise ValueError("invalid exclusion record")
            slot = item["slot"]
            reason = item.get("reason")
            if (
                not isinstance(slot, int)
                or isinstance(slot, bool)
                or slot < 1
                or slot in by_slot
                or (reason is not None and not isinstance(reason, str))
            ):
                raise ValueError("invalid exclusion record")
            by_slot[slot] = reason
        if by_slot:
            snapshots.append(
                {
                    "rc_entry_id": entry_id,
                    "slots": [
                        {"slot": slot, "reason": by_slot[slot]}
                        for slot in sorted(by_slot)
                    ],
                }
            )
    snapshots.sort(key=lambda item: item["rc_entry_id"])
    return snapshots


async def async_commit_registry_snapshot(
    allocator: Any,
    protected_store: ProtectedRegistryStore,
    mutate: Callable[[AllocationRegistry], None],
    exclusions: list[dict[str, Any]],
    *,
    authorize_owner_transition: Callable[[AllocationRegistry, AllocationRegistry], bool]
    | None = None,
    safety_transition: Callable[[AllocationRegistry, dict[str, Any], str, int], None]
    | None = None,
) -> ProtectionLoadResult:
    """Commit a staged complete snapshot before publishing it in memory.

    The operation is deliberately injectable and is not selected by the
    production allocator. It requires the protected store and allocator to
    share one lock, snapshots under that lock, then rechecks the exact base
    while the store holds that same lock for I/O. Only the acknowledged
    completion callback publishes the staged registry before releasing it.
    Any prior-owner removal or physical reassignment also needs an explicit
    allocator release-guard callback; the protected transition validator
    independently requires exact prior-fence resolution and fresh location
    evidence.
    """
    lock: asyncio.Lock = allocator._lock
    if not protected_store.uses_async_lock(lock):
        return _unavailable("allocator_lock_mismatch")
    if not valid_exclusions_section(exclusions):
        return _unavailable("snapshot_invalid")

    async with lock:
        try:
            baseline: AllocationRegistry = copy.deepcopy(allocator._registry)
            candidate = copy.deepcopy(baseline)
            mutate(candidate)
            loaded = protected_store.loaded_result
            if (
                protected_store.authorization_state
                is not ProtectionAuthorizationState.READY
                or loaded is None
                or loaded.state is not ProtectionLoadState.VALID_V2
                or loaded.data is None
                or loaded.generation is None
            ):
                return _unavailable("authorization_required")
            data = copy.deepcopy(loaded.data)
            transaction_id = secrets.token_hex(16)
            target_generation = loaded.generation + 1
            data.update(
                schema_version=2,
                transaction_id=transaction_id,
                generation=target_generation,
                ledger=serialize_registry_ledger(candidate),
                exclusions=copy.deepcopy(exclusions),
            )
            if safety_transition is not None:
                safety_transition(
                    candidate,
                    data,
                    transaction_id,
                    target_generation,
                )
            envelope = {
                "version": 2,
                "minor_version": 1,
                "key": STORE_CODE_REGISTRY_KEY,
                "data": data,
            }
        except Exception:
            return _unavailable("snapshot_precondition_failed")

    base_transaction_id = loaded.transaction_id
    base_generation = loaded.generation

    def precondition() -> ProtectionLoadResult | None:
        """Reject a stale staged candidate before marker or registry I/O."""
        current = protected_store.loaded_result
        if (
            allocator._registry != baseline
            or current is None
            or current.state is not ProtectionLoadState.VALID_V2
            or current.transaction_id != base_transaction_id
            or current.generation != base_generation
        ):
            return _unavailable("registry_changed")
        return None

    def authorize_staged_owner_transition() -> bool:
        """Run the real allocator release guard on isolated staged models."""
        if authorize_owner_transition is None:
            return False
        return authorize_owner_transition(
            copy.deepcopy(baseline),
            copy.deepcopy(candidate),
        )

    def publish(_result: ProtectionLoadResult) -> None:
        """Apply the exact staged model after durable acknowledgement."""
        allocator._registry = candidate

    if not validate_v2_envelope_shape(envelope):
        return _unavailable("snapshot_invalid")
    return await protected_store.async_commit(
        envelope,
        precondition=precondition,
        authorize_owner_transition=authorize_staged_owner_transition,
        on_acknowledged=publish,
    )
