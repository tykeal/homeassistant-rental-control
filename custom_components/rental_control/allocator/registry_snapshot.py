# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Staged commits for complete, protected allocator snapshots."""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Mapping
from contextlib import AsyncExitStack
import copy
from dataclasses import dataclass
import secrets
from typing import Any

from ..const import CONF_EXCLUDED_SLOTS
from ..const import STORE_CODE_REGISTRY_KEY
from .models import ProtectionAuthorizationState
from .models import ProtectionLoadResult
from .models import ProtectionLoadState
from .protected_store import ProtectedRegistryStore
from .registry import AllocationRegistry
from .registry_snapshot_owner_transitions import owner_transition_slots
from .registry_snapshot_owner_transitions import resolve_released_owner_fences
from .registry_validation import valid_exclusions_section
from .registry_validation import validate_v2_envelope_shape
from .store import serialize_registry_ledger


@dataclass(frozen=True, slots=True)
class SnapshotCommitHooks:
    """Optional authorization and transition callbacks for one snapshot."""

    authorize_owner_transition: (
        Callable[[AllocationRegistry, AllocationRegistry], bool] | None
    ) = None
    safety_transition: (
        Callable[[AllocationRegistry, dict[str, Any], str, int], None] | None
    ) = None
    event_loop_preflight: Callable[[], ProtectionLoadResult | None] | None = None
    publish_state: Callable[[], None] | None = None


@dataclass(frozen=True, slots=True)
class _StagedSnapshot:
    """Immutable references needed to guard and publish one candidate."""

    baseline: AllocationRegistry
    candidate: AllocationRegistry
    envelope: dict[str, Any]
    base_transaction_id: str | None
    base_generation: int
    exclusion_generation: int
    base_data: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _SnapshotStageRequest:
    """Inputs for constructing an isolated registry candidate."""

    allocator: Any
    protected_store: ProtectedRegistryStore
    mutate: Callable[[AllocationRegistry], None]
    exclusions: list[dict[str, Any]]
    current_exclusion_snapshot: Callable[[], tuple[int, list[dict[str, Any]]]]
    exclusion_generation: int
    hooks: SnapshotCommitHooks


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
    current_exclusion_snapshot: Callable[[], tuple[int, list[dict[str, Any]]]],
    *,
    hooks: SnapshotCommitHooks | None = None,
    operation_lock_held: bool = False,
) -> ProtectionLoadResult:
    """Commit a staged complete snapshot before publishing it in memory.

    The production allocator routes protected mutations through this
    transaction boundary. It requires the protected store and allocator to
    share one lock, snapshots under that lock, then rechecks the exact base
    while the store holds that same lock for I/O. Only the acknowledged
    completion callback publishes the staged registry before releasing it.
    Any prior-owner removal or physical reassignment also needs an explicit
    allocator release-guard callback; the protected transition validator
    independently requires exact prior-fence resolution and fresh location
    evidence.
    """
    mutation_lock = getattr(allocator, "_mutation_lock", None)
    if mutation_lock is not None and not operation_lock_held:
        async with mutation_lock:
            return await async_commit_registry_snapshot(
                allocator,
                protected_store,
                mutate,
                exclusions,
                current_exclusion_snapshot,
                hooks=hooks,
                operation_lock_held=True,
            )

    if not protected_store.uses_async_lock(allocator._lock):
        return _unavailable("allocator_lock_mismatch")
    exclusion_generation, exclusion_error = _validated_exclusion_generation(
        exclusions,
        current_exclusion_snapshot,
    )
    if exclusion_error is not None:
        return exclusion_error
    assert exclusion_generation is not None
    commit_hooks = hooks or SnapshotCommitHooks()

    staged = await _stage_registry_snapshot(
        _SnapshotStageRequest(
            allocator,
            protected_store,
            mutate,
            exclusions,
            current_exclusion_snapshot,
            exclusion_generation,
            commit_hooks,
        )
    )
    if isinstance(staged, ProtectionLoadResult):
        return staged
    transition_slots = owner_transition_slots(
        allocator,
        staged.base_data,
        staged.envelope["data"],
    )
    if isinstance(transition_slots, ProtectionLoadResult):
        return transition_slots
    async with AsyncExitStack() as stack:
        from ..keymaster_slot_operations import SlotOperationBusy
        from ..keymaster_slot_operations import async_keymaster_slot_operation

        try:
            for transition_lock in transition_slots:
                await stack.enter_async_context(
                    async_keymaster_slot_operation(
                        allocator.hass,
                        transition_lock.keymaster_entry_id,
                        transition_lock.slot,
                        pending=transition_lock.pending,
                        wait=False,
                    )
                )
            return await _commit_staged_snapshot(
                allocator,
                protected_store,
                staged,
                exclusions,
                current_exclusion_snapshot,
                commit_hooks,
            )
        except SlotOperationBusy:
            # The mutation lock is held by the caller. Never await a slot lock
            # here: a slot-held consumer may be waiting to acknowledge its
            # fence through this same mutation lock. The caller retains its
            # current state and a later refresh retries from a fresh snapshot.
            return _unavailable("slot_operation_busy")


async def _stage_registry_snapshot(
    request: _SnapshotStageRequest,
) -> _StagedSnapshot | ProtectionLoadResult:
    """Build and validate an isolated candidate under the allocator lock."""
    allocator = request.allocator
    protected_store = request.protected_store
    async with allocator._lock:
        if not _exclusion_snapshot_is_current(
            request.current_exclusion_snapshot,
            request.exclusion_generation,
            request.exclusions,
        ):
            return _unavailable("snapshot_exclusions_changed")
        try:
            baseline: AllocationRegistry = copy.deepcopy(allocator._registry)
            candidate = copy.deepcopy(baseline)
            request.mutate(candidate)
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
            if not _ledger_matches_registry(
                loaded.data.get("ledger"),
                serialize_registry_ledger(baseline),
            ):
                return _unavailable("registry_changed")
            transaction_id = secrets.token_hex(16)
            target_generation = loaded.generation + 1
            data = copy.deepcopy(loaded.data)
            data.update(
                schema_version=2,
                transaction_id=transaction_id,
                generation=target_generation,
                ledger=serialize_registry_ledger(candidate),
                exclusions=copy.deepcopy(request.exclusions),
            )
            if request.hooks.safety_transition is not None:
                request.hooks.safety_transition(
                    candidate,
                    data,
                    transaction_id,
                    target_generation,
                )
            resolve_released_owner_fences(
                loaded.data,
                data,
                transaction_id=transaction_id,
                generation=target_generation,
            )
            envelope = {
                "version": 2,
                "minor_version": 1,
                "key": STORE_CODE_REGISTRY_KEY,
                "data": data,
            }
            if not validate_v2_envelope_shape(envelope):
                return _unavailable("snapshot_invalid")
        except Exception:
            return _unavailable("snapshot_precondition_failed")
    return _StagedSnapshot(
        baseline,
        candidate,
        envelope,
        loaded.transaction_id,
        loaded.generation,
        request.exclusion_generation,
        copy.deepcopy(loaded.data),
    )


async def _commit_staged_snapshot(
    allocator: Any,
    protected_store: ProtectedRegistryStore,
    staged: _StagedSnapshot,
    exclusions: list[dict[str, Any]],
    current_exclusion_snapshot: Callable[[], tuple[int, list[dict[str, Any]]]],
    hooks: SnapshotCommitHooks,
) -> ProtectionLoadResult:
    """Revalidate and commit a candidate, publishing only its acknowledgement."""
    if not _exclusion_snapshot_is_current(
        current_exclusion_snapshot,
        staged.exclusion_generation,
        exclusions,
    ):
        return _unavailable("snapshot_exclusions_changed")

    def precondition() -> ProtectionLoadResult | None:
        """Reject a stale staged candidate before marker or registry I/O."""
        current = protected_store.loaded_result
        if (
            allocator._registry != staged.baseline
            or current is None
            or current.state is not ProtectionLoadState.VALID_V2
            or current.transaction_id != staged.base_transaction_id
            or current.generation != staged.base_generation
            or current.data is None
            or not _ledger_matches_registry(
                current.data.get("ledger"),
                serialize_registry_ledger(staged.baseline),
            )
        ):
            return _unavailable("registry_changed")
        return None

    def authorize_staged_owner_transition() -> bool:
        """Run the allocator release guard on isolated staged models."""
        if hooks.authorize_owner_transition is None:
            return False
        return hooks.authorize_owner_transition(
            copy.deepcopy(staged.baseline),
            copy.deepcopy(staged.candidate),
        )

    def publish(_result: ProtectionLoadResult) -> None:
        """Recheck exclusions before publishing the acknowledged candidate."""
        if not _exclusion_snapshot_is_current(
            current_exclusion_snapshot,
            staged.exclusion_generation,
            exclusions,
        ):
            raise ValueError("snapshot_exclusions_changed")
        allocator._registry = staged.candidate
        if hooks.publish_state is not None:
            hooks.publish_state()

    def locked_preflight() -> ProtectionLoadResult | None:
        """Check event-loop evidence while both persistence locks are held."""
        exclusion_result = _locked_exclusion_preflight(
            current_exclusion_snapshot,
            staged.exclusion_generation,
            exclusions,
        )
        if exclusion_result is not None:
            return exclusion_result
        if hooks.event_loop_preflight is None:
            return None
        try:
            return hooks.event_loop_preflight()
        except Exception:
            return _unavailable("snapshot_precondition_failed")

    return await protected_store.async_commit(
        staged.envelope,
        precondition=precondition,
        on_locked_preflight=locked_preflight,
        authorize_owner_transition=authorize_staged_owner_transition,
        on_acknowledged=publish,
    )


def _validated_exclusion_generation(
    exclusions: list[dict[str, Any]],
    provider: Callable[[], tuple[int, list[dict[str, Any]]]],
) -> tuple[int | None, ProtectionLoadResult | None]:
    """Validate the supplied complete exclusion snapshot and capture its epoch."""
    if not valid_exclusions_section(exclusions):
        return None, _unavailable("snapshot_invalid")
    try:
        generation, current_exclusions = provider()
    except Exception:
        return None, _unavailable("snapshot_exclusions_changed")
    if (
        isinstance(generation, bool)
        or not isinstance(generation, int)
        or generation < 0
        or current_exclusions != exclusions
    ):
        return None, _unavailable("snapshot_exclusions_changed")
    return generation, None


def _ledger_matches_registry(persisted: object, serialized: dict[str, Any]) -> bool:
    """Compare ledger contents while excluding its per-serialization timestamp."""
    if not isinstance(persisted, dict) or set(persisted) != set(serialized):
        return False
    return {key: value for key, value in persisted.items() if key != "updated_at"} == {
        key: value for key, value in serialized.items() if key != "updated_at"
    }


def _exclusion_snapshot_is_current(
    provider: Callable[[], tuple[int, list[dict[str, Any]]]],
    generation: int,
    exclusions: list[dict[str, Any]],
) -> bool:
    """Compare the event-loop snapshot source without touching HA in a worker."""
    try:
        current_generation, current_exclusions = provider()
    except Exception:
        return False
    return (
        not isinstance(current_generation, bool)
        and isinstance(current_generation, int)
        and current_generation == generation
        and current_exclusions == exclusions
    )


def _locked_exclusion_preflight(
    provider: Callable[[], tuple[int, list[dict[str, Any]]]],
    generation: int,
    exclusions: list[dict[str, Any]],
) -> ProtectionLoadResult | None:
    """Recheck HA exclusions on the event loop under both storage locks."""
    if _exclusion_snapshot_is_current(provider, generation, exclusions):
        return None
    return _unavailable("snapshot_exclusions_changed")
