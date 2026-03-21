# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Integration tests for check-in tracking turnover scenarios.

Tests cover:
- T036: Same-day turnover (FR-006a) full lifecycle with mocked time
- T037: Different-day (FR-006c) and no-follow-on (FR-006b) full lifecycle
- T043: Full lifecycle integration tests (setup → all states → cleanup)
"""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from freezegun import freeze_time
from homeassistant.components.calendar import CalendarEvent
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.rental_control.const import CHECKIN_STATE_AWAITING
from custom_components.rental_control.const import CHECKIN_STATE_CHECKED_IN
from custom_components.rental_control.const import CHECKIN_STATE_CHECKED_OUT
from custom_components.rental_control.const import CHECKIN_STATE_NO_RESERVATION
from custom_components.rental_control.const import CONF_CLEANING_WINDOW
from custom_components.rental_control.const import DEFAULT_CLEANING_WINDOW
from custom_components.rental_control.const import DOMAIN
from custom_components.rental_control.const import EVENT_RENTAL_CONTROL_CHECKIN
from custom_components.rental_control.const import EVENT_RENTAL_CONTROL_CHECKOUT
from custom_components.rental_control.sensors.checkinsensor import CheckinTrackingSensor

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(
    summary: str,
    start: datetime,
    end: datetime,
    description: str = "Guest: Test",
) -> CalendarEvent:
    """Create a CalendarEvent for testing."""
    return CalendarEvent(
        summary=summary,
        start=start,
        end=end,
        description=description,
    )


def _create_sensor(
    hass: HomeAssistant,
    coordinator: MagicMock,
    config_entry: MockConfigEntry,
) -> CheckinTrackingSensor:
    """Create a CheckinTrackingSensor wired to a mock coordinator."""
    config_entry.add_to_hass(hass)
    sensor = CheckinTrackingSensor(hass, coordinator, config_entry)
    sensor.entity_id = "sensor.test_rental_checkin"
    sensor.hass = hass
    # Patch async_write_ha_state to avoid entity registry issues
    sensor.async_write_ha_state = MagicMock()  # type: ignore[assignment]
    return sensor


def _make_coordinator(hass: HomeAssistant) -> MagicMock:
    """Return a mock coordinator with sensible defaults."""
    coordinator = MagicMock()
    coordinator.hass = hass
    coordinator.data = []
    coordinator.last_update_success = True
    coordinator.lockname = None
    coordinator.start_slot = 10
    coordinator.max_events = 3
    coordinator.event_prefix = ""
    coordinator.unique_id = "test-integration-checkin-id"
    coordinator.name = "Test Rental"
    coordinator.device_info = {
        "identifiers": {(DOMAIN, "test-integration-checkin-id")},
        "name": "Test Rental",
        "sw_version": "0.0.0",
    }
    return coordinator


def _make_config_entry() -> MockConfigEntry:
    """Return a mock config entry with cleaning window."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Test Rental",
        version=7,
        unique_id="test-integration-checkin-id",
        data={
            "name": "Test Rental",
            "url": "https://example.com/calendar.ics",
            "timezone": "UTC",
            "checkin": "16:00",
            "checkout": "11:00",
            "start_slot": 10,
            "max_events": 3,
            "days": 90,
            "verify_ssl": True,
            "ignore_non_reserved": False,
        },
        options={
            "refresh_frequency": 5,
            CONF_CLEANING_WINDOW: DEFAULT_CLEANING_WINDOW,
        },
        entry_id="test_integration_checkin_entry_id",
    )


# ===========================================================================
# T036: Same-day turnover — FR-006a integration tests
# ===========================================================================


class TestSameDayTurnoverFR006a:
    """Integration tests for same-day turnover (FR-006a).

    Full lifecycle with mocked time progression:
    event 0 checked_out → half-gap linger → awaiting_checkin for event 1.
    """

    @freeze_time("2025-06-15T08:00:00+00:00")
    async def test_same_day_turnover_full_lifecycle(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test FR-006a: full lifecycle with time progression.

        Frozen at 08:00 UTC on June 15.
        Event 0: started 5 days ago, ends today at 11:00.
        Event 1: starts today at 16:00.
        Gap = 5h, half-gap = 2.5h → linger expires at ~13:30.

        Lifecycle: no_reservation → awaiting → checked_in (event0 start
        is in the past) → [fire at 11:00] → checked_out →
        [fire at half-gap] → awaiting_checkin (event 1).
        """
        coordinator = _make_coordinator(hass)
        config_entry = _make_config_entry()
        sensor = _create_sensor(hass, coordinator, config_entry)

        event0_start = datetime(2025, 6, 10, 16, 0, 0, tzinfo=dt_util.UTC)
        event0_end = datetime(2025, 6, 15, 11, 0, 0, tzinfo=dt_util.UTC)
        event1_start = datetime(2025, 6, 15, 16, 0, 0, tzinfo=dt_util.UTC)
        event1_end = datetime(2025, 6, 20, 11, 0, 0, tzinfo=dt_util.UTC)

        event0 = _make_event("Reserved - Alice", event0_start, event0_end)
        event1 = _make_event("Reserved - Bob", event1_start, event1_end)

        # Phase 1: Sensor starts in no_reservation, sees event0
        # Event0 start (June 10) is in the past at frozen time (June 15 08:00)
        # So _transition_to_awaiting detects start passed → auto checkin
        coordinator.data = [event0, event1]
        sensor._handle_coordinator_update()

        assert sensor._state == CHECKIN_STATE_CHECKED_IN
        assert sensor._tracked_event_summary == "Reserved - Alice"

        # Phase 2: auto-checkout timer should be scheduled at event0_end
        assert sensor._transition_target_time == event0_end
        assert sensor._unsub_timer is not None

        # Phase 3: Fire time at event0_end → auto checkout fires
        async_fire_time_changed(hass, event0_end)
        await hass.async_block_till_done()

        assert sensor._state == CHECKIN_STATE_CHECKED_OUT
        assert sensor._checkout_time is not None
        assert sensor._checkout_source == "automatic"

        # Verify half-gap linger target
        checkout_time = sensor._checkout_time
        gap = event1_start - checkout_time
        expected_half_gap = checkout_time + gap / 2
        assert sensor._transition_target_time is not None
        delta = abs(
            (sensor._transition_target_time - expected_half_gap).total_seconds()
        )
        assert delta < 2

        # Phase 4: Fire time at linger target → awaiting for event 1
        linger_target = sensor._transition_target_time
        async_fire_time_changed(hass, linger_target)
        await hass.async_block_till_done()

        assert sensor._state == CHECKIN_STATE_AWAITING
        assert sensor._tracked_event_summary == "Reserved - Bob"

    @freeze_time("2025-06-15T09:00:00+00:00")
    async def test_same_day_turnover_does_not_shift_early(
        self,
        hass: HomeAssistant,
    ) -> None:
        """FR-029: sensor does NOT switch to event 1 while event 0 is active.

        While checked_in for event 0, coordinator updates with both
        events visible must NOT cause a transition to event 1.
        """
        coordinator = _make_coordinator(hass)
        config_entry = _make_config_entry()
        sensor = _create_sensor(hass, coordinator, config_entry)

        event0_start = datetime(2025, 6, 10, 16, 0, 0, tzinfo=dt_util.UTC)
        event0_end = datetime(2025, 6, 15, 11, 0, 0, tzinfo=dt_util.UTC)
        event1_start = datetime(2025, 6, 15, 16, 0, 0, tzinfo=dt_util.UTC)
        event1_end = datetime(2025, 6, 20, 11, 0, 0, tzinfo=dt_util.UTC)

        event0 = _make_event("Reserved - Alice", event0_start, event0_end)
        event1 = _make_event("Reserved - Bob", event1_start, event1_end)

        # Manually place sensor in checked_in for event 0
        sensor._state = CHECKIN_STATE_CHECKED_IN
        sensor._tracked_event_summary = "Reserved - Alice"
        sensor._tracked_event_start = event0_start
        sensor._tracked_event_end = event0_end
        sensor._checkin_source = "automatic"

        # Coordinator update with both events visible
        coordinator.data = [event0, event1]
        sensor._handle_coordinator_update()

        # Must remain checked_in for event 0
        assert sensor._state == CHECKIN_STATE_CHECKED_IN
        assert sensor._tracked_event_summary == "Reserved - Alice"
        assert sensor._tracked_event_end == event0_end

    @freeze_time("2025-06-15T08:00:00+00:00")
    async def test_same_day_event0_still_in_coordinator_after_checkout(
        self,
        hass: HomeAssistant,
    ) -> None:
        """After checkout, event 0 may still be data[0] in coordinator.

        The sensor must recognize it via FR-007 (event key match) and
        NOT re-transition. Then the linger callback correctly picks event 1.
        """
        coordinator = _make_coordinator(hass)
        config_entry = _make_config_entry()
        sensor = _create_sensor(hass, coordinator, config_entry)

        event0_start = datetime(2025, 6, 10, 16, 0, 0, tzinfo=dt_util.UTC)
        event0_end = datetime(2025, 6, 15, 11, 0, 0, tzinfo=dt_util.UTC)
        event1_start = datetime(2025, 6, 15, 16, 0, 0, tzinfo=dt_util.UTC)
        event1_end = datetime(2025, 6, 20, 11, 0, 0, tzinfo=dt_util.UTC)

        event0 = _make_event("Reserved - Alice", event0_start, event0_end)
        event1 = _make_event("Reserved - Bob", event1_start, event1_end)

        # Put sensor in checked_in state for event 0
        sensor._state = CHECKIN_STATE_CHECKED_IN
        sensor._tracked_event_summary = "Reserved - Alice"
        sensor._tracked_event_start = event0_start
        sensor._tracked_event_end = event0_end

        coordinator.data = [event0, event1]

        # Trigger checkout
        sensor._transition_to_checked_out(source="automatic")

        assert sensor._state == CHECKIN_STATE_CHECKED_OUT
        assert sensor._checked_out_event_key is not None

        # Coordinator update: event 0 still data[0]
        # FR-007: same event key → no re-transition
        sensor._handle_coordinator_update()
        assert sensor._state == CHECKIN_STATE_CHECKED_OUT

        # Fire time at linger target → awaiting for event 1
        linger_target = sensor._transition_target_time
        assert linger_target is not None
        async_fire_time_changed(hass, linger_target)
        await hass.async_block_till_done()

        assert sensor._state == CHECKIN_STATE_AWAITING
        assert sensor._tracked_event_summary == "Reserved - Bob"

    @freeze_time("2025-06-15T08:00:00+00:00")
    async def test_same_day_turnover_with_async_fire_time_changed(
        self,
        hass: HomeAssistant,
    ) -> None:
        """FR-006a using async_fire_time_changed for realistic timer behavior."""
        coordinator = _make_coordinator(hass)
        config_entry = _make_config_entry()
        sensor = _create_sensor(hass, coordinator, config_entry)

        event0_start = datetime(2025, 6, 10, 16, 0, 0, tzinfo=dt_util.UTC)
        event0_end = datetime(2025, 6, 15, 11, 0, 0, tzinfo=dt_util.UTC)
        event1_start = datetime(2025, 6, 15, 16, 0, 0, tzinfo=dt_util.UTC)
        event1_end = datetime(2025, 6, 20, 11, 0, 0, tzinfo=dt_util.UTC)

        event0 = _make_event("Reserved - Alice", event0_start, event0_end)
        event1 = _make_event("Reserved - Bob", event1_start, event1_end)

        # Start checked_in for event 0
        sensor._state = CHECKIN_STATE_CHECKED_IN
        sensor._tracked_event_summary = "Reserved - Alice"
        sensor._tracked_event_start = event0_start
        sensor._tracked_event_end = event0_end
        sensor._checkin_source = "automatic"

        coordinator.data = [event0, event1]

        # Schedule auto-checkout at event0_end
        sensor._cancel_timer()
        sensor._schedule_auto_checkout(event0_end)

        # Fire time at event0_end → auto checkout
        async_fire_time_changed(hass, event0_end)
        await hass.async_block_till_done()

        assert sensor._state == CHECKIN_STATE_CHECKED_OUT

        # Fire time at the linger target → awaiting for event 1
        linger_target = sensor._transition_target_time
        assert linger_target is not None
        async_fire_time_changed(hass, linger_target)
        await hass.async_block_till_done()

        assert sensor._state == CHECKIN_STATE_AWAITING
        assert sensor._tracked_event_summary == "Reserved - Bob"

    @freeze_time("2025-06-15T08:00:00+00:00")
    async def test_same_day_turnover_ha_events_fired(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Verify HA event bus events fire during same-day turnover lifecycle."""
        coordinator = _make_coordinator(hass)
        config_entry = _make_config_entry()
        sensor = _create_sensor(hass, coordinator, config_entry)

        event0_start = datetime(2025, 6, 10, 16, 0, 0, tzinfo=dt_util.UTC)
        event0_end = datetime(2025, 6, 15, 11, 0, 0, tzinfo=dt_util.UTC)
        event1_start = datetime(2025, 6, 15, 16, 0, 0, tzinfo=dt_util.UTC)
        event1_end = datetime(2025, 6, 20, 11, 0, 0, tzinfo=dt_util.UTC)

        event0 = _make_event("Reserved - Alice", event0_start, event0_end)
        event1 = _make_event("Reserved - Bob", event1_start, event1_end)

        coordinator.data = [event0, event1]

        checkin_events: list[dict] = []
        checkout_events: list[dict] = []
        hass.bus.async_listen(
            EVENT_RENTAL_CONTROL_CHECKIN,
            lambda e: checkin_events.append(e.data),
        )
        hass.bus.async_listen(
            EVENT_RENTAL_CONTROL_CHECKOUT,
            lambda e: checkout_events.append(e.data),
        )

        # no_reservation → awaiting → checked_in (event0 start in past)
        sensor._handle_coordinator_update()
        await hass.async_block_till_done()

        assert sensor._state == CHECKIN_STATE_CHECKED_IN
        assert len(checkin_events) == 1
        assert checkin_events[0]["summary"] == "Reserved - Alice"
        assert checkin_events[0]["source"] == "automatic"

        # Auto checkout at event0_end
        async_fire_time_changed(hass, event0_end)
        await hass.async_block_till_done()

        assert sensor._state == CHECKIN_STATE_CHECKED_OUT
        assert len(checkout_events) == 1
        assert checkout_events[0]["summary"] == "Reserved - Alice"
        assert checkout_events[0]["source"] == "automatic"

    @freeze_time("2025-06-15T11:00:00+00:00")
    async def test_same_day_half_gap_calculation(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Verify half-gap: checkout at 11:00, next at 16:00 → gap=5h → 13:30."""
        coordinator = _make_coordinator(hass)
        config_entry = _make_config_entry()
        sensor = _create_sensor(hass, coordinator, config_entry)

        event0_start = datetime(2025, 6, 10, 16, 0, 0, tzinfo=dt_util.UTC)
        event0_end = datetime(2025, 6, 15, 11, 0, 0, tzinfo=dt_util.UTC)
        event1_start = datetime(2025, 6, 15, 16, 0, 0, tzinfo=dt_util.UTC)
        event1_end = datetime(2025, 6, 20, 11, 0, 0, tzinfo=dt_util.UTC)

        event0 = _make_event("Reserved - Alice", event0_start, event0_end)
        event1 = _make_event("Reserved - Bob", event1_start, event1_end)

        sensor._state = CHECKIN_STATE_CHECKED_IN
        sensor._tracked_event_summary = "Reserved - Alice"
        sensor._tracked_event_start = event0_start
        sensor._tracked_event_end = event0_end

        coordinator.data = [event0, event1]

        sensor._transition_to_checked_out(source="automatic")

        assert sensor._state == CHECKIN_STATE_CHECKED_OUT
        assert sensor._checkout_time is not None
        assert sensor._transition_target_time is not None

        # checkout at ~11:00, next at 16:00 → gap=5h → half=2.5h → ~13:30
        expected_half_gap = datetime(2025, 6, 15, 13, 30, 0, tzinfo=dt_util.UTC)
        delta = abs(
            (sensor._transition_target_time - expected_half_gap).total_seconds()
        )
        assert delta < 5


# ===========================================================================
# T037: Different-day (FR-006c) and no-follow-on (FR-006b) integration tests
# ===========================================================================


class TestDifferentDayFollowOnFR006c:
    """Integration tests for different-day follow-on (FR-006c).

    Full lifecycle: checkout → linger until midnight → no_reservation →
    awaiting_checkin at 00:00 on next event's start day.
    """

    @freeze_time("2025-06-15T11:00:00+00:00")
    async def test_different_day_checkout_to_midnight_to_no_reservation(
        self,
        hass: HomeAssistant,
    ) -> None:
        """FR-006c: checkout → midnight boundary → no_reservation.

        Event 0 ends at 11:00 on June 15, event 1 starts on June 18.
        Sensor lingers until midnight (local timezone) following
        checkout day, then transitions to no_reservation.
        """
        coordinator = _make_coordinator(hass)
        config_entry = _make_config_entry()
        sensor = _create_sensor(hass, coordinator, config_entry)

        event0_start = datetime(2025, 6, 10, 16, 0, 0, tzinfo=dt_util.UTC)
        event0_end = datetime(2025, 6, 15, 11, 0, 0, tzinfo=dt_util.UTC)
        event1_start = datetime(2025, 6, 18, 16, 0, 0, tzinfo=dt_util.UTC)
        event1_end = datetime(2025, 6, 23, 11, 0, 0, tzinfo=dt_util.UTC)

        event0 = _make_event("Reserved - Carol", event0_start, event0_end)
        event1 = _make_event("Reserved - Dave", event1_start, event1_end)

        sensor._state = CHECKIN_STATE_CHECKED_IN
        sensor._tracked_event_summary = "Reserved - Carol"
        sensor._tracked_event_start = event0_start
        sensor._tracked_event_end = event0_end
        sensor._checkin_source = "automatic"

        coordinator.data = [event0, event1]

        # Checkout at event0_end
        sensor._transition_to_checked_out(source="automatic")

        assert sensor._state == CHECKIN_STATE_CHECKED_OUT

        # Compute expected midnight using same logic as sensor
        assert sensor._checkout_time is not None
        expected_midnight = dt_util.start_of_local_day(
            sensor._checkout_time + timedelta(days=1)
        )
        assert sensor._transition_target_time is not None
        delta = abs(
            (sensor._transition_target_time - expected_midnight).total_seconds()
        )
        assert delta < 2

        # Fire time at midnight boundary → transition to no_reservation
        midnight = sensor._transition_target_time
        async_fire_time_changed(hass, midnight)
        await hass.async_block_till_done()

        assert sensor._state == CHECKIN_STATE_NO_RESERVATION

    @freeze_time("2025-06-15T11:00:00+00:00")
    async def test_different_day_full_lifecycle_to_awaiting(
        self,
        hass: HomeAssistant,
    ) -> None:
        """FR-006c full lifecycle: checkout → midnight → no_reservation → awaiting.

        After midnight boundary, coordinator update with event 1 causes
        transition to awaiting_checkin.
        """
        coordinator = _make_coordinator(hass)
        config_entry = _make_config_entry()
        sensor = _create_sensor(hass, coordinator, config_entry)

        event0_start = datetime(2025, 6, 10, 16, 0, 0, tzinfo=dt_util.UTC)
        event0_end = datetime(2025, 6, 15, 11, 0, 0, tzinfo=dt_util.UTC)
        event1_start = datetime(2025, 6, 18, 16, 0, 0, tzinfo=dt_util.UTC)
        event1_end = datetime(2025, 6, 23, 11, 0, 0, tzinfo=dt_util.UTC)

        event0 = _make_event("Reserved - Carol", event0_start, event0_end)
        event1 = _make_event("Reserved - Dave", event1_start, event1_end)

        sensor._state = CHECKIN_STATE_CHECKED_IN
        sensor._tracked_event_summary = "Reserved - Carol"
        sensor._tracked_event_start = event0_start
        sensor._tracked_event_end = event0_end

        coordinator.data = [event0, event1]

        # Checkout
        sensor._transition_to_checked_out(source="automatic")
        assert sensor._state == CHECKIN_STATE_CHECKED_OUT

        # Fire midnight boundary timer
        midnight = sensor._transition_target_time
        assert midnight is not None
        async_fire_time_changed(hass, midnight)
        await hass.async_block_till_done()

        assert sensor._state == CHECKIN_STATE_NO_RESERVATION

        # After midnight, coordinator removes event 0 (past) and
        # event 1 becomes the relevant event. Coordinator update
        # triggers no_reservation → awaiting_checkin for event 1.
        coordinator.data = [event1]
        sensor._handle_coordinator_update()

        assert sensor._state == CHECKIN_STATE_AWAITING
        assert sensor._tracked_event_summary == "Reserved - Dave"

    @freeze_time("2025-06-15T11:00:00+00:00")
    async def test_different_day_midnight_to_awaiting_via_scheduled_timer(
        self,
        hass: HomeAssistant,
    ) -> None:
        """FR-006c with T039 scheduled follow-up timer.

        After midnight boundary fires → no_reservation, T039 should
        schedule a follow-up timer at 00:00 (local) on event 1's
        start day to auto-transition to awaiting_checkin.
        """
        coordinator = _make_coordinator(hass)
        config_entry = _make_config_entry()
        sensor = _create_sensor(hass, coordinator, config_entry)

        event0_start = datetime(2025, 6, 10, 16, 0, 0, tzinfo=dt_util.UTC)
        event0_end = datetime(2025, 6, 15, 11, 0, 0, tzinfo=dt_util.UTC)
        event1_start = datetime(2025, 6, 18, 16, 0, 0, tzinfo=dt_util.UTC)
        event1_end = datetime(2025, 6, 23, 11, 0, 0, tzinfo=dt_util.UTC)

        event0 = _make_event("Reserved - Carol", event0_start, event0_end)
        event1 = _make_event("Reserved - Dave", event1_start, event1_end)

        sensor._state = CHECKIN_STATE_CHECKED_IN
        sensor._tracked_event_summary = "Reserved - Carol"
        sensor._tracked_event_start = event0_start
        sensor._tracked_event_end = event0_end

        coordinator.data = [event0, event1]

        # Checkout
        sensor._transition_to_checked_out(source="automatic")

        # Fire midnight boundary
        midnight = sensor._transition_target_time
        assert midnight is not None
        async_fire_time_changed(hass, midnight)
        await hass.async_block_till_done()

        assert sensor._state == CHECKIN_STATE_NO_RESERVATION

        # T039: Follow-up timer should be scheduled at 00:00 (local)
        # on event 1's start day. Use start_of_local_day for expected
        # value since the sensor uses the same calculation.
        event1_start_day_local = dt_util.start_of_local_day(event1_start)

        # T039 must be implemented: a follow-up timer is required
        assert sensor._transition_target_time is not None, (
            "Expected follow-up timer for T039 to be scheduled, "
            "but _transition_target_time is None"
        )
        delta = abs(
            (sensor._transition_target_time - event1_start_day_local).total_seconds()
        )
        assert delta < 2, (
            f"Expected follow-up at {event1_start_day_local}, "
            f"got {sensor._transition_target_time}"
        )

        # Make event 1 available for the callback
        coordinator.data = [event1]

        # Fire the follow-up timer
        async_fire_time_changed(hass, sensor._transition_target_time)
        await hass.async_block_till_done()

        assert sensor._state == CHECKIN_STATE_AWAITING
        assert sensor._tracked_event_summary == "Reserved - Dave"

    @freeze_time("2025-06-15T11:00:00+00:00")
    async def test_different_day_coordinator_update_during_linger(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Coordinator updates during FR-006c linger don't cause issues.

        While lingering in checked_out waiting for midnight, coordinator
        updates should NOT cause premature transition.
        """
        coordinator = _make_coordinator(hass)
        config_entry = _make_config_entry()
        sensor = _create_sensor(hass, coordinator, config_entry)

        event0_start = datetime(2025, 6, 10, 16, 0, 0, tzinfo=dt_util.UTC)
        event0_end = datetime(2025, 6, 15, 11, 0, 0, tzinfo=dt_util.UTC)
        event1_start = datetime(2025, 6, 18, 16, 0, 0, tzinfo=dt_util.UTC)
        event1_end = datetime(2025, 6, 23, 11, 0, 0, tzinfo=dt_util.UTC)

        event0 = _make_event("Reserved - Carol", event0_start, event0_end)
        event1 = _make_event("Reserved - Dave", event1_start, event1_end)

        sensor._state = CHECKIN_STATE_CHECKED_OUT
        sensor._tracked_event_summary = "Reserved - Carol"
        sensor._tracked_event_start = event0_start
        sensor._tracked_event_end = event0_end
        sensor._checkout_time = event0_end
        sensor._checked_out_event_key = sensor._event_key(
            "Reserved - Carol", event0_start
        )

        coordinator.data = [event0, event1]

        # Coordinator update — event0 is data[0] with matching key
        sensor._handle_coordinator_update()
        assert sensor._state == CHECKIN_STATE_CHECKED_OUT

        # Another update: event0 removed, event1 is data[0]
        coordinator.data = [event1]
        sensor._handle_coordinator_update()

        # Event1 key != checked_out key → recomputes linger
        # Should stay checked_out (different-day)
        assert sensor._state == CHECKIN_STATE_CHECKED_OUT


class TestNoFollowOnFR006b:
    """Integration tests for no-follow-on reservation (FR-006b).

    Full lifecycle: checkout → cleaning window linger → no_reservation.
    """

    @freeze_time("2025-06-15T11:00:00+00:00")
    async def test_no_followon_checkout_to_no_reservation(
        self,
        hass: HomeAssistant,
    ) -> None:
        """FR-006b: checkout → cleaning window (6h) → no_reservation."""
        coordinator = _make_coordinator(hass)
        config_entry = _make_config_entry()
        sensor = _create_sensor(hass, coordinator, config_entry)

        event0_start = datetime(2025, 6, 10, 16, 0, 0, tzinfo=dt_util.UTC)
        event0_end = datetime(2025, 6, 15, 11, 0, 0, tzinfo=dt_util.UTC)

        event0 = _make_event("Reserved - Erin", event0_start, event0_end)

        sensor._state = CHECKIN_STATE_CHECKED_IN
        sensor._tracked_event_summary = "Reserved - Erin"
        sensor._tracked_event_start = event0_start
        sensor._tracked_event_end = event0_end
        sensor._checkin_source = "automatic"

        coordinator.data = [event0]

        sensor._transition_to_checked_out(source="automatic")

        assert sensor._state == CHECKIN_STATE_CHECKED_OUT
        assert sensor._checkout_time is not None

        # Verify cleaning window linger (6 hours)
        expected_linger_end = sensor._checkout_time + timedelta(
            hours=DEFAULT_CLEANING_WINDOW
        )
        assert sensor._transition_target_time is not None
        delta = abs(
            (sensor._transition_target_time - expected_linger_end).total_seconds()
        )
        assert delta < 2

        # Fire time at cleaning window end
        linger_end = sensor._transition_target_time
        async_fire_time_changed(hass, linger_end)
        await hass.async_block_till_done()

        assert sensor._state == CHECKIN_STATE_NO_RESERVATION
        assert sensor._tracked_event_summary is None

    @freeze_time("2025-06-15T11:00:00+00:00")
    async def test_no_followon_custom_cleaning_window(
        self,
        hass: HomeAssistant,
    ) -> None:
        """FR-006b with a custom cleaning window (2 hours)."""
        coordinator = _make_coordinator(hass)
        config_entry = MockConfigEntry(
            domain=DOMAIN,
            title="Test Rental",
            version=7,
            unique_id="test-custom-cw-id",
            data={
                "name": "Test Rental",
                "url": "https://example.com/calendar.ics",
                "timezone": "UTC",
                "checkin": "16:00",
                "checkout": "11:00",
                "start_slot": 10,
                "max_events": 3,
                "days": 90,
                "verify_ssl": True,
                "ignore_non_reserved": False,
                CONF_CLEANING_WINDOW: 2.0,
            },
            options={
                "refresh_frequency": 5,
            },
            entry_id="test_custom_cw_entry_id",
        )
        coordinator.unique_id = "test-custom-cw-id"
        sensor = _create_sensor(hass, coordinator, config_entry)

        event0_end = datetime(2025, 6, 15, 11, 0, 0, tzinfo=dt_util.UTC)

        sensor._state = CHECKIN_STATE_CHECKED_IN
        sensor._tracked_event_summary = "Reserved - Frank"
        sensor._tracked_event_start = datetime(
            2025, 6, 10, 16, 0, 0, tzinfo=dt_util.UTC
        )
        sensor._tracked_event_end = event0_end

        coordinator.data = [
            _make_event(
                "Reserved - Frank",
                datetime(2025, 6, 10, 16, 0, 0, tzinfo=dt_util.UTC),
                event0_end,
            )
        ]

        sensor._transition_to_checked_out(source="automatic")

        assert sensor._checkout_time is not None
        expected_linger_end = sensor._checkout_time + timedelta(hours=2.0)
        assert sensor._transition_target_time is not None
        delta = abs(
            (sensor._transition_target_time - expected_linger_end).total_seconds()
        )
        assert delta < 2

    @freeze_time("2025-06-15T11:00:00+00:00")
    async def test_no_followon_stays_checked_out_before_linger_expiry(
        self,
        hass: HomeAssistant,
    ) -> None:
        """FR-006b: sensor stays checked_out before cleaning window ends."""
        coordinator = _make_coordinator(hass)
        config_entry = _make_config_entry()
        sensor = _create_sensor(hass, coordinator, config_entry)

        event0_end = datetime(2025, 6, 15, 11, 0, 0, tzinfo=dt_util.UTC)
        event0 = _make_event(
            "Reserved - Erin",
            datetime(2025, 6, 10, 16, 0, 0, tzinfo=dt_util.UTC),
            event0_end,
        )

        sensor._state = CHECKIN_STATE_CHECKED_IN
        sensor._tracked_event_summary = "Reserved - Erin"
        sensor._tracked_event_start = datetime(
            2025, 6, 10, 16, 0, 0, tzinfo=dt_util.UTC
        )
        sensor._tracked_event_end = event0_end

        coordinator.data = [event0]

        sensor._transition_to_checked_out(source="automatic")

        assert sensor._state == CHECKIN_STATE_CHECKED_OUT

        # Coordinator update with no events — should NOT change state
        coordinator.data = []
        sensor._handle_coordinator_update()
        assert sensor._state == CHECKIN_STATE_CHECKED_OUT

    @freeze_time("2025-06-15T11:00:00+00:00")
    async def test_no_followon_checkout_event_fired(
        self,
        hass: HomeAssistant,
    ) -> None:
        """FR-006b: verify checkout event is fired on transition."""
        coordinator = _make_coordinator(hass)
        config_entry = _make_config_entry()
        sensor = _create_sensor(hass, coordinator, config_entry)

        checkout_events: list[dict] = []
        hass.bus.async_listen(
            EVENT_RENTAL_CONTROL_CHECKOUT,
            lambda e: checkout_events.append(e.data),
        )

        event0_end = datetime(2025, 6, 15, 11, 0, 0, tzinfo=dt_util.UTC)
        sensor._state = CHECKIN_STATE_CHECKED_IN
        sensor._tracked_event_summary = "Reserved - Erin"
        sensor._tracked_event_start = datetime(
            2025, 6, 10, 16, 0, 0, tzinfo=dt_util.UTC
        )
        sensor._tracked_event_end = event0_end

        coordinator.data = [
            _make_event(
                "Reserved - Erin",
                datetime(2025, 6, 10, 16, 0, 0, tzinfo=dt_util.UTC),
                event0_end,
            )
        ]

        sensor._transition_to_checked_out(source="automatic")
        await hass.async_block_till_done()

        assert len(checkout_events) == 1
        assert checkout_events[0]["summary"] == "Reserved - Erin"
        assert checkout_events[0]["source"] == "automatic"


# ===========================================================================
# Edge-case tests for turnover scenarios
# ===========================================================================


class TestTurnoverEdgeCases:
    """Edge case tests for turnover handling across all FR-006 scenarios."""

    @freeze_time("2025-06-15T11:00:00+00:00")
    async def test_checked_out_event_removed_from_coordinator(
        self,
        hass: HomeAssistant,
    ) -> None:
        """When checked-out event is removed, follow-on is still found."""
        coordinator = _make_coordinator(hass)
        config_entry = _make_config_entry()
        sensor = _create_sensor(hass, coordinator, config_entry)

        event0_start = datetime(2025, 6, 10, 16, 0, 0, tzinfo=dt_util.UTC)
        event0_end = datetime(2025, 6, 15, 11, 0, 0, tzinfo=dt_util.UTC)
        event1_start = datetime(2025, 6, 15, 16, 0, 0, tzinfo=dt_util.UTC)
        event1_end = datetime(2025, 6, 20, 11, 0, 0, tzinfo=dt_util.UTC)

        event1 = _make_event("Reserved - Bob", event1_start, event1_end)

        sensor._state = CHECKIN_STATE_CHECKED_OUT
        sensor._tracked_event_summary = "Reserved - Alice"
        sensor._tracked_event_start = event0_start
        sensor._tracked_event_end = event0_end
        sensor._checkout_time = event0_end
        sensor._checked_out_event_key = sensor._event_key(
            "Reserved - Alice", event0_start
        )

        # Coordinator removed event 0; event 1 is data[0]
        coordinator.data = [event1]

        # event1 key != checked_out key → recompute linger
        sensor._handle_coordinator_update()
        assert sensor._state == CHECKIN_STATE_CHECKED_OUT

        # Recomputed linger should be for same-day turnover (FR-006a)
        assert sensor._transition_target_time is not None

    @freeze_time("2025-06-15T11:00:00+00:00")
    async def test_find_followon_event_skips_checked_out_event(
        self,
        hass: HomeAssistant,
    ) -> None:
        """_find_followon_event skips the checked-out event by key."""
        coordinator = _make_coordinator(hass)
        config_entry = _make_config_entry()
        sensor = _create_sensor(hass, coordinator, config_entry)

        event0_start = datetime(2025, 6, 10, 16, 0, 0, tzinfo=dt_util.UTC)
        event0_end = datetime(2025, 6, 15, 11, 0, 0, tzinfo=dt_util.UTC)
        event1_start = datetime(2025, 6, 15, 16, 0, 0, tzinfo=dt_util.UTC)
        event1_end = datetime(2025, 6, 20, 11, 0, 0, tzinfo=dt_util.UTC)

        event0 = _make_event("Reserved - Alice", event0_start, event0_end)
        event1 = _make_event("Reserved - Bob", event1_start, event1_end)

        sensor._checked_out_event_key = sensor._event_key(
            "Reserved - Alice", event0_start
        )
        coordinator.data = [event0, event1]

        result = sensor._find_followon_event(event0_end)
        assert result is not None
        assert result.summary == "Reserved - Bob"

    @freeze_time("2025-06-15T13:30:00+00:00")
    async def test_linger_to_awaiting_skips_checked_out_event_at_data_0(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Linger callback skips checked-out event when it's data[0]."""
        coordinator = _make_coordinator(hass)
        config_entry = _make_config_entry()
        sensor = _create_sensor(hass, coordinator, config_entry)

        event0_start = datetime(2025, 6, 10, 16, 0, 0, tzinfo=dt_util.UTC)
        event0_end = datetime(2025, 6, 15, 11, 0, 0, tzinfo=dt_util.UTC)
        event1_start = datetime(2025, 6, 15, 16, 0, 0, tzinfo=dt_util.UTC)
        event1_end = datetime(2025, 6, 20, 11, 0, 0, tzinfo=dt_util.UTC)

        event0 = _make_event("Reserved - Alice", event0_start, event0_end)
        event1 = _make_event("Reserved - Bob", event1_start, event1_end)

        sensor._state = CHECKIN_STATE_CHECKED_OUT
        sensor._checked_out_event_key = sensor._event_key(
            "Reserved - Alice", event0_start
        )

        # Event 0 still in coordinator as data[0]
        coordinator.data = [event0, event1]

        linger_time = datetime(2025, 6, 15, 13, 30, 0, tzinfo=dt_util.UTC)
        sensor._async_linger_to_awaiting_callback(linger_time)

        assert sensor._state == CHECKIN_STATE_AWAITING
        assert sensor._tracked_event_summary == "Reserved - Bob"


# ===========================================================================
# T043: Full lifecycle integration tests
# ===========================================================================


class TestFullLifecycleT043:
    """Full lifecycle integration tests.

    Tests the complete flow from setup through all state transitions
    and back to no_reservation, verifying HA events and sensor attributes
    at each stage.
    """

    @freeze_time("2025-07-01T12:00:00+00:00")
    async def test_full_lifecycle_automatic_no_keymaster(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test full automatic lifecycle without keymaster.

        Flow: no_reservation → awaiting_checkin (coordinator update with
        future event) → checked_in (auto at event start) → checked_out
        (auto at event end) → no_reservation (linger expiry).

        Verifies all HA events fired with correct payloads and all sensor
        attributes at each state.
        """
        coordinator = _make_coordinator(hass)
        config_entry = _make_config_entry()
        sensor = _create_sensor(hass, coordinator, config_entry)

        event_start = datetime(2025, 7, 1, 16, 0, 0, tzinfo=dt_util.UTC)
        event_end = datetime(2025, 7, 5, 11, 0, 0, tzinfo=dt_util.UTC)
        event = _make_event("Reserved - Guest1", event_start, event_end)

        checkin_events: list[dict] = []
        checkout_events: list[dict] = []
        hass.bus.async_listen(
            EVENT_RENTAL_CONTROL_CHECKIN,
            lambda e: checkin_events.append(e.data),
        )
        hass.bus.async_listen(
            EVENT_RENTAL_CONTROL_CHECKOUT,
            lambda e: checkout_events.append(e.data),
        )

        # ---- Step 1: no_reservation (initial state) ----
        assert sensor._state == CHECKIN_STATE_NO_RESERVATION
        assert sensor._tracked_event_summary is None
        attrs = sensor.extra_state_attributes
        assert attrs["checkin_state"] == CHECKIN_STATE_NO_RESERVATION
        assert attrs["summary"] is None
        assert attrs["start"] is None
        assert attrs["end"] is None
        assert attrs["guest_name"] is None
        assert attrs["checkin_source"] is None
        assert attrs["checkout_source"] is None
        assert attrs["checkout_time"] is None
        assert attrs["next_transition"] is None

        # ---- Step 2: coordinator update → awaiting_checkin ----
        coordinator.data = [event]
        sensor._handle_coordinator_update()

        assert sensor._state == CHECKIN_STATE_AWAITING
        assert sensor._tracked_event_summary == "Reserved - Guest1"
        assert sensor._tracked_event_start == event_start
        assert sensor._tracked_event_end == event_end
        assert sensor._transition_target_time == event_start
        assert sensor._unsub_timer is not None
        assert sensor._checkin_source is None
        assert sensor._checkout_source is None

        attrs = sensor.extra_state_attributes
        assert attrs["checkin_state"] == CHECKIN_STATE_AWAITING
        assert attrs["summary"] == "Reserved - Guest1"
        assert attrs["start"] == event_start
        assert attrs["end"] == event_end
        assert attrs["checkin_source"] is None
        assert attrs["checkout_source"] is None
        assert attrs["checkout_time"] is None
        assert attrs["next_transition"] == event_start

        # No events fired yet
        assert len(checkin_events) == 0
        assert len(checkout_events) == 0

        # ---- Step 3: auto check-in at event start ----
        async_fire_time_changed(hass, event_start)
        await hass.async_block_till_done()

        assert sensor._state == CHECKIN_STATE_CHECKED_IN
        assert sensor._checkin_source == "automatic"
        assert sensor._tracked_event_summary == "Reserved - Guest1"
        assert sensor._transition_target_time == event_end
        assert sensor._unsub_timer is not None

        attrs = sensor.extra_state_attributes
        assert attrs["checkin_state"] == CHECKIN_STATE_CHECKED_IN
        assert attrs["checkin_source"] == "automatic"
        assert attrs["checkout_source"] is None
        assert attrs["checkout_time"] is None

        # Check-in event fired
        assert len(checkin_events) == 1
        assert checkin_events[0]["summary"] == "Reserved - Guest1"
        assert checkin_events[0]["source"] == "automatic"
        assert checkin_events[0]["entity_id"] == "sensor.test_rental_checkin"
        assert checkin_events[0]["start"] == event_start.isoformat()
        assert checkin_events[0]["end"] == event_end.isoformat()

        # ---- Step 4: auto check-out at event end ----
        async_fire_time_changed(hass, event_end)
        await hass.async_block_till_done()

        assert sensor._state == CHECKIN_STATE_CHECKED_OUT
        assert sensor._checkout_source == "automatic"
        assert sensor._checkout_time is not None

        attrs = sensor.extra_state_attributes
        assert attrs["checkin_state"] == CHECKIN_STATE_CHECKED_OUT
        assert attrs["checkout_source"] == "automatic"
        assert attrs["checkout_time"] is not None

        # Checkout event fired
        assert len(checkout_events) == 1
        assert checkout_events[0]["summary"] == "Reserved - Guest1"
        assert checkout_events[0]["source"] == "automatic"
        assert checkout_events[0]["entity_id"] == "sensor.test_rental_checkin"

        # ---- Step 5: linger expiry → no_reservation ----
        # No follow-on event → FR-006b cleaning window
        linger_target = sensor._transition_target_time
        assert linger_target is not None

        async_fire_time_changed(hass, linger_target)
        await hass.async_block_till_done()

        assert sensor._state == CHECKIN_STATE_NO_RESERVATION
        assert sensor._tracked_event_summary is None
        assert sensor._tracked_event_start is None
        assert sensor._tracked_event_end is None
        assert sensor._checkout_time is None

        attrs = sensor.extra_state_attributes
        assert attrs["checkin_state"] == CHECKIN_STATE_NO_RESERVATION
        assert attrs["summary"] is None
        assert attrs["checkin_source"] is None
        assert attrs["checkout_source"] is None

    @freeze_time("2025-07-01T12:00:00+00:00")
    async def test_full_lifecycle_with_keymaster_checkin(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test lifecycle with keymaster-triggered check-in.

        Flow: no_reservation → awaiting_checkin → checked_in
        (keymaster unlock) → checked_out (auto) → no_reservation
        (linger expiry).
        """
        coordinator = _make_coordinator(hass)
        coordinator.lockname = "front_door"
        config_entry = _make_config_entry()
        sensor = _create_sensor(hass, coordinator, config_entry)

        event_start = datetime(2025, 7, 1, 16, 0, 0, tzinfo=dt_util.UTC)
        event_end = datetime(2025, 7, 5, 11, 0, 0, tzinfo=dt_util.UTC)
        event = _make_event("Reserved - Guest2", event_start, event_end)

        checkin_events: list[dict] = []
        checkout_events: list[dict] = []
        hass.bus.async_listen(
            EVENT_RENTAL_CONTROL_CHECKIN,
            lambda e: checkin_events.append(e.data),
        )
        hass.bus.async_listen(
            EVENT_RENTAL_CONTROL_CHECKOUT,
            lambda e: checkout_events.append(e.data),
        )

        # ---- Step 1: coordinator update → awaiting_checkin ----
        coordinator.data = [event]
        sensor._handle_coordinator_update()
        assert sensor._state == CHECKIN_STATE_AWAITING
        assert sensor._tracked_event_summary == "Reserved - Guest2"

        # ---- Step 2: keymaster unlock → checked_in ----
        # Simulate keymaster unlock at slot 10 (start_slot)
        sensor.async_handle_keymaster_unlock(code_slot_num=10)
        await hass.async_block_till_done()

        assert sensor._state == CHECKIN_STATE_CHECKED_IN
        assert sensor._checkin_source == "keymaster"

        # Check-in event fired with keymaster source
        assert len(checkin_events) == 1
        assert checkin_events[0]["source"] == "keymaster"
        assert checkin_events[0]["summary"] == "Reserved - Guest2"

        # ---- Step 3: auto check-out at event end ----
        async_fire_time_changed(hass, event_end)
        await hass.async_block_till_done()

        assert sensor._state == CHECKIN_STATE_CHECKED_OUT
        assert sensor._checkout_source == "automatic"
        assert len(checkout_events) == 1
        assert checkout_events[0]["source"] == "automatic"

        # ---- Step 4: linger expiry → no_reservation ----
        linger_target = sensor._transition_target_time
        assert linger_target is not None
        async_fire_time_changed(hass, linger_target)
        await hass.async_block_till_done()

        assert sensor._state == CHECKIN_STATE_NO_RESERVATION

    @freeze_time("2025-07-01T08:00:00+00:00")
    async def test_full_lifecycle_event_already_started(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test lifecycle when event start is in the past.

        When a coordinator update brings an event whose start time has
        already passed, the sensor should skip awaiting_checkin and go
        directly to checked_in.
        """
        coordinator = _make_coordinator(hass)
        config_entry = _make_config_entry()
        sensor = _create_sensor(hass, coordinator, config_entry)

        # Event started yesterday
        event_start = datetime(2025, 6, 30, 16, 0, 0, tzinfo=dt_util.UTC)
        event_end = datetime(2025, 7, 5, 11, 0, 0, tzinfo=dt_util.UTC)
        event = _make_event("Reserved - EarlyGuest", event_start, event_end)

        checkin_events: list[dict] = []
        hass.bus.async_listen(
            EVENT_RENTAL_CONTROL_CHECKIN,
            lambda e: checkin_events.append(e.data),
        )

        coordinator.data = [event]
        sensor._handle_coordinator_update()
        await hass.async_block_till_done()

        # Should have gone straight to checked_in
        assert sensor._state == CHECKIN_STATE_CHECKED_IN
        assert sensor._checkin_source == "automatic"
        assert len(checkin_events) == 1
        assert checkin_events[0]["source"] == "automatic"

        # Verify auto-checkout is scheduled
        assert sensor._transition_target_time == event_end
        assert sensor._unsub_timer is not None

    @freeze_time("2025-07-01T12:00:00+00:00")
    async def test_keymaster_unlock_ignored_in_wrong_state(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Keymaster unlock is ignored when sensor is not in awaiting_checkin."""
        coordinator = _make_coordinator(hass)
        coordinator.lockname = "front_door"
        config_entry = _make_config_entry()
        sensor = _create_sensor(hass, coordinator, config_entry)

        # Sensor is in no_reservation — unlock should be ignored
        assert sensor._state == CHECKIN_STATE_NO_RESERVATION
        sensor.async_handle_keymaster_unlock(code_slot_num=10)
        assert sensor._state == CHECKIN_STATE_NO_RESERVATION

    @freeze_time("2025-07-01T12:00:00+00:00")
    async def test_keymaster_unlock_ignored_for_manual_rf(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Keymaster unlock with slot 0 (manual/RF) is ignored."""
        coordinator = _make_coordinator(hass)
        coordinator.lockname = "front_door"
        config_entry = _make_config_entry()
        sensor = _create_sensor(hass, coordinator, config_entry)

        event_start = datetime(2025, 7, 1, 16, 0, 0, tzinfo=dt_util.UTC)
        event_end = datetime(2025, 7, 5, 11, 0, 0, tzinfo=dt_util.UTC)
        event = _make_event("Reserved - Guest3", event_start, event_end)

        coordinator.data = [event]
        sensor._handle_coordinator_update()
        assert sensor._state == CHECKIN_STATE_AWAITING

        # Manual/RF unlock (slot 0) should be ignored
        sensor.async_handle_keymaster_unlock(code_slot_num=0)
        assert sensor._state == CHECKIN_STATE_AWAITING

    @freeze_time("2025-07-01T12:00:00+00:00")
    async def test_keymaster_unlock_out_of_slot_range(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Keymaster unlock with slot outside managed range is ignored."""
        coordinator = _make_coordinator(hass)
        coordinator.lockname = "front_door"
        config_entry = _make_config_entry()
        sensor = _create_sensor(hass, coordinator, config_entry)

        event_start = datetime(2025, 7, 1, 16, 0, 0, tzinfo=dt_util.UTC)
        event_end = datetime(2025, 7, 5, 11, 0, 0, tzinfo=dt_util.UTC)
        event = _make_event("Reserved - Guest4", event_start, event_end)

        coordinator.data = [event]
        sensor._handle_coordinator_update()
        assert sensor._state == CHECKIN_STATE_AWAITING

        # Slot 99 is outside managed range [10, 13)
        sensor.async_handle_keymaster_unlock(code_slot_num=99)
        assert sensor._state == CHECKIN_STATE_AWAITING

    @freeze_time("2025-07-01T12:00:00+00:00")
    async def test_full_lifecycle_sensor_attributes_complete(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Verify all sensor attributes are correctly populated at each state."""
        coordinator = _make_coordinator(hass)
        config_entry = _make_config_entry()
        sensor = _create_sensor(hass, coordinator, config_entry)

        event_start = datetime(2025, 7, 1, 16, 0, 0, tzinfo=dt_util.UTC)
        event_end = datetime(2025, 7, 5, 11, 0, 0, tzinfo=dt_util.UTC)
        event = _make_event(
            "Reserved - AttrGuest",
            event_start,
            event_end,
            description="Guest: Attr Test",
        )

        coordinator.data = [event]
        sensor._handle_coordinator_update()

        # Verify awaiting attributes
        assert sensor.state == CHECKIN_STATE_AWAITING
        attrs = sensor.extra_state_attributes
        assert attrs["summary"] == "Reserved - AttrGuest"
        assert attrs["start"] == event_start
        assert attrs["end"] == event_end
        assert attrs["next_transition"] == event_start

        # Fire auto check-in
        async_fire_time_changed(hass, event_start)
        await hass.async_block_till_done()

        # Verify checked_in attributes
        assert sensor.state == CHECKIN_STATE_CHECKED_IN
        attrs = sensor.extra_state_attributes
        assert attrs["checkin_source"] == "automatic"
        assert attrs["summary"] == "Reserved - AttrGuest"
        assert attrs["next_transition"] == event_end

        # Fire auto check-out
        async_fire_time_changed(hass, event_end)
        await hass.async_block_till_done()

        # Verify checked_out attributes
        assert sensor.state == CHECKIN_STATE_CHECKED_OUT
        attrs = sensor.extra_state_attributes
        assert attrs["checkout_source"] == "automatic"
        assert attrs["checkout_time"] is not None
        assert attrs["next_transition"] is not None

    @freeze_time("2025-07-03T08:00:00+00:00")
    async def test_full_lifecycle_multiple_events_sequential(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test lifecycle across two sequential events.

        After the first event completes its full lifecycle (including
        linger), a second event should start a new awaiting cycle.

        Frozen at July 3 08:00 so that checkout_time (dt_util.now()) is on
        July 3, the same day as event 1 start → FR-006a same-day turnover.
        """
        coordinator = _make_coordinator(hass)
        config_entry = _make_config_entry()
        sensor = _create_sensor(hass, coordinator, config_entry)

        event0_start = datetime(2025, 7, 1, 16, 0, 0, tzinfo=dt_util.UTC)
        event0_end = datetime(2025, 7, 3, 11, 0, 0, tzinfo=dt_util.UTC)
        event1_start = datetime(2025, 7, 3, 16, 0, 0, tzinfo=dt_util.UTC)
        event1_end = datetime(2025, 7, 7, 11, 0, 0, tzinfo=dt_util.UTC)

        event0 = _make_event("Reserved - First", event0_start, event0_end)
        event1 = _make_event("Reserved - Second", event1_start, event1_end)

        checkin_events: list[dict] = []
        checkout_events: list[dict] = []
        hass.bus.async_listen(
            EVENT_RENTAL_CONTROL_CHECKIN,
            lambda e: checkin_events.append(e.data),
        )
        hass.bus.async_listen(
            EVENT_RENTAL_CONTROL_CHECKOUT,
            lambda e: checkout_events.append(e.data),
        )

        # ---- Event 0 lifecycle ----
        coordinator.data = [event0, event1]
        sensor._handle_coordinator_update()
        # Event0 start is in the past at frozen time (July 3 08:00)
        # so sensor skips awaiting and goes directly to checked_in
        assert sensor._state == CHECKIN_STATE_CHECKED_IN
        assert sensor._tracked_event_summary == "Reserved - First"

        # Auto check-out at event0_end
        async_fire_time_changed(hass, event0_end)
        await hass.async_block_till_done()
        assert sensor._state == CHECKIN_STATE_CHECKED_OUT
        assert len(checkout_events) == 1

        # Same-day turnover linger → awaiting for event 1
        linger_target = sensor._transition_target_time
        assert linger_target is not None
        async_fire_time_changed(hass, linger_target)
        await hass.async_block_till_done()

        assert sensor._state == CHECKIN_STATE_AWAITING
        assert sensor._tracked_event_summary == "Reserved - Second"

        # ---- Event 1 lifecycle ----
        # Auto check-in for event 1
        async_fire_time_changed(hass, event1_start)
        await hass.async_block_till_done()
        assert sensor._state == CHECKIN_STATE_CHECKED_IN
        # 2 checkin events total: event0 auto-checkin + event1 auto-checkin
        assert len(checkin_events) == 2
        assert checkin_events[1]["summary"] == "Reserved - Second"

        # Auto check-out for event 1
        # Remove event0 from coordinator (it would be filtered out by now)
        coordinator.data = [event1]
        async_fire_time_changed(hass, event1_end)
        await hass.async_block_till_done()
        assert sensor._state == CHECKIN_STATE_CHECKED_OUT
        assert len(checkout_events) == 2

        # Final linger → no_reservation (no follow-on)
        final_linger = sensor._transition_target_time
        assert final_linger is not None
        async_fire_time_changed(hass, final_linger)
        await hass.async_block_till_done()
        assert sensor._state == CHECKIN_STATE_NO_RESERVATION
