# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Verify bounded, private registry file operations and owned-temp cleanup."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import stat
import sys
from types import ModuleType
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.rental_control.allocator import registry_files
from custom_components.rental_control.allocator import registry_marker_io
from custom_components.rental_control.allocator.models import AtomicWriteResult
from custom_components.rental_control.allocator.models import CleanupResult
from custom_components.rental_control.allocator.models import CleanupState
from custom_components.rental_control.allocator.models import ProtectionLoadResult
from custom_components.rental_control.allocator.models import ProtectionLoadState
from custom_components.rental_control.allocator.models import RawFileState
from custom_components.rental_control.allocator.models import RecoveryAction
from custom_components.rental_control.allocator.models import StorageRunResult
from custom_components.rental_control.allocator.models import StorageRunState
from custom_components.rental_control.const import STORE_CODE_REGISTRY_KEY


def _storage_dir(tmp_path: Path) -> Path:
    """Create the required Home Assistant storage directory."""
    storage_dir = tmp_path / ".storage"
    storage_dir.mkdir()
    return storage_dir


def _owned_temp(storage_dir: Path, suffix: str = "writer") -> Path:
    """Build one filename matching this integration's exact temp namespace."""
    return storage_dir / (
        f".rental_control.code_registry.{STORE_CODE_REGISTRY_KEY}."
        f"tmp-{'a' * 32}-{suffix}"
    )


def test_bounded_reads_accept_exact_bytes_and_reject_unsafe_paths(
    tmp_path: Path,
) -> None:
    """Reads distinguish absent, oversize, unsafe, and exact regular files."""
    storage_dir = _storage_dir(tmp_path)
    target = storage_dir / STORE_CODE_REGISTRY_KEY
    target.write_bytes(b"exact-bytes")
    read = registry_files.read_bounded_bytes(target, maximum=11)
    assert read.state is RawFileState.PRESENT
    assert read.data == b"exact-bytes"
    assert registry_files.read_bounded_bytes(target, maximum=10).state is (
        RawFileState.OVERSIZE
    )
    assert (
        registry_files.read_bounded_bytes(
            storage_dir / "unapproved-name", maximum=32
        ).failure_code
        == "unsafe_path"
    )
    assert registry_files.read_bounded_bytes(target, maximum=-1).failure_code == (
        "invalid_limit"
    )
    assert (
        registry_files.read_bounded_bytes(
            storage_dir / "rental_control.code_registry_protection", maximum=32
        ).state
        is RawFileState.ABSENT
    )

    link = storage_dir / "rental_control.code_registry_protection"
    link.symlink_to(target)
    assert registry_files.read_bounded_bytes(link, maximum=32).failure_code == (
        "unsafe_path"
    )
    assert registry_files.read_bounded_bytes(storage_dir, maximum=32).failure_code == (
        "unsafe_path"
    )


def test_bounded_read_reports_open_and_descriptor_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unusual OS read failures stay sanitized and do not leak descriptors."""
    storage_dir = _storage_dir(tmp_path)
    target = storage_dir / STORE_CODE_REGISTRY_KEY
    target.write_bytes(b"evidence")
    original_open = os.open

    def fail_target_open(path: str | os.PathLike[str], flags: int, *args: int) -> int:
        """Fail only the protected target open with a private OS error."""
        if Path(path) == target:
            raise PermissionError("private path")
        return original_open(path, flags, *args)

    monkeypatch.setattr(registry_files.os, "open", fail_target_open)
    assert registry_files.read_bounded_bytes(target, maximum=32).failure_code == (
        "read_failed"
    )
    monkeypatch.setattr(registry_files.os, "open", original_open)

    original_fstat = os.fstat

    def report_directory(descriptor: int) -> os.stat_result:
        """Report an unsafe directory descriptor for the protected read."""
        if descriptor != 0:
            return SimpleNamespace(st_mode=stat.S_IFDIR)  # type: ignore[return-value]
        return original_fstat(descriptor)

    monkeypatch.setattr(registry_files.os, "fstat", report_directory)
    assert registry_files.read_bounded_bytes(target, maximum=32).failure_code == (
        "unsafe_path"
    )


def test_storage_directory_metadata_and_stream_errors_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unreadable directory or file metadata is not treated as missing evidence."""
    storage_dir = _storage_dir(tmp_path)
    target = storage_dir / STORE_CODE_REGISTRY_KEY
    target.write_bytes(b"evidence")
    original_lstat = os.lstat

    def fail_directory_metadata(path: str | os.PathLike[str]) -> os.stat_result:
        """Fail metadata lookup only for the storage directory."""
        if Path(path) == storage_dir:
            raise PermissionError("private path")
        return original_lstat(path)

    monkeypatch.setattr(registry_files.os, "lstat", fail_directory_metadata)
    assert registry_files.read_bounded_bytes(target, maximum=32).failure_code == (
        "unsafe_path"
    )
    assert registry_files.scan_corrupt_evidence(storage_dir).failure_code == (
        "scan_unavailable"
    )
    assert registry_files._cleanup_owned_temporary_files(storage_dir).state is (
        CleanupState.UNAVAILABLE
    )
    assert (
        registry_files.atomic_write_bytes(target, b"replace", maximum=32).failure_code
        == "unsafe_path"
    )

    monkeypatch.setattr(registry_files.os, "lstat", original_lstat)
    original_read = os.read

    def fail_stream_read(_descriptor: int, _size: int) -> bytes:
        """Simulate a low-level read failure after opening the target."""
        raise OSError("private path")

    monkeypatch.setattr(registry_files.os, "read", fail_stream_read)
    assert registry_files.read_bounded_bytes(target, maximum=32).failure_code == (
        "read_failed"
    )
    monkeypatch.setattr(registry_files.os, "read", original_read)


def test_evidence_scan_reports_quarantines_partial_writes_and_limits(
    tmp_path: Path,
) -> None:
    """The bounded scan sees evidence but refuses incomplete directory views."""
    storage_dir = _storage_dir(tmp_path)
    (storage_dir / f"{STORE_CODE_REGISTRY_KEY}.corrupt.1").touch()
    (storage_dir / "rental_control.code_registry_protection.corrupt.2").touch()
    _owned_temp(storage_dir).touch()
    (storage_dir / "ordinary-file").touch()

    result = registry_files.scan_corrupt_evidence(storage_dir)
    assert result.complete is True
    assert result.matches == (
        f"{STORE_CODE_REGISTRY_KEY}.corrupt.1",
        "rental_control.code_registry_protection.corrupt.2",
    )
    assert result.partial_writes is True
    assert (
        registry_files.scan_corrupt_evidence(
            storage_dir, maximum_entries=1
        ).failure_code
        == "scan_limit"
    )
    assert (
        registry_files.scan_corrupt_evidence(tmp_path, maximum_entries=0).failure_code
        == "scan_unavailable"
    )
    assert (
        registry_files.scan_corrupt_evidence(
            storage_dir, maximum_entries=-1
        ).failure_code
        == "scan_unavailable"
    )


def test_evidence_scan_failure_does_not_claim_a_complete_view(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Directory enumeration errors remain distinct from an empty scan."""
    storage_dir = _storage_dir(tmp_path)

    def fail_scandir(_path: str | os.PathLike[str]) -> Any:
        """Simulate an unreadable evidence directory."""
        raise PermissionError("private path")

    monkeypatch.setattr(registry_files.os, "scandir", fail_scandir)
    result = registry_files.scan_corrupt_evidence(storage_dir)
    assert result.complete is False
    assert result.failure_code == "scan_failed"


def test_atomic_writer_persists_exact_bytes_with_private_mode(
    tmp_path: Path,
) -> None:
    """Successful writes are exact and acknowledged only after durable sync."""
    storage_dir = _storage_dir(tmp_path)
    target = storage_dir / STORE_CODE_REGISTRY_KEY
    result = registry_files.atomic_write_bytes(target, b"protected", maximum=32)

    assert result.acknowledged is True
    assert result.ambiguous is False
    assert target.read_bytes() == b"protected"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_atomic_writer_overwrites_only_a_regular_owned_target(
    tmp_path: Path,
) -> None:
    """A regular target can be atomically replaced and remains private."""
    storage_dir = _storage_dir(tmp_path)
    target = storage_dir / STORE_CODE_REGISTRY_KEY
    target.write_bytes(b"old")
    target.chmod(0o600)

    result = registry_files.atomic_write_bytes(target, b"new", maximum=32)

    assert result.acknowledged is True
    assert target.read_bytes() == b"new"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_atomic_writer_preflight_rejects_unknown_target_and_metadata_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The preflight never admits an unapproved filename or unknown target."""
    storage_dir = _storage_dir(tmp_path)
    assert (
        registry_files.atomic_write_bytes(
            storage_dir / "unapproved", b"data", maximum=32
        ).failure_code
        == "unsafe_path"
    )
    target = storage_dir / STORE_CODE_REGISTRY_KEY
    original_lstat = os.lstat

    def fail_target_metadata(path: str | os.PathLike[str]) -> os.stat_result:
        """Fail metadata inspection only for the intended target."""
        if Path(path) == target:
            raise PermissionError("private path")
        return original_lstat(path)

    monkeypatch.setattr(registry_files.os, "lstat", fail_target_metadata)
    assert (
        registry_files.atomic_write_bytes(target, b"data", maximum=32).failure_code
        == "write_failed"
    )


def test_atomic_writer_refuses_platform_without_nofollow_support(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unsupported storage platforms cannot claim a protected write."""
    storage_dir = _storage_dir(tmp_path)
    target = storage_dir / STORE_CODE_REGISTRY_KEY
    monkeypatch.setattr(registry_files.os, "name", "nt")
    result = registry_files.atomic_write_bytes(target, b"data", maximum=32)
    assert result.acknowledged is False
    assert result.failure_code == "write_unavailable"


def test_atomic_writer_rejects_non_directory_after_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An invalid directory sync handle makes the committed bytes uncertain."""
    storage_dir = _storage_dir(tmp_path)
    target = storage_dir / STORE_CODE_REGISTRY_KEY
    original_open = os.open
    original_fstat = os.fstat
    original_close = os.close
    directory_descriptor = 987655
    directory_opens = 0

    def fake_open(path: str | os.PathLike[str], flags: int, *args: int) -> int:
        """Return a controlled regular-file descriptor for directory sync."""
        nonlocal directory_opens
        if Path(path) == storage_dir:
            directory_opens += 1
            if directory_opens == 2:
                return directory_descriptor
        return original_open(path, flags, *args)

    def fake_fstat(descriptor: int) -> os.stat_result:
        """Make the controlled directory descriptor appear non-directory."""
        if descriptor == directory_descriptor:
            return SimpleNamespace(st_mode=stat.S_IFREG)  # type: ignore[return-value]
        return original_fstat(descriptor)

    def fake_close(descriptor: int) -> None:
        """Avoid closing the synthetic descriptor while closing real ones."""
        if descriptor != directory_descriptor:
            original_close(descriptor)

    monkeypatch.setattr(registry_files.os, "open", fake_open)
    monkeypatch.setattr(registry_files.os, "fstat", fake_fstat)
    monkeypatch.setattr(registry_files.os, "close", fake_close)
    result = registry_files.atomic_write_bytes(target, b"data", maximum=32)

    assert result.acknowledged is False
    assert result.ambiguous is True
    assert result.failure_code == "unsafe_path"
    assert target.read_bytes() == b"data"


@pytest.mark.parametrize(
    ("mode", "expected_code"),
    [(stat.S_IFIFO, "write_failed"), (stat.S_IFREG | 0o644, "write_failed")],
)
def test_atomic_writer_requires_private_regular_final_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: int,
    expected_code: str,
) -> None:
    """The final target is not acknowledged unless it remains private/regular."""
    storage_dir = _storage_dir(tmp_path)
    target = storage_dir / STORE_CODE_REGISTRY_KEY
    original_lstat = os.lstat
    target_checks = 0

    def report_untrusted_target(path: str | os.PathLike[str]) -> os.stat_result:
        """Report the selected nonprivate or nonregular target on recheck."""
        nonlocal target_checks
        if Path(path) == target:
            target_checks += 1
            if target_checks > 1:
                return SimpleNamespace(st_mode=mode)  # type: ignore[return-value]
        return original_lstat(path)

    monkeypatch.setattr(registry_files.os, "lstat", report_untrusted_target)
    result = registry_files.atomic_write_bytes(target, b"data", maximum=32)

    assert result.acknowledged is False
    assert result.ambiguous is True
    assert result.failure_code == expected_code
    assert target.read_bytes() == b"data"


def test_atomic_writer_rejects_oversize_and_symlink_targets(
    tmp_path: Path,
) -> None:
    """Preflight failures never replace a target or follow a link."""
    storage_dir = _storage_dir(tmp_path)
    target = storage_dir / STORE_CODE_REGISTRY_KEY
    assert (
        registry_files.atomic_write_bytes(target, b"too large", maximum=1).failure_code
        == "serialized_oversize"
    )

    elsewhere = tmp_path / "outside"
    elsewhere.write_bytes(b"preserve")
    target.symlink_to(elsewhere)
    result = registry_files.atomic_write_bytes(target, b"replace", maximum=32)
    assert result.acknowledged is False
    assert result.failure_code == "unsafe_path"
    assert elsewhere.read_bytes() == b"preserve"


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        ("wrong_name", "unsafe_temp"),
        ("wrong_mode", "unsafe_temp"),
        ("short_write", "write_failed"),
    ],
)
def test_atomic_writer_rejects_untrusted_temp_file_outcomes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    expected_code: str,
) -> None:
    """Temp-file identity, mode, and exact-byte writes are checked precommit."""
    storage_dir = _storage_dir(tmp_path)
    target = storage_dir / STORE_CODE_REGISTRY_KEY
    monkeypatch.setattr(registry_files.secrets, "token_hex", lambda _bytes: "a" * 32)
    fake_file = SimpleNamespace(
        name=(
            "untrusted.tmp"
            if failure == "wrong_name"
            else str(_owned_temp(storage_dir))
        ),
        fileno=lambda: 987654,
        write=lambda data: 0 if failure == "short_write" else len(data),
    )

    class OpenContext:
        """Return one controlled file object from the fake writer."""

        def __enter__(self) -> SimpleNamespace:
            """Expose the synthetic temporary file to the writer."""
            return fake_file

        def __exit__(self, *_args: object) -> None:
            """Close the synthetic writer context without side effects."""
            return None

    class FakeAtomicWriter:
        """Provide controlled temp results without touching production files."""

        replacement_started = False

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            """Accept writer arguments without performing a real write."""
            pass

        def open(self) -> OpenContext:
            """Return the fake context manager containing the test file."""
            return OpenContext()

    fake_atomicwrites = ModuleType("atomicwrites")
    fake_atomicwrites.AtomicWriter = FakeAtomicWriter  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "atomicwrites", fake_atomicwrites)
    original_fstat = os.fstat

    def report_temp_mode(descriptor: int) -> os.stat_result:
        """Return a controlled mode for the synthetic temporary descriptor."""
        if descriptor == 987654:
            mode = stat.S_IFREG | (0o644 if failure == "wrong_mode" else 0o600)
            return SimpleNamespace(st_mode=mode)  # type: ignore[return-value]
        return original_fstat(descriptor)

    monkeypatch.setattr(registry_files.os, "fstat", report_temp_mode)
    result = registry_files.atomic_write_bytes(target, b"protected", maximum=32)

    assert result.acknowledged is False
    assert result.ambiguous is False
    assert result.failure_code == expected_code
    assert not target.exists()


def test_atomic_writer_reports_postreplace_directory_sync_ambiguity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A directory fsync failure after replacement is never acknowledged."""
    storage_dir = _storage_dir(tmp_path)
    target = storage_dir / STORE_CODE_REGISTRY_KEY
    original_open = os.open
    original_fsync = os.fsync

    def fail_directory_open(
        path: str | os.PathLike[str], flags: int, *args: int
    ) -> int:
        """Fail opening the directory after the atomic replacement."""
        if Path(path) == storage_dir:
            raise PermissionError("private path")
        return original_open(path, flags, *args)

    monkeypatch.setattr(registry_files.os, "open", fail_directory_open)
    result = registry_files.atomic_write_bytes(target, b"protected", maximum=32)

    assert result.acknowledged is False
    assert result.ambiguous is True
    assert result.failure_code == "write_failed"
    assert target.read_bytes() == b"protected"
    monkeypatch.setattr(registry_files.os, "fsync", original_fsync)


def test_owned_temp_cleanup_removes_only_private_exact_names(
    tmp_path: Path,
) -> None:
    """Cleanup removes exact regular private temps and preserves unrelated data."""
    storage_dir = _storage_dir(tmp_path)
    owned = _owned_temp(storage_dir)
    owned.write_bytes(b"partial")
    owned.chmod(0o600)
    unrelated = storage_dir / "another-tool.tmp"
    unrelated.write_bytes(b"keep")

    result = registry_files._cleanup_owned_temporary_files(storage_dir)

    assert result.state is CleanupState.COMPLETED
    assert result.removed_count == 1
    assert not owned.exists()
    assert unrelated.read_bytes() == b"keep"


def test_owned_temp_cleanup_rejects_linked_temp_before_deleting(
    tmp_path: Path,
) -> None:
    """A matching symlink is evidence, never a cleanup target."""
    storage_dir = _storage_dir(tmp_path)
    outside = tmp_path / "outside"
    outside.write_bytes(b"preserve")
    linked_temp = _owned_temp(storage_dir)
    linked_temp.symlink_to(outside)

    result = registry_files._cleanup_owned_temporary_files(storage_dir)

    assert result.state is CleanupState.FAILED
    assert result.failure_code == "unsafe_temp"
    assert outside.read_bytes() == b"preserve"
    assert linked_temp.is_symlink()


def test_owned_temp_cleanup_refuses_unverifiable_directory_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scan errors and nonprivate matching temps keep cleanup incomplete."""
    storage_dir = _storage_dir(tmp_path)
    owned = _owned_temp(storage_dir)
    owned.write_bytes(b"partial")
    owned.chmod(0o644)
    result = registry_files._cleanup_owned_temporary_files(storage_dir)
    assert result.state is CleanupState.FAILED
    assert result.failure_code == "unsafe_temp"
    assert owned.exists()

    owned.chmod(0o600)
    original_scandir = os.scandir

    def fail_scandir(path: str | os.PathLike[str]) -> Any:
        """Fail only the scan of the target cleanup directory."""
        if Path(path) == storage_dir:
            raise PermissionError("private path")
        return original_scandir(path)

    monkeypatch.setattr(registry_files.os, "scandir", fail_scandir)
    result = registry_files._cleanup_owned_temporary_files(storage_dir)
    assert result.state is CleanupState.FAILED
    assert result.failure_code == "scan_failed"
    assert owned.exists()


def test_owned_temp_cleanup_does_not_claim_success_after_unlink_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed unlink reports the known removal count and retains evidence."""
    storage_dir = _storage_dir(tmp_path)
    owned = _owned_temp(storage_dir)
    owned.write_bytes(b"partial")
    owned.chmod(0o600)
    original_unlink = os.unlink

    def fail_owned_unlink(path: str | os.PathLike[str], *args: object) -> None:
        """Fail removal of the exact owned temporary file."""
        if Path(path) == owned:
            raise PermissionError("private path")
        original_unlink(path, *args)

    monkeypatch.setattr(registry_files.os, "unlink", fail_owned_unlink)
    result = registry_files._cleanup_owned_temporary_files(storage_dir)
    assert result.state is CleanupState.FAILED
    assert result.failure_code == "cleanup_failed"
    assert result.removed_count == 0
    assert owned.exists()


def test_owned_temp_cleanup_observes_scan_limit_before_unlinking(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A truncated directory listing cannot authorize even one cleanup."""
    storage_dir = _storage_dir(tmp_path)
    owned = _owned_temp(storage_dir)
    owned.write_bytes(b"partial")
    owned.chmod(0o600)
    monkeypatch.setattr(registry_files, "MAX_DIRECTORY_ENTRIES", 0)

    result = registry_files._cleanup_owned_temporary_files(storage_dir)

    assert result.state is CleanupState.FAILED
    assert result.failure_code == "scan_limit"
    assert owned.exists()


async def test_async_cleanup_propagates_busy_lock_without_touching_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Busy cross-process coordination defers cleanup and retains all evidence."""
    from custom_components.rental_control.allocator import registry_locks

    storage_dir = _storage_dir(tmp_path)
    owned = _owned_temp(storage_dir)
    owned.write_bytes(b"partial")
    owned.chmod(0o600)

    async def busy_operation(*_args: object, **_kwargs: object) -> object:
        """Report a busy cross-process lock without running cleanup."""
        return StorageRunResult(StorageRunState.BUSY)

    monkeypatch.setattr(registry_locks, "async_run_protected_operation", busy_operation)
    result = await registry_files.async_cleanup_owned_temporary_files(
        asyncio.Lock(), storage_dir
    )

    assert result.state is CleanupState.BUSY
    assert owned.exists()


@pytest.mark.parametrize(
    ("recovery_state", "plan_action", "expected_state", "expected_failure"),
    [
        (
            ProtectionLoadState.PROTECTIVE_UNKNOWN,
            RecoveryAction.VALID_V2,
            CleanupState.DEFERRED,
            "envelope_invalid",
        ),
        (
            ProtectionLoadState.VALID_V2,
            RecoveryAction.PROTECTIVE_UNKNOWN,
            CleanupState.DEFERRED,
            "cleanup_deferred",
        ),
        (
            ProtectionLoadState.VALID_V2,
            RecoveryAction.VALID_V2,
            CleanupState.COMPLETED,
            None,
        ),
    ],
)
async def test_async_cleanup_requires_recovered_authoritative_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recovery_state: ProtectionLoadState,
    plan_action: RecoveryAction,
    expected_state: CleanupState,
    expected_failure: str | None,
) -> None:
    """Uncertain recovery preserves temps; only a valid plan permits cleanup."""
    from custom_components.rental_control.allocator import registry_locks
    from custom_components.rental_control.allocator import registry_transactions

    storage_dir = _storage_dir(tmp_path)
    owned = _owned_temp(storage_dir)
    owned.write_bytes(b"partial")
    owned.chmod(0o600)
    recovered = ProtectionLoadResult(
        recovery_state,
        failure_code=(
            "envelope_invalid"
            if recovery_state is ProtectionLoadState.PROTECTIVE_UNKNOWN
            else None
        ),
    )
    plan = SimpleNamespace(
        action=plan_action,
        failure_code=None,
        registry_result=SimpleNamespace(failure_code=None),
    )
    monkeypatch.setattr(
        registry_transactions,
        "_recover_protection_files_locked",
        lambda _storage_dir: recovered,
    )
    monkeypatch.setattr(
        registry_transactions,
        "_read_recovery_plan",
        lambda _storage_dir: plan,
    )

    async def run_under_test_lock(
        _lock: asyncio.Lock,
        _storage_dir: Path,
        operation: object,
    ) -> StorageRunResult:
        """Run the supplied recovery operation under the test lock."""
        assert callable(operation)
        value = operation()
        return StorageRunResult(StorageRunState.COMPLETED, value=value)

    monkeypatch.setattr(
        registry_locks, "async_run_protected_operation", run_under_test_lock
    )
    result = await registry_files.async_cleanup_owned_temporary_files(
        asyncio.Lock(), storage_dir
    )

    assert result.state is expected_state
    assert result.failure_code == expected_failure
    assert owned.exists() is (expected_state is not CleanupState.COMPLETED)


@pytest.mark.parametrize(
    ("run_result", "expected_state", "expected_count", "expected_failure"),
    [
        (
            StorageRunResult(
                StorageRunState.UNAVAILABLE, failure_code="lock_unavailable"
            ),
            CleanupState.UNAVAILABLE,
            0,
            "lock_unavailable",
        ),
        (
            StorageRunResult(
                StorageRunState.UNCERTAIN,
                value=CleanupResult(CleanupState.COMPLETED, removed_count=2),
                failure_code="io_failed",
            ),
            CleanupState.FAILED,
            2,
            "io_failed",
        ),
        (
            StorageRunResult(StorageRunState.UNCERTAIN, value="invalid"),
            CleanupState.FAILED,
            0,
            "cleanup_failed",
        ),
        (
            StorageRunResult(
                StorageRunState.COMPLETED,
                value=CleanupResult(
                    CleanupState.DEFERRED, failure_code="cleanup_deferred"
                ),
            ),
            CleanupState.DEFERRED,
            0,
            "cleanup_deferred",
        ),
        (
            StorageRunResult(StorageRunState.COMPLETED, value="invalid"),
            CleanupState.FAILED,
            0,
            "cleanup_failed",
        ),
    ],
)
async def test_async_cleanup_maps_storage_completion_without_guessing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    run_result: StorageRunResult,
    expected_state: CleanupState,
    expected_count: int,
    expected_failure: str,
) -> None:
    """Cleanup status preserves the protected operation's exact outcome."""
    from custom_components.rental_control.allocator import registry_locks

    storage_dir = _storage_dir(tmp_path)

    async def completed_operation(
        *_args: object, **_kwargs: object
    ) -> StorageRunResult:
        """Return the configured storage result without filesystem I/O."""
        return run_result

    monkeypatch.setattr(
        registry_locks,
        "async_run_protected_operation",
        completed_operation,
    )
    result = await registry_files.async_cleanup_owned_temporary_files(
        asyncio.Lock(), storage_dir
    )

    assert result.state is expected_state
    assert result.removed_count == expected_count
    assert result.failure_code == expected_failure


def test_pending_marker_removal_requires_exact_reread_and_sync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only exact private pending bytes are unlinked and directory-synced."""
    storage_dir = _storage_dir(tmp_path)
    marker = storage_dir / "rental_control.code_registry_protection"
    expected = b'{"state":"pending"}'
    marker.write_bytes(expected)
    marker.chmod(0o600)

    assert (
        registry_marker_io._remove_exact_pending_marker(
            storage_dir, b"not-the-current-marker"
        )
        is False
    )
    assert marker.exists()
    assert registry_marker_io._remove_exact_pending_marker(storage_dir, expected)
    assert not marker.exists()

    marker.write_bytes(expected)
    marker.chmod(0o600)
    original_unlink = os.unlink

    def fail_marker_unlink(path: str | os.PathLike[str], *args: object) -> None:
        """Fail removal only for the exact pending marker."""
        if Path(path) == marker:
            raise PermissionError("private marker path")
        original_unlink(path, *args)

    monkeypatch.setattr(registry_marker_io.os, "unlink", fail_marker_unlink)
    assert (
        registry_marker_io._remove_exact_pending_marker(storage_dir, expected) is False
    )
    assert marker.exists()


def test_marker_removal_does_not_acknowledge_directory_sync_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unlinked marker followed by failed directory fsync is unacknowledged."""
    storage_dir = _storage_dir(tmp_path)
    marker = storage_dir / "rental_control.code_registry_protection"
    expected = b'{"state":"pending"}'
    marker.write_bytes(expected)
    marker.chmod(0o600)

    monkeypatch.setattr(
        registry_marker_io.os,
        "fsync",
        lambda _descriptor: (_ for _ in ()).throw(OSError("private fsync error")),
    )
    assert (
        registry_marker_io._remove_exact_pending_marker(storage_dir, expected) is False
    )
    assert not marker.exists()


def test_pending_marker_sync_rejects_non_directory_and_write_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Directory sync handles are validated and errors become false."""
    storage_dir = _storage_dir(tmp_path)
    original_open = os.open
    original_fstat = os.fstat
    descriptor = 987656
    monkeypatch.setattr(
        registry_marker_io.os,
        "open",
        lambda path, flags: (
            descriptor if Path(path) == storage_dir else original_open(path, flags)
        ),
    )
    monkeypatch.setattr(
        registry_marker_io.os,
        "fstat",
        lambda fd: (
            SimpleNamespace(st_mode=stat.S_IFREG)
            if fd == descriptor
            else original_fstat(fd)
        ),
    )
    monkeypatch.setattr(registry_marker_io.os, "close", lambda _fd: None)
    assert registry_marker_io._sync_storage_directory(storage_dir) is False

    def fail_open(_path: object, _flags: int) -> int:
        """Simulate an unavailable marker-directory descriptor."""
        raise OSError("private directory error")

    monkeypatch.setattr(registry_marker_io.os, "open", fail_open)
    assert registry_marker_io._sync_storage_directory(storage_dir) is False


def test_marker_serialization_and_atomic_acknowledgement_are_exact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pending marker is finalized from its exact target and write status."""
    marker = {
        "activation_id": "activation",
        "target_generation": 7,
        "target_digest": "digest",
        "target_transaction_id": "transaction",
    }
    assert registry_marker_io._finalized_marker_from_pending(marker) == {
        "schema_version": 1,
        "activation_id": "activation",
        "state": "finalized",
        "committed_version": "v2",
        "committed_generation": 7,
        "envelope_digest": "digest",
        "committed_transaction_id": "transaction",
    }
    path = _storage_dir(tmp_path) / "rental_control.code_registry_protection"
    monkeypatch.setattr(
        registry_marker_io,
        "atomic_write_bytes",
        lambda *_args, **_kwargs: AtomicWriteResult(True),
    )
    assert registry_marker_io._atomic_write_acknowledged(path, b"marker", 1024)
    monkeypatch.setattr(
        registry_marker_io,
        "atomic_write_bytes",
        lambda *_args, **_kwargs: AtomicWriteResult(
            False, ambiguous=True, failure_code="write_failed"
        ),
    )
    assert not registry_marker_io._atomic_write_acknowledged(path, b"marker", 1024)
