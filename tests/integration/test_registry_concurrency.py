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

from custom_components.rental_control.allocator import registry_marker_io
from custom_components.rental_control.allocator import registry_transactions
from custom_components.rental_control.allocator.models import AtomicWriteResult
from custom_components.rental_control.allocator.models import (
    ProtectionAuthorizationState,
)
from custom_components.rental_control.allocator.models import ProtectionLoadResult
from custom_components.rental_control.allocator.models import ProtectionLoadState
from custom_components.rental_control.allocator.models import RecoveryAction
from custom_components.rental_control.allocator.protected_store import (
    ProtectedRegistryStore,
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
    ) -> ProtectionLoadResult:
        """Dispatch only if the complete transaction is acknowledged."""
        result = await self._store.async_commit(envelope)
        if (
            result.state is ProtectionLoadState.VALID_V2
            and self._store.authorization_state is ProtectionAuthorizationState.READY
        ):
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
    fence_a = {
        "entry_id": "entry-a",
        "slot": 4,
        "reason": "dispatched-clear-unresolved",
    }
    fence_b = {
        "entry_id": "entry-b",
        "slot": 9,
        "reason": "allocation-protection",
    }

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
    assert stale.failure_code == "generation_mismatch"
    assert dispatches == ["entry-a", "entry-b"]
    committed = json.loads((storage / "rental_control.code_registry").read_bytes())
    assert committed["data"]["pending_fences"] == [fence_a, fence_b]


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
    original_fence = {"entry_id": "km-a", "slot": 4, "owner_id": "rc-a"}

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
    base_fence = {"entry_id": "km-a", "slot": 4, "owner_id": "rc-a"}
    initial = await consumer.async_commit_then_dispatch(
        _envelope("1" * 32, 1, [base_fence]), "baseline"
    )
    assert initial.state is ProtectionLoadState.VALID_V2

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
