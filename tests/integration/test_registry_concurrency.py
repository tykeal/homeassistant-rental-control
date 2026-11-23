# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Storage-only fake-consumer ordering tests for protected registry commits."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import threading
from typing import Any
from unittest.mock import Mock

import pytest

from custom_components.rental_control.allocator import registry_commit
from custom_components.rental_control.allocator import registry_locks
from custom_components.rental_control.allocator import registry_marker_io
from custom_components.rental_control.allocator import registry_transactions
from custom_components.rental_control.allocator.models import AtomicWriteResult
from custom_components.rental_control.allocator.models import (
    ProtectionAuthorizationState,
)
from custom_components.rental_control.allocator.models import ProtectionCommitToken
from custom_components.rental_control.allocator.models import ProtectionLoadResult
from custom_components.rental_control.allocator.models import ProtectionLoadState
from custom_components.rental_control.allocator.models import RecoveryAction
from custom_components.rental_control.allocator.protected_store import (
    ProtectedRegistryStore,
)
from custom_components.rental_control.allocator.registry_safety_models import (
    PendingSlotFence,
)
from custom_components.rental_control.allocator.registry_safety_models import (
    resolve_fence_to_tombstone,
)


def _envelope(
    transaction_id: str,
    generation: int,
    fences: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build one complete exact v2 state for fake consumers."""
    return {
        "version": 2,
        "minor_version": 1,
        "key": "rental_control.code_registry",
        "data": {
            "schema_version": 1,
            "transaction_id": transaction_id,
            "generation": generation,
            "ledger": {
                "schema_version": 1,
                "updated_at": "2026-11-21T00:00:00+00:00",
                "code_ref_salt": "test-salt",
                "records": [],
                "announced_conflicts": {},
            },
            "associations": [],
            "attestations": [],
            "pending_fences": fences,
            "holds": [],
            "tombstones": [],
            "pending_config": [],
        },
    }


def _fence(
    *,
    fence_id: str,
    km_entry: str,
    slot: int,
    rc_entry: str,
    owner_ref: str,
    allocation_ref: str,
    transaction_id: str,
    generation: int,
    fence_type: str = "allocation",
) -> dict[str, Any]:
    """Build a strict original-slot fence without inferring identity."""
    return {
        "fence_id": fence_id,
        "original_km_entry_id": km_entry,
        "slot": slot,
        "rc_entry_id": rc_entry,
        "owner_ref": owner_ref,
        "allocation_ref": allocation_ref,
        "created_transaction_id": transaction_id,
        "created_generation": generation,
        "fence_type": fence_type,
    }


class FakeProtectiveConsumer:
    """Model the storage-before-dispatch seam without a real lock provider."""

    def __init__(
        self,
        store: ProtectedRegistryStore,
        dispatches: list[str],
    ) -> None:
        """Bind one fake consumer to the shared protected store."""
        self._store = store
        self._dispatches = dispatches

    async def async_commit_then_dispatch(
        self,
        envelope: dict[str, Any],
        label: str,
        *,
        local_lock: asyncio.Lock | None = None,
    ) -> ProtectionLoadResult:
        """Revalidate the acknowledged transaction after reacquiring local state."""
        result = await self._store.async_commit(envelope)
        if result.state is not ProtectionLoadState.VALID_V2:
            return result
        token = ProtectionCommitToken(
            transaction_id=result.transaction_id or "",
            generation=result.generation or 0,
        )
        if local_lock is None:
            if self._store.is_current_commit_token(token):
                self._dispatches.append(label)
            return result
        async with local_lock:
            if self._store.is_current_commit_token(token):
                self._dispatches.append(label)
        return result


@pytest.mark.asyncio
async def test_two_fake_entries_preserve_prior_fence_and_reject_stale_snapshot(
    hass,
    tmp_path: Path,
) -> None:
    """A later entry snapshot carries prior protection; stale state cannot dispatch."""
    storage = tmp_path / ".storage"
    storage.mkdir(parents=True, exist_ok=True)
    shared_lock = asyncio.Lock()
    store = ProtectedRegistryStore(hass, shared_lock, storage_dir=storage)
    assert (await store.async_load()).state is ProtectionLoadState.ABSENT
    dispatches: list[str] = []
    entry_a = FakeProtectiveConsumer(store, dispatches)
    entry_b = FakeProtectiveConsumer(store, dispatches)
    fence_a = _fence(
        fence_id="f" * 32,
        km_entry="km-a",
        slot=4,
        rc_entry="entry-a",
        owner_ref="owner-a",
        allocation_ref="allocation-a",
        transaction_id="1" * 32,
        generation=1,
        fence_type="pending_clear",
    )
    fence_b = _fence(
        fence_id="e" * 32,
        km_entry="km-b",
        slot=9,
        rc_entry="entry-b",
        owner_ref="owner-b",
        allocation_ref="allocation-b",
        transaction_id="2" * 32,
        generation=2,
    )

    first = await entry_a.async_commit_then_dispatch(
        _envelope("1" * 32, 1, [fence_a]), "entry-a"
    )
    second = await entry_b.async_commit_then_dispatch(
        _envelope("2" * 32, 2, [fence_a, fence_b]), "entry-b"
    )
    stale = await entry_a.async_commit_then_dispatch(
        _envelope("3" * 32, 2, [fence_a]), "stale-entry-a"
    )

    assert first.state is ProtectionLoadState.VALID_V2
    assert second.state is ProtectionLoadState.VALID_V2
    assert stale.state is ProtectionLoadState.PROTECTIVE_UNKNOWN
    assert stale.failure_code == "snapshot_transition_invalid"
    assert dispatches == ["entry-a", "entry-b"]
    committed = json.loads((storage / "rental_control.code_registry").read_bytes())
    assert committed["data"]["pending_fences"] == [fence_a, fence_b]


@pytest.mark.asyncio
async def test_local_lock_reacquisition_rejects_stale_commit_token(
    hass,
    tmp_path: Path,
) -> None:
    """A later owner commit invalidates an earlier token before dispatch."""
    storage = tmp_path / ".storage"
    storage.mkdir(parents=True, exist_ok=True)
    store = ProtectedRegistryStore(hass, asyncio.Lock(), storage_dir=storage)
    assert (await store.async_load()).state is ProtectionLoadState.ABSENT
    dispatches: list[str] = []
    entry_a = FakeProtectiveConsumer(store, dispatches)
    entry_b = FakeProtectiveConsumer(store, dispatches)
    local_lock = asyncio.Lock()
    await local_lock.acquire()

    fence_a = _fence(
        fence_id="f" * 32,
        km_entry="km-a",
        slot=4,
        rc_entry="rc-a",
        owner_ref="owner-a",
        allocation_ref="allocation-a",
        transaction_id="1" * 32,
        generation=1,
    )
    fence_b = _fence(
        fence_id="e" * 32,
        km_entry="km-b",
        slot=9,
        rc_entry="rc-b",
        owner_ref="owner-b",
        allocation_ref="allocation-b",
        transaction_id="2" * 32,
        generation=2,
    )
    first = asyncio.create_task(
        entry_a.async_commit_then_dispatch(
            _envelope("1" * 32, 1, [fence_a]),
            "stale-entry-a",
            local_lock=local_lock,
        )
    )
    for _ in range(100):
        if store.loaded_result is not None and store.loaded_result.generation == 1:
            break
        await asyncio.sleep(0.01)
    assert store.loaded_result is not None
    assert store.loaded_result.generation == 1

    second = asyncio.create_task(
        entry_b.async_commit_then_dispatch(
            _envelope("2" * 32, 2, [fence_a, fence_b]),
            "current-entry-b",
            local_lock=local_lock,
        )
    )
    for _ in range(100):
        if store.loaded_result is not None and store.loaded_result.generation == 2:
            break
        await asyncio.sleep(0.01)
    assert store.loaded_result is not None
    assert store.loaded_result.generation == 2
    assert not first.done()
    assert not second.done()

    local_lock.release()
    first_result, second_result = await asyncio.gather(first, second)

    assert first_result.state is ProtectionLoadState.VALID_V2
    assert second_result.state is ProtectionLoadState.VALID_V2
    assert dispatches == ["current-entry-b"]


@pytest.mark.asyncio
async def test_ambiguous_resolution_retains_tombstone_and_later_owner(
    hass,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recovery preserves a later owner after ambiguous tombstone replacement."""
    storage = tmp_path / ".storage"
    storage.mkdir(parents=True, exist_ok=True)
    store = ProtectedRegistryStore(hass, asyncio.Lock(), storage_dir=storage)
    assert (await store.async_load()).state is ProtectionLoadState.ABSENT
    dispatches: list[str] = []
    consumer = FakeProtectiveConsumer(store, dispatches)
    original_fence = PendingSlotFence(
        fence_id="f" * 32,
        original_km_entry_id="km-a",
        slot=4,
        rc_entry_id="rc-a",
        owner_ref="owner-a",
        allocation_ref="allocation-a",
        created_transaction_id="1" * 32,
        created_generation=1,
        fence_type="pending_clear",
    ).to_record()
    initial = await consumer.async_commit_then_dispatch(
        _envelope("1" * 32, 1, [original_fence]), "initial"
    )
    assert initial.state is ProtectionLoadState.VALID_V2

    tombstone = resolve_fence_to_tombstone(
        original_fence,
        tombstone_id="c" * 32,
        resolved_transaction_id="2" * 32,
        resolved_generation=2,
        resolution_kind="clear_confirmed",
        retained_at="2026-11-21T00:00:00+00:00",
    )
    later_owner = _fence(
        fence_id="e" * 32,
        km_entry="km-b",
        slot=9,
        rc_entry="rc-b",
        owner_ref="owner-b",
        allocation_ref="allocation-b",
        transaction_id="2" * 32,
        generation=2,
    )
    target = _envelope("2" * 32, 2, [later_owner])
    target["data"]["tombstones"] = [tombstone]
    original_write = registry_marker_io.atomic_write_bytes
    write_calls = 0

    def fail_after_final_marker_replace(
        path: Path,
        data: bytes,
        *,
        maximum: int,
    ) -> AtomicWriteResult:
        """Replace all bytes but withhold acknowledgement for final marker."""
        nonlocal write_calls
        write_calls += 1
        written = original_write(path, data, maximum=maximum)
        if write_calls == 3 and written.acknowledged:
            return AtomicWriteResult(False, ambiguous=True, failure_code="write_failed")
        return written

    monkeypatch.setattr(
        registry_marker_io, "atomic_write_bytes", fail_after_final_marker_replace
    )

    uncertain = await consumer.async_commit_then_dispatch(target, "must-not-dispatch")

    assert uncertain.state is ProtectionLoadState.PROTECTIVE_UNKNOWN
    assert dispatches == ["initial"]
    assert store.authorization_state is ProtectionAuthorizationState.PROTECTIVE_UNKNOWN
    disk = json.loads((storage / "rental_control.code_registry").read_bytes())
    assert disk["data"]["pending_fences"] == [later_owner]
    assert disk["data"]["tombstones"] == [tombstone]

    monkeypatch.setattr(registry_marker_io, "atomic_write_bytes", original_write)
    recovered = await store.async_load()

    assert recovered.state is ProtectionLoadState.VALID_V2
    assert recovered.data is not None
    assert recovered.data["pending_fences"] == [later_owner]
    assert recovered.data["tombstones"] == [tombstone]
    assert store.authorization_state is ProtectionAuthorizationState.READY


@pytest.mark.parametrize(
    "violation",
    [
        "drop_active_fence",
        "mutate_active_fence",
        "wrong_resolution_transaction",
        "wrong_resolution_generation",
        "duplicate_active_allocation",
        "active_fence_tombstone_overlap",
        "drop_historical_tombstone",
        "mutate_historical_tombstone",
        "invent_tombstone",
        "new_fence_old_transaction",
        "new_fence_old_generation",
        "new_fence_historical_creation",
    ],
)
@pytest.mark.asyncio
async def test_real_commit_path_rejects_unsafe_snapshot_transition(
    hass,
    tmp_path: Path,
    violation: str,
) -> None:
    """Validate actual disk predecessor against a target before any write."""
    storage = tmp_path / ".storage"
    storage.mkdir(parents=True, exist_ok=True)
    store = ProtectedRegistryStore(hass, asyncio.Lock(), storage_dir=storage)
    assert (await store.async_load()).state is ProtectionLoadState.ABSENT

    fence_a = _fence(
        fence_id="f" * 32,
        km_entry="km-a",
        slot=4,
        rc_entry="rc-a",
        owner_ref="owner-a",
        allocation_ref="allocation-a",
        transaction_id="1" * 32,
        generation=1,
        fence_type="pending_clear",
    )
    fence_b = _fence(
        fence_id="e" * 32,
        km_entry="km-b",
        slot=9,
        rc_entry="rc-b",
        owner_ref="owner-b",
        allocation_ref="allocation-b",
        transaction_id="1" * 32,
        generation=1,
    )
    initial = await store.async_commit(_envelope("1" * 32, 1, [fence_a, fence_b]))
    assert initial.state is ProtectionLoadState.VALID_V2

    tombstone_a = resolve_fence_to_tombstone(
        fence_a,
        tombstone_id="c" * 32,
        resolved_transaction_id="2" * 32,
        resolved_generation=2,
        resolution_kind="clear_confirmed",
        retained_at="2026-11-21T00:00:00+00:00",
    )
    transition = _envelope("2" * 32, 2, [fence_b])
    transition["data"]["tombstones"] = [tombstone_a]
    second = await store.async_commit(transition)
    assert second.state is ProtectionLoadState.VALID_V2

    target = _envelope("3" * 32, 3, [fence_b])
    target["data"]["tombstones"] = [tombstone_a]
    if violation == "drop_active_fence":
        target["data"]["pending_fences"] = []
    elif violation == "mutate_active_fence":
        changed_fence = dict(fence_b)
        changed_fence["slot"] = 10
        target["data"]["pending_fences"] = [changed_fence]
    elif violation in {
        "wrong_resolution_transaction",
        "wrong_resolution_generation",
        "active_fence_tombstone_overlap",
    }:
        target["data"]["pending_fences"] = (
            [fence_b] if violation == "active_fence_tombstone_overlap" else []
        )
        tombstone_b = resolve_fence_to_tombstone(
            fence_b,
            tombstone_id="d" * 32,
            resolved_transaction_id=(
                "2" * 32 if violation == "wrong_resolution_transaction" else "3" * 32
            ),
            resolved_generation=(
                2
                if violation
                in {"wrong_resolution_transaction", "wrong_resolution_generation"}
                else 3
            ),
            resolution_kind="owner_released",
            retained_at="2026-11-21T00:00:00+00:00",
        )
        target["data"]["tombstones"].append(tombstone_b)
    elif violation == "duplicate_active_allocation":
        duplicate = _fence(
            fence_id="d" * 32,
            km_entry="km-b",
            slot=9,
            rc_entry="rc-c",
            owner_ref="owner-c",
            allocation_ref="allocation-b",
            transaction_id="3" * 32,
            generation=3,
        )
        target["data"]["pending_fences"].append(duplicate)
    elif violation == "drop_historical_tombstone":
        target["data"]["tombstones"] = []
    elif violation == "mutate_historical_tombstone":
        mutated = dict(tombstone_a)
        mutated["owner_ref"] = "different-owner"
        target["data"]["tombstones"] = [mutated]
    elif violation == "invent_tombstone":
        unrelated_fence = dict(fence_b)
        unrelated_fence["fence_id"] = "d" * 32
        invented = resolve_fence_to_tombstone(
            unrelated_fence,
            tombstone_id="9" * 32,
            resolved_transaction_id="3" * 32,
            resolved_generation=3,
            resolution_kind="owner_released",
            retained_at="2026-11-21T00:00:00+00:00",
        )
        target["data"]["tombstones"].append(invented)
    elif violation in {
        "new_fence_old_transaction",
        "new_fence_old_generation",
        "new_fence_historical_creation",
    }:
        target_generation = 3
        target_transaction = "3" * 32
        creation_transaction, creation_generation = {
            "new_fence_old_transaction": ("2" * 32, target_generation),
            "new_fence_old_generation": (target_transaction, 2),
            "new_fence_historical_creation": ("1" * 32, 1),
        }[violation]
        target["data"]["pending_fences"].append(
            _fence(
                fence_id="9" * 32,
                km_entry="km-c",
                slot=12,
                rc_entry="rc-c",
                owner_ref="owner-c",
                allocation_ref="allocation-c",
                transaction_id=creation_transaction,
                generation=creation_generation,
            )
        )

    registry_path = storage / "rental_control.code_registry"
    marker_path = storage / "rental_control.code_registry_protection"
    prior_registry = registry_path.read_bytes()
    prior_marker = marker_path.read_bytes()
    rejected = await store.async_commit(target)

    assert rejected.state is ProtectionLoadState.PROTECTIVE_UNKNOWN
    expected_failure = (
        "envelope_invalid"
        if violation
        in {
            "duplicate_active_allocation",
            "active_fence_tombstone_overlap",
            "new_fence_old_transaction",
        }
        else "snapshot_transition_invalid"
    )
    assert rejected.failure_code == expected_failure
    assert registry_path.read_bytes() == prior_registry
    assert marker_path.read_bytes() == prior_marker
    assert store.authorization_state is ProtectionAuthorizationState.PROTECTIVE_UNKNOWN


@pytest.mark.asyncio
async def test_commit_token_invalid_while_durable_write_completion_is_blocked(
    hass,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A durable new snapshot invalidates the old token before completion."""
    storage = tmp_path / ".storage"
    storage.mkdir(parents=True, exist_ok=True)
    store = ProtectedRegistryStore(hass, asyncio.Lock(), storage_dir=storage)
    assert (await store.async_load()).state is ProtectionLoadState.ABSENT
    initial = await store.async_commit(_envelope("1" * 32, 1, []))
    assert initial.state is ProtectionLoadState.VALID_V2
    old_token = ProtectionCommitToken("1" * 32, 1)
    assert store.is_current_commit_token(old_token)

    written = threading.Event()
    finish = threading.Event()
    original_write = registry_commit._write_prepared_transaction

    def pause_after_durable_transaction(prepared):
        """Pause after all target bytes are acknowledged but before completion."""
        result = original_write(prepared)
        written.set()
        if not finish.wait(timeout=5):
            raise TimeoutError
        return result

    monkeypatch.setattr(
        registry_commit, "_write_prepared_transaction", pause_after_durable_transaction
    )
    task = asyncio.create_task(store.async_commit(_envelope("2" * 32, 2, [])))
    try:
        assert await asyncio.to_thread(written.wait, 2)
        durable = json.loads((storage / "rental_control.code_registry").read_bytes())
        assert durable["data"]["transaction_id"] == "2" * 32
        assert store.loaded_result is not None
        assert store.loaded_result.transaction_id == "1" * 32
        assert not store.is_current_commit_token(old_token)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done()
    finally:
        finish.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert store.loaded_result is not None
    assert store.loaded_result.transaction_id == "2" * 32
    assert store.loaded_result.generation == 2
    assert not store.is_current_commit_token(old_token)
    assert store.is_current_commit_token(ProtectionCommitToken("2" * 32, 2))


@pytest.mark.asyncio
async def test_executor_submission_failure_applies_completion_before_cancel(
    hass,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancelled lock cleanup cannot strand the matching commit in flight."""
    storage = tmp_path / ".storage"
    storage.mkdir(parents=True, exist_ok=True)
    store = ProtectedRegistryStore(hass, asyncio.Lock(), storage_dir=storage)
    assert (await store.async_load()).state is ProtectionLoadState.ABSENT
    initial = await store.async_commit(_envelope("1" * 32, 1, []))
    assert initial.state is ProtectionLoadState.VALID_V2
    old_token = ProtectionCommitToken("1" * 32, 1)
    assert store.is_current_commit_token(old_token)

    release_started = threading.Event()
    finish_release = threading.Event()
    original_release = registry_locks.release_io_lock
    original_run_in_executor = asyncio.get_running_loop().run_in_executor
    submission_count = 0

    def blocked_release(descriptor: int) -> bool:
        """Hold fallback cleanup so cancellation lands at its drain boundary."""
        release_started.set()
        if not finish_release.wait(timeout=5):
            raise TimeoutError("test lock release timed out")
        return original_release(descriptor)

    def fail_worker_submission(executor, function, *args):
        """Allow lock acquisition but fail submission of the protected worker."""
        nonlocal submission_count
        submission_count += 1
        if submission_count == 2:
            raise RuntimeError("synthetic executor submission failure")
        return original_run_in_executor(executor, function, *args)

    monkeypatch.setattr(registry_locks, "release_io_lock", blocked_release)
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "run_in_executor", fail_worker_submission)
    task = asyncio.create_task(store.async_commit(_envelope("2" * 32, 2, [])))
    try:
        for _ in range(200):
            if release_started.is_set():
                break
            await asyncio.sleep(0.01)
        assert release_started.is_set()
        assert not store.is_current_commit_token(old_token)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done()
        assert store._commit_in_flight is not None
    finally:
        finish_release.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert submission_count == 3
    assert store._commit_in_flight is None
    assert store.authorization_state is ProtectionAuthorizationState.READY
    assert store.loaded_result is not None
    assert store.loaded_result.transaction_id == "1" * 32
    assert store.last_result is not None
    assert store.last_result.failure_code == "io_failed"
    assert store.is_current_commit_token(old_token)
    durable = json.loads((storage / "rental_control.code_registry").read_bytes())
    assert durable["data"]["transaction_id"] == "1" * 32


@pytest.mark.asyncio
async def test_failed_protective_commit_never_reaches_fake_consumer(
    hass,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed marker acknowledgement suppresses every dependent dispatch."""
    storage = tmp_path / ".storage"
    storage.mkdir(parents=True, exist_ok=True)
    dispatches: list[str] = []
    store = ProtectedRegistryStore(hass, asyncio.Lock(), storage_dir=storage)
    assert (await store.async_load()).state is ProtectionLoadState.ABSENT
    consumer = FakeProtectiveConsumer(store, dispatches)
    monkeypatch.setattr(
        registry_marker_io,
        "atomic_write_bytes",
        Mock(return_value=AtomicWriteResult(False)),
    )

    result = await consumer.async_commit_then_dispatch(
        _envelope("1" * 32, 1, []), "must-not-run"
    )

    assert result.state is ProtectionLoadState.PROTECTIVE_UNKNOWN
    assert store.authorization_state is ProtectionAuthorizationState.PROTECTIVE_UNKNOWN
    assert dispatches == []
    assert not (storage / "rental_control.code_registry").exists()


@pytest.mark.asyncio
async def test_fake_consumer_cannot_use_retained_snapshot_after_restore_latch(
    hass,
    tmp_path: Path,
) -> None:
    """Consumers check the current gate, never retained observational data."""
    storage = tmp_path / ".storage"
    storage.mkdir(parents=True, exist_ok=True)
    store = ProtectedRegistryStore(hass, asyncio.Lock(), storage_dir=storage)
    assert (await store.async_load()).state is ProtectionLoadState.ABSENT
    dispatches: list[str] = []
    consumer = FakeProtectiveConsumer(store, dispatches)
    original_fence = _fence(
        fence_id="f" * 32,
        km_entry="km-a",
        slot=4,
        rc_entry="rc-a",
        owner_ref="owner-a",
        allocation_ref="allocation-a",
        transaction_id="1" * 32,
        generation=1,
    )

    committed = await consumer.async_commit_then_dispatch(
        _envelope("1" * 32, 1, [original_fence]), "entry-a"
    )
    assert committed.state is ProtectionLoadState.VALID_V2
    store.mark_external_restore_suspected()

    blocked = await consumer.async_commit_then_dispatch(
        _envelope("2" * 32, 2, [original_fence]), "must-not-run"
    )

    assert blocked.state is ProtectionLoadState.PROTECTIVE_UNKNOWN
    assert store.authorization_state is ProtectionAuthorizationState.PROTECTIVE_UNKNOWN
    assert store.loaded_result is not None
    assert store.loaded_result.generation == 1
    assert dispatches == ["entry-a"]


@pytest.mark.asyncio
async def test_queued_commit_rechecks_unknown_gate_before_disk_io(
    hass,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A queued stale commit cannot recover or overwrite a pending-prior state."""
    storage = tmp_path / ".storage"
    storage.mkdir(parents=True, exist_ok=True)
    store = ProtectedRegistryStore(hass, asyncio.Lock(), storage_dir=storage)
    assert (await store.async_load()).state is ProtectionLoadState.ABSENT
    dispatches: list[str] = []
    consumer = FakeProtectiveConsumer(store, dispatches)
    base_fence = _fence(
        fence_id="f" * 32,
        km_entry="km-a",
        slot=4,
        rc_entry="rc-a",
        owner_ref="owner-a",
        allocation_ref="allocation-a",
        transaction_id="1" * 32,
        generation=1,
    )
    initial = await consumer.async_commit_then_dispatch(
        _envelope("1" * 32, 1, [base_fence]), "baseline"
    )
    assert initial.state is ProtectionLoadState.VALID_V2
    baseline_token = ProtectionCommitToken("1" * 32, 1)
    assert store.is_current_commit_token(baseline_token)

    original_write = registry_marker_io.atomic_write_bytes
    original_read = registry_transactions.read_bounded_bytes
    write_calls = 0
    read_calls = 0
    envelope_started = threading.Event()
    finish_envelope = threading.Event()

    def fail_envelope_after_pending(
        path: Path,
        data: bytes,
        *,
        maximum: int,
    ) -> AtomicWriteResult:
        """Acknowledge the pending marker and fail the following envelope write."""
        nonlocal write_calls
        write_calls += 1
        if write_calls == 2:
            envelope_started.set()
            if not finish_envelope.wait(timeout=5):
                return AtomicWriteResult(False, failure_code="write_failed")
            return AtomicWriteResult(False, failure_code="write_failed")
        return original_write(path, data, maximum=maximum)

    def count_reads(path: Path, *, maximum: int):
        """Count application reads to prove the queued commit is blocked early."""
        nonlocal read_calls
        read_calls += 1
        return original_read(path, maximum=maximum)

    monkeypatch.setattr(
        registry_marker_io, "atomic_write_bytes", fail_envelope_after_pending
    )
    monkeypatch.setattr(registry_transactions, "read_bounded_bytes", count_reads)
    first_task = asyncio.create_task(
        consumer.async_commit_then_dispatch(
            _envelope("2" * 32, 2, [base_fence]), "first-uncertain"
        )
    )
    try:
        assert await asyncio.to_thread(envelope_started.wait, 2)
        assert not store.is_current_commit_token(baseline_token)
        reads_before_queue = read_calls
        second_task = asyncio.create_task(
            consumer.async_commit_then_dispatch(
                _envelope("3" * 32, 2, [base_fence]), "queued-stale"
            )
        )
        await asyncio.sleep(0.02)
        assert not second_task.done()
    finally:
        finish_envelope.set()

    first = await first_task
    second = await second_task

    assert first.state is ProtectionLoadState.PROTECTIVE_UNKNOWN
    assert first.failure_code == "envelope_write_failed"
    assert second.state is ProtectionLoadState.PROTECTIVE_UNKNOWN
    assert second.failure_code == "envelope_write_failed"
    assert write_calls == 2
    assert read_calls == reads_before_queue
    assert dispatches == ["baseline"]
    assert store.authorization_state is ProtectionAuthorizationState.PROTECTIVE_UNKNOWN
    assert not store.is_current_commit_token(baseline_token)
    pending = json.loads(
        (storage / "rental_control.code_registry_protection").read_bytes()
    )
    prior = json.loads((storage / "rental_control.code_registry").read_bytes())
    assert pending["state"] == "pending"
    assert prior["data"]["transaction_id"] == "1" * 32
    assert (
        registry_transactions._read_recovery_plan(storage).action
        is RecoveryAction.PENDING_PRIOR
    )
