# SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Integration tests for calendar refresh cycles.

These tests verify that the coordinator refresh pipeline works end-to-end:
initial data load, scheduled refresh, sensor/calendar state propagation,
door-code generation, and independent multi-entry updates.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING
from unittest.mock import patch

from aioresponses import aioresponses
from homeassistant.helpers import entity_registry as er
import homeassistant.util.dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rental_control.const import COORDINATOR
from custom_components.rental_control.const import DOMAIN

from tests.fixtures import calendar_data
from tests.integration.helpers import FROZEN_START_OF_DAY
from tests.integration.helpers import FROZEN_TIME
from tests.integration.helpers import future_ics

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


# ---------------------------------------------------------------------------
# T112 – initial data load
# ---------------------------------------------------------------------------


async def test_initial_data_load(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Verify first refresh fetches and processes calendar data.

    After integration setup the coordinator should have loaded the ICS
    feed and populated its calendar list with parsed events.
    """
    mock_config_entry.add_to_hass(hass)

    with (
        aioresponses() as mock_session,
        patch.object(dt_util, "now", return_value=FROZEN_TIME),
        patch.object(dt_util, "start_of_local_day", return_value=FROZEN_START_OF_DAY),
    ):
        mock_session.get(
            mock_config_entry.data["url"],
            status=200,
            body=calendar_data.AIRBNB_ICS_CALENDAR,
            repeat=True,
        )

        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][mock_config_entry.entry_id][COORDINATOR]
    assert coordinator.data is not None
    assert len(coordinator.data) > 0


# ---------------------------------------------------------------------------
# T113 – scheduled refresh
# ---------------------------------------------------------------------------


async def test_scheduled_refresh(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Verify automatic refresh happens after the refresh interval elapses.

    After initial setup, calls async_refresh() with time advanced past
    the refresh interval to confirm a second fetch occurs successfully.
    """
    mock_config_entry.add_to_hass(hass)

    with (
        aioresponses() as mock_session,
        patch.object(dt_util, "now", return_value=FROZEN_TIME),
        patch.object(dt_util, "start_of_local_day", return_value=FROZEN_START_OF_DAY),
    ):
        mock_session.get(
            mock_config_entry.data["url"],
            status=200,
            body=calendar_data.AIRBNB_ICS_CALENDAR,
            repeat=True,
        )

        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id][COORDINATOR]

    # Advance past the refresh interval and trigger update
    future = FROZEN_TIME + timedelta(minutes=coordinator.refresh_frequency + 1)

    with (
        aioresponses() as mock_session,
        patch.object(dt_util, "now", return_value=future),
        patch.object(
            dt_util,
            "start_of_local_day",
            return_value=future.replace(hour=0, minute=0, second=0, microsecond=0),
        ),
    ):
        mock_session.get(
            mock_config_entry.data["url"],
            status=200,
            body=calendar_data.AIRBNB_ICS_CALENDAR,
            repeat=True,
        )

        await coordinator.async_refresh()
        await hass.async_block_till_done()

    assert coordinator.data is not None
    assert coordinator.last_update_success is True


# ---------------------------------------------------------------------------
# T114 – sensor state updates on refresh
# ---------------------------------------------------------------------------


async def test_sensor_updates_on_refresh(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Verify sensor entity states reflect data after coordinator refresh.

    After setup, sensors are created but not yet updated with event data.
    A subsequent async_refresh() (with time advanced past the refresh
    interval) triggers sensor updates so their state includes the guest
    name.
    """
    mock_config_entry.add_to_hass(hass)

    ics_body = future_ics()

    with (
        aioresponses() as mock_session,
        patch.object(dt_util, "now", return_value=FROZEN_TIME),
        patch.object(dt_util, "start_of_local_day", return_value=FROZEN_START_OF_DAY),
    ):
        mock_session.get(
            mock_config_entry.data["url"],
            status=200,
            body=ics_body,
            repeat=True,
        )

        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id][COORDINATOR]

        # Advance past the refresh interval so a second refresh triggers
        # sensor updates with event data
        future = FROZEN_TIME + timedelta(minutes=coordinator.refresh_frequency + 1)

    with (
        aioresponses() as mock_session,
        patch.object(dt_util, "now", return_value=future),
        patch.object(
            dt_util,
            "start_of_local_day",
            return_value=future.replace(hour=0, minute=0, second=0, microsecond=0),
        ),
    ):
        mock_session.get(
            mock_config_entry.data["url"],
            status=200,
            body=ics_body,
            repeat=True,
        )

        await coordinator.async_refresh()
        await hass.async_block_till_done()

    registry = er.async_get(hass)
    entries = er.async_entries_for_config_entry(registry, mock_config_entry.entry_id)
    event_0 = next(
        (e for e in entries if e.domain == "sensor" and "event_0" in e.entity_id),
        None,
    )
    assert event_0 is not None, "event_0 sensor not found in entity registry"

    sensor_state = hass.states.get(event_0.entity_id)
    assert sensor_state is not None
    assert "Test Guest" in sensor_state.state


# ---------------------------------------------------------------------------
# T115 – calendar entity reflects new events
# ---------------------------------------------------------------------------


async def test_calendar_updates_on_refresh(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Verify calendar entity reflects events after refresh.

    The coordinator.event should be set to the next upcoming event
    after the initial data load.
    """
    mock_config_entry.add_to_hass(hass)

    ics_body = future_ics()

    with (
        aioresponses() as mock_session,
        patch.object(dt_util, "now", return_value=FROZEN_TIME),
        patch.object(dt_util, "start_of_local_day", return_value=FROZEN_START_OF_DAY),
    ):
        mock_session.get(
            mock_config_entry.data["url"],
            status=200,
            body=ics_body,
            repeat=True,
        )

        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][mock_config_entry.entry_id][COORDINATOR]

    assert coordinator.event is not None
    assert coordinator.event.summary == "Reserved: Test Guest"


# ---------------------------------------------------------------------------
# T116 – door code generation on refresh
# ---------------------------------------------------------------------------


async def test_door_code_generation_on_refresh(
    hass: HomeAssistant,
) -> None:
    """Verify door codes are generated during refresh when configured.

    Uses a config entry with code_generation enabled. After the initial
    setup, a second async_refresh() (past the refresh interval) triggers
    sensor updates which generate door codes from event data.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Code Test",
        version=8,
        unique_id="test-code-unique-id",
        data={
            "name": "Code Test",
            "url": "https://example.com/calendar.ics",
            "timezone": "America/New_York",
            "checkin": "16:00",
            "checkout": "11:00",
            "start_slot": 10,
            "max_events": 3,
            "days": 90,
            "verify_ssl": True,
            "ignore_non_reserved": False,
            "code_generation": "date_based",
            "code_length": 4,
        },
        entry_id="test_code_entry",
    )
    entry.add_to_hass(hass)

    ics_body = future_ics()

    with (
        aioresponses() as mock_session,
        patch.object(dt_util, "now", return_value=FROZEN_TIME),
        patch.object(dt_util, "start_of_local_day", return_value=FROZEN_START_OF_DAY),
    ):
        mock_session.get(
            entry.data["url"],
            status=200,
            body=ics_body,
            repeat=True,
        )

        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        coordinator = hass.data[DOMAIN][entry.entry_id][COORDINATOR]

        # Trigger a second refresh to populate sensors with event data
        future = FROZEN_TIME + timedelta(minutes=coordinator.refresh_frequency + 1)

    with (
        aioresponses() as mock_session,
        patch.object(dt_util, "now", return_value=future),
        patch.object(
            dt_util,
            "start_of_local_day",
            return_value=future.replace(hour=0, minute=0, second=0, microsecond=0),
        ),
    ):
        mock_session.get(
            entry.data["url"],
            status=200,
            body=ics_body,
            repeat=True,
        )

        await coordinator.async_refresh()
        await hass.async_block_till_done()

    registry = er.async_get(hass)
    entries = er.async_entries_for_config_entry(registry, entry.entry_id)
    event_0 = next(
        (e for e in entries if e.domain == "sensor" and "event_0" in e.entity_id),
        None,
    )
    assert event_0 is not None, "event_0 sensor not found in entity registry"

    sensor_state = hass.states.get(event_0.entity_id)
    assert sensor_state is not None

    # Sensor with an event should have generated a door code (stored as slot_code)
    attrs = sensor_state.attributes
    assert attrs.get("slot_code") is not None
    assert len(attrs["slot_code"]) == 4
    assert attrs["slot_code"].isdigit()


# ---------------------------------------------------------------------------
# T117 – concurrent calendar updates (multiple entries)
# ---------------------------------------------------------------------------


async def test_concurrent_calendar_updates(
    hass: HomeAssistant,
) -> None:
    """Verify multiple config entries update independently.

    Sets up two separate integration entries, each with its own calendar
    URL and ICS data, and confirms they maintain independent state.
    Version is set to 7 to skip migrations that would overwrite
    unique_id with gen_uuid(dt.now()) and cause a collision.
    """
    entry_a = MockConfigEntry(
        domain=DOMAIN,
        title="Rental A",
        unique_id="unique_rental_a",
        version=8,
        data={
            "name": "Rental A",
            "url": "https://example.com/a.ics",
            "timezone": "America/New_York",
            "checkin": "16:00",
            "checkout": "11:00",
            "start_slot": 10,
            "max_events": 2,
            "days": 90,
            "verify_ssl": True,
            "ignore_non_reserved": False,
            "creation_datetime": "2025-01-01T00:00:00",
        },
        entry_id="entry_a",
    )
    entry_b = MockConfigEntry(
        domain=DOMAIN,
        title="Rental B",
        unique_id="unique_rental_b",
        version=8,
        data={
            "name": "Rental B",
            "url": "https://example.com/b.ics",
            "timezone": "America/Chicago",
            "checkin": "15:00",
            "checkout": "10:00",
            "start_slot": 20,
            "max_events": 3,
            "days": 180,
            "verify_ssl": True,
            "ignore_non_reserved": False,
            "creation_datetime": "2025-02-01T00:00:00",
        },
        entry_id="entry_b",
    )

    ics_a = future_ics(summary="Reserved: Guest A")
    ics_b = future_ics(summary="Reserved: Guest B", days_ahead=10)

    entry_a.add_to_hass(hass)
    entry_b.add_to_hass(hass)

    with (
        aioresponses() as mock_session,
        patch.object(dt_util, "now", return_value=FROZEN_TIME),
        patch.object(dt_util, "start_of_local_day", return_value=FROZEN_START_OF_DAY),
    ):
        mock_session.get(entry_a.data["url"], status=200, body=ics_a, repeat=True)
        mock_session.get(entry_b.data["url"], status=200, body=ics_b, repeat=True)

        # HA component setup auto-loads all registered config entries for the
        # domain.  Calling async_setup on entry_a triggers async_setup_component
        # which in turn sets up every entry added to hass for this domain.
        assert await hass.config_entries.async_setup(entry_a.entry_id)
        await hass.async_block_till_done()

    coord_a = hass.data[DOMAIN][entry_a.entry_id][COORDINATOR]
    coord_b = hass.data[DOMAIN][entry_b.entry_id][COORDINATOR]

    assert coord_a.name == "Rental A"
    assert coord_b.name == "Rental B"
    assert coord_a.max_events == 2
    assert coord_b.max_events == 3

    # Each coordinator loaded its own calendar independently
    assert coord_a.data is not None
    assert coord_b.data is not None
    assert coord_a.event.summary == "Reserved: Guest A"
    assert coord_b.event.summary == "Reserved: Guest B"


class TestClearFailureSlotNotReused:
    """Verify failed clear prevents slot reuse."""

    async def test_clear_failure_slot_not_reused(self) -> None:
        """A slot that fails to clear is not assigned to a new reservation."""
        from datetime import datetime
        from datetime import timezone
        from unittest.mock import AsyncMock
        from unittest.mock import MagicMock
        from unittest.mock import patch

        from custom_components.rental_control.event_overrides import EventOverrides
        from custom_components.rental_control.reconciliation import ActionKind
        from custom_components.rental_control.reconciliation import DesiredPlan
        from custom_components.rental_control.reconciliation import SlotAction
        from custom_components.rental_control.util import OperationResult

        eo = EventOverrides(start_slot=1, max_slots=2)
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        eo.update(1, "c1", "OldGuest", now, now)
        eo.update(2, "c2", "Slot2", now, now)

        plan = DesiredPlan(plan_id="test-t063", generated_at=now)
        plan.actions = [SlotAction(kind=ActionKind.CLEAR, slot=1, identity_key=None)]

        coordinator = MagicMock()
        coordinator.lockname = "test_lock"
        coordinator.hass.services.async_call = AsyncMock()

        failed_result = OperationResult(
            kind="clear",
            slot=1,
            failed=True,
            error="lock offline",
        )

        with patch(
            "custom_components.rental_control.event_overrides.async_fire_clear_code",
            return_value=failed_result,
        ):
            await eo.async_apply_plan(coordinator, plan, {})

        assert eo.overrides[1] is not None
        assert eo.overrides[1]["slot_name"] == "OldGuest"
        assert 1 in eo.pending_fences

    async def test_no_double_assignment_after_failed_clear(self) -> None:
        """A slot with failed clear is not available for new assignment."""
        from datetime import datetime
        from datetime import timezone
        from unittest.mock import AsyncMock
        from unittest.mock import MagicMock
        from unittest.mock import patch

        from custom_components.rental_control.event_overrides import EventOverrides
        from custom_components.rental_control.reconciliation import ActionKind
        from custom_components.rental_control.reconciliation import DesiredPlan
        from custom_components.rental_control.reconciliation import Reservation
        from custom_components.rental_control.reconciliation import SlotAction
        from custom_components.rental_control.util import OperationResult

        eo = EventOverrides(start_slot=1, max_slots=2)
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        eo.update(1, "c1", "OldGuest", now, now)
        eo.update(2, "", "", now, now)

        start = datetime(2026, 8, 1, 14, tzinfo=timezone.utc)
        end = datetime(2026, 8, 8, 11, tzinfo=timezone.utc)
        new_res = Reservation(
            identity_key="new-res",
            start=start,
            end=end,
            buffered_start=start,
            buffered_end=end,
            summary="New Guest",
            slot_name="New Guest",
            display_slot_name="RC New Guest",
            slot_code="1234",
        )

        plan = DesiredPlan(plan_id="t063-no-double", generated_at=now)
        plan.actions = [
            SlotAction(kind=ActionKind.CLEAR, slot=1, identity_key=None),
            SlotAction(kind=ActionKind.SET, slot=2, identity_key="new-res"),
        ]

        coordinator = MagicMock()
        coordinator.lockname = "test_lock"
        coordinator.event_prefix = ""
        coordinator.trim_names = False
        coordinator.code_buffer_before = 0
        coordinator.code_buffer_after = 0
        coordinator.hass.services.async_call = AsyncMock()
        coordinator.event_overrides = eo
        name_state = MagicMock()
        name_state.state = "New Guest"
        coordinator.hass.states.get.return_value = name_state

        failed_result = OperationResult(
            kind="clear",
            slot=1,
            failed=True,
            error="lock offline",
        )

        with patch(
            "custom_components.rental_control.event_overrides.async_fire_clear_code",
            return_value=failed_result,
        ):
            await eo.async_apply_plan(coordinator, plan, {"new-res": new_res})

        assert eo.overrides[1] is not None
        assert eo.overrides[1]["slot_name"] == "OldGuest"
        assert eo.overrides[2] is not None
        assert eo.overrides[2]["slot_name"] == "New Guest"


# ---------------------------------------------------------------------------
# T093 – Diagnostics desired-vs-actual completeness scenario
# ---------------------------------------------------------------------------


class TestDiagnosticsDesiredVsActual:
    """T093: Integration scenario proving diagnostics capture all significant
    slot states: matched slot, overflow reservation, manual drift, and
    pending clear.

    Uses focused mock-based integration (same pattern as TestClearFailureSlotNotReused)
    so no HA infrastructure is required.
    """

    async def test_diagnostics_captures_all_states(self) -> None:
        """Diagnostics snapshot covers matched, overflow, drift, and pending clear.

        Scenario:
          - Slot 1 occupied by matching reservation r-match (NOOP action)
          - Slot 2 pending_clear from a previous failed clear (RETRY_CLEAR)
          - Reservation r-overflow exceeds capacity and lands in plan.overflow
          - plan.diagnostics contains per-slot and per-reservation detail
          - No raw PIN codes appear anywhere in diagnostics
        """
        from datetime import datetime
        from datetime import timezone

        from custom_components.rental_control.event_overrides import EventOverrides
        from custom_components.rental_control.reconciliation import ActionKind
        from custom_components.rental_control.reconciliation import DesiredPlan
        from custom_components.rental_control.reconciliation import ManagedSlot
        from custom_components.rental_control.reconciliation import PlannedSlot
        from custom_components.rental_control.reconciliation import Reservation
        from custom_components.rental_control.reconciliation import SlotStatus
        from custom_components.rental_control.reconciliation import compute_desired_plan

        _TZ = timezone.utc

        def _mk(key: str, day: int) -> Reservation:
            """Build a minimal August Reservation with *key* starting on *day*."""
            from datetime import timedelta

            s = datetime(2026, 8, day, 14, tzinfo=_TZ)
            e = s + timedelta(days=7)
            return Reservation(
                identity_key=key,
                start=s,
                end=e,
                buffered_start=s,
                buffered_end=e,
                summary=f"Guest {key}",
                slot_name=f"Guest {key}",
                display_slot_name=f"RC Guest {key}",
                slot_code="SECRETCODE",
            )

        r_match = _mk("r-match", 1)
        r_overflow = _mk("r-overflow", 8)

        # Slot 1: occupied by r-match (persisted_identity_key matches)
        # Slot 2: pending_clear from a prior failed attempt
        ms1 = ManagedSlot(
            slot=1,
            managed=True,
            status=SlotStatus.OCCUPIED,
            actual_name="Guest r-match",
            actual_code_present=True,
            persisted_identity_key="r-match",
            last_error=None,
        )
        ms2 = ManagedSlot(
            slot=2,
            managed=True,
            status=SlotStatus.PENDING_CLEAR,
            actual_name="OldGuest",
            actual_code_present=True,
            retry_count=1,
            last_error="prior clear failed",
            blocked_reason="prior clear unconfirmed",
        )

        generated = datetime(2026, 8, 1, tzinfo=_TZ)
        plan = compute_desired_plan(
            [r_match, r_overflow],
            [ms1, ms2],
            max_events=1,  # capacity=1 → r_overflow overflows
            plan_id="t093-diag",
            generated_at=generated,
            entry_id="entry-t093",
            lockname="test_lock",
            start_slot=1,
        )

        diag = plan.diagnostics

        # --- Plan metadata present ---
        assert diag["plan_id"] == "t093-diag"
        assert diag["entry_id"] == "entry-t093"
        assert diag["lockname"] == "test_lock"
        assert diag["start_slot"] == 1

        # --- Matched slot: r-match in slot 1 ---
        assert "r-match" in plan.selected
        assert diag["reservations"]["r-match"]["selected"] is True
        assert diag["reservations"]["r-match"]["assigned_slot"] == 1

        # --- Overflow reservation: r-overflow ---
        assert "r-overflow" in plan.overflow
        assert diag["reservations"]["r-overflow"]["selected"] is False
        assert diag["reservations"]["r-overflow"]["overflow_reason"] is not None

        # --- Pending clear: slot 2 has RETRY_CLEAR action ---
        assert diag["slots"][2]["action"] == ActionKind.RETRY_CLEAR.value
        assert diag["slots"][2]["retry_count"] == 1
        assert diag["slots"][2]["last_error"] == "prior clear failed"

        # --- EventOverrides snapshot also captures this ---
        eo = EventOverrides(start_slot=1, max_slots=2)
        eo._pending_clear_slots[2] = "op-token"
        eo._record_slot_error(2, "prior clear failed")

        # Build a matching plan for the snapshot
        snap_plan = DesiredPlan(plan_id="t093-snap", generated_at=generated)
        snap_plan.slots[1] = PlannedSlot(
            slot=1,
            desired_identity_key="r-match",
            actual_classification="occupied",
            action=ActionKind.NOOP,
        )
        snap_plan.slots[2] = PlannedSlot(
            slot=2,
            desired_identity_key=None,
            actual_classification="pending_clear",
            action=ActionKind.RETRY_CLEAR,
            pending_reason="prior clear unconfirmed",
            retry_count=1,
        )

        eo.update_diagnostics_snapshot(snap_plan)
        snap = eo.diagnostics_snapshot

        assert 1 in snap["matched_slots"]
        assert snap["matched_slots"][1]["identity_key"] == "r-match"
        assert 2 in snap["pending_corrections"]
        assert 2 in snap["pending_clear_slots"]
        assert snap["last_slot_errors"][2] == "prior clear failed"

        # --- No raw codes anywhere ---
        assert "SECRETCODE" not in str(diag)
        assert "SECRETCODE" not in str(snap)
        assert "slot_code" not in str(diag)
        assert "slot_code" not in str(snap)

    async def test_diagnostics_manual_drift_detected(self) -> None:
        """Manual drift (OVERWRITE_MANUAL_CHANGE) shows in per-slot diagnostics."""
        from datetime import datetime
        from datetime import timedelta
        from datetime import timezone

        from custom_components.rental_control.reconciliation import ManagedSlot
        from custom_components.rental_control.reconciliation import Reservation
        from custom_components.rental_control.reconciliation import SlotStatus
        from custom_components.rental_control.reconciliation import compute_desired_plan

        _TZ = timezone.utc

        s = datetime(2026, 8, 1, 14, tzinfo=_TZ)
        e = s + timedelta(days=7)
        r = Reservation(
            identity_key="r-drift",
            start=s,
            end=e,
            buffered_start=s,
            buffered_end=e,
            summary="Guest Drift",
            slot_name="Guest Drift",
            display_slot_name="RC Guest Drift",
            slot_code="DRIFT_CODE",
        )

        # Slot occupied with DIFFERENT persisted key → planner will CLEAR then SET
        # (not OVERWRITE_MANUAL_CHANGE since that happens in apply, but the CLEAR
        # action + wrong persisted key is visible in diagnostics)
        ms = ManagedSlot(
            slot=5,
            managed=True,
            status=SlotStatus.OCCUPIED,
            actual_name="WrongGuest",
            actual_code_present=True,
            persisted_identity_key="r-wrong",  # different from r-drift
        )

        plan = compute_desired_plan(
            [r],
            [ms],
            max_events=3,
            plan_id="t093-drift",
            generated_at=s,
        )

        slot_diag = plan.diagnostics["slots"][5]
        # CLEAR because persisted key doesn't match desired key
        assert slot_diag["action"] == "clear"
        assert slot_diag["actual_classification"] == "occupied"
        assert "DRIFT_CODE" not in str(plan.diagnostics)


# ---------------------------------------------------------------------------
# T072 / T073: Manual drift correction and unmanaged-slot ignore scenarios
# ---------------------------------------------------------------------------


class TestManualDriftCorrection:
    """T072 + T073: Integration scenarios for manual/external slot drift.

    T072 verifies that when a managed slot's actual state drifts from the
    desired plan (name changed), async_apply_plan issues a WARNING log
    containing the slot number, changed field names, and desired identity —
    without exposing the raw PIN — and then restores the desired state via
    async_fire_set_code.

    T073 verifies that edits to unmanaged (out-of-range) slots produce no
    OVERWRITE_MANUAL_CHANGE action because compute_desired_plan iterates
    only managed == True slots.

    Uses a focused mock-based approach (no HA infrastructure) mirroring
    TestClearFailureSlotNotReused.
    """

    async def test_manual_edit_corrected_and_caplog(self, caplog) -> None:
        """T072: Name-drifted managed slot is corrected and WARNING logged.

        Scenario:
          - Reservation r-drift-t072 is assigned to slot 5.
          - Actual Keymaster name is 'WRONG NAME' (manual drift).
          - compute_desired_plan generates OVERWRITE_MANUAL_CHANGE for slot 5.
          - async_apply_plan calls async_fire_set_code and emits WARNING with:
              slot number, changed field names, desired identity_key.
          - Raw PIN value never appears in logs.
          - diagnostics snapshot captures manual_drift_slots entry.
        """
        from datetime import datetime
        from datetime import timedelta
        from datetime import timezone
        import logging
        from unittest.mock import AsyncMock
        from unittest.mock import MagicMock
        from unittest.mock import patch

        from custom_components.rental_control.event_overrides import EventOverrides
        from custom_components.rental_control.reconciliation import ActionKind
        from custom_components.rental_control.reconciliation import ManagedSlot
        from custom_components.rental_control.reconciliation import Reservation
        from custom_components.rental_control.reconciliation import SlotStatus
        from custom_components.rental_control.reconciliation import compute_desired_plan
        from custom_components.rental_control.util import OperationResult

        _TZ = timezone.utc
        s = datetime(2026, 8, 1, 14, tzinfo=_TZ)
        e = s + timedelta(days=7)
        r = Reservation(
            identity_key="r-drift-t072",
            start=s,
            end=e,
            buffered_start=s,
            buffered_end=e,
            summary="Guest Drift",
            slot_name="Guest Drift",
            display_slot_name="RC Guest Drift",
            slot_code="SECRETPIN72",
        )

        ms = ManagedSlot(
            slot=5,
            managed=True,
            status=SlotStatus.OCCUPIED,
            actual_name="WRONG NAME",  # manual drift
            actual_code_present=True,
            persisted_identity_key="r-drift-t072",
        )

        plan = compute_desired_plan(
            [r],
            [ms],
            max_events=3,
            plan_id="t072-drift",
            generated_at=s,
        )

        overwrite_actions = [
            a for a in plan.actions if a.kind is ActionKind.OVERWRITE_MANUAL_CHANGE
        ]
        assert len(overwrite_actions) == 1
        assert overwrite_actions[0].slot == 5

        eo = EventOverrides(start_slot=5, max_slots=1)
        eo.update(5, "SECRETPIN72", "Guest Drift", s, e)

        coordinator = MagicMock()
        coordinator.lockname = "test_lock"
        coordinator.hass.services.async_call = AsyncMock()

        confirmed = OperationResult(kind="set", slot=5, confirmed=True)
        with (
            caplog.at_level(logging.WARNING, logger="custom_components.rental_control"),
            patch(
                "custom_components.rental_control.event_overrides.async_fire_set_code",
                return_value=confirmed,
            ) as mock_set,
        ):
            await eo.async_apply_plan(coordinator, plan, {"r-drift-t072": r})

        # async_fire_set_code was called to restore desired state
        assert mock_set.called

        # WARNING log contains slot number, field names, and identity
        log_text = " ".join(r.message for r in caplog.records)
        assert "slot 5" in log_text
        assert "name" in log_text
        assert "r-drift-t072" in log_text

        # Raw PIN never appears in WARNING (or above) log records
        for record in (r for r in caplog.records if r.levelno >= logging.WARNING):
            assert "SECRETPIN72" not in record.message

        # Diagnostics snapshot captures the drift entry
        snap = eo.diagnostics_snapshot
        assert "manual_drift_slots" in snap
        assert 5 in snap["manual_drift_slots"]
        drift_info = snap["manual_drift_slots"][5]
        assert drift_info["identity_key"] == "r-drift-t072"
        assert "name" in drift_info["drift_fields"]
        # No raw codes in snapshot
        assert "SECRETPIN72" not in str(snap)

    async def test_unmanaged_slot_manual_edit_ignored(self) -> None:
        """T073: Manual edits to unmanaged slots produce no OVERWRITE action.

        Scenario:
          - Slot 3 is outside the RC-managed range (managed=False).
          - Its actual_name differs — any drift would normally trigger
            OVERWRITE_MANUAL_CHANGE, but unmanaged slots are invisible to
            compute_desired_plan.
          - Slot 5 is managed and free; the reservation lands there via SET.
        """
        from datetime import datetime
        from datetime import timedelta
        from datetime import timezone

        from custom_components.rental_control.reconciliation import ActionKind
        from custom_components.rental_control.reconciliation import ManagedSlot
        from custom_components.rental_control.reconciliation import Reservation
        from custom_components.rental_control.reconciliation import SlotStatus
        from custom_components.rental_control.reconciliation import compute_desired_plan

        _TZ = timezone.utc
        s = datetime(2026, 8, 1, 14, tzinfo=_TZ)
        e = s + timedelta(days=7)
        r = Reservation(
            identity_key="r-unmanaged-t073",
            start=s,
            end=e,
            buffered_start=s,
            buffered_end=e,
            summary="Guest Unmanaged",
            slot_name="Guest Unmanaged",
            display_slot_name="RC Guest Unmanaged",
            slot_code="SECRETPIN73",
        )

        # Slot 3: unmanaged, heavily drifted — must be completely ignored
        unmanaged = ManagedSlot(
            slot=3,
            managed=False,
            status=SlotStatus.OCCUPIED,
            actual_name="EDITED BY OWNER",
            actual_code_present=True,
            persisted_identity_key="r-unmanaged-t073",
        )
        # Slot 5: managed and free
        managed_free = ManagedSlot(slot=5, managed=True, status=SlotStatus.FREE)

        plan = compute_desired_plan(
            [r],
            [unmanaged, managed_free],
            max_events=3,
            plan_id="t073-unmanaged",
            generated_at=s,
        )

        # Unmanaged slot 3 must not appear in slots or actions
        assert 3 not in plan.slots
        assert not any(a.slot == 3 for a in plan.actions)

        # No OVERWRITE_MANUAL_CHANGE actions at all
        overwrite_actions = [
            a for a in plan.actions if a.kind is ActionKind.OVERWRITE_MANUAL_CHANGE
        ]
        assert len(overwrite_actions) == 0

        # Reservation assigned to managed slot 5 via SET
        set_actions = [a for a in plan.actions if a.kind is ActionKind.SET]
        assert len(set_actions) == 1
        assert set_actions[0].slot == 5
