# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Retired greedy cleanup shell method for EventOverrides."""

from __future__ import annotations

import asyncio
from typing import Any

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
        for decision in decisions:
            if decision.action is EvictionAction.RESET_MISS:
                self._slot_miss_counts.pop(decision.slot, None)
                continue
            if decision.action is EvictionAction.INCREMENT_MISS:
                self._slot_miss_counts[decision.slot] = decision.new_miss_count or 0
                continue
            if decision.action is not EvictionAction.CLEAR:
                continue
            try:
                result = await self._module.async_fire_clear_code(
                    coordinator,
                    decision.slot,
                    expected_name=self.get_slot_name(decision.slot),
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                self._logger.exception(
                    "Unexpected error firing clear code for slot %d; slot remains occupied to prevent double-assignment.",
                    decision.slot,
                )
                continue
            if not isinstance(result, self._operation_result_type):
                result = self._operation_result_type(
                    kind="clear", slot=decision.slot, unconfirmed=True
                )
            if (
                not result.confirmed
                or result.failed
                or result.lingering_name
                or result.lingering_pin
            ):
                self._logger.warning(
                    "Clear not confirmed for slot %d (failed=%s, unconfirmed=%s, lingering_name=%s, lingering_pin=%s); slot remains occupied.",
                    decision.slot,
                    result.failed,
                    result.unconfirmed,
                    result.lingering_name,
                    result.lingering_pin,
                )
                continue
            self._clear_assignment(decision.slot)
            self._assign_next_slot()
