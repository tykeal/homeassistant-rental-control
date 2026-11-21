# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Apply-plan shell methods for EventOverrides."""

from __future__ import annotations

from typing import Any

from ..keymaster_operations import persisted_owner_identity_for_slot
from ..keymaster_operations import prepare_verified_intent
from ..keymaster_operations import verified_lifecycle_active
from ..keymaster_slot_operations import async_slot_operation
from ..reconciliation import ActionKind
from .apply_dispatch import classify_action
from .apply_set import build_set_operation_id
from .apply_set import build_suppression_changes
from .apply_set import build_tentative_override
from .apply_set import decide_set_result_mutation
from .apply_update import build_replacement_plan_id
from .apply_update import build_update_time_suppression
from .apply_update import parse_drift_fields
from .shell_apply_clear import apply_clear

_apply_clear = apply_clear
_CODE_REQUIRED_ACTIONS = {
    ActionKind.ASSIGN,
    ActionKind.SET,
    ActionKind.OVERWRITE_MANUAL_CHANGE,
    ActionKind.UPDATE_IN_PLACE,
    ActionKind.UPDATE_TIMES,
}
_SET_REQUIRED_PROPERTIES = (
    "switch.code_slots:{slot}.enabled",
    "switch.code_slots:{slot}.accesslimit_date_range_enabled",
    "datetime.code_slots:{slot}.accesslimit_date_range_start",
    "datetime.code_slots:{slot}.accesslimit_date_range_end",
    "text.code_slots:{slot}.pin",
    "text.code_slots:{slot}.name",
)


def _suppression_changes(
    coordinator: Any, slot: int, res: Any, intent: Any
) -> dict[str, str]:
    """Build feedback suppression with only verified exact addresses."""
    if intent is None:
        return build_suppression_changes(coordinator.lockname, slot, res)
    return {
        intent.entity_id(
            f"switch.code_slots:{slot}.accesslimit_date_range_enabled"
        ): "on",
        intent.entity_id(f"text.code_slots:{slot}.name"): res.display_slot_name,
        intent.entity_id(f"text.code_slots:{slot}.pin"): res.slot_code,
        intent.entity_id(
            f"datetime.code_slots:{slot}.accesslimit_date_range_start"
        ): res.buffered_start.isoformat(),
        intent.entity_id(
            f"datetime.code_slots:{slot}.accesslimit_date_range_end"
        ): res.buffered_end.isoformat(),
    }


def _update_time_suppression(
    coordinator: Any, slot: int, res: Any, intent: Any
) -> dict[str, str]:
    """Build date feedback suppression with verified exact addresses."""
    if intent is None:
        return build_update_time_suppression(coordinator.lockname, slot, res)
    return {
        intent.entity_id(
            f"datetime.code_slots:{slot}.accesslimit_date_range_start"
        ): res.buffered_start.isoformat(),
        intent.entity_id(
            f"datetime.code_slots:{slot}.accesslimit_date_range_end"
        ): res.buffered_end.isoformat(),
    }


def _codeless_action_warning(action: Any, res_by_key: dict[str, Any]) -> str | None:
    """Return a warning when an action would write a missing code."""
    if action.kind not in _CODE_REQUIRED_ACTIONS:
        return None
    identity_key = action.identity_key or action.desired_id
    reservation = res_by_key.get(identity_key) if identity_key else None
    if reservation is None or reservation.slot_code is not None:
        return None
    return (
        f"{action.kind.value} action for slot {action.slot} targets "
        f"codeless reservation {reservation.identity_key}; skipping"
    )


async def async_apply_plan(self, coordinator: Any, plan, res_by_key):
    """Apply a desired plan by executing slot actions."""
    async with self._lock:
        self._reconciliation_active = True
    results = []
    try:
        for action in plan.actions:
            codeless_warning = _codeless_action_warning(action, res_by_key)
            if codeless_warning is not None:
                self._logger.warning(codeless_warning)
                continue
            decision = classify_action(action, res_by_key)
            if decision["warning"]:
                if "%d" in decision["warning"]:
                    self._logger.warning(decision["warning"], action.slot)
                else:
                    self._logger.warning(decision["warning"])
            if decision["operation"] == "clear":
                results.append(
                    await self._apply_clear(
                        coordinator,
                        action.slot,
                        preflight_read=action.preflight_read,
                        owner_identity_key=action.identity_key
                        or action.desired_id
                        or persisted_owner_identity_for_slot(coordinator, action.slot),
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
                        coordinator,
                        action.slot,
                        decision["reservation"],
                        action,
                        collector=results,
                    )
                )
    finally:
        self.update_diagnostics_snapshot(plan)
        async with self._lock:
            self._reconciliation_active = False
    return results


async def _apply_set(self, coordinator: Any, slot: int, res, plan_id: str):
    """Serialize set preparation, hardware writes, and local reconciliation."""
    async with async_slot_operation(coordinator, slot):
        return await _apply_set_locked(self, coordinator, slot, res, plan_id)


async def _apply_set_locked(self, coordinator: Any, slot: int, res, plan_id: str):
    """Apply a SET or ASSIGN action for one slot."""
    verified = verified_lifecycle_active(coordinator)
    intent = None
    if verified:
        intent = await prepare_verified_intent(
            coordinator,
            slot,
            res.identity_key,
            (
                f"switch.code_slots:{slot}.enabled",
                f"switch.code_slots:{slot}.accesslimit_date_range_enabled",
                f"datetime.code_slots:{slot}.accesslimit_date_range_start",
                f"datetime.code_slots:{slot}.accesslimit_date_range_end",
                f"text.code_slots:{slot}.pin",
                f"text.code_slots:{slot}.name",
            ),
            "allocation",
        )
        if intent is None:
            return self._operation_result_type(kind="set", slot=slot, unconfirmed=True)
    if not await self._slot_confirmed_empty(coordinator, slot, intent=intent):
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
            slot,
            _suppression_changes(coordinator, slot, res, intent),
        )
    if intent is None:
        result = await self._module.async_fire_set_code(
            coordinator,
            self._slot_event_cls(res.slot_name, res.slot_code, res.start, res.end),
            slot,
        )
    else:
        result = await self._module.async_fire_set_code(
            coordinator,
            self._slot_event_cls(res.slot_name, res.slot_code, res.start, res.end),
            slot,
            verified_intent=intent,
        )
    async with self._lock:
        intent_current = intent is None or await intent.async_is_current()
        mutation = decide_set_result_mutation(
            result,
            self._pending_fences.get(slot) == operation_id and intent_current,
            slot,
        )
        if mutation["status"] == "stale":
            self._logger.warning("Stale set token for slot %d; discarding result", slot)
            return self._operation_result_type(kind="set", slot=slot, unconfirmed=True)
        self._pending_fences.pop(slot, None)
        if mutation["status"] == "confirmed":
            self._logger.debug(
                "Set confirmed for slot %d for reservation %s", slot, res.identity_key
            )
            self._clear_slot_error(slot)
        elif mutation["revert"]:
            self._logger.warning(
                "Set failed for slot %d (error: %s); reverting pre-assignment",
                slot,
                result.error,
            )
            self._clear_assignment(slot)
            self._record_slot_error(slot, mutation["record_error"])
        else:
            self._logger.debug(
                "Set unconfirmed for slot %d; keeping tentative assignment", slot
            )
    return result


async def _apply_update_times(self, coordinator: Any, slot: int, res):
    """Serialize date preparation, writes, confirmations, and publication."""
    async with async_slot_operation(coordinator, slot):
        return await _apply_update_times_locked(self, coordinator, slot, res)


async def _apply_update_times_locked(self, coordinator: Any, slot: int, res):
    """Apply an UPDATE_TIMES action for one slot."""
    verified = verified_lifecycle_active(coordinator)
    intent = None
    if verified:
        intent = await prepare_verified_intent(
            coordinator,
            slot,
            res.identity_key,
            (
                f"text.code_slots:{slot}.name",
                f"switch.code_slots:{slot}.accesslimit_date_range_enabled",
                f"datetime.code_slots:{slot}.accesslimit_date_range_start",
                f"datetime.code_slots:{slot}.accesslimit_date_range_end",
            ),
            "allocation",
        )
        if intent is None:
            return self._operation_result_type(
                kind="update_times", slot=slot, unconfirmed=True
            )
    async with self._lock:
        self.suppress_state_changes(
            slot,
            _update_time_suppression(coordinator, slot, res, intent),
        )
    if intent is None:
        result = await self._module.async_fire_update_times(
            coordinator,
            self._slot_event_cls(res.slot_name, res.slot_code, res.start, res.end),
            slot,
        )
    else:
        result = await self._module.async_fire_update_times(
            coordinator,
            self._slot_event_cls(res.slot_name, res.slot_code, res.start, res.end),
            slot,
            verified_intent=intent,
        )
    if result.confirmed:
        async with self._lock:
            if intent is not None and not await intent.async_is_current():
                return self._operation_result_type(
                    kind="update_times", slot=slot, unconfirmed=True
                )
            override = self._overrides.get(slot)
            if override is not None:
                override["start_time"], override["end_time"] = (
                    res.buffered_start,
                    res.buffered_end,
                )
    return result


async def _apply_overwrite_manual_change(
    self, coordinator: Any, slot: int, res, action, collector=None
):
    """Serialize clear-and-replacement as one indivisible slot operation."""
    async with async_slot_operation(coordinator, slot):
        return await _apply_overwrite_manual_change_locked(
            self, coordinator, slot, res, action, collector
        )


async def _apply_overwrite_manual_change_locked(
    self, coordinator: Any, slot: int, res, action, collector=None
):
    """Apply an OVERWRITE_MANUAL_CHANGE or UPDATE_IN_PLACE action.

    ``collector`` receives the intermediate confirmed clear so callers
    replaying the cycle see that the old code left the slot even when
    the replacement set is not confirmed.
    """
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
        coordinator,
        slot,
        preflight_read=action.preflight_read,
        owner_identity_key=res.identity_key,
    )
    if not clear_result.confirmed:
        self._logger.warning(
            "Skipping replacement set for slot %d because clear was not physically confirmed",
            slot,
        )
        return clear_result
    if collector is not None:
        collector.append(clear_result)
    return await self._apply_set(
        coordinator,
        slot,
        res,
        build_replacement_plan_id(slot, self._module.uuid.uuid4),
    )
