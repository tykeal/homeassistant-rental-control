# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Pure diagnostics projection helpers for EventOverrides."""

from __future__ import annotations

from typing import Any

from ..reconciliation import ActionKind


def build_diagnostics_snapshot(
    plan: Any,
    pending_clear_slots: dict[int, str],
    retry_counts: dict[int, int],
    last_errors: dict[int, str],
    start_slot: int,
    max_slots: int,
) -> dict[str, Any]:
    """Return the diagnostics snapshot dict stored by the shell."""
    matched: dict[int, dict[str, Any]] = {}
    pending_corrections: dict[int, dict[str, Any]] = {}
    manual_drift_slots: dict[int, dict[str, Any]] = {}
    for slot_num, planned_slot in plan.slots.items():
        if planned_slot.desired_identity_key is not None:
            matched[slot_num] = {
                "identity_key": planned_slot.desired_identity_key,
                "action": planned_slot.action.value,
            }
        if planned_slot.action.value in {
            ActionKind.RETRY_CLEAR.value,
            ActionKind.BLOCKED.value,
        }:
            pending_corrections[slot_num] = {
                "action": planned_slot.action.value,
                "blocked_reason": planned_slot.pending_reason,
                "retry_count": planned_slot.retry_count,
            }
        if planned_slot.action is ActionKind.OVERWRITE_MANUAL_CHANGE:
            manual_drift_slots[slot_num] = {
                "action": planned_slot.action.value,
                "identity_key": planned_slot.desired_identity_key,
                "drift_fields": _parse_drift_fields(planned_slot.pending_reason),
            }
    return {
        "plan_id": plan.plan_id,
        "generated_at": plan.generated_at.isoformat(),
        "matched_slots": matched,
        "pending_corrections": pending_corrections,
        "manual_drift_slots": manual_drift_slots,
        "pending_clear_slots": sorted(pending_clear_slots.keys()),
        "slot_retry_counts": {
            slot: retry_counts.get(slot, 0)
            for slot in range(start_slot, start_slot + max_slots)
        },
        "last_slot_errors": dict(last_errors),
    }


def _parse_drift_fields(reason: str | None) -> list[str]:
    """Return diagnostics drift field names without raw PIN data."""
    if not reason or not reason.startswith("drifted fields: "):
        return []
    return [
        field.strip()
        for field in reason[len("drifted fields: ") :].split(",")
        if field.strip()
    ]
