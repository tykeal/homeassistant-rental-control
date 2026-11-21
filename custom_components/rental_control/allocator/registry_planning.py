# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Private protected registry storage protocol helpers."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .models import EvidenceScanResult
from .models import ProtectionLoadResult
from .models import ProtectionLoadState
from .models import ProtectionRecoveryPlan
from .models import RawFileRead
from .models import RawFileState
from .models import RecoveryAction
from .registry_validation import _matches_finalized_marker_record
from .registry_validation import _reject_json_constant
from .registry_validation import classify_registry_file
from .registry_validation import validate_protection_marker_shape


def _unknown_recovery_plan(code: str) -> ProtectionRecoveryPlan:
    """Create one fixed-code protective-unknown transition."""
    return ProtectionRecoveryPlan(
        RecoveryAction.PROTECTIVE_UNKNOWN,
        ProtectionLoadResult(
            ProtectionLoadState.PROTECTIVE_UNKNOWN,
            failure_code=code,
        ),
        failure_code=code,
    )


def _plan_without_marker(
    registry_raw: RawFileRead,
    registry_result: ProtectionLoadResult,
) -> ProtectionRecoveryPlan:
    """Permit only verified fresh absence or intact markerless v1."""
    if registry_raw.state is RawFileState.ABSENT:
        return ProtectionRecoveryPlan(
            RecoveryAction.ABSENT,
            ProtectionLoadResult(ProtectionLoadState.ABSENT),
        )
    if (
        registry_result.state is ProtectionLoadState.LEGACY_V1
        and registry_raw.data is not None
    ):
        return ProtectionRecoveryPlan(
            RecoveryAction.LEGACY_V1,
            registry_result,
            registry_bytes=registry_raw.data,
        )
    return _unknown_recovery_plan(registry_result.failure_code or "marker_missing")


def _plan_finalized_marker(
    registry_raw: RawFileRead,
    registry_result: ProtectionLoadResult,
    marker_raw: RawFileRead,
    marker: dict[str, Any],
) -> ProtectionRecoveryPlan:
    """Accept only a finalized marker for the exact validated envelope bytes."""
    if not _matches_finalized_marker_record(registry_result, registry_raw.data, marker):
        return _unknown_recovery_plan("marker_mismatch")
    action = (
        RecoveryAction.VALID_V1
        if marker["committed_version"] == "v1"
        else RecoveryAction.VALID_V2
    )
    return ProtectionRecoveryPlan(
        action,
        registry_result,
        marker=marker,
        registry_bytes=registry_raw.data,
        marker_bytes=marker_raw.data,
    )


def _plan_pending_marker(
    registry_raw: RawFileRead,
    registry_result: ProtectionLoadResult,
    marker_raw: RawFileRead,
    marker: dict[str, Any],
) -> ProtectionRecoveryPlan:
    """Accept only exact target bytes or the recorded predecessor."""
    registry_bytes = registry_raw.data
    target_matches = (
        registry_result.state is ProtectionLoadState.VALID_V2
        and registry_bytes is not None
        and hashlib.sha256(registry_bytes).hexdigest() == marker["target_digest"]
        and registry_result.transaction_id == marker["target_transaction_id"]
        and registry_result.generation == marker["target_generation"]
    )
    if target_matches:
        action = RecoveryAction.PENDING_TARGET
    else:
        prior = marker["prior"]
        if prior == {"state": "genesis_absent"}:
            if registry_raw.state is not RawFileState.ABSENT:
                return _unknown_recovery_plan("marker_mismatch")
            action = RecoveryAction.PENDING_GENESIS_ABSENT
        elif _matches_finalized_marker_record(registry_result, registry_bytes, prior):
            action = RecoveryAction.PENDING_PRIOR
        else:
            return _unknown_recovery_plan("marker_mismatch")
    return ProtectionRecoveryPlan(
        action,
        registry_result,
        marker=marker,
        registry_bytes=registry_bytes,
        marker_bytes=marker_raw.data,
    )


def plan_protection_recovery(
    registry_raw: RawFileRead,
    marker_raw: RawFileRead,
    evidence: EvidenceScanResult,
) -> ProtectionRecoveryPlan:
    """Select only a documented byte/marker recovery transition."""
    registry_result = classify_registry_file(registry_raw)
    if not evidence.complete:
        return _unknown_recovery_plan(evidence.failure_code or "scan_failed")
    if evidence.matches:
        return _unknown_recovery_plan("partial_history")
    if marker_raw.state is RawFileState.ABSENT:
        if evidence.partial_writes:
            return _unknown_recovery_plan("partial_history")
        return _plan_without_marker(registry_raw, registry_result)
    if marker_raw.state is not RawFileState.PRESENT or marker_raw.data is None:
        return _unknown_recovery_plan(marker_raw.failure_code or "marker_read_failed")
    try:
        marker_object = json.loads(
            marker_raw.data.decode("utf-8"), parse_constant=_reject_json_constant
        )
    except Exception:
        return _unknown_recovery_plan("marker_decode_failed")
    if not validate_protection_marker_shape(marker_object):
        return _unknown_recovery_plan("marker_invalid")
    marker: dict[str, Any] = marker_object
    if marker["state"] == "finalized":
        return _plan_finalized_marker(registry_raw, registry_result, marker_raw, marker)
    return _plan_pending_marker(registry_raw, registry_result, marker_raw, marker)
