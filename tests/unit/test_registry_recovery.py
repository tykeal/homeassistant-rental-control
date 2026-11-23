# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Disk-backed tests for exact-byte registry/marker crash recovery."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import stat
import threading
from typing import Any

from homeassistant.util import json as ha_json
import pytest

from custom_components.rental_control.allocator import (
    protected_registry as store_module,
)
from custom_components.rental_control.allocator import registry_commit
from custom_components.rental_control.allocator import registry_marker_io
from custom_components.rental_control.allocator import registry_validation
from custom_components.rental_control.allocator import store as legacy_store
from custom_components.rental_control.allocator.models import AtomicWriteResult
from custom_components.rental_control.allocator.models import PreparedJsonBytes
from custom_components.rental_control.allocator.models import ProtectionLoadState


def _v1_bytes() -> bytes:
    """Return one valid markerless legacy wrapper."""
    payload = {"version": 1, "data": {"schema_version": 1, "records": []}}
    prepared = store_module.prepare_exact_json_bytes(payload)
    assert prepared.data is not None
    return prepared.data


def _v2_bytes(transaction_id: str, generation: int) -> bytes:
    """Return one complete v2 wrapper with a PIN-free empty ledger."""
    envelope: dict[str, Any] = {
        "version": 2,
        "minor_version": 1,
        "key": "rental_control.code_registry",
        "data": {
            "schema_version": 1,
            "transaction_id": transaction_id,
            "generation": generation,
            "ledger": {
                "schema_version": 1,
                "updated_at": "2026-01-01T00:00:00+00:00",
                "code_ref_salt": "salt",
                "records": [],
                "announced_conflicts": {},
            },
            "associations": [],
            "attestations": [],
            "pending_fences": [],
            "holds": [],
            "tombstones": [],
            "pending_config": [],
        },
    }
    assert store_module.validate_v2_envelope_shape(envelope)
    prepared = store_module.prepare_exact_json_bytes(envelope)
    assert prepared.data is not None
    return prepared.data


def _v2_final_marker(envelope_bytes: bytes, transaction_id: str) -> dict[str, Any]:
    """Build the finalized marker for an exact v2 envelope byte string."""
    return {
        "schema_version": 1,
        "activation_id": "a" * 32,
        "state": "finalized",
        "committed_version": "v2",
        "committed_generation": 1,
        "envelope_digest": hashlib.sha256(envelope_bytes).hexdigest(),
        "committed_transaction_id": transaction_id,
    }


def _marker_bytes(marker: dict[str, Any]) -> bytes:
    """Serialize a marker using its smaller independent cap."""
    prepared = store_module.prepare_exact_json_bytes(
        marker, maximum=store_module.MAX_MARKER_BYTES
    )
    assert prepared.data is not None
    return prepared.data


@pytest.mark.asyncio
async def test_fresh_absence_is_not_materialized_as_a_registry(
    tmp_path: Path,
) -> None:
    """Verified first-run absence stays absence after recovery."""
    storage = tmp_path / ".storage"
    storage.mkdir()

    result = await store_module.async_recover_protection_files(asyncio.Lock(), storage)

    assert result.state is ProtectionLoadState.ABSENT
    assert not (storage / "rental_control.code_registry").exists()
    assert not (storage / "rental_control.code_registry_protection").exists()


@pytest.mark.asyncio
async def test_raw_load_never_calls_ha_store_or_quarantines_corrupt_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Keep corrupt exact bytes and avoid HA's logging/quarantine loader."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    registry_path = storage / "rental_control.code_registry"
    sensitive_bytes = b'{"version":1,"private":"PIN 2468 at /private"}'
    registry_path.write_bytes(sensitive_bytes)

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        """Fail if a protected read reaches a legacy HA loader."""
        raise AssertionError("legacy loader invoked")

    monkeypatch.setattr(legacy_store, "Store", forbidden)
    monkeypatch.setattr(ha_json, "load_json", forbidden)
    caplog.clear()

    result = await store_module.async_recover_protection_files(asyncio.Lock(), storage)

    assert result.state is ProtectionLoadState.PROTECTIVE_UNKNOWN
    assert result.failure_code == "legacy_invalid"
    assert registry_path.read_bytes() == sensitive_bytes
    assert "2468" not in caplog.text
    assert "/private" not in caplog.text
    assert caplog.records == []


@pytest.mark.asyncio
async def test_markerless_intact_v1_remains_a_legacy_upgrade_candidate(
    tmp_path: Path,
) -> None:
    """Recovery classifies intact markerless v1 without writing on read."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    registry = storage / "rental_control.code_registry"
    original = _v1_bytes()
    registry.write_bytes(original)

    result = await store_module.async_recover_protection_files(asyncio.Lock(), storage)

    assert result.state is ProtectionLoadState.LEGACY_V1
    assert registry.read_bytes() == original
    assert not (storage / "rental_control.code_registry_protection").exists()


@pytest.mark.asyncio
async def test_finalized_v2_reacknowledges_exact_registry_and_marker_bytes(
    tmp_path: Path,
) -> None:
    """A valid committed pair is atomically re-acknowledged without reserialization."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    registry = storage / "rental_control.code_registry"
    marker_path = storage / "rental_control.code_registry_protection"
    envelope_bytes = _v2_bytes("c" * 32, 1)
    marker_raw = _marker_bytes(_v2_final_marker(envelope_bytes, "c" * 32))
    registry.write_bytes(envelope_bytes)
    marker_path.write_bytes(marker_raw)

    result = await store_module.async_recover_protection_files(asyncio.Lock(), storage)

    assert result.state is ProtectionLoadState.VALID_V2
    assert registry.read_bytes() == envelope_bytes
    assert marker_path.read_bytes() == marker_raw
    assert (
        store_module.read_bounded_bytes(
            registry, maximum=store_module.MAX_REGISTRY_BYTES
        ).data
        == envelope_bytes
    )


@pytest.mark.asyncio
async def test_pending_target_is_reacked_then_finalized_on_disk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Crash after target replacement recovers only the exact target transaction."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    registry = storage / "rental_control.code_registry"
    marker_path = storage / "rental_control.code_registry_protection"
    target_id = "d" * 32
    envelope_bytes = _v2_bytes(target_id, 2)
    pending = {
        "schema_version": 1,
        "activation_id": "a" * 32,
        "state": "pending",
        "prior": {
            "schema_version": 1,
            "activation_id": "a" * 32,
            "state": "finalized",
            "committed_version": "v2",
            "committed_generation": 1,
            "envelope_digest": "b" * 64,
            "committed_transaction_id": "c" * 32,
        },
        "target_transaction_id": target_id,
        "target_generation": 2,
        "target_digest": hashlib.sha256(envelope_bytes).hexdigest(),
        "kind": "commit",
    }
    assert store_module.validate_protection_marker_shape(pending)
    pending_bytes = _marker_bytes(pending)
    registry.write_bytes(envelope_bytes)
    marker_path.write_bytes(pending_bytes)
    original_writer = registry_marker_io.atomic_write_bytes
    writes: list[tuple[str, bytes]] = []

    def record_writes(path: Path, data: bytes, *, maximum: int):
        """Record re-acknowledged files in recovery order."""
        writes.append((path.name, data))
        return original_writer(path, data, maximum=maximum)

    monkeypatch.setattr(registry_marker_io, "atomic_write_bytes", record_writes)

    result = await store_module.async_recover_protection_files(asyncio.Lock(), storage)

    finalized = json.loads(marker_path.read_bytes())
    assert result.state is ProtectionLoadState.VALID_V2
    assert registry.read_bytes() == envelope_bytes
    assert finalized["state"] == "finalized"
    assert finalized["committed_generation"] == 2
    assert finalized["committed_transaction_id"] == target_id
    assert finalized["envelope_digest"] == hashlib.sha256(envelope_bytes).hexdigest()
    assert [name for name, _data in writes] == [
        "rental_control.code_registry_protection",
        "rental_control.code_registry",
        "rental_control.code_registry_protection",
    ]
    assert writes[0][1] == pending_bytes
    assert writes[1][1] == envelope_bytes
    assert json.loads(writes[2][1]) == finalized


@pytest.mark.asyncio
async def test_pending_v1_predecessor_restores_finalized_v1_genesis_marker(
    tmp_path: Path,
) -> None:
    """A recorded v1 predecessor is retained and exactly re-acknowledged."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    registry = storage / "rental_control.code_registry"
    marker_path = storage / "rental_control.code_registry_protection"
    v1_bytes = _v1_bytes()
    prior = {
        "schema_version": 1,
        "activation_id": "a" * 32,
        "state": "finalized",
        "committed_version": "v1",
        "committed_generation": 0,
        "envelope_digest": hashlib.sha256(v1_bytes).hexdigest(),
        "committed_transaction_id": None,
    }
    pending = {
        "schema_version": 1,
        "activation_id": "a" * 32,
        "state": "pending",
        "prior": prior,
        "target_transaction_id": "d" * 32,
        "target_generation": 1,
        "target_digest": "e" * 64,
        "kind": "v1_migration",
    }
    registry.write_bytes(v1_bytes)
    marker_path.write_bytes(_marker_bytes(pending))

    result = await store_module.async_recover_protection_files(asyncio.Lock(), storage)

    restored_marker = json.loads(marker_path.read_bytes())
    assert result.state is ProtectionLoadState.LEGACY_V1
    assert registry.read_bytes() == v1_bytes
    assert restored_marker == prior


@pytest.mark.asyncio
async def test_finalized_v1_genesis_marker_is_validated_and_reacked(
    tmp_path: Path,
) -> None:
    """A rolled-back v1 predecessor needs its exact finalized marker pair."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    registry = storage / "rental_control.code_registry"
    marker_path = storage / "rental_control.code_registry_protection"
    v1_bytes = _v1_bytes()
    marker = {
        "schema_version": 1,
        "activation_id": "a" * 32,
        "state": "finalized",
        "committed_version": "v1",
        "committed_generation": 0,
        "envelope_digest": hashlib.sha256(v1_bytes).hexdigest(),
        "committed_transaction_id": None,
    }
    marker_raw = _marker_bytes(marker)
    registry.write_bytes(v1_bytes)
    marker_path.write_bytes(marker_raw)

    result = await store_module.async_recover_protection_files(asyncio.Lock(), storage)

    assert result.state is ProtectionLoadState.LEGACY_V1
    assert registry.read_bytes() == v1_bytes
    assert marker_path.read_bytes() == marker_raw


@pytest.mark.asyncio
async def test_post_replace_directory_sync_failure_stays_protective_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Visible target bytes after failed directory sync do not finalize."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    registry = storage / "rental_control.code_registry"
    marker_path = storage / "rental_control.code_registry_protection"
    target_id = "d" * 32
    envelope_bytes = _v2_bytes(target_id, 2)
    pending = {
        "schema_version": 1,
        "activation_id": "a" * 32,
        "state": "pending",
        "prior": {
            "schema_version": 1,
            "activation_id": "a" * 32,
            "state": "finalized",
            "committed_version": "v2",
            "committed_generation": 1,
            "envelope_digest": "b" * 64,
            "committed_transaction_id": "c" * 32,
        },
        "target_transaction_id": target_id,
        "target_generation": 2,
        "target_digest": hashlib.sha256(envelope_bytes).hexdigest(),
        "kind": "commit",
    }
    pending_bytes = _marker_bytes(pending)
    registry.write_bytes(envelope_bytes)
    marker_path.write_bytes(pending_bytes)
    actual_fsync = os.fsync
    directory_syncs = 0

    def fail_registry_directory_sync(descriptor: int) -> None:
        """Fail the second directory acknowledgement after exact re-ack."""
        nonlocal directory_syncs
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            directory_syncs += 1
            if directory_syncs == 2:
                raise OSError("PIN 2468 /private/config/.storage")
        actual_fsync(descriptor)

    monkeypatch.setattr(store_module.os, "fsync", fail_registry_directory_sync)
    result = await store_module.async_recover_protection_files(asyncio.Lock(), storage)

    assert directory_syncs == 2
    assert result.state is ProtectionLoadState.PROTECTIVE_UNKNOWN
    assert result.failure_code == "reack_failed"
    assert registry.read_bytes() == envelope_bytes
    assert marker_path.read_bytes() == pending_bytes


@pytest.mark.asyncio
async def test_pending_genesis_absent_removes_only_marker_and_syncs(
    tmp_path: Path,
) -> None:
    """Rollback of an uncommitted fresh genesis never creates an envelope."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    marker_path = storage / "rental_control.code_registry_protection"
    pending = {
        "schema_version": 1,
        "activation_id": "f" * 32,
        "state": "pending",
        "prior": {"state": "genesis_absent"},
        "target_transaction_id": "1" * 32,
        "target_generation": 1,
        "target_digest": "2" * 64,
        "kind": "genesis",
    }
    marker_path.write_bytes(_marker_bytes(pending))

    result = await store_module.async_recover_protection_files(asyncio.Lock(), storage)

    assert result.state is ProtectionLoadState.ABSENT
    assert not marker_path.exists()
    assert not (storage / "rental_control.code_registry").exists()


@pytest.mark.asyncio
async def test_digest_mismatch_preserves_both_files_as_protective_unknown(
    tmp_path: Path,
) -> None:
    """A mismatched marker never triggers re-acknowledgement or cleanup."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    registry = storage / "rental_control.code_registry"
    marker_path = storage / "rental_control.code_registry_protection"
    envelope_bytes = _v2_bytes("c" * 32, 1)
    marker = _v2_final_marker(envelope_bytes, "c" * 32)
    marker["envelope_digest"] = "0" * 64
    marker_raw = _marker_bytes(marker)
    registry.write_bytes(envelope_bytes)
    marker_path.write_bytes(marker_raw)

    result = await store_module.async_recover_protection_files(asyncio.Lock(), storage)

    assert result.state is ProtectionLoadState.PROTECTIVE_UNKNOWN
    assert result.failure_code == "marker_mismatch"
    assert registry.read_bytes() == envelope_bytes
    assert marker_path.read_bytes() == marker_raw


@pytest.mark.asyncio
async def test_commit_uses_pending_envelope_final_marker_order_and_successor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Commit only after the ordered marker/envelope/final-marker writes."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    target = json.loads(_v2_bytes("1" * 32, 1))
    original_writer = registry_marker_io.atomic_write_bytes
    writes: list[tuple[str, bytes]] = []

    def record_writes(path: Path, data: bytes, *, maximum: int):
        """Record pending, envelope and final marker writes."""
        writes.append((path.name, data))
        return original_writer(path, data, maximum=maximum)

    monkeypatch.setattr(registry_marker_io, "atomic_write_bytes", record_writes)
    result = await store_module.async_commit_protection_transaction(
        asyncio.Lock(), storage, target
    )

    registry = storage / "rental_control.code_registry"
    marker_path = storage / "rental_control.code_registry_protection"
    raw_registry = registry.read_bytes()
    final_marker = json.loads(marker_path.read_bytes())
    assert result.state is ProtectionLoadState.VALID_V2
    assert result.transaction_id == "1" * 32
    assert result.generation == 1
    assert [name for name, _data in writes] == [
        "rental_control.code_registry_protection",
        "rental_control.code_registry",
        "rental_control.code_registry_protection",
    ]
    assert json.loads(writes[0][1])["state"] == "pending"
    assert json.loads(writes[1][1]) == target
    assert json.loads(writes[2][1]) == final_marker
    assert final_marker["committed_generation"] == 1
    assert final_marker["envelope_digest"] == hashlib.sha256(raw_registry).hexdigest()

    next_target = json.loads(_v2_bytes("2" * 32, 2))
    second_result = await store_module.async_commit_protection_transaction(
        asyncio.Lock(), storage, next_target
    )
    second_marker = json.loads(marker_path.read_bytes())
    assert second_result.state is ProtectionLoadState.VALID_V2
    assert second_result.generation == 2
    assert second_marker["committed_transaction_id"] == "2" * 32


@pytest.mark.asyncio
async def test_legacy_migration_requires_explicit_quiescence_gate(
    tmp_path: Path,
) -> None:
    """Never start a v1 migration before caller-established writer quiescence."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    registry = storage / "rental_control.code_registry"
    v1_bytes = _v1_bytes()
    registry.write_bytes(v1_bytes)
    target = json.loads(_v2_bytes("1" * 32, 1))

    blocked = await store_module.async_commit_protection_transaction(
        asyncio.Lock(), storage, target
    )

    assert blocked.state is ProtectionLoadState.UNAVAILABLE
    assert blocked.failure_code == "migration_not_quiescent"
    assert registry.read_bytes() == v1_bytes
    assert not (storage / "rental_control.code_registry_protection").exists()


@pytest.mark.parametrize("fail_at", [1, 2, 3])
@pytest.mark.asyncio
async def test_commit_failure_at_each_marker_boundary_never_acknowledges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fail_at: int,
) -> None:
    """Any failed pending, envelope or final-marker write blocks commit."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    target = json.loads(_v2_bytes("1" * 32, 1))
    original_writer = registry_marker_io.atomic_write_bytes
    call_count = 0

    def fail_one_write(path: Path, data: bytes, *, maximum: int):
        """Fail one selected transaction boundary and pass through the others."""
        nonlocal call_count
        call_count += 1
        if call_count == fail_at:
            return AtomicWriteResult(False, failure_code="write_failed")
        return original_writer(path, data, maximum=maximum)

    monkeypatch.setattr(registry_marker_io, "atomic_write_bytes", fail_one_write)
    result = await store_module.async_commit_protection_transaction(
        asyncio.Lock(), storage, target
    )

    assert result.state is ProtectionLoadState.PROTECTIVE_UNKNOWN
    assert result.transaction_id is None
    assert call_count == fail_at
    marker_path = storage / "rental_control.code_registry_protection"
    registry_path = storage / "rental_control.code_registry"
    if fail_at == 1:
        assert not marker_path.exists()
        assert not registry_path.exists()
    else:
        assert json.loads(marker_path.read_bytes())["state"] == "pending"
    if fail_at == 2:
        assert not registry_path.exists()
    if fail_at == 3:
        assert json.loads(registry_path.read_bytes()) == target


@pytest.mark.asyncio
async def test_commit_serializes_envelope_and_each_marker_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hash and persist the one prepared envelope snapshot without re-encoding."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    target = json.loads(_v2_bytes("1" * 32, 1))
    actual_prepare = registry_validation.prepare_save_json
    prepared_values: list[Any] = []

    def record_prepare(value: Any):
        """Record each immutable object passed to HA serialization."""
        prepared_values.append(value)
        return actual_prepare(value)

    monkeypatch.setattr(registry_validation, "prepare_save_json", record_prepare)
    result = await store_module.async_commit_protection_transaction(
        asyncio.Lock(), storage, target
    )

    assert result.state is ProtectionLoadState.VALID_V2
    assert len(prepared_values) == 3
    assert prepared_values[0] == target
    assert prepared_values[1]["state"] == "pending"
    assert prepared_values[2]["state"] == "finalized"


@pytest.mark.parametrize("reject_call", [1, 2, 3])
@pytest.mark.asyncio
async def test_commit_oversize_target_or_marker_writes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reject_call: int,
) -> None:
    """Preflight all three exact outputs before the first transaction write."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    target = json.loads(_v2_bytes("1" * 32, 1))
    actual_prepare = registry_commit.prepare_exact_json_bytes
    prepared_calls = 0
    writes: list[Path] = []

    def reject_one_payload(
        payload: dict[str, Any],
        *,
        maximum: int = store_module.MAX_REGISTRY_BYTES,
    ) -> PreparedJsonBytes:
        """Reject the selected serialized payload before any write."""
        nonlocal prepared_calls
        prepared_calls += 1
        if prepared_calls == reject_call:
            return PreparedJsonBytes(None, None, "serialized_oversize")
        return actual_prepare(payload, maximum=maximum)

    actual_writer = registry_marker_io.atomic_write_bytes

    def record_write(
        path: Path,
        data: bytes,
        *,
        maximum: int,
    ) -> AtomicWriteResult:
        """Record any write that would violate all-output preflight."""
        writes.append(path)
        return actual_writer(path, data, maximum=maximum)

    monkeypatch.setattr(registry_commit, "prepare_exact_json_bytes", reject_one_payload)
    monkeypatch.setattr(registry_marker_io, "atomic_write_bytes", record_write)

    result = await store_module.async_commit_protection_transaction(
        asyncio.Lock(), storage, target
    )

    assert result.state is ProtectionLoadState.PROTECTIVE_UNKNOWN
    assert result.failure_code in {"serialized_oversize", "marker_oversize"}
    assert prepared_calls >= reject_call
    assert writes == []
    assert not (storage / "rental_control.code_registry").exists()
    assert not (storage / "rental_control.code_registry_protection").exists()


@pytest.mark.asyncio
async def test_cancellation_drains_full_protective_commit_before_propagating(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancelled commit keeps its async and OS locks through worker completion."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    async_lock = asyncio.Lock()
    target = json.loads(_v2_bytes("1" * 32, 1))
    original_writer = registry_marker_io.atomic_write_bytes
    writer_started = threading.Event()
    finish_writer = threading.Event()

    def delay_first_marker_write(path: Path, data: bytes, *, maximum: int):
        """Block the initial marker writer while cancellation is delivered."""
        if (
            path.name == "rental_control.code_registry_protection"
            and not writer_started.is_set()
        ):
            writer_started.set()
            if not finish_writer.wait(timeout=5):
                raise TimeoutError("test writer timed out")
        return original_writer(path, data, maximum=maximum)

    monkeypatch.setattr(
        registry_marker_io, "atomic_write_bytes", delay_first_marker_write
    )
    task = asyncio.create_task(
        store_module.async_commit_protection_transaction(async_lock, storage, target)
    )
    try:
        assert await asyncio.to_thread(writer_started.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done()
        assert async_lock.locked()
        lock_attempt = store_module.try_acquire_io_lock(storage)
        assert lock_attempt.busy
        assert lock_attempt.descriptor is None
    finally:
        finish_writer.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    registry = storage / "rental_control.code_registry"
    marker_path = storage / "rental_control.code_registry_protection"
    assert not async_lock.locked()
    assert json.loads(registry.read_bytes()) == target
    assert json.loads(marker_path.read_bytes())["state"] == "finalized"
