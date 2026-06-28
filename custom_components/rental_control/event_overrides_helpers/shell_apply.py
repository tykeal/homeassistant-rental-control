# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Apply-plan shell methods for EventOverrides."""

from __future__ import annotations

from typing import Any

from .apply_clear import decide_clear_result_mutation
from .apply_dispatch import classify_action
from .apply_set import build_set_operation_id
from .apply_set import build_suppression_changes
from .apply_set import build_tentative_override
from .apply_set import decide_set_result_mutation
from .apply_update import build_replacement_plan_id
from .apply_update import build_update_time_suppression
from .apply_update import parse_drift_fields


async def async_apply_plan(self, coordinator: Any, plan, res_by_key):
    """Apply a desired plan by executing slot actions."""
    async with self._lock:
        self._reconciliation_active = True
    results = []
    try:
        for action in plan.actions:
            decision = classify_action(action, res_by_key)
            if decision["warning"]:
                self._logger.warning(
                    decision["warning"], action.slot
                ) if "%d" in decision["warning"] else self._logger.warning(
                    decision["warning"]
                )
            if decision["operation"] == "clear":
                results.append(
                    await self._apply_clear(
                        coordinator, action.slot, preflight_read=action.preflight_read
                    )
                )
            elif decision["operation"] == "set":
                results.append(
                    await self._apply_set(
                        coordinator, action.slot, decision["reservation"], plan.plan_id
                    )
                )
            elif decision["operation"] == "update_times":
                results.append(
                    await self._apply_update_times(
                        coordinator, action.slot, decision["reservation"]
                    )
                )
            elif decision["operation"] == "overwrite":
                results.append(
                    await self._apply_overwrite_manual_change(
                        coordinator, action.slot, decision["reservation"], action
                    )
                )
    finally:
        self.update_diagnostics_snapshot(plan)
        async with self._lock:
            self._reconciliation_active = False
    return results


async def _apply_clear(
    self, coordinator: Any, slot: int, *, preflight_read: bool = False
):
    """Apply a CLEAR, RETRY_CLEAR, or RESET action for one slot."""
    operation_id = str(self._module.uuid.uuid4())
    async with self._lock:
        self._pending_fences[slot] = operation_id
        self._pending_clear_slots[slot] = operation_id
        expected_name = self.get_slot_name(slot) or None
    if preflight_read:
        preflight_result = self._preflight_clear_result(
            coordinator, slot, expected_name
        )
        if preflight_result is not None:
            async with self._lock:
                self._pending_fences.pop(slot, None)
                self._pending_clear_slots.pop(slot, None)
            return preflight_result
    result = await self._module.async_fire_clear_code(
        coordinator, slot, expected_name=expected_name
    )
    async with self._lock:
        if self._pending_fences.get(slot) != operation_id:
            self._logger.warning(
                "Stale clear token for slot %d (expected %s, got %s); discarding result",
                slot,
                operation_id,
                self._pending_fences.get(slot),
            )
            return self._operation_result_type(
                kind="clear", slot=slot, unconfirmed=True
            )
        mutation = decide_clear_result_mutation(result)
        if mutation["clear_slot"]:
            self._pending_fences.pop(slot, None)
            self._pending_clear_slots.pop(slot, None)
            self._clear_assignment(slot)
            self._clear_slot_error(slot)
        elif mutation["record_error"]:
            self._record_slot_error(slot, mutation["record_error"])
        return result


async def _apply_set(self, coordinator: Any, slot: int, res, plan_id: str):
    """Apply a SET or ASSIGN action for one slot."""
    if not self._slot_confirmed_empty(coordinator, slot):
        self._record_slot_error(slot, "slot not confirmed empty before set")
        return self._operation_result_type(
            kind="set", slot=slot, unconfirmed=True, error="slot not confirmed empty"
        )
    operation_id = build_set_operation_id(plan_id, slot, res.identity_key)
    async with self._lock:
        self._pending_fences[slot] = operation_id
        self._overrides[slot] = build_tentative_override(res)
        self._slot_miss_counts.pop(slot, None)
        self.suppress_state_changes(
            slot, build_suppression_changes(coordinator.lockname, slot, res)
        )
    result = await self._module.async_fire_set_code(
        coordinator,
        self._slot_event_cls(res.slot_name, res.slot_code, res.start, res.end),
        slot,
    )
    async with self._lock:
        mutation = decide_set_result_mutation(
            result, self._pending_fences.get(slot) == operation_id, slot
        )
        if mutation["status"] == "stale":
            self._logger.warning("Stale set token for slot %d; discarding result", slot)
            return self._operation_result_type(kind="set", slot=slot, unconfirmed=True)
        self._pending_fences.pop(slot, None)
        if mutation["status"] == "confirmed":
            self._clear_slot_error(slot)
        elif mutation["revert"]:
            self._clear_assignment(slot)
            self._record_slot_error(slot, mutation["record_error"])
    return result


async def _apply_update_times(self, coordinator: Any, slot: int, res):
    """Apply an UPDATE_TIMES action for one slot."""
    async with self._lock:
        self.suppress_state_changes(
            slot, build_update_time_suppression(coordinator.lockname, slot, res)
        )
    result = await self._module.async_fire_update_times(
        coordinator,
        self._slot_event_cls(res.slot_name, res.slot_code, res.start, res.end),
        slot,
    )
    if result.confirmed:
        async with self._lock:
            override = self._overrides.get(slot)
            if override is not None:
                override["start_time"], override["end_time"] = (
                    res.buffered_start,
                    res.buffered_end,
                )
    return result


async def _apply_overwrite_manual_change(
    self, coordinator: Any, slot: int, res, action
):
    """Apply an OVERWRITE_MANUAL_CHANGE or UPDATE_IN_PLACE action."""
    actual = self._actual_state_cache.get(slot) or {}
    self._logger.warning(
        "Manual/external drift detected on managed slot %d (reservation %s, desired name %r): changed fields=%s, observed name=%r, observed classification=%s, observed has_code=%s; restoring desired state.",
        slot,
        res.identity_key,
        res.display_slot_name,
        parse_drift_fields(action.reason),
        actual.get("name_state") or "(unknown)",
        actual.get("classification") or "(unknown)",
        actual.get("has_code"),
    )
    clear_result = await self._apply_clear(
        coordinator, slot, preflight_read=action.preflight_read
    )
    if not clear_result.confirmed:
        self._logger.warning(
            "Skipping replacement set for slot %d because clear was not physically confirmed",
            slot,
        )
        return clear_result
    return await self._apply_set(
        coordinator,
        slot,
        res,
        build_replacement_plan_id(slot, self._module.uuid.uuid4),
    )
