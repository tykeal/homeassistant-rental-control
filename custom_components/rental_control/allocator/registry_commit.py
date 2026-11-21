# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Serialized, exact-byte protected-registry transaction commits."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
from pathlib import Path
import secrets
from typing import Any
from typing import Callable

from ..const import STORE_CODE_REGISTRY_KEY
from .models import ProtectionLoadResult
from .models import ProtectionLoadState
from .models import ProtectionRecoveryPlan
from .models import RecoveryAction
from .models import StorageRunResult
from .models import StorageRunState
from .models import protection_result_from_storage
from .registry_files import MAX_MARKER_BYTES
from .registry_files import MAX_REGISTRY_BYTES
from .registry_files import _atomic_write_preflight
from .registry_locks import async_run_protected_operation
from .registry_marker_io import _atomic_write_acknowledged
from .registry_marker_io import _finalized_marker_from_pending
from .registry_safety_transitions import validate_snapshot_transition
from .registry_transactions import _read_recovery_plan
from .registry_transactions import _recover_protection_files_locked
from .registry_validation import prepare_exact_json_bytes
from .registry_validation import validate_protection_marker_shape
from .registry_validation import validate_v2_envelope_shape


@dataclass(frozen=True, slots=True)
class _PreparedTransaction:
    """Validated immutable bytes and fixed target metadata for one commit."""

    registry_path: Path
    marker_path: Path
    envelope_bytes: bytes
    pending_marker_bytes: bytes
    final_marker_bytes: bytes
    target_data: dict[str, Any]
    transaction_id: str
    generation: int


def _commit_predecessor(
    plan: ProtectionRecoveryPlan,
    *,
    legacy_migration_verified: bool,
) -> tuple[dict[str, Any] | None, str | None, str | None]:
    """Return the predecessor marker, activation ID, and transaction kind."""
    if plan.action is RecoveryAction.ABSENT:
        activation_id = secrets.token_hex(16)
        return {"state": "genesis_absent"}, activation_id, "genesis"
    if plan.action is RecoveryAction.LEGACY_V1:
        if not legacy_migration_verified:
            return None, None, "migration_not_quiescent"
        if plan.registry_bytes is None:
            return None, None, "legacy_snapshot_unavailable"
        activation_id = secrets.token_hex(16)
        prior = {
            "schema_version": 1,
            "activation_id": activation_id,
            "state": "finalized",
            "committed_version": "v1",
            "committed_generation": 0,
            "envelope_digest": hashlib.sha256(plan.registry_bytes).hexdigest(),
            "committed_transaction_id": None,
        }
        return prior, activation_id, "v1_migration"
    if plan.action is RecoveryAction.VALID_V1:
        if not legacy_migration_verified:
            return None, None, "migration_not_quiescent"
        if plan.marker is None:
            return None, None, "marker_missing"
        return plan.marker, plan.marker["activation_id"], "v1_migration"
    if plan.action is RecoveryAction.VALID_V2 and plan.marker is not None:
        return plan.marker, plan.marker["activation_id"], "commit"
    return None, None, "recovery_required"


def _resolve_commit_predecessor(
    storage_dir: Path,
    envelope: dict[str, Any],
    *,
    legacy_migration_verified: bool,
) -> tuple[ProtectionRecoveryPlan, dict[str, Any], str, str] | ProtectionLoadResult:
    """Recover pending work and identify one validated commit predecessor."""
    if not validate_v2_envelope_shape(envelope):
        return ProtectionLoadResult(
            ProtectionLoadState.PROTECTIVE_UNKNOWN,
            failure_code="envelope_invalid",
        )
    plan = _read_recovery_plan(storage_dir)
    if plan.action in {
        RecoveryAction.PENDING_TARGET,
        RecoveryAction.PENDING_PRIOR,
        RecoveryAction.PENDING_GENESIS_ABSENT,
    }:
        recovered = _recover_protection_files_locked(storage_dir)
        if recovered.state in {
            ProtectionLoadState.PROTECTIVE_UNKNOWN,
            ProtectionLoadState.UNAVAILABLE,
        }:
            return recovered
        plan = _read_recovery_plan(storage_dir)
    if plan.action is RecoveryAction.PROTECTIVE_UNKNOWN:
        return plan.registry_result
    prior, activation_id, kind = _commit_predecessor(
        plan, legacy_migration_verified=legacy_migration_verified
    )
    if prior is None or activation_id is None or kind is None:
        state = (
            ProtectionLoadState.UNAVAILABLE
            if kind == "migration_not_quiescent"
            else ProtectionLoadState.PROTECTIVE_UNKNOWN
        )
        return ProtectionLoadResult(state, failure_code=kind)
    return plan, prior, activation_id, kind


def _prepare_transaction_bytes(
    storage_dir: Path,
    envelope: dict[str, Any],
    prior: dict[str, Any],
    activation_id: str,
    kind: str,
) -> _PreparedTransaction | ProtectionLoadResult:
    """Prepare and cap all immutable bytes before the first transaction write."""
    target_data = envelope["data"]
    expected_generation = (
        1
        if prior.get("state") == "genesis_absent"
        or prior.get("committed_version") == "v1"
        else prior["committed_generation"] + 1
    )
    if target_data["generation"] != expected_generation:
        return ProtectionLoadResult(
            ProtectionLoadState.PROTECTIVE_UNKNOWN,
            failure_code="generation_mismatch",
        )
    transaction_id = target_data["transaction_id"]
    if transaction_id in {activation_id, prior.get("committed_transaction_id")}:
        return ProtectionLoadResult(
            ProtectionLoadState.PROTECTIVE_UNKNOWN,
            failure_code="transaction_id_reused",
        )
    prepared_envelope = prepare_exact_json_bytes(envelope)
    if prepared_envelope.data is None or prepared_envelope.digest is None:
        return ProtectionLoadResult(
            ProtectionLoadState.PROTECTIVE_UNKNOWN,
            failure_code=prepared_envelope.failure_code or "serialization_failed",
        )
    pending = {
        "schema_version": 1,
        "activation_id": activation_id,
        "state": "pending",
        "prior": prior,
        "target_transaction_id": transaction_id,
        "target_generation": target_data["generation"],
        "target_digest": prepared_envelope.digest,
        "kind": kind,
    }
    if not validate_protection_marker_shape(pending):
        return ProtectionLoadResult(
            ProtectionLoadState.PROTECTIVE_UNKNOWN,
            failure_code="marker_invalid",
        )
    prepared_pending = prepare_exact_json_bytes(pending, maximum=MAX_MARKER_BYTES)
    prepared_final = prepare_exact_json_bytes(
        _finalized_marker_from_pending(pending),
        maximum=MAX_MARKER_BYTES,
    )
    if prepared_pending.data is None or prepared_final.data is None:
        return ProtectionLoadResult(
            ProtectionLoadState.PROTECTIVE_UNKNOWN,
            failure_code="marker_oversize",
        )
    registry_path = storage_dir / STORE_CODE_REGISTRY_KEY
    marker_path = storage_dir / "rental_control.code_registry_protection"
    if any(
        _atomic_write_preflight(path, data, maximum) is not None
        for path, data, maximum in (
            (registry_path, prepared_envelope.data, MAX_REGISTRY_BYTES),
            (marker_path, prepared_pending.data, MAX_MARKER_BYTES),
            (marker_path, prepared_final.data, MAX_MARKER_BYTES),
        )
    ):
        return ProtectionLoadResult(
            ProtectionLoadState.PROTECTIVE_UNKNOWN,
            failure_code="transaction_preflight_failed",
        )
    return _PreparedTransaction(
        registry_path=registry_path,
        marker_path=marker_path,
        envelope_bytes=prepared_envelope.data,
        pending_marker_bytes=prepared_pending.data,
        final_marker_bytes=prepared_final.data,
        target_data=target_data,
        transaction_id=transaction_id,
        generation=target_data["generation"],
    )


def _write_prepared_transaction(
    prepared: _PreparedTransaction,
) -> ProtectionLoadResult:
    """Write pending marker, envelope, then finalized marker in that order."""
    if not _atomic_write_acknowledged(
        prepared.marker_path, prepared.pending_marker_bytes, MAX_MARKER_BYTES
    ):
        return ProtectionLoadResult(
            ProtectionLoadState.PROTECTIVE_UNKNOWN,
            failure_code="pending_marker_failed",
        )
    if not _atomic_write_acknowledged(
        prepared.registry_path, prepared.envelope_bytes, MAX_REGISTRY_BYTES
    ):
        return ProtectionLoadResult(
            ProtectionLoadState.PROTECTIVE_UNKNOWN,
            failure_code="envelope_write_failed",
        )
    if not _atomic_write_acknowledged(
        prepared.marker_path, prepared.final_marker_bytes, MAX_MARKER_BYTES
    ):
        return ProtectionLoadResult(
            ProtectionLoadState.PROTECTIVE_UNKNOWN,
            failure_code="final_marker_failed",
        )
    return ProtectionLoadResult(
        ProtectionLoadState.VALID_V2,
        data=prepared.target_data,
        transaction_id=prepared.transaction_id,
        generation=prepared.generation,
    )


def _commit_transaction_locked(
    storage_dir: Path,
    envelope: dict[str, Any],
    *,
    legacy_migration_verified: bool,
) -> ProtectionLoadResult:
    """Preflight and atomically commit one protected registry snapshot."""
    try:
        predecessor = _resolve_commit_predecessor(
            storage_dir,
            envelope,
            legacy_migration_verified=legacy_migration_verified,
        )
        if isinstance(predecessor, ProtectionLoadResult):
            return predecessor
        plan, prior, activation_id, kind = predecessor
        prior_data = (
            plan.registry_result.data
            if plan.registry_result.state is ProtectionLoadState.VALID_V2
            else None
        )
        if not validate_snapshot_transition(prior_data, envelope["data"]):
            return ProtectionLoadResult(
                ProtectionLoadState.PROTECTIVE_UNKNOWN,
                failure_code="snapshot_transition_invalid",
            )
        prepared = _prepare_transaction_bytes(
            storage_dir, envelope, prior, activation_id, kind
        )
        if isinstance(prepared, ProtectionLoadResult):
            return prepared
        return _write_prepared_transaction(prepared)
    except Exception:
        return ProtectionLoadResult(
            ProtectionLoadState.PROTECTIVE_UNKNOWN,
            failure_code="transaction_failed",
        )


async def async_commit_protection_transaction(
    async_lock: asyncio.Lock,
    storage_dir: Path,
    envelope: dict[str, Any],
    *,
    legacy_migration_verified: bool = False,
    on_start: Callable[[], StorageRunResult | None] | None = None,
    on_completion: Callable[[ProtectionLoadResult], None] | None = None,
    operation_preflight: Callable[[], ProtectionLoadResult | None] | None = None,
) -> ProtectionLoadResult:
    """Acknowledge one whole v2 snapshot before returning it to any caller.

    The legacy gate is a caller assertion that the old delayed writer is
    quiescent and its live snapshot matches the validated v1 bytes. It is
    intentionally false by default; production migration must not set it
    until those conditions are independently established.
    """
    completion_called = False

    def publish_completion(run_result: StorageRunResult) -> None:
        """Publish the sanitized transaction result to the owning store."""
        nonlocal completion_called
        completion_called = True
        if on_completion is not None:
            on_completion(
                protection_result_from_storage(
                    run_result,
                    fallback_failure_code="transaction_failed",
                )
            )

    def commit_if_authorized() -> ProtectionLoadResult:
        """Recheck the live gate under both locks before any disk operation."""
        if operation_preflight is not None:
            rejection = operation_preflight()
            if rejection is not None:
                return rejection
        return _commit_transaction_locked(
            storage_dir,
            envelope,
            legacy_migration_verified=legacy_migration_verified,
        )

    run_result = await async_run_protected_operation(
        async_lock,
        storage_dir,
        commit_if_authorized,
        on_start=on_start,
        on_completion=publish_completion,
    )
    if not completion_called:
        try:
            publish_completion(run_result)
        except Exception:
            run_result = StorageRunResult(
                StorageRunState.UNAVAILABLE,
                failure_code="completion_failed",
            )
    return protection_result_from_storage(
        run_result,
        fallback_failure_code="transaction_failed",
    )
