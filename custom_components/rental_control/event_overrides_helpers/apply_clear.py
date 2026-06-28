# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Pure clear-application decisions for EventOverrides."""

from __future__ import annotations

from typing import Any

from ..util import is_cleared_keymaster_text_state
from ..util import is_unreadable_keymaster_text_state


def decide_clear_preflight(
    fresh: tuple[Any, Any] | None,
    expected_name: str | None,
    actual_state: dict[str, Any],
) -> dict[str, Any]:
    """Return the pure clear preflight outcome."""
    if fresh is None:
        return {"status": "unconfirmed", "reason": "read_failed"}
    fresh_name, fresh_pin = fresh
    if not isinstance(fresh_name, (str, type(None))) or not isinstance(
        fresh_pin, (str, type(None))
    ):
        return {"status": "unconfirmed", "reason": "non_string"}
    if is_unreadable_keymaster_text_state(
        fresh_name
    ) or is_unreadable_keymaster_text_state(fresh_pin):
        return {"status": "unconfirmed", "reason": "unreadable"}
    fresh_empty = is_cleared_keymaster_text_state(
        fresh_name
    ) and is_cleared_keymaster_text_state(fresh_pin)
    if fresh_empty:
        return {"status": "confirmed", "reason": "already_empty"}
    planned_name = (
        actual_state.get("name_state")
        if "name_state" in actual_state
        else expected_name
    )
    planned_has_code = actual_state.get("has_code")
    fresh_name_text = "" if fresh_name is None else str(fresh_name)
    fresh_has_code = not is_cleared_keymaster_text_state(fresh_pin)
    if planned_name:
        if str(planned_name) != fresh_name_text:
            return {"status": "unconfirmed", "reason": "name_changed"}
    elif not is_cleared_keymaster_text_state(fresh_name):
        return {"status": "unconfirmed", "reason": "name_appeared"}
    if isinstance(planned_has_code, bool) and planned_has_code != fresh_has_code:
        return {"status": "unconfirmed", "reason": "pin_changed"}
    return {"status": "proceed", "reason": None}


def decide_clear_result_mutation(result: Any) -> dict[str, Any]:
    """Return the in-memory mutation decision for a clear result."""
    if result.confirmed:
        return {"clear_slot": True, "record_error": None}
    if result.failed:
        return {"clear_slot": False, "record_error": result.error or "clear failed"}
    if result.lingering_name or result.lingering_pin:
        return {
            "clear_slot": False,
            "record_error": (
                "lingering state after clear: "
                f"name={result.lingering_name} pin={result.lingering_pin}"
            ),
        }
    return {"clear_slot": False, "record_error": None}
