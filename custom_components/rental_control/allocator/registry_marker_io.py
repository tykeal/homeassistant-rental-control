# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Private marker acknowledgement and exact pending-marker cleanup."""

from __future__ import annotations

import os
from pathlib import Path
import stat

from .models import RawFileState
from .registry_files import MAX_MARKER_BYTES
from .registry_files import atomic_write_bytes
from .registry_files import read_bounded_bytes


def _finalized_marker_from_pending(marker: dict[str, object]) -> dict[str, object]:
    """Build the committed marker for an exactly matched pending target."""
    return {
        "schema_version": 1,
        "activation_id": marker["activation_id"],
        "state": "finalized",
        "committed_version": "v2",
        "committed_generation": marker["target_generation"],
        "envelope_digest": marker["target_digest"],
        "committed_transaction_id": marker["target_transaction_id"],
    }


def _sync_storage_directory(storage_dir: Path) -> bool:
    """Acknowledge a marker unlink by syncing its containing directory."""
    descriptor: int | None = None
    try:
        flags = os.O_RDONLY | os.O_NOFOLLOW
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        descriptor = os.open(storage_dir, flags)
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            return False
        os.fsync(descriptor)
        return True
    except OSError:
        return False
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                # The directory fsync result, not a redundant close, is the ack.
                pass


def _remove_exact_pending_marker(storage_dir: Path, expected_bytes: bytes) -> bool:
    """Remove only the re-read exact genesis marker and fsync its parent."""
    marker_path = storage_dir / "rental_control.code_registry_protection"
    current = read_bounded_bytes(marker_path, maximum=MAX_MARKER_BYTES)
    if current.state is not RawFileState.PRESENT or current.data != expected_bytes:
        return False
    try:
        file_stat = os.lstat(marker_path)
        if not stat.S_ISREG(file_stat.st_mode) or stat.S_ISLNK(file_stat.st_mode):
            return False
        os.unlink(marker_path)
    except OSError:
        return False
    return _sync_storage_directory(storage_dir)


def _atomic_write_acknowledged(path: Path, data: bytes, maximum: int) -> bool:
    """Write one exact bounded byte string without exposing exception data."""
    result = atomic_write_bytes(path, data, maximum=maximum)
    return result.acknowledged and not result.ambiguous
