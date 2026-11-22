# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Typed outcomes for the private protected-registry storage protocol."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from enum import StrEnum
from typing import Any

_STORAGE_FAILURE_CODES = frozenset(
    {
        "async_lock_failed",
        "authorization_required",
        "allocator_lock_mismatch",
        "cleanup_deferred",
        "cleanup_failed",
        "completion_failed",
        "decode_failed",
        "envelope_invalid",
        "envelope_write_failed",
        "external_restore_suspected",
        "final_marker_failed",
        "finalize_failed",
        "generation_mismatch",
        "invalid_limit",
        "io_failed",
        "legacy_invalid",
        "legacy_snapshot_unavailable",
        "lock_release_failed",
        "lock_unavailable",
        "marker_decode_failed",
        "marker_invalid",
        "marker_missing",
        "marker_mismatch",
        "marker_oversize",
        "marker_read_failed",
        "marker_remove_failed",
        "migration_not_quiescent",
        "operation_failed",
        "owner_transition_unauthorized",
        "oversize",
        "partial_history",
        "pending_marker_failed",
        "quarantine_evidence",
        "read_failed",
        "reack_failed",
        "reack_preflight_failed",
        "recovery_failed",
        "recovery_plan_invalid",
        "recovery_required",
        "registry_changed",
        "scan_failed",
        "scan_limit",
        "scan_unavailable",
        "snapshot_transition_invalid",
        "snapshot_precondition_failed",
        "snapshot_exclusions_changed",
        "snapshot_projection_failed",
        "snapshot_invalid",
        "serialization_failed",
        "serialization_invalid",
        "serialized_oversize",
        "slot_operation_busy",
        "transaction_failed",
        "transaction_id_reused",
        "transaction_preflight_failed",
        "unsafe_lock",
        "unsafe_path",
        "unsafe_temp",
        "version_unsupported",
        "write_failed",
        "write_unavailable",
    }
)


def _allowlisted_failure_code(code: str | None) -> str | None:
    """Keep arbitrary exception or path text out of storage result objects."""
    if code is None:
        return None
    if not isinstance(code, str) or code not in _STORAGE_FAILURE_CODES:
        return "operation_failed"
    return code


@dataclass(frozen=True, slots=True)
class PreparedJsonBytes:
    """Exact bounded JSON bytes and digest, or a sanitized preparation code."""

    data: bytes | None = field(repr=False)
    digest: str | None
    failure_code: str | None = None

    def __post_init__(self) -> None:
        """Normalize failures to the fixed storage-code allowlist."""
        object.__setattr__(
            self, "failure_code", _allowlisted_failure_code(self.failure_code)
        )


class RawFileState(StrEnum):
    """Classify a bounded raw storage read without collapsing absence/errors."""

    ABSENT = "absent"
    PRESENT = "present"
    OVERSIZE = "oversize"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class RawFileRead:
    """Bounded raw bytes or a fixed, sanitized read outcome."""

    state: RawFileState
    data: bytes | None = field(default=None, repr=False)
    failure_code: str | None = None

    def __post_init__(self) -> None:
        """Normalize failures to the fixed storage-code allowlist."""
        object.__setattr__(
            self, "failure_code", _allowlisted_failure_code(self.failure_code)
        )


@dataclass(frozen=True, slots=True)
class AtomicWriteResult:
    """Report exact atomic-write acknowledgement without a raw exception."""

    acknowledged: bool
    ambiguous: bool = False
    failure_code: str | None = None

    def __post_init__(self) -> None:
        """Normalize failures to the fixed storage-code allowlist."""
        object.__setattr__(
            self, "failure_code", _allowlisted_failure_code(self.failure_code)
        )


@dataclass(frozen=True, slots=True)
class EvidenceScanResult:
    """Report whether a bounded quarantine scan completed."""

    complete: bool
    matches: tuple[str, ...] = field(default=(), repr=False)
    partial_writes: bool = False
    failure_code: str | None = None

    def __post_init__(self) -> None:
        """Normalize failures to the fixed storage-code allowlist."""
        object.__setattr__(
            self, "failure_code", _allowlisted_failure_code(self.failure_code)
        )


class CleanupState(StrEnum):
    """Sanitized outcome for quiescent owned-temp maintenance."""

    COMPLETED = "completed"
    DEFERRED = "deferred"
    BUSY = "busy"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class CleanupResult:
    """Maintenance result without exposing filesystem names or exceptions."""

    state: CleanupState
    removed_count: int = 0
    failure_code: str | None = None

    def __post_init__(self) -> None:
        """Normalize failures to the fixed storage-code allowlist."""
        object.__setattr__(
            self, "failure_code", _allowlisted_failure_code(self.failure_code)
        )


@dataclass(frozen=True, slots=True)
class IOLockAttempt:
    """Nonblocking process-lock result; descriptors remain internal to RC."""

    descriptor: int | None
    busy: bool = False
    failure_code: str | None = None

    def __post_init__(self) -> None:
        """Normalize failures to the fixed storage-code allowlist."""
        object.__setattr__(
            self, "failure_code", _allowlisted_failure_code(self.failure_code)
        )


class StorageRunState(StrEnum):
    """Outcome of bounded async/process-lock acquisition."""

    COMPLETED = "completed"
    BUSY = "busy"
    UNAVAILABLE = "unavailable"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True, slots=True)
class StorageRunResult:
    """Sanitized operation result; values are internal validated data only."""

    state: StorageRunState
    value: Any = field(default=None, repr=False)
    failure_code: str | None = None

    def __post_init__(self) -> None:
        """Normalize failures to the fixed storage-code allowlist."""
        object.__setattr__(
            self, "failure_code", _allowlisted_failure_code(self.failure_code)
        )


class ProtectionLoadState(StrEnum):
    """Publicly safe state of the protected registry loader."""

    ABSENT = "absent"
    LEGACY_V1 = "legacy_v1"
    VALID_V2 = "valid_v2"
    PROTECTIVE_UNKNOWN = "protective_unknown"
    BUSY = "busy"
    UNAVAILABLE = "unavailable"


class ProtectionAuthorizationState(StrEnum):
    """Current safety gate, independent from retained observational data."""

    UNVERIFIED = "unverified"
    READY = "ready"
    PROTECTIVE_UNKNOWN = "protective_unknown"


@dataclass(frozen=True, slots=True)
class ProtectionCommitToken:
    """Bind follow-up work to one acknowledged transaction and generation."""

    transaction_id: str
    generation: int

    def __post_init__(self) -> None:
        """Reject malformed transaction IDs and boolean/nonpositive generations."""
        if (
            not isinstance(self.transaction_id, str)
            or len(self.transaction_id) != 32
            or any(
                character not in "0123456789abcdef" for character in self.transaction_id
            )
            or not isinstance(self.generation, int)
            or isinstance(self.generation, bool)
            or self.generation < 1
        ):
            raise ValueError("invalid protection commit token")


@dataclass(frozen=True, slots=True)
class ProtectionLoadResult:
    """Validated load state with only fixed, sanitized failure codes."""

    state: ProtectionLoadState
    data: dict[str, Any] | None = field(default=None, repr=False)
    transaction_id: str | None = None
    generation: int | None = None
    failure_code: str | None = None

    def __post_init__(self) -> None:
        """Normalize failures to the fixed storage-code allowlist."""
        object.__setattr__(
            self, "failure_code", _allowlisted_failure_code(self.failure_code)
        )


def protection_result_from_storage(
    result: StorageRunResult,
    *,
    fallback_failure_code: str,
) -> ProtectionLoadResult:
    """Translate worker status while preserving any known durable outcome."""
    if result.state is StorageRunState.BUSY:
        return ProtectionLoadResult(ProtectionLoadState.BUSY)
    if result.state is StorageRunState.UNAVAILABLE:
        return ProtectionLoadResult(
            ProtectionLoadState.UNAVAILABLE,
            failure_code=result.failure_code or fallback_failure_code,
        )
    if result.state is StorageRunState.UNCERTAIN:
        operation_result = (
            result.value if isinstance(result.value, ProtectionLoadResult) else None
        )
        return ProtectionLoadResult(
            ProtectionLoadState.PROTECTIVE_UNKNOWN,
            data=operation_result.data if operation_result is not None else None,
            transaction_id=(
                operation_result.transaction_id
                if operation_result is not None
                else None
            ),
            generation=(
                operation_result.generation if operation_result is not None else None
            ),
            failure_code=result.failure_code or fallback_failure_code,
        )
    if isinstance(result.value, ProtectionLoadResult):
        return result.value
    return ProtectionLoadResult(
        ProtectionLoadState.PROTECTIVE_UNKNOWN,
        failure_code=fallback_failure_code,
    )


class RecoveryAction(StrEnum):
    """Disk/marker relationship established under the shared lock."""

    ABSENT = "absent"
    LEGACY_V1 = "legacy_v1"
    VALID_V1 = "valid_v1"
    VALID_V2 = "valid_v2"
    PENDING_TARGET = "pending_target"
    PENDING_PRIOR = "pending_prior"
    PENDING_GENESIS_ABSENT = "pending_genesis_absent"
    PROTECTIVE_UNKNOWN = "protective_unknown"


@dataclass(frozen=True, slots=True)
class ProtectionRecoveryPlan:
    """Validated relation of raw bytes and marker; raw bytes stay non-repr."""

    action: RecoveryAction
    registry_result: ProtectionLoadResult
    marker: dict[str, Any] | None = field(default=None, repr=False)
    registry_bytes: bytes | None = field(default=None, repr=False)
    marker_bytes: bytes | None = field(default=None, repr=False)
    failure_code: str | None = None

    def __post_init__(self) -> None:
        """Normalize failures to the fixed storage-code allowlist."""
        object.__setattr__(
            self, "failure_code", _allowlisted_failure_code(self.failure_code)
        )
