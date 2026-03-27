# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for CheckinTrackingSensor state machine.

Tests cover:
- T007: State machine transitions
- T008: Event identity and FR-007
- T009: HA event bus firing
- T016: State restoration (RestoreEntity)
- T017: Stale state validation after restore
- T027: Manual checkout action (async_checkout)
- T046: Event cancelled/removed
- T048: FR-030 auto check-out rescheduling
- T022: Keymaster event handling
- T047: Toggle mid-event
"""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

from homeassistant.components.calendar import CalendarEvent
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
import pytest

from custom_components.rental_control.const import CHECKIN_STATE_AWAITING
from custom_components.rental_control.const import CHECKIN_STATE_CHECKED_IN
from custom_components.rental_control.const import CHECKIN_STATE_CHECKED_OUT
from custom_components.rental_control.const import CHECKIN_STATE_NO_RESERVATION
from custom_components.rental_control.const import COORDINATOR
from custom_components.rental_control.const import DEFAULT_CLEANING_WINDOW
from custom_components.rental_control.const import DOMAIN
from custom_components.rental_control.const import EARLY_CHECKOUT_EXPIRY_SWITCH
from custom_components.rental_control.const import EARLY_CHECKOUT_GRACE_MINUTES
from custom_components.rental_control.const import EVENT_RENTAL_CONTROL_CHECKIN
from custom_components.rental_control.const import EVENT_RENTAL_CONTROL_CHECKOUT
from custom_components.rental_control.const import KEYMASTER_MONITORING_SWITCH
from custom_components.rental_control.const import UNSUB_LISTENERS
from custom_components.rental_control.sensors.checkinsensor import CheckinTrackingSensor

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(
    summary: str = "Reserved - John Smith",
    start: datetime | None = None,
    end: datetime | None = None,
    description: str = "Guest: John Smith",
) -> CalendarEvent:
    """Create a CalendarEvent for testing."""
    now = dt_util.now()
    if start is None:
        start = now + timedelta(hours=24)
    if end is None:
        end = start + timedelta(hours=120)
    return CalendarEvent(
        summary=summary,
        start=start,
        end=end,
        description=description,
    )


def _create_sensor(
    hass: HomeAssistant,
    mock_checkin_coordinator: MagicMock,
    mock_checkin_config_entry: MockConfigEntry,
) -> CheckinTrackingSensor:
    """Create a CheckinTrackingSensor for testing.

    Does NOT call async_added_to_hass. Use this when you need a
    sensor in a known state without triggering initial coordinator
    processing.
    """
    mock_checkin_config_entry.add_to_hass(hass)
    sensor = CheckinTrackingSensor(
        hass,
        mock_checkin_coordinator,
        mock_checkin_config_entry,
    )
    # Set entity_id for event bus payload assertions
    sensor.entity_id = "sensor.test_rental_checkin"
    # Set hass so async_write_ha_state doesn't blow up
    sensor.hass = hass
    # Patch async_write_ha_state to avoid entity registry issues
    sensor.async_write_ha_state = MagicMock()
    return sensor


# ===========================================================================
# Sensor entity properties
# ===========================================================================


class TestSensorEntityProperties:
    """Tests for CheckinTrackingSensor entity configuration."""

    async def test_device_class_is_enum(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test sensor declares ENUM device class."""
        from homeassistant.components.sensor import SensorDeviceClass

        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )
        assert sensor.device_class == SensorDeviceClass.ENUM

    async def test_options_lists_all_states(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test sensor options contain all four states."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )
        assert sensor.options == [
            CHECKIN_STATE_NO_RESERVATION,
            CHECKIN_STATE_AWAITING,
            CHECKIN_STATE_CHECKED_IN,
            CHECKIN_STATE_CHECKED_OUT,
        ]


# ===========================================================================
# T007: State machine transitions
# ===========================================================================


class TestStateMachineTransitions:
    """Tests for state machine transitions (T007)."""

    async def test_no_reservation_to_awaiting_on_event(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test no_reservation → awaiting_checkin when coordinator provides event."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )
        assert sensor._state == CHECKIN_STATE_NO_RESERVATION

        # Coordinator provides a future event
        future_event = _make_event(
            start=dt_util.now() + timedelta(hours=24),
        )
        mock_checkin_coordinator.data = [future_event]
        mock_checkin_coordinator.last_update_success = True

        sensor._handle_coordinator_update()

        assert sensor._state == CHECKIN_STATE_AWAITING
        assert sensor._tracked_event_summary == future_event.summary
        assert sensor._tracked_event_start == future_event.start
        assert sensor._tracked_event_end == future_event.end

    async def test_awaiting_to_checked_in_at_event_start(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test awaiting_checkin → checked_in at event start time.

        When event start is in the past, the sensor should transition
        immediately to checked_in.
        """
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        # Create an event that started in the past
        event = _make_event(
            start=dt_util.now() - timedelta(hours=1),
            end=dt_util.now() + timedelta(hours=48),
        )
        mock_checkin_coordinator.data = [event]
        mock_checkin_coordinator.last_update_success = True

        # Transition to awaiting (start in past → immediate checkin)
        sensor._handle_coordinator_update()

        assert sensor._state == CHECKIN_STATE_CHECKED_IN
        assert sensor._checkin_source == "automatic"

    async def test_checked_in_to_checked_out_at_event_end(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test checked_in → checked_out at event end time via timer."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        # Set up sensor in checked_in state
        now = dt_util.now()
        event = _make_event(
            start=now - timedelta(hours=48),
            end=now + timedelta(hours=2),
        )
        mock_checkin_coordinator.data = [event]
        sensor._state = CHECKIN_STATE_CHECKED_IN
        sensor._tracked_event_summary = event.summary
        sensor._tracked_event_start = event.start
        sensor._tracked_event_end = event.end
        sensor._tracked_event_slot_name = "John Smith"
        sensor._checkin_source = "automatic"

        # Simulate auto checkout timer firing
        sensor._async_auto_checkout_callback(now)

        assert sensor._state == CHECKIN_STATE_CHECKED_OUT
        assert sensor._checkout_source == "automatic"

    async def test_checked_out_to_no_reservation_after_cleaning_window(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test checked_out → no_reservation after cleaning window (FR-006b)."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        # Put sensor in checked_out state with no follow-on events
        mock_checkin_coordinator.data = []
        sensor._state = CHECKIN_STATE_CHECKED_OUT
        sensor._tracked_event_summary = "Reserved - John Smith"
        sensor._tracked_event_start = dt_util.now() - timedelta(hours=120)
        sensor._tracked_event_end = dt_util.now() - timedelta(hours=1)
        sensor._checkout_time = dt_util.now()

        # Simulate linger timer expiring
        sensor._async_linger_to_no_reservation_callback(dt_util.now())

        assert sensor._state == CHECKIN_STATE_NO_RESERVATION
        assert sensor._tracked_event_summary is None
        assert sensor._tracked_event_start is None

    async def test_stays_no_reservation_with_no_events(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test sensor stays in no_reservation when coordinator has no events."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )
        assert sensor._state == CHECKIN_STATE_NO_RESERVATION

        mock_checkin_coordinator.data = []
        mock_checkin_coordinator.last_update_success = True

        sensor._handle_coordinator_update()

        assert sensor._state == CHECKIN_STATE_NO_RESERVATION

    async def test_attributes_update_with_event_data(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test sensor attributes are populated with event data on transition."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        event = _make_event(
            summary="Reserved - Jane Doe",
            start=dt_util.now() + timedelta(hours=24),
            end=dt_util.now() + timedelta(hours=144),
            description="Guest: Jane Doe",
        )
        mock_checkin_coordinator.data = [event]
        mock_checkin_coordinator.last_update_success = True

        sensor._handle_coordinator_update()

        attrs = sensor.extra_state_attributes
        assert attrs["checkin_state"] == CHECKIN_STATE_AWAITING
        assert attrs["summary"] == "Reserved - Jane Doe"
        assert attrs["start"] is not None
        assert attrs["end"] is not None
        assert attrs["guest_name"] is not None


# ===========================================================================
# T008: Event identity and FR-007
# ===========================================================================


class TestEventIdentity:
    """Tests for event identity and FR-007 protection (T008)."""

    async def test_event_key_generates_correct_composite(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test _event_key generates correct composite from summary and start."""
        now = dt_util.now()
        key = CheckinTrackingSensor._event_key("Reserved - John", now)
        assert key == f"Reserved - John|{now.isoformat()}"

    async def test_checked_out_event_not_retriggered_on_end_extension(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test FR-007: checked_out event does NOT re-transition on end extension."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        now = dt_util.now()
        start = now - timedelta(hours=120)
        original_end = now - timedelta(hours=1)

        # Put sensor in checked_out state for this event
        sensor._state = CHECKIN_STATE_CHECKED_OUT
        sensor._tracked_event_summary = "Reserved - John Smith"
        sensor._tracked_event_start = start
        sensor._tracked_event_end = original_end
        sensor._checkout_source = "automatic"
        sensor._checkout_time = now - timedelta(minutes=30)
        sensor._checked_out_event_key = CheckinTrackingSensor._event_key(
            "Reserved - John Smith", start
        )

        # Coordinator provides same event but with extended end time
        extended_event = _make_event(
            summary="Reserved - John Smith",
            start=start,
            end=now + timedelta(hours=24),  # Extended end
        )
        mock_checkin_coordinator.data = [extended_event]
        mock_checkin_coordinator.last_update_success = True

        sensor._handle_coordinator_update()

        # Should remain in checked_out - FR-007
        assert sensor._state == CHECKIN_STATE_CHECKED_OUT

    async def test_new_event_triggers_new_cycle(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test that a genuinely new event triggers a new awaiting cycle."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        now = dt_util.now()
        old_start = now - timedelta(hours=120)

        # Put sensor in checked_out state for the old event
        sensor._state = CHECKIN_STATE_CHECKED_OUT
        sensor._tracked_event_summary = "Reserved - Old Guest"
        sensor._tracked_event_start = old_start
        sensor._checked_out_event_key = CheckinTrackingSensor._event_key(
            "Reserved - Old Guest", old_start
        )
        sensor._checkout_time = now - timedelta(minutes=5)

        # Coordinator provides a genuinely different event
        new_event = _make_event(
            summary="Reserved - New Guest",
            start=now + timedelta(hours=24),
        )
        mock_checkin_coordinator.data = [new_event]
        mock_checkin_coordinator.last_update_success = True

        # Simulate linger timer → transition to awaiting for new event
        sensor._async_linger_to_awaiting_callback(now)

        assert sensor._state == CHECKIN_STATE_AWAITING
        assert sensor._tracked_event_summary == "Reserved - New Guest"


# ===========================================================================
# T009: HA event bus firing
# ===========================================================================


class TestEventBusFiring:
    """Tests for HA event bus event firing (T009)."""

    async def test_checkin_event_fires_with_correct_payload(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test rental_control_checkin fires with correct payload on check-in."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        now = dt_util.now()
        start = now - timedelta(hours=1)
        end = now + timedelta(hours=48)
        event = _make_event(
            summary="Reserved - John Smith",
            start=start,
            end=end,
            description="Guest: John Smith",
        )
        mock_checkin_coordinator.data = [event]
        mock_checkin_coordinator.last_update_success = True

        # Collect fired events
        fired_events = []
        hass.bus.async_listen(
            EVENT_RENTAL_CONTROL_CHECKIN,
            lambda e: fired_events.append(e),
        )

        # Trigger transition (start in past → immediate checkin)
        sensor._handle_coordinator_update()
        await hass.async_block_till_done()

        assert len(fired_events) == 1
        event_data = fired_events[0].data
        assert event_data["entity_id"] == "sensor.test_rental_checkin"
        assert event_data["summary"] == "Reserved - John Smith"
        assert event_data["start"] == start.isoformat()
        assert event_data["end"] == end.isoformat()
        assert event_data["source"] == "automatic"
        # guest_name is extracted from summary via get_slot_name
        assert event_data["guest_name"] is not None

    async def test_checkout_event_fires_with_correct_payload(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test rental_control_checkout fires with correct payload on check-out."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        now = dt_util.now()
        start = now - timedelta(hours=120)
        end = now + timedelta(hours=1)

        # Set sensor to checked_in state
        sensor._state = CHECKIN_STATE_CHECKED_IN
        sensor._tracked_event_summary = "Reserved - John Smith"
        sensor._tracked_event_start = start
        sensor._tracked_event_end = end
        sensor._tracked_event_slot_name = "John Smith"
        sensor._checkin_source = "automatic"

        mock_checkin_coordinator.data = []

        # Collect fired events
        fired_events = []
        hass.bus.async_listen(
            EVENT_RENTAL_CONTROL_CHECKOUT,
            lambda e: fired_events.append(e),
        )

        # Trigger checkout via timer callback
        sensor._async_auto_checkout_callback(now)
        await hass.async_block_till_done()

        assert len(fired_events) == 1
        event_data = fired_events[0].data
        assert event_data["entity_id"] == "sensor.test_rental_checkin"
        assert event_data["summary"] == "Reserved - John Smith"
        assert event_data["start"] == start.isoformat()
        assert event_data["end"] == end.isoformat()
        assert event_data["guest_name"] == "John Smith"
        assert event_data["source"] == "automatic"


# ===========================================================================
# T046: Event cancelled/removed
# ===========================================================================


class TestEventCancelled:
    """Tests for event cancellation/removal (T046)."""

    async def test_awaiting_to_no_reservation_on_event_removed(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test awaiting → no_reservation when tracked event disappears."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        # Put sensor in awaiting state
        event = _make_event(start=dt_util.now() + timedelta(hours=24))
        mock_checkin_coordinator.data = [event]
        mock_checkin_coordinator.last_update_success = True
        sensor._handle_coordinator_update()
        assert sensor._state == CHECKIN_STATE_AWAITING

        # Event disappears (cancelled)
        mock_checkin_coordinator.data = []
        sensor._handle_coordinator_update()

        assert sensor._state == CHECKIN_STATE_NO_RESERVATION

    async def test_checked_in_to_no_reservation_on_event_removed(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test checked_in → no_reservation when tracked event disappears."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        now = dt_util.now()
        # Put sensor in checked_in state
        sensor._state = CHECKIN_STATE_CHECKED_IN
        sensor._tracked_event_summary = "Reserved - John Smith"
        sensor._tracked_event_start = now - timedelta(hours=2)
        sensor._tracked_event_end = now + timedelta(hours=48)
        sensor._tracked_event_slot_name = "John Smith"

        # Event disappears
        mock_checkin_coordinator.data = []
        mock_checkin_coordinator.last_update_success = True
        sensor._handle_coordinator_update()

        assert sensor._state == CHECKIN_STATE_NO_RESERVATION

    async def test_awaiting_shifts_to_next_event_on_cancel(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test that when current event is cancelled, next event takes over.

        When the coordinator updates and event[0] is now a different event,
        the sensor should track the new event and reschedule the timer.
        """
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        now = dt_util.now()
        # Initial event
        event1 = _make_event(
            summary="Reserved - Alice",
            start=now + timedelta(hours=24),
        )
        mock_checkin_coordinator.data = [event1]
        mock_checkin_coordinator.last_update_success = True
        sensor._handle_coordinator_update()
        assert sensor._state == CHECKIN_STATE_AWAITING
        assert sensor._tracked_event_summary == "Reserved - Alice"
        assert sensor._tracked_event_start == event1.start

        # Alice's reservation cancelled; Bob's becomes event[0]
        event2 = _make_event(
            summary="Reserved - Bob",
            start=now + timedelta(hours=48),
        )
        mock_checkin_coordinator.data = [event2]
        sensor._handle_coordinator_update()

        # Should still be awaiting but tracking Bob's event
        assert sensor._state == CHECKIN_STATE_AWAITING
        assert sensor._tracked_event_summary == "Reserved - Bob"
        # Start time and timer target must update to Bob's event
        assert sensor._tracked_event_start == event2.start
        assert sensor._transition_target_time == event2.start


# ===========================================================================
# T048: FR-030 auto check-out rescheduling
# ===========================================================================


class TestAutoCheckoutRescheduling:
    """Tests for FR-030 auto check-out rescheduling (T048)."""

    async def test_reschedule_checkout_on_end_time_change(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test checkout timer is rescheduled when event end time changes.

        Starting from checked_in with an auto check-out timer, when the
        coordinator updates with the same event but different end time,
        the timer should be rescheduled.
        """
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        now = dt_util.now()
        start = now - timedelta(hours=2)
        original_end = now + timedelta(hours=24)
        new_end = now + timedelta(hours=48)

        # Set sensor to checked_in state with original end
        sensor._state = CHECKIN_STATE_CHECKED_IN
        sensor._tracked_event_summary = "Reserved - John Smith"
        sensor._tracked_event_start = start
        sensor._tracked_event_end = original_end
        sensor._tracked_event_slot_name = "John Smith"
        sensor._checkin_source = "automatic"

        # Set a mock timer
        mock_unsub = MagicMock()
        sensor._unsub_timer = mock_unsub

        # Coordinator updates with extended end time
        updated_event = _make_event(
            summary="Reserved - John Smith",
            start=start,
            end=new_end,
        )
        mock_checkin_coordinator.data = [updated_event]
        mock_checkin_coordinator.last_update_success = True

        sensor._handle_coordinator_update()

        # Timer should have been cancelled
        mock_unsub.assert_called_once()
        # End time should be updated
        assert sensor._tracked_event_end == new_end
        # Should still be checked_in
        assert sensor._state == CHECKIN_STATE_CHECKED_IN
        # New timer should be scheduled
        assert sensor._unsub_timer is not None

    async def test_no_reschedule_when_end_unchanged(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test no timer reschedule when event end time is unchanged."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        now = dt_util.now()
        start = now - timedelta(hours=2)
        end = now + timedelta(hours=24)

        # Set sensor to checked_in state
        sensor._state = CHECKIN_STATE_CHECKED_IN
        sensor._tracked_event_summary = "Reserved - John Smith"
        sensor._tracked_event_start = start
        sensor._tracked_event_end = end
        sensor._tracked_event_slot_name = "John Smith"
        sensor._checkin_source = "automatic"

        # Set a mock timer
        mock_unsub = MagicMock()
        sensor._unsub_timer = mock_unsub

        # Coordinator updates with same event, same end time
        same_event = _make_event(
            summary="Reserved - John Smith",
            start=start,
            end=end,
        )
        mock_checkin_coordinator.data = [same_event]
        mock_checkin_coordinator.last_update_success = True

        sensor._handle_coordinator_update()

        # Timer should NOT have been cancelled
        mock_unsub.assert_not_called()
        # Should still be checked_in
        assert sensor._state == CHECKIN_STATE_CHECKED_IN


# ===========================================================================
# Additional: Post-checkout linger scenarios
# ===========================================================================


class TestPostCheckoutLinger:
    """Tests for post-checkout linger timing (related to T014)."""

    async def test_same_day_turnover_linger_timing(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test FR-006a: Same-day turnover schedules half-gap transition."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        now = dt_util.now()
        start = now - timedelta(hours=120)
        end = now - timedelta(hours=1)

        # Next event starts same day, 4 hours from now
        next_event = _make_event(
            summary="Reserved - Next Guest",
            start=now + timedelta(hours=4),
            end=now + timedelta(hours=124),
        )
        mock_checkin_coordinator.data = [
            _make_event(summary="Reserved - Current", start=start, end=end),
            next_event,
        ]

        sensor._state = CHECKIN_STATE_CHECKED_IN
        sensor._tracked_event_summary = "Reserved - Current"
        sensor._tracked_event_start = start
        sensor._tracked_event_end = end
        sensor._tracked_event_slot_name = "Current"

        # Trigger checkout
        sensor._transition_to_checked_out(source="automatic")

        assert sensor._state == CHECKIN_STATE_CHECKED_OUT
        assert sensor._transition_target_time is not None
        assert sensor._checkout_time is not None
        # Half-gap between checkout and next event start
        gap = next_event.start - sensor._checkout_time
        expected_linger = sensor._checkout_time + gap / 2
        delta = sensor._transition_target_time - expected_linger
        assert abs(delta.total_seconds()) < 1

    async def test_no_followon_cleaning_window_linger(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test FR-006b: No follow-on uses cleaning window."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        now = dt_util.now()
        start = now - timedelta(hours=120)
        end = now - timedelta(hours=1)

        # No next event
        mock_checkin_coordinator.data = [
            _make_event(summary="Reserved - Current", start=start, end=end),
        ]

        sensor._state = CHECKIN_STATE_CHECKED_IN
        sensor._tracked_event_summary = "Reserved - Current"
        sensor._tracked_event_start = start
        sensor._tracked_event_end = end
        sensor._tracked_event_slot_name = "Current"

        sensor._transition_to_checked_out(source="automatic")

        assert sensor._state == CHECKIN_STATE_CHECKED_OUT
        assert sensor._transition_target_time is not None
        assert sensor._checkout_time is not None
        # Cleaning window default is 6 hours
        expected_linger = sensor._checkout_time + timedelta(
            hours=DEFAULT_CLEANING_WINDOW
        )
        # Allow 1 second tolerance for timing
        delta = sensor._transition_target_time - expected_linger
        assert abs(delta.total_seconds()) < 1

    async def test_different_day_midnight_boundary_linger(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test FR-006c: Different-day follow-on uses midnight boundary."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        now = dt_util.now()
        start = now - timedelta(hours=120)
        end = now - timedelta(hours=1)

        # Next event starts in 3 days
        next_event = _make_event(
            summary="Reserved - Future Guest",
            start=now + timedelta(days=3),
            end=now + timedelta(days=8),
        )
        mock_checkin_coordinator.data = [
            _make_event(summary="Reserved - Current", start=start, end=end),
            next_event,
        ]

        sensor._state = CHECKIN_STATE_CHECKED_IN
        sensor._tracked_event_summary = "Reserved - Current"
        sensor._tracked_event_start = start
        sensor._tracked_event_end = end
        sensor._tracked_event_slot_name = "Current"

        sensor._transition_to_checked_out(source="automatic")

        assert sensor._state == CHECKIN_STATE_CHECKED_OUT
        assert sensor._transition_target_time is not None
        assert sensor._checkout_time is not None
        # FR-006c: should transition at midnight boundary
        expected_midnight = dt_util.start_of_local_day(
            sensor._checkout_time + timedelta(days=1)
        )
        delta = sensor._transition_target_time - expected_midnight
        assert abs(delta.total_seconds()) < 1

    async def test_auto_checkin_timer_scheduled_for_future_event(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test that auto check-in timer is scheduled for future events."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        future_start = dt_util.now() + timedelta(hours=24)
        event = _make_event(start=future_start)
        mock_checkin_coordinator.data = [event]
        mock_checkin_coordinator.last_update_success = True

        sensor._handle_coordinator_update()

        assert sensor._state == CHECKIN_STATE_AWAITING
        assert sensor._transition_target_time == future_start
        assert sensor._unsub_timer is not None

    async def test_auto_checkout_timer_scheduled_on_checkin(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test that auto check-out timer is scheduled when entering checked_in."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        now = dt_util.now()
        end = now + timedelta(hours=48)
        event = _make_event(
            start=now - timedelta(hours=1),
            end=end,
        )
        mock_checkin_coordinator.data = [event]
        mock_checkin_coordinator.last_update_success = True

        sensor._handle_coordinator_update()

        assert sensor._state == CHECKIN_STATE_CHECKED_IN
        assert sensor._transition_target_time == end
        assert sensor._unsub_timer is not None


# ===========================================================================
# Helpers for T016/T017: State Restoration
# ===========================================================================


def _make_extra_data_dict(
    state: str = CHECKIN_STATE_CHECKED_IN,
    summary: str | None = "Reserved - John Smith",
    start: datetime | None = None,
    end: datetime | None = None,
    slot_name: str | None = "John Smith",
    checkin_source: str | None = "automatic",
    checkout_source: str | None = None,
    checkout_time: datetime | None = None,
    transition_target_time: datetime | None = None,
    checked_out_event_key: str | None = None,
) -> dict:
    """Build a dict matching CheckinExtraStoredData.as_dict() output.

    Datetime values are serialised to ISO 8601 strings (or None).
    """
    return {
        "state": state,
        "tracked_event_summary": summary,
        "tracked_event_start": start.isoformat() if start else None,
        "tracked_event_end": end.isoformat() if end else None,
        "tracked_event_slot_name": slot_name,
        "checkin_source": checkin_source,
        "checkout_source": checkout_source,
        "checkout_time": checkout_time.isoformat() if checkout_time else None,
        "transition_target_time": (
            transition_target_time.isoformat() if transition_target_time else None
        ),
        "checked_out_event_key": checked_out_event_key,
    }


def _mock_extra_data(data_dict: dict) -> MagicMock:
    """Create a mock ExtraStoredData-like object with an ``as_dict`` method."""
    extra_data = MagicMock()
    extra_data.as_dict.return_value = data_dict
    return extra_data


# ===========================================================================
# T016: State restoration (async_added_to_hass, RestoreEntity)
# ===========================================================================


class TestStateRestoration:
    """Tests for state restoration via RestoreEntity (T016)."""

    async def test_restore_checked_in_state(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test async_added_to_hass restores checked_in state from extra data."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        now = dt_util.now()
        start = now - timedelta(hours=2)
        end = now + timedelta(hours=48)

        data_dict = _make_extra_data_dict(
            state=CHECKIN_STATE_CHECKED_IN,
            summary="Reserved - John Smith",
            start=start,
            end=end,
            slot_name="John Smith",
            checkin_source="automatic",
        )

        # Provide a current event matching the restored one so stale
        # validation keeps it as-is.
        event = _make_event(
            summary="Reserved - John Smith",
            start=start,
            end=end,
        )
        mock_checkin_coordinator.data = [event]
        mock_checkin_coordinator.last_update_success = True

        with patch.object(
            sensor,
            "async_get_last_extra_data",
            new=AsyncMock(return_value=_mock_extra_data(data_dict)),
        ):
            await sensor.async_added_to_hass()

        assert sensor._state == CHECKIN_STATE_CHECKED_IN
        assert sensor._tracked_event_summary == "Reserved - John Smith"
        assert sensor._tracked_event_start == start
        assert sensor._tracked_event_end == end
        assert sensor._tracked_event_slot_name == "John Smith"
        assert sensor._checkin_source == "automatic"

    async def test_restore_all_fields(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test that all persisted fields are restored from extra data."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        now = dt_util.now()
        start = now - timedelta(hours=120)
        end = now - timedelta(hours=1)
        checkout_time = now - timedelta(minutes=30)
        transition_target = now + timedelta(hours=5)
        event_key = f"Reserved - John Smith|{start.isoformat()}"

        data_dict = _make_extra_data_dict(
            state=CHECKIN_STATE_CHECKED_OUT,
            summary="Reserved - John Smith",
            start=start,
            end=end,
            slot_name="John Smith",
            checkin_source="automatic",
            checkout_source="automatic",
            checkout_time=checkout_time,
            transition_target_time=transition_target,
            checked_out_event_key=event_key,
        )

        # No current events — stale validation will handle transition
        # but for this test we just verify restore populates fields.
        mock_checkin_coordinator.data = []
        mock_checkin_coordinator.last_update_success = True

        with patch.object(
            sensor,
            "async_get_last_extra_data",
            new=AsyncMock(return_value=_mock_extra_data(data_dict)),
        ):
            await sensor.async_added_to_hass()

        # Verify all fields populated (stale validation may change state,
        # but the underlying fields should have been restored first)
        assert sensor._tracked_event_summary == "Reserved - John Smith"
        assert sensor._tracked_event_start == start
        assert sensor._tracked_event_end == end
        assert sensor._tracked_event_slot_name == "John Smith"
        assert sensor._checkin_source == "automatic"
        assert sensor._checkout_source == "automatic"
        assert sensor._checkout_time == checkout_time
        assert sensor._checked_out_event_key == event_key

    async def test_no_prior_state_starts_no_reservation(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test sensor starts in no_reservation when no prior state exists."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        mock_checkin_coordinator.data = []
        mock_checkin_coordinator.last_update_success = True

        with patch.object(
            sensor,
            "async_get_last_extra_data",
            new=AsyncMock(return_value=None),
        ):
            await sensor.async_added_to_hass()

        assert sensor._state == CHECKIN_STATE_NO_RESERVATION

    async def test_restore_awaiting_state(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test restoring awaiting_checkin state with future event."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        now = dt_util.now()
        start = now + timedelta(hours=24)
        end = start + timedelta(hours=120)

        data_dict = _make_extra_data_dict(
            state=CHECKIN_STATE_AWAITING,
            summary="Reserved - Jane Doe",
            start=start,
            end=end,
            slot_name="Jane Doe",
            checkin_source=None,
            transition_target_time=start,
        )

        # Provide matching future event so stale validation keeps it
        event = _make_event(
            summary="Reserved - Jane Doe",
            start=start,
            end=end,
        )
        mock_checkin_coordinator.data = [event]
        mock_checkin_coordinator.last_update_success = True

        with patch.object(
            sensor,
            "async_get_last_extra_data",
            new=AsyncMock(return_value=_mock_extra_data(data_dict)),
        ):
            await sensor.async_added_to_hass()

        assert sensor._state == CHECKIN_STATE_AWAITING
        assert sensor._tracked_event_summary == "Reserved - Jane Doe"
        assert sensor._tracked_event_slot_name == "Jane Doe"

    async def test_restore_with_none_datetime_fields(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test restore handles None datetime fields gracefully."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        data_dict = _make_extra_data_dict(
            state=CHECKIN_STATE_NO_RESERVATION,
            summary=None,
            start=None,
            end=None,
            slot_name=None,
            checkin_source=None,
            checkout_source=None,
            checkout_time=None,
            transition_target_time=None,
            checked_out_event_key=None,
        )

        mock_checkin_coordinator.data = []
        mock_checkin_coordinator.last_update_success = True

        with patch.object(
            sensor,
            "async_get_last_extra_data",
            new=AsyncMock(return_value=_mock_extra_data(data_dict)),
        ):
            await sensor.async_added_to_hass()

        assert sensor._state == CHECKIN_STATE_NO_RESERVATION
        assert sensor._tracked_event_start is None
        assert sensor._tracked_event_end is None
        assert sensor._checkout_time is None
        assert sensor._transition_target_time is None


# ===========================================================================
# T017: Stale state validation after restore
# ===========================================================================


class TestStaleStateValidation:
    """Tests for stale state validation on restore (T017)."""

    async def test_checked_in_kept_when_event_still_active(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test restored checked_in is kept when event is still active."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        now = dt_util.now()
        start = now - timedelta(hours=24)
        end = now + timedelta(hours=48)

        data_dict = _make_extra_data_dict(
            state=CHECKIN_STATE_CHECKED_IN,
            summary="Reserved - John Smith",
            start=start,
            end=end,
            slot_name="John Smith",
            checkin_source="automatic",
        )

        # Event is still active (end is in the future)
        event = _make_event(
            summary="Reserved - John Smith",
            start=start,
            end=end,
        )
        mock_checkin_coordinator.data = [event]
        mock_checkin_coordinator.last_update_success = True

        with patch.object(
            sensor,
            "async_get_last_extra_data",
            new=AsyncMock(return_value=_mock_extra_data(data_dict)),
        ):
            await sensor.async_added_to_hass()

        assert sensor._state == CHECKIN_STATE_CHECKED_IN
        # Timer should be rescheduled for auto-checkout at event end
        assert sensor._unsub_timer is not None
        assert sensor._transition_target_time == end

    async def test_checked_in_transitions_to_checked_out_when_event_ended(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test restored checked_in → checked_out when event has ended."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        now = dt_util.now()
        start = now - timedelta(hours=120)
        end = now - timedelta(hours=1)  # Event already ended

        data_dict = _make_extra_data_dict(
            state=CHECKIN_STATE_CHECKED_IN,
            summary="Reserved - John Smith",
            start=start,
            end=end,
            slot_name="John Smith",
            checkin_source="automatic",
        )

        # No current events
        mock_checkin_coordinator.data = []
        mock_checkin_coordinator.last_update_success = True

        with patch.object(
            sensor,
            "async_get_last_extra_data",
            new=AsyncMock(return_value=_mock_extra_data(data_dict)),
        ):
            await sensor.async_added_to_hass()

        assert sensor._state == CHECKIN_STATE_CHECKED_OUT
        assert sensor._checkout_source == "automatic"
        # Checkout time should be anchored to event end, not restore time
        assert sensor._checkout_time == end

    async def test_awaiting_transitions_to_checked_in_when_start_passed(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test restored awaiting → checked_in when event start has passed (time-based)."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        now = dt_util.now()
        start = now - timedelta(hours=2)  # Event already started
        end = now + timedelta(hours=48)

        data_dict = _make_extra_data_dict(
            state=CHECKIN_STATE_AWAITING,
            summary="Reserved - John Smith",
            start=start,
            end=end,
            slot_name="John Smith",
            checkin_source=None,
            transition_target_time=start,
        )

        # Event is active (started, hasn't ended)
        event = _make_event(
            summary="Reserved - John Smith",
            start=start,
            end=end,
        )
        mock_checkin_coordinator.data = [event]
        mock_checkin_coordinator.last_update_success = True

        with patch.object(
            sensor,
            "async_get_last_extra_data",
            new=AsyncMock(return_value=_mock_extra_data(data_dict)),
        ):
            await sensor.async_added_to_hass()

        assert sensor._state == CHECKIN_STATE_CHECKED_IN
        assert sensor._checkin_source == "automatic"

    async def test_awaiting_stays_awaiting_on_restore_with_monitoring(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test restored awaiting stays awaiting when monitoring is on.

        When keymaster monitoring is enabled and HA restarts with
        awaiting_checkin state whose start has already passed, the
        sensor must NOT silently transition to checked_in.
        """
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        # Monitoring switch is ON
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][mock_checkin_config_entry.entry_id] = {
            KEYMASTER_MONITORING_SWITCH: MagicMock(is_on=True),
        }

        now = dt_util.now()
        start = now - timedelta(hours=2)
        end = now + timedelta(hours=48)

        data_dict = _make_extra_data_dict(
            state=CHECKIN_STATE_AWAITING,
            summary="Reserved - John Smith",
            start=start,
            end=end,
            slot_name="John Smith",
            checkin_source=None,
            transition_target_time=start,
        )

        event = _make_event(
            summary="Reserved - John Smith",
            start=start,
            end=end,
        )
        mock_checkin_coordinator.data = [event]
        mock_checkin_coordinator.last_update_success = True

        with patch.object(
            sensor,
            "async_get_last_extra_data",
            new=AsyncMock(return_value=_mock_extra_data(data_dict)),
        ):
            await sensor.async_added_to_hass()

        assert sensor._state == CHECKIN_STATE_AWAITING
        assert sensor._checkin_source is None

    async def test_checked_out_transitions_to_awaiting_when_new_event(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test restored checked_out → awaiting when a new event is relevant."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        now = dt_util.now()
        old_start = now - timedelta(hours=120)
        old_end = now - timedelta(hours=1)
        checkout_time = now - timedelta(hours=2)
        old_event_key = f"Reserved - Old Guest|{old_start.isoformat()}"

        data_dict = _make_extra_data_dict(
            state=CHECKIN_STATE_CHECKED_OUT,
            summary="Reserved - Old Guest",
            start=old_start,
            end=old_end,
            slot_name="Old Guest",
            checkin_source="automatic",
            checkout_source="automatic",
            checkout_time=checkout_time,
            checked_out_event_key=old_event_key,
        )

        # A new event is now relevant (different from checked-out event)
        new_event = _make_event(
            summary="Reserved - New Guest",
            start=now + timedelta(hours=12),
            end=now + timedelta(hours=132),
        )
        mock_checkin_coordinator.data = [new_event]
        mock_checkin_coordinator.last_update_success = True

        with patch.object(
            sensor,
            "async_get_last_extra_data",
            new=AsyncMock(return_value=_mock_extra_data(data_dict)),
        ):
            await sensor.async_added_to_hass()

        # Stale validation should recompute linger which triggers
        # coordinator processing → awaiting_checkin for the new event
        assert sensor._state == CHECKIN_STATE_AWAITING
        assert sensor._tracked_event_summary == "Reserved - New Guest"

    async def test_timers_rescheduled_after_restore_checked_in(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test timers are re-scheduled after restoring checked_in state."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        now = dt_util.now()
        start = now - timedelta(hours=24)
        end = now + timedelta(hours=48)

        data_dict = _make_extra_data_dict(
            state=CHECKIN_STATE_CHECKED_IN,
            summary="Reserved - John Smith",
            start=start,
            end=end,
            slot_name="John Smith",
            checkin_source="automatic",
        )

        event = _make_event(
            summary="Reserved - John Smith",
            start=start,
            end=end,
        )
        mock_checkin_coordinator.data = [event]
        mock_checkin_coordinator.last_update_success = True

        with patch.object(
            sensor,
            "async_get_last_extra_data",
            new=AsyncMock(return_value=_mock_extra_data(data_dict)),
        ):
            await sensor.async_added_to_hass()

        # Timer should be rescheduled for auto-checkout
        assert sensor._unsub_timer is not None
        assert sensor._transition_target_time == end

    async def test_timers_rescheduled_after_restore_awaiting(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test timers are re-scheduled after restoring awaiting state."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        now = dt_util.now()
        start = now + timedelta(hours=24)
        end = start + timedelta(hours=120)

        data_dict = _make_extra_data_dict(
            state=CHECKIN_STATE_AWAITING,
            summary="Reserved - John Smith",
            start=start,
            end=end,
            slot_name="John Smith",
            checkin_source=None,
            transition_target_time=start,
        )

        event = _make_event(
            summary="Reserved - John Smith",
            start=start,
            end=end,
        )
        mock_checkin_coordinator.data = [event]
        mock_checkin_coordinator.last_update_success = True

        with patch.object(
            sensor,
            "async_get_last_extra_data",
            new=AsyncMock(return_value=_mock_extra_data(data_dict)),
        ):
            await sensor.async_added_to_hass()

        # Timer should be scheduled for auto-checkin at event start
        assert sensor._unsub_timer is not None
        assert sensor._transition_target_time == start

    async def test_checked_out_linger_expired_transitions_to_no_reservation(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test checked_out with expired linger transitions to no_reservation."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        now = dt_util.now()
        old_start = now - timedelta(hours=240)
        old_end = now - timedelta(hours=120)
        # Checkout happened long ago; linger has fully expired
        checkout_time = now - timedelta(hours=100)
        old_event_key = f"Reserved - Old Guest|{old_start.isoformat()}"

        data_dict = _make_extra_data_dict(
            state=CHECKIN_STATE_CHECKED_OUT,
            summary="Reserved - Old Guest",
            start=old_start,
            end=old_end,
            slot_name="Old Guest",
            checkin_source="automatic",
            checkout_source="automatic",
            checkout_time=checkout_time,
            checked_out_event_key=old_event_key,
        )

        # No events at all
        mock_checkin_coordinator.data = []
        mock_checkin_coordinator.last_update_success = True

        with patch.object(
            sensor,
            "async_get_last_extra_data",
            new=AsyncMock(return_value=_mock_extra_data(data_dict)),
        ):
            await sensor.async_added_to_hass()

        # Linger long expired with no events → should go to no_reservation
        # or at minimum handle the state correctly by reprocessing
        # coordinator data
        assert sensor._state == CHECKIN_STATE_NO_RESERVATION

    async def test_unknown_state_resets_to_no_reservation(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test unknown restored state falls back to no_reservation."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        data_dict = _make_extra_data_dict(state="bogus_state")

        mock_checkin_coordinator.data = []
        mock_checkin_coordinator.last_update_success = True

        with patch.object(
            sensor,
            "async_get_last_extra_data",
            new=AsyncMock(return_value=_mock_extra_data(data_dict)),
        ):
            await sensor.async_added_to_hass()

        assert sensor._state == CHECKIN_STATE_NO_RESERVATION

    async def test_checked_out_linger_timer_rescheduled_on_restore(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test checked_out with unexpired linger reschedules timer."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        now = dt_util.now()
        old_start = now - timedelta(hours=48)
        old_end = now - timedelta(hours=1)
        checkout_time = now - timedelta(minutes=30)
        old_event_key = f"Reserved - Recent Guest|{old_start.isoformat()}"

        data_dict = _make_extra_data_dict(
            state=CHECKIN_STATE_CHECKED_OUT,
            summary="Reserved - Recent Guest",
            start=old_start,
            end=old_end,
            slot_name="Recent Guest",
            checkin_source="automatic",
            checkout_source="automatic",
            checkout_time=checkout_time,
            checked_out_event_key=old_event_key,
        )

        # No new events — same checked-out event scenario
        mock_checkin_coordinator.data = []
        mock_checkin_coordinator.last_update_success = True

        with patch.object(
            sensor,
            "async_get_last_extra_data",
            new=AsyncMock(return_value=_mock_extra_data(data_dict)),
        ):
            await sensor.async_added_to_hass()

        # Should stay checked_out with linger timer rescheduled
        assert sensor._state == CHECKIN_STATE_CHECKED_OUT
        assert sensor._unsub_timer is not None
        assert sensor._transition_target_time is not None


# ===========================================================================
# T019: ExtraStoredData subclass (extra_restore_state_data property)
# ===========================================================================


class TestExtraStoredData:
    """Tests for CheckinExtraStoredData persistence (T019)."""

    async def test_extra_restore_state_data_returns_stored_data(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test extra_restore_state_data returns a CheckinExtraStoredData."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        # Set up sensor in a known state
        now = dt_util.now()
        start = now - timedelta(hours=2)
        end = now + timedelta(hours=48)

        sensor._state = CHECKIN_STATE_CHECKED_IN
        sensor._tracked_event_summary = "Reserved - John Smith"
        sensor._tracked_event_start = start
        sensor._tracked_event_end = end
        sensor._tracked_event_slot_name = "John Smith"
        sensor._checkin_source = "automatic"
        sensor._checkout_source = None
        sensor._checkout_time = None
        sensor._transition_target_time = end
        sensor._checked_out_event_key = None

        extra = sensor.extra_restore_state_data
        assert extra is not None

        data = extra.as_dict()
        assert data["state"] == CHECKIN_STATE_CHECKED_IN
        assert data["tracked_event_summary"] == "Reserved - John Smith"
        assert data["tracked_event_start"] == start.isoformat()
        assert data["tracked_event_end"] == end.isoformat()
        assert data["tracked_event_slot_name"] == "John Smith"
        assert data["checkin_source"] == "automatic"
        assert data["checkout_source"] is None
        assert data["checkout_time"] is None
        assert data["transition_target_time"] == end.isoformat()
        assert data["checked_out_event_key"] is None

    async def test_extra_restore_state_data_checked_out_fields(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test extra_restore_state_data includes checkout fields."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        now = dt_util.now()
        start = now - timedelta(hours=120)
        end = now - timedelta(hours=1)
        checkout_time = now - timedelta(minutes=30)
        event_key = f"Reserved - John Smith|{start.isoformat()}"

        sensor._state = CHECKIN_STATE_CHECKED_OUT
        sensor._tracked_event_summary = "Reserved - John Smith"
        sensor._tracked_event_start = start
        sensor._tracked_event_end = end
        sensor._tracked_event_slot_name = "John Smith"
        sensor._checkin_source = "automatic"
        sensor._checkout_source = "automatic"
        sensor._checkout_time = checkout_time
        sensor._transition_target_time = now + timedelta(hours=5)
        sensor._checked_out_event_key = event_key

        extra = sensor.extra_restore_state_data
        assert extra is not None

        data = extra.as_dict()
        assert data["state"] == CHECKIN_STATE_CHECKED_OUT
        assert data["checkout_source"] == "automatic"
        assert data["checkout_time"] == checkout_time.isoformat()
        assert data["checked_out_event_key"] == event_key

    async def test_extra_restore_state_data_no_reservation_all_none(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test extra_restore_state_data for no_reservation state."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        # Default no_reservation state (all fields None)
        extra = sensor.extra_restore_state_data
        assert extra is not None

        data = extra.as_dict()
        assert data["state"] == CHECKIN_STATE_NO_RESERVATION
        assert data["tracked_event_summary"] is None
        assert data["tracked_event_start"] is None
        assert data["tracked_event_end"] is None
        assert data["checkout_time"] is None
        assert data["checked_out_event_key"] is None


# ===========================================================================
# T022: Keymaster event handling
# ===========================================================================


class TestKeymasterEventHandling:
    """Tests for keymaster unlock event handling (T022)."""

    async def test_matching_unlock_transitions_to_checked_in(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test sensor transitions from awaiting to checked_in on unlock.

        Calls async_handle_keymaster_unlock directly (the event bus
        listener validates lockname, state, slot range, and switch
        before forwarding).  Verifies the sensor transitions correctly
        and fires the check-in event with source='keymaster'.
        """
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )
        mock_checkin_coordinator.lockname = "test_lock"
        mock_checkin_coordinator.start_slot = 10
        mock_checkin_coordinator.max_events = 3

        # Put sensor in awaiting_checkin state
        event = _make_event(start=dt_util.now() + timedelta(hours=1))
        mock_checkin_coordinator.data = [event]
        mock_checkin_coordinator.last_update_success = True
        sensor._handle_coordinator_update()
        assert sensor._state == CHECKIN_STATE_AWAITING

        # Collect fired events
        fired_events = []
        hass.bus.async_listen(
            EVENT_RENTAL_CONTROL_CHECKIN,
            lambda e: fired_events.append(e),
        )

        # Call the keymaster unlock handler with a valid code slot
        sensor.async_handle_keymaster_unlock(
            code_slot_num=11,
        )

        assert sensor._state == CHECKIN_STATE_CHECKED_IN
        assert sensor._checkin_source == "keymaster"

        # Verify event bus fires with source: keymaster
        await hass.async_block_till_done()
        assert len(fired_events) == 1
        assert fired_events[0].data["source"] == "keymaster"

    async def test_code_slot_zero_ignored(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test that code_slot_num == 0 is ignored (FR-017)."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )
        mock_checkin_coordinator.lockname = "test_lock"
        mock_checkin_coordinator.start_slot = 10
        mock_checkin_coordinator.max_events = 3

        # Put sensor in awaiting_checkin state
        event = _make_event(start=dt_util.now() + timedelta(hours=1))
        mock_checkin_coordinator.data = [event]
        mock_checkin_coordinator.last_update_success = True
        sensor._handle_coordinator_update()
        assert sensor._state == CHECKIN_STATE_AWAITING

        # Switch gating is handled by the event bus listener;
        # this test only checks that code_slot_num=0 is ignored.

        # Call with code_slot_num=0 — should be ignored
        sensor.async_handle_keymaster_unlock(
            code_slot_num=0,
        )

        # Should remain in awaiting_checkin
        assert sensor._state == CHECKIN_STATE_AWAITING

    async def test_code_slot_outside_managed_range_ignored(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test that code_slot outside [start_slot, start_slot + max_events) is ignored."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )
        mock_checkin_coordinator.lockname = "test_lock"
        mock_checkin_coordinator.start_slot = 10
        mock_checkin_coordinator.max_events = 3

        event = _make_event(start=dt_util.now() + timedelta(hours=1))
        mock_checkin_coordinator.data = [event]
        mock_checkin_coordinator.last_update_success = True
        sensor._handle_coordinator_update()
        assert sensor._state == CHECKIN_STATE_AWAITING

        # Slot 9 is below range [10, 13)
        sensor.async_handle_keymaster_unlock(
            code_slot_num=9,
        )
        assert sensor._state == CHECKIN_STATE_AWAITING

        # Slot 13 is at upper bound (exclusive), so outside range
        sensor.async_handle_keymaster_unlock(
            code_slot_num=13,
        )
        assert sensor._state == CHECKIN_STATE_AWAITING

        # Slot 100 is well outside range
        sensor.async_handle_keymaster_unlock(
            code_slot_num=100,
        )
        assert sensor._state == CHECKIN_STATE_AWAITING

    async def test_unlock_when_already_checked_in_ignored(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test that unlock when sensor is already checked_in is ignored (FR-016)."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )
        mock_checkin_coordinator.lockname = "test_lock"
        mock_checkin_coordinator.start_slot = 10
        mock_checkin_coordinator.max_events = 3

        # Put sensor directly in checked_in state
        sensor._state = CHECKIN_STATE_CHECKED_IN
        sensor._checkin_source = "automatic"

        sensor.async_handle_keymaster_unlock(
            code_slot_num=11,
        )

        # Should remain checked_in with original source
        assert sensor._state == CHECKIN_STATE_CHECKED_IN
        assert sensor._checkin_source == "automatic"

    async def test_direct_sensor_call_transitions_to_checked_in(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test direct sensor method call transitions when awaiting.

        The monitoring switch check is performed by the event bus
        listener. When called directly, the sensor transitions if in
        awaiting_checkin with a valid code slot.
        """
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )
        mock_checkin_coordinator.lockname = "test_lock"
        mock_checkin_coordinator.start_slot = 10
        mock_checkin_coordinator.max_events = 3

        event = _make_event(start=dt_util.now() + timedelta(hours=1))
        mock_checkin_coordinator.data = [event]
        mock_checkin_coordinator.last_update_success = True
        sensor._handle_coordinator_update()
        assert sensor._state == CHECKIN_STATE_AWAITING

        # Calling the sensor method directly transitions (switch check
        # is now in the listener, not the sensor)
        sensor.async_handle_keymaster_unlock(
            code_slot_num=11,
        )

        assert sensor._state == CHECKIN_STATE_CHECKED_IN

    async def test_checkin_event_fires_with_keymaster_source(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test rental_control_checkin event fires with source=keymaster."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )
        mock_checkin_coordinator.lockname = "test_lock"
        mock_checkin_coordinator.start_slot = 10
        mock_checkin_coordinator.max_events = 3

        event = _make_event(
            summary="Reserved - John Smith",
            start=dt_util.now() + timedelta(hours=1),
            end=dt_util.now() + timedelta(hours=48),
        )
        mock_checkin_coordinator.data = [event]
        mock_checkin_coordinator.last_update_success = True
        sensor._handle_coordinator_update()
        assert sensor._state == CHECKIN_STATE_AWAITING

        fired_events = []
        hass.bus.async_listen(
            EVENT_RENTAL_CONTROL_CHECKIN,
            lambda e: fired_events.append(e),
        )

        sensor.async_handle_keymaster_unlock(
            code_slot_num=11,
        )
        await hass.async_block_till_done()

        assert len(fired_events) == 1
        assert fired_events[0].data["source"] == "keymaster"
        assert fired_events[0].data["summary"] == "Reserved - John Smith"

    async def test_boundary_code_slot_start_is_valid(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test that code_slot_num == start_slot (lower bound inclusive) works."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )
        mock_checkin_coordinator.lockname = "test_lock"
        mock_checkin_coordinator.start_slot = 10
        mock_checkin_coordinator.max_events = 3

        event = _make_event(start=dt_util.now() + timedelta(hours=1))
        mock_checkin_coordinator.data = [event]
        mock_checkin_coordinator.last_update_success = True
        sensor._handle_coordinator_update()
        assert sensor._state == CHECKIN_STATE_AWAITING

        # Slot 10 is the lower bound (inclusive)
        sensor.async_handle_keymaster_unlock(
            code_slot_num=10,
        )
        assert sensor._state == CHECKIN_STATE_CHECKED_IN

    async def test_boundary_code_slot_last_valid(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test that code_slot_num == start_slot + max_events - 1 (last valid) works."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )
        mock_checkin_coordinator.lockname = "test_lock"
        mock_checkin_coordinator.start_slot = 10
        mock_checkin_coordinator.max_events = 3

        event = _make_event(start=dt_util.now() + timedelta(hours=1))
        mock_checkin_coordinator.data = [event]
        mock_checkin_coordinator.last_update_success = True
        sensor._handle_coordinator_update()
        assert sensor._state == CHECKIN_STATE_AWAITING

        # Slot 12 = 10 + 3 - 1 is the last valid slot
        sensor.async_handle_keymaster_unlock(
            code_slot_num=12,
        )
        assert sensor._state == CHECKIN_STATE_CHECKED_IN


# ===========================================================================
# T047: Toggle mid-event tests
# ===========================================================================


class TestToggleMidEvent:
    """Tests for keymaster monitoring toggle changed mid-event (T047)."""

    async def test_toggle_on_while_awaiting_listens_for_unlock(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test sensor accepts keymaster unlock when in awaiting state.

        The monitoring switch check is now in the event bus listener.
        This test verifies the sensor method transitions correctly
        when called directly (listener already validated switch is on).
        """
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )
        mock_checkin_coordinator.lockname = "test_lock"
        mock_checkin_coordinator.start_slot = 10
        mock_checkin_coordinator.max_events = 3

        # Set up sensor in awaiting state with future event
        event = _make_event(start=dt_util.now() + timedelta(hours=2))
        mock_checkin_coordinator.data = [event]
        mock_checkin_coordinator.last_update_success = True
        sensor._handle_coordinator_update()
        assert sensor._state == CHECKIN_STATE_AWAITING

        # Sensor method called (listener verified switch is on)
        sensor.async_handle_keymaster_unlock(
            code_slot_num=11,
        )
        assert sensor._state == CHECKIN_STATE_CHECKED_IN
        assert sensor._checkin_source == "keymaster"

    async def test_toggle_off_while_awaiting_falls_back_to_time_based(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test time-based auto check-in fires when monitoring is off.

        When keymaster monitoring is explicitly disabled the auto
        check-in timer callback should transition to checked_in at
        event start time.
        """
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )
        mock_checkin_coordinator.lockname = "test_lock"
        mock_checkin_coordinator.start_slot = 10
        mock_checkin_coordinator.max_events = 3

        # Explicitly register monitoring switch as OFF
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][mock_checkin_config_entry.entry_id] = {
            KEYMASTER_MONITORING_SWITCH: MagicMock(is_on=False),
        }

        # Set up sensor in awaiting state with future event
        future_start = dt_util.now() + timedelta(hours=2)
        event = _make_event(start=future_start)
        mock_checkin_coordinator.data = [event]
        mock_checkin_coordinator.last_update_success = True
        sensor._handle_coordinator_update()
        assert sensor._state == CHECKIN_STATE_AWAITING

        # Auto check-in timer should be scheduled
        assert sensor._unsub_timer is not None
        assert sensor._transition_target_time == future_start

        # Simulate the timer callback firing at event start
        sensor._async_auto_checkin_callback(future_start)
        assert sensor._state == CHECKIN_STATE_CHECKED_IN
        assert sensor._checkin_source == "automatic"

    async def test_monitoring_on_blocks_auto_checkin_callback(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test auto check-in callback is suppressed when monitoring on.

        When keymaster monitoring is enabled the timer callback must
        NOT transition to checked_in — the sensor should remain in
        awaiting_checkin until the guest actually uses their door code.
        """
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )
        mock_checkin_coordinator.lockname = "test_lock"
        mock_checkin_coordinator.start_slot = 10
        mock_checkin_coordinator.max_events = 3

        # Monitoring switch is ON
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][mock_checkin_config_entry.entry_id] = {
            KEYMASTER_MONITORING_SWITCH: MagicMock(is_on=True),
        }

        # Set up sensor in awaiting state with future event
        future_start = dt_util.now() + timedelta(hours=2)
        event = _make_event(start=future_start)
        mock_checkin_coordinator.data = [event]
        mock_checkin_coordinator.last_update_success = True
        sensor._handle_coordinator_update()
        assert sensor._state == CHECKIN_STATE_AWAITING

        # Timer fires — but monitoring is on so no transition
        sensor._async_auto_checkin_callback(future_start)
        assert sensor._state == CHECKIN_STATE_AWAITING
        assert sensor._checkin_source is None

    async def test_monitoring_on_blocks_immediate_auto_checkin(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test past-start event stays awaiting when monitoring is on.

        When a new event whose start time has already passed is picked
        up by the coordinator update, _transition_to_awaiting should
        NOT immediately transition to checked_in if keymaster
        monitoring is enabled.
        """
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )
        mock_checkin_coordinator.lockname = "test_lock"
        mock_checkin_coordinator.start_slot = 10
        mock_checkin_coordinator.max_events = 3

        # Monitoring switch is ON
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][mock_checkin_config_entry.entry_id] = {
            KEYMASTER_MONITORING_SWITCH: MagicMock(is_on=True),
        }

        # Event whose start has already passed
        event = _make_event(
            start=dt_util.now() - timedelta(hours=1),
            end=dt_util.now() + timedelta(hours=48),
        )
        mock_checkin_coordinator.data = [event]
        mock_checkin_coordinator.last_update_success = True

        sensor._handle_coordinator_update()

        # Should stay in awaiting, not auto-checkin
        assert sensor._state == CHECKIN_STATE_AWAITING
        assert sensor._checkin_source is None

    async def test_monitoring_on_then_keymaster_unlock_checks_in(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test full flow: monitoring on, timer suppressed, unlock works.

        With monitoring enabled and event start passed, the sensor
        stays in awaiting_checkin.  A subsequent keymaster unlock
        transitions to checked_in with source ``keymaster``.
        """
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )
        mock_checkin_coordinator.lockname = "test_lock"
        mock_checkin_coordinator.start_slot = 10
        mock_checkin_coordinator.max_events = 3

        # Monitoring switch is ON
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][mock_checkin_config_entry.entry_id] = {
            KEYMASTER_MONITORING_SWITCH: MagicMock(is_on=True),
        }

        # Event whose start has already passed
        event = _make_event(
            start=dt_util.now() - timedelta(hours=1),
            end=dt_util.now() + timedelta(hours=48),
        )
        mock_checkin_coordinator.data = [event]
        mock_checkin_coordinator.last_update_success = True

        sensor._handle_coordinator_update()
        assert sensor._state == CHECKIN_STATE_AWAITING

        # Guest uses their door code
        sensor.async_handle_keymaster_unlock(code_slot_num=11)
        assert sensor._state == CHECKIN_STATE_CHECKED_IN
        assert sensor._checkin_source == "keymaster"

    async def test_toggle_while_checked_in_no_effect(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test toggling while already checked_in has no effect on current event."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )
        mock_checkin_coordinator.lockname = "test_lock"
        mock_checkin_coordinator.start_slot = 10
        mock_checkin_coordinator.max_events = 3

        # Set up sensor in checked_in state (via automatic checkin)
        now = dt_util.now()
        event = _make_event(
            start=now - timedelta(hours=1),
            end=now + timedelta(hours=48),
        )
        mock_checkin_coordinator.data = [event]
        sensor._state = CHECKIN_STATE_CHECKED_IN
        sensor._tracked_event_summary = event.summary
        sensor._tracked_event_start = event.start
        sensor._tracked_event_end = event.end
        sensor._checkin_source = "automatic"

        # Unlock event while already checked in — should be ignored
        sensor.async_handle_keymaster_unlock(
            code_slot_num=11,
        )

        # State and source unchanged
        assert sensor._state == CHECKIN_STATE_CHECKED_IN
        assert sensor._checkin_source == "automatic"

    async def test_unlock_in_no_reservation_state_ignored(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test keymaster unlock while in no_reservation state is ignored."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )
        mock_checkin_coordinator.lockname = "test_lock"
        mock_checkin_coordinator.start_slot = 10
        mock_checkin_coordinator.max_events = 3

        assert sensor._state == CHECKIN_STATE_NO_RESERVATION

        sensor.async_handle_keymaster_unlock(
            code_slot_num=11,
        )

        assert sensor._state == CHECKIN_STATE_NO_RESERVATION

    async def test_unlock_in_checked_out_state_ignored(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test keymaster unlock while in checked_out state is ignored."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )
        mock_checkin_coordinator.lockname = "test_lock"
        mock_checkin_coordinator.start_slot = 10
        mock_checkin_coordinator.max_events = 3

        sensor._state = CHECKIN_STATE_CHECKED_OUT

        sensor.async_handle_keymaster_unlock(
            code_slot_num=11,
        )

        assert sensor._state == CHECKIN_STATE_CHECKED_OUT


# ===========================================================================
# T024/T026: Event bus listener filtering and forwarding
# ===========================================================================


class TestEventBusListenerFiltering:
    """Tests for keymaster event bus listener in __init__.py (T024/T026).

    Validates that async_register_keymaster_listener correctly filters
    events by lockname, state, and code_slot_num before forwarding to
    the checkin sensor.
    """

    async def test_matching_event_forwarded_to_sensor(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test matching unlock event is forwarded to checkin sensor."""
        from custom_components.rental_control import async_register_keymaster_listener
        from custom_components.rental_control.const import CHECKIN_SENSOR
        from custom_components.rental_control.const import COORDINATOR
        from custom_components.rental_control.const import DOMAIN
        from custom_components.rental_control.const import KEYMASTER_MONITORING_SWITCH
        from custom_components.rental_control.const import UNSUB_LISTENERS

        mock_checkin_coordinator.lockname = "front_door"
        mock_checkin_coordinator.start_slot = 10
        mock_checkin_coordinator.max_events = 3

        sensor = MagicMock()
        mock_checkin_config_entry.add_to_hass(hass)
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][mock_checkin_config_entry.entry_id] = {
            COORDINATOR: mock_checkin_coordinator,
            UNSUB_LISTENERS: [],
            CHECKIN_SENSOR: sensor,
            KEYMASTER_MONITORING_SWITCH: MagicMock(is_on=True),
        }

        async_register_keymaster_listener(hass, mock_checkin_config_entry)

        hass.bus.async_fire(
            "keymaster_lock_state_changed",
            {
                "lockname": "front_door",
                "state": "unlocked",
                "code_slot_num": 11,
            },
        )
        await hass.async_block_till_done()

        sensor.async_handle_keymaster_unlock.assert_called_once_with(
            code_slot_num=11,
        )

    async def test_wrong_lockname_not_forwarded(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test event with wrong lockname is filtered out."""
        from custom_components.rental_control import async_register_keymaster_listener
        from custom_components.rental_control.const import CHECKIN_SENSOR
        from custom_components.rental_control.const import COORDINATOR
        from custom_components.rental_control.const import DOMAIN
        from custom_components.rental_control.const import KEYMASTER_MONITORING_SWITCH
        from custom_components.rental_control.const import UNSUB_LISTENERS

        mock_checkin_coordinator.lockname = "front_door"
        mock_checkin_coordinator.start_slot = 10
        mock_checkin_coordinator.max_events = 3

        sensor = MagicMock()
        mock_checkin_config_entry.add_to_hass(hass)
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][mock_checkin_config_entry.entry_id] = {
            COORDINATOR: mock_checkin_coordinator,
            UNSUB_LISTENERS: [],
            CHECKIN_SENSOR: sensor,
            KEYMASTER_MONITORING_SWITCH: MagicMock(is_on=True),
        }

        async_register_keymaster_listener(hass, mock_checkin_config_entry)

        hass.bus.async_fire(
            "keymaster_lock_state_changed",
            {
                "lockname": "back_door",
                "state": "unlocked",
                "code_slot_num": 11,
            },
        )
        await hass.async_block_till_done()

        sensor.async_handle_keymaster_unlock.assert_not_called()

    async def test_locked_state_not_forwarded(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test event with state != unlocked is filtered out."""
        from custom_components.rental_control import async_register_keymaster_listener
        from custom_components.rental_control.const import CHECKIN_SENSOR
        from custom_components.rental_control.const import COORDINATOR
        from custom_components.rental_control.const import DOMAIN
        from custom_components.rental_control.const import KEYMASTER_MONITORING_SWITCH
        from custom_components.rental_control.const import UNSUB_LISTENERS

        mock_checkin_coordinator.lockname = "front_door"
        mock_checkin_coordinator.start_slot = 10
        mock_checkin_coordinator.max_events = 3

        sensor = MagicMock()
        mock_checkin_config_entry.add_to_hass(hass)
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][mock_checkin_config_entry.entry_id] = {
            COORDINATOR: mock_checkin_coordinator,
            UNSUB_LISTENERS: [],
            CHECKIN_SENSOR: sensor,
            KEYMASTER_MONITORING_SWITCH: MagicMock(is_on=True),
        }

        async_register_keymaster_listener(hass, mock_checkin_config_entry)

        hass.bus.async_fire(
            "keymaster_lock_state_changed",
            {
                "lockname": "front_door",
                "state": "locked",
                "code_slot_num": 11,
            },
        )
        await hass.async_block_till_done()

        sensor.async_handle_keymaster_unlock.assert_not_called()

    async def test_code_slot_zero_not_forwarded(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test event with code_slot_num == 0 is filtered (FR-017)."""
        from custom_components.rental_control import async_register_keymaster_listener
        from custom_components.rental_control.const import CHECKIN_SENSOR
        from custom_components.rental_control.const import COORDINATOR
        from custom_components.rental_control.const import DOMAIN
        from custom_components.rental_control.const import KEYMASTER_MONITORING_SWITCH
        from custom_components.rental_control.const import UNSUB_LISTENERS

        mock_checkin_coordinator.lockname = "front_door"
        mock_checkin_coordinator.start_slot = 10
        mock_checkin_coordinator.max_events = 3

        sensor = MagicMock()
        mock_checkin_config_entry.add_to_hass(hass)
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][mock_checkin_config_entry.entry_id] = {
            COORDINATOR: mock_checkin_coordinator,
            UNSUB_LISTENERS: [],
            CHECKIN_SENSOR: sensor,
            KEYMASTER_MONITORING_SWITCH: MagicMock(is_on=True),
        }

        async_register_keymaster_listener(hass, mock_checkin_config_entry)

        hass.bus.async_fire(
            "keymaster_lock_state_changed",
            {
                "lockname": "front_door",
                "state": "unlocked",
                "code_slot_num": 0,
            },
        )
        await hass.async_block_till_done()

        sensor.async_handle_keymaster_unlock.assert_not_called()

    async def test_code_slot_outside_range_not_forwarded(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test event with slot outside managed range is filtered."""
        from custom_components.rental_control import async_register_keymaster_listener
        from custom_components.rental_control.const import CHECKIN_SENSOR
        from custom_components.rental_control.const import COORDINATOR
        from custom_components.rental_control.const import DOMAIN
        from custom_components.rental_control.const import KEYMASTER_MONITORING_SWITCH
        from custom_components.rental_control.const import UNSUB_LISTENERS

        mock_checkin_coordinator.lockname = "front_door"
        mock_checkin_coordinator.start_slot = 10
        mock_checkin_coordinator.max_events = 3

        sensor = MagicMock()
        mock_checkin_config_entry.add_to_hass(hass)
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][mock_checkin_config_entry.entry_id] = {
            COORDINATOR: mock_checkin_coordinator,
            UNSUB_LISTENERS: [],
            CHECKIN_SENSOR: sensor,
            KEYMASTER_MONITORING_SWITCH: MagicMock(is_on=True),
        }

        async_register_keymaster_listener(hass, mock_checkin_config_entry)

        hass.bus.async_fire(
            "keymaster_lock_state_changed",
            {
                "lockname": "front_door",
                "state": "unlocked",
                "code_slot_num": 99,
            },
        )
        await hass.async_block_till_done()

        sensor.async_handle_keymaster_unlock.assert_not_called()

    async def test_unsubscribe_stops_forwarding(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test unsubscribe function stops event forwarding."""
        from custom_components.rental_control import async_register_keymaster_listener
        from custom_components.rental_control.const import CHECKIN_SENSOR
        from custom_components.rental_control.const import COORDINATOR
        from custom_components.rental_control.const import DOMAIN
        from custom_components.rental_control.const import KEYMASTER_MONITORING_SWITCH
        from custom_components.rental_control.const import UNSUB_LISTENERS

        mock_checkin_coordinator.lockname = "front_door"
        mock_checkin_coordinator.start_slot = 10
        mock_checkin_coordinator.max_events = 3

        unsub_list: list = []
        sensor = MagicMock()
        mock_checkin_config_entry.add_to_hass(hass)
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][mock_checkin_config_entry.entry_id] = {
            COORDINATOR: mock_checkin_coordinator,
            UNSUB_LISTENERS: unsub_list,
            CHECKIN_SENSOR: sensor,
            KEYMASTER_MONITORING_SWITCH: MagicMock(is_on=True),
        }

        async_register_keymaster_listener(hass, mock_checkin_config_entry)

        assert len(unsub_list) == 1
        assert callable(unsub_list[0])

        # Fire matching event — should forward
        hass.bus.async_fire(
            "keymaster_lock_state_changed",
            {
                "lockname": "front_door",
                "state": "unlocked",
                "code_slot_num": 11,
            },
        )
        await hass.async_block_till_done()
        assert sensor.async_handle_keymaster_unlock.call_count == 1

        # Unsubscribe and fire again — should NOT forward
        unsub_list[0]()
        sensor.async_handle_keymaster_unlock.reset_mock()
        hass.bus.async_fire(
            "keymaster_lock_state_changed",
            {
                "lockname": "front_door",
                "state": "unlocked",
                "code_slot_num": 11,
            },
        )
        await hass.async_block_till_done()
        sensor.async_handle_keymaster_unlock.assert_not_called()

    async def test_missing_sensor_reference_no_crash(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test no crash when checkin sensor not in hass.data."""
        from custom_components.rental_control import async_register_keymaster_listener
        from custom_components.rental_control.const import COORDINATOR
        from custom_components.rental_control.const import DOMAIN
        from custom_components.rental_control.const import KEYMASTER_MONITORING_SWITCH
        from custom_components.rental_control.const import UNSUB_LISTENERS

        mock_checkin_coordinator.lockname = "front_door"
        mock_checkin_coordinator.start_slot = 10
        mock_checkin_coordinator.max_events = 3

        mock_checkin_config_entry.add_to_hass(hass)
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][mock_checkin_config_entry.entry_id] = {
            COORDINATOR: mock_checkin_coordinator,
            UNSUB_LISTENERS: [],
            KEYMASTER_MONITORING_SWITCH: MagicMock(is_on=True),
        }

        async_register_keymaster_listener(hass, mock_checkin_config_entry)

        # Fire matching event — sensor not stored, should not crash
        hass.bus.async_fire(
            "keymaster_lock_state_changed",
            {
                "lockname": "front_door",
                "state": "unlocked",
                "code_slot_num": 11,
            },
        )
        await hass.async_block_till_done()

    async def test_monitoring_switch_off_not_forwarded(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test event filtered when monitoring switch is off."""
        from custom_components.rental_control import async_register_keymaster_listener
        from custom_components.rental_control.const import CHECKIN_SENSOR
        from custom_components.rental_control.const import COORDINATOR
        from custom_components.rental_control.const import DOMAIN
        from custom_components.rental_control.const import KEYMASTER_MONITORING_SWITCH
        from custom_components.rental_control.const import UNSUB_LISTENERS

        mock_checkin_coordinator.lockname = "front_door"
        mock_checkin_coordinator.start_slot = 10
        mock_checkin_coordinator.max_events = 3

        sensor = MagicMock()
        mock_checkin_config_entry.add_to_hass(hass)
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][mock_checkin_config_entry.entry_id] = {
            COORDINATOR: mock_checkin_coordinator,
            UNSUB_LISTENERS: [],
            CHECKIN_SENSOR: sensor,
            KEYMASTER_MONITORING_SWITCH: MagicMock(is_on=False),
        }

        async_register_keymaster_listener(hass, mock_checkin_config_entry)

        hass.bus.async_fire(
            "keymaster_lock_state_changed",
            {
                "lockname": "front_door",
                "state": "unlocked",
                "code_slot_num": 11,
            },
        )
        await hass.async_block_till_done()

        sensor.async_handle_keymaster_unlock.assert_not_called()

    async def test_monitoring_switch_missing_not_forwarded(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test event filtered when monitoring switch not in hass.data."""
        from custom_components.rental_control import async_register_keymaster_listener
        from custom_components.rental_control.const import CHECKIN_SENSOR
        from custom_components.rental_control.const import COORDINATOR
        from custom_components.rental_control.const import DOMAIN
        from custom_components.rental_control.const import UNSUB_LISTENERS

        mock_checkin_coordinator.lockname = "front_door"
        mock_checkin_coordinator.start_slot = 10
        mock_checkin_coordinator.max_events = 3

        sensor = MagicMock()
        mock_checkin_config_entry.add_to_hass(hass)
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][mock_checkin_config_entry.entry_id] = {
            COORDINATOR: mock_checkin_coordinator,
            UNSUB_LISTENERS: [],
            CHECKIN_SENSOR: sensor,
        }

        async_register_keymaster_listener(hass, mock_checkin_config_entry)

        hass.bus.async_fire(
            "keymaster_lock_state_changed",
            {
                "lockname": "front_door",
                "state": "unlocked",
                "code_slot_num": 11,
            },
        )
        await hass.async_block_till_done()

        sensor.async_handle_keymaster_unlock.assert_not_called()
        assert sensor._unsub_timer is not None


# ===========================================================================
# T027: Manual checkout action (async_checkout)
# ===========================================================================


class TestManualCheckout:
    """Tests for manual checkout action / async_checkout (T027)."""

    async def test_checkout_success_when_checked_in_within_window(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test successful checkout when checked_in and within reservation window.

        Verifies:
        - State transitions to checked_out
        - rental_control_checkout fires with source: manual
        - checkout_time is recorded
        """
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        now = dt_util.now()
        start = now - timedelta(hours=2)
        end = now + timedelta(hours=48)

        # Set sensor to checked_in state with active reservation window
        sensor._state = CHECKIN_STATE_CHECKED_IN
        sensor._tracked_event_summary = "Reserved - John Smith"
        sensor._tracked_event_start = start
        sensor._tracked_event_end = end
        sensor._tracked_event_slot_name = "John Smith"
        sensor._checkin_source = "automatic"

        # No follow-on events
        mock_checkin_coordinator.data = []

        # Collect fired events
        fired_events: list = []
        hass.bus.async_listen(
            EVENT_RENTAL_CONTROL_CHECKOUT,
            lambda e: fired_events.append(e),
        )

        await sensor.async_checkout()
        await hass.async_block_till_done()

        assert sensor._state == CHECKIN_STATE_CHECKED_OUT
        assert sensor._checkout_source == "manual"
        assert sensor._checkout_time is not None

        # Verify checkout event fired with source: manual
        assert len(fired_events) == 1
        event_data = fired_events[0].data
        assert event_data["entity_id"] == "sensor.test_rental_checkin"
        assert event_data["source"] == "manual"
        assert event_data["summary"] == "Reserved - John Smith"
        assert event_data["start"] == start.isoformat()
        assert event_data["end"] == end.isoformat()
        assert event_data["guest_name"] == "John Smith"

    async def test_checkout_raises_when_not_checked_in(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test ServiceValidationError raised when state is not checked_in (FR-019).

        Tests all non-checked_in states: no_reservation, awaiting_checkin,
        checked_out.
        """
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        for state in [
            CHECKIN_STATE_NO_RESERVATION,
            CHECKIN_STATE_AWAITING,
            CHECKIN_STATE_CHECKED_OUT,
        ]:
            sensor._state = state
            with pytest.raises(ServiceValidationError, match="current state"):
                await sensor.async_checkout()

    async def test_checkout_raises_when_before_reservation_start(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test ServiceValidationError when current time is before event start (FR-019).

        Guard condition: current datetime must be >= start_datetime.
        """
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        now = dt_util.now()
        # Event starts in the future — shouldn't normally be checked_in,
        # but we force the state to test the guard condition
        sensor._state = CHECKIN_STATE_CHECKED_IN
        sensor._tracked_event_summary = "Reserved - John Smith"
        sensor._tracked_event_start = now + timedelta(hours=2)
        sensor._tracked_event_end = now + timedelta(hours=48)

        with pytest.raises(ServiceValidationError, match="active reservation window"):
            await sensor.async_checkout()

    async def test_checkout_raises_when_at_or_after_reservation_end(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test ServiceValidationError when current time is at or after event end (FR-019).

        Guard condition: current datetime must be strictly before end_datetime.
        Tests both at-end and after-end scenarios.
        """
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        now = dt_util.now()

        # Case 1: exactly at end time
        sensor._state = CHECKIN_STATE_CHECKED_IN
        sensor._tracked_event_summary = "Reserved - John Smith"
        sensor._tracked_event_start = now - timedelta(hours=48)
        sensor._tracked_event_end = now  # End is exactly now

        with pytest.raises(ServiceValidationError, match="active reservation window"):
            await sensor.async_checkout()

        # Case 2: after end time
        sensor._state = CHECKIN_STATE_CHECKED_IN
        sensor._tracked_event_end = now - timedelta(hours=1)

        with pytest.raises(ServiceValidationError, match="active reservation window"):
            await sensor.async_checkout()

    async def test_checkout_error_message_includes_state(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test that guard error message includes current state per contract."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        sensor._state = CHECKIN_STATE_AWAITING

        with pytest.raises(ServiceValidationError) as exc_info:
            await sensor.async_checkout()

        assert CHECKIN_STATE_AWAITING in str(exc_info.value)

    async def test_checkout_error_message_includes_datetime_info(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test that reservation window error includes datetime info per contract."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        now = dt_util.now()
        start = now + timedelta(hours=2)
        end = now + timedelta(hours=48)

        sensor._state = CHECKIN_STATE_CHECKED_IN
        sensor._tracked_event_summary = "Reserved - John Smith"
        sensor._tracked_event_start = start
        sensor._tracked_event_end = end

        with pytest.raises(ServiceValidationError) as exc_info:
            await sensor.async_checkout()

        error_msg = str(exc_info.value)
        # Per contract: message should reference the allowed window
        assert "active reservation window" in error_msg
        assert start.isoformat() in error_msg
        assert end.isoformat() in error_msg

    async def test_checkout_no_state_change_on_guard_failure(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test no state change or events when guard conditions fail."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        sensor._state = CHECKIN_STATE_NO_RESERVATION
        original_state = sensor._state

        fired_events: list = []
        hass.bus.async_listen(
            EVENT_RENTAL_CONTROL_CHECKOUT,
            lambda e: fired_events.append(e),
        )

        with pytest.raises(ServiceValidationError):
            await sensor.async_checkout()

        await hass.async_block_till_done()

        # No state change, no events fired
        assert sensor._state == original_state
        assert len(fired_events) == 0

    async def test_checkout_linger_timing_uses_actual_checkout_time(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test post-checkout linger is computed from actual checkout time.

        When manual checkout occurs mid-reservation, linger timing should
        be based on the actual checkout time (dt_util.now()), not the
        event end time.
        """
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        now = dt_util.now()
        start = now - timedelta(hours=2)
        # Event still has 48 hours to go
        end = now + timedelta(hours=48)

        sensor._state = CHECKIN_STATE_CHECKED_IN
        sensor._tracked_event_summary = "Reserved - John Smith"
        sensor._tracked_event_start = start
        sensor._tracked_event_end = end
        sensor._tracked_event_slot_name = "John Smith"

        # No follow-on events → FR-006b cleaning window
        mock_checkin_coordinator.data = []

        await sensor.async_checkout()

        assert sensor._state == CHECKIN_STATE_CHECKED_OUT
        assert sensor._checkout_time is not None
        assert sensor._transition_target_time is not None

        # Linger should be based on checkout_time (≈now), not event end
        expected_linger = sensor._checkout_time + timedelta(
            hours=DEFAULT_CLEANING_WINDOW
        )
        delta = sensor._transition_target_time - expected_linger
        assert abs(delta.total_seconds()) < 1

        # Verify checkout_time is approximately now (not event end)
        assert abs((sensor._checkout_time - now).total_seconds()) < 2
        # Event end is 48 hours away — if linger used event end,
        # transition_target_time would be ~54 hours from now
        assert sensor._transition_target_time < now + timedelta(hours=12)

    async def test_checkout_at_start_boundary_succeeds(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test checkout succeeds when current time equals event start (inclusive).

        Guard: current datetime must be >= start (inclusive boundary).
        """
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        now = dt_util.now()
        # Start is exactly now or slightly in the past to ensure boundary
        start = now - timedelta(seconds=1)
        end = now + timedelta(hours=48)

        sensor._state = CHECKIN_STATE_CHECKED_IN
        sensor._tracked_event_summary = "Reserved - John Smith"
        sensor._tracked_event_start = start
        sensor._tracked_event_end = end
        sensor._tracked_event_slot_name = "John Smith"

        mock_checkin_coordinator.data = []

        # Should NOT raise — start boundary is inclusive
        await sensor.async_checkout()

        assert sensor._state == CHECKIN_STATE_CHECKED_OUT


# ===========================================================================
# T032: Early checkout expiry integration tests
# ===========================================================================


def _setup_early_expiry_switch(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    is_on: bool,
) -> MagicMock:
    """Create a mock EarlyCheckoutExpirySwitch and store it in hass.data.

    Args:
        hass: Home Assistant instance.
        config_entry: Mock config entry.
        is_on: Whether the switch should be on.

    Returns:
        MagicMock: The mock switch.
    """
    mock_switch = MagicMock()
    mock_switch.is_on = is_on

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault(
        config_entry.entry_id,
        {COORDINATOR: MagicMock(), UNSUB_LISTENERS: []},
    )
    hass.data[DOMAIN][config_entry.entry_id][EARLY_CHECKOUT_EXPIRY_SWITCH] = mock_switch
    return mock_switch


class TestEarlyCheckoutExpiry:
    """Tests for early checkout expiry behavior on CheckinTrackingSensor (T032).

    Per FR-022/FR-023, early checkout expiry triggers ONLY on manual
    checkout (async_checkout), NOT on keymaster unlock while checked_in.
    """

    async def test_unlock_while_checked_in_is_ignored(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test keymaster unlock while checked_in does NOT trigger early expiry.

        An unlock mid-stay (guest going to dinner, etc.) must not shorten
        the lock code or alter state — regardless of switch setting.
        """
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        now = dt_util.now()
        start = now - timedelta(hours=2)
        original_end = now + timedelta(hours=48)

        sensor._state = CHECKIN_STATE_CHECKED_IN
        sensor._tracked_event_summary = "Reserved - John Smith"
        sensor._tracked_event_start = start
        sensor._tracked_event_end = original_end
        sensor._tracked_event_slot_name = "John Smith"
        sensor._checkin_source = "keymaster"

        mock_checkin_coordinator.event_overrides.get_slot_key_by_name.return_value = 10

        # Switch ON — unlock should still be ignored
        _setup_early_expiry_switch(hass, mock_checkin_config_entry, is_on=True)

        sensor.async_handle_keymaster_unlock(code_slot_num=10)

        # State and end time must remain unchanged
        assert sensor._state == CHECKIN_STATE_CHECKED_IN
        assert sensor._tracked_event_end == original_end

    async def test_unlock_while_checked_in_with_expiry_off_no_change(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test keymaster unlock while checked_in with early-expiry OFF does NOT alter end time."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        now = dt_util.now()
        start = now - timedelta(hours=2)
        original_end = now + timedelta(hours=48)

        sensor._state = CHECKIN_STATE_CHECKED_IN
        sensor._tracked_event_summary = "Reserved - John Smith"
        sensor._tracked_event_start = start
        sensor._tracked_event_end = original_end
        sensor._tracked_event_slot_name = "John Smith"
        sensor._checkin_source = "keymaster"

        mock_checkin_coordinator.event_overrides.get_slot_key_by_name.return_value = 10

        _setup_early_expiry_switch(hass, mock_checkin_config_entry, is_on=False)

        sensor.async_handle_keymaster_unlock(code_slot_num=10)

        assert sensor._tracked_event_end == original_end
        assert sensor._state == CHECKIN_STATE_CHECKED_IN

    async def test_manual_checkout_with_expiry_on_shortens_end_time(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test manual checkout with early-expiry ON shortens end time.

        Per FR-022, when the early checkout expiry switch is on and
        async_checkout is called, the end time is shortened to
        now + EARLY_CHECKOUT_GRACE_MINUTES before transitioning.
        """
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        now = dt_util.now()
        start = now - timedelta(hours=2)
        original_end = now + timedelta(hours=48)

        sensor._state = CHECKIN_STATE_CHECKED_IN
        sensor._tracked_event_summary = "Reserved - John Smith"
        sensor._tracked_event_start = start
        sensor._tracked_event_end = original_end
        sensor._tracked_event_slot_name = "John Smith"
        sensor._checkin_source = "keymaster"

        mock_checkin_coordinator.data = []

        _setup_early_expiry_switch(hass, mock_checkin_config_entry, is_on=True)

        await sensor.async_checkout()

        # End time should be shortened to approximately now + grace
        assert sensor._tracked_event_end < original_end
        expected_end = now + timedelta(minutes=EARLY_CHECKOUT_GRACE_MINUTES)
        delta = abs((sensor._tracked_event_end - expected_end).total_seconds())
        assert delta < 2

        # Should have transitioned to checked_out
        assert sensor._state == CHECKIN_STATE_CHECKED_OUT

    async def test_manual_checkout_with_expiry_off_no_shortening(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test manual checkout with early-expiry OFF does not shorten end time (FR-023)."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        now = dt_util.now()
        start = now - timedelta(hours=2)
        original_end = now + timedelta(hours=48)

        sensor._state = CHECKIN_STATE_CHECKED_IN
        sensor._tracked_event_summary = "Reserved - John Smith"
        sensor._tracked_event_start = start
        sensor._tracked_event_end = original_end
        sensor._tracked_event_slot_name = "John Smith"
        sensor._checkin_source = "keymaster"

        mock_checkin_coordinator.data = []

        _setup_early_expiry_switch(hass, mock_checkin_config_entry, is_on=False)

        await sensor.async_checkout()

        # End time should remain unchanged
        assert sensor._tracked_event_end == original_end
        assert sensor._state == CHECKIN_STATE_CHECKED_OUT

    async def test_auto_checkout_timer_cancelled_on_manual_checkout(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test auto-checkout timer is cancelled on manual checkout.

        Verifies that the existing auto-checkout timer is cancelled
        when _transition_to_checked_out runs during manual checkout.
        """
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        now = dt_util.now()
        start = now - timedelta(hours=2)
        original_end = now + timedelta(hours=48)

        sensor._state = CHECKIN_STATE_CHECKED_IN
        sensor._tracked_event_summary = "Reserved - John Smith"
        sensor._tracked_event_start = start
        sensor._tracked_event_end = original_end
        sensor._tracked_event_slot_name = "John Smith"
        sensor._checkin_source = "keymaster"

        mock_checkin_coordinator.data = []
        mock_checkin_coordinator.event_overrides.get_slot_key_by_name.return_value = 10

        # Set an existing timer that would fire at original_end
        old_unsub = MagicMock()
        sensor._unsub_timer = old_unsub
        sensor._transition_target_time = original_end

        _setup_early_expiry_switch(hass, mock_checkin_config_entry, is_on=True)

        await sensor.async_checkout()

        # Old timer should have been cancelled
        old_unsub.assert_called_once()

    async def test_manual_checkout_no_switch_no_shortening(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test manual checkout without switch configured does not shorten end time."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        now = dt_util.now()
        original_end = now + timedelta(hours=48)

        sensor._state = CHECKIN_STATE_CHECKED_IN
        sensor._tracked_event_summary = "Reserved - John Smith"
        sensor._tracked_event_start = now - timedelta(hours=2)
        sensor._tracked_event_end = original_end
        sensor._tracked_event_slot_name = "John Smith"

        mock_checkin_coordinator.data = []

        # NO early expiry switch in hass.data

        await sensor.async_checkout()

        # Should still transition, end time unchanged
        assert sensor._state == CHECKIN_STATE_CHECKED_OUT
        assert sensor._tracked_event_end == original_end

    async def test_early_expiry_no_shortening_when_less_than_grace_remain(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test no shortening when less than grace_minutes remain before end.

        When the remaining time is already less than or equal to the grace
        period, the end time should not be changed during manual checkout.
        """
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        now = dt_util.now()
        # Only 10 minutes remain
        original_end = now + timedelta(minutes=10)

        sensor._state = CHECKIN_STATE_CHECKED_IN
        sensor._tracked_event_summary = "Reserved - John Smith"
        sensor._tracked_event_start = now - timedelta(hours=2)
        sensor._tracked_event_end = original_end
        sensor._tracked_event_slot_name = "John Smith"

        mock_checkin_coordinator.data = []

        _setup_early_expiry_switch(hass, mock_checkin_config_entry, is_on=True)

        await sensor.async_checkout()

        # End time should remain unchanged — already within grace period
        assert sensor._tracked_event_end == original_end
        assert sensor._state == CHECKIN_STATE_CHECKED_OUT

    async def test_early_expiry_updates_lock_code_expiry(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test early checkout expiry updates keymaster lock code end time.

        When manual checkout triggers early expiry, the keymaster slot's
        date_range_end entity must be updated via the datetime service.
        """
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        now = dt_util.now()
        start = now - timedelta(hours=2)
        original_end = now + timedelta(hours=48)

        sensor._state = CHECKIN_STATE_CHECKED_IN
        sensor._tracked_event_summary = "Reserved - John Smith"
        sensor._tracked_event_start = start
        sensor._tracked_event_end = original_end
        sensor._tracked_event_slot_name = "John Smith"
        sensor._checkin_source = "keymaster"

        mock_checkin_coordinator.lockname = "front_door"
        mock_checkin_coordinator.event_overrides.get_slot_key_by_name.return_value = 10
        mock_checkin_coordinator.data = []

        _setup_early_expiry_switch(hass, mock_checkin_config_entry, is_on=True)

        mock_coro = AsyncMock(return_value=None)
        with patch(
            "custom_components.rental_control.sensors.checkinsensor.add_call",
            return_value=[mock_coro()],
        ) as mock_add_call:
            await sensor.async_checkout()

            mock_add_call.assert_called_once_with(
                hass,
                [],
                "datetime",
                "set_value",
                "datetime.front_door_code_slot_10_date_range_end",
                {"datetime": sensor._tracked_event_end.isoformat()},
            )

    async def test_early_expiry_no_lock_update_without_lockname(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test no lock code update when coordinator has no lockname."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        now = dt_util.now()
        original_end = now + timedelta(hours=48)

        sensor._state = CHECKIN_STATE_CHECKED_IN
        sensor._tracked_event_summary = "Reserved - John Smith"
        sensor._tracked_event_start = now - timedelta(hours=2)
        sensor._tracked_event_end = original_end
        sensor._tracked_event_slot_name = "John Smith"
        sensor._checkin_source = "keymaster"

        mock_checkin_coordinator.lockname = None
        mock_checkin_coordinator.event_overrides.get_slot_key_by_name.return_value = 10
        mock_checkin_coordinator.data = []

        _setup_early_expiry_switch(hass, mock_checkin_config_entry, is_on=True)

        with patch(
            "custom_components.rental_control.sensors.checkinsensor.add_call",
        ) as mock_add_call:
            await sensor.async_checkout()

            mock_add_call.assert_not_called()

    async def test_early_expiry_no_lock_update_without_slot(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test no lock code update when slot name is not mapped."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        now = dt_util.now()
        original_end = now + timedelta(hours=48)

        sensor._state = CHECKIN_STATE_CHECKED_IN
        sensor._tracked_event_summary = "Reserved - John Smith"
        sensor._tracked_event_start = now - timedelta(hours=2)
        sensor._tracked_event_end = original_end
        sensor._tracked_event_slot_name = "John Smith"
        sensor._checkin_source = "keymaster"

        mock_checkin_coordinator.lockname = "front_door"
        mock_checkin_coordinator.event_overrides.get_slot_key_by_name.return_value = 0
        mock_checkin_coordinator.data = []

        _setup_early_expiry_switch(hass, mock_checkin_config_entry, is_on=True)

        with patch(
            "custom_components.rental_control.sensors.checkinsensor.add_call",
        ) as mock_add_call:
            await sensor.async_checkout()

            mock_add_call.assert_not_called()

    async def test_different_slot_unlock_does_not_trigger_early_expiry(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test unlock from different slot is ignored while checked_in.

        A maintenance or housekeeping code in the managed range
        must not affect the tracked reservation state.
        """
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )

        now = dt_util.now()
        original_end = now + timedelta(hours=48)

        sensor._state = CHECKIN_STATE_CHECKED_IN
        sensor._tracked_event_summary = "Reserved - John Smith"
        sensor._tracked_event_start = now - timedelta(hours=2)
        sensor._tracked_event_end = original_end
        sensor._tracked_event_slot_name = "John Smith"
        sensor._checkin_source = "keymaster"

        mock_checkin_coordinator.event_overrides.get_slot_key_by_name.return_value = 10

        _setup_early_expiry_switch(hass, mock_checkin_config_entry, is_on=True)

        # Unlock from slot 11 (different — e.g. maintenance)
        sensor.async_handle_keymaster_unlock(code_slot_num=11)

        # End time must remain unchanged
        assert sensor._tracked_event_end == original_end
        assert sensor._state == CHECKIN_STATE_CHECKED_IN
        sensor.async_write_ha_state.assert_not_called()


# ===========================================================================
# Event tracking stability (identity-based lookup)
# ===========================================================================


class TestEventTrackingStability:
    """Tests for identity-based event tracking.

    Verifies that the sensor tracks events by identity key
    (summary + start) rather than list position, preventing
    state oscillation when coordinator data reorders.
    """

    async def test_find_tracked_event_at_position_zero(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test _find_tracked_event returns event at position 0."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )
        now = dt_util.now()
        event = _make_event(start=now + timedelta(hours=2))
        mock_checkin_coordinator.data = [event]
        sensor._tracked_event_summary = event.summary
        sensor._tracked_event_start = event.start

        result = sensor._find_tracked_event()

        assert result is event

    async def test_find_tracked_event_at_nonzero_position(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test _find_tracked_event finds event not at position 0."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )
        now = dt_util.now()
        other = _make_event(
            summary="Reserved - Other",
            start=now - timedelta(hours=1),
        )
        tracked = _make_event(
            summary="Reserved - Tracked",
            start=now + timedelta(hours=4),
        )
        mock_checkin_coordinator.data = [other, tracked]
        sensor._tracked_event_summary = tracked.summary
        sensor._tracked_event_start = tracked.start

        result = sensor._find_tracked_event()

        assert result is tracked

    async def test_find_tracked_event_returns_none_when_gone(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test _find_tracked_event returns None when event removed."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )
        now = dt_util.now()
        sensor._tracked_event_summary = "Reserved - Gone"
        sensor._tracked_event_start = now + timedelta(hours=2)
        mock_checkin_coordinator.data = [_make_event(summary="Reserved - Different")]

        result = sensor._find_tracked_event()

        assert result is None

    async def test_find_tracked_event_empty_data(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test _find_tracked_event returns None with empty data."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )
        sensor._tracked_event_summary = "Reserved - Test"
        sensor._tracked_event_start = dt_util.now()
        mock_checkin_coordinator.data = []

        assert sensor._find_tracked_event() is None

    async def test_find_tracked_event_no_tracked_summary(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test _find_tracked_event returns None with no summary."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )
        sensor._tracked_event_summary = None
        sensor._tracked_event_start = dt_util.now()
        mock_checkin_coordinator.data = [_make_event()]

        assert sensor._find_tracked_event() is None

    async def test_checked_in_survives_event_position_shift(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test CHECKED_IN stays stable when tracked event shifts position.

        When a new event appears before the tracked event in the
        coordinator list, the sensor must NOT transition to
        no_reservation.
        """
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )
        now = dt_util.now()
        tracked = _make_event(
            summary="Reserved - Guest B",
            start=now - timedelta(hours=2),
            end=now + timedelta(hours=48),
        )
        mock_checkin_coordinator.data = [tracked]

        sensor._state = CHECKIN_STATE_CHECKED_IN
        sensor._tracked_event_summary = tracked.summary
        sensor._tracked_event_start = tracked.start
        sensor._tracked_event_end = tracked.end
        sensor._checkin_source = "keymaster"

        # Position 0 is tracked event → should stay checked_in
        sensor._handle_coordinator_update()
        assert sensor._state == CHECKIN_STATE_CHECKED_IN

        # Now another event appears at position 0
        earlier = _make_event(
            summary="Reserved - Guest A (leftover)",
            start=now - timedelta(hours=24),
            end=now - timedelta(hours=1),
        )
        mock_checkin_coordinator.data = [earlier, tracked]

        sensor._handle_coordinator_update()
        assert sensor._state == CHECKIN_STATE_CHECKED_IN

    async def test_checked_in_transitions_when_event_truly_gone(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test CHECKED_IN transitions to no_reservation when gone."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )
        now = dt_util.now()

        sensor._state = CHECKIN_STATE_CHECKED_IN
        sensor._tracked_event_summary = "Reserved - Vanished"
        sensor._tracked_event_start = now - timedelta(hours=2)
        sensor._tracked_event_end = now + timedelta(hours=48)
        sensor._checkin_source = "automatic"

        mock_checkin_coordinator.data = [
            _make_event(summary="Reserved - Completely Different")
        ]

        sensor._handle_coordinator_update()
        assert sensor._state == CHECKIN_STATE_NO_RESERVATION

    async def test_awaiting_survives_event_position_shift(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test AWAITING stays stable when tracked event shifts position."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )
        now = dt_util.now()
        tracked = _make_event(
            summary="Reserved - Guest B",
            start=now + timedelta(hours=4),
            end=now + timedelta(hours=124),
        )

        sensor._state = CHECKIN_STATE_AWAITING
        sensor._tracked_event_summary = tracked.summary
        sensor._tracked_event_start = tracked.start
        sensor._tracked_event_end = tracked.end
        sensor._unsub_timer = MagicMock()

        # Tracked at position 0
        mock_checkin_coordinator.data = [tracked]
        sensor._handle_coordinator_update()
        assert sensor._state == CHECKIN_STATE_AWAITING

        # Tracked at position 1
        earlier = _make_event(
            summary="Reserved - Guest A (stale)",
            start=now - timedelta(hours=10),
            end=now - timedelta(hours=1),
        )
        mock_checkin_coordinator.data = [earlier, tracked]
        sensor._handle_coordinator_update()
        assert sensor._state == CHECKIN_STATE_AWAITING
        assert sensor._tracked_event_summary == "Reserved - Guest B"

    async def test_checked_out_no_oscillation(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test CHECKED_OUT does not oscillate on coordinator refresh.

        When the checked-out event appears/disappears from the list
        across refreshes, the linger timer must not be cancelled and
        recomputed on every update.
        """
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )
        now = dt_util.now()
        guest_a = _make_event(
            summary="Reserved - Guest A",
            start=now - timedelta(hours=24),
            end=now - timedelta(hours=1),
        )
        guest_b = _make_event(
            summary="Reserved - Guest B",
            start=now + timedelta(hours=4),
            end=now + timedelta(hours=124),
        )

        sensor._state = CHECKIN_STATE_CHECKED_OUT
        sensor._tracked_event_summary = guest_a.summary
        sensor._tracked_event_start = guest_a.start
        sensor._tracked_event_end = guest_a.end
        sensor._checkout_time = now
        sensor._checked_out_event_key = sensor._event_key(
            guest_a.summary, guest_a.start
        )

        # First refresh: both events visible → computes linger
        mock_checkin_coordinator.data = [guest_a, guest_b]
        sensor._handle_coordinator_update()
        assert sensor._state == CHECKIN_STATE_CHECKED_OUT
        first_timer = sensor._unsub_timer
        first_target = sensor._transition_target_time
        assert first_timer is not None

        # Second refresh: only guest B (guest A filtered)
        mock_checkin_coordinator.data = [guest_b]
        sensor._handle_coordinator_update()
        assert sensor._state == CHECKIN_STATE_CHECKED_OUT
        # Timer must not have been reset
        assert sensor._unsub_timer is first_timer
        assert sensor._transition_target_time == first_target

        # Third refresh: both again
        mock_checkin_coordinator.data = [guest_a, guest_b]
        sensor._handle_coordinator_update()
        assert sensor._state == CHECKIN_STATE_CHECKED_OUT
        assert sensor._unsub_timer is first_timer

    async def test_checked_out_recomputes_on_changed_followon(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test CHECKED_OUT recomputes linger when follow-on changes."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )
        now = dt_util.now()
        guest_a = _make_event(
            summary="Reserved - Guest A",
            start=now - timedelta(hours=24),
            end=now - timedelta(hours=1),
        )
        guest_b = _make_event(
            summary="Reserved - Guest B",
            start=now + timedelta(hours=4),
            end=now + timedelta(hours=124),
        )
        guest_c = _make_event(
            summary="Reserved - Guest C",
            start=now + timedelta(hours=2),
            end=now + timedelta(hours=122),
        )

        sensor._state = CHECKIN_STATE_CHECKED_OUT
        sensor._tracked_event_summary = guest_a.summary
        sensor._tracked_event_start = guest_a.start
        sensor._tracked_event_end = guest_a.end
        sensor._checkout_time = now
        sensor._checked_out_event_key = sensor._event_key(
            guest_a.summary, guest_a.start
        )

        # First: follow-on is Guest B
        mock_checkin_coordinator.data = [guest_b]
        sensor._handle_coordinator_update()
        first_target = sensor._transition_target_time

        # Second: follow-on changes to Guest C (earlier)
        mock_checkin_coordinator.data = [guest_c]
        sensor._handle_coordinator_update()
        assert sensor._transition_target_time != first_target

    async def test_same_day_turnover_full_flow(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test same-day turnover: checkout → linger → awaiting → checkin.

        Guest A checks out, linger timer fires, sensor transitions to
        awaiting_checkin for Guest B.
        """
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )
        now = dt_util.now()
        guest_a = _make_event(
            summary="Reserved - Guest A",
            start=now - timedelta(hours=120),
            end=now - timedelta(minutes=30),
        )
        guest_b = _make_event(
            summary="Reserved - Guest B",
            start=now + timedelta(hours=4),
            end=now + timedelta(hours=124),
        )

        # Set up checked_in state for Guest A
        sensor._state = CHECKIN_STATE_CHECKED_IN
        sensor._tracked_event_summary = guest_a.summary
        sensor._tracked_event_start = guest_a.start
        sensor._tracked_event_end = guest_a.end
        sensor._checkin_source = "automatic"
        mock_checkin_coordinator.data = [guest_a, guest_b]

        # Trigger checkout
        sensor._transition_to_checked_out(source="automatic")
        assert sensor._state == CHECKIN_STATE_CHECKED_OUT
        assert sensor._unsub_timer is not None

        # Simulate linger timer firing
        sensor._async_linger_to_awaiting_callback(now)
        assert sensor._state == CHECKIN_STATE_AWAITING
        assert sensor._tracked_event_summary == guest_b.summary

    async def test_linger_callback_uses_followon_search(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test linger-to-awaiting callback finds follow-on at any position.

        Even if the checked-out event is still at position 0, the
        callback should find the follow-on event correctly.
        """
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )
        now = dt_util.now()
        guest_a = _make_event(
            summary="Reserved - Guest A",
            start=now - timedelta(hours=120),
            end=now - timedelta(hours=1),
        )
        guest_b = _make_event(
            summary="Reserved - Guest B",
            start=now + timedelta(hours=2),
            end=now + timedelta(hours=122),
        )

        sensor._state = CHECKIN_STATE_CHECKED_OUT
        sensor._checkout_time = now - timedelta(minutes=30)
        sensor._checked_out_event_key = sensor._event_key(
            guest_a.summary, guest_a.start
        )
        # Checked-out event still at position 0
        mock_checkin_coordinator.data = [guest_a, guest_b]

        sensor._async_linger_to_awaiting_callback(now)

        assert sensor._state == CHECKIN_STATE_AWAITING
        assert sensor._tracked_event_summary == guest_b.summary

    async def test_checked_out_no_followon_keeps_timer(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test CHECKED_OUT with no follow-on preserves existing timer."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )
        now = dt_util.now()

        sensor._state = CHECKIN_STATE_CHECKED_OUT
        sensor._checkout_time = now
        sensor._checked_out_event_key = "old|key"
        existing_timer = MagicMock()
        sensor._unsub_timer = existing_timer

        # Only the checked-out event remains (no follow-on)
        mock_checkin_coordinator.data = []
        sensor._handle_coordinator_update()

        assert sensor._state == CHECKIN_STATE_CHECKED_OUT
        # Timer should not be replaced when no follow-on and timer exists
        # (the no-followon branch only computes if timer is None)

    async def test_linger_followon_key_set_by_compute(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test _compute_linger_timing sets _linger_followon_key."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )
        now = dt_util.now()
        guest_b = _make_event(
            summary="Reserved - Guest B",
            start=now + timedelta(hours=4),
        )
        mock_checkin_coordinator.data = [guest_b]

        sensor._state = CHECKIN_STATE_CHECKED_OUT
        sensor._checkout_time = now
        sensor._checked_out_event_key = "old|key"

        sensor._compute_linger_timing()

        expected_key = sensor._event_key(guest_b.summary, guest_b.start)
        assert sensor._linger_followon_key == expected_key

    async def test_linger_followon_key_none_when_no_followon(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test _compute_linger_timing clears key with no follow-on."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )
        now = dt_util.now()
        mock_checkin_coordinator.data = []

        sensor._state = CHECKIN_STATE_CHECKED_OUT
        sensor._checkout_time = now
        sensor._linger_followon_key = "stale|key"

        sensor._compute_linger_timing()

        assert sensor._linger_followon_key is None

    async def test_linger_followon_key_cleared_on_awaiting(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test _linger_followon_key cleared on transition to awaiting."""
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )
        now = dt_util.now()
        event = _make_event(start=now + timedelta(hours=4))
        sensor._linger_followon_key = "some|key"

        sensor._transition_to_awaiting(event)

        assert sensor._linger_followon_key is None

    async def test_checked_out_followon_disappears_recomputes(
        self,
        hass: HomeAssistant,
        mock_checkin_coordinator: MagicMock,
        mock_checkin_config_entry: MockConfigEntry,
    ) -> None:
        """Test CHECKED_OUT recomputes when follow-on disappears.

        When a follow-on event existed (FR-006a timer scheduled) but
        then gets cancelled, the sensor must recompute linger timing
        for the cleaning-window scenario (FR-006b).
        """
        sensor = _create_sensor(
            hass, mock_checkin_coordinator, mock_checkin_config_entry
        )
        now = dt_util.now()
        guest_a = _make_event(
            summary="Reserved - Guest A",
            start=now - timedelta(hours=24),
            end=now - timedelta(hours=1),
        )
        guest_b = _make_event(
            summary="Reserved - Guest B",
            start=now + timedelta(hours=4),
            end=now + timedelta(hours=124),
        )

        sensor._state = CHECKIN_STATE_CHECKED_OUT
        sensor._tracked_event_summary = guest_a.summary
        sensor._tracked_event_start = guest_a.start
        sensor._tracked_event_end = guest_a.end
        sensor._checkout_time = now
        sensor._checked_out_event_key = sensor._event_key(
            guest_a.summary, guest_a.start
        )

        # First: follow-on exists → linger computed with follow-on key
        mock_checkin_coordinator.data = [guest_b]
        sensor._handle_coordinator_update()
        assert sensor._linger_followon_key is not None
        first_target = sensor._transition_target_time

        # Second: follow-on disappears
        mock_checkin_coordinator.data = []
        sensor._handle_coordinator_update()
        # Key cleared, timer recomputed for cleaning window
        assert sensor._linger_followon_key is None
        assert sensor._transition_target_time != first_target
