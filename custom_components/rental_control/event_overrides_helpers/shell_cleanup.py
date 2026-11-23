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
from .models import OverrideSnapshot


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
        catalog = self._match_catalog()
        decisions = compute_eviction_decisions(
            catalog,
            event_ids,
            cal,
            coordinator.max_events,
            dict(self._slot_miss_counts),
            self._today_date(),
        )
        snapshots_by_slot = {snapshot.slot: snapshot for snapshot in catalog.snapshots}
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
            snapshot = snapshots_by_slot.get(decision.slot)
            override = self._overrides.get(decision.slot)
            if snapshot is None or override is None:
                continue
            clear_decisions.append(
                (
                    decision,
                    self._feedback_generations.get(decision.slot, 0),
                    snapshot,
                    override,
                )
            )

    for decision, generation, snapshot, override in clear_decisions:
        await _async_apply_clear_decision(
            self,
            coordinator,
            decision,
            expected_generation=generation,
            expected_snapshot=snapshot,
            expected_override=override,
        )


async def _async_apply_clear_decision(
    self: Any,
    coordinator: Any,
    decision,
    *,
    expected_generation: int | None = None,
    expected_snapshot: OverrideSnapshot | None = None,
    expected_override: dict[str, Any] | None = None,
) -> None:
    """Serialize cleanup intent, reset, and confirmed local release."""
    async with self._lock:
        current_snapshot = _snapshot_for_slot(self, decision.slot)
        current_override = self._overrides.get(decision.slot)
        if current_snapshot is None or current_override is None:
            return
        if expected_generation is None:
            expected_generation = self._feedback_generations.get(decision.slot, 0)
        if expected_snapshot is None:
            expected_snapshot = current_snapshot
        if expected_override is None:
            expected_override = current_override
    async with async_slot_operation(coordinator, decision.slot):
        await _async_apply_clear_decision_locked(
            self,
            coordinator,
            decision,
            expected_generation,
            expected_snapshot,
            expected_override,
        )


async def _async_apply_clear_decision_locked(
    self: Any,
    coordinator: Any,
    decision,
    expected_generation: int,
    expected_snapshot: OverrideSnapshot,
    expected_override: dict[str, Any],
) -> None:
    """Apply one guarded stale-slot clear without widening cleanup authority."""
    slot = decision.slot
    async with self._lock:
        if not _clear_target_is_current(
            self, slot, expected_generation, expected_snapshot, expected_override
        ):
            return
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
    async with self._lock:
        if not _clear_target_is_current(
            self, slot, expected_generation, expected_snapshot, expected_override
        ):
            return
        if verified:
            self._pending_fences[slot] = operation_id
            self._pending_clear_slots[slot] = operation_id
    result = await _dispatch_cleanup_clear(
        self, coordinator, slot, intent, expected_snapshot.slot_name
    )
    if result is None:
        return
    if not isinstance(result, self._operation_result_type):
        result = self._operation_result_type(kind="clear", slot=slot, unconfirmed=True)
    if not _clear_result_confirmed(result):
        self._logger.warning(
            "Clear not confirmed for slot %d; slot remains occupied.", slot
        )
        return
    if intent is not None and not await async_resolve_verified_clear_fence(intent):
        return
    async with self._lock:
        if verified and self._pending_fences.get(slot) != operation_id:
            return
        if not _clear_target_is_current(
            self, slot, expected_generation, expected_snapshot, expected_override
        ):
            if verified and self._pending_fences.get(slot) == operation_id:
                self._pending_fences.pop(slot, None)
                self._pending_clear_slots.pop(slot, None)
            return
        self._pending_fences.pop(slot, None)
        self._pending_clear_slots.pop(slot, None)
        self._clear_assignment(slot)
        self._assign_next_slot()


def _snapshot_for_slot(self: Any, slot: int) -> OverrideSnapshot | None:
    """Return one current non-secret cleanup identity snapshot."""
    return next(
        (
            snapshot
            for snapshot in self._match_catalog().snapshots
            if snapshot.slot == slot
        ),
        None,
    )


def _clear_target_is_current(
    self: Any,
    slot: int,
    expected_generation: int,
    expected_snapshot: OverrideSnapshot,
    expected_override: dict[str, Any],
) -> bool:
    """Require the captured slot mapping to remain current before cleanup."""
    if (
        self._feedback_generations.get(slot, 0) != expected_generation
        or self._overrides.get(slot) is not expected_override
    ):
        return False
    current = _snapshot_for_slot(self, slot)
    return current is not None and (
        current.slot_name,
        current.start_time,
        current.end_time,
        current.uid,
    ) == (
        expected_snapshot.slot_name,
        expected_snapshot.start_time,
        expected_snapshot.end_time,
        expected_snapshot.uid,
    )


async def _dispatch_cleanup_clear(
    self: Any,
    coordinator: Any,
    slot: int,
    intent: Any,
    expected_name: str,
):
    """Call the reset service, retaining occupancy on an unexpected failure."""
    try:
        if intent is None:
            return await self._module.async_fire_clear_code(
                coordinator,
                slot,
                expected_name=expected_name,
            )
        return await self._module.async_fire_clear_code(
            coordinator,
            slot,
            expected_name=expected_name,
            verified_intent=intent,
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        self._logger.warning(
            "Cleanup reset dispatch failed (failure category provider_error) "
            "for slot %d; its protective fence and occupancy remain in place. "
            "Verify the physical slot and service availability, then retry cleanup.",
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
