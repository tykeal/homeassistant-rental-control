# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for POSIX process-shared protected-registry locking."""

from __future__ import annotations

import errno
import multiprocessing
import os
from pathlib import Path
import time

import pytest

from custom_components.rental_control.allocator import (
    protected_registry as store_module,
)


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
