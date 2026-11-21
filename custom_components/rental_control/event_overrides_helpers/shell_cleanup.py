# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Retired greedy cleanup shell method for EventOverrides."""

from __future__ import annotations

import asyncio
from typing import Any

from ..keymaster_operations import async_resolve_verified_clear_fence
from ..keymaster_operations import persisted_owner_identity_for_slot
from ..keymaster_operations import prepare_verified_intent
from ..keymaster_operations import verified_lifecycle_active
from ..keymaster_slot_operations import async_slot_operation
from .greedy_cleanup import compute_eviction_decisions
from .models import EvictionAction


async def async_check_overrides(self, coordinator: Any, calendar=None) -> None:
    """Check overrides and clear stale slots for the retired greedy path."""
    cal = calendar if calendar is not None else coordinator.data
    if cal is None:
        self._logger.debug(
            "Calendar data not available, not checking override validity"
        )
        return
    event_ids = self._module.get_event_identities(
        coordinator, calendar=cal[: coordinator.max_events]
    )
    async with self._lock:
        assigned_slots = self._get_slots_with_values()
        if not assigned_slots:
            return
        for slot in assigned_slots:
            self._slot_has_matching_event(slot, event_ids)
        decisions = compute_eviction_decisions(
            self._match_catalog(),
            event_ids,
            cal,
            coordinator.max_events,
            dict(self._slot_miss_counts),
            self._today_date(),
        )
        clear_decisions = []
        for decision in decisions:
            if decision.action is EvictionAction.RESET_MISS:
                self._slot_miss_counts.pop(decision.slot, None)
                continue
            if decision.action is EvictionAction.INCREMENT_MISS:
                self._slot_miss_counts[decision.slot] = decision.new_miss_count or 0
                continue
            if decision.action is not EvictionAction.CLEAR:
                continue
            clear_decisions.append(decision)

    for decision in clear_decisions:
        await _async_apply_clear_decision(self, coordinator, decision)


async def _async_apply_clear_decision(self: Any, coordinator: Any, decision) -> None:
    """Serialize cleanup intent, reset, and confirmed local release."""
    async with async_slot_operation(coordinator, decision.slot):
        await _async_apply_clear_decision_locked(self, coordinator, decision)


async def _async_apply_clear_decision_locked(
    self: Any, coordinator: Any, decision
) -> None:
    """Apply one guarded stale-slot clear without widening cleanup authority."""
    slot = decision.slot
    verified = verified_lifecycle_active(coordinator)
    intent = None
    if verified:
        owner_identity_key = persisted_owner_identity_for_slot(coordinator, slot)
        if owner_identity_key is None:
            return
        intent = await prepare_verified_intent(
            coordinator,
            slot,
            owner_identity_key,
            (
                f"button.code_slots:{slot}.reset",
                f"text.code_slots:{slot}.name",
                f"text.code_slots:{slot}.pin",
            ),
            "pending_clear",
        )
        if intent is None:
            return
    operation_id = str(self._module.uuid.uuid4())
    if verified:
        async with self._lock:
            self._pending_fences[slot] = operation_id
            self._pending_clear_slots[slot] = operation_id
    result = await _dispatch_cleanup_clear(self, coordinator, slot, intent)
    if result is None:
        return
    if not isinstance(result, self._operation_result_type):
        result = self._operation_result_type(kind="clear", slot=slot, unconfirmed=True)
    if not _clear_result_confirmed(result):
        self._logger.warning(
            "Clear not confirmed for slot %d (failed=%s, unconfirmed=%s, "
            "lingering_name=%s, lingering_pin=%s); slot remains occupied.",
            slot,
            result.failed,
            result.unconfirmed,
            result.lingering_name,
            result.lingering_pin,
        )
        return
    if intent is not None and not await async_resolve_verified_clear_fence(intent):
        return
    async with self._lock:
        if verified and self._pending_fences.get(slot) != operation_id:
            return
        self._pending_fences.pop(slot, None)
        self._pending_clear_slots.pop(slot, None)
        self._clear_assignment(slot)
        self._assign_next_slot()


async def _dispatch_cleanup_clear(self: Any, coordinator: Any, slot: int, intent: Any):
    """Call the reset service, retaining occupancy on an unexpected failure."""
    try:
        if intent is None:
            return await self._module.async_fire_clear_code(
                coordinator,
                slot,
                expected_name=self.get_slot_name(slot),
            )
        return await self._module.async_fire_clear_code(
            coordinator,
            slot,
            expected_name=self.get_slot_name(slot),
            verified_intent=intent,
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        self._logger.exception(
            "Unexpected error firing clear code for slot %d; slot remains "
            "occupied to prevent double-assignment.",
            slot,
        )
        return None


def _clear_result_confirmed(result: Any) -> bool:
    """Return whether provider evidence permits local stale-slot retirement."""
    return (
        result.confirmed
        and not result.failed
        and not result.lingering_name
        and not result.lingering_pin
    )
