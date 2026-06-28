# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Pure action-dispatch helpers for apply-plan wrappers."""

from __future__ import annotations

from typing import Any

from ..reconciliation import ActionKind

_CLEAR_WARNINGS = {
    "duplicate_non_canonical": (
        "Duplicate collapse: clearing non-canonical slot %d "
        "(duplicate actual assignment)"
    ),
    "phantom": "Phantom recovery: clearing slot %d (name-only state, no usable PIN)",
    "stale": "Stale correction: clearing slot %d (expired or absent reservation)",
    "mis_assigned": (
        "Mis-assignment correction: clearing slot %d (wrong reservation in slot)"
    ),
}


def classify_action(action: Any, res_by_key: dict[str, Any]) -> dict[str, Any]:
    """Return the pure dispatch decision for a reconciliation action."""
    if action.kind in {ActionKind.NOOP, ActionKind.BLOCKED}:
        return {"operation": None, "warning": None, "reservation": None}
    if action.kind in {ActionKind.CLEAR, ActionKind.RETRY_CLEAR, ActionKind.RESET}:
        return {
            "operation": "clear",
            "warning": _CLEAR_WARNINGS.get(action.reason),
            "reservation": None,
        }
    return _reservation_operation(action, res_by_key)


def _reservation_operation(action: Any, res_by_key: dict[str, Any]) -> dict[str, Any]:
    """Resolve actions that require a reservation lookup."""
    identity_key = action.identity_key
    reservation = res_by_key.get(identity_key) if identity_key else None
    operation = {
        ActionKind.SET: "set",
        ActionKind.ASSIGN: "set",
        ActionKind.UPDATE_TIMES: "update_times",
        ActionKind.OVERWRITE_MANUAL_CHANGE: "overwrite",
        ActionKind.UPDATE_IN_PLACE: "overwrite",
    }.get(action.kind)
    if operation is None:
        return {"operation": None, "warning": None, "reservation": None}
    if reservation is None:
        return {
            "operation": None,
            "warning": _missing_reservation_warning(action.kind, action.slot),
            "reservation": None,
        }
    return {"operation": operation, "warning": None, "reservation": reservation}


def _missing_reservation_warning(kind: ActionKind, slot: int) -> str:
    """Return the existing missing-reservation warning string."""
    if kind is ActionKind.UPDATE_TIMES:
        return f"UPDATE_TIMES action for slot {slot} has no reservation; skipping"
    if kind in {ActionKind.OVERWRITE_MANUAL_CHANGE, ActionKind.UPDATE_IN_PLACE}:
        return (
            f"OVERWRITE_MANUAL_CHANGE action for slot {slot} has no reservation; "
            "skipping"
        )
    return f"SET action for slot {slot} has no reservation; skipping"
