# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Fail-closed allocator reconciliation integration tests."""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import homeassistant.util.dt as dt_util
import pytest

from custom_components.rental_control.event_overrides import EventOverrides
from custom_components.rental_control.reconciliation import ActionKind
from custom_components.rental_control.reconciliation import ManagedSlot
from custom_components.rental_control.reconciliation import Reservation
from custom_components.rental_control.reconciliation import SlotStatus
from custom_components.rental_control.reconciliation import compute_desired_plan


@pytest.mark.asyncio
@pytest.mark.parametrize("actual_code_present", [True, None])
async def test_codeless_matched_slot_never_clears_working_code(
    actual_code_present: bool | None,
) -> None:
    """Hazard 1: a codeless matched reservation holds the physical code."""
    start = datetime(2026, 9, 1, 16, tzinfo=dt_util.UTC)
    end = start + timedelta(days=4)
    physical_code = "2468"
    reservation = Reservation(
        identity_key="hazard-1",
        start=start,
        end=end,
        buffered_start=start,
        buffered_end=end,
        summary="Hazard Guest",
        slot_name="Hazard Guest",
        display_slot_name="RC Hazard Guest",
        slot_code=None,
        code_source="unallocated",
    )
    managed_slot = ManagedSlot(
        slot=1,
        managed=True,
        status=SlotStatus.OCCUPIED,
        actual_name="RC Hazard Guest",
        actual_code=physical_code,
        actual_code_present=actual_code_present,
        actual_start=start,
        actual_end=end,
        persisted_identity_key=reservation.identity_key,
    )

    plan = compute_desired_plan(
        [reservation],
        [managed_slot],
        max_events=1,
        plan_id="hazard-1",
        generated_at=start,
    )

    forbidden = {
        ActionKind.CLEAR,
        ActionKind.RESET,
        ActionKind.OVERWRITE_MANUAL_CHANGE,
        ActionKind.SET,
    }
    assert plan.selected == {reservation.identity_key: 1}
    assert plan.slots[1].action is ActionKind.NOOP
    assert all(action.kind not in forbidden for action in plan.actions)
    assert plan.validate({reservation.identity_key: reservation}) == []

    event_overrides = EventOverrides(start_slot=1, max_slots=1)
    event_overrides.update(1, physical_code, "RC Hazard Guest", start, end)
    coordinator = MagicMock()
    coordinator.lockname = "front_door"

    with (
        patch(
            "custom_components.rental_control.event_overrides.async_fire_clear_code",
            new_callable=AsyncMock,
        ) as clear_code,
        patch(
            "custom_components.rental_control.event_overrides.async_fire_set_code",
            new_callable=AsyncMock,
        ) as set_code,
    ):
        results = await event_overrides.async_apply_plan(
            coordinator, plan, {reservation.identity_key: reservation}
        )

    assert results == []
    clear_code.assert_not_called()
    set_code.assert_not_called()
    override = event_overrides.overrides[1]
    assert override is not None
    assert override["slot_code"] == physical_code
