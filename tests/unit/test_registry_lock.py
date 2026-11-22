# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for POSIX process-shared protected-registry locking."""

from __future__ import annotations

import asyncio
import builtins
from collections.abc import Callable
import errno
import multiprocessing
import os
from pathlib import Path
import threading
import time
from typing import Any
from typing import cast

import pytest

from custom_components.rental_control.allocator import (
    protected_registry as store_module,
)
from custom_components.rental_control.allocator import registry_locks
from custom_components.rental_control.allocator.models import IOLockAttempt
from custom_components.rental_control.allocator.models import StorageRunResult
from custom_components.rental_control.allocator.models import StorageRunState


def _record_release(released: list[int]) -> Callable[[int], bool]:
    """Return a typed process-lock release stub that records its descriptor."""

    def release(descriptor: int) -> bool:
        """Record one released process descriptor."""
        released.append(descriptor)
        return True

    return release


def _hold_registry_lock(
    storage_dir: str,
    acquired: multiprocessing.synchronize.Event,
    release: multiprocessing.synchronize.Event,
) -> None:
    """Hold the actual RC lock in a separate process until released."""
    attempt = store_module.try_acquire_io_lock(Path(storage_dir))
    if attempt.descriptor is None:
        acquired.set()
        return
    acquired.set()
    release.wait(timeout=10)
    store_module.release_io_lock(attempt.descriptor)


@pytest.mark.skipif(os.name != "posix", reason="POSIX flock must be tested natively")
def test_process_lock_reports_actual_second_process_contention(
    tmp_path: Path,
) -> None:
    """Classify real flock contention as busy and allow a later retry."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    context = multiprocessing.get_context("spawn")
    acquired = context.Event()
    release = context.Event()
    process = context.Process(
        target=_hold_registry_lock,
        args=(str(storage), acquired, release),
    )
    process.start()
    try:
        assert acquired.wait(timeout=5)

        busy = store_module.try_acquire_io_lock(storage)

        assert busy.descriptor is None
        assert busy.busy
        assert busy.failure_code is None
    finally:
        release.set()
        process.join(timeout=5)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)

    retry = store_module.try_acquire_io_lock(storage)
    assert retry.descriptor is not None
    assert not retry.busy
    assert retry.failure_code is None
    assert store_module.release_io_lock(retry.descriptor)


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission check")
def test_unsafe_existing_lock_file_is_not_repaired(tmp_path: Path) -> None:
    """Fail closed without chmodding an unsafe lock file."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    lock_file = storage / "rental_control.code_registry.io_lock"
    lock_file.touch(mode=0o600)
    lock_file.chmod(0o644)

    result = store_module.try_acquire_io_lock(storage)

    assert result.descriptor is None
    assert not result.busy
    assert result.failure_code == "unsafe_lock"
    assert lock_file.stat().st_mode & 0o777 == 0o644


@pytest.mark.skipif(os.name != "posix", reason="POSIX hard-link check")
def test_hardlinked_lock_file_is_not_accepted(tmp_path: Path) -> None:
    """A second hard-link name cannot alias the stable lock inode."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    lock_file = storage / store_module._IO_LOCK_NAME
    lock_file.touch(mode=0o600)
    os.link(lock_file, tmp_path / "outside-lock")

    result = store_module.try_acquire_io_lock(storage)

    assert result.descriptor is None
    assert result.failure_code == "unsafe_lock"
    assert lock_file.stat().st_nlink == 2


@pytest.mark.skipif(os.name != "posix", reason="POSIX no-follow lock path check")
def test_symlinked_lock_file_is_not_followed(tmp_path: Path) -> None:
    """Reject the lock symlink and leave its target untouched."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    outside = tmp_path / "outside-lock"
    outside.touch(mode=0o600)
    lock_file = storage / "rental_control.code_registry.io_lock"
    lock_file.symlink_to(outside)

    result = store_module.try_acquire_io_lock(storage)

    assert result.descriptor is None
    assert result.failure_code == "unsafe_lock"
    assert outside.stat().st_size == 0


@pytest.mark.skipif(os.name != "posix", reason="POSIX storage path check")
def test_symlinked_storage_directory_is_not_used_for_locking(
    tmp_path: Path,
) -> None:
    """Reject the storage directory alias before creating a lock through it."""
    real_storage = tmp_path / "real-storage"
    real_storage.mkdir()
    aliased_storage = tmp_path / "aliased-storage"
    aliased_storage.symlink_to(real_storage, target_is_directory=True)

    result = store_module.try_acquire_io_lock(aliased_storage)

    assert result.descriptor is None
    assert result.failure_code == "unsafe_lock"
    assert not (real_storage / store_module._IO_LOCK_NAME).exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX flock error classification")
def test_process_lock_contention_error_closes_open_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An EAGAIN flock result is busy and does not leak its file descriptor."""
    import fcntl

    storage = tmp_path / ".storage"
    storage.mkdir()
    opened: list[int] = []
    original_open = os.open

    def capture_open(*args, **kwargs) -> int:
        """Track the real descriptor so the failed acquisition can be checked."""
        descriptor = original_open(*args, **kwargs)
        opened.append(descriptor)
        return descriptor

    def contend(_descriptor: int, _operation: int) -> None:
        """Report kernel-style nonblocking contention."""
        raise BlockingIOError(errno.EAGAIN, "synthetic contention")

    monkeypatch.setattr(os, "open", capture_open)
    monkeypatch.setattr(fcntl, "flock", contend)

    result = store_module.try_acquire_io_lock(storage)

    assert result.descriptor is None
    assert result.busy
    assert result.failure_code is None
    assert opened
    with pytest.raises(OSError):
        os.fstat(opened[0])


@pytest.mark.skipif(os.name != "posix", reason="POSIX flock release")
def test_process_lock_release_failure_is_sanitized_and_closes_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed unlock returns false while still closing the acquired descriptor."""
    import fcntl

    storage = tmp_path / ".storage"
    storage.mkdir()
    attempt = store_module.try_acquire_io_lock(storage)
    assert attempt.descriptor is not None

    def fail_unlock(_descriptor: int, _operation: int) -> None:
        """Inject a sanitized process-lock release failure."""
        raise OSError(errno.EIO, "synthetic unlock failure")

    monkeypatch.setattr(fcntl, "flock", fail_unlock)

    assert not registry_locks.release_io_lock(attempt.descriptor)
    with pytest.raises(OSError):
        os.fstat(attempt.descriptor)


@pytest.mark.skipif(os.name != "posix", reason="POSIX flock interruption")
def test_interrupted_nonblocking_lock_retries_inside_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retry EINTR only while the caller's monotonic queue deadline remains."""
    import fcntl

    storage = tmp_path / ".storage"
    storage.mkdir()
    original_flock = fcntl.flock
    calls = 0

    def interrupt_once(descriptor: int, operation: int) -> None:
        """Inject one interruption before taking the real process lock."""
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError(errno.EINTR, "synthetic interrupt")
        original_flock(descriptor, operation)

    monkeypatch.setattr(fcntl, "flock", interrupt_once)
    result = store_module.try_acquire_io_lock(storage, deadline=time.monotonic() + 1)

    assert result.descriptor is not None
    assert calls == 2
    assert store_module.release_io_lock(result.descriptor)


@pytest.mark.skipif(os.name != "posix", reason="POSIX flock interruption")
def test_interrupted_lock_outside_deadline_closes_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An EINTR after deadline returns a sanitized failure without leaking fd."""
    import fcntl

    storage = tmp_path / ".storage"
    storage.mkdir()
    opened: list[int] = []
    original_open = os.open

    def capture_open(*args, **kwargs) -> int:
        """Track the real descriptor so the failed acquisition can be checked."""
        descriptor = original_open(*args, **kwargs)
        opened.append(descriptor)
        return descriptor

    def interrupt(descriptor: int, operation: int) -> None:
        """Inject a late interruption after the lock deadline."""
        raise OSError(errno.EINTR, "synthetic late interrupt")

    monkeypatch.setattr(os, "open", capture_open)
    monkeypatch.setattr(fcntl, "flock", interrupt)
    result = store_module.try_acquire_io_lock(storage, deadline=time.monotonic() - 1)

    assert result.descriptor is None
    assert result.failure_code == "lock_unavailable"
    assert opened
    with pytest.raises(OSError):
        os.fstat(opened[0])


def test_unsupported_platform_fails_closed_without_touching_storage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not claim unverified Windows path traversal or locking support."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    monkeypatch.setattr(store_module.os, "name", "nt")
    target = storage / "rental_control.code_registry"

    read_result = store_module.read_bounded_bytes(target, maximum=1024)
    lock_result = store_module.try_acquire_io_lock(storage)
    write_result = store_module.atomic_write_bytes(target, b"protected", maximum=1024)

    assert read_result.state.value == "failed"
    assert read_result.failure_code == "unsafe_path"
    assert lock_result.descriptor is None
    assert lock_result.failure_code == "lock_unavailable"
    assert not write_result.acknowledged
    assert write_result.failure_code == "write_unavailable"
    assert not target.exists()
    assert not (storage / store_module._IO_LOCK_NAME).exists()


@pytest.mark.asyncio
async def test_protected_operation_rejects_bad_deadlines_and_busy_async_lock(
    tmp_path: Path,
) -> None:
    """Invalid or contended async locks return BUSY without running storage."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    operation_calls = 0

    def operation() -> str:
        """Record an unexpected worker start."""
        nonlocal operation_calls
        operation_calls += 1
        return "unexpected"

    for deadline in (True, 0, -1, float("nan")):
        result = await registry_locks.async_run_protected_operation(
            asyncio.Lock(),
            storage,
            operation,
            deadline_seconds=deadline,
        )
        assert result.state is StorageRunState.BUSY
    lock = asyncio.Lock()
    await lock.acquire()
    busy = await registry_locks.async_run_protected_operation(
        lock,
        storage,
        operation,
        deadline_seconds=0.01,
    )
    lock.release()
    assert busy.state is StorageRunState.BUSY
    assert operation_calls == 0


@pytest.mark.asyncio
async def test_protected_operation_start_and_completion_failures_are_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Start refusal skips I/O; callbacks and worker failures stay typed."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    monkeypatch.setattr(
        registry_locks,
        "try_acquire_io_lock",
        lambda *_args: IOLockAttempt(123),
    )
    released: list[int] = []

    def release(descriptor: int) -> bool:
        """Capture the descriptor released during preflight refusal."""
        released.append(descriptor)
        return True

    monkeypatch.setattr(registry_locks, "release_io_lock", release)
    operation_calls = 0

    def operation() -> None:
        """Record an unexpected worker start."""
        nonlocal operation_calls
        operation_calls += 1

    start_refused = await registry_locks.async_run_protected_operation(
        asyncio.Lock(),
        storage,
        operation,
        on_start=lambda: StorageRunResult(StorageRunState.BUSY),
    )
    assert start_refused.state is StorageRunState.BUSY
    assert operation_calls == 0
    assert released == [123]

    def bad_start() -> None:
        """Raise from a caller-owned preflight without exposing the exception."""
        raise RuntimeError("private preflight detail")

    start_failed = await registry_locks.async_run_protected_operation(
        asyncio.Lock(),
        storage,
        operation,
        on_start=bad_start,
    )
    assert start_failed.state is StorageRunState.UNAVAILABLE
    assert start_failed.failure_code == "operation_failed"
    assert operation_calls == 0

    completion_calls: list[StorageRunResult] = []

    def bad_completion(result: StorageRunResult) -> None:
        """Fail once, then accept the sanitized retry result."""
        completion_calls.append(result)
        if len(completion_calls) == 1:
            raise RuntimeError("private callback detail")

    worker_failed = await registry_locks.async_run_protected_operation(
        asyncio.Lock(),
        storage,
        lambda: (_ for _ in ()).throw(RuntimeError("private worker detail")),
        on_completion=bad_completion,
    )
    assert worker_failed.state is StorageRunState.UNCERTAIN
    assert worker_failed.failure_code == "completion_failed"
    assert [item.failure_code for item in completion_calls] == [
        "io_failed",
        "completion_failed",
    ]

    def invalid_start_result() -> StorageRunResult | None:
        """A callback result with the wrong contract cannot authorize I/O."""
        return cast(StorageRunResult, object())

    invalid_start = await registry_locks.async_run_protected_operation(
        asyncio.Lock(),
        storage,
        operation,
        on_start=invalid_start_result,
    )
    assert invalid_start.state is StorageRunState.UNAVAILABLE
    assert invalid_start.failure_code == "operation_failed"
    assert operation_calls == 0


@pytest.mark.asyncio
async def test_repeated_completion_failure_remains_uncertain_and_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A callback that rejects both outcomes cannot expose private exception text."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    monkeypatch.setattr(
        registry_locks,
        "try_acquire_io_lock",
        lambda *_args: IOLockAttempt(789),
    )
    monkeypatch.setattr(registry_locks, "release_io_lock", lambda _fd: True)
    callback_results: list[StorageRunResult] = []

    def fail_completion(result: StorageRunResult) -> None:
        """Reject both the original and the sanitized completion result."""
        callback_results.append(result)
        raise RuntimeError("private completion detail")

    result = await registry_locks.async_run_protected_operation(
        asyncio.Lock(),
        storage,
        lambda: "durable",
        on_completion=fail_completion,
    )

    assert result.state is StorageRunState.UNCERTAIN
    assert result.failure_code == "completion_failed"
    assert [item.failure_code for item in callback_results] == [
        None,
        "completion_failed",
    ]
    assert "private completion detail" not in caplog.text


@pytest.mark.asyncio
async def test_cancelled_protected_io_drains_and_applies_result_before_propagating(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation cannot release the async lock before worker completion."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    monkeypatch.setattr(
        registry_locks,
        "try_acquire_io_lock",
        lambda *_args: IOLockAttempt(456),
    )
    monkeypatch.setattr(registry_locks, "release_io_lock", lambda _fd: True)
    worker_started = threading.Event()
    finish_worker = threading.Event()
    completion: list[StorageRunResult] = []

    def operation() -> str:
        """Wait for the simulated durable operation to finish."""
        worker_started.set()
        assert finish_worker.wait(timeout=3)
        return "durable"

    lock = asyncio.Lock()
    task = asyncio.create_task(
        registry_locks.async_run_protected_operation(
            lock,
            storage,
            operation,
            on_completion=completion.append,
        )
    )
    assert await asyncio.to_thread(worker_started.wait, 1)
    task.cancel()
    await asyncio.sleep(0)
    assert lock.locked()
    task.cancel()
    await asyncio.sleep(0)
    assert lock.locked()
    finish_worker.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not lock.locked()
    assert len(completion) == 1
    assert completion[0].state is StorageRunState.COMPLETED
    assert completion[0].value == "durable"


@pytest.mark.asyncio
async def test_protected_operation_sanitizes_executor_and_lock_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Executor submission and async-lock errors remain typed and fail closed."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    loop = asyncio.get_running_loop()
    actual_run_in_executor = loop.run_in_executor

    def fail_acquisition(executor, function, *args):
        """Fail only the process-lock worker submission."""
        if function is registry_locks.try_acquire_io_lock:
            raise RuntimeError("private executor detail")
        return actual_run_in_executor(executor, function, *args)

    monkeypatch.setattr(loop, "run_in_executor", fail_acquisition)
    unavailable = await registry_locks.async_run_protected_operation(
        asyncio.Lock(),
        storage,
        lambda: "must-not-run",
    )
    assert unavailable.state is StorageRunState.UNAVAILABLE
    assert unavailable.failure_code == "lock_unavailable"

    class BrokenAsyncLock:
        """Represent an invalid allocator-level lock implementation."""

        async def acquire(self) -> None:
            """Raise when the allocator tries to acquire this invalid lock."""
            raise RuntimeError("private lock detail")

        def release(self) -> None:
            """Fail if code incorrectly releases a lock it did not acquire."""
            raise AssertionError("a failed acquisition cannot be released")

    broken = await registry_locks.async_run_protected_operation(
        cast(asyncio.Lock, BrokenAsyncLock()),
        storage,
        lambda: "must-not-run",
    )
    assert broken.state is StorageRunState.UNAVAILABLE
    assert broken.failure_code == "async_lock_failed"


@pytest.mark.asyncio
async def test_process_lock_release_fallback_runs_when_executor_submission_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A just-acquired OS lock is synchronously released if draining cannot start."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    released: list[int] = []
    monkeypatch.setattr(
        registry_locks,
        "try_acquire_io_lock",
        lambda *_args: IOLockAttempt(1234),
    )
    monkeypatch.setattr(
        registry_locks,
        "release_io_lock",
        _record_release(released),
    )
    loop = asyncio.get_running_loop()
    actual_run_in_executor = loop.run_in_executor

    def fail_release(executor, function, *args):
        """Refuse only the lock-release worker, not process-lock acquisition."""
        if function is registry_locks.release_io_lock:
            raise RuntimeError("private executor detail")
        return actual_run_in_executor(executor, function, *args)

    monkeypatch.setattr(loop, "run_in_executor", fail_release)
    refused = await registry_locks.async_run_protected_operation(
        asyncio.Lock(),
        storage,
        lambda: pytest.fail("start refusal must skip worker I/O"),
        on_start=lambda: StorageRunResult(StorageRunState.BUSY),
    )
    assert refused.state is StorageRunState.BUSY
    assert released == [1234]


@pytest.mark.skipif(os.name != "posix", reason="POSIX lock open failure")
@pytest.mark.parametrize(
    ("error_number", "failure_code"),
    [(errno.EACCES, "lock_unavailable"), (errno.ELOOP, "unsafe_lock")],
)
def test_lock_open_failures_are_classified_without_creating_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_number: int,
    failure_code: str,
) -> None:
    """Open failures remain sanitized and do not leave a partial lock file."""
    storage = tmp_path / ".storage"
    storage.mkdir()

    def fail_open(*_args: object, **_kwargs: object) -> int:
        """Model an unavailable process-lock path."""
        raise OSError(error_number, "private path")

    monkeypatch.setattr(registry_locks.os, "open", fail_open)
    result = store_module.try_acquire_io_lock(storage)
    assert result.descriptor is None
    assert result.failure_code == failure_code
    assert not (storage / registry_locks._IO_LOCK_NAME).exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX lock close failure")
def test_lock_close_failure_is_not_reported_as_release_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An OS close failure remains visible as a false release acknowledgement."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    attempt = store_module.try_acquire_io_lock(storage)
    assert attempt.descriptor is not None
    descriptor = attempt.descriptor

    def fail_close(_descriptor: int) -> None:
        """Model a failed OS descriptor close."""
        raise OSError(errno.EIO, "private close failure")

    monkeypatch.setattr(registry_locks.os, "close", fail_close)
    assert not registry_locks.release_io_lock(descriptor)
    monkeypatch.undo()
    os.close(descriptor)


@pytest.mark.asyncio
async def test_cancelled_lock_acquisition_drains_and_releases_late_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancelled wait cannot leak a process lock acquired by its worker."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    worker_started = threading.Event()
    release_worker = threading.Event()
    released: list[int] = []

    def acquire_later(*_args: object) -> IOLockAttempt:
        """Hold the worker until the awaiting task has been cancelled."""
        worker_started.set()
        assert release_worker.wait(timeout=3)
        return IOLockAttempt(765)

    monkeypatch.setattr(registry_locks, "try_acquire_io_lock", acquire_later)
    monkeypatch.setattr(
        registry_locks,
        "release_io_lock",
        _record_release(released),
    )
    task = asyncio.create_task(
        registry_locks.async_run_protected_operation(
            asyncio.Lock(), storage, lambda: "must-not-run"
        )
    )
    assert await asyncio.to_thread(worker_started.wait, 1)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    release_worker.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert released == [765]


@pytest.mark.asyncio
async def test_deadline_expiry_after_process_lock_acquisition_releases_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lock returned after the shared deadline is released and reported busy."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    clock_values = iter((0.0, 0.0, 0.0, 6.0))
    released: list[int] = []
    monkeypatch.setattr(registry_locks, "monotonic", lambda: next(clock_values))
    monkeypatch.setattr(
        registry_locks,
        "try_acquire_io_lock",
        lambda *_args: IOLockAttempt(876),
    )
    monkeypatch.setattr(
        registry_locks,
        "release_io_lock",
        _record_release(released),
    )

    result = await registry_locks.async_run_protected_operation(
        asyncio.Lock(), storage, lambda: "must-not-run"
    )

    assert result.state is StorageRunState.BUSY
    assert released == [876]


@pytest.mark.asyncio
async def test_run_worker_submission_failure_releases_process_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failure to submit the durable worker returns unavailable and releases."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    released: list[int] = []
    monkeypatch.setattr(
        registry_locks,
        "try_acquire_io_lock",
        lambda *_args: IOLockAttempt(987),
    )
    monkeypatch.setattr(
        registry_locks,
        "release_io_lock",
        _record_release(released),
    )
    loop = asyncio.get_running_loop()
    original_executor = loop.run_in_executor

    def fail_worker(executor, function, *args):
        """Reject only the protected-operation worker submission."""
        if function is registry_locks._run_with_io_lock:
            raise RuntimeError("private executor detail")
        return original_executor(executor, function, *args)

    monkeypatch.setattr(loop, "run_in_executor", fail_worker)
    result = await registry_locks.async_run_protected_operation(
        asyncio.Lock(), storage, lambda: "must-not-run"
    )

    assert result.state is StorageRunState.UNAVAILABLE
    assert result.failure_code == "io_failed"
    assert released == [987]


@pytest.mark.skipif(os.name != "posix", reason="POSIX fcntl availability")
def test_missing_fcntl_closes_lock_descriptor_and_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without process-lock support, no descriptor or protected write is accepted."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    opened: list[int] = []
    original_open = os.open
    original_import = builtins.__import__

    def capture_open(*args: Any, **kwargs: Any) -> int:
        """Keep the descriptor for verifying cleanup when fcntl is unavailable."""
        descriptor = original_open(*args, **kwargs)
        opened.append(descriptor)
        return descriptor

    def missing_fcntl(*args: Any, **kwargs: Any) -> Any:
        """Make process-lock support unavailable without changing other imports."""
        if args and args[0] == "fcntl":
            raise ImportError("private capability detail")
        return original_import(*args, **kwargs)

    monkeypatch.setattr(registry_locks.os, "open", cast(Any, capture_open))
    monkeypatch.setattr(builtins, "__import__", cast(Any, missing_fcntl))
    result = store_module.try_acquire_io_lock(storage)

    assert result.descriptor is None
    assert result.failure_code == "lock_unavailable"
    assert opened
    with pytest.raises(OSError):
        os.fstat(opened[0])


@pytest.mark.asyncio
async def test_failed_process_lock_worker_is_unavailable_not_authorized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unexpected acquisition worker result cannot enter protected I/O."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    operation_calls = 0

    def fail_acquisition(*_args: object) -> IOLockAttempt:
        """Model an exception in the process-lock acquisition worker."""
        raise RuntimeError("private worker detail")

    def operation() -> str:
        """Track whether protected work ran without an acquired lock."""
        nonlocal operation_calls
        operation_calls += 1
        return "must-not-run"

    monkeypatch.setattr(registry_locks, "try_acquire_io_lock", fail_acquisition)
    result = await registry_locks.async_run_protected_operation(
        asyncio.Lock(), storage, operation
    )

    assert result.state is StorageRunState.UNAVAILABLE
    assert result.failure_code == "lock_unavailable"
    assert operation_calls == 0


@pytest.mark.asyncio
async def test_expired_shared_deadline_does_not_acquire_async_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deadline exhausted before waiting performs no lock or storage work."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    clock_values = iter((0.0, 10.0))
    operation_calls = 0

    def operation() -> None:
        """Track work that must not run after the shared deadline expires."""
        nonlocal operation_calls
        operation_calls += 1

    monkeypatch.setattr(registry_locks, "monotonic", lambda: next(clock_values))
    result = await registry_locks.async_run_protected_operation(
        asyncio.Lock(), storage, operation
    )

    assert result.state is StorageRunState.BUSY
    assert operation_calls == 0
