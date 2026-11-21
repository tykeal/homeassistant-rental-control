# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Private protected registry storage protocol helpers."""

from __future__ import annotations

import asyncio
from pathlib import Path
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
from .registry_files import read_bounded_bytes
from .registry_files import scan_corrupt_evidence
from .registry_locks import async_run_protected_operation
from .registry_marker_io import _atomic_write_acknowledged
from .registry_marker_io import _finalized_marker_from_pending
from .registry_marker_io import _remove_exact_pending_marker
from .registry_planning import plan_protection_recovery
from .registry_validation import prepare_exact_json_bytes


def _recover_protection_files_locked(storage_dir: Path) -> ProtectionLoadResult:
    """Read, plan and re-acknowledge protected files under both locks."""
    registry_path = storage_dir / STORE_CODE_REGISTRY_KEY
    marker_path = storage_dir / "rental_control.code_registry_protection"
    registry_raw = read_bounded_bytes(registry_path, maximum=MAX_REGISTRY_BYTES)
    marker_raw = read_bounded_bytes(marker_path, maximum=MAX_MARKER_BYTES)
    evidence = scan_corrupt_evidence(storage_dir)
    plan = plan_protection_recovery(registry_raw, marker_raw, evidence)
    if plan.action is RecoveryAction.PROTECTIVE_UNKNOWN:
        return plan.registry_result
    if plan.action in {RecoveryAction.ABSENT, RecoveryAction.LEGACY_V1}:
        return plan.registry_result
    if plan.action is RecoveryAction.PENDING_GENESIS_ABSENT:
        if plan.marker_bytes is None or not _remove_exact_pending_marker(
            storage_dir, plan.marker_bytes
        ):
            return ProtectionLoadResult(
                ProtectionLoadState.PROTECTIVE_UNKNOWN,
                failure_code="marker_remove_failed",
            )
        return ProtectionLoadResult(ProtectionLoadState.ABSENT)

    registry_bytes = plan.registry_bytes
    marker_bytes = plan.marker_bytes
    if registry_bytes is None or marker_bytes is None or plan.marker is None:
        return ProtectionLoadResult(
            ProtectionLoadState.PROTECTIVE_UNKNOWN,
            failure_code="recovery_plan_invalid",
        )
    if plan.action in {RecoveryAction.PENDING_TARGET, RecoveryAction.PENDING_PRIOR}:
        final_marker = (
            _finalized_marker_from_pending(plan.marker)
            if plan.action is RecoveryAction.PENDING_TARGET
            else plan.marker["prior"]
        )
        prepared_final_marker = prepare_exact_json_bytes(
            final_marker, maximum=MAX_MARKER_BYTES
        )
        if prepared_final_marker.data is None:
            return ProtectionLoadResult(
                ProtectionLoadState.PROTECTIVE_UNKNOWN,
                failure_code=prepared_final_marker.failure_code or "marker_oversize",
            )
        final_marker_bytes = prepared_final_marker.data
    else:
        final_marker_bytes = marker_bytes

    if (
        _atomic_write_preflight(registry_path, registry_bytes, MAX_REGISTRY_BYTES)
        is not None
        or _atomic_write_preflight(marker_path, marker_bytes, MAX_MARKER_BYTES)
        is not None
        or _atomic_write_preflight(marker_path, final_marker_bytes, MAX_MARKER_BYTES)
        is not None
    ):
        return ProtectionLoadResult(
            ProtectionLoadState.PROTECTIVE_UNKNOWN,
            failure_code="reack_preflight_failed",
        )
    if not _atomic_write_acknowledged(marker_path, marker_bytes, MAX_MARKER_BYTES):
        return ProtectionLoadResult(
            ProtectionLoadState.PROTECTIVE_UNKNOWN,
            failure_code="reack_failed",
        )
    if not _atomic_write_acknowledged(
        registry_path, registry_bytes, MAX_REGISTRY_BYTES
    ):
        return ProtectionLoadResult(
            ProtectionLoadState.PROTECTIVE_UNKNOWN,
            failure_code="reack_failed",
        )
    if final_marker_bytes != marker_bytes and not _atomic_write_acknowledged(
        marker_path, final_marker_bytes, MAX_MARKER_BYTES
    ):
        return ProtectionLoadResult(
            ProtectionLoadState.PROTECTIVE_UNKNOWN,
            failure_code="finalize_failed",
        )
    return plan.registry_result


async def async_recover_protection_files(
    async_lock: asyncio.Lock,
    storage_dir: Path,
    *,
    on_completion: Callable[[ProtectionLoadResult], None] | None = None,
    operation_preflight: Callable[[], ProtectionLoadResult | None] | None = None,
) -> ProtectionLoadResult:
    """Load/recover registry and marker under the ordered shared locks."""
    completion_called = False

    def publish_completion(run_result: StorageRunResult) -> None:
        """Publish the sanitized recovery result to the owning store."""
        nonlocal completion_called
        completion_called = True
        if on_completion is not None:
            on_completion(
                protection_result_from_storage(
                    run_result,
                    fallback_failure_code="recovery_failed",
                )
            )

    def recover_if_authorized() -> ProtectionLoadResult:
        """Recheck live recovery permission before reading or re-acking files."""
        if operation_preflight is not None:
            rejection = operation_preflight()
            if rejection is not None:
                return rejection
        return _recover_protection_files_locked(storage_dir)

    run_result = await async_run_protected_operation(
        async_lock,
        storage_dir,
        recover_if_authorized,
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
        fallback_failure_code="recovery_failed",
    )


def _read_recovery_plan(storage_dir: Path) -> ProtectionRecoveryPlan:
    """Capture and classify both authoritative files under the process lock."""
    registry_raw = read_bounded_bytes(
        storage_dir / STORE_CODE_REGISTRY_KEY, maximum=MAX_REGISTRY_BYTES
    )
    marker_raw = read_bounded_bytes(
        storage_dir / "rental_control.code_registry_protection",
        maximum=MAX_MARKER_BYTES,
    )
    return plan_protection_recovery(
        registry_raw, marker_raw, scan_corrupt_evidence(storage_dir)
    )
