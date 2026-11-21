# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Convenience exports for protected-registry storage and recovery tests."""

from __future__ import annotations

import os

from .code_codec import code_ref_for
from .code_codec import decode_code
from .code_codec import encode_code
from .models import AtomicWriteResult
from .models import CleanupResult
from .models import CleanupState
from .models import EvidenceScanResult
from .models import IOLockAttempt
from .models import PreparedJsonBytes
from .models import ProtectionLoadResult
from .models import ProtectionLoadState
from .models import ProtectionRecoveryPlan
from .models import RawFileRead
from .models import RawFileState
from .models import RecoveryAction
from .models import StorageRunResult
from .models import StorageRunState
from .registry_commit import async_commit_protection_transaction
from .registry_files import _IO_LOCK_NAME
from .registry_files import MAX_DIRECTORY_ENTRIES
from .registry_files import MAX_MARKER_BYTES
from .registry_files import MAX_REGISTRY_BYTES
from .registry_files import _owned_temp_prefix
from .registry_files import async_cleanup_owned_temporary_files
from .registry_files import atomic_write_bytes
from .registry_files import read_bounded_bytes
from .registry_files import scan_corrupt_evidence
from .registry_locks import async_run_protected_operation
from .registry_locks import release_io_lock
from .registry_locks import try_acquire_io_lock
from .registry_planning import plan_protection_recovery
from .registry_transactions import async_recover_protection_files
from .registry_validation import classify_registry_file
from .registry_validation import prepare_exact_json_bytes
from .registry_validation import validate_protection_marker_shape
from .registry_validation import validate_v2_envelope_shape

__all__ = [
    "AtomicWriteResult",
    "CleanupResult",
    "CleanupState",
    "EvidenceScanResult",
    "IOLockAttempt",
    "MAX_DIRECTORY_ENTRIES",
    "MAX_MARKER_BYTES",
    "MAX_REGISTRY_BYTES",
    "PreparedJsonBytes",
    "ProtectionLoadResult",
    "ProtectionLoadState",
    "ProtectionRecoveryPlan",
    "RawFileRead",
    "RawFileState",
    "RecoveryAction",
    "StorageRunResult",
    "StorageRunState",
    "_IO_LOCK_NAME",
    "_owned_temp_prefix",
    "async_cleanup_owned_temporary_files",
    "async_commit_protection_transaction",
    "async_recover_protection_files",
    "async_run_protected_operation",
    "atomic_write_bytes",
    "classify_registry_file",
    "code_ref_for",
    "decode_code",
    "encode_code",
    "os",
    "plan_protection_recovery",
    "prepare_exact_json_bytes",
    "read_bounded_bytes",
    "release_io_lock",
    "scan_corrupt_evidence",
    "try_acquire_io_lock",
    "validate_protection_marker_shape",
    "validate_v2_envelope_shape",
]
