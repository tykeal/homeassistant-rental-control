# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for bounded and cancellation-safe protected storage operations."""

from __future__ import annotations

import asyncio
import multiprocessing
import os
from pathlib import Path
import threading
from typing import Any

import pytest

from custom_components.rental_control.allocator import (
    protected_registry as store_module,
)
from custom_components.rental_control.allocator import registry_locks
from custom_components.rental_control.allocator.models import CleanupState
from custom_components.rental_control.allocator.models import StorageRunResult
from custom_components.rental_control.allocator.models import StorageRunState


def _empty_v2_envelope() -> dict[str, Any]:
    """Build a complete empty v2 snapshot for cleanup recovery tests."""
    return {
        "version": 2,
        "minor_version": 1,
        "key": "rental_control.code_registry",
        "data": {
            "schema_version": 1,
            "transaction_id": "a" * 32,
            "generation": 1,
            "ledger": {
                "schema_version": 1,
                "updated_at": "2026-11-21T00:00:00+00:00",
                "code_ref_salt": "test-salt",
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


async def _write_valid_v2(storage: Path) -> None:
    """Create an actually acknowledged v2 registry/marker pair."""
    result = await store_module.async_commit_protection_transaction(
        asyncio.Lock(),
        storage,
        _empty_v2_envelope(),
    )
    assert result.state.value == "valid_v2"


@pytest.mark.parametrize("deadline", [0.0, -1.0, float("nan"), True])
@pytest.mark.asyncio
async def test_invalid_operation_deadline_returns_busy_without_io(
    tmp_path: Path,
    deadline: float,
) -> None:
    """Reject zero, negative, nonfinite and boolean deadline values."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    invoked = False

    def worker() -> None:
        """Record an unexpected worker start."""
        nonlocal invoked
        invoked = True

    result = await store_module.async_run_protected_operation(
        asyncio.Lock(),
        storage,
        worker,
        deadline_seconds=deadline,
    )

    assert result.state is StorageRunState.BUSY
    assert not invoked
    assert not (storage / store_module._IO_LOCK_NAME).exists()


def _hold_io_lock_for_cleanup_test(
    storage_dir: str,
    acquired: Any,
    release: Any,
) -> None:
    """Hold the real process-shared lock until the parent releases it."""
    attempt = store_module.try_acquire_io_lock(Path(storage_dir))
    if attempt.descriptor is None:
        acquired.set()
        return
    acquired.set()
    release.wait(timeout=10)
    store_module.release_io_lock(attempt.descriptor)


@pytest.mark.asyncio
async def test_async_lock_deadline_returns_busy_without_running_worker(
    tmp_path: Path,
) -> None:
    """Queue expiry must not start any protected operation."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    async_lock = asyncio.Lock()
    await async_lock.acquire()
    invoked = False

    def worker() -> str:
        """Record an unexpected worker start."""
        nonlocal invoked
        invoked = True
        return "unexpected"

    try:
        result = await store_module.async_run_protected_operation(
            async_lock,
            storage,
            worker,
            deadline_seconds=0.02,
        )
    finally:
        async_lock.release()

    assert result.state is StorageRunState.BUSY
    assert result.failure_code is None
    assert not invoked
    assert not (storage / store_module._IO_LOCK_NAME).exists()


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="requires native POSIX flock")
async def test_process_lock_deadline_retries_without_blocking_event_loop(
    tmp_path: Path,
) -> None:
    """Contention retries use the shared deadline without blocking HA's loop."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    holder = store_module.try_acquire_io_lock(storage)
    assert holder.descriptor is not None
    async_lock = asyncio.Lock()
    invoked = False
    heartbeats = 0

    def worker() -> None:
        """Record whether protected work starts despite process contention."""
        nonlocal invoked
        invoked = True

    async def heartbeat() -> None:
        """Keep the loop responsive while the process lock is contended."""
        nonlocal heartbeats
        while True:
            heartbeats += 1
            await asyncio.sleep(0.005)

    heartbeat_task = asyncio.create_task(heartbeat())
    try:
        result = await store_module.async_run_protected_operation(
            async_lock,
            storage,
            worker,
            deadline_seconds=0.08,
        )
    finally:
        heartbeat_task.cancel()
        assert store_module.release_io_lock(holder.descriptor)
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass

    assert result.state is StorageRunState.BUSY
    assert not invoked
    assert heartbeats > 1
    assert not async_lock.locked()


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="requires native POSIX flock")
async def test_cancellation_stops_process_lock_contention_waiter(
    tmp_path: Path,
) -> None:
    """A cancelled contended waiter releases the async lock without I/O."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    holder = store_module.try_acquire_io_lock(storage)
    assert holder.descriptor is not None
    async_lock = asyncio.Lock()
    invoked = False

    def worker() -> None:
        """Record whether a cancelled lock waiter starts protected work."""
        nonlocal invoked
        invoked = True

    task = asyncio.create_task(
        store_module.async_run_protected_operation(
            async_lock,
            storage,
            worker,
            deadline_seconds=5,
        )
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert not invoked
    assert not async_lock.locked()
    still_held = store_module.try_acquire_io_lock(storage)
    assert still_held.busy
    assert still_held.descriptor is None
    assert store_module.release_io_lock(holder.descriptor)


@pytest.mark.asyncio
async def test_cancellation_drains_worker_and_applies_result_before_propagating(
    tmp_path: Path,
) -> None:
    """Cancellation cannot abandon submitted I/O or release its locks early."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    async_lock = asyncio.Lock()
    worker_started = threading.Event()
    finish_worker = threading.Event()
    completed: list[StorageRunResult] = []

    def worker() -> str:
        """Block the executor worker until cancellation draining is tested."""
        worker_started.set()
        if not finish_worker.wait(timeout=5):
            raise TimeoutError("test worker timed out")
        return "durable-result"

    task = asyncio.create_task(
        store_module.async_run_protected_operation(
            async_lock,
            storage,
            worker,
            on_completion=completed.append,
        )
    )
    try:
        assert await asyncio.to_thread(worker_started.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0.02)

        assert not task.done()
        assert async_lock.locked()
        contention = store_module.try_acquire_io_lock(storage)
        assert contention.busy
        assert contention.descriptor is None
    finally:
        finish_worker.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert completed
    result = completed[0]
    assert result.state is StorageRunState.COMPLETED
    assert result.value == "durable-result"
    assert not async_lock.locked()

    retry = store_module.try_acquire_io_lock(storage)
    assert retry.descriptor is not None
    assert store_module.release_io_lock(retry.descriptor)


@pytest.mark.asyncio
async def test_worker_failure_after_submission_is_sanitized_as_uncertain(
    tmp_path: Path,
) -> None:
    """Raw operation exceptions never escape the protected I/O boundary."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    async_lock = asyncio.Lock()

    def worker() -> None:
        """Raise a sensitive error after executor submission."""
        raise OSError("private path and contents")

    result = await store_module.async_run_protected_operation(
        async_lock,
        storage,
        worker,
    )

    assert result.state is StorageRunState.UNCERTAIN
    assert result.failure_code == "io_failed"
    assert result.value is None
    assert not async_lock.locked()
    retry = store_module.try_acquire_io_lock(storage)
    assert retry.descriptor is not None
    assert store_module.release_io_lock(retry.descriptor)


@pytest.mark.asyncio
async def test_lock_release_failure_preserves_operation_result_as_uncertain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A completed worker is not downgraded to no-mutation unavailable."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    original_release = registry_locks.release_io_lock

    def report_release_failure(descriptor: int) -> bool:
        """Close the lock, then simulate an unacknowledged release result."""
        original_release(descriptor)
        return False

    monkeypatch.setattr(registry_locks, "release_io_lock", report_release_failure)

    result = await store_module.async_run_protected_operation(
        asyncio.Lock(),
        storage,
        lambda: "durable-outcome",
    )

    assert result.state is StorageRunState.UNCERTAIN
    assert result.failure_code == "lock_release_failed"
    assert result.value == "durable-outcome"


@pytest.mark.asyncio
async def test_sensitive_lock_release_exception_preserves_operation_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A raw release exception cannot erase a known worker outcome."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    original_release = registry_locks.release_io_lock

    def raise_after_release(descriptor: int) -> bool:
        """Close the lock and raise a sensitive chained release error."""
        original_release(descriptor)
        try:
            raise OSError("PIN 2468 at /private/lock")
        except OSError as cause:
            raise RuntimeError("sensitive release chain") from cause

    monkeypatch.setattr(registry_locks, "release_io_lock", raise_after_release)

    result = await store_module.async_run_protected_operation(
        asyncio.Lock(),
        storage,
        lambda: "durable-outcome",
    )

    assert result.state is StorageRunState.UNCERTAIN
    assert result.failure_code == "lock_release_failed"
    assert result.value == "durable-outcome"
    assert "2468" not in repr(result)
    assert "/private" not in repr(result)


@pytest.mark.asyncio
async def test_completion_callback_failure_preserves_result_and_fails_closed(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A sensitive callback exception becomes uncertain without losing outcome."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    observed: list[StorageRunResult] = []

    def broken_callback(result: StorageRunResult) -> None:
        """Raise a sensitive chained exception for both callback attempts."""
        observed.append(result)
        try:
            raise OSError("PIN 2468 in /private/registry")
        except OSError as cause:
            raise RuntimeError("sensitive callback") from cause

    result = await store_module.async_run_protected_operation(
        asyncio.Lock(),
        storage,
        lambda: "durable-outcome",
        on_completion=broken_callback,
    )

    assert result.state is StorageRunState.UNCERTAIN
    assert result.failure_code == "completion_failed"
    assert result.value == "durable-outcome"
    assert len(observed) == 2
    assert observed[1].state is StorageRunState.UNCERTAIN
    assert "2468" not in repr(result)
    assert "/private" not in repr(result)
    assert "completion callback failed" in caplog.text
    assert "2468" not in caplog.text
    assert "/private" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="requires native POSIX storage locking")
async def test_temp_cleanup_is_deferred_until_recovered_and_quiescent(
    tmp_path: Path,
) -> None:
    """Pending or unknown storage never triggers temporary-file cleanup."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    target = storage / "rental_control.code_registry"
    temporary = storage / (store_module._owned_temp_prefix(target, "a" * 32) + "random")
    temporary.write_bytes(b"unfinished private temp")
    temporary.chmod(0o600)
    async_lock = asyncio.Lock()

    result = await store_module.async_cleanup_owned_temporary_files(
        async_lock,
        storage,
    )

    assert result.state is CleanupState.DEFERRED
    assert result.failure_code == "partial_history"
    assert temporary.read_bytes() == b"unfinished private temp"
    assert (storage / store_module._IO_LOCK_NAME).exists()


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="requires native private POSIX files")
async def test_temp_cleanup_is_exact_and_all_or_none_on_unsafe_candidate(
    tmp_path: Path,
) -> None:
    """Preserve all candidates on unsafe names and remove only owned temps."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    await _write_valid_v2(storage)
    registry = storage / "rental_control.code_registry"
    owned = storage / (store_module._owned_temp_prefix(registry, "b" * 32) + "random")
    owned.write_bytes(b"orphaned temp")
    owned.chmod(0o600)
    decoy = storage / ".rental_control.code_registry.not-owned"
    decoy.write_bytes(b"decoy")
    outside = tmp_path / "outside"
    outside.write_bytes(b"external")
    linked_owned = storage / (
        store_module._owned_temp_prefix(registry, "c" * 32) + "random"
    )
    linked_owned.symlink_to(outside)

    deferred_failure = await store_module.async_cleanup_owned_temporary_files(
        asyncio.Lock(),
        storage,
    )

    assert deferred_failure.state is CleanupState.FAILED
    assert deferred_failure.failure_code == "unsafe_temp"
    assert owned.exists()
    assert linked_owned.is_symlink()
    assert registry.exists()

    linked_owned.unlink()
    completed = await store_module.async_cleanup_owned_temporary_files(
        asyncio.Lock(),
        storage,
    )

    assert completed.state is CleanupState.COMPLETED
    assert completed.removed_count == 1
    assert not owned.exists()
    assert decoy.read_bytes() == b"decoy"


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="requires native POSIX storage locking")
async def test_temp_cleanup_never_cleans_partial_history(
    tmp_path: Path,
) -> None:
    """An owned temp without an authoritative marker remains protective-unknown."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    registry = storage / "rental_control.code_registry"
    temporary = storage / (
        store_module._owned_temp_prefix(registry, "f" * 32) + "random"
    )
    temporary.write_bytes(b"unfinished transaction evidence")
    temporary.chmod(0o600)

    result = await store_module.async_cleanup_owned_temporary_files(
        asyncio.Lock(),
        storage,
    )

    assert result.state is CleanupState.DEFERRED
    assert result.failure_code == "partial_history"
    assert temporary.read_bytes() == b"unfinished transaction evidence"

    quarantined_storage = tmp_path / "quarantined" / ".storage"
    quarantined_storage.mkdir(parents=True)
    registry = quarantined_storage / "rental_control.code_registry"
    registry.write_text(
        '{"version":1,"data":{"schema_version":1,"records":[]}}',
        encoding="utf-8",
    )
    quarantine = quarantined_storage / "rental_control.code_registry.corrupt.keep"
    quarantine.write_bytes(b"preserve")
    orphan = quarantined_storage / (
        store_module._owned_temp_prefix(registry, "9" * 32) + "random"
    )
    orphan.write_bytes(b"partial")
    orphan.chmod(0o600)

    quarantined_result = await store_module.async_cleanup_owned_temporary_files(
        asyncio.Lock(),
        quarantined_storage,
    )

    assert quarantined_result.state is CleanupState.DEFERRED
    assert quarantined_result.failure_code == "partial_history"
    assert orphan.read_bytes() == b"partial"
    assert quarantine.read_bytes() == b"preserve"


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="requires native POSIX storage locking")
async def test_temp_cleanup_over_limit_deletes_none(tmp_path: Path) -> None:
    """Scan only through the 4,097th entry and preserve every candidate."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    registry = storage / "rental_control.code_registry"
    temporary = storage / (
        store_module._owned_temp_prefix(registry, "e" * 32) + "random"
    )
    temporary.write_bytes(b"candidate")
    temporary.chmod(0o600)
    for index in range(store_module.MAX_DIRECTORY_ENTRIES):
        (storage / f"unrelated-{index}").touch()

    result = await store_module.async_cleanup_owned_temporary_files(
        asyncio.Lock(),
        storage,
    )

    assert result.state is CleanupState.DEFERRED
    assert result.failure_code == "scan_limit"
    assert temporary.read_bytes() == b"candidate"


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="requires native POSIX flock")
async def test_temp_cleanup_respects_second_process_lock_deadline(
    tmp_path: Path,
) -> None:
    """A competing process blocks cleanup without deleting its candidates."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    await _write_valid_v2(storage)
    registry = storage / "rental_control.code_registry"
    temporary = storage / (
        store_module._owned_temp_prefix(registry, "d" * 32) + "random"
    )
    temporary.write_bytes(b"candidate")
    temporary.chmod(0o600)
    context = multiprocessing.get_context("spawn")
    acquired = context.Event()
    release = context.Event()
    process = context.Process(
        target=_hold_io_lock_for_cleanup_test,
        args=(str(storage), acquired, release),
    )
    process.start()
    try:
        assert acquired.wait(timeout=5)
        result = await store_module.async_cleanup_owned_temporary_files(
            asyncio.Lock(),
            storage,
        )
    finally:
        release.set()
        process.join(timeout=5)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)

    assert result.state is CleanupState.BUSY
    assert temporary.read_bytes() == b"candidate"
