# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Pure set/assign helpers for EventOverrides."""

from __future__ import annotations

import hashlib
from typing import Any


def build_set_operation_id(plan_id: str, slot: int, identity_key: str) -> str:
    """Return the deterministic set operation fence token."""
    digest = hashlib.sha256(identity_key.encode()).hexdigest()[:8]
    return f"{plan_id}-set-{slot}-{digest}"


def build_tentative_override(res: Any) -> dict[str, Any]:
    """Return the tentative in-memory override for a reservation."""
    return {
        "slot_name": res.slot_name,
        "slot_code": res.slot_code,
        "start_time": res.buffered_start,
        "end_time": res.buffered_end,
    }


def build_suppression_changes(lockname: str, slot: int, res: Any) -> dict[str, str]:
    """Return the state-suppression payload for a set operation."""
    return {
        f"switch.{lockname}_code_slot_{slot}_use_date_range_limits": "on",
        f"text.{lockname}_code_slot_{slot}_name": res.display_slot_name,
        f"text.{lockname}_code_slot_{slot}_pin": res.slot_code,
        f"datetime.{lockname}_code_slot_{slot}_date_range_start": res.buffered_start.isoformat(),
        f"datetime.{lockname}_code_slot_{slot}_date_range_end": res.buffered_end.isoformat(),
    }


def decide_set_result_mutation(result: Any, token: bool, slot: int) -> dict[str, Any]:
    """Return the in-memory mutation decision for a set result."""
    del slot
    if not token:
        return {"status": "stale", "revert": False, "record_error": None}
    if result.confirmed:
        return {"status": "confirmed", "revert": False, "record_error": None}
    if result.failed:
        return {
            "status": "failed",
            "revert": True,
            "record_error": result.error or "set failed",
        }
    return {"status": "unconfirmed", "revert": False, "record_error": None}
