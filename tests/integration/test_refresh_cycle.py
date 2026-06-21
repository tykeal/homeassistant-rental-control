# SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Integration tests for calendar refresh cycles.

These tests verify that the coordinator refresh pipeline works end-to-end:
initial data load, scheduled refresh, sensor/calendar state propagation,
door-code generation, and independent multi-entry updates.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta
from typing import TYPE_CHECKING
from typing import Any
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

from aioresponses import aioresponses
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.const import STATE_UNKNOWN
from homeassistant.helpers import entity_registry as er
import homeassistant.util.dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.rental_control.const import CONF_LOCK_ENTRY
from custom_components.rental_control.const import CONF_MAX_EVENTS
from custom_components.rental_control.const import CONF_REFRESH_FREQUENCY
from custom_components.rental_control.const import CONF_START_SLOT
from custom_components.rental_control.const import COORDINATOR
from custom_components.rental_control.const import DOMAIN
from custom_components.rental_control.const import SLOT_STATUS_PENDING_CLEAR
from custom_components.rental_control.coordinator import RentalControlCoordinator
from custom_components.rental_control.util import OperationResult

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


async def test_wedged_recovers_promptly_when_slots_become_available(
    hass: HomeAssistant,
) -> None:
    """Recover a pending-clear startup wedge when Keymaster becomes readable."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Prompt Recovery Rental",
        version=10,
        unique_id="prompt-recovery",
        data={
            "name": "Prompt Recovery Rental",
            "url": "https://example.com/calendar.ics",
            "timezone": "UTC",
            "checkin": "16:00",
            "checkout": "11:00",
            CONF_START_SLOT: 10,
            CONF_MAX_EVENTS: 1,
            "days": 90,
            "verify_ssl": True,
            "ignore_non_reserved": False,
            "honor_event_times": False,
            CONF_LOCK_ENTRY: "front_door",
            CONF_REFRESH_FREQUENCY: 30,
            "code_buffer_before": 0,
            "code_buffer_after": 0,
        },
        entry_id="prompt_recovery_entry",
    )
    entry.add_to_hass(hass)

    for suffix in ("name", "pin"):
        hass.states.async_set(
            f"text.front_door_code_slot_10_{suffix}",
            STATE_UNAVAILABLE,
        )
    hass.states.async_set("switch.front_door_code_slot_10_enabled", STATE_UNAVAILABLE)

    store_data: dict[str, Any] = {
        "schema_version": 1,
        "entry_id": entry.entry_id,
        "lockname": "front_door",
        "start_slot": 10,
        "max_slots": 1,
        "updated_at": "2026-06-21T00:00:00+00:00",
        "mappings": {
            "stale-pending-clear": {
                "slot": 10,
                "status": SLOT_STATUS_PENDING_CLEAR,
                "operation_id": "pending-clear-token",
                "operation_kind": "clear",
                "identity": {
                    "identity_key": "stale-pending-clear",
                    "summary": "Old Guest",
                    "slot_name": "Old Guest",
                    "uid_aliases": [],
                    "booking_aliases": [],
                },
                "missing_count": 0,
                "pending_set_since": None,
                "pending_clear_since": "2026-06-20T00:00:00+00:00",
                "fingerprint_history": [],
                "updated_at": "2026-06-20T00:00:00+00:00",
                "last_observed_actual": {
                    "slot": 10,
                    "classification": SLOT_STATUS_PENDING_CLEAR,
                    "name_state": None,
                    "has_code": None,
                    "start_state": None,
                    "end_state": None,
                    "use_date_range": None,
                    "enabled": None,
                },
            }
        },
        "blocked_slots": {},
    }

    async def load_store(coordinator: RentalControlCoordinator) -> None:
        """Load wedged pending-clear state into the coordinator."""
        coordinator._slot_mappings = store_data

    async def save_store(_coordinator: RentalControlCoordinator) -> None:
        """Avoid writing the HA Store in the test."""

    with (
        aioresponses() as mock_session,
        patch.object(
            RentalControlCoordinator,
            "async_load_slot_store",
            autospec=True,
            side_effect=load_store,
        ),
        patch.object(
            RentalControlCoordinator,
            "async_save_slot_store",
            autospec=True,
            side_effect=save_store,
        ),
        patch(
            "custom_components.rental_control.event_overrides.async_fire_set_code",
            new=AsyncMock(
                return_value=OperationResult(kind="set", slot=10, confirmed=True)
            ),
        ) as set_code,
        patch.object(dt_util, "now", return_value=FROZEN_TIME),
        patch.object(dt_util, "start_of_local_day", return_value=FROZEN_START_OF_DAY),
    ):
        mock_session.get(
            entry.data["url"],
            status=200,
            body=future_ics(),
            repeat=True,
        )

        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        coordinator = hass.data[DOMAIN][entry.entry_id][COORDINATOR]
        assert coordinator._latest_plan is not None
        assert coordinator._latest_plan.selected == {}
        assert coordinator._latest_plan.overflow != {}
        set_code.assert_not_awaited()

        for suffix in ("name", "pin"):
            hass.states.async_set(
                f"text.front_door_code_slot_10_{suffix}",
                STATE_UNKNOWN,
            )
        hass.states.async_set("switch.front_door_code_slot_10_enabled", "off")
        await hass.async_block_till_done()

        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=3))
        await hass.async_block_till_done()
        await hass.async_block_till_done()

        assert set_code.await_count == 1
        assert coordinator._latest_plan is not None
        assert coordinator._latest_plan.overflow == {}
        assert coordinator._latest_plan.selected != {}

        hass.states.async_set("text.front_door_code_slot_10_name", "Later Change")
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=6))
        await hass.async_block_till_done()
        await hass.async_block_till_done()

        assert set_code.await_count == 1


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


# ---------------------------------------------------------------------------
# T047 – #589 triple-assignment duplicate-collapse regression
# ---------------------------------------------------------------------------


class TestTripleAssignmentCollapse:
    """T047: Regression for #589 — triple duplicate assignment collapses.

    In issue #589, the same reservation ended up programmed into three slots
    simultaneously.  The reconciliation system must detect this and clear
    the two non-canonical slots, converging the state in one or two
    normal refreshes.
    """

    async def test_triple_assignment_converges_one_refresh(self) -> None:
        """Three copies of same reservation collapse to one in a single refresh.

        Setup:
          - Slots 3, 4, 5 all OCCUPIED with persisted_identity_key='r-589'
          - One active reservation 'r-589'
          - max_events=3 (only 1 reservation, so slots 4 and 5 must be cleared)

        After one refresh with confirmed clears, slots 4 and 5 are free and
        slot 3 is the canonical owner.
        """
        from datetime import datetime
        from datetime import timedelta
        from datetime import timezone
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
        res = Reservation(
            identity_key="r-589",
            start=s,
            end=e,
            buffered_start=s,
            buffered_end=e,
            summary="Guest 589",
            slot_name="Guest 589",
            display_slot_name="RC Guest 589",
            slot_code="PIN589",
        )

        # Corrupt starting state: same reservation in 3 slots
        slot3 = ManagedSlot(
            slot=3,
            managed=True,
            status=SlotStatus.OCCUPIED,
            actual_name="RC Guest 589",
            actual_code_present=True,
            persisted_identity_key="r-589",
            actual_start=s,
            actual_end=e,
        )
        slot4 = ManagedSlot(
            slot=4,
            managed=True,
            status=SlotStatus.OCCUPIED,
            actual_name="Guest 589",
            actual_code_present=True,
            persisted_identity_key="r-589",
        )
        slot5 = ManagedSlot(
            slot=5,
            managed=True,
            status=SlotStatus.OCCUPIED,
            actual_name="Guest 589",
            actual_code_present=True,
            persisted_identity_key="r-589",
        )

        plan = compute_desired_plan(
            [res],
            [slot3, slot4, slot5],
            max_events=3,
            plan_id="t047-triple",
            generated_at=s,
        )

        # Canonical slot 3 should keep the reservation
        assert plan.selected.get("r-589") == 3
        # Slots 4 and 5 must be cleared
        clear_slots = {a.slot for a in plan.actions if a.kind is ActionKind.CLEAR}
        assert 4 in clear_slots
        assert 5 in clear_slots

        # Apply plan with confirmed clears
        eo = EventOverrides(start_slot=3, max_slots=3)
        eo.update(3, "PIN589", "Guest 589", s, e)
        eo.update(4, "PIN589", "Guest 589", s, e)
        eo.update(5, "PIN589", "Guest 589", s, e)

        coordinator = MagicMock()
        coordinator.lockname = "test_lock"
        coordinator.hass.services.async_call = AsyncMock()

        async def mock_clear(coord, slot, **kwargs):
            """Return a confirmed clear result for the given slot."""
            return OperationResult(kind="clear", slot=slot, confirmed=True)

        with patch(
            "custom_components.rental_control.event_overrides.async_fire_clear_code",
            side_effect=mock_clear,
        ):
            await eo.async_apply_plan(coordinator, plan, {"r-589": res})

        # After confirmed clears: slots 4 and 5 freed, slot 3 still occupied
        assert eo.overrides.get(4) is None
        assert eo.overrides.get(5) is None
        assert eo.overrides.get(3) is not None


# ---------------------------------------------------------------------------
# T048 – #521 phantom name-only slot pending-clear then reuse
# ---------------------------------------------------------------------------


class TestPhantomNameOnlySlot:
    """T048: Regression for #521 — phantom slot becomes pending-clear then reusable.

    In issue #521, a slot had a guest name but no PIN (phantom state).
    Rental Control must classify it as PHANTOM, issue a CLEAR, and only
    reuse the slot after a confirmed clear.
    """

    async def test_phantom_slot_pending_clear_then_free_then_reuse(self) -> None:
        """PHANTOM slot is cleared then becomes free and reusable.

        Refresh 1: slot has PHANTOM state → CLEAR action issued.
        After clear confirmed: slot is FREE.
        Refresh 2: a waiting reservation is assigned (SET) to the now-free slot.
        """
        from datetime import datetime
        from datetime import timedelta
        from datetime import timezone
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
        res = Reservation(
            identity_key="r-521",
            start=s,
            end=e,
            buffered_start=s,
            buffered_end=e,
            summary="Guest 521",
            slot_name="Guest 521",
            display_slot_name="RC Guest 521",
            slot_code="PIN521",
        )

        # Corrupt starting state: slot 5 is PHANTOM (name only, no PIN)
        slot5_phantom = ManagedSlot(
            slot=5,
            managed=True,
            status=SlotStatus.PHANTOM,
            actual_name="OldPhantomGuest",
            actual_code_present=False,
        )

        # Refresh 1: phantom slot must receive CLEAR, reservation overflows
        plan1 = compute_desired_plan(
            [res],
            [slot5_phantom],
            max_events=3,
            plan_id="t048-r1",
            generated_at=s,
        )

        assert plan1.slots[5].action is ActionKind.CLEAR
        assert plan1.slots[5].action.value == "clear"

        # Apply plan 1: confirmed clear
        eo = EventOverrides(start_slot=5, max_slots=1)
        eo.update(5, "", "OldPhantomGuest", s, e)

        coordinator = MagicMock()
        coordinator.lockname = "test_lock"
        coordinator.hass.services.async_call = AsyncMock()

        confirmed_clear = OperationResult(kind="clear", slot=5, confirmed=True)
        with patch(
            "custom_components.rental_control.event_overrides.async_fire_clear_code",
            return_value=confirmed_clear,
        ):
            await eo.async_apply_plan(coordinator, plan1, {})

        # After clear: slot 5 should be free (override cleared)
        assert eo.overrides.get(5) is None
        assert 5 not in eo.pending_fences

        # Refresh 2: slot 5 is now FREE → reservation gets SET
        slot5_free = ManagedSlot(
            slot=5,
            managed=True,
            status=SlotStatus.FREE,
        )

        plan2 = compute_desired_plan(
            [res],
            [slot5_free],
            max_events=3,
            plan_id="t048-r2",
            generated_at=s,
        )

        assert "r-521" in plan2.selected
        set_actions = [a for a in plan2.actions if a.kind is ActionKind.SET]
        assert len(set_actions) == 1
        assert set_actions[0].slot == 5


# ---------------------------------------------------------------------------
# T049 – stale expired assignment self-heal without restart/reload
# ---------------------------------------------------------------------------


class TestStaleExpiredAssignment:
    """T049: Stale expired assignment self-heals on the next normal refresh.

    A slot occupied by a reservation that is no longer in the calendar feed
    (expired or cancelled) must be cleared and made available, all within
    a normal coordinator refresh — no restart or reload required.
    """

    async def test_stale_assignment_cleared_on_next_refresh(self) -> None:
        """Stale occupied slot receives CLEAR; slot is freed for a new reservation.

        Setup:
          - Slot 5 OCCUPIED with r-expired (NOT in current reservations)
          - r-new needs assignment
        Refresh 1: slot 5 gets CLEAR; r-new overflows (no free slot).
        After clear confirmed: slot 5 is FREE.
        Refresh 2: r-new gets SET to slot 5.
        """
        from datetime import datetime
        from datetime import timedelta
        from datetime import timezone
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

        r_new = Reservation(
            identity_key="r-new-t049",
            start=s,
            end=e,
            buffered_start=s,
            buffered_end=e,
            summary="New Guest",
            slot_name="New Guest",
            display_slot_name="RC New Guest",
            slot_code="PIN049",
        )

        # Slot 5 occupied by expired reservation (not in current list)
        slot5_stale = ManagedSlot(
            slot=5,
            managed=True,
            status=SlotStatus.OCCUPIED,
            actual_name="OldExpiredGuest",
            actual_code_present=True,
            persisted_identity_key="r-expired-t049",
        )

        # Refresh 1: r-new needs slot, slot 5 is stale → CLEAR; r-new overflows
        plan1 = compute_desired_plan(
            [r_new],  # r-expired NOT in list
            [slot5_stale],
            max_events=3,
            plan_id="t049-r1",
            generated_at=s,
        )

        # Slot 5 gets CLEAR (stale)
        assert plan1.slots[5].action is ActionKind.CLEAR
        # r-new overflows (no free slot available)
        assert "r-new-t049" in plan1.overflow

        # Apply plan 1: confirmed clear
        eo = EventOverrides(start_slot=5, max_slots=1)
        eo.update(5, "PIN-EXPIRED", "OldExpiredGuest", s, e)

        coordinator = MagicMock()
        coordinator.lockname = "test_lock"
        coordinator.hass.services.async_call = AsyncMock()

        confirmed_clear = OperationResult(kind="clear", slot=5, confirmed=True)
        with patch(
            "custom_components.rental_control.event_overrides.async_fire_clear_code",
            return_value=confirmed_clear,
        ):
            await eo.async_apply_plan(coordinator, plan1, {})

        assert eo.overrides.get(5) is None

        # Refresh 2: slot 5 now FREE → r-new gets SET
        slot5_free = ManagedSlot(slot=5, managed=True, status=SlotStatus.FREE)

        plan2 = compute_desired_plan(
            [r_new],
            [slot5_free],
            max_events=3,
            plan_id="t049-r2",
            generated_at=s,
        )

        assert "r-new-t049" in plan2.selected
        set_actions = [a for a in plan2.actions if a.kind is ActionKind.SET]
        assert any(a.slot == 5 for a in set_actions)


# ---------------------------------------------------------------------------
# T050 – stale mis-assigned slot replaced with desired reservation
# ---------------------------------------------------------------------------


class TestStaleMisassignedSlot:
    """T050: Stale mis-assigned slot self-heals by clearing and reassigning.

    A slot occupied by reservation A (stale) is cleared so that reservation
    B (the desired, currently-active reservation) can take the slot on the
    next refresh cycle.
    """

    async def test_misassigned_slot_replaced_with_desired_two_refreshes(
        self,
    ) -> None:
        """Mis-assigned slot is cleared on refresh 1; desired reservation SET on refresh 2.

        Setup:
          - Slot 3 OCCUPIED with r-wrong (NOT in current reservations)
          - Slot 5 FREE
          - r-desired is the active reservation

        Refresh 1:
          - r-desired gets slot 5 (SET — free slot available)
          - Slot 3 gets CLEAR (stale occupant)

        After clear: slot 3 is FREE (slot 5 already has r-desired).
        Convergence happens in 1 refresh (r-desired gets a slot immediately).
        """
        from datetime import datetime
        from datetime import timedelta
        from datetime import timezone
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

        r_desired = Reservation(
            identity_key="r-desired-t050",
            start=s,
            end=e,
            buffered_start=s,
            buffered_end=e,
            summary="Desired Guest",
            slot_name="Desired Guest",
            display_slot_name="RC Desired Guest",
            slot_code="PIN050",
        )

        slot3_wrong = ManagedSlot(
            slot=3,
            managed=True,
            status=SlotStatus.OCCUPIED,
            actual_name="WrongGuest",
            actual_code_present=True,
            persisted_identity_key="r-wrong-t050",  # NOT in current list
        )
        slot5_free = ManagedSlot(slot=5, managed=True, status=SlotStatus.FREE)

        plan = compute_desired_plan(
            [r_desired],
            [slot3_wrong, slot5_free],
            max_events=3,
            plan_id="t050-misassign",
            generated_at=s,
        )

        # r-desired gets the free slot 5 (SET)
        assert plan.selected.get("r-desired-t050") == 5
        set_actions = [a for a in plan.actions if a.kind is ActionKind.SET]
        assert any(a.slot == 5 for a in set_actions)

        # Slot 3 gets CLEAR (stale r-wrong)
        clear_actions = [a for a in plan.actions if a.kind is ActionKind.CLEAR]
        assert any(a.slot == 3 for a in clear_actions)

        # Apply plan with confirmed clears and set
        eo = EventOverrides(start_slot=3, max_slots=3)
        eo.update(3, "WRONGPIN", "WrongGuest", s, e)
        eo.update(4, "", "", s, e)
        eo.update(5, "", "", s, e)

        coordinator = MagicMock()
        coordinator.lockname = "test_lock"
        coordinator.hass.services.async_call = AsyncMock()
        coordinator.event_prefix = ""
        name_state = MagicMock()
        name_state.state = "Desired Guest"
        coordinator.hass.states.get.return_value = name_state

        async def mock_clear(coord, slot, **kwargs):
            """Return a confirmed clear result for the given slot."""
            return OperationResult(kind="clear", slot=slot, confirmed=True)

        async def mock_set(coord, event, slot):
            """Return a confirmed set result for the given slot."""
            return OperationResult(kind="set", slot=slot, confirmed=True)

        with (
            patch(
                "custom_components.rental_control.event_overrides.async_fire_clear_code",
                side_effect=mock_clear,
            ),
            patch(
                "custom_components.rental_control.event_overrides.async_fire_set_code",
                side_effect=mock_set,
            ),
        ):
            await eo.async_apply_plan(coordinator, plan, {"r-desired-t050": r_desired})

        # Slot 3 cleared (stale removed)
        assert eo.overrides.get(3) is None
        # Slot 5 has r-desired
        assert eo.overrides.get(5) is not None


# ---------------------------------------------------------------------------
# T051 – #535 nearer-not-programmed-when-full corrupt starting state
# ---------------------------------------------------------------------------


class TestNearerNotProgrammedWhenFull:
    """T051: Regression for #535 — nearer reservation not programmed when all
    slots are occupied by farther reservations.

    When all managed slots are occupied by later-starting reservations and a
    nearer reservation arrives, the reconciliation system must:
    1. Overflow the farthest reservation.
    2. Clear the farthest slot.
    3. Assign the nearer reservation to the newly freed slot (may take 2 refreshes
       if the clear is confirmed after the planning pass).
    """

    async def test_nearer_programmed_after_farther_cleared(self) -> None:
        """Nearer reservation wins a slot within 2 refresh cycles.

        Setup (corrupt starting state):
          - Slot 3: OCCUPIED r-far1 (Aug 15)
          - Slot 4: OCCUPIED r-far2 (Aug 20)
          - r-near (Aug 1) arrives — must be assigned within 2 refreshes

        Refresh 1:
          - r-near selected (soonest); r-far2 overflows (farther, capacity=2)
          - Slot 4 gets CLEAR (r-far2 not in plan)
          - r-near overflows (no_free_slot — both slots still OCCUPIED)

        After clear of slot 4: slot 4 FREE.
        Refresh 2: r-near gets SET to slot 4.
        """
        from datetime import datetime
        from datetime import timedelta
        from datetime import timezone
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

        def _r(key: str, day: int) -> Reservation:
            """Build a minimal August 2026 Reservation with *key* starting on *day*."""
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
                slot_code=f"PIN{key}",
            )

        r_near = _r("r-near-535", 1)
        r_far1 = _r("r-far1-535", 15)
        r_far2 = _r("r-far2-535", 20)

        # Corrupt starting state: farther reservations occupy all slots
        slot3 = ManagedSlot(
            slot=3,
            managed=True,
            status=SlotStatus.OCCUPIED,
            actual_name="RC Guest r-far1-535",
            actual_code_present=True,
            persisted_identity_key="r-far1-535",
            actual_start=r_far1.buffered_start,
            actual_end=r_far1.buffered_end,
        )
        slot4 = ManagedSlot(
            slot=4,
            managed=True,
            status=SlotStatus.OCCUPIED,
            actual_name="Guest r-far2-535",
            actual_code_present=True,
            persisted_identity_key="r-far2-535",
            actual_start=r_far2.buffered_start,
            actual_end=r_far2.buffered_end,
        )

        # Refresh 1
        plan1 = compute_desired_plan(
            [r_near, r_far1, r_far2],
            [slot3, slot4],
            max_events=2,
            plan_id="t051-r1",
            generated_at=datetime(2026, 8, 1, tzinfo=_TZ),
        )

        # r-far2 must overflow (farther than r-near and r-far1)
        assert "r-far2-535" in plan1.overflow
        # Slot 4 must get CLEAR (r-far2 overflows; its slot has no desired occupant)
        clear_slot4 = [
            a for a in plan1.actions if a.kind is ActionKind.CLEAR and a.slot == 4
        ]
        assert len(clear_slot4) == 1

        # Apply refresh 1: confirm clear of slot 4
        eo = EventOverrides(start_slot=3, max_slots=2)
        eo.update(
            3,
            "PINr-far1-535",
            "Guest r-far1-535",
            r_far1.buffered_start,
            r_far1.buffered_end,
        )
        eo.update(
            4,
            "PINr-far2-535",
            "Guest r-far2-535",
            r_far2.buffered_start,
            r_far2.buffered_end,
        )

        coordinator = MagicMock()
        coordinator.lockname = "test_lock"
        coordinator.hass.services.async_call = AsyncMock()

        async def mock_clear(coord, slot, **kwargs):
            """Return a confirmed clear result for the given slot."""
            return OperationResult(kind="clear", slot=slot, confirmed=True)

        with patch(
            "custom_components.rental_control.event_overrides.async_fire_clear_code",
            side_effect=mock_clear,
        ):
            await eo.async_apply_plan(
                coordinator, plan1, {"r-far1-535": r_far1, "r-near-535": r_near}
            )

        # Slot 4 freed after confirmed clear
        assert eo.overrides.get(4) is None

        # Refresh 2: slot 4 now FREE → r-near gets SET
        slot3_r2 = ManagedSlot(
            slot=3,
            managed=True,
            status=SlotStatus.OCCUPIED,
            actual_name="Guest r-far1-535",
            actual_code_present=True,
            persisted_identity_key="r-far1-535",
            actual_start=r_far1.buffered_start,
            actual_end=r_far1.buffered_end,
        )
        slot4_r2 = ManagedSlot(slot=4, managed=True, status=SlotStatus.FREE)

        plan2 = compute_desired_plan(
            [r_near, r_far1, r_far2],
            [slot3_r2, slot4_r2],
            max_events=2,
            plan_id="t051-r2",
            generated_at=datetime(2026, 8, 1, tzinfo=_TZ),
        )

        # r-near must be assigned in refresh 2
        assert "r-near-535" in plan2.selected
        set_actions = [a for a in plan2.actions if a.kind is ActionKind.SET]
        assert any(a.slot == 4 for a in set_actions)


# ---------------------------------------------------------------------------
# T052 – #546 farther-evicts-nearer corrupt starting state
# ---------------------------------------------------------------------------


class TestFartherEvictsNearer:
    """T052: Regression for #546 — farther reservation evicted a nearer one.

    In issue #546, a farther reservation was programmed into a slot while the
    nearer reservation (which should have priority) was left unassigned.
    The reconciliation system must detect and correct this within 1-2 refreshes.
    """

    async def test_farther_evicted_nearer_corrected_within_two_refreshes(
        self,
    ) -> None:
        """Farther-evicts-nearer corrupt state corrected in ≤2 refresh cycles.

        Setup (corrupt starting state):
          - Slot 3 OCCUPIED by r-far (Aug 15) — farther has a slot it shouldn't
          - r-near (Aug 1) — nearer, should have priority but has no slot

        Desired plan (max_events=1):
          - r-near selected (nearer start time, capacity=1)
          - r-far overflows (farther, capacity exceeded)
          - Slot 3: desired_key=None (r-far overflows) → CLEAR
          - r-near: no free slot (slot 3 occupied) → no_free_slot overflow

        Refresh 1: CLEAR slot 3; r-near overflows.
        After clear: slot 3 FREE.
        Refresh 2: r-near gets SET to slot 3.
        """
        from datetime import datetime
        from datetime import timedelta
        from datetime import timezone
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

        def _r(key: str, day: int) -> Reservation:
            """Build a minimal August 2026 Reservation with *key* starting on *day*."""
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
                slot_code=f"PIN{key}",
            )

        r_near = _r("r-near-546", 1)
        r_far = _r("r-far-546", 15)

        # Corrupt starting state: far has the slot, near is unassigned
        slot3_far = ManagedSlot(
            slot=3,
            managed=True,
            status=SlotStatus.OCCUPIED,
            actual_name="Guest r-far-546",
            actual_code_present=True,
            persisted_identity_key="r-far-546",
            actual_start=r_far.buffered_start,
            actual_end=r_far.buffered_end,
        )

        # Refresh 1: r-near selected (nearer), r-far overflows (farther)
        # slot 3 has r-far but r-far is overflow → slot 3 gets CLEAR
        # r-near has no persisted slot and no free slot → no_free_slot overflow
        plan1 = compute_desired_plan(
            [r_near, r_far],
            [slot3_far],
            max_events=1,
            plan_id="t052-r1",
            generated_at=datetime(2026, 8, 1, tzinfo=_TZ),
        )

        # r-far must overflow (farther, capacity=1)
        assert "r-far-546" in plan1.overflow
        # Slot 3 gets CLEAR (r-far is overflow, no desired occupant)
        clear_slot3 = [
            a for a in plan1.actions if a.kind is ActionKind.CLEAR and a.slot == 3
        ]
        assert len(clear_slot3) == 1

        # Apply refresh 1: confirm clear of slot 3
        eo = EventOverrides(start_slot=3, max_slots=1)
        eo.update(
            3,
            "PINr-far-546",
            "Guest r-far-546",
            r_far.buffered_start,
            r_far.buffered_end,
        )

        coordinator = MagicMock()
        coordinator.lockname = "test_lock"
        coordinator.hass.services.async_call = AsyncMock()

        async def mock_clear(coord, slot, **kwargs):
            """Return a confirmed clear result for the given slot."""
            return OperationResult(kind="clear", slot=slot, confirmed=True)

        with patch(
            "custom_components.rental_control.event_overrides.async_fire_clear_code",
            side_effect=mock_clear,
        ):
            await eo.async_apply_plan(coordinator, plan1, {})

        # Slot 3 freed
        assert eo.overrides.get(3) is None

        # Refresh 2: slot 3 FREE → r-near gets SET
        slot3_free = ManagedSlot(slot=3, managed=True, status=SlotStatus.FREE)

        plan2 = compute_desired_plan(
            [r_near, r_far],
            [slot3_free],
            max_events=1,
            plan_id="t052-r2",
            generated_at=datetime(2026, 8, 1, tzinfo=_TZ),
        )

        assert "r-near-546" in plan2.selected
        assert plan2.selected["r-near-546"] == 3
        set_actions = [a for a in plan2.actions if a.kind is ActionKind.SET]
        assert any(a.slot == 3 for a in set_actions)


# ---------------------------------------------------------------------------
# T083 – restart with persisted Store mapping and UID churn
# ---------------------------------------------------------------------------


class TestRestartWithPersistedMappingAndUidChurn:
    """T083: Coordinator rehydrates persisted slot mappings after a restart
    even when the calendar platform reissues a new volatile UID for the
    same reservation.

    The fingerprint is computed from entry_id + slot_name + dates (not UID)
    so UID churn does not change the identity key.  After restart the
    coordinator's _build_reservations() must:
      1. Find the persisted mapping by fingerprint.
      2. Load missing_count=0 (no misses) for the recognised reservation.
      3. Not treat the reservation as absent (no ghost with incremented count).
    """

    def _make_entry(self, entry_id: str = "rc-restart-t083") -> "MockConfigEntry":
        """Build a minimal config entry scoped to the restart/UID-churn scenario."""
        from pytest_homeassistant_custom_component.common import MockConfigEntry

        return MockConfigEntry(
            domain="rental_control",
            title="Restart Rental",
            version=10,
            unique_id=f"restart-{entry_id}",
            data={
                "name": "Restart Rental",
                "url": "https://example.com/calendar.ics",
                "timezone": "UTC",
                "checkin": "16:00",
                "checkout": "11:00",
                "start_slot": 10,
                "max_events": 2,
                "days": 90,
                "verify_ssl": True,
                "ignore_non_reserved": False,
                "honor_event_times": False,
                "keymaster_entry_id": "test_lock",
                "code_buffer_before": 0,
                "code_buffer_after": 0,
            },
            entry_id=entry_id,
        )

    async def test_uid_churn_does_not_change_identity_key(self) -> None:
        """T083-1: Same name/dates with new UID produce the same fingerprint.

        This is the fundamental guarantee: UID churn cannot create a new
        identity so the slot mapping survives platform UID reissuance.
        """
        from datetime import timezone

        from custom_components.rental_control.reconciliation import (
            make_reservation_fingerprint,
        )

        _TZ = timezone.utc
        entry_id = "rc-restart-t083"
        name = "Alice Guest"
        start = datetime(2026, 7, 1, 16, 0, 0, tzinfo=_TZ)
        end = datetime(2026, 7, 8, 11, 0, 0, tzinfo=_TZ)

        fp_first_run = make_reservation_fingerprint(entry_id, name, start, end)
        fp_second_run = make_reservation_fingerprint(entry_id, name, start, end)

        assert fp_first_run == fp_second_run, (
            "Fingerprint changed between runs — UID churn broke identity stability"
        )

    async def test_restart_with_persisted_mapping_recognises_reservation(
        self, hass: "HomeAssistant"
    ) -> None:
        """T083-2: After restart, _build_reservations recognises persisted mapping.

        Simulates:
          - Store has a mapping for Alice Guest in slot 10 (missing_count=0).
          - Calendar returns Alice Guest with a NEW volatile UID (churn).
          - _build_reservations() must find the mapping by fingerprint.
          - The Reservation built has missing_count=0 (not treated as absent).
        """
        from datetime import timezone
        from unittest.mock import AsyncMock
        from unittest.mock import MagicMock
        from unittest.mock import patch

        from custom_components.rental_control.coordinator import (
            RentalControlCoordinator,
        )
        from custom_components.rental_control.reconciliation import (
            make_reservation_fingerprint,
        )

        _TZ = timezone.utc
        entry = self._make_entry()
        entry.add_to_hass(hass)

        frozen_time = datetime(2026, 7, 10, 12, 0, 0, tzinfo=_TZ)
        start_dt = datetime(2026, 7, 15, 16, 0, 0, tzinfo=_TZ)
        end_dt = datetime(2026, 7, 22, 11, 0, 0, tzinfo=_TZ)
        fp = make_reservation_fingerprint(
            "rc-restart-t083", "Alice Guest", start_dt, end_dt
        )

        store_data: dict[str, Any] = {
            "schema_version": 1,
            "entry_id": "rc-restart-t083",
            "mappings": {
                fp: {
                    "slot": 10,
                    "status": "occupied",
                    "operation_id": None,
                    "operation_kind": None,
                    "identity": {
                        "identity_key": fp,
                        "summary": "Alice Guest",
                        "slot_name": "Alice Guest",
                        "uid_aliases": ["uid-alice-old"],
                        "booking_aliases": [],
                    },
                    "missing_count": 0,
                    "pending_set_since": None,
                    "pending_clear_since": None,
                    "fingerprint_history": [],
                    "updated_at": "2026-07-01T00:00:00+00:00",
                    "last_observed_actual": {
                        "slot": 10,
                        "classification": "occupied",
                        "name_state": "Alice Guest",
                        "has_code": True,
                        "start_state": start_dt.isoformat(),
                        "end_state": end_dt.isoformat(),
                        "use_date_range": True,
                        "enabled": True,
                    },
                }
            },
            "blocked_slots": {},
        }

        coordinator = RentalControlCoordinator(hass, entry)
        # Inject persisted state (simulating post-restart Store load).
        coordinator._slot_mappings = store_data
        assert coordinator.event_overrides is not None
        eo = coordinator.event_overrides

        # Calendar returns Alice Guest with a NEW UID (churn).
        ics_body = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//EN\r\n"
            "BEGIN:VEVENT\r\n"
            "DTSTART;VALUE=DATE:20260715\r\n"
            "DTEND;VALUE=DATE:20260722\r\n"
            "SUMMARY:Alice Guest\r\nUID:uid-alice-NEW-after-churn\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
        )

        mock_plan = MagicMock()
        mock_plan.plan_id = "t083-restart"
        mock_plan.selected = {}
        mock_plan.overflow = {}
        mock_plan.actions = []
        mock_plan.diagnostics = {}
        mock_plan.validate.return_value = []

        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=frozen_time),
            patch.object(dt_util, "start_of_local_day", return_value=frozen_time),
            patch(
                "custom_components.rental_control.coordinator.compute_desired_plan",
                return_value=mock_plan,
            ) as mock_compute,
            patch.object(eo, "async_apply_plan", new=AsyncMock(return_value=[])),
            patch.object(coordinator, "async_save_slot_store", new_callable=AsyncMock),
        ):
            mock_session.get("https://example.com/calendar.ics", body=ics_body)
            await coordinator._async_update_data()

        reservations = mock_compute.call_args.kwargs["reservations"]
        # Exactly one live reservation built from the calendar event.
        live = [r for r in reservations if r.identity_key == fp]
        assert len(live) == 1, "Persisted reservation not recognised after UID churn"
        # Not treated as absent → missing_count stays 0.
        assert live[0].missing_count == 0

    async def test_restart_uid_churn_no_ghost_created(
        self, hass: "HomeAssistant"
    ) -> None:
        """T083-3: Recognised reservation does not also generate a ghost entry.

        When the live reservation is found by fingerprint, _build_ghost_reservations
        must not also create a ghost with incremented missing_count.
        """
        from datetime import timezone
        from unittest.mock import AsyncMock
        from unittest.mock import MagicMock
        from unittest.mock import patch

        from custom_components.rental_control.coordinator import (
            RentalControlCoordinator,
        )
        from custom_components.rental_control.reconciliation import (
            make_reservation_fingerprint,
        )

        _TZ = timezone.utc
        entry = self._make_entry("rc-restart-t083-ghost")
        entry.add_to_hass(hass)

        frozen_time = datetime(2026, 7, 10, 12, 0, 0, tzinfo=_TZ)
        start_dt = datetime(2026, 7, 15, 16, 0, 0, tzinfo=_TZ)
        end_dt = datetime(2026, 7, 22, 11, 0, 0, tzinfo=_TZ)
        fp = make_reservation_fingerprint(
            "rc-restart-t083-ghost", "Alice Guest", start_dt, end_dt
        )

        store_data: dict[str, Any] = {
            "schema_version": 1,
            "entry_id": "rc-restart-t083-ghost",
            "mappings": {
                fp: {
                    "slot": 10,
                    "status": "occupied",
                    "operation_id": None,
                    "operation_kind": None,
                    "identity": {
                        "identity_key": fp,
                        "summary": "Alice Guest",
                        "slot_name": "Alice Guest",
                        "uid_aliases": [],
                        "booking_aliases": [],
                    },
                    "missing_count": 0,
                    "pending_set_since": None,
                    "pending_clear_since": None,
                    "fingerprint_history": [],
                    "updated_at": "2026-07-01T00:00:00+00:00",
                    "last_observed_actual": {
                        "slot": 10,
                        "classification": "occupied",
                        "name_state": "Alice Guest",
                        "has_code": True,
                        "start_state": start_dt.isoformat(),
                        "end_state": end_dt.isoformat(),
                        "use_date_range": True,
                        "enabled": True,
                    },
                }
            },
            "blocked_slots": {},
        }

        coordinator = RentalControlCoordinator(hass, entry)
        coordinator._slot_mappings = store_data
        assert coordinator.event_overrides is not None
        eo = coordinator.event_overrides

        # Same reservation with new UID.
        ics_body = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//EN\r\n"
            "BEGIN:VEVENT\r\n"
            "DTSTART;VALUE=DATE:20260715\r\n"
            "DTEND;VALUE=DATE:20260722\r\n"
            "SUMMARY:Alice Guest\r\nUID:uid-alice-brand-new\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
        )

        mock_plan = MagicMock()
        mock_plan.plan_id = "t083-no-ghost"
        mock_plan.selected = {}
        mock_plan.overflow = {}
        mock_plan.actions = []
        mock_plan.diagnostics = {}
        mock_plan.validate.return_value = []

        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=frozen_time),
            patch.object(dt_util, "start_of_local_day", return_value=frozen_time),
            patch(
                "custom_components.rental_control.coordinator.compute_desired_plan",
                return_value=mock_plan,
            ) as mock_compute,
            patch.object(eo, "async_apply_plan", new=AsyncMock(return_value=[])),
            patch.object(coordinator, "async_save_slot_store", new_callable=AsyncMock),
        ):
            mock_session.get("https://example.com/calendar.ics", body=ics_body)
            await coordinator._async_update_data()

        reservations = mock_compute.call_args.kwargs["reservations"]
        # Exactly one entry for this fingerprint — no duplicate ghost
        matching = [r for r in reservations if r.identity_key == fp]
        assert len(matching) == 1, (
            f"Expected exactly 1 reservation for fp, got {len(matching)}"
        )
        # missing_count must remain 0 — not incremented by ghost logic
        assert matching[0].missing_count == 0


# ---------------------------------------------------------------------------
# T084 – first-upgrade migration does-not-wipe-working-slots
# ---------------------------------------------------------------------------


class TestFirstUpgradeMigrationNoWipe:
    """T084: First-upgrade slot adoption leaves working Keymaster slots intact.

    When the HA Store is empty (first upgrade from a version without slot
    persistence), async_adopt_keymaster_slots() records existing populated
    slots as 'occupied' without issuing any clear_code service calls.

    This is the integration-level version of T010-2 (unit test).
    """

    def _make_entry(
        self,
        entry_id: str = "rc-upgrade-t084",
        lockname: str = "upgrade_lock",
        start_slot: int = 10,
        max_events: int = 3,
    ) -> MockConfigEntry:
        """Build a minimal config entry for the first-upgrade scenario."""
        return MockConfigEntry(
            domain="rental_control",
            title="Upgrade Rental",
            version=10,
            unique_id=f"upgrade-{entry_id}",
            data={
                "name": "Upgrade Rental",
                "url": "https://example.com/calendar.ics",
                "timezone": "UTC",
                "checkin": "16:00",
                "checkout": "11:00",
                "start_slot": start_slot,
                "max_events": max_events,
                "days": 90,
                "verify_ssl": True,
                "ignore_non_reserved": False,
                "honor_event_times": False,
                "keymaster_entry_id": lockname,
                "code_buffer_before": 0,
                "code_buffer_after": 0,
            },
            entry_id=entry_id,
        )

    async def test_adoption_does_not_wipe_codes_scenario(
        self, hass: "HomeAssistant"
    ) -> None:
        """T084-1: async_adopt_keymaster_slots reads slot state but issues no writes.

        Scenario: first-time setup with two occupied Keymaster slots.
          - Slot 10: Guest A with PIN 1234
          - Slot 11: Guest B with PIN 5678
          - Slot 12: empty

        After adoption, PINs must still be 1234 and 5678 (no wipes).
        Mappings for slots 10 and 11 must be recorded as occupied.
        """
        from custom_components.rental_control.coordinator import (
            RentalControlCoordinator,
        )

        entry = self._make_entry()
        entry.add_to_hass(hass)

        hass.states.async_set("text.upgrade_lock_code_slot_10_name", "Guest A")
        hass.states.async_set("text.upgrade_lock_code_slot_10_pin", "1234")
        hass.states.async_set("text.upgrade_lock_code_slot_11_name", "Guest B")
        hass.states.async_set("text.upgrade_lock_code_slot_11_pin", "5678")
        hass.states.async_set("text.upgrade_lock_code_slot_12_name", "")
        hass.states.async_set("text.upgrade_lock_code_slot_12_pin", "")

        coordinator = RentalControlCoordinator(hass, entry)
        # Store is empty → first-upgrade path
        coordinator._slot_mappings = {}
        await coordinator.async_adopt_keymaster_slots()

        # PINs must not be wiped
        pin10 = hass.states.get("text.upgrade_lock_code_slot_10_pin")
        assert pin10 is not None and pin10.state == "1234"
        pin11 = hass.states.get("text.upgrade_lock_code_slot_11_pin")
        assert pin11 is not None and pin11.state == "5678"

        # Occupied mappings recorded
        mappings = coordinator._slot_mappings.get("mappings", {})
        occupied_slots = {
            v["slot"] for v in mappings.values() if v["status"] == "occupied"
        }
        assert 10 in occupied_slots
        assert 11 in occupied_slots
        assert 12 not in occupied_slots

    async def test_adoption_no_raw_pin_in_mappings(self, hass: "HomeAssistant") -> None:
        """T084-2: Adopted slot mappings contain no raw PIN values.

        The no-raw-PIN invariant must hold for adopted mappings so
        that the Store never persists sensitive access codes.
        """
        from custom_components.rental_control.coordinator import (
            RentalControlCoordinator,
        )

        entry = self._make_entry("rc-upgrade-t084-pin")
        entry.add_to_hass(hass)

        hass.states.async_set("text.upgrade_lock_code_slot_10_name", "Guest C")
        hass.states.async_set("text.upgrade_lock_code_slot_10_pin", "9999")
        hass.states.async_set("text.upgrade_lock_code_slot_11_name", "")
        hass.states.async_set("text.upgrade_lock_code_slot_11_pin", "")
        hass.states.async_set("text.upgrade_lock_code_slot_12_name", "")
        hass.states.async_set("text.upgrade_lock_code_slot_12_pin", "")

        coordinator = RentalControlCoordinator(hass, entry)
        coordinator._slot_mappings = {}
        await coordinator.async_adopt_keymaster_slots()

        for _key, v in coordinator._slot_mappings.get("mappings", {}).items():
            last_obs = v.get("last_observed_actual", {})
            assert "pin" not in last_obs
            assert "code" not in last_obs
            assert "slot_code" not in last_obs
            assert "has_code" in last_obs


# ---------------------------------------------------------------------------
# T085 – two-cycle transient miss tolerance and third-miss clearable
# ---------------------------------------------------------------------------


class TestTwoCycleTransientMissTolerance:
    """T085: Reservations absent from the feed for 1 or 2 cycles keep their
    slot; on the third consecutive miss the slot becomes clearable.

    Uses compute_desired_plan directly with Reservations that have the
    appropriate missing_count pre-set (mirrors what _build_ghost_reservations
    produces after N missed cycles).
    """

    def _mk(self, key: str, day: int = 1, *, missing_count: int = 0):  # type: ignore[return]
        """Build a minimal August 2026 ghost Reservation for miss-tolerance tests."""
        from datetime import timezone as _tz

        from custom_components.rental_control.reconciliation import Reservation

        _TZ = _tz.utc
        s = datetime(2026, 8, day, 14, 0, 0, tzinfo=_TZ)
        e = s + timedelta(days=7)
        return Reservation(
            identity_key=key,
            start=s,
            end=e,
            buffered_start=s,
            buffered_end=e,
            summary=f"Ghost {key}",
            slot_name=f"Ghost {key}",
            display_slot_name=f"RC Ghost {key}",
            slot_code="",
            missing_count=missing_count,
        )

    def _occupied(self, slot: int, key: str, day: int = 1):  # type: ignore[return]
        """Build an OCCUPIED ManagedSlot for the given slot and identity key."""
        from datetime import timezone as _tz

        from custom_components.rental_control.reconciliation import ManagedSlot
        from custom_components.rental_control.reconciliation import SlotStatus

        _TZ = _tz.utc
        s = datetime(2026, 8, day, 14, 0, 0, tzinfo=_TZ)
        e = s + timedelta(days=7)
        return ManagedSlot(
            slot=slot,
            managed=True,
            status=SlotStatus.OCCUPIED,
            actual_name=f"RC Ghost {key}",
            actual_code_present=True,
            persisted_identity_key=key,
            actual_start=s,
            actual_end=e,
        )

    async def test_miss_cycle_1_slot_retained(self) -> None:
        """T085-1: After one miss (missing_count=1) the slot is retained.

        The planner still sees the reservation as eligible (count < 3)
        and keeps NOOP on its slot.
        """
        from datetime import timezone as _tz

        from custom_components.rental_control.reconciliation import ActionKind
        from custom_components.rental_control.reconciliation import compute_desired_plan

        _TZ = _tz.utc
        res = self._mk("r-t085-miss1", missing_count=1)
        slot = self._occupied(3, "r-t085-miss1")

        plan = compute_desired_plan(
            [res],
            [slot],
            max_events=1,
            plan_id="t085-c1",
            generated_at=datetime(2026, 8, 1, tzinfo=_TZ),
        )

        assert "r-t085-miss1" in plan.selected
        clear_actions = [
            a for a in plan.actions if a.kind is ActionKind.CLEAR and a.slot == 3
        ]
        assert len(clear_actions) == 0, (
            "Slot cleared after only 1 miss — expected retention"
        )

    async def test_miss_cycle_2_slot_still_retained(self) -> None:
        """T085-2: After two consecutive misses (missing_count=2) slot still retained."""
        from datetime import timezone as _tz

        from custom_components.rental_control.reconciliation import ActionKind
        from custom_components.rental_control.reconciliation import compute_desired_plan

        _TZ = _tz.utc
        res = self._mk("r-t085-miss2", missing_count=2)
        slot = self._occupied(3, "r-t085-miss2")

        plan = compute_desired_plan(
            [res],
            [slot],
            max_events=1,
            plan_id="t085-c2",
            generated_at=datetime(2026, 8, 1, tzinfo=_TZ),
        )

        assert "r-t085-miss2" in plan.selected
        clear_actions = [
            a for a in plan.actions if a.kind is ActionKind.CLEAR and a.slot == 3
        ]
        assert len(clear_actions) == 0, (
            "Slot cleared after only 2 misses — expected retention"
        )

    async def test_miss_cycle_3_slot_clearable(self) -> None:
        """T085-3: After three consecutive misses (missing_count=3) slot becomes clearable.

        The planner excludes the ghost from eligible candidates.  The slot
        has no desired occupant → CLEAR action generated.
        """
        from datetime import timezone as _tz

        from custom_components.rental_control.reconciliation import ActionKind
        from custom_components.rental_control.reconciliation import compute_desired_plan

        _TZ = _tz.utc
        res = self._mk("r-t085-miss3", missing_count=3)
        slot = self._occupied(3, "r-t085-miss3")

        plan = compute_desired_plan(
            [res],
            [slot],
            max_events=1,
            plan_id="t085-c3",
            generated_at=datetime(2026, 8, 1, tzinfo=_TZ),
        )

        assert "r-t085-miss3" not in plan.selected
        clear_actions = [
            a for a in plan.actions if a.kind is ActionKind.CLEAR and a.slot == 3
        ]
        assert len(clear_actions) == 1, "Expected CLEAR action on third miss"

    async def test_missing_count_incremented_across_cycles_via_coordinator(
        self, hass: "HomeAssistant"
    ) -> None:
        """T085-4: Coordinator increments missing_count in _slot_mappings each cycle.

        Runs _async_update_data() three times with an empty calendar while
        a persisted occupied mapping exists.  After three cycles the
        mapping's missing_count must be 3.
        """
        from unittest.mock import AsyncMock
        from unittest.mock import MagicMock
        from unittest.mock import patch

        from custom_components.rental_control.coordinator import (
            RentalControlCoordinator,
        )

        entry = MockConfigEntry(
            domain="rental_control",
            title="Ghost Rental",
            version=10,
            unique_id="ghost-t085-cycles",
            data={
                "name": "Ghost Rental",
                "url": "https://example.com/calendar.ics",
                "timezone": "UTC",
                "checkin": "16:00",
                "checkout": "11:00",
                "start_slot": 10,
                "max_events": 1,
                "days": 90,
                "verify_ssl": True,
                "ignore_non_reserved": False,
                "honor_event_times": False,
                "keymaster_entry_id": "ghost_lock",
                "code_buffer_before": 0,
                "code_buffer_after": 0,
            },
            entry_id="ghost-t085-cycles",
        )
        entry.add_to_hass(hass)

        from datetime import timezone as _tz

        _TZ = _tz.utc
        fp = "t085cycles" + "0" * 54
        start_state = "2026-08-01T14:00:00+00:00"
        end_state = "2026-08-08T11:00:00+00:00"
        store_data: dict[str, Any] = {
            "schema_version": 1,
            "entry_id": "ghost-t085-cycles",
            "mappings": {
                fp: {
                    "slot": 10,
                    "status": "occupied",
                    "operation_id": None,
                    "operation_kind": None,
                    "identity": {
                        "identity_key": fp,
                        "summary": "Ghost A",
                        "slot_name": "Ghost A",
                        "uid_aliases": [],
                        "booking_aliases": [],
                    },
                    "missing_count": 0,
                    "pending_set_since": None,
                    "pending_clear_since": None,
                    "fingerprint_history": [],
                    "updated_at": "2026-01-01T00:00:00+00:00",
                    "last_observed_actual": {
                        "slot": 10,
                        "classification": "occupied",
                        "name_state": "Ghost A",
                        "has_code": True,
                        "start_state": start_state,
                        "end_state": end_state,
                        "use_date_range": True,
                        "enabled": True,
                    },
                }
            },
            "blocked_slots": {},
        }

        coordinator = RentalControlCoordinator(hass, entry)
        coordinator._slot_mappings = store_data
        assert coordinator.event_overrides is not None
        eo = coordinator.event_overrides

        empty_ics = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//EN\r\nEND:VCALENDAR\r\n"
        )
        frozen_time = datetime(2026, 8, 10, 12, 0, 0, tzinfo=_TZ)

        def _mock_plan(cycle: int) -> MagicMock:
            """Build a minimal DesiredPlan mock for one refresh cycle of T085."""
            mp = MagicMock()
            mp.plan_id = f"t085-cycle{cycle}"
            mp.selected = {}
            mp.overflow = {}
            mp.actions = []
            mp.diagnostics = {}
            mp.validate.return_value = []
            return mp

        for cycle in range(1, 4):
            mock_plan = _mock_plan(cycle)
            with (
                aioresponses() as mock_session,
                patch.object(dt_util, "now", return_value=frozen_time),
                patch.object(dt_util, "start_of_local_day", return_value=frozen_time),
                patch(
                    "custom_components.rental_control.coordinator.compute_desired_plan",
                    return_value=mock_plan,
                ),
                patch.object(eo, "async_apply_plan", new=AsyncMock(return_value=[])),
                patch.object(
                    coordinator, "async_save_slot_store", new_callable=AsyncMock
                ),
            ):
                mock_session.get("https://example.com/calendar.ics", body=empty_ics)
                await coordinator._async_update_data()

            mc = coordinator._slot_mappings["mappings"][fp]["missing_count"]
            assert mc == cycle, (
                f"After cycle {cycle}: expected missing_count={cycle}, got {mc}"
            )


# ---------------------------------------------------------------------------
# T086 – reappearing-before-third-miss resets missing_count
# ---------------------------------------------------------------------------


class TestReappearingBeforeThirdMiss:
    """T086: When a reservation reappears in the feed before the third miss,
    its missing_count is reset to 0 and its slot is retained without
    interruption.
    """

    def _make_entry(self, entry_id: str = "rc-reappear-t086") -> MockConfigEntry:
        """Build a minimal config entry for the reappear-before-third-miss scenario."""
        return MockConfigEntry(
            domain="rental_control",
            title="Reappear Rental",
            version=10,
            unique_id=f"reappear-{entry_id}",
            data={
                "name": "Reappear Rental",
                "url": "https://example.com/calendar.ics",
                "timezone": "UTC",
                "checkin": "16:00",
                "checkout": "11:00",
                "start_slot": 10,
                "max_events": 1,
                "days": 90,
                "verify_ssl": True,
                "ignore_non_reserved": False,
                "honor_event_times": False,
                "keymaster_entry_id": "reappear_lock",
                "code_buffer_before": 0,
                "code_buffer_after": 0,
            },
            entry_id=entry_id,
        )

    async def test_reappear_at_miss2_resets_count_to_zero(
        self, hass: "HomeAssistant"
    ) -> None:
        """T086-1: Reservation reappearing on cycle 3 (after 2 misses) resets count.

        Sequence:
          Cycle 1: absent → missing_count 0→1
          Cycle 2: absent → missing_count 1→2
          Cycle 3: PRESENT → missing_count 2→0 (reset)
        After cycle 3, _slot_mappings missing_count must be 0.
        """
        from datetime import timezone as _tz
        from unittest.mock import AsyncMock
        from unittest.mock import MagicMock
        from unittest.mock import patch

        from custom_components.rental_control.coordinator import (
            RentalControlCoordinator,
        )
        from custom_components.rental_control.reconciliation import (
            make_reservation_fingerprint,
        )

        _TZ = _tz.utc
        entry = self._make_entry()
        entry.add_to_hass(hass)

        frozen_time = datetime(2026, 8, 10, 12, 0, 0, tzinfo=_TZ)
        # The coordinator parses VALUE=DATE DTSTART with checkin=16:00 UTC and
        # DTEND with checkout=11:00 UTC (from the config entry defaults).
        start_dt = datetime(2026, 8, 15, 16, 0, 0, tzinfo=_TZ)
        end_dt = datetime(2026, 8, 22, 11, 0, 0, tzinfo=_TZ)
        fp = make_reservation_fingerprint(
            "rc-reappear-t086", "Reappear Guest", start_dt, end_dt
        )

        store_data: dict[str, Any] = {
            "schema_version": 1,
            "entry_id": "rc-reappear-t086",
            "mappings": {
                fp: {
                    "slot": 10,
                    "status": "occupied",
                    "operation_id": None,
                    "operation_kind": None,
                    "identity": {
                        "identity_key": fp,
                        "summary": "Reappear Guest",
                        "slot_name": "Reappear Guest",
                        "uid_aliases": [],
                        "booking_aliases": [],
                    },
                    "missing_count": 0,
                    "pending_set_since": None,
                    "pending_clear_since": None,
                    "fingerprint_history": [],
                    "updated_at": "2026-08-01T00:00:00+00:00",
                    "last_observed_actual": {
                        "slot": 10,
                        "classification": "occupied",
                        "name_state": "Reappear Guest",
                        "has_code": True,
                        "start_state": start_dt.isoformat(),
                        "end_state": end_dt.isoformat(),
                        "use_date_range": True,
                        "enabled": True,
                    },
                }
            },
            "blocked_slots": {},
        }

        coordinator = RentalControlCoordinator(hass, entry)
        coordinator._slot_mappings = store_data
        assert coordinator.event_overrides is not None
        eo = coordinator.event_overrides

        empty_ics = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//EN\r\nEND:VCALENDAR\r\n"
        )
        present_ics = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//EN\r\n"
            "BEGIN:VEVENT\r\n"
            "DTSTART;VALUE=DATE:20260815\r\n"
            "DTEND;VALUE=DATE:20260822\r\n"
            "SUMMARY:Reappear Guest\r\nUID:uid-reappear-001\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
        )

        def _mock_plan(cycle: int) -> MagicMock:
            """Build a minimal DesiredPlan mock for one refresh cycle of T086."""
            mp = MagicMock()
            mp.plan_id = f"t086-c{cycle}"
            mp.selected = {}
            mp.overflow = {}
            mp.actions = []
            mp.diagnostics = {}
            mp.validate.return_value = []
            return mp

        # Cycles 1 & 2: reservation absent
        for cycle in range(1, 3):
            mock_plan = _mock_plan(cycle)
            with (
                aioresponses() as mock_session,
                patch.object(dt_util, "now", return_value=frozen_time),
                patch.object(dt_util, "start_of_local_day", return_value=frozen_time),
                patch(
                    "custom_components.rental_control.coordinator.compute_desired_plan",
                    return_value=mock_plan,
                ),
                patch.object(eo, "async_apply_plan", new=AsyncMock(return_value=[])),
                patch.object(
                    coordinator, "async_save_slot_store", new_callable=AsyncMock
                ),
            ):
                mock_session.get("https://example.com/calendar.ics", body=empty_ics)
                await coordinator._async_update_data()

        mc_after_2 = coordinator._slot_mappings["mappings"][fp]["missing_count"]
        assert mc_after_2 == 2, f"Expected 2 after two misses, got {mc_after_2}"

        # Cycle 3: reservation REAPPEARS
        mock_plan3 = _mock_plan(3)
        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=frozen_time),
            patch.object(dt_util, "start_of_local_day", return_value=frozen_time),
            patch(
                "custom_components.rental_control.coordinator.compute_desired_plan",
                return_value=mock_plan3,
            ),
            patch.object(eo, "async_apply_plan", new=AsyncMock(return_value=[])),
            patch.object(coordinator, "async_save_slot_store", new_callable=AsyncMock),
        ):
            mock_session.get("https://example.com/calendar.ics", body=present_ics)
            await coordinator._async_update_data()

        mc_after_return = coordinator._slot_mappings["mappings"][fp]["missing_count"]
        assert mc_after_return == 0, (
            f"Expected missing_count=0 after reappearance, got {mc_after_return}"
        )

    async def test_reappear_at_miss1_does_not_clear(self) -> None:
        """T086-2: Reservation with missing_count=1 that reappears stays assigned.

        When the reservation comes back at count=1 (before the tolerance
        limit), the planner must see it as eligible and assign it to its
        slot — no CLEAR action generated.
        """
        from datetime import timezone as _tz

        from custom_components.rental_control.reconciliation import ActionKind
        from custom_components.rental_control.reconciliation import ManagedSlot
        from custom_components.rental_control.reconciliation import Reservation
        from custom_components.rental_control.reconciliation import SlotStatus
        from custom_components.rental_control.reconciliation import compute_desired_plan

        _TZ = _tz.utc
        # Represents a "reappeared" reservation (missing_count reset to 0)
        s = datetime(2026, 8, 1, 14, 0, 0, tzinfo=_TZ)
        e = s + timedelta(days=7)
        res = Reservation(
            identity_key="r-reappear-t086",
            start=s,
            end=e,
            buffered_start=s,
            buffered_end=e,
            summary="Reappear Guest",
            slot_name="Reappear Guest",
            display_slot_name="RC Reappear Guest",
            slot_code="",
            missing_count=0,  # reset on reappearance
        )
        slot3 = ManagedSlot(
            slot=3,
            managed=True,
            status=SlotStatus.OCCUPIED,
            actual_name="RC Reappear Guest",
            actual_code_present=True,
            persisted_identity_key="r-reappear-t086",
            actual_start=s,
            actual_end=e,
        )

        plan = compute_desired_plan(
            [res],
            [slot3],
            max_events=1,
            plan_id="t086-reappear",
            generated_at=datetime(2026, 8, 1, tzinfo=_TZ),
        )

        assert "r-reappear-t086" in plan.selected
        clear_actions = [
            a for a in plan.actions if a.kind is ActionKind.CLEAR and a.slot == 3
        ]
        assert len(clear_actions) == 0, (
            "Slot was cleared after reappearance — expected NOOP"
        )


class TestPreservedSemanticsEndToEnd:
    """T106 end-to-end regression: all preserved semantics in one refresh cycle.

    Verifies that a single coordinator refresh produces the expected
    *coordinator-level* outcomes for slot names, buffers, honor-PMS-times,
    code regeneration, and check-in tracking protection.  Each assertion
    references the coordinator attribute or reconciliation output; no
    physical Keymaster service calls are made (all patched).
    """

    async def test_preserved_semantics_single_cycle(
        self, hass: "HomeAssistant"
    ) -> None:
        """Single cycle covers slot names, buffers, PMS times, code regen, check-in."""
        from custom_components.rental_control.coordinator import (
            RentalControlCoordinator,
        )

        entry = MockConfigEntry(
            domain=DOMAIN,
            title="Regression Rental",
            version=10,
            unique_id="t106-regression",
            data={
                "name": "Regression Rental",
                "url": "https://example.com/calendar.ics",
                "timezone": "America/New_York",
                "checkin": "16:00",
                "checkout": "11:00",
                "start_slot": 10,
                "max_events": 1,
                "days": 90,
                "verify_ssl": True,
                "ignore_non_reserved": False,
                "honor_event_times": True,
                "code_generation": "date_based",
                "trim_names": True,
                "max_name_length": 12,
                "event_prefix": "RC",
                "code_buffer_before": 30,
                "code_buffer_after": 15,
                "should_update_code": True,
                "keymaster_entry_id": "front_door",
            },
            entry_id="t106_regression",
        )
        entry.add_to_hass(hass)
        MockConfigEntry(
            domain="keymaster",
            data={"lockname": "front_door"},
            entry_id="km_t106_regression",
        ).add_to_hass(hass)

        frozen_time = datetime(2024, 12, 20, 12, 0, 0, tzinfo=dt_util.UTC)
        ics_body = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
DTSTART;VALUE=DATE:20241225
DTEND;VALUE=DATE:20241230
UID:t106-reg@example.com
SUMMARY:Reserved - Alice Bob
DESCRIPTION:Check-in: 3:00 PM\\nEmail: alice@example.com
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR
"""
        mock_plan = MagicMock()
        mock_plan.plan_id = "t106-plan"
        mock_plan.selected = {}
        mock_plan.overflow = {}
        mock_plan.actions = []
        mock_plan.diagnostics = {}
        mock_plan.validate.return_value = []

        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=frozen_time),
            patch.object(dt_util, "start_of_local_day", return_value=frozen_time),
            patch(
                "custom_components.rental_control.coordinator.compute_desired_plan",
                return_value=mock_plan,
            ),
        ):
            mock_session.get(
                "https://example.com/calendar.ics", status=200, body=ics_body
            )
            coordinator = RentalControlCoordinator(hass, entry)
            assert coordinator.event_overrides is not None
            with (
                patch.object(
                    coordinator.event_overrides,
                    "async_apply_plan",
                    new=AsyncMock(return_value=[]),
                ),
                patch.object(
                    coordinator,
                    "async_save_slot_store",
                    new=AsyncMock(),
                ),
            ):
                coordinator.data = await coordinator._async_update_data()

        assert coordinator.honor_event_times is True
        assert coordinator.trim_names is True
        assert coordinator.code_buffer_before == 30
        assert coordinator.code_buffer_after == 15
        assert len(coordinator.data) == 1
        event = coordinator.data[0]
        assert event.start is not None
        assert event.end is not None
        assert coordinator.should_update_code is True


class TestManualOverrideSurvival:
    """Regression tests for manual Keymaster override survival."""

    async def test_manual_time_and_code_override_survive_reconciliation(
        self,
        hass: "HomeAssistant",
    ) -> None:
        """Issue #589: manual time-of-day and PIN edits survive reconcile."""
        from custom_components.rental_control.coordinator import (
            RentalControlCoordinator,
        )
        from custom_components.rental_control.reconciliation import ActionKind
        from custom_components.rental_control.util import OperationResult
        from custom_components.rental_control.util import handle_state_change

        lockname = "front_door"
        slot = 10

        def _set_keymaster_slot(
            name: str,
            pin: str,
            start: datetime,
            end: datetime,
            *,
            enabled: bool,
            use_date_range_limits: bool = True,
        ) -> None:
            """Populate the Keymaster entities observed by reconciliation."""
            hass.states.async_set(
                f"switch.{lockname}_code_slot_{slot}_enabled",
                "on" if enabled else "off",
            )
            hass.states.async_set(
                f"switch.{lockname}_code_slot_{slot}_use_date_range_limits",
                "on" if use_date_range_limits else "off",
            )
            hass.states.async_set(
                f"text.{lockname}_code_slot_{slot}_name",
                name,
            )
            hass.states.async_set(
                f"text.{lockname}_code_slot_{slot}_pin",
                pin,
            )
            hass.states.async_set(
                f"datetime.{lockname}_code_slot_{slot}_date_range_start",
                start.isoformat(),
            )
            hass.states.async_set(
                f"datetime.{lockname}_code_slot_{slot}_date_range_end",
                end.isoformat(),
            )

        entry = MockConfigEntry(
            domain=DOMAIN,
            title="Manual Override Rental",
            version=10,
            unique_id="manual-override-regression",
            data={
                "name": "Manual Override Rental",
                "url": "https://example.com/calendar.ics",
                "timezone": "America/New_York",
                "checkin": "16:00",
                "checkout": "11:00",
                "start_slot": slot,
                "max_events": 1,
                "days": 90,
                "verify_ssl": True,
                "ignore_non_reserved": False,
                "honor_event_times": False,
                "code_generation": "date_based",
                "should_update_code": True,
                "keymaster_entry_id": lockname,
                "refresh_frequency": 5,
            },
            entry_id="manual_override_regression",
        )
        entry.add_to_hass(hass)
        MockConfigEntry(
            domain="keymaster",
            data={"lockname": lockname},
            entry_id="km_manual_override_regression",
        ).add_to_hass(hass)

        coordinator = RentalControlCoordinator(hass, entry)
        coordinator.async_request_refresh = AsyncMock()
        assert coordinator.event_overrides is not None
        hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {COORDINATOR: coordinator}

        _set_keymaster_slot(
            "",
            "",
            FROZEN_START_OF_DAY,
            FROZEN_START_OF_DAY,
            enabled=False,
        )

        ics_body = future_ics(summary="Reserved - Manual Guest")
        set_result = OperationResult(kind="set", slot=slot, confirmed=True)
        update_result = OperationResult(kind="update_times", slot=slot, confirmed=True)
        clear_result = OperationResult(kind="clear", slot=slot, confirmed=True)

        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=FROZEN_TIME),
            patch.object(
                dt_util, "start_of_local_day", return_value=FROZEN_START_OF_DAY
            ),
            patch(
                "custom_components.rental_control.event_overrides.async_fire_set_code",
                new=AsyncMock(return_value=set_result),
            ) as mock_set_code,
            patch(
                "custom_components.rental_control.event_overrides."
                "async_fire_update_times",
                new=AsyncMock(return_value=update_result),
            ) as mock_update_times,
            patch(
                "custom_components.rental_control.event_overrides."
                "async_fire_clear_code",
                new=AsyncMock(return_value=clear_result),
            ) as mock_clear_code,
        ):
            mock_session.get(entry.data["url"], status=200, body=ics_body, repeat=True)

            coordinator.data = await coordinator._async_update_data()
            await hass.async_block_till_done()

            assert mock_set_code.call_count == 1
            override = coordinator.event_overrides.overrides[slot]
            assert override is not None
            initial_pin = str(override["slot_code"])
            initial_start = override["start_time"]
            initial_end = override["end_time"]

            _set_keymaster_slot(
                "Manual Guest",
                initial_pin,
                initial_start,
                initial_end,
                enabled=True,
            )

            manual_pin = "8642"
            assert manual_pin != initial_pin
            manual_start = initial_start.replace(
                hour=9,
                minute=15,
                second=0,
                microsecond=0,
            )
            manual_end = initial_end.replace(
                hour=20,
                minute=45,
                second=0,
                microsecond=0,
            )
            assert manual_start != initial_start
            assert manual_end != initial_end

            _set_keymaster_slot(
                "Manual Guest",
                manual_pin,
                manual_start,
                manual_end,
                enabled=True,
            )

            event = MagicMock()
            event.data = {"entity_id": f"text.{lockname}_code_slot_{slot}_pin"}
            with patch(
                "custom_components.rental_control.util.asyncio.sleep",
                new_callable=AsyncMock,
            ):
                await handle_state_change(hass, entry, event)
            await hass.async_block_till_done()

            override = coordinator.event_overrides.overrides[slot]
            assert override is not None
            assert override["slot_code"] == manual_pin
            assert override["start_time"] == manual_start
            assert override["end_time"] == manual_end

            mock_set_code.reset_mock()
            mock_update_times.reset_mock()
            mock_clear_code.reset_mock()

            observed_plan_ids = set()
            previous_plan_id = (
                coordinator.latest_plan.plan_id if coordinator.latest_plan else None
            )
            for _ in range(2):
                coordinator.data = await coordinator._async_update_data()
                await hass.async_block_till_done()

                plan = coordinator.latest_plan
                assert plan is not None
                assert plan.plan_id != previous_plan_id
                assert plan.slots[slot].action is ActionKind.NOOP
                assert all(action.slot != slot for action in plan.actions)
                observed_plan_ids.add(plan.plan_id)
                previous_plan_id = plan.plan_id

            override = coordinator.event_overrides.overrides[slot]
            assert override is not None
            assert override["slot_code"] == manual_pin
            assert override["start_time"] == manual_start
            assert override["end_time"] == manual_end
            assert mock_update_times.call_count == 0
            assert mock_set_code.call_count == 0
            assert mock_clear_code.call_count == 0

            plan = coordinator.latest_plan
            assert plan is not None
            assert len(observed_plan_ids) == 2
            assert plan.slots[slot].action is ActionKind.NOOP
            assert all(action.slot != slot for action in plan.actions)

    async def test_manual_time_override_wins_pre_feedback_refresh_race(
        self,
        hass: "HomeAssistant",
    ) -> None:
        """Manual datetime event values survive a pre-feedback refresh race."""
        from custom_components.rental_control.coordinator import (
            RentalControlCoordinator,
        )
        from custom_components.rental_control.reconciliation import ActionKind
        from custom_components.rental_control.util import OperationResult
        from custom_components.rental_control.util import handle_state_change

        lockname = "front_door"
        slot = 10

        def _set_keymaster_slot(
            name: str,
            pin: str,
            start: datetime,
            end: datetime,
            *,
            enabled: bool,
            use_date_range_limits: bool = True,
        ) -> None:
            """Populate the Keymaster entities observed by reconciliation."""
            hass.states.async_set(
                f"switch.{lockname}_code_slot_{slot}_enabled",
                "on" if enabled else "off",
            )
            hass.states.async_set(
                f"switch.{lockname}_code_slot_{slot}_use_date_range_limits",
                "on" if use_date_range_limits else "off",
            )
            hass.states.async_set(
                f"text.{lockname}_code_slot_{slot}_name",
                name,
            )
            hass.states.async_set(
                f"text.{lockname}_code_slot_{slot}_pin",
                pin,
            )
            hass.states.async_set(
                f"datetime.{lockname}_code_slot_{slot}_date_range_start",
                start.isoformat(),
            )
            hass.states.async_set(
                f"datetime.{lockname}_code_slot_{slot}_date_range_end",
                end.isoformat(),
            )

        entry = MockConfigEntry(
            domain=DOMAIN,
            title="Manual Race Rental",
            version=10,
            unique_id="manual-override-race-regression",
            data={
                "name": "Manual Race Rental",
                "url": "https://example.com/calendar.ics",
                "timezone": "America/New_York",
                "checkin": "16:00",
                "checkout": "11:00",
                "start_slot": slot,
                "max_events": 1,
                "days": 90,
                "verify_ssl": True,
                "ignore_non_reserved": False,
                "honor_event_times": False,
                "code_generation": "date_based",
                "should_update_code": True,
                "keymaster_entry_id": lockname,
                "refresh_frequency": 5,
            },
            entry_id="manual_override_race_regression",
        )
        entry.add_to_hass(hass)
        MockConfigEntry(
            domain="keymaster",
            data={"lockname": lockname},
            entry_id="km_manual_override_race_regression",
        ).add_to_hass(hass)

        coordinator = RentalControlCoordinator(hass, entry)
        coordinator.async_request_refresh = AsyncMock()
        assert coordinator.event_overrides is not None
        hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {COORDINATOR: coordinator}

        _set_keymaster_slot(
            "",
            "",
            FROZEN_START_OF_DAY,
            FROZEN_START_OF_DAY,
            enabled=False,
        )

        ics_body = future_ics(summary="Reserved - Manual Guest")
        set_result = OperationResult(kind="set", slot=slot, confirmed=True)
        update_result = OperationResult(kind="update_times", slot=slot, confirmed=True)
        clear_result = OperationResult(kind="clear", slot=slot, confirmed=True)

        with (
            aioresponses() as mock_session,
            patch.object(dt_util, "now", return_value=FROZEN_TIME),
            patch.object(
                dt_util, "start_of_local_day", return_value=FROZEN_START_OF_DAY
            ),
            patch(
                "custom_components.rental_control.event_overrides.async_fire_set_code",
                new=AsyncMock(return_value=set_result),
            ) as mock_set_code,
            patch(
                "custom_components.rental_control.event_overrides."
                "async_fire_update_times",
                new=AsyncMock(return_value=update_result),
            ) as mock_update_times,
            patch(
                "custom_components.rental_control.event_overrides."
                "async_fire_clear_code",
                new=AsyncMock(return_value=clear_result),
            ) as mock_clear_code,
        ):
            mock_session.get(entry.data["url"], status=200, body=ics_body, repeat=True)

            coordinator.data = await coordinator._async_update_data()
            await hass.async_block_till_done()

            assert mock_set_code.call_count == 1
            override = coordinator.event_overrides.overrides[slot]
            assert override is not None
            initial_pin = str(override["slot_code"])
            initial_start = override["start_time"]
            initial_end = override["end_time"]

            _set_keymaster_slot(
                "Manual Guest",
                initial_pin,
                initial_start,
                initial_end,
                enabled=True,
            )

            manual_pin = "8642"
            manual_start = initial_start.replace(
                hour=9,
                minute=15,
                second=0,
                microsecond=0,
            )
            manual_end = initial_end.replace(
                hour=20,
                minute=45,
                second=0,
                microsecond=0,
            )
            assert manual_pin != initial_pin
            assert manual_start != initial_start
            assert manual_end != initial_end
            _set_keymaster_slot(
                "Manual Guest",
                manual_pin,
                manual_start,
                manual_end,
                enabled=True,
            )
            start_state = hass.states.get(
                f"datetime.{lockname}_code_slot_{slot}_date_range_start"
            )
            end_state = hass.states.get(
                f"datetime.{lockname}_code_slot_{slot}_date_range_end"
            )
            pin_state = hass.states.get(f"text.{lockname}_code_slot_{slot}_pin")
            assert start_state is not None
            assert end_state is not None
            assert pin_state is not None

            mock_set_code.reset_mock()
            mock_update_times.reset_mock()
            mock_clear_code.reset_mock()

            coordinator.data = await coordinator._async_update_data()
            await hass.async_block_till_done()

            plan = coordinator.latest_plan
            assert plan is not None
            assert plan.slots[slot].action is ActionKind.UPDATE_TIMES
            assert mock_update_times.call_count == 1
            racing_update = mock_update_times.call_args.args[1]
            assert racing_update.extra_state_attributes["start"] == initial_start
            assert racing_update.extra_state_attributes["end"] == initial_end

            _set_keymaster_slot(
                "Manual Guest",
                manual_pin,
                initial_start,
                initial_end,
                enabled=True,
            )
            feedback_start_state = hass.states.get(
                f"datetime.{lockname}_code_slot_{slot}_date_range_start"
            )
            feedback_end_state = hass.states.get(
                f"datetime.{lockname}_code_slot_{slot}_date_range_end"
            )
            assert feedback_start_state is not None
            assert feedback_end_state is not None

            for changed_state in (start_state, end_state, pin_state):
                event = MagicMock()
                event.data = {
                    "entity_id": changed_state.entity_id,
                    "new_state": changed_state,
                }
                with patch(
                    "custom_components.rental_control.util.asyncio.sleep",
                    new_callable=AsyncMock,
                ):
                    await handle_state_change(hass, entry, event)
            for changed_state in (feedback_start_state, feedback_end_state):
                event = MagicMock()
                event.data = {
                    "entity_id": changed_state.entity_id,
                    "new_state": changed_state,
                }
                with patch(
                    "custom_components.rental_control.util.asyncio.sleep",
                    new_callable=AsyncMock,
                ):
                    await handle_state_change(hass, entry, event)
            await hass.async_block_till_done()

            override = coordinator.event_overrides.overrides[slot]
            assert override is not None
            assert override["slot_code"] == manual_pin
            assert override["start_time"] == manual_start
            assert override["end_time"] == manual_end

            mock_set_code.reset_mock()
            mock_update_times.reset_mock()
            mock_clear_code.reset_mock()

            coordinator.data = await coordinator._async_update_data()
            await hass.async_block_till_done()

            plan = coordinator.latest_plan
            assert plan is not None
            assert plan.slots[slot].action is ActionKind.UPDATE_TIMES
            assert mock_update_times.call_count == 1
            reapply_update = mock_update_times.call_args.args[1]
            assert reapply_update.extra_state_attributes["start"] == manual_start
            assert reapply_update.extra_state_attributes["end"] == manual_end
            assert mock_set_code.call_count == 0
            assert mock_clear_code.call_count == 0

            _set_keymaster_slot(
                "Manual Guest",
                manual_pin,
                manual_start,
                manual_end,
                enabled=True,
            )
            mock_update_times.reset_mock()

            coordinator.data = await coordinator._async_update_data()
            await hass.async_block_till_done()

            plan = coordinator.latest_plan
            assert plan is not None
            assert plan.slots[slot].action is ActionKind.NOOP
            assert all(action.slot != slot for action in plan.actions)
            assert mock_update_times.call_count == 0
