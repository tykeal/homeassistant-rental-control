# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Integration tests for check-in tracking turnover scenarios.

Tests cover:
- T036: Same-day turnover (FR-006a) full lifecycle with mocked time
- T037: Different-day (FR-006c) and no-follow-on (FR-006b) full lifecycle
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
            },
            options={
                "refresh_frequency": 5,
                CONF_CLEANING_WINDOW: 2.0,
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
