# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Protective-unknown gates preserve evidence without authorizing mutations."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from typing import cast

import pytest

from custom_components.rental_control.allocator import protection_gate
from custom_components.rental_control.allocator.allocator import DoorCodeAllocator
from custom_components.rental_control.allocator.models import AllocationOrigin
from custom_components.rental_control.allocator.models import AllocationOwner
from custom_components.rental_control.allocator.models import AllocationRecord
from custom_components.rental_control.allocator.models import AllocationRequest
from custom_components.rental_control.allocator.models import CycleObservation
from custom_components.rental_control.allocator.models import CycleRequest
from custom_components.rental_control.allocator.models import ForcedReissueDirective
from custom_components.rental_control.allocator.models import ReissuePreviewRequest
from custom_components.rental_control.allocator.registry import AllocationRegistry
from custom_components.rental_control.allocator.store import code_ref_for
from custom_components.rental_control.const import DOMAIN
from custom_components.rental_control.const import PROTECTED_REGISTRY_STORE


def _allocator(*, required: bool = True):
    """Create the smallest allocator state needed by gate projections."""
    registry = AllocationRegistry(code_ref_salt="gate-reference-salt")
    owner = AllocationOwner(
        entry_id="rc-1",
        identity_key="reservation-1",
        origin=AllocationOrigin.PREFERRED,
        lockname="front",
        slot=10,
        lock_observed=True,
        first_seen="2026-11-21T00:00:00+00:00",
        last_seen="2026-11-21T00:00:00+00:00",
    )
    registry.records["4729"] = AllocationRecord(
        code="4729",
        code_ref=code_ref_for("4729", registry.code_ref_salt),
        encoding_salt_value="rc-1",
        code_length=4,
        owners=[owner],
        created_at="2026-11-21T00:00:00+00:00",
        updated_at="2026-11-21T00:00:00+00:00",
    )
    registry.rebuild_index()
    return SimpleNamespace(
        hass=SimpleNamespace(data={}),
        _protection_required=required,
        _registry=registry,
        _lock=asyncio.Lock(),
        _store=SimpleNamespace(async_save=lambda _registry: None),
        _pending_adoption=set(),
        _adopted_locks={},
        _read_pending_locks={},
        _pending_adoption_slots={},
        _gate_deadline=0.0,
        _gate_warning_sent=False,
        _registry_lost=False,
        _registry_missing=False,
        _reported_identity_mismatches=set(),
        _conflict_verdicts={},
    )


class _GateStore:
    """Expose readiness outcomes for operation-gate behavior tests."""

    def __init__(self, *, ready: bool = True, wait_result: bool = True) -> None:
        """Set the independent readiness and commit-wait results."""
        self.ready = ready
        self.wait_result = wait_result

    def operation_snapshot_ready(self) -> bool:
        """Return the controlled current-snapshot readiness result."""
        return self.ready

    async def async_wait_for_commit_completion(self) -> bool:
        """Return the controlled in-flight commit completion result."""
        return self.wait_result


def test_missing_or_malformed_protected_state_fails_closed_only_when_required() -> None:
    """Legacy state remains available; required but absent/malformed state blocks."""
    allocator = _allocator(required=False)
    assert protection_gate.snapshot_ready(allocator)

    allocator._protection_required = True
    assert not protection_gate.snapshot_ready(allocator)
    assert not asyncio.run(protection_gate.async_operation_ready(allocator))

    allocator.hass.data[DOMAIN] = []
    assert not protection_gate.snapshot_ready(allocator)

    allocator.hass.data[DOMAIN] = {}
    allocator._protection_required = False
    assert protection_gate.snapshot_ready(allocator)


@pytest.mark.asyncio
async def test_allocator_public_mutation_apis_validate_request_types(hass) -> None:
    """Malformed callers cannot reach allocator mutation or persistence code."""
    allocator = DoorCodeAllocator(hass)
    with pytest.raises(TypeError, match="CycleRequest"):
        await allocator.async_resolve_cycle(cast(Any, object()))
    with pytest.raises(TypeError, match="ReissuePreviewRequest"):
        await allocator.async_preview_reissue(cast(Any, object()))
    with pytest.raises(TypeError, match="AdoptionRequest"):
        await allocator.async_adopt(cast(Any, object()))
    with pytest.raises(TypeError, match="AllocationRequest"):
        await allocator.async_allocate(cast(Any, object()))


@pytest.mark.asyncio
async def test_protected_wait_and_snapshot_readiness_are_both_required() -> None:
    """An incomplete waiter, stale snapshot, or raising API denies operations."""
    allocator = _allocator()
    store = _GateStore(ready=True, wait_result=True)
    allocator.hass.data[DOMAIN] = {PROTECTED_REGISTRY_STORE: store}
    assert await protection_gate.async_operation_ready(allocator)

    store.ready = False
    assert not protection_gate.snapshot_ready(allocator)
    assert not await protection_gate.async_operation_ready(allocator)

    store.ready = True
    store.wait_result = False
    assert not await protection_gate.async_operation_ready(allocator)

    class RaisingStore:
        """A broken readiness API is treated as unknown, never as ready."""

        def operation_snapshot_ready(self) -> bool:
            """Raise to model unavailable protection state."""
            raise RuntimeError("untrusted status")

        async def async_wait_for_commit_completion(self) -> bool:
            """Raise to model a failing protected-store waiter."""
            raise RuntimeError("untrusted waiter")

    allocator.hass.data[DOMAIN][PROTECTED_REGISTRY_STORE] = RaisingStore()
    assert not protection_gate.snapshot_ready(allocator)
    assert not await protection_gate.async_operation_ready(allocator)


@pytest.mark.asyncio
async def test_unknown_gate_blocks_reissue_and_reports_retained_owner() -> None:
    """Read-only cleanup reports preserve the owner and do not call legacy save."""
    allocator = _allocator()
    preview = await protection_gate.async_preview_reissue(
        allocator,
        ReissuePreviewRequest("rc-1", "reservation-1", "9384", 4, "front", 10, "4729"),
    )
    assert preview.reason == "protective_unknown"
    assert not await protection_gate.async_rekey(
        allocator, "reservation-1", "reservation-2"
    )

    report = await protection_gate.async_cleanup_report(allocator, dry_run=True)
    assert report.dry_run
    assert not report.cleared
    assert [owner.identity_key for owner in report.retained] == ["reservation-1"]
    assert allocator._registry.record_for_identity("reservation-1").code == "4729"


@pytest.mark.asyncio
async def test_preview_rechecks_snapshot_after_waiting_for_allocator_lock() -> None:
    """A snapshot becoming unknown during lock contention denies the preview."""
    allocator = _allocator()
    store = _GateStore()
    allocator.hass.data[DOMAIN] = {PROTECTED_REGISTRY_STORE: store}

    class ReadinessChangingLock:
        """Model an unrelated commit failing while this request waits."""

        async def __aenter__(self) -> None:
            """Make the protected snapshot unknown before lock acquisition ends."""
            store.ready = False

        async def __aexit__(self, *_args: object) -> None:
            """Complete the simulated lock context without further changes."""
            return None

    allocator._lock = ReadinessChangingLock()
    result = await protection_gate.async_preview_reissue(
        allocator,
        ReissuePreviewRequest("rc-1", "reservation-1", "9384", 4, "front", 10, "4729"),
    )

    assert result.reason == "protective_unknown"


@pytest.mark.asyncio
async def test_unprotected_rekey_uses_allocator_lock_and_persists(hass) -> None:
    """Legacy-only state can rekey an existing allocation without a fake store."""
    allocator = DoorCodeAllocator(hass)
    allocator._registry = _allocator(required=False)._registry

    assert await allocator.async_rekey("reservation-1", "reservation-renamed")
    assert allocator._registry.record_for_identity("reservation-1") is None
    renamed = allocator._registry.record_for_identity("reservation-renamed")
    assert renamed is not None
    assert renamed.code == "4729"
    assert not await allocator.async_rekey("reservation-renamed", "reservation-renamed")


def test_unknown_cycle_retains_allocations_and_describes_forced_reissue() -> None:
    """Unknown resolution returns no code or release and tags reissue as held."""
    request = CycleRequest(
        observation=CycleObservation(
            "rc-1",
            "front",
            frozenset({10}),
            {},
            frozenset(),
        ),
        adoptions=[],
        rekeys=[],
        allocations=[
            AllocationRequest(
                entry_id="rc-1",
                identity_key="reservation-2",
                preferred_code="9384",
                code_length=4,
                lockname="front",
                slot=11,
            )
        ],
        active_keys={"reservation-2"},
        forced_reissues=(ForcedReissueDirective("rc-1", "reservation-1", "front", 10),),
    )

    result = protection_gate.protective_unknown_cycle_result(request)

    assert result.allocated["reservation-2"].code is None
    assert result.allocated["reservation-2"].reason == "protective_unknown"
    assert not result.released.released
    assert not result.released.retained
    assert len(result.reissues) == 1
    assert result.reissues[0].retention_reason == "protective_unknown"
