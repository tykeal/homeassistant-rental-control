# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Diagnostics snapshot builders for reconciliation planners."""

from __future__ import annotations

from typing import Any

from .enums import ActionKind
from .plan_models import DesiredPlan
from .plan_models import PlannedSlot
from .plan_models import Reservation
from .stateless_models import StatelessPlan


def _plan_metadata(
    plan: DesiredPlan,
    max_events: int,
    entry_id: str | None,
    lockname: str | None,
    start_slot: int | None,
) -> dict[str, Any]:
    """Return desired-plan metadata diagnostics."""
    diag: dict[str, Any] = {
        "plan_id": plan.plan_id,
        "generated_at": plan.generated_at.isoformat(),
        "max_slots": max_events,
    }
    if entry_id is not None:
        diag["entry_id"] = entry_id
    if lockname is not None:
        diag["lockname"] = lockname
    if start_slot is not None:
        diag["start_slot"] = start_slot
    return diag


def _slot_diagnostics_entry(ps: PlannedSlot) -> dict[str, Any]:
    """Return diagnostics for one planned slot."""
    entry: dict[str, Any] = {
        "desired_identity_key": ps.desired_identity_key,
        "actual_classification": ps.actual_classification,
        "action": ps.action.value,
        "blocked_reason": ps.pending_reason,
        "retry_count": ps.retry_count,
        "last_error": ps.last_error,
    }
    if ps.action is ActionKind.OVERWRITE_MANUAL_CHANGE and ps.pending_reason:
        prefix = "drifted fields: "
        if ps.pending_reason.startswith(prefix):
            entry["drift_fields"] = [
                field.strip()
                for field in ps.pending_reason[len(prefix) :].split(",")
                if field.strip()
            ]
    return entry


def _slots_diagnostics(plan: DesiredPlan) -> dict[int, dict[str, Any]]:
    """Return desired-plan per-slot diagnostics."""
    return {
        slot_num: _slot_diagnostics_entry(planned_slot)
        for slot_num, planned_slot in plan.slots.items()
    }


def _reservation_diagnostics(
    plan: DesiredPlan, reservations: list[Reservation]
) -> dict[str, dict[str, Any]]:
    """Return desired-plan per-reservation diagnostics."""
    return {
        res.identity_key: {
            "selected": res.identity_key in plan.selected,
            "protected": res.identity_key in plan.protected,
            "overflow_reason": plan.overflow.get(res.identity_key),
            "missing_count": res.missing_count,
            "assigned_slot": plan.selected.get(res.identity_key),
            "uid_aliases": sorted(res.uid_aliases),
            "booking_aliases": sorted(res.booking_aliases),
            "slot_name": res.slot_name,
            "summary": res.summary,
            "eligible": res.eligible,
            "protected_active": res.protected_active,
            "checked_out": res.checked_out,
        }
        for res in reservations
    }


def _build_plan_diagnostics_snapshot(
    plan: DesiredPlan,
    reservations: list[Reservation],
    max_events: int,
    *,
    entry_id: str | None = None,
    lockname: str | None = None,
    start_slot: int | None = None,
) -> dict[str, Any]:
    """Build a comprehensive diagnostics snapshot for *plan*."""
    existing_diag: dict[str, Any] = dict(plan.diagnostics)
    diag = _plan_metadata(plan, max_events, entry_id, lockname, start_slot)
    diag["slots"] = _slots_diagnostics(plan)
    diag["reservations"] = _reservation_diagnostics(plan, reservations)
    for key, value in existing_diag.items():
        diag.setdefault(key, value)
    return diag


def build_stateless_diagnostics(plan: StatelessPlan) -> dict[str, Any]:
    """Build diagnostics for a stateless plan."""
    return {
        "plan_id": plan.plan_id,
        "generated_at": plan.generated_at.isoformat(),
        "selected": dict(plan.selected),
        "overflow": dict(plan.overflow),
        "actions": [
            {
                "kind": action.kind.value,
                "slot": action.slot,
                "desired_id": action.desired_id,
                "reason": action.reason or action.blocked_reason,
            }
            for action in plan.actions
        ],
    }
