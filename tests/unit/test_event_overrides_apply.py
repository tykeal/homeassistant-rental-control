# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>

"""Focused apply-helper and diagnostics tests for EventOverrides decomposition."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from homeassistant.util import dt as dt_util
import pytest

from custom_components.rental_control.event_overrides import EventOverrides
from custom_components.rental_control.event_overrides_helpers.apply_clear import (
    decide_clear_preflight,
)
from custom_components.rental_control.event_overrides_helpers.apply_clear import (
    decide_clear_result_mutation,
)
from custom_components.rental_control.event_overrides_helpers.apply_dispatch import (
    classify_action,
)
from custom_components.rental_control.event_overrides_helpers.apply_set import (
    build_set_operation_id,
)
from custom_components.rental_control.event_overrides_helpers.apply_set import (
    build_suppression_changes,
)
from custom_components.rental_control.event_overrides_helpers.apply_set import (
    build_tentative_override,
)
from custom_components.rental_control.event_overrides_helpers.apply_set import (
    decide_set_result_mutation,
)
from custom_components.rental_control.event_overrides_helpers.apply_update import (
    build_replacement_plan_id,
)
from custom_components.rental_control.event_overrides_helpers.apply_update import (
    build_update_time_suppression,
)
from custom_components.rental_control.event_overrides_helpers.apply_update import (
    parse_drift_fields,
)
from custom_components.rental_control.event_overrides_helpers.diagnostics import (
    build_diagnostics_snapshot,
)
from custom_components.rental_control.reconciliation import ActionKind
from custom_components.rental_control.reconciliation import DesiredPlan
from custom_components.rental_control.reconciliation import PlannedSlot
from custom_components.rental_control.reconciliation import Reservation
from custom_components.rental_control.reconciliation import SlotAction
from custom_components.rental_control.util import OperationResult


def _dt(day: int, hour: int = 14) -> datetime:
    """Return a UTC-aware datetime for February 2026."""
    return datetime(2026, 2, day, hour, tzinfo=dt_util.UTC)


def _reservation(identity_key: str = "res-1") -> Reservation:
    """Return a reservation suitable for apply helper tests."""
    start = _dt(1)
    end = _dt(5)
    return Reservation(
        identity_key=identity_key,
        start=start,
        end=end,
        buffered_start=start,
        buffered_end=end,
        summary="Guest",
        slot_name="Guest",
        display_slot_name="RC Guest",
        slot_code="1234",
    )


class TestApplyHelpers:
    """T013/T040-T045/T053: focused apply helper tests."""

    def test_dispatch_classifies_actions(self) -> None:
        """Dispatch helper preserves operation selection and warnings."""
        res = _reservation()
        res_by_key = {res.identity_key: res}

        clear = classify_action(
            SlotAction(kind=ActionKind.CLEAR, slot=1, reason="stale"), res_by_key
        )
        set_op = classify_action(
            SlotAction(kind=ActionKind.SET, slot=1, identity_key=res.identity_key),
            res_by_key,
        )
        update_op = classify_action(
            SlotAction(
                kind=ActionKind.UPDATE_TIMES, slot=1, identity_key=res.identity_key
            ),
            res_by_key,
        )
        overwrite = classify_action(
            SlotAction(
                kind=ActionKind.OVERWRITE_MANUAL_CHANGE,
                slot=1,
                identity_key=res.identity_key,
            ),
            res_by_key,
        )
        missing = classify_action(
            SlotAction(kind=ActionKind.SET, slot=1, identity_key="missing"), res_by_key
        )
        noop = classify_action(SlotAction(kind=ActionKind.NOOP, slot=1), res_by_key)

        assert clear["operation"] == "clear"
        assert "Stale correction" in clear["warning"]
        assert set_op["reservation"] is res
        assert update_op["operation"] == "update_times"
        assert overwrite["operation"] == "overwrite"
        assert "has no reservation" in missing["warning"]
        assert noop["operation"] is None

    def test_clear_preflight_and_result_decisions(self) -> None:
        """Clear helpers preserve preflight safety and result mutation choices."""
        already_empty = decide_clear_preflight(("", ""), "Guest", {})
        name_changed = decide_clear_preflight(
            ("Other", "1234"), "Guest", {"name_state": "Guest"}
        )
        pin_changed = decide_clear_preflight(("Guest", ""), "Guest", {"has_code": True})

        assert already_empty["status"] == "confirmed"
        assert name_changed["reason"] == "name_changed"
        assert pin_changed["reason"] == "pin_changed"

        confirmed = decide_clear_result_mutation(
            OperationResult(kind="clear", slot=1, confirmed=True)
        )
        failed = decide_clear_result_mutation(
            OperationResult(kind="clear", slot=1, failed=True, error="boom")
        )
        lingering = decide_clear_result_mutation(
            OperationResult(kind="clear", slot=1, lingering_name=True)
        )

        assert confirmed["clear_slot"] is True
        assert failed["record_error"] == "boom"
        assert "lingering state after clear" in lingering["record_error"]

    def test_set_and_update_helpers_build_expected_payloads(self) -> None:
        """Set/update helpers keep deterministic tokens and payload shapes."""
        res = _reservation("res-99")
        operation_id = build_set_operation_id("plan-1", 3, res.identity_key)
        tentative = build_tentative_override(res)
        suppression = build_suppression_changes("lock", 3, res)
        update = build_update_time_suppression("lock", 3, res)
        drift_fields = parse_drift_fields("drifted fields: name, start, end")
        replacement = build_replacement_plan_id(3, lambda: "token-123")

        assert operation_id == build_set_operation_id("plan-1", 3, res.identity_key)
        assert tentative["slot_name"] == "Guest"
        assert suppression["text.lock_code_slot_3_name"] == "RC Guest"
        assert (
            update["datetime.lock_code_slot_3_date_range_start"]
            == res.buffered_start.isoformat()
        )
        assert drift_fields == ["name", "start", "end"]
        assert replacement == "replace-3-token-123"

        confirmed = decide_set_result_mutation(
            OperationResult(kind="set", slot=3, confirmed=True), True, 3
        )
        stale = decide_set_result_mutation(
            OperationResult(kind="set", slot=3), False, 3
        )
        failed = decide_set_result_mutation(
            OperationResult(kind="set", slot=3, failed=True, error="fail"), True, 3
        )

        assert confirmed["status"] == "confirmed"
        assert stale["status"] == "stale"
        assert failed["revert"] is True

    def test_build_diagnostics_snapshot_redacts_raw_codes(self) -> None:
        """Diagnostics projection keeps behaviorally relevant metadata only."""
        plan = DesiredPlan(plan_id="diag-1", generated_at=_dt(1))
        plan.slots[1] = PlannedSlot(1, "res-1", "occupied", ActionKind.NOOP)
        plan.slots[2] = PlannedSlot(
            2,
            None,
            "blocked",
            ActionKind.BLOCKED,
            pending_reason="slot entity unavailable",
            retry_count=2,
        )
        plan.slots[3] = PlannedSlot(
            3,
            "res-3",
            "occupied",
            ActionKind.OVERWRITE_MANUAL_CHANGE,
            pending_reason="drifted fields: name, end",
        )

        snapshot = build_diagnostics_snapshot(
            plan,
            {2: "token"},
            {1: 1, 3: 2},
            {3: "needs overwrite"},
            1,
            3,
        )

        assert snapshot["matched_slots"][1]["identity_key"] == "res-1"
        assert snapshot["pending_corrections"][2]["retry_count"] == 2
        assert snapshot["manual_drift_slots"][3]["drift_fields"] == ["name", "end"]
        assert snapshot["pending_clear_slots"] == [2]
        assert "slot_code" not in str(snapshot)

    @pytest.mark.asyncio
    async def test_async_apply_plan_finalizes_activity_and_diagnostics(self) -> None:
        """async_apply_plan toggles reconciliation_active and updates diagnostics."""
        eo = EventOverrides(start_slot=1, max_slots=1)
        now = _dt(1)
        eo.update(1, "", "", now, now)
        res = _reservation()
        plan = DesiredPlan(plan_id="plan-1", generated_at=now)
        plan.actions = [
            SlotAction(kind=ActionKind.SET, slot=1, identity_key=res.identity_key)
        ]
        coordinator = SimpleNamespace(
            lockname="lock", hass=SimpleNamespace(states=None, services=None)
        )
        coordinator.hass.states = SimpleNamespace(
            get=lambda _entity_id: SimpleNamespace(state="")
        )
        coordinator.hass.services = SimpleNamespace(async_call=None)

        async def fake_apply_set(_coordinator, _slot, _res, _plan_id):
            """Assert lifecycle state during the delegated set call."""
            assert eo.reconciliation_active is True
            return OperationResult(kind="set", slot=1, confirmed=True)

        with (
            patch.object(eo, "_apply_set", side_effect=fake_apply_set),
            patch.object(eo, "update_diagnostics_snapshot") as snapshot,
        ):
            results = await eo.async_apply_plan(
                coordinator, plan, {res.identity_key: res}
            )

        assert results == [OperationResult(kind="set", slot=1, confirmed=True)]
        assert eo.reconciliation_active is False
        snapshot.assert_called_once_with(plan)
