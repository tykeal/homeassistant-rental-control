# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Stage allocator mutations and publish only acknowledged snapshots."""

from __future__ import annotations

from collections.abc import Callable
import copy
import hashlib
import json
import logging
from typing import Any
from typing import TypeVar
from typing import cast

from ..const import DOMAIN
from ..const import PROTECTED_REGISTRY_STORE
from ..keymaster_lifecycle_registry import get_keymaster_lifecycle
from . import reissue
from .allocator_protocol import AllocatorState
from .models import AllocationOwner
from .models import CycleObservation
from .models import ForcedReleaseExemption
from .models import ProtectionLoadResult
from .models import ProtectionLoadState
from .registry import AllocationRegistry
from .registry_snapshot import SnapshotCommitHooks
from .registry_snapshot import async_commit_registry_snapshot
from .registry_snapshot import serialize_exclusion_snapshots
from .store import serialize_registry_ledger

_PROJECTED_FIELDS = (
    "_registry_lost",
    "_registry_missing",
    "_pending_adoption",
    "_adopted_locks",
    "_read_pending_locks",
    "_pending_adoption_slots",
    "_gate_deadline",
    "_gate_warning_sent",
    "_reported_identity_mismatches",
    "_conflict_verdicts",
)
_LOGGER = logging.getLogger(__name__)
_AllocatorT = TypeVar("_AllocatorT", bound=AllocatorState)


def clone_allocator(allocator: _AllocatorT) -> _AllocatorT:
    """Clone mutable allocator state for one lock-protected calculation."""
    staged: Any = copy.copy(allocator)
    staged._registry = copy.deepcopy(allocator._registry)
    for field in _PROJECTED_FIELDS:
        setattr(staged, field, copy.deepcopy(getattr(allocator, field)))
    staged._after_commit_actions = []
    return cast(_AllocatorT, staged)


def after_commit(allocator: AllocatorState, action: Callable[[], None]) -> None:
    """Run a user-visible side effect only after its staged state is durable."""
    actions = getattr(allocator, "_after_commit_actions", None)
    if isinstance(actions, list):
        actions.append(action)
    else:
        action()


def _publish_after_commit_actions(allocator: AllocatorState) -> None:
    """Publish queued notices after an acknowledged registry transaction."""
    actions = getattr(allocator, "_after_commit_actions", None)
    if not isinstance(actions, list):
        return
    for action in actions:
        try:
            action()
        except Exception:
            _LOGGER.warning("Deferred post-commit notice failed to publish")
    actions.clear()


def registry_changed(before: AllocationRegistry, after: AllocationRegistry) -> bool:
    """Compare persisted ledger fields without their refreshed timestamp."""
    first = serialize_registry_ledger(before)
    second = serialize_registry_ledger(after)
    first.pop("updated_at", None)
    second.pop("updated_at", None)
    return first != second


def owner_transition_authorizer(
    allocator: AllocatorState,
    observations: list[CycleObservation],
    *,
    rekeys: tuple[tuple[str, str], ...] = (),
    forced_release_identities: frozenset[str] = frozenset(),
) -> Callable[[AllocationRegistry, AllocationRegistry], bool]:
    """Re-run the release guard for every protected owner transition."""
    rekey_map = dict(rekeys)

    def authorize(before: AllocationRegistry, after: AllocationRegistry) -> bool:
        """Approve only staged owner removals that pass release guards."""
        old_owners = _owners_by_identity(before)
        new_owners = _owners_by_identity(after)
        removed: list[tuple[str, str, AllocationOwner]] = []
        for identity_key, (code, owner) in old_owners.items():
            new = new_owners.get(identity_key)
            if new is not None and _same_owner_location(code, owner, *new):
                continue
            rekey = rekey_map.get(identity_key)
            if (
                rekey is not None
                and (replacement := new_owners.get(rekey)) is not None
                and _same_owner_location(code, owner, *replacement)
            ):
                continue
            hold_replacement = next(
                (
                    (new_code, new_owner)
                    for new_key, (new_code, new_owner) in new_owners.items()
                    if reissue.is_forced_release_hold(new_key)
                    and _same_owner_location(code, owner, new_code, new_owner)
                ),
                None,
            )
            if hold_replacement is not None:
                continue
            removed.append((code, identity_key, owner))

        releasing = {identity_key for _, identity_key, _ in removed}
        for code, identity_key, owner in removed:
            record = before.records.get(code)
            if record is None:
                return False
            exemption = (
                ForcedReleaseExemption(code, owner.entry_id, identity_key)
                if (
                    reissue.is_forced_release_hold(identity_key)
                    or identity_key in forced_release_identities
                )
                else None
            )
            if (
                allocator._release_guard_reason(
                    record,
                    [owner],
                    observations,
                    forced_release=exemption,
                    releasing=releasing,
                )
                is not None
            ):
                return False
        return True

    return authorize


def observation_preflight(
    allocator: AllocatorState,
    observations: list[CycleObservation],
) -> ProtectionLoadResult | None:
    """Reject locked observations that lost their selected lifecycle epoch."""
    from ..coordinator_helpers.cycle_requests import verified_observation_identity

    for observation in observations:
        if observation.lockname is None:
            continue
        current = verified_observation_identity(allocator.hass, observation.entry_id)
        if (
            current is None
            or observation.keymaster_entry_id != current[0]
            or observation.keymaster_generation != current[1]
        ):
            return ProtectionLoadResult(
                ProtectionLoadState.UNAVAILABLE,
                failure_code="observation_not_current",
            )
        lifecycle = get_keymaster_lifecycle(allocator.hass, observation.entry_id)
        if lifecycle is None or not lifecycle.current:
            return ProtectionLoadResult(
                ProtectionLoadState.UNAVAILABLE,
                failure_code="observation_not_current",
            )
    return None


async def async_commit_staged_registry(
    allocator: AllocatorState,
    baseline: AllocationRegistry,
    staged: AllocatorState,
    *,
    authorize_owner_transition: (
        Callable[[AllocationRegistry, AllocationRegistry], bool] | None
    ) = None,
    event_loop_preflight: Callable[[], ProtectionLoadResult | None] | None = None,
    operation_lock_held: bool = False,
) -> ProtectionLoadResult:
    """Persist one staged ledger and publish its coupled in-memory state."""
    mutation_lock = getattr(allocator, "_mutation_lock", None)
    if mutation_lock is not None and not operation_lock_held:
        async with mutation_lock:
            return await async_commit_staged_registry(
                allocator,
                baseline,
                staged,
                authorize_owner_transition=authorize_owner_transition,
                event_loop_preflight=event_loop_preflight,
                operation_lock_held=True,
            )

    domain_data = allocator.hass.data.get(DOMAIN)
    store = (
        domain_data.get(PROTECTED_REGISTRY_STORE)
        if isinstance(domain_data, dict)
        else None
    )
    if store is None:
        return ProtectionLoadResult(
            ProtectionLoadState.UNAVAILABLE,
            failure_code="protected_store_missing",
        )

    if not registry_changed(baseline, staged._registry):
        async with allocator._lock:
            if allocator._registry != baseline:
                return ProtectionLoadResult(
                    ProtectionLoadState.UNAVAILABLE,
                    failure_code="registry_changed",
                )
            _publish_staged_state(allocator, staged)
            loaded = getattr(store, "loaded_result", None)
            result = (
                loaded
                if isinstance(loaded, ProtectionLoadResult)
                else (
                    ProtectionLoadResult(
                        ProtectionLoadState.UNAVAILABLE,
                        failure_code="snapshot_unavailable",
                    )
                )
            )
        if result.state is ProtectionLoadState.VALID_V2:
            _publish_after_commit_actions(staged)
        return result

    exclusion_generation, exclusions = _current_exclusion_snapshot(allocator.hass)
    committed_registry: AllocationRegistry | None = None

    def replace_staged_registry(candidate: AllocationRegistry) -> None:
        """Replace only the exact registry from which this operation staged."""
        nonlocal committed_registry
        if candidate != baseline:
            raise ValueError("registry_changed")
        candidate.records = copy.deepcopy(staged._registry.records)
        candidate.code_ref_salt = staged._registry.code_ref_salt
        candidate.announced_conflicts = copy.deepcopy(
            staged._registry.announced_conflicts
        )
        candidate.by_identity = copy.deepcopy(staged._registry.by_identity)
        committed_registry = candidate

    def publish_state() -> None:
        """Project non-ledger state under the protected commit locks."""
        if committed_registry is None:
            raise ValueError("snapshot_projection_missing")
        staged._registry = committed_registry
        _publish_staged_state(allocator, staged)

    result = await async_commit_registry_snapshot(
        allocator,
        store,
        replace_staged_registry,
        exclusions,
        lambda: _current_exclusion_snapshot(allocator.hass),
        hooks=SnapshotCommitHooks(
            authorize_owner_transition=authorize_owner_transition,
            event_loop_preflight=event_loop_preflight,
            publish_state=publish_state,
        ),
        operation_lock_held=True,
    )
    if result.state is ProtectionLoadState.VALID_V2:
        _publish_after_commit_actions(staged)
    return result


def _publish_staged_state(allocator: AllocatorState, staged: AllocatorState) -> None:
    """Copy the staged registry and allocator gates into the live instance."""
    allocator._registry = staged._registry
    for field in _PROJECTED_FIELDS:
        setattr(allocator, field, copy.deepcopy(getattr(staged, field)))


def _current_exclusion_snapshot(hass: Any) -> tuple[int, list[dict[str, Any]]]:
    """Capture all current entry exclusions with a stable content generation."""
    exclusions = serialize_exclusion_snapshots(
        hass.config_entries.async_entries(DOMAIN)
    )
    encoded = json.dumps(
        exclusions,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    generation = int.from_bytes(hashlib.sha256(encoded).digest()[:8], "big")
    return generation, exclusions


def _owners_by_identity(
    registry: AllocationRegistry,
) -> dict[str, tuple[str, AllocationOwner]]:
    """Index persisted owners by their globally unique reservation identity."""
    return {
        owner.identity_key: (record.code, owner)
        for record in registry.records.values()
        for owner in record.owners
    }


def _same_owner_location(
    first_code: str,
    first: AllocationOwner,
    second_code: str,
    second: AllocationOwner,
) -> bool:
    """Compare code and physical location while ignoring observation timestamps."""
    return (
        first_code == second_code
        and first.entry_id == second.entry_id
        and first.lockname == second.lockname
        and first.slot == second.slot
    )


__all__ = [
    "async_commit_staged_registry",
    "after_commit",
    "clone_allocator",
    "observation_preflight",
    "owner_transition_authorizer",
    "registry_changed",
]
