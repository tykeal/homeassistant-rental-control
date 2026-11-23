# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Private protected registry storage protocol helpers."""

from __future__ import annotations

import asyncio
import errno
import os
from pathlib import Path
import re
import secrets
import stat
from typing import Any

from ..const import STORE_CODE_REGISTRY_KEY
from .models import AtomicWriteResult
from .models import CleanupResult
from .models import CleanupState
from .models import EvidenceScanResult
from .models import ProtectionLoadState
from .models import RawFileRead
from .models import RawFileState
from .models import RecoveryAction
from .models import StorageRunState

MAX_REGISTRY_BYTES = 32 * 1024 * 1024
MAX_MARKER_BYTES = 64 * 1024
MAX_DIRECTORY_ENTRIES = 4096
_IO_LOCK_NAME = "rental_control.code_registry.io_lock"
_OWNED_TEMP_NAME = re.compile(
    r"\.rental_control\.code_registry\."
    r"(rental_control\.code_registry|rental_control\.code_registry_protection)"
    r"\.tmp-[0-9a-f]{32}-[A-Za-z0-9_-]+\Z"
)
_QUARANTINE_PREFIXES = (
    f"{STORE_CODE_REGISTRY_KEY}.corrupt.",
    "rental_control.code_registry_protection.corrupt.",
)


def _safe_storage_directory(storage_dir: Path) -> bool:
    """Require the fixed storage directory to be an actual POSIX directory."""
    if (
        os.name != "posix"
        or not hasattr(os, "O_NOFOLLOW")
        or storage_dir.name != ".storage"
    ):
        return False
    try:
        storage_stat = os.lstat(storage_dir)
    except OSError:
        return False
    return stat.S_ISDIR(storage_stat.st_mode) and not stat.S_ISLNK(storage_stat.st_mode)


def read_bounded_bytes(path: Path, *, maximum: int) -> RawFileRead:
    """Read one regular file with no-follow semantics and a strict byte cap."""
    if maximum < 0:
        return RawFileRead(RawFileState.FAILED, failure_code="invalid_limit")
    if not _safe_storage_directory(path.parent):
        return RawFileRead(RawFileState.FAILED, failure_code="unsafe_path")
    if path.name not in {
        STORE_CODE_REGISTRY_KEY,
        "rental_control.code_registry_protection",
    }:
        return RawFileRead(RawFileState.FAILED, failure_code="unsafe_path")
    flags = os.O_RDONLY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return RawFileRead(RawFileState.ABSENT)
    except OSError as error:
        code = (
            "unsafe_path"
            if error.errno in {errno.ELOOP, errno.EMLINK}
            else "read_failed"
        )
        return RawFileRead(RawFileState.FAILED, failure_code=code)
    try:
        try:
            file_stat = os.fstat(descriptor)
            if not stat.S_ISREG(file_stat.st_mode):
                return RawFileRead(RawFileState.FAILED, failure_code="unsafe_path")
            chunks: list[bytes] = []
            size = 0
            while size <= maximum:
                chunk = os.read(descriptor, min(64 * 1024, maximum + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
            if size > maximum:
                return RawFileRead(RawFileState.OVERSIZE, failure_code="oversize")
            return RawFileRead(RawFileState.PRESENT, b"".join(chunks))
        finally:
            os.close(descriptor)
    except Exception:
        return RawFileRead(RawFileState.FAILED, failure_code="read_failed")


def scan_corrupt_evidence(
    storage_dir: Path,
    *,
    maximum_entries: int = MAX_DIRECTORY_ENTRIES,
) -> EvidenceScanResult:
    """Scan all names, including unrelated entries, under a fixed entry cap."""
    if maximum_entries < 0 or not _safe_storage_directory(storage_dir):
        return EvidenceScanResult(False, failure_code="scan_unavailable")
    matches: list[str] = []
    partial_writes = False
    count = 0
    try:
        with os.scandir(storage_dir) as entries:
            for entry in entries:
                count += 1
                if count > maximum_entries:
                    return EvidenceScanResult(
                        False, tuple(matches), failure_code="scan_limit"
                    )
                if entry.name.startswith(_QUARANTINE_PREFIXES):
                    matches.append(entry.name)
                elif _OWNED_TEMP_NAME.fullmatch(entry.name):
                    partial_writes = True
    except Exception:
        return EvidenceScanResult(False, tuple(matches), failure_code="scan_failed")
    return EvidenceScanResult(
        True, tuple(sorted(matches)), partial_writes=partial_writes
    )


class _AtomicWriteFailure(Exception):
    """Internal, fixed-code rejection that aborts AtomicWriter's context."""

    def __init__(self, failure_code: str) -> None:
        """Store only an allowlisted result code."""
        self.failure_code = failure_code


def _atomic_write_preflight(path: Path, data: bytes, maximum: int) -> str | None:
    """Validate fixed target, platform, cap and existing target type."""
    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
        return "write_unavailable"
    if len(data) > maximum:
        return "serialized_oversize"
    if not _safe_storage_directory(path.parent) or path.name not in {
        STORE_CODE_REGISTRY_KEY,
        "rental_control.code_registry_protection",
    }:
        return "unsafe_path"
    try:
        target_stat = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError:
        return "write_failed"
    if target_stat is not None and (
        not stat.S_ISREG(target_stat.st_mode) or stat.S_ISLNK(target_stat.st_mode)
    ):
        return "unsafe_path"
    return None


def _owned_temp_prefix(path: Path, operation_id: str) -> str:
    """Return the exact per-kind prefix used by the RC atomic writer."""
    return f".rental_control.code_registry.{path.name}.tmp-{operation_id}-"


def atomic_write_bytes(
    path: Path,
    data: bytes,
    *,
    maximum: int,
) -> AtomicWriteResult:
    """Atomically acknowledge private exact bytes through HA's AtomicWriter."""
    failure_code = _atomic_write_preflight(path, data, maximum)
    if failure_code is not None:
        return AtomicWriteResult(False, failure_code=failure_code)

    writer: Any = None
    try:
        from atomicwrites import AtomicWriter  # type: ignore[import-untyped]

        operation_id = secrets.token_hex(16)
        prefix = _owned_temp_prefix(path, operation_id)

        class RegistryAtomicWriter(AtomicWriter):
            """Use an exact RC-owned, per-operation temporary-file prefix."""

            replacement_started = False

            def get_fileobject(
                self,
                suffix: str = "",
                prefix: str | None = None,
                dir: str | None = None,
                **kwargs: Any,
            ) -> Any:
                """Create the temp file beside the target with private mode."""
                return super().get_fileobject(
                    suffix=suffix,
                    prefix=prefix,
                    dir=dir or str(path.parent),
                    **kwargs,
                )

            def commit(self, file: Any) -> None:
                """Record the ambiguity boundary before atomic replacement."""
                self.replacement_started = True
                super().commit(file)

        writer = RegistryAtomicWriter(
            str(path),
            mode="wb",
            overwrite=True,
            prefix=prefix,
        )
        with writer.open() as file:
            if not Path(file.name).name.startswith(prefix):
                raise _AtomicWriteFailure("unsafe_temp")
            temp_stat = os.fstat(file.fileno())
            if (
                not stat.S_ISREG(temp_stat.st_mode)
                or stat.S_IMODE(temp_stat.st_mode) != 0o600
            ):
                raise _AtomicWriteFailure("unsafe_temp")
            if file.write(data) != len(data):
                raise _AtomicWriteFailure("write_failed")
        directory_flags = os.O_RDONLY | os.O_NOFOLLOW
        if hasattr(os, "O_DIRECTORY"):
            directory_flags |= os.O_DIRECTORY
        directory_fd = os.open(path.parent, directory_flags)
        try:
            directory_stat = os.fstat(directory_fd)
            if not stat.S_ISDIR(directory_stat.st_mode):
                return AtomicWriteResult(
                    False, ambiguous=True, failure_code="unsafe_path"
                )
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        final_stat = os.lstat(path)
        if not stat.S_ISREG(final_stat.st_mode) or stat.S_ISLNK(final_stat.st_mode):
            return AtomicWriteResult(False, ambiguous=True, failure_code="write_failed")
        if stat.S_IMODE(final_stat.st_mode) != 0o600:
            return AtomicWriteResult(False, ambiguous=True, failure_code="write_failed")
        return AtomicWriteResult(True)
    except _AtomicWriteFailure as error:
        return AtomicWriteResult(
            False,
            ambiguous=bool(writer and writer.replacement_started),
            failure_code=error.failure_code,
        )
    except Exception:
        return AtomicWriteResult(
            False,
            ambiguous=bool(writer and writer.replacement_started),
            failure_code="write_failed",
        )


def _cleanup_owned_temporary_files(storage_dir: Path) -> CleanupResult:
    """Delete only complete-scan, exact-prefix, private RC temp files."""
    if not _safe_storage_directory(storage_dir):
        return CleanupResult(CleanupState.UNAVAILABLE, failure_code="unsafe_path")
    candidates: list[Path] = []
    try:
        with os.scandir(storage_dir) as entries:
            for count, entry in enumerate(entries, start=1):
                if count > MAX_DIRECTORY_ENTRIES:
                    return CleanupResult(CleanupState.FAILED, failure_code="scan_limit")
                if _OWNED_TEMP_NAME.fullmatch(entry.name):
                    candidates.append(Path(entry.path))
    except Exception:
        return CleanupResult(CleanupState.FAILED, failure_code="scan_failed")

    for candidate in candidates:
        try:
            candidate_stat = os.lstat(candidate)
        except Exception:
            return CleanupResult(CleanupState.FAILED, failure_code="cleanup_failed")
        if (
            not stat.S_ISREG(candidate_stat.st_mode)
            or stat.S_ISLNK(candidate_stat.st_mode)
            or candidate_stat.st_uid != os.geteuid()
            or stat.S_IMODE(candidate_stat.st_mode) != 0o600
            or candidate_stat.st_nlink != 1
        ):
            return CleanupResult(CleanupState.FAILED, failure_code="unsafe_temp")

    removed_count = 0
    for candidate in candidates:
        try:
            os.unlink(candidate)
        except Exception:
            return CleanupResult(
                CleanupState.FAILED,
                removed_count=removed_count,
                failure_code="cleanup_failed",
            )
        removed_count += 1
    return CleanupResult(CleanupState.COMPLETED, removed_count=removed_count)


async def async_cleanup_owned_temporary_files(
    async_lock: asyncio.Lock,
    storage_dir: Path,
) -> CleanupResult:
    """Revalidate disk state under both locks before cleaning owned temps."""
    from .registry_locks import async_run_protected_operation
    from .registry_transactions import _read_recovery_plan
    from .registry_transactions import _recover_protection_files_locked

    def cleanup_after_recovery() -> CleanupResult:
        """Recover exact pending bytes, then re-read the authoritative plan."""
        recovered = _recover_protection_files_locked(storage_dir)
        if recovered.state in {
            ProtectionLoadState.PROTECTIVE_UNKNOWN,
            ProtectionLoadState.UNAVAILABLE,
        }:
            return CleanupResult(
                CleanupState.DEFERRED,
                failure_code=recovered.failure_code or "cleanup_deferred",
            )
        plan = _read_recovery_plan(storage_dir)
        if plan.action not in {
            RecoveryAction.ABSENT,
            RecoveryAction.LEGACY_V1,
            RecoveryAction.VALID_V1,
            RecoveryAction.VALID_V2,
        }:
            return CleanupResult(
                CleanupState.DEFERRED,
                failure_code=(
                    plan.failure_code
                    or plan.registry_result.failure_code
                    or "cleanup_deferred"
                ),
            )
        return _cleanup_owned_temporary_files(storage_dir)

    run_result = await async_run_protected_operation(
        async_lock,
        storage_dir,
        cleanup_after_recovery,
    )
    if run_result.state is StorageRunState.BUSY:
        return CleanupResult(CleanupState.BUSY)
    if run_result.state is StorageRunState.UNAVAILABLE:
        return CleanupResult(
            CleanupState.UNAVAILABLE,
            failure_code=run_result.failure_code or "lock_unavailable",
        )
    if run_result.state is StorageRunState.UNCERTAIN:
        cleanup_result = (
            run_result.value if isinstance(run_result.value, CleanupResult) else None
        )
        return CleanupResult(
            CleanupState.FAILED,
            removed_count=cleanup_result.removed_count if cleanup_result else 0,
            failure_code=run_result.failure_code or "cleanup_failed",
        )
    if isinstance(run_result.value, CleanupResult):
        return run_result.value
    return CleanupResult(CleanupState.FAILED, failure_code="cleanup_failed")
