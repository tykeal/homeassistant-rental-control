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
