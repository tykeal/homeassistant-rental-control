# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Private protected registry storage protocol helpers."""

from __future__ import annotations

import asyncio
import errno
import logging
import math
import os
from pathlib import Path
import stat
from time import monotonic
from typing import Any
from typing import Callable

from .models import IOLockAttempt
from .models import StorageRunResult
from .models import StorageRunState
from .registry_files import _IO_LOCK_NAME
from .registry_files import _safe_storage_directory

_LOGGER = logging.getLogger(__name__)


def try_acquire_io_lock(
    storage_dir: Path, deadline: float | None = None
) -> IOLockAttempt:
    """Try the stable private POSIX lock file once without blocking."""
    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
        return IOLockAttempt(None, failure_code="lock_unavailable")
    if not _safe_storage_directory(storage_dir):
        return IOLockAttempt(None, failure_code="unsafe_lock")
    path = storage_dir / _IO_LOCK_NAME
    flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags, 0o600)
        file_stat = os.fstat(descriptor)
        if (
            not stat.S_ISREG(file_stat.st_mode)
            or stat.S_IMODE(file_stat.st_mode) != 0o600
            or file_stat.st_nlink != 1
            or file_stat.st_uid != os.geteuid()
        ):
            os.close(descriptor)
            descriptor = None
            return IOLockAttempt(None, failure_code="unsafe_lock")
        import fcntl

        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as error:
                if (
                    error.errno == errno.EINTR
                    and deadline is not None
                    and monotonic() < deadline
                ):
                    continue
                os.close(descriptor)
                descriptor = None
                if error.errno in {errno.EAGAIN, errno.EWOULDBLOCK}:
                    return IOLockAttempt(None, busy=True)
                return IOLockAttempt(None, failure_code="lock_unavailable")
        return IOLockAttempt(descriptor)
    except OSError as error:
        if descriptor is not None:
            _close_descriptor(descriptor)
        if error.errno in {errno.ELOOP, errno.EMLINK}:
            return IOLockAttempt(None, failure_code="unsafe_lock")
        return IOLockAttempt(None, failure_code="lock_unavailable")
    except ImportError:
        if descriptor is not None:
            _close_descriptor(descriptor)
        return IOLockAttempt(None, failure_code="lock_unavailable")


def _close_descriptor(descriptor: int) -> bool:
    """Close a descriptor and return a sanitized best-effort outcome."""
    try:
        os.close(descriptor)
    except OSError:
        return False
    return True


def release_io_lock(descriptor: int) -> bool:
    """Release and close a process lock without exposing an OS exception."""
    released = True
    try:
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_UN)
    except ImportError, OSError:
        released = False
    return _close_descriptor(descriptor) and released


def _run_with_io_lock(
    descriptor: int, operation: Callable[[], Any]
) -> StorageRunResult:
    """Run one synchronous operation and always close its process lock."""
    try:
        value = operation()
    except Exception:
        result = StorageRunResult(StorageRunState.UNCERTAIN, failure_code="io_failed")
    else:
        result = StorageRunResult(StorageRunState.COMPLETED, value=value)

    try:
        released = release_io_lock(descriptor)
    except Exception:
        released = False
    if not released:
        return StorageRunResult(
            StorageRunState.UNCERTAIN,
            value=result.value,
            failure_code="lock_release_failed",
        )
    return result


async def _drain_executor_future(
    future: asyncio.Future[Any],
) -> tuple[Any, bool]:
    """Drain submitted work despite repeated cancellation requests."""
    cancelled = False
    while True:
        try:
            return await asyncio.shield(future), cancelled
        except asyncio.CancelledError:
            if future.done() and future.cancelled():
                return None, cancelled
            cancelled = True
            task = asyncio.current_task()
            if task is not None:
                task.uncancel()
        except Exception:
            return None, cancelled


async def _release_io_lock_and_drain(descriptor: int) -> tuple[bool, bool]:
    """Release a newly acquired OS lock even if its requester is cancelled."""
    loop = asyncio.get_running_loop()
    try:
        future = loop.run_in_executor(None, release_io_lock, descriptor)
    except Exception:
        return release_io_lock(descriptor), False
    released, cancelled = await _drain_executor_future(future)
    return released is True, cancelled


async def _acquire_io_lock_until(storage_dir: Path, deadline: float) -> IOLockAttempt:
    """Retry only process-lock contention until the shared deadline."""
    loop = asyncio.get_running_loop()
    while (remaining := deadline - monotonic()) > 0:
        try:
            future = loop.run_in_executor(
                None, try_acquire_io_lock, storage_dir, deadline
            )
        except Exception:
            return IOLockAttempt(None, failure_code="lock_unavailable")
        attempt, cancelled = await _drain_executor_future(future)
        if cancelled:
            if isinstance(attempt, IOLockAttempt) and attempt.descriptor is not None:
                await _release_io_lock_and_drain(attempt.descriptor)
            raise asyncio.CancelledError
        if not isinstance(attempt, IOLockAttempt):
            return IOLockAttempt(None, failure_code="lock_unavailable")
        if not attempt.busy:
            if attempt.descriptor is not None and monotonic() >= deadline:
                _released, cancelled = await _release_io_lock_and_drain(
                    attempt.descriptor
                )
                if cancelled:
                    raise asyncio.CancelledError from None
                return IOLockAttempt(None, busy=True)
            return attempt
        remaining = deadline - monotonic()
        if remaining > 0:
            await asyncio.sleep(min(0.025, remaining))
    return IOLockAttempt(None, busy=True)


def _apply_completion_result(
    result: StorageRunResult,
    on_completion: Callable[[StorageRunResult], None] | None,
) -> StorageRunResult:
    """Apply one sanitized operation outcome before returning or propagating cancel."""
    if on_completion is None:
        return result
    try:
        on_completion(result)
    except Exception:
        result = StorageRunResult(
            StorageRunState.UNCERTAIN,
            value=result.value,
            failure_code="completion_failed",
        )
        try:
            on_completion(result)
        except Exception:
            _LOGGER.error(
                "Protected storage completion callback failed; "
                "the operation remains uncertain"
            )
    return result


async def _run_and_apply_result(
    descriptor: int,
    operation: Callable[[], Any],
    on_completion: Callable[[StorageRunResult], None] | None,
) -> StorageRunResult:
    """Drain one submitted operation, publish its result, then cancel."""
    loop = asyncio.get_running_loop()
    try:
        future = loop.run_in_executor(None, _run_with_io_lock, descriptor, operation)
    except Exception:
        _, cancelled = await _release_io_lock_and_drain(descriptor)
        result = StorageRunResult(StorageRunState.UNAVAILABLE, failure_code="io_failed")
        result = _apply_completion_result(result, on_completion)
        if cancelled:
            raise asyncio.CancelledError from None
        return result
    result, cancelled = await _drain_executor_future(future)
    if not isinstance(result, StorageRunResult):
        result = StorageRunResult(StorageRunState.UNCERTAIN, failure_code="io_failed")
    result = _apply_completion_result(result, on_completion)
    if cancelled:
        raise asyncio.CancelledError from None
    return result


async def async_run_protected_operation(
    async_lock: asyncio.Lock,
    storage_dir: Path,
    operation: Callable[[], Any],
    *,
    deadline_seconds: float = 5.0,
    on_start: Callable[[], StorageRunResult | None] | None = None,
    on_completion: Callable[[StorageRunResult], None] | None = None,
) -> StorageRunResult:
    """Run protected I/O under ordered async/process locks and one deadline.

    Cancellation while acquiring a lock stops the waiter. Once synchronous
    I/O has been submitted, it is drained before the async lock is released;
    ``on_completion`` lets the owner apply the durable result before a pending
    cancellation is re-raised.
    """
    if (
        isinstance(deadline_seconds, bool)
        or not isinstance(deadline_seconds, (int, float))
        or not math.isfinite(deadline_seconds)
        or deadline_seconds <= 0
    ):
        return StorageRunResult(StorageRunState.BUSY)

    deadline = monotonic() + min(deadline_seconds, 5.0)
    remaining = deadline - monotonic()
    if remaining <= 0:
        return StorageRunResult(StorageRunState.BUSY)
    try:
        await asyncio.wait_for(async_lock.acquire(), timeout=remaining)
    except TimeoutError:
        return StorageRunResult(StorageRunState.BUSY)
    except Exception:
        return StorageRunResult(
            StorageRunState.UNAVAILABLE, failure_code="async_lock_failed"
        )

    try:
        attempt = await _acquire_io_lock_until(storage_dir, deadline)
        if attempt.busy:
            return StorageRunResult(StorageRunState.BUSY)
        if attempt.failure_code is not None or attempt.descriptor is None:
            return StorageRunResult(
                StorageRunState.UNAVAILABLE,
                failure_code=attempt.failure_code or "lock_unavailable",
            )
        if on_start is not None:
            try:
                start_result = on_start()
            except Exception:
                _released, cancelled = await _release_io_lock_and_drain(
                    attempt.descriptor
                )
                if cancelled:
                    raise asyncio.CancelledError from None
                return StorageRunResult(
                    StorageRunState.UNAVAILABLE,
                    failure_code="operation_failed",
                )
            if start_result is not None:
                _released, cancelled = await _release_io_lock_and_drain(
                    attempt.descriptor
                )
                if cancelled:
                    raise asyncio.CancelledError from None
                if isinstance(start_result, StorageRunResult):
                    return start_result
                return StorageRunResult(
                    StorageRunState.UNAVAILABLE,
                    failure_code="operation_failed",
                )
        return await _run_and_apply_result(attempt.descriptor, operation, on_completion)
    finally:
        async_lock.release()
