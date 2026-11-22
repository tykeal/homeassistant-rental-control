# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Revalidate staged owner transitions before protected publication."""

from __future__ import annotations

import copy
import logging
from types import SimpleNamespace
from typing import Any
from typing import cast

import pytest

from custom_components.rental_control.allocator.allocator import DoorCodeAllocator
from custom_components.rental_control.allocator.allocator_protocol import AllocatorState
from custom_components.rental_control.allocator.models import AllocationOrigin
from custom_components.rental_control.allocator.models import AllocationOwner
from custom_components.rental_control.allocator.models import AllocationRecord
from custom_components.rental_control.allocator.models import ForcedReleaseExemption
from custom_components.rental_control.allocator.models import ProtectionLoadResult
from custom_components.rental_control.allocator.models import ProtectionLoadState
from custom_components.rental_control.allocator.registry import AllocationRegistry
from custom_components.rental_control.allocator.registry_mutation import (
    _publish_after_commit_actions,
)
from custom_components.rental_control.allocator.registry_mutation import after_commit
from custom_components.rental_control.allocator.registry_mutation import (
    async_commit_staged_registry,
)
from custom_components.rental_control.allocator.registry_mutation import clone_allocator
from custom_components.rental_control.allocator.registry_mutation import (
    owner_transition_authorizer,
)
from custom_components.rental_control.allocator.registry_mutation import (
    registry_changed,
)
from custom_components.rental_control.allocator.store import code_ref_for
from custom_components.rental_control.const import ALLOCATOR
from custom_components.rental_control.const import DOMAIN
from custom_components.rental_control.const import PROTECTED_REGISTRY_STORE

_CODE = "4729"


def _owner(
    identity: str = "reservation-old",
    *,
    entry_id: str = "rc-1",
    lockname: str = "front",
    slot: int = 10,
) -> AllocationOwner:
    """Create one persisted physical owner."""
    return AllocationOwner(
        entry_id=entry_id,
        identity_key=identity,
        origin=AllocationOrigin.PREFERRED,
        lockname=lockname,
        slot=slot,
        lock_observed=True,
        first_seen="2026-11-21T00:00:00+00:00",
        last_seen="2026-11-21T00:00:00+00:00",
    )


def _registry(owner: AllocationOwner) -> AllocationRegistry:
    """Create one ledger record with a rebuilt identity index."""
    registry = AllocationRegistry(code_ref_salt="mutation-reference-salt")
    registry.records[_CODE] = AllocationRecord(
        code=_CODE,
        code_ref=code_ref_for(_CODE, registry.code_ref_salt),
        encoding_salt_value=owner.entry_id,
        code_length=4,
        owners=[owner],
        created_at="2026-11-21T00:00:00+00:00",
        updated_at="2026-11-21T00:00:00+00:00",
    )
    registry.rebuild_index()
    return registry


class _ReleaseGuard:
    """Capture release-guard evidence while returning a selected verdict."""

    def __init__(self, reason: str | None = None) -> None:
        """Initialize the verdict and record for guard invocations."""
        self.reason = reason
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def _release_guard_reason(self, *args, **kwargs) -> str | None:
        """Record a guard invocation and return the configured verdict."""
        self.calls.append((args, kwargs))
        return self.reason


def test_transition_authorizer_skips_same_location_rekey_and_hold_replacement() -> None:
    """Identity reshapes do not release an owner when location is unchanged."""
    guard = _ReleaseGuard("must-not-run")
    allocator = cast(AllocatorState, guard)
    old = _owner()
    before = _registry(old)

    same = _registry(old)
    assert owner_transition_authorizer(allocator, [])(before, same)

    renamed = _registry(_owner("reservation-new"))
    assert owner_transition_authorizer(
        allocator, [], rekeys=(("reservation-old", "reservation-new"),)
    )(before, renamed)

    held = _registry(_owner("reservation-old:reissued:rc-1:front:10"))
    assert owner_transition_authorizer(allocator, [])(before, held)
    assert guard.calls == []


def test_transition_authorizer_rechecks_every_removed_owner_and_forced_exemption() -> (
    None
):
    """Actual release depends on fresh guard approval for each prior owner."""
    owner = _owner()
    before = _registry(owner)
    empty = AllocationRegistry(code_ref_salt=before.code_ref_salt)
    allow = _ReleaseGuard()
    assert owner_transition_authorizer(cast(AllocatorState, allow), [])(before, empty)
    assert len(allow.calls) == 1
    assert allow.calls[0][0][0] == before.records[_CODE]
    assert allow.calls[0][0][1] == [owner]
    assert allow.calls[0][1]["forced_release"] is None
    assert allow.calls[0][1]["releasing"] == {"reservation-old"}

    deny = _ReleaseGuard("physical_slot_unverified")
    assert not owner_transition_authorizer(cast(AllocatorState, deny), [])(
        before, empty
    )
    assert len(deny.calls) == 1

    forced = _owner("reservation-old:reissued:rc-1:front:10")
    forced_before = _registry(forced)
    forced_after = AllocationRegistry(code_ref_salt=forced_before.code_ref_salt)
    forced_guard = _ReleaseGuard()
    assert owner_transition_authorizer(cast(AllocatorState, forced_guard), [])(
        forced_before, forced_after
    )
    exemption = forced_guard.calls[0][1]["forced_release"]
    assert isinstance(exemption, ForcedReleaseExemption)
    assert exemption.identity_key == forced.identity_key


def test_transition_authorizer_refuses_missing_record_or_moved_replacement() -> None:
    """A missing prior record or changed physical location cannot be authorized."""
    owner = _owner()
    before = _registry(owner)
    missing_record = _registry(owner)
    missing_record.records = {"wrong-index": missing_record.records[_CODE]}
    after = AllocationRegistry(code_ref_salt=before.code_ref_salt)
    assert not owner_transition_authorizer(cast(AllocatorState, _ReleaseGuard()), [])(
        missing_record, after
    )

    moved = _registry(_owner("reservation-old", slot=11))
    guard = _ReleaseGuard("slot_changed")
    assert not owner_transition_authorizer(cast(AllocatorState, guard), [])(
        before, moved
    )
    assert len(guard.calls) == 1


def test_staged_clone_and_persisted_timestamp_comparison() -> None:
    """Cloning isolates state; changing a persisted owner timestamp is material."""
    live_registry = _registry(_owner())
    live = SimpleNamespace(
        _registry=live_registry,
        _registry_lost=False,
        _registry_missing=False,
        _pending_adoption={"rc-1"},
        _adopted_locks={},
        _read_pending_locks={},
        _pending_adoption_slots={},
        _gate_deadline=1.0,
        _gate_warning_sent=False,
        _reported_identity_mismatches=set(),
        _conflict_verdicts={},
    )
    staged = clone_allocator(live)
    assert staged is not live
    assert staged._registry is not live._registry
    assert staged._pending_adoption == {"rc-1"}
    staged._pending_adoption.clear()
    assert live._pending_adoption == {"rc-1"}

    timestamp_only = copy.deepcopy(live_registry)
    assert not registry_changed(live_registry, timestamp_only)
    timestamp_only.records[_CODE].updated_at = "2026-11-22T00:00:00+00:00"
    assert registry_changed(live_registry, timestamp_only)
    timestamp_only.records[_CODE].owners[0].slot = 11
    assert registry_changed(live_registry, timestamp_only)


def test_after_commit_runs_immediately_without_a_staged_queue() -> None:
    """Standalone callers retain their immediate callback behavior."""
    called: list[str] = []
    after_commit(cast(AllocatorState, SimpleNamespace()), lambda: called.append("done"))
    assert called == ["done"]


def test_post_commit_notice_failure_is_sanitized_and_does_not_skip_later_actions(
    caplog,
) -> None:
    """One failed HA notice cannot prevent later acknowledged notices."""
    actions: list[str] = []

    def broken_notice() -> None:
        """Raise with synthetic sensitive data to test sanitized logging."""
        raise RuntimeError("PIN 1234 guest Alice")

    staged = SimpleNamespace(
        _after_commit_actions=[broken_notice, lambda: actions.append("published")]
    )
    with caplog.at_level(logging.WARNING):
        _publish_after_commit_actions(cast(AllocatorState, staged))

    assert actions == ["published"]
    assert staged._after_commit_actions == []
    assert "Deferred post-commit notice failed to publish" in caplog.text
    assert "1234" not in caplog.text
    assert "Alice" not in caplog.text


async def test_protected_stage_fails_closed_when_store_is_not_registered(hass) -> None:
    """A missing store cannot publish staged registry or allocator state."""
    allocator = DoorCodeAllocator(hass)
    allocator._protection_required = True
    hass.data[DOMAIN] = {ALLOCATOR: allocator}
    live_registry = allocator._registry
    staged = clone_allocator(cast(AllocatorState, allocator))
    staged._pending_adoption.add("staged-only")

    result = await async_commit_staged_registry(
        cast(AllocatorState, allocator), live_registry, staged
    )

    assert result.failure_code == "operation_failed"
    assert allocator._registry is live_registry
    assert allocator._pending_adoption == set()


@pytest.mark.parametrize(
    ("loaded_result", "expected_state", "callback_expected"),
    (
        (
            ProtectionLoadResult(ProtectionLoadState.VALID_V2),
            ProtectionLoadState.VALID_V2,
            True,
        ),
        (None, ProtectionLoadState.UNAVAILABLE, False),
    ),
)
async def test_noop_stage_publishes_only_against_current_registry(
    hass, loaded_result, expected_state: ProtectionLoadState, callback_expected: bool
) -> None:
    """No-op projection requires unchanged registry and a ready loaded snapshot."""
    allocator = DoorCodeAllocator(hass)
    allocator._protection_required = True
    allocator._registry = _registry(_owner())
    store = SimpleNamespace(loaded_result=loaded_result)
    hass.data[DOMAIN] = {
        ALLOCATOR: allocator,
        PROTECTED_REGISTRY_STORE: store,
    }
    baseline = allocator._registry
    staged = clone_allocator(cast(AllocatorState, allocator))
    notices: list[str] = []
    after_commit(cast(AllocatorState, staged), lambda: notices.append("acknowledged"))

    result = await async_commit_staged_registry(
        cast(AllocatorState, allocator), baseline, staged
    )

    assert result.state is expected_state
    assert notices == (["acknowledged"] if callback_expected else [])


async def test_noop_stage_rejects_stale_baseline_without_projection(hass) -> None:
    """A concurrent registry update cannot be overwritten by no-op projection."""
    allocator = DoorCodeAllocator(hass)
    allocator._protection_required = True
    allocator._registry = _registry(_owner())
    hass.data[DOMAIN] = {
        ALLOCATOR: allocator,
        PROTECTED_REGISTRY_STORE: SimpleNamespace(
            loaded_result=ProtectionLoadResult(ProtectionLoadState.VALID_V2)
        ),
    }
    baseline = copy.deepcopy(allocator._registry)
    staged = clone_allocator(cast(AllocatorState, allocator))
    allocator._registry.code_ref_salt = "concurrent-update"

    result = await async_commit_staged_registry(
        cast(AllocatorState, allocator), baseline, staged
    )

    assert result.failure_code == "registry_changed"
    assert allocator._registry.code_ref_salt == "concurrent-update"
