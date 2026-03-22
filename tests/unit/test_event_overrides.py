# SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the Rental Control EventOverrides module."""

from __future__ import annotations

from datetime import datetime
from datetime import time
from datetime import timedelta
import logging
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

from homeassistant.util import dt as dt_util
import pytest

from custom_components.rental_control.event_overrides import EventOverride
from custom_components.rental_control.event_overrides import EventOverrides
from custom_components.rental_control.event_overrides import ReserveResult

# ---------------------------------------------------------------------------
# Helpers / Fixtures
# ---------------------------------------------------------------------------


def _make_dt(
    year: int, month: int, day: int, hour: int = 0, minute: int = 0
) -> datetime:
    """Build a timezone-aware datetime in UTC."""
    return datetime(year, month, day, hour, minute, tzinfo=dt_util.UTC)


def _make_event(end: datetime, summary: str = "") -> MagicMock:
    """Create a mock calendar event with the given end and summary."""
    event = MagicMock()
    event.end = end
    event.summary = summary
    event.description = ""
    return event


def _make_coordinator(
    calendar_events: list[MagicMock] | None = None,
    max_events: int = 3,
    lockname: str = "test_lock",
) -> MagicMock:
    """Build a mock coordinator suitable for async_check_overrides tests."""
    coordinator = MagicMock()
    coordinator.name = "Test Rental"
    coordinator.lockname = lockname
    coordinator.hass = MagicMock()
    coordinator.hass.services = MagicMock()
    coordinator.hass.services.async_call = AsyncMock()
    coordinator.max_events = max_events
    coordinator.event_prefix = ""
    coordinator.data = calendar_events
    return coordinator


@pytest.fixture
def eo() -> EventOverrides:
    """Return an EventOverrides with start_slot=1 and max_slots=3."""
    return EventOverrides(start_slot=1, max_slots=3)


@pytest.fixture
def populated_eo() -> EventOverrides:
    """Return an EventOverrides pre-populated with all 3 empty slots (ready)."""
    eo = EventOverrides(start_slot=1, max_slots=3)
    now = dt_util.now()
    # Populate all slots as None to make the system "ready"
    eo.update(1, "", "", now, now)
    eo.update(2, "", "", now, now)
    eo.update(3, "", "", now, now)
    return eo


# ---------------------------------------------------------------------------
# Initialization & Properties
# ---------------------------------------------------------------------------


class TestEventOverridesInit:
    """Tests for EventOverrides initialization and properties."""

    def test_initial_properties(self, eo: EventOverrides) -> None:
        """Verify initial state after construction."""
        assert eo.max_slots == 3
        assert eo.start_slot == 1
        assert eo.next_slot is None
        assert eo.overrides == {}
        assert eo.ready is False

    def test_custom_start_slot(self) -> None:
        """Verify non-default start_slot is stored correctly."""
        eo = EventOverrides(start_slot=10, max_slots=5)
        assert eo.start_slot == 10
        assert eo.max_slots == 5

    def test_ready_becomes_true_when_all_slots_populated(self) -> None:
        """Verify ready flag is set once overrides dict reaches max_slots size."""
        eo = EventOverrides(start_slot=1, max_slots=2)
        now = dt_util.now()
        eo.update(1, "", "", now, now)
        assert eo.ready is False
        eo.update(2, "", "", now, now)
        assert eo.ready is True

    def test_ready_stays_true_after_clearing_slot(self) -> None:
        """Once ready, clearing a slot doesn't unset ready (dict still has max_slots keys)."""
        eo = EventOverrides(start_slot=1, max_slots=2)
        now = dt_util.now()
        eo.update(1, "code", "Guest A", now, now)
        eo.update(2, "code", "Guest B", now, now)
        assert eo.ready is True
        # Clear slot 1
        eo.update(1, "", "", now, now)
        assert eo.ready is True


# ---------------------------------------------------------------------------
# EventOverride TypedDict
# ---------------------------------------------------------------------------


class TestEventOverrideTypedDict:
    """Tests for EventOverride TypedDict structure."""

    def test_typed_dict_fields(self) -> None:
        """Verify EventOverride has expected keys."""
        now = _make_dt(2025, 6, 1, 14, 0)
        override: EventOverride = {
            "slot_name": "Guest",
            "slot_code": "1234",
            "start_time": now,
            "end_time": now + timedelta(days=3),
        }
        assert override["slot_name"] == "Guest"
        assert override["slot_code"] == "1234"
        assert override["start_time"] == now
        assert override["end_time"] == now + timedelta(days=3)


# ---------------------------------------------------------------------------
# update() method
# ---------------------------------------------------------------------------


class TestUpdate:
    """Tests for EventOverrides.update."""

    def test_update_creates_override_with_name(self, eo: EventOverrides) -> None:
        """Verify update with a non-empty name populates the slot."""
        start = _make_dt(2025, 7, 1, 16, 0)
        end = _make_dt(2025, 7, 5, 11, 0)
        eo.update(1, "5678", "John Doe", start, end)
        assert eo.overrides[1] is not None
        assert eo.overrides[1]["slot_name"] == "John Doe"
        assert eo.overrides[1]["slot_code"] == "5678"
        assert eo.overrides[1]["start_time"] == start
        assert eo.overrides[1]["end_time"] == end

    def test_update_empty_name_clears_slot(self, eo: EventOverrides) -> None:
        """Verify update with empty name sets the slot to None."""
        now = dt_util.now()
        eo.update(1, "code", "Guest", now, now)
        assert eo.overrides[1] is not None
        eo.update(1, "", "", now, now)
        assert eo.overrides[1] is None

    def test_update_strips_prefix(self, eo: EventOverrides) -> None:
        """Verify prefix is stripped from slot_name."""
        now = dt_util.now()
        eo.update(1, "1234", "Rental Guest X", now, now, prefix="Rental")
        assert eo.overrides[1] is not None
        assert eo.overrides[1]["slot_name"] == "Guest X"

    def test_update_no_prefix_keeps_full_name(self, eo: EventOverrides) -> None:
        """Verify name is kept when prefix is None."""
        now = dt_util.now()
        eo.update(1, "1234", "Guest Y", now, now, prefix=None)
        assert eo.overrides[1] is not None
        assert eo.overrides[1]["slot_name"] == "Guest Y"

    def test_update_empty_prefix_keeps_full_name(self, eo: EventOverrides) -> None:
        """Verify name is kept when prefix is empty string."""
        now = dt_util.now()
        eo.update(1, "1234", "Guest Z", now, now, prefix="")
        assert eo.overrides[1] is not None
        assert eo.overrides[1]["slot_name"] == "Guest Z"

    def test_update_prefix_not_present_in_name(self, eo: EventOverrides) -> None:
        """Verify name is kept intact when prefix doesn't match."""
        now = dt_util.now()
        eo.update(1, "1234", "Guest Q", now, now, prefix="Vacation")
        assert eo.overrides[1] is not None
        assert eo.overrides[1]["slot_name"] == "Guest Q"

    def test_update_replaces_existing_override(self, eo: EventOverrides) -> None:
        """Verify updating a slot replaces the previous override."""
        now = dt_util.now()
        eo.update(1, "1111", "First", now, now)
        eo.update(1, "2222", "Second", now, now)
        assert eo.overrides[1] is not None
        assert eo.overrides[1]["slot_name"] == "Second"
        assert eo.overrides[1]["slot_code"] == "2222"


# ---------------------------------------------------------------------------
# Slot assignment logic (__assign_next_slot via update)
# ---------------------------------------------------------------------------


class TestNextSlotAssignment:
    """Tests for next_slot computation triggered by update."""

    def test_next_slot_none_during_startup(self) -> None:
        """Before all slots are registered, next_slot stays None."""
        eo = EventOverrides(start_slot=1, max_slots=3)
        now = dt_util.now()
        eo.update(1, "", "", now, now)
        # Only 1 of 3 slots registered
        assert eo.next_slot is None

    def test_next_slot_assigned_after_all_slots_registered(
        self, populated_eo: EventOverrides
    ) -> None:
        """Once all slots are registered (even empty), next_slot is assigned."""
        # All 3 slots are None, so next should be start_slot (1)
        assert populated_eo.next_slot == 1

    def test_next_slot_advances_after_filling(self) -> None:
        """Filling a slot moves next_slot to the next empty one."""
        eo = EventOverrides(start_slot=1, max_slots=3)
        now = dt_util.now()
        end = now + timedelta(days=3)
        # Register all slots as empty
        eo.update(1, "", "", now, now)
        eo.update(2, "", "", now, now)
        eo.update(3, "", "", now, now)
        assert eo.next_slot == 1
        # Fill slot 1
        eo.update(1, "code", "Guest A", now, end)
        assert eo.next_slot == 2
        # Fill slot 2
        eo.update(2, "code", "Guest B", now, end)
        assert eo.next_slot == 3

    def test_next_slot_none_when_all_filled(self) -> None:
        """When all slots have values, next_slot is None."""
        eo = EventOverrides(start_slot=1, max_slots=3)
        now = dt_util.now()
        end = now + timedelta(days=3)
        eo.update(1, "c", "A", now, end)
        eo.update(2, "c", "B", now, end)
        eo.update(3, "c", "C", now, end)
        assert eo.next_slot is None

    def test_next_slot_wraps_around(self) -> None:
        """Clearing a lower slot after filling higher ones wraps next_slot."""
        eo = EventOverrides(start_slot=1, max_slots=3)
        now = dt_util.now()
        end = now + timedelta(days=3)
        # Fill all
        eo.update(1, "c", "A", now, end)
        eo.update(2, "c", "B", now, end)
        eo.update(3, "c", "C", now, end)
        assert eo.next_slot is None
        # Clear slot 1 (lower than max occupied slot 3)
        eo.update(1, "", "", now, now)
        # next_slot should wrap to 1 (the first free slot)
        assert eo.next_slot == 1

    def test_next_slot_prefers_after_max_occupied(self) -> None:
        """Next slot prefers a slot after the highest occupied one."""
        eo = EventOverrides(start_slot=1, max_slots=4)
        now = dt_util.now()
        end = now + timedelta(days=3)
        # Register all 4 slots
        eo.update(1, "", "", now, now)
        eo.update(2, "c", "A", now, end)
        eo.update(3, "", "", now, now)
        eo.update(4, "", "", now, now)
        # Highest occupied is 2, so next should be 3 (first empty after 2)
        assert eo.next_slot == 3

    def test_next_slot_with_high_start_slot(self) -> None:
        """Verify next_slot works correctly with non-1 start_slot."""
        eo = EventOverrides(start_slot=10, max_slots=3)
        now = dt_util.now()
        end = now + timedelta(days=3)
        eo.update(10, "", "", now, now)
        eo.update(11, "", "", now, now)
        eo.update(12, "", "", now, now)
        assert eo.next_slot == 10
        eo.update(10, "c", "Guest", now, end)
        assert eo.next_slot == 11


# ---------------------------------------------------------------------------
# get_slot_name / get_slot_with_name / get_slot_key_by_name
# ---------------------------------------------------------------------------


class TestSlotLookups:
    """Tests for slot lookup methods."""

    def test_get_slot_name_returns_name(self, eo: EventOverrides) -> None:
        """Verify get_slot_name returns the stored name."""
        now = dt_util.now()
        eo.update(1, "code", "Alice", now, now)
        assert eo.get_slot_name(1) == "Alice"

    def test_get_slot_name_returns_empty_for_none_slot(
        self, eo: EventOverrides
    ) -> None:
        """Verify get_slot_name returns '' for a None slot."""
        now = dt_util.now()
        eo.update(1, "", "", now, now)
        assert eo.get_slot_name(1) == ""

    def test_get_slot_with_name_found(self, eo: EventOverrides) -> None:
        """Verify get_slot_with_name returns the EventOverride dict."""
        now = dt_util.now()
        eo.update(1, "code", "Bob", now, now)
        result = eo.get_slot_with_name("Bob")
        assert result is not None
        assert result["slot_name"] == "Bob"
        assert result["slot_code"] == "code"

    def test_get_slot_with_name_not_found(self, eo: EventOverrides) -> None:
        """Verify get_slot_with_name returns None when name isn't present."""
        now = dt_util.now()
        eo.update(1, "code", "Bob", now, now)
        assert eo.get_slot_with_name("Charlie") is None

    def test_get_slot_with_name_empty_overrides(self, eo: EventOverrides) -> None:
        """Verify get_slot_with_name returns None with no overrides."""
        assert eo.get_slot_with_name("Nobody") is None

    def test_get_slot_key_by_name_found(self, eo: EventOverrides) -> None:
        """Verify get_slot_key_by_name returns the slot number."""
        now = dt_util.now()
        eo.update(2, "code", "Diana", now, now)
        assert eo.get_slot_key_by_name("Diana") == 2

    def test_get_slot_key_by_name_not_found(self, eo: EventOverrides) -> None:
        """Verify get_slot_key_by_name returns 0 when name isn't found."""
        assert eo.get_slot_key_by_name("Ghost") == 0

    def test_get_slot_key_by_name_skips_none_slots(self) -> None:
        """Verify get_slot_key_by_name skips None-valued slots."""
        eo = EventOverrides(start_slot=1, max_slots=3)
        now = dt_util.now()
        eo.update(1, "", "", now, now)
        eo.update(2, "code", "Eve", now, now)
        eo.update(3, "", "", now, now)
        assert eo.get_slot_key_by_name("Eve") == 2
        assert eo.get_slot_key_by_name("Nobody") == 0


# ---------------------------------------------------------------------------
# get_slot_start_date / get_slot_start_time / get_slot_end_date / get_slot_end_time
# ---------------------------------------------------------------------------


class TestSlotTimeAccessors:
    """Tests for slot start/end date and time extraction."""

    def test_start_date_from_override(self, eo: EventOverrides) -> None:
        """Verify get_slot_start_date returns the date from start_time."""
        start = _make_dt(2025, 8, 15, 14, 30)
        end = _make_dt(2025, 8, 20, 11, 0)
        eo.update(1, "c", "Guest", start, end)
        assert eo.get_slot_start_date(1) == start.date()

    def test_start_time_from_override(self, eo: EventOverrides) -> None:
        """Verify get_slot_start_time returns the time from start_time."""
        start = _make_dt(2025, 8, 15, 14, 30)
        end = _make_dt(2025, 8, 20, 11, 0)
        eo.update(1, "c", "Guest", start, end)
        assert eo.get_slot_start_time(1) == time(14, 30)

    def test_end_date_from_override(self, eo: EventOverrides) -> None:
        """Verify get_slot_end_date returns the date from end_time."""
        start = _make_dt(2025, 8, 15, 14, 30)
        end = _make_dt(2025, 8, 20, 11, 0)
        eo.update(1, "c", "Guest", start, end)
        assert eo.get_slot_end_date(1) == end.date()

    def test_end_time_from_override(self, eo: EventOverrides) -> None:
        """Verify get_slot_end_time returns the time from end_time."""
        start = _make_dt(2025, 8, 15, 14, 30)
        end = _make_dt(2025, 8, 20, 11, 0)
        eo.update(1, "c", "Guest", start, end)
        assert eo.get_slot_end_time(1) == time(11, 0)

    def test_start_date_defaults_for_none_slot(self, eo: EventOverrides) -> None:
        """Verify get_slot_start_date returns today for a cleared slot."""
        frozen = _make_dt(2025, 6, 15)
        with patch.object(dt_util, "start_of_local_day", return_value=frozen):
            now = dt_util.now()
            eo.update(1, "", "", now, now)
            assert eo.get_slot_start_date(1) == frozen.date()

    def test_start_time_defaults_for_none_slot(self, eo: EventOverrides) -> None:
        """Verify get_slot_start_time returns midnight for a cleared slot."""
        now = dt_util.now()
        eo.update(1, "", "", now, now)
        assert eo.get_slot_start_time(1) == time(0, 0)

    def test_end_date_defaults_for_none_slot(self, eo: EventOverrides) -> None:
        """Verify get_slot_end_date returns today for a cleared slot."""
        frozen = _make_dt(2025, 6, 15)
        with patch.object(dt_util, "start_of_local_day", return_value=frozen):
            now = dt_util.now()
            eo.update(1, "", "", now, now)
            assert eo.get_slot_end_date(1) == frozen.date()

    def test_end_time_defaults_for_none_slot(self, eo: EventOverrides) -> None:
        """Verify get_slot_end_time returns midnight for a cleared slot."""
        now = dt_util.now()
        eo.update(1, "", "", now, now)
        assert eo.get_slot_end_time(1) == time(0, 0)

    def test_timezone_aware_dates_extracted(self) -> None:
        """Verify date/time extraction works with timezone-aware datetimes."""
        import zoneinfo

        tz = zoneinfo.ZoneInfo("America/New_York")
        start = datetime(2025, 7, 4, 16, 0, tzinfo=tz)
        end = datetime(2025, 7, 8, 11, 0, tzinfo=tz)
        eo = EventOverrides(start_slot=1, max_slots=1)
        eo.update(1, "c", "Tz Guest", start, end)
        assert eo.get_slot_start_date(1) == start.date()
        assert eo.get_slot_start_time(1) == time(16, 0)
        assert eo.get_slot_end_date(1) == end.date()
        assert eo.get_slot_end_time(1) == time(11, 0)


# ---------------------------------------------------------------------------
# async_check_overrides
# ---------------------------------------------------------------------------


class TestAsyncCheckOverrides:
    """Tests for async_check_overrides clearing logic."""

    async def test_noop_when_calendar_data_unavailable(self) -> None:
        """Verify no action when calendar data is unavailable."""
        eo = EventOverrides(start_slot=1, max_slots=1)
        coordinator = _make_coordinator()
        now = dt_util.now()
        eo.update(1, "c", "Guest", now, now + timedelta(days=5))

        with patch(
            "custom_components.rental_control.event_overrides.async_fire_clear_code",
            new_callable=AsyncMock,
        ) as mock_fire:
            await eo.async_check_overrides(coordinator)
            mock_fire.assert_not_called()

    async def test_noop_when_no_assigned_slots(self) -> None:
        """Verify no action when all slots are None."""
        eo = EventOverrides(start_slot=1, max_slots=1)
        now = dt_util.now()
        eo.update(1, "", "", now, now)
        coordinator = _make_coordinator(
            calendar_events=[_make_event(_make_dt(2025, 8, 1), "Some Event")],
        )

        with patch(
            "custom_components.rental_control.event_overrides.async_fire_clear_code",
            new_callable=AsyncMock,
        ) as mock_fire:
            await eo.async_check_overrides(coordinator)
            mock_fire.assert_not_called()

    async def test_clears_slot_name_not_in_event_names(self) -> None:
        """Verify slot is cleared when its name is not in current event sensors."""
        eo = EventOverrides(start_slot=1, max_slots=1)
        now = dt_util.now()
        eo.update(1, "c", "Departed Guest", now, now + timedelta(days=5))

        coordinator = _make_coordinator(
            calendar_events=[
                _make_event(now + timedelta(days=10), "Current Guest"),
            ],
        )

        frozen = _make_dt(2025, 1, 1)
        with (
            patch(
                "custom_components.rental_control.event_overrides.async_fire_clear_code",
                new_callable=AsyncMock,
            ) as mock_fire,
            patch.object(dt_util, "start_of_local_day", return_value=frozen),
        ):
            await eo.async_check_overrides(coordinator)
            mock_fire.assert_called_once_with(coordinator, 1)
            # Slot should be cleared
            assert eo.overrides[1] is None

    async def test_clears_overrides_when_calendar_empty(self) -> None:
        """Verify overrides are cleared when calendar is empty.

        An empty event list means no active reservations, so any
        existing overrides should be cleared.
        """
        eo = EventOverrides(start_slot=1, max_slots=1)
        now = _make_dt(2025, 7, 1)
        eo.update(1, "c", "Guest", now, now + timedelta(days=5))

        coordinator = _make_coordinator(calendar_events=[])

        with patch(
            "custom_components.rental_control.event_overrides.async_fire_clear_code",
            new_callable=AsyncMock,
        ) as mock_fire:
            await eo.async_check_overrides(coordinator)
            mock_fire.assert_called_once()

    async def test_clears_when_start_after_end(self) -> None:
        """Verify slot is cleared when start_date > end_date."""
        eo = EventOverrides(start_slot=1, max_slots=1)
        start = _make_dt(2025, 7, 10)
        end = _make_dt(2025, 7, 5)  # Before start
        eo.update(1, "c", "Bad Guest", start, end)

        coordinator = _make_coordinator(
            calendar_events=[_make_event(_make_dt(2025, 8, 1), "Bad Guest")],
        )

        frozen = _make_dt(2025, 7, 1)
        with (
            patch(
                "custom_components.rental_control.event_overrides.async_fire_clear_code",
                new_callable=AsyncMock,
            ) as mock_fire,
            patch.object(dt_util, "start_of_local_day", return_value=frozen),
        ):
            await eo.async_check_overrides(coordinator)
            mock_fire.assert_called_once_with(coordinator, 1)

    async def test_clears_when_end_before_today(self) -> None:
        """Verify slot is cleared when end_date < current date."""
        eo = EventOverrides(start_slot=1, max_slots=1)
        start = _make_dt(2025, 6, 1)
        end = _make_dt(2025, 6, 5)
        eo.update(1, "c", "Past Guest", start, end)

        coordinator = _make_coordinator(
            calendar_events=[_make_event(_make_dt(2025, 8, 1), "Past Guest")],
        )

        # "Today" is June 10, so end (June 5) < today
        frozen = _make_dt(2025, 6, 10)
        with (
            patch(
                "custom_components.rental_control.event_overrides.async_fire_clear_code",
                new_callable=AsyncMock,
            ) as mock_fire,
            patch.object(dt_util, "start_of_local_day", return_value=frozen),
        ):
            await eo.async_check_overrides(coordinator)
            mock_fire.assert_called_once_with(coordinator, 1)

    async def test_clears_when_start_after_last_calendar_event(self) -> None:
        """Verify slot is cleared when start is beyond last calendar event end."""
        eo = EventOverrides(start_slot=1, max_slots=1)
        start = _make_dt(2025, 9, 1)
        end = _make_dt(2025, 9, 5)
        eo.update(1, "c", "Future Guest", start, end)

        # Calendar ends July 15, but guest starts Sept 1
        coordinator = _make_coordinator(
            calendar_events=[_make_event(_make_dt(2025, 7, 15), "Future Guest")],
            max_events=5,
        )

        frozen = _make_dt(2025, 7, 1)
        with (
            patch(
                "custom_components.rental_control.event_overrides.async_fire_clear_code",
                new_callable=AsyncMock,
            ) as mock_fire,
            patch.object(dt_util, "start_of_local_day", return_value=frozen),
        ):
            await eo.async_check_overrides(coordinator)
            mock_fire.assert_called_once_with(coordinator, 1)

    async def test_uses_max_events_index_for_last_end(self) -> None:
        """Verify last_end uses max_events index when calendar >= max_events."""
        eo = EventOverrides(start_slot=1, max_slots=1)
        start = _make_dt(2025, 7, 20)
        end = _make_dt(2025, 7, 25)
        eo.update(1, "c", "Guest", start, end)

        # 3 events, max_events=2 => uses calendar[1].end
        events = [
            _make_event(_make_dt(2025, 7, 10), "Guest"),
            _make_event(_make_dt(2025, 7, 15)),  # index 1
            _make_event(_make_dt(2025, 8, 1)),  # index 2 — ignored
        ]
        coordinator = _make_coordinator(
            calendar_events=events,
            max_events=2,
        )

        frozen = _make_dt(2025, 7, 1)
        with (
            patch(
                "custom_components.rental_control.event_overrides.async_fire_clear_code",
                new_callable=AsyncMock,
            ) as mock_fire,
            patch.object(dt_util, "start_of_local_day", return_value=frozen),
        ):
            await eo.async_check_overrides(coordinator)
            # start (July 20) > last_end (July 15) => cleared
            mock_fire.assert_called_once_with(coordinator, 1)

    async def test_does_not_clear_valid_override(self) -> None:
        """Verify a valid override is NOT cleared."""
        eo = EventOverrides(start_slot=1, max_slots=1)
        start = _make_dt(2025, 7, 5)
        end = _make_dt(2025, 7, 10)
        eo.update(1, "c", "Valid Guest", start, end)

        coordinator = _make_coordinator(
            calendar_events=[_make_event(_make_dt(2025, 8, 1), "Valid Guest")],
            max_events=5,
        )

        frozen = _make_dt(2025, 7, 1)
        with (
            patch(
                "custom_components.rental_control.event_overrides.async_fire_clear_code",
                new_callable=AsyncMock,
            ) as mock_fire,
            patch.object(dt_util, "start_of_local_day", return_value=frozen),
        ):
            await eo.async_check_overrides(coordinator)
            mock_fire.assert_not_called()
            # Override should still be present
            assert eo.overrides[1] is not None
            assert eo.overrides[1]["slot_name"] == "Valid Guest"

    async def test_clears_multiple_invalid_slots(self) -> None:
        """Verify multiple invalid slots are all cleared."""
        eo = EventOverrides(start_slot=1, max_slots=3)
        past = _make_dt(2025, 5, 1)
        past_end = _make_dt(2025, 5, 3)
        future = _make_dt(2025, 7, 5)
        future_end = _make_dt(2025, 7, 10)

        eo.update(1, "c", "Past Guest", past, past_end)
        eo.update(2, "c", "Valid Guest", future, future_end)
        eo.update(3, "c", "Also Past", past, past_end)

        coordinator = _make_coordinator(
            calendar_events=[
                _make_event(_make_dt(2025, 8, 1), "Past Guest"),
                _make_event(_make_dt(2025, 8, 1), "Valid Guest"),
                _make_event(_make_dt(2025, 8, 1), "Also Past"),
            ],
            max_events=5,
        )

        frozen = _make_dt(2025, 7, 1)
        with (
            patch(
                "custom_components.rental_control.event_overrides.async_fire_clear_code",
                new_callable=AsyncMock,
            ) as mock_fire,
            patch.object(dt_util, "start_of_local_day", return_value=frozen),
        ):
            await eo.async_check_overrides(coordinator)
            assert mock_fire.call_count == 2
            # Slot 2 (valid) should remain
            assert eo.overrides[2] is not None
            assert eo.overrides[2]["slot_name"] == "Valid Guest"
            # Slots 1 and 3 should be cleared
            assert eo.overrides[1] is None
            assert eo.overrides[3] is None


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge case and boundary condition tests."""

    def test_single_slot_system(self) -> None:
        """Verify behavior with max_slots=1."""
        eo = EventOverrides(start_slot=5, max_slots=1)
        now = dt_util.now()
        eo.update(5, "c", "Solo", now, now + timedelta(days=1))
        assert eo.ready is True
        assert eo.next_slot is None
        assert eo.get_slot_name(5) == "Solo"
        # Clear it
        eo.update(5, "", "", now, now)
        assert eo.next_slot == 5

    def test_update_preserves_other_slots(self) -> None:
        """Verify updating one slot does not affect others."""
        eo = EventOverrides(start_slot=1, max_slots=3)
        now = dt_util.now()
        end = now + timedelta(days=3)
        eo.update(1, "c1", "A", now, end)
        eo.update(2, "c2", "B", now, end)
        eo.update(3, "c3", "C", now, end)
        # Update slot 2
        new_end = now + timedelta(days=5)
        eo.update(2, "c2new", "B Updated", now, new_end)
        # Others unchanged
        assert eo.overrides[1] is not None
        assert eo.overrides[1]["slot_name"] == "A"
        assert eo.overrides[3] is not None
        assert eo.overrides[3]["slot_name"] == "C"
        assert eo.overrides[2] is not None
        assert eo.overrides[2]["slot_name"] == "B Updated"

    def test_get_slot_with_name_returns_first_match(self) -> None:
        """Verify get_slot_with_name returns the lowest-numbered matching slot."""
        eo = EventOverrides(start_slot=1, max_slots=3)
        now = dt_util.now()
        # Two slots with the same name (unusual but possible)
        eo.update(1, "c1", "Dup", now, now)
        eo.update(2, "c2", "Dup", now, now)
        eo.update(3, "", "", now, now)
        result = eo.get_slot_with_name("Dup")
        assert result is not None
        # Should be from slot 1 (sorted order)
        assert result["slot_code"] == "c1"

    def test_get_slot_key_by_name_returns_first_match(self) -> None:
        """Verify get_slot_key_by_name returns the lowest slot number matching."""
        eo = EventOverrides(start_slot=1, max_slots=3)
        now = dt_util.now()
        eo.update(1, "c1", "Dup", now, now)
        eo.update(2, "c2", "Dup", now, now)
        eo.update(3, "", "", now, now)
        assert eo.get_slot_key_by_name("Dup") == 1

    async def test_check_overrides_end_equals_today_not_cleared(self) -> None:
        """Verify slot is NOT cleared when end_date == current date (not <)."""
        eo = EventOverrides(start_slot=1, max_slots=1)
        today = _make_dt(2025, 7, 10)
        start = _make_dt(2025, 7, 5)
        end = _make_dt(2025, 7, 10)  # Same as "today"
        eo.update(1, "c", "Today Guest", start, end)

        coordinator = _make_coordinator(
            calendar_events=[_make_event(_make_dt(2025, 8, 1), "Today Guest")],
            max_events=5,
        )

        with (
            patch(
                "custom_components.rental_control.event_overrides.async_fire_clear_code",
                new_callable=AsyncMock,
            ) as mock_fire,
            patch.object(dt_util, "start_of_local_day", return_value=today),
        ):
            await eo.async_check_overrides(coordinator)
            mock_fire.assert_not_called()

    async def test_check_overrides_start_equals_last_end_not_cleared(self) -> None:
        """Verify slot is NOT cleared when start_date == last_end (not >)."""
        eo = EventOverrides(start_slot=1, max_slots=1)
        today = _make_dt(2025, 7, 1)
        start = _make_dt(2025, 7, 15)
        end = _make_dt(2025, 7, 20)
        eo.update(1, "c", "Edge Guest", start, end)

        # Last event ends July 15 — same as start
        coordinator = _make_coordinator(
            calendar_events=[_make_event(_make_dt(2025, 7, 15), "Edge Guest")],
            max_events=5,
        )

        with (
            patch(
                "custom_components.rental_control.event_overrides.async_fire_clear_code",
                new_callable=AsyncMock,
            ) as mock_fire,
            patch.object(dt_util, "start_of_local_day", return_value=today),
        ):
            await eo.async_check_overrides(coordinator)
            mock_fire.assert_not_called()

    async def test_clears_slot_beyond_max_events_boundary(self) -> None:
        """Verify slot is cleared when its event is beyond max_events.

        When the calendar has more events than max_events, only the
        first max_events events are managed by sensors. A slot tied
        to an event beyond that boundary should be cleared even though
        the event name still exists in the full calendar.
        """
        eo = EventOverrides(start_slot=1, max_slots=2)
        today = _make_dt(2025, 7, 1)

        # Slot 1: assigned to "Current Guest" (will be within boundary)
        eo.update(
            1,
            "1234",
            "Current Guest",
            _make_dt(2025, 7, 5),
            _make_dt(2025, 7, 10),
        )
        # Slot 2: assigned to "Old Guest" (will be beyond boundary)
        eo.update(
            2,
            "5678",
            "Old Guest",
            _make_dt(2025, 7, 12),
            _make_dt(2025, 7, 18),
        )

        # Calendar: 3 events, but max_events=2 so sensors only see
        # the first 2. "Old Guest" is at index 2 (beyond sensors).
        events = [
            _make_event(_make_dt(2025, 7, 10), "Current Guest"),
            _make_event(_make_dt(2025, 7, 20), "New Guest"),
            _make_event(_make_dt(2025, 7, 25), "Old Guest"),
        ]
        coordinator = _make_coordinator(
            calendar_events=events,
            max_events=2,
        )

        with (
            patch(
                "custom_components.rental_control.event_overrides.async_fire_clear_code",
                new_callable=AsyncMock,
            ) as mock_fire,
            patch.object(dt_util, "start_of_local_day", return_value=today),
        ):
            await eo.async_check_overrides(coordinator)
            # "Old Guest" at index 2 is beyond max_events=2, clear it
            mock_fire.assert_called_once_with(coordinator, 2)
            assert eo.overrides[2] is None
            # "Current Guest" at index 0 stays
            assert eo.overrides[1] is not None
            assert eo.overrides[1]["slot_name"] == "Current Guest"


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Idempotent reservation (US2: async_reserve_or_get_slot)
# ---------------------------------------------------------------------------


class TestIdempotentReservation:
    """Tests for idempotent reservation updates via async_reserve_or_get_slot."""

    @pytest.mark.asyncio
    async def test_time_update_returns_times_updated_true(self) -> None:
        """Re-reserving with changed times updates the slot.

        Guest in slot 10 with Mon-Fri, reserve again with Mon-Sat.
        Expected: ReserveResult(10, False, True) and stored times
        reflect the new end time.
        """
        eo = EventOverrides(start_slot=10, max_slots=3)
        now = dt_util.now()
        # Bootstrap all slots so next_slot is computed
        for s in (10, 11, 12):
            eo.update(s, "", "", now, now)

        mon = _make_dt(2025, 7, 7)
        fri = _make_dt(2025, 7, 11)
        sat = _make_dt(2025, 7, 12)

        # Initial reservation: Mon-Fri
        first = await eo.async_reserve_or_get_slot("Alice", "1234", mon, fri)
        assert first == ReserveResult(10, True, False)

        # Re-deliver with extended end: Mon-Sat
        second = await eo.async_reserve_or_get_slot("Alice", "1234", mon, sat)
        assert second == ReserveResult(10, False, True)

        # Stored times reflect the update
        override = eo.overrides[10]
        assert override is not None
        assert override["start_time"] == mon
        assert override["end_time"] == sat

    @pytest.mark.asyncio
    async def test_identical_reservation_is_noop(self) -> None:
        """Re-reserving with identical times is a no-op.

        Guest in slot 10 with Mon-Fri, reserve again with Mon-Fri.
        Expected: ReserveResult(10, False, False) and no state changes.
        """
        eo = EventOverrides(start_slot=10, max_slots=3)
        now = dt_util.now()
        # Bootstrap all slots so next_slot is computed
        for s in (10, 11, 12):
            eo.update(s, "", "", now, now)

        mon = _make_dt(2025, 7, 7)
        fri = _make_dt(2025, 7, 11)

        # Initial reservation: Mon-Fri
        first = await eo.async_reserve_or_get_slot("Alice", "1234", mon, fri)
        assert first == ReserveResult(10, True, False)

        # Snapshot state before re-delivery
        override_10 = eo.overrides[10]
        assert override_10 is not None
        before = override_10.copy()

        # Re-deliver with identical times
        second = await eo.async_reserve_or_get_slot("Alice", "1234", mon, fri)
        assert second == ReserveResult(10, False, False)

        # No state changes
        assert eo.overrides[10] == before


# ---------------------------------------------------------------------------
# Dedup redirect, back-to-back stays, UID tiebreaker (T023-T025)
# ---------------------------------------------------------------------------


def _make_ready_eo(max_slots: int = 5, start_slot: int = 1) -> EventOverrides:
    """Return a ready EventOverrides with all slots initialised as empty."""
    eo = EventOverrides(start_slot=start_slot, max_slots=max_slots)
    now = dt_util.now()
    for slot in range(start_slot, start_slot + max_slots):
        eo.update(slot, "", "", now, now)
    return eo


class TestDedupRedirect:
    """T023 — async_update dedup redirect on name+overlap conflict."""

    async def test_redirect_merges_into_existing_slot(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Overlapping write for same guest redirects to existing slot.

        Setup: "Alice" in slot 3 Mon–Fri.
        Action: async_update(slot=5, "Alice", Wed–Sun).
        Expected: slot 3 updated to Wed–Sun, slot 5 unchanged (None),
                  warning logged about the redirect.
        """
        eo = _make_ready_eo()
        mon = _make_dt(2025, 7, 7)
        fri = _make_dt(2025, 7, 11)
        wed = _make_dt(2025, 7, 9)
        sun = _make_dt(2025, 7, 13)

        # Pre-populate slot 3 with Alice Mon-Fri
        eo.update(3, "code3", "Alice", mon, fri)

        with caplog.at_level(logging.WARNING):
            await eo.async_update(
                slot=5,
                slot_code="code5",
                slot_name="Alice",
                start_time=wed,
                end_time=sun,
            )

        # Slot 3 received the redirected write (times overwritten)
        assert eo.overrides[3] is not None
        assert eo.overrides[3]["slot_name"] == "Alice"
        assert eo.overrides[3]["start_time"] == wed
        assert eo.overrides[3]["end_time"] == sun

        # Slot 5 was never touched — still None
        assert eo.overrides[5] is None

        # Warning about the redirect was logged
        assert any(
            "Duplicate slot_name 'Alice'" in rec.message
            and "slot 3" in rec.message
            and "slot 5" in rec.message
            for rec in caplog.records
        )

    async def test_redirect_preserves_code_from_new_write(self) -> None:
        """Redirected write carries the new slot_code to the target."""
        eo = _make_ready_eo()
        mon = _make_dt(2025, 7, 7)
        fri = _make_dt(2025, 7, 11)
        wed = _make_dt(2025, 7, 9)
        sun = _make_dt(2025, 7, 13)

        eo.update(3, "old_code", "Alice", mon, fri)

        await eo.async_update(
            slot=5,
            slot_code="new_code",
            slot_name="Alice",
            start_time=wed,
            end_time=sun,
        )

        assert eo.overrides[3] is not None
        assert eo.overrides[3]["slot_code"] == "new_code"


class TestBackToBackStays:
    """T024 — non-overlapping stays for same guest are independent."""

    async def test_back_to_back_stays_both_active(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Adjacent non-overlapping stays create separate slots.

        Setup: "Alice" in slot 3 Mon(Jul 7)–Fri(Jul 11).
        Action: async_update(slot=5, "Alice", Mon(Jul 14)–Fri(Jul 18)).
        Expected: both slots active, no dedup warning.
        """
        eo = _make_ready_eo()
        mon1 = _make_dt(2025, 7, 7)
        fri1 = _make_dt(2025, 7, 11)
        mon2 = _make_dt(2025, 7, 14)
        fri2 = _make_dt(2025, 7, 18)

        eo.update(3, "code3", "Alice", mon1, fri1)

        with caplog.at_level(logging.WARNING):
            await eo.async_update(
                slot=5,
                slot_code="code5",
                slot_name="Alice",
                start_time=mon2,
                end_time=fri2,
            )

        # Slot 3 unchanged
        assert eo.overrides[3] is not None
        assert eo.overrides[3]["slot_name"] == "Alice"
        assert eo.overrides[3]["start_time"] == mon1
        assert eo.overrides[3]["end_time"] == fri1

        # Slot 5 independently populated
        assert eo.overrides[5] is not None
        assert eo.overrides[5]["slot_name"] == "Alice"
        assert eo.overrides[5]["start_time"] == mon2
        assert eo.overrides[5]["end_time"] == fri2

        # No dedup warning
        assert not any("Duplicate slot_name" in rec.message for rec in caplog.records)

    async def test_abutting_stays_no_redirect(self) -> None:
        """Stays where end_a == start_b are strictly non-overlapping."""
        eo = _make_ready_eo()
        mon = _make_dt(2025, 7, 7)
        fri = _make_dt(2025, 7, 11)
        # Second stay starts exactly when first ends
        fri_same = _make_dt(2025, 7, 11)
        tue = _make_dt(2025, 7, 15)

        eo.update(3, "code3", "Alice", mon, fri)

        await eo.async_update(
            slot=5,
            slot_code="code5",
            slot_name="Alice",
            start_time=fri_same,
            end_time=tue,
        )

        # Both slots independently populated
        assert eo.overrides[3] is not None
        assert eo.overrides[3]["start_time"] == mon
        assert eo.overrides[5] is not None
        assert eo.overrides[5]["start_time"] == fri_same


class TestUidTiebreaker:
    """T025 — different UIDs distinguish same-name overlapping stays."""

    async def test_different_uid_reserves_new_slot(self) -> None:
        """Same name+overlap but different UIDs create separate slots.

        Setup: "Alice" reserved with uid="AAA".
        Action: async_reserve_or_get_slot("Alice", same times, uid="BBB").
        Expected: new slot reserved (UIDs prove distinct reservations).
        """
        eo = _make_ready_eo()
        mon = _make_dt(2025, 7, 7)
        fri = _make_dt(2025, 7, 11)

        # Seed first reservation to store uid
        result1 = await eo.async_reserve_or_get_slot(
            slot_name="Alice",
            slot_code="code3",
            start_time=mon,
            end_time=fri,
            uid="AAA",
        )
        assert result1.slot is not None
        assert result1.is_new is True

        # Reserve again with different UID
        result2 = await eo.async_reserve_or_get_slot(
            slot_name="Alice",
            slot_code="code4",
            start_time=mon,
            end_time=fri,
            uid="BBB",
        )

        assert result2.slot is not None
        assert result2.slot != result1.slot
        assert result2.is_new is True

        # Both slots independently populated
        override1 = eo.overrides[result1.slot]
        assert override1 is not None
        assert override1["slot_name"] == "Alice"
        override2 = eo.overrides[result2.slot]
        assert override2 is not None
        assert override2["slot_name"] == "Alice"

    async def test_same_uid_returns_existing_slot(self) -> None:
        """Same name+overlap+UID returns existing slot, not new."""
        eo = _make_ready_eo()
        mon = _make_dt(2025, 7, 7)
        fri = _make_dt(2025, 7, 11)

        result1 = await eo.async_reserve_or_get_slot(
            slot_name="Alice",
            slot_code="code3",
            start_time=mon,
            end_time=fri,
            uid="AAA",
        )
        assert result1.is_new is True

        result2 = await eo.async_reserve_or_get_slot(
            slot_name="Alice",
            slot_code="code3",
            start_time=mon,
            end_time=fri,
            uid="AAA",
        )

        assert result2.slot == result1.slot
        assert result2.is_new is False

    async def test_no_uid_stored_matches_any_incoming_uid(self) -> None:
        """Slot without stored UID matches any incoming UID.

        When the existing slot has no UID (seeded via sync update),
        _find_overlapping_slot matches regardless of incoming UID
        because the UID tiebreaker only skips when both are non-None
        and differ.
        """
        eo = _make_ready_eo()
        mon = _make_dt(2025, 7, 7)
        fri = _make_dt(2025, 7, 11)

        # Seed via sync update (no UID stored)
        eo.update(3, "code3", "Alice", mon, fri)

        result = await eo.async_reserve_or_get_slot(
            slot_name="Alice",
            slot_code="code_new",
            start_time=mon,
            end_time=fri,
            uid="BBB",
        )

        # Matches existing slot 3 (no stored UID → no tiebreaker skip)
        assert result.slot == 3
        assert result.is_new is False
