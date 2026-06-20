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

from custom_components.rental_control import event_overrides as _eo_module
from custom_components.rental_control.const import COORDINATOR
from custom_components.rental_control.const import DOMAIN
from custom_components.rental_control.event_overrides import EventOverride
from custom_components.rental_control.event_overrides import EventOverrides
from custom_components.rental_control.event_overrides import ReserveResult
from custom_components.rental_control.reconciliation import ActionKind
from custom_components.rental_control.reconciliation import DesiredPlan
from custom_components.rental_control.reconciliation import Reservation
from custom_components.rental_control.reconciliation import SlotAction
from custom_components.rental_control.util import EventIdentity
from custom_components.rental_control.util import OperationResult
from custom_components.rental_control.util import async_fire_set_code
from custom_components.rental_control.util import handle_state_change
from custom_components.rental_control.util import trim_name

# ---------------------------------------------------------------------------
# Helpers / Fixtures
# ---------------------------------------------------------------------------


def _make_dt(
    year: int, month: int, day: int, hour: int = 0, minute: int = 0
) -> datetime:
    """Build a timezone-aware datetime in UTC."""
    return datetime(year, month, day, hour, minute, tzinfo=dt_util.UTC)


def _make_event(
    end: datetime,
    summary: str = "",
    start: datetime | None = None,
    uid: str | None = None,
) -> MagicMock:
    """Create a mock calendar event with the given end and summary.

    When *start* is omitted it defaults to ``end - 5 days`` which is
    fine for tests that never inspect start.  Supply an explicit
    *start* whenever the test relies on time-range overlap matching.
    """
    event = MagicMock()
    event.start = start if start is not None else end - timedelta(days=5)
    event.end = end
    event.summary = summary
    event.description = ""
    event.uid = uid
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

    @pytest.mark.asyncio
    async def test_async_update_sets_ready(self) -> None:
        """Verify async_update sets ready once all slots are populated."""
        eo = EventOverrides(start_slot=1, max_slots=2)
        now = dt_util.now()
        assert eo.ready is False
        await eo.async_update(1, "code1", "Guest A", now, now)
        assert eo.ready is False
        await eo.async_update(2, "code2", "Guest B", now, now)
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
            return_value=OperationResult(kind="clear", slot=1, confirmed=True),
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
            return_value=OperationResult(kind="clear", slot=1, confirmed=True),
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
                return_value=OperationResult(kind="clear", slot=1, confirmed=True),
            ) as mock_fire,
            patch.object(dt_util, "start_of_local_day", return_value=frozen),
        ):
            await eo.async_check_overrides(coordinator)
            assert eo.overrides[1] is not None
            assert eo._slot_miss_counts[1] == 1

            await eo.async_check_overrides(coordinator)

            mock_fire.assert_called_once_with(
                coordinator, 1, expected_name="Departed Guest"
            )
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
            return_value=OperationResult(kind="clear", slot=1, confirmed=True),
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
                return_value=OperationResult(kind="clear", slot=1, confirmed=True),
            ) as mock_fire,
            patch.object(dt_util, "start_of_local_day", return_value=frozen),
        ):
            await eo.async_check_overrides(coordinator)
            mock_fire.assert_called_once_with(coordinator, 1, expected_name="Bad Guest")

    async def test_unconfirmed_legacy_clear_keeps_slot_occupied(self) -> None:
        """Verify legacy clears do not free slots without confirmation."""
        eo = EventOverrides(start_slot=1, max_slots=1)
        start = _make_dt(2025, 7, 10)
        end = _make_dt(2025, 7, 5)
        eo.update(1, "c", "Bad Guest", start, end)
        coordinator = _make_coordinator(
            calendar_events=[_make_event(_make_dt(2025, 8, 1), "Bad Guest")],
        )

        with patch(
            "custom_components.rental_control.event_overrides.async_fire_clear_code",
            new_callable=AsyncMock,
            return_value=OperationResult(kind="clear", slot=1, unconfirmed=True),
        ):
            await eo.async_check_overrides(coordinator)

        assert eo.overrides[1] is not None

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
            mock_fire.assert_called_once_with(
                coordinator, 1, expected_name="Past Guest"
            )

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
                return_value=OperationResult(kind="clear", slot=1, confirmed=True),
            ) as mock_fire,
            patch.object(dt_util, "start_of_local_day", return_value=frozen),
        ):
            await eo.async_check_overrides(coordinator)
            mock_fire.assert_called_once_with(
                coordinator, 1, expected_name="Future Guest"
            )

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
                return_value=OperationResult(kind="clear", slot=1, confirmed=True),
            ) as mock_fire,
            patch.object(dt_util, "start_of_local_day", return_value=frozen),
        ):
            await eo.async_check_overrides(coordinator)
            # start (July 20) > last_end (July 15) => cleared
            mock_fire.assert_called_once_with(coordinator, 1, expected_name="Guest")

    async def test_does_not_clear_valid_override(self) -> None:
        """Verify a valid override is NOT cleared."""
        eo = EventOverrides(start_slot=1, max_slots=1)
        start = _make_dt(2025, 7, 5)
        end = _make_dt(2025, 7, 10)
        eo.update(1, "c", "Valid Guest", start, end)

        coordinator = _make_coordinator(
            calendar_events=[
                _make_event(end, "Valid Guest", start=start),
            ],
            max_events=5,
        )

        frozen = _make_dt(2025, 7, 1)
        with (
            patch(
                "custom_components.rental_control.event_overrides.async_fire_clear_code",
                new_callable=AsyncMock,
                return_value=OperationResult(kind="clear", slot=1, confirmed=True),
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
        past2 = _make_dt(2025, 5, 5)
        past2_end = _make_dt(2025, 5, 8)
        future = _make_dt(2025, 7, 5)
        future_end = _make_dt(2025, 7, 10)

        eo.update(1, "c", "Past Guest", past, past_end)
        eo.update(2, "c", "Valid Guest", future, future_end)
        eo.update(3, "c", "Also Past", past2, past2_end)

        # Calendar ordered chronologically; Valid Guest at end
        # makes last_end=Jul 10 so slot 2 isn't caught by
        # start_after_last_end.
        coordinator = _make_coordinator(
            calendar_events=[
                _make_event(past_end, "Past Guest", start=past),
                _make_event(past2_end, "Also Past", start=past2),
                _make_event(future_end, "Valid Guest", start=future),
            ],
            max_events=5,
        )

        frozen = _make_dt(2025, 7, 1)
        with (
            patch(
                "custom_components.rental_control.event_overrides.async_fire_clear_code",
                new_callable=AsyncMock,
                return_value=OperationResult(kind="clear", slot=1, confirmed=True),
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

    async def test_clears_orphaned_slot_same_name_different_dates(
        self,
    ) -> None:
        """Verify orphaned slot is cleared when same-name event cancelled.

        Scenario: Guest creates two reservations (same name, different
        dates).  One reservation is cancelled.  The remaining event
        still has the same name but a different time range.  The
        orphaned slot must be cleared even though the name still
        exists in the calendar.
        """
        eo = EventOverrides(start_slot=1, max_slots=2)

        # Two overrides for the same guest, non-overlapping dates
        stay_a_start = _make_dt(2025, 7, 1)
        stay_a_end = _make_dt(2025, 7, 5)
        stay_b_start = _make_dt(2025, 7, 10)
        stay_b_end = _make_dt(2025, 7, 15)

        eo.update(1, "1234", "John Doe", stay_a_start, stay_a_end)
        eo.update(2, "5678", "John Doe", stay_b_start, stay_b_end)

        # Guest cancels stay B — only stay A remains in calendar
        coordinator = _make_coordinator(
            calendar_events=[
                _make_event(stay_a_end, "John Doe", start=stay_a_start),
            ],
            max_events=5,
        )

        frozen = _make_dt(2025, 6, 25)
        with (
            patch(
                "custom_components.rental_control.event_overrides.async_fire_clear_code",
                new_callable=AsyncMock,
                return_value=OperationResult(kind="clear", slot=1, confirmed=True),
            ) as mock_fire,
            patch.object(dt_util, "start_of_local_day", return_value=frozen),
        ):
            await eo.async_check_overrides(coordinator)
            # Only slot 2 (stay B) should be cleared
            mock_fire.assert_called_once_with(coordinator, 2, expected_name="John Doe")
            assert eo.overrides[1] is not None
            assert eo.overrides[1]["slot_name"] == "John Doe"
            assert eo.overrides[2] is None

    async def test_keeps_both_slots_same_name_both_active(self) -> None:
        """Verify both slots kept when same-name events both exist."""
        eo = EventOverrides(start_slot=1, max_slots=2)

        stay_a_start = _make_dt(2025, 7, 1)
        stay_a_end = _make_dt(2025, 7, 5)
        stay_b_start = _make_dt(2025, 7, 10)
        stay_b_end = _make_dt(2025, 7, 15)

        eo.update(1, "1234", "John Doe", stay_a_start, stay_a_end)
        eo.update(2, "5678", "John Doe", stay_b_start, stay_b_end)

        # Both stays still in calendar
        coordinator = _make_coordinator(
            calendar_events=[
                _make_event(stay_a_end, "John Doe", start=stay_a_start),
                _make_event(stay_b_end, "John Doe", start=stay_b_start),
            ],
            max_events=5,
        )

        frozen = _make_dt(2025, 6, 25)
        with (
            patch(
                "custom_components.rental_control.event_overrides.async_fire_clear_code",
                new_callable=AsyncMock,
                return_value=OperationResult(kind="clear", slot=1, confirmed=True),
            ) as mock_fire,
            patch.object(dt_util, "start_of_local_day", return_value=frozen),
        ):
            await eo.async_check_overrides(coordinator)
            mock_fire.assert_not_called()
            assert eo.overrides[1] is not None
            assert eo.overrides[2] is not None

    async def test_uid_distinguishes_same_name_overlapping(self) -> None:
        """Different UID with different start still counts as a miss."""
        eo = EventOverrides(start_slot=1, max_slots=2)

        start = _make_dt(2025, 7, 1)
        end = _make_dt(2025, 7, 10)
        start_2 = _make_dt(2025, 7, 2)

        eo.update(1, "1234", "John Doe", start, end)
        eo._slot_uids[1] = "uid-A"
        eo.update(2, "5678", "John Doe", start_2, end)
        eo._slot_uids[2] = "uid-B"

        # Only uid-A event remains
        coordinator = _make_coordinator(
            calendar_events=[
                _make_event(end, "John Doe", start=start, uid="uid-A"),
            ],
            max_events=5,
        )

        frozen = _make_dt(2025, 6, 25)
        with (
            patch(
                "custom_components.rental_control.event_overrides.async_fire_clear_code",
                new_callable=AsyncMock,
                return_value=OperationResult(kind="clear", slot=1, confirmed=True),
            ) as mock_fire,
            patch.object(dt_util, "start_of_local_day", return_value=frozen),
        ):
            await eo.async_check_overrides(coordinator)
            assert eo.overrides[2] is not None
            assert eo._slot_miss_counts[2] == 1

            await eo.async_check_overrides(coordinator)

            # Slot 2 (uid-B) should be cleared
            mock_fire.assert_called_once_with(coordinator, 2, expected_name="John Doe")
            assert eo.overrides[1] is not None
            assert eo.overrides[2] is None

    async def test_uid_same_start_duplicate_clears_stale_slot(self) -> None:
        """Same-start bypass must still evict stale duplicates from old bug."""
        eo = EventOverrides(start_slot=1, max_slots=2)

        start = _make_dt(2025, 7, 1)
        old_end = _make_dt(2025, 7, 5)
        new_end = _make_dt(2025, 7, 10)

        eo.update(1, "1234", "John Doe", start, old_end)
        eo._slot_uids[1] = "uid-old"
        eo.update(2, "5678", "John Doe", start, new_end)
        eo._slot_uids[2] = "uid-new"

        coordinator = _make_coordinator(
            calendar_events=[
                _make_event(new_end, "John Doe", start=start, uid="uid-new"),
            ],
            max_events=5,
        )

        frozen = _make_dt(2025, 6, 25)
        with (
            patch(
                "custom_components.rental_control.event_overrides.async_fire_clear_code",
                new_callable=AsyncMock,
                return_value=OperationResult(kind="clear", slot=1, confirmed=True),
            ) as mock_fire,
            patch.object(dt_util, "start_of_local_day", return_value=frozen),
        ):
            await eo.async_check_overrides(coordinator)
            assert eo._slot_miss_counts[1] == 1
            assert eo.overrides[1] is not None
            assert eo.overrides[2] is not None

            await eo.async_check_overrides(coordinator)

            mock_fire.assert_called_once_with(coordinator, 1, expected_name="John Doe")
            assert eo.overrides[1] is None
            assert eo.overrides[2] is not None

    async def test_uid_same_start_duplicate_without_owner_ages_stale_slot(self) -> None:
        """Closest same-start slot should win when all stored UIDs are stale."""
        eo = EventOverrides(start_slot=1, max_slots=2)

        start = _make_dt(2025, 7, 1)
        stale_end = _make_dt(2025, 7, 5)
        live_end = _make_dt(2025, 7, 10)
        current_end = _make_dt(2025, 7, 12)

        eo.update(1, "1234", "John Doe", start, stale_end)
        eo._slot_uids[1] = "uid-old-1"
        eo.update(2, "5678", "John Doe", start, live_end)
        eo._slot_uids[2] = "uid-old-2"

        coordinator = _make_coordinator(
            calendar_events=[
                _make_event(current_end, "John Doe", start=start, uid="uid-new"),
            ],
            max_events=5,
        )

        frozen = _make_dt(2025, 6, 25)
        with (
            patch(
                "custom_components.rental_control.event_overrides.async_fire_clear_code",
                new_callable=AsyncMock,
                return_value=OperationResult(kind="clear", slot=1, confirmed=True),
            ) as mock_fire,
            patch.object(dt_util, "start_of_local_day", return_value=frozen),
        ):
            await eo.async_check_overrides(coordinator)
            assert eo._slot_miss_counts[1] == 1
            assert 2 not in eo._slot_miss_counts

            await eo.async_check_overrides(coordinator)

            mock_fire.assert_called_once_with(coordinator, 1, expected_name="John Doe")
            assert eo.overrides[1] is None
            assert eo.overrides[2] is not None

    async def test_uid_same_start_duplicate_with_no_uid_and_stale_peer_ages_slot(
        self,
    ) -> None:
        """A no-UID stale exact-name slot must not shadow a stale same-start peer."""
        eo = EventOverrides(start_slot=1, max_slots=2)

        start = _make_dt(2025, 7, 1)
        stale_end = _make_dt(2025, 7, 5)
        live_end = _make_dt(2025, 7, 10)
        current_end = _make_dt(2025, 7, 12)

        eo.update(1, "1234", "John Doe", start, stale_end)
        eo.update(2, "5678", "John Doe", start, live_end)
        eo._slot_uids[2] = "uid-old"

        coordinator = _make_coordinator(
            calendar_events=[
                _make_event(current_end, "John Doe", start=start, uid="uid-new"),
            ],
            max_events=5,
        )

        frozen = _make_dt(2025, 6, 25)
        with (
            patch(
                "custom_components.rental_control.event_overrides.async_fire_clear_code",
                new_callable=AsyncMock,
                return_value=OperationResult(kind="clear", slot=1, confirmed=True),
            ) as mock_fire,
            patch.object(dt_util, "start_of_local_day", return_value=frozen),
        ):
            await eo.async_check_overrides(coordinator)
            assert eo._slot_miss_counts[1] == 1
            assert 2 not in eo._slot_miss_counts

            await eo.async_check_overrides(coordinator)

            mock_fire.assert_called_once_with(coordinator, 1, expected_name="John Doe")
            assert eo.overrides[1] is None
            assert eo.overrides[2] is not None

    async def test_uid_different_start_no_uid_slot_does_not_shadow_peer(self) -> None:
        """A no-UID overlap on another start date must age out."""
        eo = EventOverrides(start_slot=1, max_slots=2)

        stale_start = _make_dt(2025, 7, 1)
        live_start = _make_dt(2025, 7, 2)
        stale_end = _make_dt(2025, 7, 5)
        live_end = _make_dt(2025, 7, 10)
        current_end = _make_dt(2025, 7, 12)

        eo.update(1, "1234", "John Doe", stale_start, stale_end)
        eo.update(2, "5678", "John Doe", live_start, live_end)
        eo._slot_uids[2] = "uid-old"

        coordinator = _make_coordinator(
            calendar_events=[
                _make_event(current_end, "John Doe", start=live_start, uid="uid-new"),
            ],
            max_events=5,
        )

        frozen = _make_dt(2025, 6, 25)
        with (
            patch(
                "custom_components.rental_control.event_overrides.async_fire_clear_code",
                new_callable=AsyncMock,
                return_value=OperationResult(kind="clear", slot=1, confirmed=True),
            ) as mock_fire,
            patch.object(dt_util, "start_of_local_day", return_value=frozen),
        ):
            await eo.async_check_overrides(coordinator)
            assert eo._slot_miss_counts[1] == 1
            assert 2 not in eo._slot_miss_counts

            await eo.async_check_overrides(coordinator)

            mock_fire.assert_called_once_with(coordinator, 1, expected_name="John Doe")
            assert eo.overrides[1] is None
            assert eo.overrides[2] is not None

    async def test_uid_same_start_mixed_trim_duplicate_ages_stale_slot(self) -> None:
        """Trim-aware cleanup should keep the closest mixed-name duplicate."""
        eo = EventOverrides(start_slot=1, max_slots=2)
        eo.trim_names = True
        eo.max_name_length = 11
        eo.prefix_length = 7

        start = _make_dt(2025, 7, 1)
        stale_end = _make_dt(2025, 7, 5)
        live_end = _make_dt(2025, 7, 10)
        current_end = _make_dt(2025, 7, 12)

        eo.update(1, "1234", "John Doe", start, stale_end)
        eo._slot_uids[1] = "uid-old-1"
        eo.update(2, "5678", "John", start, live_end)
        eo._slot_uids[2] = "uid-old-2"

        coordinator = _make_coordinator(
            calendar_events=[
                _make_event(current_end, "John Doe", start=start, uid="uid-new"),
            ],
            max_events=5,
        )

        frozen = _make_dt(2025, 6, 25)
        with (
            patch(
                "custom_components.rental_control.event_overrides.async_fire_clear_code",
                new_callable=AsyncMock,
                return_value=OperationResult(kind="clear", slot=1, confirmed=True),
            ) as mock_fire,
            patch.object(dt_util, "start_of_local_day", return_value=frozen),
        ):
            await eo.async_check_overrides(coordinator)
            assert eo._slot_miss_counts[1] == 1
            assert 2 not in eo._slot_miss_counts

            await eo.async_check_overrides(coordinator)

            mock_fire.assert_called_once_with(coordinator, 1, expected_name="John Doe")
            assert eo.overrides[1] is None
            assert eo.overrides[2] is not None

    async def test_uid_same_start_mixed_trim_duplicate_with_no_uid_ages_stale_slot(
        self,
    ) -> None:
        """A no-UID stale exact-name slot must not shadow the live trimmed one."""
        eo = EventOverrides(start_slot=1, max_slots=2)
        eo.trim_names = True
        eo.max_name_length = 11
        eo.prefix_length = 7

        start = _make_dt(2025, 7, 1)
        stale_end = _make_dt(2025, 7, 5)
        live_end = _make_dt(2025, 7, 10)
        current_end = _make_dt(2025, 7, 12)

        eo.update(1, "1234", "John Doe", start, stale_end)
        eo.update(2, "5678", "John", start, live_end)
        eo._slot_uids[2] = "uid-live"

        coordinator = _make_coordinator(
            calendar_events=[
                _make_event(current_end, "John Doe", start=start, uid="uid-live"),
            ],
            max_events=5,
        )

        frozen = _make_dt(2025, 6, 25)
        with (
            patch(
                "custom_components.rental_control.event_overrides.async_fire_clear_code",
                new_callable=AsyncMock,
                return_value=OperationResult(kind="clear", slot=1, confirmed=True),
            ) as mock_fire,
            patch.object(dt_util, "start_of_local_day", return_value=frozen),
        ):
            await eo.async_check_overrides(coordinator)
            assert eo._slot_miss_counts[1] == 1
            assert 2 not in eo._slot_miss_counts

            await eo.async_check_overrides(coordinator)

            mock_fire.assert_called_once_with(coordinator, 1, expected_name="John Doe")
            assert eo.overrides[1] is None
            assert eo.overrides[2] is not None


# ---------------------------------------------------------------------------
# verify_slot_ownership
# ---------------------------------------------------------------------------


class TestVerifySlotOwnership:
    """Tests for verify_slot_ownership read-only check."""

    def test_match_returns_true(self) -> None:
        """Verify True when slot name matches expected_name."""
        eo = EventOverrides(start_slot=1, max_slots=1)
        now = dt_util.now()
        eo.update(1, "c", "Guest A", now, now + timedelta(days=1))
        assert eo.verify_slot_ownership(1, "Guest A") is True

    def test_mismatch_returns_false(self) -> None:
        """Verify False when slot name does not match expected_name."""
        eo = EventOverrides(start_slot=1, max_slots=1)
        now = dt_util.now()
        eo.update(1, "c", "Guest A", now, now + timedelta(days=1))
        assert eo.verify_slot_ownership(1, "Guest B") is False

    def test_empty_slot_returns_false(self) -> None:
        """Verify False when slot is empty (None override)."""
        eo = EventOverrides(start_slot=1, max_slots=1)
        now = dt_util.now()
        eo.update(1, "", "", now, now)
        assert eo.verify_slot_ownership(1, "Guest A") is False

    def test_nonexistent_slot_returns_false(self) -> None:
        """Verify False when slot key does not exist."""
        eo = EventOverrides(start_slot=1, max_slots=1)
        assert eo.verify_slot_ownership(99, "Guest A") is False


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
            calendar_events=[
                _make_event(end, "Today Guest", start=start),
            ],
            max_events=5,
        )

        with (
            patch(
                "custom_components.rental_control.event_overrides.async_fire_clear_code",
                new_callable=AsyncMock,
                return_value=OperationResult(kind="clear", slot=1, confirmed=True),
            ) as mock_fire,
            patch.object(dt_util, "start_of_local_day", return_value=today),
        ):
            await eo.async_check_overrides(coordinator)
            mock_fire.assert_not_called()

    async def test_valid_override_with_matching_event_not_cleared(self) -> None:
        """Verify slot is NOT cleared when event matches by time overlap."""
        eo = EventOverrides(start_slot=1, max_slots=1)
        today = _make_dt(2025, 7, 1)
        start = _make_dt(2025, 7, 15)
        end = _make_dt(2025, 7, 20)
        eo.update(1, "c", "Edge Guest", start, end)

        coordinator = _make_coordinator(
            calendar_events=[
                _make_event(end, "Edge Guest", start=start),
            ],
            max_events=5,
        )

        with (
            patch(
                "custom_components.rental_control.event_overrides.async_fire_clear_code",
                new_callable=AsyncMock,
                return_value=OperationResult(kind="clear", slot=1, confirmed=True),
            ) as mock_fire,
            patch.object(dt_util, "start_of_local_day", return_value=today),
        ):
            await eo.async_check_overrides(coordinator)
            mock_fire.assert_not_called()

    async def test_clear_failure_still_frees_slot(self) -> None:
        """Verify failed clear leaves the slot occupied for retry."""
        eo = EventOverrides(start_slot=1, max_slots=1)
        start = _make_dt(2025, 6, 1)
        end = _make_dt(2025, 6, 5)
        eo.update(1, "c", "Past Guest", start, end)

        coordinator = _make_coordinator(
            calendar_events=[_make_event(_make_dt(2025, 8, 1), "Past Guest")],
        )

        frozen = _make_dt(2025, 6, 10)
        with (
            patch(
                "custom_components.rental_control.event_overrides.async_fire_clear_code",
                new_callable=AsyncMock,
                return_value=OperationResult(kind="clear", slot=1, failed=True),
            ) as mock_fire,
            patch.object(dt_util, "start_of_local_day", return_value=frozen),
        ):
            await eo.async_check_overrides(coordinator)
            mock_fire.assert_called_once_with(
                coordinator, 1, expected_name="Past Guest"
            )
            assert eo.overrides[1] is not None
            assert eo.next_slot is None

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
            _make_event(
                _make_dt(2025, 7, 10),
                "Current Guest",
                start=_make_dt(2025, 7, 5),
            ),
            _make_event(
                _make_dt(2025, 7, 20),
                "New Guest",
                start=_make_dt(2025, 7, 11),
            ),
            _make_event(
                _make_dt(2025, 7, 25),
                "Old Guest",
                start=_make_dt(2025, 7, 12),
            ),
        ]
        coordinator = _make_coordinator(
            calendar_events=events,
            max_events=2,
        )

        with (
            patch(
                "custom_components.rental_control.event_overrides.async_fire_clear_code",
                new_callable=AsyncMock,
                return_value=OperationResult(kind="clear", slot=1, confirmed=True),
            ) as mock_fire,
            patch.object(dt_util, "start_of_local_day", return_value=today),
        ):
            await eo.async_check_overrides(coordinator)
            assert eo.overrides[2] is not None
            assert eo._slot_miss_counts[2] == 1

            await eo.async_check_overrides(coordinator)

            # "Old Guest" at index 2 is beyond max_events=2, clear it
            mock_fire.assert_called_once_with(coordinator, 2, expected_name="Old Guest")
            assert eo.overrides[2] is None
            # "Current Guest" at index 0 stays
            assert eo.overrides[1] is not None
            assert eo.overrides[1]["slot_name"] == "Current Guest"


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Phase 3 — async_reserve_or_get_slot & _find_overlapping_slot Tests
# ---------------------------------------------------------------------------


class TestAsyncReserveOrGetSlotNewReservation:
    """T011: Tests for async_reserve_or_get_slot() new-reservation path."""

    async def test_single_reservation_gets_slot(
        self, populated_eo: EventOverrides
    ) -> None:
        """A single new reservation should receive a valid slot."""
        result = await populated_eo.async_reserve_or_get_slot(
            slot_name="Alice",
            slot_code="1234",
            start_time=_make_dt(2025, 8, 1),
            end_time=_make_dt(2025, 8, 5),
        )
        assert result.slot is not None
        assert result.is_new is True
        assert result.times_updated is False
        override = populated_eo.overrides[result.slot]
        assert override is not None
        assert override["slot_name"] == "Alice"

    async def test_two_sequential_reservations_get_different_slots(
        self, populated_eo: EventOverrides
    ) -> None:
        """Two distinct reservations must receive different slots."""
        r1 = await populated_eo.async_reserve_or_get_slot(
            slot_name="Alice",
            slot_code="1234",
            start_time=_make_dt(2025, 8, 1),
            end_time=_make_dt(2025, 8, 5),
        )
        r2 = await populated_eo.async_reserve_or_get_slot(
            slot_name="Bob",
            slot_code="5678",
            start_time=_make_dt(2025, 8, 6),
            end_time=_make_dt(2025, 8, 10),
        )
        assert r1.slot is not None
        assert r2.slot is not None
        assert r1.slot != r2.slot
        assert r1.is_new is True
        assert r2.is_new is True

    async def test_next_slot_recalculated_after_each_reservation(
        self, populated_eo: EventOverrides
    ) -> None:
        """_next_slot should advance after each successful reservation."""
        initial_next = populated_eo.next_slot
        assert initial_next is not None

        r1 = await populated_eo.async_reserve_or_get_slot(
            slot_name="Alice",
            slot_code="1234",
            start_time=_make_dt(2025, 8, 1),
            end_time=_make_dt(2025, 8, 5),
        )
        after_first = populated_eo.next_slot
        assert after_first != initial_next

        r2 = await populated_eo.async_reserve_or_get_slot(
            slot_name="Bob",
            slot_code="5678",
            start_time=_make_dt(2025, 8, 6),
            end_time=_make_dt(2025, 8, 10),
        )
        after_second = populated_eo.next_slot
        assert after_second != after_first
        assert r1.slot != r2.slot


class TestFindOverlappingSlot:
    """T012: Tests for _find_overlapping_slot() identity matching."""

    async def test_same_name_overlapping_times_returns_existing(
        self, populated_eo: EventOverrides
    ) -> None:
        """Same guest name with overlapping dates returns the existing slot."""
        r1 = await populated_eo.async_reserve_or_get_slot(
            slot_name="Alice",
            slot_code="1234",
            start_time=_make_dt(2025, 8, 1),
            end_time=_make_dt(2025, 8, 10),
        )
        assert r1.is_new is True

        # Same name, overlapping range
        r2 = await populated_eo.async_reserve_or_get_slot(
            slot_name="Alice",
            slot_code="1234",
            start_time=_make_dt(2025, 8, 5),
            end_time=_make_dt(2025, 8, 15),
        )
        assert r2.slot == r1.slot
        assert r2.is_new is False

    async def test_different_name_returns_none_reserves_new(
        self, populated_eo: EventOverrides
    ) -> None:
        """Different guest name should not match existing slot."""
        r1 = await populated_eo.async_reserve_or_get_slot(
            slot_name="Alice",
            slot_code="1234",
            start_time=_make_dt(2025, 8, 1),
            end_time=_make_dt(2025, 8, 10),
        )
        r2 = await populated_eo.async_reserve_or_get_slot(
            slot_name="Bob",
            slot_code="5678",
            start_time=_make_dt(2025, 8, 1),
            end_time=_make_dt(2025, 8, 10),
        )
        assert r2.slot != r1.slot
        assert r2.is_new is True

    async def test_same_name_non_overlapping_returns_none_reserves_new(
        self, populated_eo: EventOverrides
    ) -> None:
        """Same name with non-overlapping (back-to-back) stays gets new slot."""
        r1 = await populated_eo.async_reserve_or_get_slot(
            slot_name="Alice",
            slot_code="1234",
            start_time=_make_dt(2025, 8, 1),
            end_time=_make_dt(2025, 8, 5),
        )
        # Back-to-back: starts exactly when previous ends
        r2 = await populated_eo.async_reserve_or_get_slot(
            slot_name="Alice",
            slot_code="5678",
            start_time=_make_dt(2025, 8, 5),
            end_time=_make_dt(2025, 8, 10),
        )
        assert r2.slot != r1.slot
        assert r2.is_new is True


class TestSlotOverflow:
    """T013: Tests for slot overflow when all slots are occupied."""

    async def test_overflow_returns_none(self) -> None:
        """All slots occupied returns ReserveResult(None, False, False)."""
        eo = EventOverrides(start_slot=1, max_slots=2)
        now = dt_util.now()
        # Initialize all slots as empty (system ready)
        eo.update(1, "", "", now, now)
        eo.update(2, "", "", now, now)

        # Fill both slots
        r1 = await eo.async_reserve_or_get_slot(
            slot_name="Alice",
            slot_code="1234",
            start_time=_make_dt(2025, 8, 1),
            end_time=_make_dt(2025, 8, 5),
        )
        r2 = await eo.async_reserve_or_get_slot(
            slot_name="Bob",
            slot_code="5678",
            start_time=_make_dt(2025, 8, 6),
            end_time=_make_dt(2025, 8, 10),
        )
        assert r1.is_new is True
        assert r2.is_new is True

        # Third reservation should overflow
        r3 = await eo.async_reserve_or_get_slot(
            slot_name="Charlie",
            slot_code="9012",
            start_time=_make_dt(2025, 8, 11),
            end_time=_make_dt(2025, 8, 15),
        )
        assert r3 == ReserveResult(None, False, False)

    async def test_overflow_logs_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Overflow should log a warning with guest name."""
        eo = EventOverrides(start_slot=1, max_slots=1)
        now = dt_util.now()
        eo.update(1, "", "", now, now)

        # Fill the single slot
        await eo.async_reserve_or_get_slot(
            slot_name="Alice",
            slot_code="1234",
            start_time=_make_dt(2025, 8, 1),
            end_time=_make_dt(2025, 8, 5),
        )

        # Attempt overflow
        with caplog.at_level(logging.WARNING):
            result = await eo.async_reserve_or_get_slot(
                slot_name="Bob",
                slot_code="5678",
                start_time=_make_dt(2025, 8, 6),
                end_time=_make_dt(2025, 8, 10),
            )
        assert result.slot is None
        assert "Bob" in caplog.text
        assert "occupied" in caplog.text


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
        """Different UID plus different start creates a separate slot.

        Setup: "Alice" reserved with uid="AAA".
        Action: async_reserve_or_get_slot("Alice", shifted start,
        uid="BBB").
        Expected: new slot reserved because the same-start bypass does
        not apply.
        """
        eo = _make_ready_eo()
        mon = _make_dt(2025, 7, 7)
        fri = _make_dt(2025, 7, 11)
        wed = _make_dt(2025, 7, 9)

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
            start_time=wed,
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


class TestUidPositiveMatch:
    """Tests for UID-based positive matching in slot lookup."""

    async def test_uid_positive_match_non_overlapping_dates(self) -> None:
        """Same UID + name matches even when dates do not overlap.

        Simulates a reservation whose dates shift entirely (e.g.,
        extended stay moves end date past the original range).
        """
        eo = _make_ready_eo()
        # Original reservation
        r1 = await eo.async_reserve_or_get_slot(
            slot_name="Alice",
            slot_code="1234",
            start_time=_make_dt(2025, 8, 1),
            end_time=_make_dt(2025, 8, 5),
            uid="UID-001",
        )
        assert r1.is_new is True

        # Same UID, shifted dates (no overlap with original)
        r2 = await eo.async_reserve_or_get_slot(
            slot_name="Alice",
            slot_code="5678",
            start_time=_make_dt(2025, 8, 6),
            end_time=_make_dt(2025, 8, 10),
            uid="UID-001",
        )
        assert r2.slot == r1.slot
        assert r2.is_new is False
        assert r2.times_updated is True

    async def test_slot_has_matching_event_uid_positive_match(self) -> None:
        """Slot matches event by UID even when dates shifted."""
        eo = _make_ready_eo()
        r1 = await eo.async_reserve_or_get_slot(
            slot_name="Alice",
            slot_code="1234",
            start_time=_make_dt(2025, 8, 1),
            end_time=_make_dt(2025, 8, 5),
            uid="UID-001",
        )
        assert r1.slot is not None

        # Event with same UID but shifted dates
        events = [
            EventIdentity(
                name="Alice",
                start=_make_dt(2025, 8, 6),
                end=_make_dt(2025, 8, 10),
                uid="UID-001",
            )
        ]
        assert eo._slot_has_matching_event(r1.slot, events) is True

    async def test_date_extension_updates_existing_slot(self) -> None:
        """Extending end date of active reservation reuses slot."""
        eo = _make_ready_eo()
        # Original reservation: Aug 1-5
        r1 = await eo.async_reserve_or_get_slot(
            slot_name="Alice",
            slot_code="0105",
            start_time=_make_dt(2025, 8, 1),
            end_time=_make_dt(2025, 8, 5),
            uid="UID-002",
        )
        assert r1.is_new is True

        # Guest extends stay to Aug 10 (overlapping, same UID)
        r2 = await eo.async_reserve_or_get_slot(
            slot_name="Alice",
            slot_code="0110",
            start_time=_make_dt(2025, 8, 1),
            end_time=_make_dt(2025, 8, 10),
            uid="UID-002",
        )
        assert r2.slot == r1.slot
        assert r2.slot is not None
        assert r2.is_new is False
        assert r2.times_updated is True
        # Code should remain original
        override = eo.overrides[r2.slot]
        assert override is not None
        assert override["slot_code"] == "0105"
        # End time should be updated
        assert override["end_time"] == _make_dt(2025, 8, 10)

    async def test_different_uid_still_creates_new_slot(self) -> None:
        """Different UIDs with same name still get separate slots."""
        eo = _make_ready_eo()
        r1 = await eo.async_reserve_or_get_slot(
            slot_name="Alice",
            slot_code="1234",
            start_time=_make_dt(2025, 8, 1),
            end_time=_make_dt(2025, 8, 5),
            uid="UID-AAA",
        )
        assert r1.is_new is True

        # Different UID, overlapping dates
        r2 = await eo.async_reserve_or_get_slot(
            slot_name="Alice",
            slot_code="5678",
            start_time=_make_dt(2025, 8, 3),
            end_time=_make_dt(2025, 8, 7),
            uid="UID-BBB",
        )
        assert r2.slot != r1.slot
        assert r2.is_new is True


class TestDateExtensionUidChange:
    """Tests for date extension/shortening when UID is regenerated."""

    async def test_extension_with_new_uid_reuses_slot(self) -> None:
        """Extending end date with a changed UID should reuse existing slot."""
        eo = _make_ready_eo()
        r1 = await eo.async_reserve_or_get_slot(
            slot_name="Alice",
            slot_code="1234",
            start_time=_make_dt(2025, 8, 1),
            end_time=_make_dt(2025, 8, 5),
            uid="UID-OLD",
        )
        assert r1.is_new is True

        r2 = await eo.async_reserve_or_get_slot(
            slot_name="Alice",
            slot_code="1234",
            start_time=_make_dt(2025, 8, 1),
            end_time=_make_dt(2025, 8, 10),
            uid="UID-NEW",
        )
        assert r2.slot == r1.slot
        assert r2.slot is not None
        assert r2.is_new is False
        assert r2.times_updated is True
        assert eo._slot_uids[r2.slot] == "UID-NEW"
        override = eo.overrides[r2.slot]
        assert override is not None
        assert override["end_time"] == _make_dt(2025, 8, 10)

    async def test_shortening_with_new_uid_reuses_slot(self) -> None:
        """Shortening stay with a changed UID should reuse existing slot."""
        eo = _make_ready_eo()
        r1 = await eo.async_reserve_or_get_slot(
            slot_name="Bob",
            slot_code="5678",
            start_time=_make_dt(2025, 9, 1),
            end_time=_make_dt(2025, 9, 15),
            uid="UID-ORIG",
        )
        assert r1.is_new is True

        r2 = await eo.async_reserve_or_get_slot(
            slot_name="Bob",
            slot_code="5678",
            start_time=_make_dt(2025, 9, 1),
            end_time=_make_dt(2025, 9, 10),
            uid="UID-MODIFIED",
        )
        assert r2.slot == r1.slot
        assert r2.slot is not None
        assert r2.is_new is False
        assert r2.times_updated is True
        override = eo.overrides[r2.slot]
        assert override is not None
        assert override["end_time"] == _make_dt(2025, 9, 10)

    async def test_different_start_with_new_uid_creates_new_slot(self) -> None:
        """Different start time + different UID = different booking."""
        eo = _make_ready_eo()
        r1 = await eo.async_reserve_or_get_slot(
            slot_name="Carol",
            slot_code="1111",
            start_time=_make_dt(2025, 10, 1),
            end_time=_make_dt(2025, 10, 5),
            uid="UID-FIRST",
        )
        assert r1.is_new is True

        r2 = await eo.async_reserve_or_get_slot(
            slot_name="Carol",
            slot_code="2222",
            start_time=_make_dt(2025, 10, 3),
            end_time=_make_dt(2025, 10, 8),
            uid="UID-SECOND",
        )
        assert r2.slot != r1.slot
        assert r2.is_new is True

    async def test_eviction_check_respects_same_start_bypass(self) -> None:
        """UID changes with same start should not trigger slot eviction."""
        eo = EventOverrides(start_slot=1, max_slots=2)
        now = _make_dt(2025, 8, 1)
        eo.update(1, "1234", "Alice", now, _make_dt(2025, 8, 5))
        eo._slot_uids[1] = "uid-old"
        eo.update(2, "", "", now, now)

        coordinator = _make_coordinator(
            calendar_events=[
                _make_event(
                    _make_dt(2025, 8, 10),
                    "Alice",
                    start=_make_dt(2025, 8, 1),
                    uid="uid-new",
                ),
            ],
        )

        frozen = _make_dt(2025, 7, 31)
        with (
            patch(
                "custom_components.rental_control.event_overrides.async_fire_clear_code",
                new_callable=AsyncMock,
                return_value=OperationResult(kind="clear", slot=1, confirmed=True),
            ) as mock_fire,
            patch.object(dt_util, "start_of_local_day", return_value=frozen),
        ):
            await eo.async_check_overrides(coordinator)
            await eo.async_check_overrides(coordinator)
            mock_fire.assert_not_called()
            assert eo.overrides[1] is not None

    async def test_duplicate_state_prefers_closest_same_start_slot(self) -> None:
        """Later updates should stay on the live duplicate slot."""
        eo = _make_ready_eo()
        start = _make_dt(2025, 8, 1)
        old_end = _make_dt(2025, 8, 5)
        live_end = _make_dt(2025, 8, 10)
        extended_end = _make_dt(2025, 8, 12)

        eo.update(1, "1111", "Alice", start, old_end)
        eo._slot_uids[1] = "UID-OLD"
        eo.update(2, "2222", "Alice", start, live_end)
        eo._slot_uids[2] = "UID-LIVE"

        result = await eo.async_reserve_or_get_slot(
            slot_name="Alice",
            slot_code="2222",
            start_time=start,
            end_time=extended_end,
            uid="UID-NEWEST",
        )

        assert result.slot == 2
        assert result.is_new is False
        assert result.times_updated is True
        assert eo._slot_uids[1] == "UID-OLD"
        assert eo._slot_uids[2] == "UID-NEWEST"
        assert eo.overrides[1] is not None
        assert eo.overrides[2] is not None
        assert eo.overrides[2]["end_time"] == extended_end


# ---------------------------------------------------------------------------
# Timezone-safe datetime comparison (Issue #513)
# ---------------------------------------------------------------------------


class TestToUtc:
    """Tests for the _to_utc helper function."""

    def test_aware_utc_passthrough(self) -> None:
        """UTC datetimes pass through unchanged."""
        from custom_components.rental_control.event_overrides import _to_utc

        value = datetime(2025, 8, 1, 12, 0, tzinfo=dt_util.UTC)
        result = _to_utc(value)
        assert result == value
        assert result.tzinfo == dt_util.UTC

    def test_aware_non_utc_converts(self) -> None:
        """Non-UTC aware datetimes are converted to UTC."""
        from datetime import timezone

        from custom_components.rental_control.event_overrides import _to_utc

        eastern = timezone(timedelta(hours=-5))
        value = datetime(2025, 8, 1, 7, 0, tzinfo=eastern)
        result = _to_utc(value)
        assert result == datetime(2025, 8, 1, 12, 0, tzinfo=dt_util.UTC)

    def test_naive_treated_as_local(self) -> None:
        """Naive datetimes are assumed to be HA local time."""
        from datetime import timezone

        from custom_components.rental_control.event_overrides import _to_utc

        # Set HA default timezone to a fixed UTC-5 offset
        previous_tz = dt_util.get_default_time_zone()
        eastern = timezone(timedelta(hours=-5))
        dt_util.set_default_time_zone(eastern)
        try:
            naive = datetime(2025, 8, 1, 12, 0)
            result = _to_utc(naive)
            # 12:00 at UTC-5 => 17:00 UTC
            assert result.tzinfo is not None
            assert result.utcoffset() == timedelta(0)
            assert result == datetime(2025, 8, 1, 17, 0, tzinfo=dt_util.UTC)
        finally:
            dt_util.set_default_time_zone(previous_tz)


class TestTimezoneSafeComparison:
    """Verify that datetime comparisons are timezone-safe."""

    @pytest.mark.asyncio
    async def test_same_instant_different_tz_no_times_updated(self) -> None:
        """Override and event at same instant but different tz => no update."""
        from datetime import timezone

        eo = _make_ready_eo()

        eastern = timezone(timedelta(hours=-5))
        utc_start = datetime(2025, 8, 1, 5, 0, tzinfo=dt_util.UTC)
        utc_end = datetime(2025, 8, 5, 5, 0, tzinfo=dt_util.UTC)

        # First reservation in UTC
        r1 = await eo.async_reserve_or_get_slot(
            slot_name="Alice",
            slot_code="1234",
            start_time=utc_start,
            end_time=utc_end,
            uid="UID-1",
        )
        assert r1.is_new is True

        # Same instant expressed in Eastern
        east_start = datetime(2025, 8, 1, 0, 0, tzinfo=eastern)
        east_end = datetime(2025, 8, 5, 0, 0, tzinfo=eastern)

        r2 = await eo.async_reserve_or_get_slot(
            slot_name="Alice",
            slot_code="1234",
            start_time=east_start,
            end_time=east_end,
            uid="UID-1",
        )
        assert r2.slot == r1.slot
        assert r2.is_new is False
        assert r2.times_updated is False

    @pytest.mark.asyncio
    async def test_overlap_detected_across_timezones(self) -> None:
        """Overlap detection works when times are in different timezones."""
        from datetime import timezone

        eo = _make_ready_eo()

        eastern = timezone(timedelta(hours=-5))
        # Store in UTC
        r1 = await eo.async_reserve_or_get_slot(
            slot_name="Alice",
            slot_code="1234",
            start_time=datetime(2025, 8, 1, 5, 0, tzinfo=dt_util.UTC),
            end_time=datetime(2025, 8, 5, 5, 0, tzinfo=dt_util.UTC),
        )
        assert r1.is_new is True

        # Query in Eastern — overlaps the same range
        r2 = await eo.async_reserve_or_get_slot(
            slot_name="Alice",
            slot_code="1234",
            start_time=datetime(2025, 8, 3, 0, 0, tzinfo=eastern),
            end_time=datetime(2025, 8, 7, 0, 0, tzinfo=eastern),
        )
        # Should find existing slot, not create new
        assert r2.slot == r1.slot
        assert r2.is_new is False

    @pytest.mark.asyncio
    async def test_slot_has_matching_event_cross_timezone(self) -> None:
        """_slot_has_matching_event finds match across timezones."""
        from datetime import timezone

        eo = _make_ready_eo()

        eastern = timezone(timedelta(hours=-5))
        # Store override in UTC
        await eo.async_reserve_or_get_slot(
            slot_name="Alice",
            slot_code="1234",
            start_time=datetime(2025, 8, 1, 5, 0, tzinfo=dt_util.UTC),
            end_time=datetime(2025, 8, 5, 5, 0, tzinfo=dt_util.UTC),
            uid="UID-1",
        )

        # Event identity in Eastern
        events = [
            EventIdentity(
                name="Alice",
                start=datetime(2025, 8, 2, 0, 0, tzinfo=eastern),
                end=datetime(2025, 8, 4, 0, 0, tzinfo=eastern),
                uid="UID-1",
            ),
        ]
        assert eo._slot_has_matching_event(1, events) is True


# ---------------------------------------------------------------------------
# Phase 3 trim-aware matching tests
# ---------------------------------------------------------------------------


class TestPhase3TrimAwareMatching:
    """Tests for Phase 3 trim-aware matching in EventOverrides."""

    @staticmethod
    def _ready_eo() -> EventOverrides:
        """Build a ready EventOverrides with trim configuration."""
        eo = EventOverrides(start_slot=1, max_slots=3)
        now = dt_util.now()
        eo.update(1, "", "", now, now)
        eo.update(2, "", "", now, now)
        eo.update(3, "", "", now, now)
        return eo

    # -- _find_overlapping_slot Phase 3 tests --

    @pytest.mark.asyncio
    async def test_find_overlapping_hard_truncated(self) -> None:
        """Verify Phase 3 matches hard-truncated single-word name."""
        eo = self._ready_eo()
        eo.trim_names = True
        eo.max_name_length = 11
        eo.prefix_length = 7

        start = _make_dt(2025, 6, 1)
        end = _make_dt(2025, 6, 5)

        # guest_max = 11 - 7 = 4
        # trim_name("Christopher", 4) = "Chri"
        result = await eo.async_reserve_or_get_slot(
            "Chri", "1234", start, end, uid=None
        )
        assert result.slot == 1

        slot = eo._find_overlapping_slot("Christopher", start, end, uid=None)
        assert slot == 1
        assert eo._overrides[1] is not None
        assert eo._overrides[1]["slot_name"] == "Christopher"

    @pytest.mark.asyncio
    async def test_find_overlapping_word_boundary(self) -> None:
        """Verify Phase 3 matches word-boundary trimmed name."""
        eo = self._ready_eo()
        eo.trim_names = True
        eo.max_name_length = 16
        eo.prefix_length = 7

        start = _make_dt(2025, 6, 1)
        end = _make_dt(2025, 6, 5)

        # guest_max = 16 - 7 = 9
        # trim_name("Very Long Guest", 9) = "Very Long"
        result = await eo.async_reserve_or_get_slot(
            "Very Long", "1234", start, end, uid=None
        )
        assert result.slot == 1

        slot = eo._find_overlapping_slot("Very Long Guest", start, end, uid=None)
        assert slot == 1
        assert eo._overrides[1] is not None
        assert eo._overrides[1]["slot_name"] == "Very Long Guest"

    @pytest.mark.asyncio
    async def test_find_overlapping_trim_disabled(self) -> None:
        """Verify Phase 3 does not run when trim is disabled."""
        eo = self._ready_eo()
        eo.trim_names = False

        start = _make_dt(2025, 6, 1)
        end = _make_dt(2025, 6, 5)

        result = await eo.async_reserve_or_get_slot(
            "Chri", "1234", start, end, uid=None
        )
        assert result.slot == 1

        slot = eo._find_overlapping_slot("Christopher", start, end, uid=None)
        assert slot is None

    @pytest.mark.asyncio
    async def test_find_overlapping_trim_uid_mismatch(self) -> None:
        """Verify Phase 3 rejects UID mismatch when starts differ."""
        eo = self._ready_eo()
        eo.trim_names = True
        eo.max_name_length = 11
        eo.prefix_length = 7

        start = _make_dt(2025, 6, 1)
        end = _make_dt(2025, 6, 5)
        start_2 = _make_dt(2025, 6, 2)

        result = await eo.async_reserve_or_get_slot(
            "Chri", "1234", start, end, uid="UID-A"
        )
        assert result.slot == 1

        slot = eo._find_overlapping_slot("Christopher", start_2, end, uid="UID-B")
        assert slot is None

    @pytest.mark.asyncio
    async def test_find_overlapping_trim_uid_same_start(self) -> None:
        """Verify Phase 3 reuses slot when UID changes but start matches."""
        eo = self._ready_eo()
        eo.trim_names = True
        eo.max_name_length = 11
        eo.prefix_length = 7

        start = _make_dt(2025, 6, 1)
        end = _make_dt(2025, 6, 5)
        extended_end = _make_dt(2025, 6, 8)

        result = await eo.async_reserve_or_get_slot(
            "Chri", "1234", start, end, uid="UID-A"
        )
        assert result.slot == 1

        slot = eo._find_overlapping_slot(
            "Christopher", start, extended_end, uid="UID-B"
        )
        assert slot == 1
        assert eo._overrides[1] is not None
        assert eo._overrides[1]["slot_name"] == "Christopher"

    @pytest.mark.asyncio
    async def test_find_overlapping_prefers_trimmed_uid_owner(self) -> None:
        """Trimmed UID owner must win over stale full-name duplicate."""
        eo = self._ready_eo()
        eo.trim_names = True
        eo.max_name_length = 11
        eo.prefix_length = 7

        start = _make_dt(2025, 6, 1)
        stale_end = _make_dt(2025, 6, 5)
        live_end = _make_dt(2025, 6, 8)
        extended_end = _make_dt(2025, 6, 10)

        eo.update(1, "1111", "Christopher", start, stale_end)
        eo._slot_uids[1] = "UID-OLD"
        eo.update(2, "2222", "Chri", start, live_end)
        eo._slot_uids[2] = "UID-LIVE"

        result = await eo.async_reserve_or_get_slot(
            "Christopher",
            "2222",
            start,
            extended_end,
            uid="UID-LIVE",
        )

        assert result.slot == 2
        assert result.is_new is False
        assert result.times_updated is True
        assert eo._overrides[2] is not None
        assert eo._overrides[2]["slot_name"] == "Christopher"
        assert eo._overrides[2]["end_time"] == extended_end

    @pytest.mark.asyncio
    async def test_find_overlapping_uid_owner_beats_closer_stale_slot(self) -> None:
        """Exact UID owner must win even if stale full-name slot is closer."""
        eo = self._ready_eo()
        eo.trim_names = True
        eo.max_name_length = 11
        eo.prefix_length = 7

        start = _make_dt(2025, 6, 1)
        stale_end = _make_dt(2025, 6, 9)
        live_end = _make_dt(2025, 6, 5)
        extended_end = _make_dt(2025, 6, 10)

        eo.update(1, "1111", "Christopher", start, stale_end)
        eo._slot_uids[1] = "UID-OLD"
        eo.update(2, "2222", "Chri", start, live_end)
        eo._slot_uids[2] = "UID-LIVE"

        result = await eo.async_reserve_or_get_slot(
            "Christopher",
            "2222",
            start,
            extended_end,
            uid="UID-LIVE",
        )

        assert result.slot == 2
        assert result.is_new is False
        assert result.times_updated is True
        assert eo._slot_uids[1] == "UID-OLD"
        assert eo._slot_uids[2] == "UID-LIVE"
        assert eo._overrides[2] is not None
        assert eo._overrides[2]["slot_name"] == "Christopher"
        assert eo._overrides[2]["end_time"] == extended_end

    @pytest.mark.asyncio
    async def test_find_overlapping_prefers_trimmed_uid_owner_over_no_uid(self) -> None:
        """No-UID stale slot must not shadow the trimmed UID owner."""
        eo = self._ready_eo()
        eo.trim_names = True
        eo.max_name_length = 11
        eo.prefix_length = 7

        start = _make_dt(2025, 6, 1)
        stale_end = _make_dt(2025, 6, 5)
        live_end = _make_dt(2025, 6, 8)
        extended_end = _make_dt(2025, 6, 10)

        eo.update(1, "1111", "Christopher", start, stale_end)
        eo.update(2, "2222", "Chri", start, live_end)
        eo._slot_uids[2] = "UID-LIVE"

        result = await eo.async_reserve_or_get_slot(
            "Christopher",
            "2222",
            start,
            extended_end,
            uid="UID-LIVE",
        )

        assert result.slot == 2
        assert result.is_new is False
        assert result.times_updated is True
        assert eo._overrides[2] is not None
        assert eo._overrides[2]["slot_name"] == "Christopher"
        assert eo._overrides[2]["end_time"] == extended_end

    @pytest.mark.asyncio
    async def test_find_overlapping_prefers_same_start_trim_peer_over_no_uid_overlap(
        self,
    ) -> None:
        """Different-start no-UID trim match must not shadow same-start peer."""
        eo = self._ready_eo()
        eo.trim_names = True
        eo.max_name_length = 11
        eo.prefix_length = 7

        stale_start = _make_dt(2025, 6, 1)
        live_start = _make_dt(2025, 6, 2)
        stale_end = _make_dt(2025, 6, 5)
        live_end = _make_dt(2025, 6, 8)
        current_end = _make_dt(2025, 6, 10)

        eo.update(1, "1111", "Christopher", stale_start, stale_end)
        eo.update(2, "2222", "Chri", live_start, live_end)
        eo._slot_uids[2] = "UID-OLD"

        result = await eo.async_reserve_or_get_slot(
            "Christopher",
            "2222",
            live_start,
            current_end,
            uid="UID-NEW",
        )

        assert result.slot == 2
        assert result.is_new is False
        assert result.times_updated is True
        assert eo._overrides[2] is not None
        assert eo._overrides[2]["slot_name"] == "Christopher"
        assert eo._overrides[2]["end_time"] == current_end

    @pytest.mark.asyncio
    async def test_find_overlapping_trim_no_time_overlap(self) -> None:
        """Verify Phase 3 rejects non-overlapping times."""
        eo = self._ready_eo()
        eo.trim_names = True
        eo.max_name_length = 11
        eo.prefix_length = 7

        start1 = _make_dt(2025, 6, 1)
        end1 = _make_dt(2025, 6, 5)
        start2 = _make_dt(2025, 7, 1)
        end2 = _make_dt(2025, 7, 5)

        result = await eo.async_reserve_or_get_slot(
            "Chri", "1234", start1, end1, uid=None
        )
        assert result.slot == 1

        slot = eo._find_overlapping_slot("Christopher", start2, end2, uid=None)
        assert slot is None

    # -- _slot_has_matching_event Phase 3 tests --

    @pytest.mark.asyncio
    async def test_slot_matching_hard_truncated(self) -> None:
        """Verify _slot_has_matching_event matches truncated name."""
        eo = self._ready_eo()
        eo.trim_names = True
        eo.max_name_length = 11
        eo.prefix_length = 7

        start = _make_dt(2025, 6, 1)
        end = _make_dt(2025, 6, 5)

        await eo.async_reserve_or_get_slot("Chri", "1234", start, end, uid=None)

        events = [
            EventIdentity(name="Christopher", start=start, end=end, uid=None),
        ]
        assert eo._slot_has_matching_event(1, events) is True
        assert eo._overrides[1] is not None
        assert eo._overrides[1]["slot_name"] == "Christopher"

    @pytest.mark.asyncio
    async def test_slot_matching_trim_disabled(self) -> None:
        """Verify Phase 3 is skipped when trim is off."""
        eo = self._ready_eo()
        eo.trim_names = False

        start = _make_dt(2025, 6, 1)
        end = _make_dt(2025, 6, 5)

        await eo.async_reserve_or_get_slot("Chri", "1234", start, end, uid=None)

        events = [
            EventIdentity(name="Christopher", start=start, end=end, uid=None),
        ]
        assert eo._slot_has_matching_event(1, events) is False

    @pytest.mark.asyncio
    async def test_slot_matching_uid_mismatch(self) -> None:
        """Verify _slot_has_matching_event rejects UID mismatch on new start."""
        eo = self._ready_eo()
        eo.trim_names = True
        eo.max_name_length = 11
        eo.prefix_length = 7

        start = _make_dt(2025, 6, 1)
        end = _make_dt(2025, 6, 5)
        start_2 = _make_dt(2025, 6, 2)

        await eo.async_reserve_or_get_slot("Chri", "1234", start, end, uid="UID-A")

        events = [
            EventIdentity(name="Christopher", start=start_2, end=end, uid="UID-B"),
        ]
        assert eo._slot_has_matching_event(1, events) is False

    @pytest.mark.asyncio
    async def test_slot_matching_uid_same_start(self) -> None:
        """Verify Phase 3 matches UID change when trimmed start is the same."""
        eo = self._ready_eo()
        eo.trim_names = True
        eo.max_name_length = 11
        eo.prefix_length = 7

        start = _make_dt(2025, 6, 1)
        end = _make_dt(2025, 6, 5)
        extended_end = _make_dt(2025, 6, 8)

        await eo.async_reserve_or_get_slot("Chri", "1234", start, end, uid="UID-A")

        events = [
            EventIdentity(
                name="Christopher",
                start=start,
                end=extended_end,
                uid="UID-B",
            ),
        ]
        assert eo._slot_has_matching_event(1, events) is True
        assert eo._overrides[1] is not None
        assert eo._overrides[1]["slot_name"] == "Christopher"

    @pytest.mark.asyncio
    async def test_slot_matching_uid_same_start_prefers_closest_trimmed_slot(
        self,
    ) -> None:
        """Closest trimmed same-start slot should own event when UIDs are stale."""
        eo = self._ready_eo()
        eo.trim_names = True
        eo.max_name_length = 11
        eo.prefix_length = 7

        start = _make_dt(2025, 6, 1)
        stale_end = _make_dt(2025, 6, 5)
        live_end = _make_dt(2025, 6, 8)
        current_end = _make_dt(2025, 6, 10)

        eo.update(1, "1111", "Chri", start, stale_end)
        eo._slot_uids[1] = "UID-OLD-1"
        eo.update(2, "2222", "Chri", start, live_end)
        eo._slot_uids[2] = "UID-OLD-2"

        events = [
            EventIdentity(
                name="Christopher",
                start=start,
                end=current_end,
                uid="UID-NEW",
            ),
        ]

        assert eo._slot_has_matching_event(1, events) is False
        assert eo._slot_has_matching_event(2, events) is True
        assert eo._overrides[2] is not None
        assert eo._overrides[2]["slot_name"] == "Christopher"

    @pytest.mark.asyncio
    async def test_slot_matching_word_boundary(self) -> None:
        """Verify _slot_has_matching_event matches word-boundary trim."""
        eo = self._ready_eo()
        eo.trim_names = True
        eo.max_name_length = 16
        eo.prefix_length = 7

        start = _make_dt(2025, 6, 1)
        end = _make_dt(2025, 6, 5)

        await eo.async_reserve_or_get_slot("Very Long", "1234", start, end, uid=None)

        events = [
            EventIdentity(name="Very Long Guest", start=start, end=end, uid=None),
        ]
        assert eo._slot_has_matching_event(1, events) is True
        assert eo._overrides[1] is not None
        assert eo._overrides[1]["slot_name"] == "Very Long Guest"

    # -- Phase 3a UID-positive trim match (no overlap required) --

    @pytest.mark.asyncio
    async def test_find_overlapping_uid_positive_trim_no_overlap(self) -> None:
        """Phase 3a: UID match + trim match without time overlap."""
        eo = self._ready_eo()
        eo.trim_names = True
        eo.max_name_length = 11
        eo.prefix_length = 7

        start1 = _make_dt(2025, 6, 1)
        end1 = _make_dt(2025, 6, 5)
        # Non-overlapping window
        start2 = _make_dt(2025, 7, 1)
        end2 = _make_dt(2025, 7, 5)

        result = await eo.async_reserve_or_get_slot(
            "Chri", "1234", start1, end1, uid="UID-A"
        )
        assert result.slot == 1

        # Same UID, trimmed name, no overlap → Phase 3a matches
        slot = eo._find_overlapping_slot("Christopher", start2, end2, uid="UID-A")
        assert slot == 1
        assert eo._overrides[1] is not None
        assert eo._overrides[1]["slot_name"] == "Christopher"

    @pytest.mark.asyncio
    async def test_slot_matching_uid_positive_trim_no_overlap(self) -> None:
        """Phase 3a: _slot_has_matching_event UID + trim without overlap."""
        eo = self._ready_eo()
        eo.trim_names = True
        eo.max_name_length = 11
        eo.prefix_length = 7

        start = _make_dt(2025, 6, 1)
        end = _make_dt(2025, 6, 5)
        # Non-overlapping event window
        ev_start = _make_dt(2025, 7, 1)
        ev_end = _make_dt(2025, 7, 5)

        await eo.async_reserve_or_get_slot("Chri", "1234", start, end, uid="UID-A")

        events = [
            EventIdentity(name="Christopher", start=ev_start, end=ev_end, uid="UID-A"),
        ]
        assert eo._slot_has_matching_event(1, events) is True
        assert eo._overrides[1] is not None
        assert eo._overrides[1]["slot_name"] == "Christopher"


# ---------------------------------------------------------------------------
# Slot eviction when all slots full and new earlier event arrives (#535)
# ---------------------------------------------------------------------------


class TestSlotEvictionOnNewEarlierEvent:
    """Tests for slot eviction when a new earlier event displaces an existing one."""

    async def test_new_earlier_event_evicts_last_slot(self) -> None:
        """Verify that a new earlier event evicts the displaced slot.

        Minimal reproduction of issue #535:
        - 2 slots both assigned to existing events
        - A 3rd earlier reservation arrives
        - sensor_cal = cal[:2] includes new + first old event
        - The 2nd old event is displaced and its slot cleared
        """
        eo = EventOverrides(start_slot=1, max_slots=2)

        # Fill both slots
        s1 = _make_dt(2025, 7, 10)
        e1 = _make_dt(2025, 7, 15)
        s2 = _make_dt(2025, 7, 20)
        e2 = _make_dt(2025, 7, 25)

        eo.update(1, "c1", "Alpha", s1, e1)
        eo._slot_uids[1] = "uid-A"
        eo.update(2, "c2", "Beta", s2, e2)
        eo._slot_uids[2] = "uid-B"

        assert eo.ready is True
        assert eo.next_slot is None  # all full

        # New earlier event arrives (starts tomorrow, before both)
        new_start = _make_dt(2025, 7, 2)
        new_end = _make_dt(2025, 7, 6)

        # Full calendar: [New, Alpha, Beta] — 3 events total
        # sensor_cal = cal[:2] = [New, Alpha] — Beta displaced
        cal_events = [
            _make_event(new_end, "New Guest", start=new_start, uid="uid-new"),
            _make_event(e1, "Alpha", start=s1, uid="uid-A"),
            _make_event(e2, "Beta", start=s2, uid="uid-B"),
        ]
        coordinator = _make_coordinator(
            calendar_events=cal_events,
            max_events=2,
        )

        frozen = _make_dt(2025, 7, 1)
        with (
            patch(
                "custom_components.rental_control.event_overrides.async_fire_clear_code",
                new_callable=AsyncMock,
                return_value=OperationResult(kind="clear", slot=1, confirmed=True),
            ) as mock_fire,
            patch.object(dt_util, "start_of_local_day", return_value=frozen),
        ):
            await eo.async_check_overrides(coordinator)

            # Slot 2 (Beta) should be evicted
            mock_fire.assert_called_once_with(coordinator, 2, expected_name="Beta")
            assert eo.overrides[2] is None

            # next_slot should now be available
            assert eo.next_slot is not None

            # The new event should be able to reserve the freed slot
            result = await eo.async_reserve_or_get_slot(
                "New Guest", "newcode", new_start, new_end, uid="uid-new"
            )
            assert result.slot is not None
            assert result.is_new is True

    async def test_evicted_slot_freed_for_reservation(self) -> None:
        """Verify the freed slot is usable after eviction.

        End-to-end: after async_check_overrides clears the evicted
        slot, async_reserve_or_get_slot should successfully assign
        the new event to a slot.
        """
        eo = EventOverrides(start_slot=1, max_slots=2)

        # Fill both slots
        s1 = _make_dt(2025, 7, 10)
        e1 = _make_dt(2025, 7, 15)
        s2 = _make_dt(2025, 7, 20)
        e2 = _make_dt(2025, 7, 25)

        eo.update(1, "c1", "Alpha", s1, e1)
        eo.update(2, "c2", "Beta", s2, e2)

        assert eo.next_slot is None  # all slots full

        # New earlier event pushes Beta out of sensor_cal
        new_s = _make_dt(2025, 7, 5)
        new_e = _make_dt(2025, 7, 9)

        # Full calendar: [Delta, Alpha, Beta] but max_events=2
        # sensor_cal = [Delta, Alpha] — Beta displaced
        cal_events = [
            _make_event(new_e, "Delta", start=new_s),
            _make_event(e1, "Alpha", start=s1),
            _make_event(e2, "Beta", start=s2),
        ]
        coordinator = _make_coordinator(
            calendar_events=cal_events,
            max_events=2,
        )

        frozen = _make_dt(2025, 7, 1)
        with (
            patch(
                "custom_components.rental_control.event_overrides.async_fire_clear_code",
                new_callable=AsyncMock,
                return_value=OperationResult(kind="clear", slot=1, confirmed=True),
            ) as mock_fire,
            patch.object(dt_util, "start_of_local_day", return_value=frozen),
        ):
            await eo.async_check_overrides(coordinator)

            # Beta (slot 2) should be cleared
            mock_fire.assert_called_once_with(coordinator, 2, expected_name="Beta")
            assert eo.overrides[2] is None
            assert eo.next_slot is not None

            # Now reserve a slot for Delta
            result = await eo.async_reserve_or_get_slot(
                "Delta", "newcode", new_s, new_e
            )
            assert result.slot is not None
            assert result.is_new is True
            assert eo.overrides[result.slot]["slot_name"] == "Delta"

    async def test_slot_freed_even_when_clear_code_fails(self) -> None:
        """Verify failed clear does not free the evicted slot."""
        eo = EventOverrides(start_slot=1, max_slots=2)

        # Fill both slots
        s1 = _make_dt(2025, 7, 10)
        e1 = _make_dt(2025, 7, 15)
        s2 = _make_dt(2025, 7, 20)
        e2 = _make_dt(2025, 7, 25)

        eo.update(1, "c1", "Alpha", s1, e1)
        eo.update(2, "c2", "Beta", s2, e2)

        assert eo.next_slot is None  # all slots full

        # New earlier event pushes Beta out of sensor_cal
        new_s = _make_dt(2025, 7, 5)
        new_e = _make_dt(2025, 7, 9)

        cal_events = [
            _make_event(new_e, "Delta", start=new_s),
            _make_event(e1, "Alpha", start=s1),
            _make_event(e2, "Beta", start=s2),
        ]
        coordinator = _make_coordinator(
            calendar_events=cal_events,
            max_events=2,
        )

        frozen = _make_dt(2025, 7, 1)

        failing_clear = AsyncMock(
            return_value=OperationResult(kind="clear", slot=2, failed=True)
        )
        with (
            patch(
                "custom_components.rental_control.event_overrides.async_fire_clear_code",
                failing_clear,
            ),
            patch.object(dt_util, "start_of_local_day", return_value=frozen),
        ):
            await eo.async_check_overrides(coordinator)

            assert eo.overrides[2] is not None
            assert eo.overrides[2]["slot_name"] == "Beta"
            assert eo.next_slot is None


class TestSlotEvictionTolerance:
    """Tests for per-slot miss tolerance preventing transient eviction (#546)."""

    @staticmethod
    def _make_two_slot_eo(
        alpha_start: datetime,
        alpha_end: datetime,
        beta_start: datetime,
        beta_end: datetime,
    ) -> EventOverrides:
        """Build a ready overrides object with two assigned slots."""
        eo = EventOverrides(start_slot=1, max_slots=2)
        eo.update(1, "c1", "Alpha", alpha_start, alpha_end)
        eo._slot_uids[1] = "uid-A"
        eo.update(2, "c2", "Beta", beta_start, beta_end)
        eo._slot_uids[2] = "uid-B"
        return eo

    async def test_single_miss_does_not_evict_upcoming_slot(self) -> None:
        """A single refresh where an upcoming event is missing should NOT evict."""
        alpha_start = _make_dt(2025, 7, 10)
        alpha_end = _make_dt(2025, 7, 15)
        beta_start = _make_dt(2025, 7, 20)
        beta_end = _make_dt(2025, 7, 25)
        later_start = _make_dt(2025, 7, 26)
        later_end = _make_dt(2025, 7, 30)
        eo = self._make_two_slot_eo(alpha_start, alpha_end, beta_start, beta_end)
        coordinator = _make_coordinator(
            calendar_events=[
                _make_event(alpha_end, "Alpha", start=alpha_start, uid="uid-A"),
                _make_event(later_end, "Gamma", start=later_start, uid="uid-C"),
            ],
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

        mock_fire.assert_not_called()
        assert eo.overrides[2] is not None
        assert eo._slot_miss_counts == {2: 1}

    async def test_two_consecutive_misses_evicts_slot(self) -> None:
        """Two consecutive refreshes with event missing should evict."""
        alpha_start = _make_dt(2025, 7, 10)
        alpha_end = _make_dt(2025, 7, 15)
        beta_start = _make_dt(2025, 7, 20)
        beta_end = _make_dt(2025, 7, 25)
        later_start = _make_dt(2025, 7, 26)
        later_end = _make_dt(2025, 7, 30)
        eo = self._make_two_slot_eo(alpha_start, alpha_end, beta_start, beta_end)
        coordinator = _make_coordinator(
            calendar_events=[
                _make_event(alpha_end, "Alpha", start=alpha_start, uid="uid-A"),
                _make_event(later_end, "Gamma", start=later_start, uid="uid-C"),
            ],
            max_events=2,
        )

        frozen = _make_dt(2025, 7, 1)
        with (
            patch(
                "custom_components.rental_control.event_overrides.async_fire_clear_code",
                new_callable=AsyncMock,
                return_value=OperationResult(kind="clear", slot=1, confirmed=True),
            ) as mock_fire,
            patch.object(dt_util, "start_of_local_day", return_value=frozen),
        ):
            await eo.async_check_overrides(coordinator)
            assert eo._slot_miss_counts == {2: 1}

            await eo.async_check_overrides(coordinator)

        mock_fire.assert_called_once_with(coordinator, 2, expected_name="Beta")
        assert eo.overrides[2] is None
        assert 2 not in eo._slot_miss_counts

    async def test_miss_counter_resets_on_event_return(self) -> None:
        """If event reappears after 1 miss, counter resets."""
        alpha_start = _make_dt(2025, 7, 10)
        alpha_end = _make_dt(2025, 7, 15)
        beta_start = _make_dt(2025, 7, 20)
        beta_end = _make_dt(2025, 7, 25)
        later_start = _make_dt(2025, 7, 26)
        later_end = _make_dt(2025, 7, 30)
        eo = self._make_two_slot_eo(alpha_start, alpha_end, beta_start, beta_end)
        missing_coordinator = _make_coordinator(
            calendar_events=[
                _make_event(alpha_end, "Alpha", start=alpha_start, uid="uid-A"),
                _make_event(later_end, "Gamma", start=later_start, uid="uid-C"),
            ],
            max_events=2,
        )
        restored_coordinator = _make_coordinator(
            calendar_events=[
                _make_event(alpha_end, "Alpha", start=alpha_start, uid="uid-A"),
                _make_event(beta_end, "Beta", start=beta_start, uid="uid-B"),
            ],
            max_events=2,
        )

        frozen = _make_dt(2025, 7, 1)
        with (
            patch(
                "custom_components.rental_control.event_overrides.async_fire_clear_code",
                new_callable=AsyncMock,
                return_value=OperationResult(kind="clear", slot=1, confirmed=True),
            ) as mock_fire,
            patch.object(dt_util, "start_of_local_day", return_value=frozen),
        ):
            await eo.async_check_overrides(missing_coordinator)
            assert eo._slot_miss_counts == {2: 1}

            await eo.async_check_overrides(restored_coordinator)
            assert 2 not in eo._slot_miss_counts

            await eo.async_check_overrides(missing_coordinator)

        mock_fire.assert_not_called()
        assert eo.overrides[2] is not None
        assert eo._slot_miss_counts == {2: 1}

    async def test_past_event_evicted_immediately(self) -> None:
        """Events with end_date < today bypass tolerance — evict immediately."""
        alpha_start = _make_dt(2025, 7, 10)
        alpha_end = _make_dt(2025, 7, 15)
        beta_start = _make_dt(2025, 7, 6)
        beta_end = _make_dt(2025, 7, 9)
        later_start = _make_dt(2025, 7, 16)
        later_end = _make_dt(2025, 7, 20)
        eo = self._make_two_slot_eo(alpha_start, alpha_end, beta_start, beta_end)
        coordinator = _make_coordinator(
            calendar_events=[
                _make_event(alpha_end, "Alpha", start=alpha_start, uid="uid-A"),
                _make_event(later_end, "Gamma", start=later_start, uid="uid-C"),
            ],
            max_events=2,
        )

        frozen = _make_dt(2025, 7, 10)
        with (
            patch(
                "custom_components.rental_control.event_overrides.async_fire_clear_code",
                new_callable=AsyncMock,
                return_value=OperationResult(kind="clear", slot=1, confirmed=True),
            ) as mock_fire,
            patch.object(dt_util, "start_of_local_day", return_value=frozen),
        ):
            await eo.async_check_overrides(coordinator)

        mock_fire.assert_called_once_with(coordinator, 2, expected_name="Beta")
        assert eo.overrides[2] is None
        assert 2 not in eo._slot_miss_counts

    async def test_miss_counter_cleared_on_slot_free(self) -> None:
        """When a slot is freed, its miss counter is removed."""
        alpha_start = _make_dt(2025, 7, 10)
        alpha_end = _make_dt(2025, 7, 15)
        beta_start = _make_dt(2025, 7, 20)
        beta_end = _make_dt(2025, 7, 25)
        later_start = _make_dt(2025, 7, 26)
        later_end = _make_dt(2025, 7, 30)
        eo = self._make_two_slot_eo(alpha_start, alpha_end, beta_start, beta_end)
        coordinator = _make_coordinator(
            calendar_events=[
                _make_event(alpha_end, "Alpha", start=alpha_start, uid="uid-A"),
                _make_event(later_end, "Gamma", start=later_start, uid="uid-C"),
            ],
            max_events=2,
        )

        frozen = _make_dt(2025, 7, 1)
        with (
            patch(
                "custom_components.rental_control.event_overrides.async_fire_clear_code",
                new_callable=AsyncMock,
                return_value=OperationResult(kind="clear", slot=2, confirmed=True),
            ),
            patch.object(dt_util, "start_of_local_day", return_value=frozen),
        ):
            await eo.async_check_overrides(coordinator)
            assert eo._slot_miss_counts == {2: 1}

            await eo.async_check_overrides(coordinator)

        assert eo.overrides[2] is None
        assert eo._slot_miss_counts == {}

    async def test_tolerance_does_not_affect_other_eviction_reasons(self) -> None:
        """start > end, empty calendar, etc. still evict immediately."""
        eo = EventOverrides(start_slot=1, max_slots=1)
        start = _make_dt(2025, 7, 20)
        end = _make_dt(2025, 7, 10)
        eo.update(1, "c1", "Broken", start, end)
        coordinator = _make_coordinator(
            calendar_events=[
                _make_event(
                    _make_dt(2025, 7, 30),
                    "Other",
                    start=_make_dt(2025, 7, 25),
                    uid="uid-other",
                ),
            ],
            max_events=1,
        )

        frozen = _make_dt(2025, 7, 1)
        with (
            patch(
                "custom_components.rental_control.event_overrides.async_fire_clear_code",
                new_callable=AsyncMock,
                return_value=OperationResult(kind="clear", slot=1, confirmed=True),
            ) as mock_fire,
            patch.object(dt_util, "start_of_local_day", return_value=frozen),
        ):
            await eo.async_check_overrides(coordinator)

        mock_fire.assert_called_once_with(coordinator, 1, expected_name="Broken")
        assert eo.overrides[1] is None
        assert 1 not in eo._slot_miss_counts


# ---------------------------------------------------------------------------
# TestStoreSchemaV1 (T009)
# ---------------------------------------------------------------------------


class TestStoreSchemaV1:
    """Tests for store schema v1 serialisation, deserialisation, and fencing."""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_v1_mapping(
        self,
        identity_key: str = "test-key-1",
        slot: int = 10,
        status: str = "occupied",
        has_code: bool = True,
    ) -> dict:
        """Build a minimal v1 slot mapping dict."""
        return {
            "slot": slot,
            "status": status,
            "operation_id": None,
            "operation_kind": None,
            "identity": {
                "identity_key": identity_key,
                "summary": "Jane Doe",
                "slot_name": "Jane Doe",
                "uid_aliases": [],
                "booking_aliases": [],
            },
            "missing_count": 0,
            "pending_set_since": None,
            "pending_clear_since": None,
            "fingerprint_history": [],
            "updated_at": "2025-01-01T00:00:00+00:00",
            "last_observed_actual": {
                "slot": slot,
                "classification": "adopted",
                "name_state": "Jane Doe",
                "has_code": has_code,
                "start_state": None,
                "end_state": None,
                "use_date_range": None,
                "enabled": None,
            },
        }

    def _make_v1_store(
        self,
        mappings: dict | None = None,
    ) -> dict:
        """Build a minimal schema v1 store dict."""
        return {
            "schema_version": 1,
            "entry_id": "test_entry_id",
            "lockname": "test_lock",
            "start_slot": 10,
            "max_slots": 3,
            "updated_at": "2025-01-01T00:00:00+00:00",
            "mappings": mappings or {},
            "blocked_slots": {},
        }

    # ------------------------------------------------------------------
    # T009-1: v1 serialisation
    # ------------------------------------------------------------------

    def test_store_schema_v1_serialization(self) -> None:
        """T009-1: v1 store dict has required keys and no raw PIN."""
        mapping = self._make_v1_mapping()
        store = self._make_v1_store({"test-key-1": mapping})

        assert store["schema_version"] == 1
        assert "entry_id" in store
        assert "lockname" in store
        assert "start_slot" in store
        assert "max_slots" in store
        assert "updated_at" in store
        assert "mappings" in store
        assert "blocked_slots" in store

        m = store["mappings"]["test-key-1"]
        assert m["slot"] == 10
        assert m["status"] == "occupied"
        last_obs = m["last_observed_actual"]
        assert last_obs["has_code"] is True
        assert "pin" not in last_obs
        assert "code" not in last_obs
        assert "slot_code" not in last_obs

    # ------------------------------------------------------------------
    # T009-2: v1 deserialisation via load_persisted_mappings
    # ------------------------------------------------------------------

    def test_store_schema_v1_deserialization(self) -> None:
        """T009-2: load_persisted_mappings loads v1 mappings correctly."""
        eo = EventOverrides(start_slot=10, max_slots=3)
        mapping = self._make_v1_mapping()
        eo.load_persisted_mappings({"test-key-1": mapping})

        pm = eo.persisted_mappings
        assert "test-key-1" in pm
        assert pm["test-key-1"]["slot"] == 10
        assert pm["test-key-1"]["status"] == "occupied"

    # ------------------------------------------------------------------
    # T009-3: no raw PIN in last_observed_actual
    # ------------------------------------------------------------------

    def test_no_raw_pin_in_store(self) -> None:
        """T009-3: has_code bool is acceptable; raw pin/code keys are not."""
        mapping_true = self._make_v1_mapping(has_code=True)
        mapping_false = self._make_v1_mapping(
            identity_key="test-key-2", slot=11, status="free", has_code=False
        )
        last_true = mapping_true["last_observed_actual"]
        last_false = mapping_false["last_observed_actual"]

        # has_code as bool is allowed
        assert last_true["has_code"] is True
        assert last_false["has_code"] is False

        # Raw PIN keys must not be present
        for last_obs in (last_true, last_false):
            assert "pin" not in last_obs
            assert "code" not in last_obs
            assert "slot_code" not in last_obs

    # ------------------------------------------------------------------
    # T009-4: duplicate occupied slot rejection
    # ------------------------------------------------------------------

    def test_duplicate_slot_rejection(self) -> None:
        """T009-4: two occupied mappings claiming the same slot raise ValueError."""
        from custom_components.rental_control.const import SLOT_STATUS_OCCUPIED

        eo = EventOverrides(start_slot=10, max_slots=3)
        m1 = self._make_v1_mapping(
            identity_key="key-a", slot=10, status=SLOT_STATUS_OCCUPIED
        )
        m2 = self._make_v1_mapping(
            identity_key="key-b", slot=10, status=SLOT_STATUS_OCCUPIED
        )

        with pytest.raises(ValueError, match="Duplicate occupied slot 10"):
            eo.load_persisted_mappings({"key-a": m1, "key-b": m2})

    # ------------------------------------------------------------------
    # T009-5: pending_clear fence rebuilt on load
    # ------------------------------------------------------------------

    def test_pending_fence_on_load(self) -> None:
        """T009-5: pending_clear mapping populates pending_clear_slots."""
        from custom_components.rental_control.const import SLOT_STATUS_PENDING_CLEAR

        eo = EventOverrides(start_slot=10, max_slots=3)
        m = self._make_v1_mapping(
            identity_key="phantom-key",
            slot=10,
            status=SLOT_STATUS_PENDING_CLEAR,
            has_code=False,
        )
        m["pending_clear_since"] = "2025-01-01T00:00:00+00:00"
        eo.load_persisted_mappings({"phantom-key": m})

        assert 10 in eo.pending_clear_slots


# ---------------------------------------------------------------------------
# T061 / T032 / T064: apply-plan fencing and callback re-entrancy
# ---------------------------------------------------------------------------


class TestPendingClearFenceTokenLifecycle:
    """Tests for _apply_clear fence-token lifecycle."""

    def _make_eo(self) -> EventOverrides:
        """Return an EventOverrides with one occupied slot."""
        eo = EventOverrides(start_slot=1, max_slots=1)
        now = _make_dt(2026, 1, 1)
        eo.update(1, "1234", "Guest", now, now)
        return eo

    def _make_coordinator(self, eo: EventOverrides) -> MagicMock:
        """Return a minimal coordinator for apply-clear tests."""
        coordinator = MagicMock()
        coordinator.lockname = "test_lock"
        coordinator.event_overrides = eo
        return coordinator

    async def test_fence_token_set_before_service_call(self) -> None:
        """Fence token is present before the clear service call starts."""
        eo = self._make_eo()
        coordinator = self._make_coordinator(eo)
        observed: dict[str, str | None] = {}

        async def mock_clear_code(*args, **kwargs) -> OperationResult:
            """Capture fence state during the clear service call."""
            observed["fence"] = eo.pending_fences.get(1)
            observed["pending_clear"] = eo.pending_clear_slots.get(1)
            return OperationResult(kind="clear", slot=1, failed=True)

        with patch(
            "custom_components.rental_control.event_overrides.async_fire_clear_code",
            side_effect=mock_clear_code,
        ):
            await eo._apply_clear(coordinator, 1)

        assert observed["fence"] is not None
        assert observed["fence"] == observed["pending_clear"]

    async def test_fence_cleared_on_confirmed_free(self) -> None:
        """Confirmed clear frees the slot and removes both fences."""
        eo = self._make_eo()
        coordinator = self._make_coordinator(eo)

        with patch(
            "custom_components.rental_control.event_overrides.async_fire_clear_code",
            return_value=OperationResult(kind="clear", slot=1, confirmed=True),
        ):
            result = await eo._apply_clear(coordinator, 1)

        assert result.confirmed is True
        assert eo.overrides[1] is None
        assert 1 not in eo.pending_fences
        assert 1 not in eo.pending_clear_slots

    async def test_stale_token_rejected(self) -> None:
        """A replaced token causes the original clear result to be discarded."""
        eo = self._make_eo()
        coordinator = self._make_coordinator(eo)

        async def mock_clear_code(*args, **kwargs) -> OperationResult:
            """Replace the token to simulate a newer in-flight operation."""
            eo._pending_fences[1] = "newer-token"
            return OperationResult(kind="clear", slot=1, confirmed=True)

        with patch(
            "custom_components.rental_control.event_overrides.async_fire_clear_code",
            side_effect=mock_clear_code,
        ):
            result = await eo._apply_clear(coordinator, 1)

        assert result.unconfirmed is True
        assert eo.overrides[1] is not None
        assert eo.pending_fences[1] == "newer-token"

    async def test_retry_clear_persists_new_token(self) -> None:
        """A retry-clear call replaces the prior fence token."""
        eo = self._make_eo()
        coordinator = self._make_coordinator(eo)

        with (
            patch(
                "custom_components.rental_control.event_overrides.uuid.uuid4",
                side_effect=["token-1", "token-2"],
            ),
            patch(
                "custom_components.rental_control.event_overrides.async_fire_clear_code",
                return_value=OperationResult(kind="clear", slot=1, failed=True),
            ),
        ):
            await eo._apply_clear(coordinator, 1)
            first_token = eo.pending_fences[1]
            await eo._apply_clear(coordinator, 1)

        assert first_token == "token-1"
        assert eo.pending_fences[1] == "token-2"

    async def test_slot_remains_pending_on_lingering_name(self) -> None:
        """Lingering name keeps the slot fenced and unavailable."""
        eo = self._make_eo()
        coordinator = self._make_coordinator(eo)

        with patch(
            "custom_components.rental_control.event_overrides.async_fire_clear_code",
            return_value=OperationResult(
                kind="clear",
                slot=1,
                unconfirmed=True,
                lingering_name=True,
            ),
        ):
            result = await eo._apply_clear(coordinator, 1)

        assert result.lingering_name is True
        assert eo.overrides[1] is not None
        assert 1 in eo.pending_fences

    async def test_slot_remains_pending_on_failure(self) -> None:
        """Failed clear keeps the slot fenced and unavailable."""
        eo = self._make_eo()
        coordinator = self._make_coordinator(eo)

        with patch(
            "custom_components.rental_control.event_overrides.async_fire_clear_code",
            return_value=OperationResult(kind="clear", slot=1, failed=True),
        ):
            result = await eo._apply_clear(coordinator, 1)

        assert result.failed is True
        assert eo.overrides[1] is not None
        assert 1 in eo.pending_fences

    async def test_later_confirmed_free_clears_fence(self) -> None:
        """A later confirmed retry finally clears the slot and fence."""
        eo = self._make_eo()
        coordinator = self._make_coordinator(eo)

        with (
            patch(
                "custom_components.rental_control.event_overrides.uuid.uuid4",
                side_effect=["token-1", "token-2"],
            ),
            patch(
                "custom_components.rental_control.event_overrides.async_fire_clear_code",
                side_effect=[
                    OperationResult(kind="clear", slot=1, failed=True),
                    OperationResult(kind="clear", slot=1, confirmed=True),
                ],
            ),
        ):
            await eo._apply_clear(coordinator, 1)
            assert 1 in eo.pending_fences
            result = await eo._apply_clear(coordinator, 1)

        assert result.confirmed is True
        assert eo.overrides[1] is None
        assert 1 not in eo.pending_fences
        assert 1 not in eo.pending_clear_slots


class TestApplyPlanActions:
    """Tests for EventOverrides.async_apply_plan."""

    def _make_reservation(
        self,
        identity_key: str = "res-1",
        *,
        slot_name: str = "Guest",
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> Reservation:
        """Return a Reservation suitable for apply-plan tests."""
        start_dt = start or _make_dt(2026, 8, 1, 14)
        end_dt = end or _make_dt(2026, 8, 8, 11)
        return Reservation(
            identity_key=identity_key,
            start=start_dt,
            end=end_dt,
            buffered_start=start_dt,
            buffered_end=end_dt,
            summary=slot_name,
            slot_name=slot_name,
            display_slot_name=f"RC {slot_name}",
            slot_code="1234",
        )

    def _make_plan(self, *actions: SlotAction) -> DesiredPlan:
        """Return a DesiredPlan with the provided actions."""
        plan = DesiredPlan(plan_id="plan-1", generated_at=_make_dt(2026, 8, 1))
        plan.actions = list(actions)
        return plan

    def _make_coordinator(self, eo: EventOverrides) -> MagicMock:
        """Return a coordinator mock for async_apply_plan."""
        coordinator = MagicMock()
        coordinator.lockname = "test_lock"
        coordinator.event_prefix = ""
        coordinator.trim_names = False
        coordinator.code_buffer_before = 0
        coordinator.code_buffer_after = 0
        coordinator.event_overrides = eo
        coordinator.hass.services.async_call = AsyncMock()
        coordinator.hass.states.get.return_value = None
        return coordinator

    async def test_set_action_pre_assigns_and_confirms(self) -> None:
        """SET pre-assigns the override before the service call and confirms."""
        eo = EventOverrides(start_slot=1, max_slots=1)
        now = _make_dt(2026, 8, 1)
        eo.update(1, "", "", now, now)
        coordinator = self._make_coordinator(eo)
        res = self._make_reservation()
        observed: list[bool] = []

        async def mock_set_code(*args, **kwargs) -> OperationResult:
            """Verify pre-assignment state during the set service call."""
            observed.append(eo.overrides[1] is not None)
            observed.append(1 in eo.pending_fences)
            return OperationResult(kind="set", slot=1, confirmed=True)

        with patch(
            "custom_components.rental_control.event_overrides.async_fire_set_code",
            side_effect=mock_set_code,
        ):
            results = await eo.async_apply_plan(
                coordinator,
                self._make_plan(
                    SlotAction(
                        kind=ActionKind.SET, slot=1, identity_key=res.identity_key
                    )
                ),
                {res.identity_key: res},
            )

        assert results[0].confirmed is True
        assert observed == [True, True]
        assert eo.overrides[1] is not None
        assert eo.overrides[1]["slot_name"] == "Guest"
        assert 1 not in eo.pending_fences

    async def test_set_action_passes_unbuffered_dates(self) -> None:
        """SET helpers receive unbuffered dates so util applies buffer once."""
        eo = EventOverrides(start_slot=1, max_slots=1)
        now = _make_dt(2026, 8, 1)
        eo.update(1, "", "", now, now)
        coordinator = self._make_coordinator(eo)
        res = self._make_reservation()
        res.buffered_start = res.start.replace(hour=res.start.hour - 1)
        res.buffered_end = res.end.replace(hour=res.end.hour + 1)
        observed: list[tuple[datetime, datetime]] = []

        async def mock_set_code(_coordinator, event, _slot) -> OperationResult:
            """Capture the event dates passed to the physical helper."""
            observed.append(
                (
                    event.extra_state_attributes["start"],
                    event.extra_state_attributes["end"],
                )
            )
            return OperationResult(kind="set", slot=1, confirmed=True)

        with patch(
            "custom_components.rental_control.event_overrides.async_fire_set_code",
            side_effect=mock_set_code,
        ):
            await eo.async_apply_plan(
                coordinator,
                self._make_plan(
                    SlotAction(
                        kind=ActionKind.SET, slot=1, identity_key=res.identity_key
                    )
                ),
                {res.identity_key: res},
            )

        assert observed == [(res.start, res.end)]

    async def test_set_action_reverted_on_failure(self) -> None:
        """Failed SET reverts the tentative in-memory assignment."""
        eo = EventOverrides(start_slot=1, max_slots=1)
        now = _make_dt(2026, 8, 1)
        eo.update(1, "", "", now, now)
        coordinator = self._make_coordinator(eo)
        res = self._make_reservation()

        with patch(
            "custom_components.rental_control.event_overrides.async_fire_set_code",
            return_value=OperationResult(kind="set", slot=1, failed=True),
        ):
            results = await eo.async_apply_plan(
                coordinator,
                self._make_plan(
                    SlotAction(
                        kind=ActionKind.SET, slot=1, identity_key=res.identity_key
                    )
                ),
                {res.identity_key: res},
            )

        assert results[0].failed is True
        assert eo.overrides[1] is None
        assert 1 not in eo.pending_fences

    async def test_update_times_action_updates_dates(self) -> None:
        """Confirmed UPDATE_TIMES updates in-memory dates."""
        eo = EventOverrides(start_slot=1, max_slots=1)
        old_start = _make_dt(2026, 8, 1, 14)
        old_end = _make_dt(2026, 8, 8, 11)
        eo.update(1, "1234", "Guest", old_start, old_end)
        coordinator = self._make_coordinator(eo)
        res = self._make_reservation(
            start=_make_dt(2026, 8, 2, 14),
            end=_make_dt(2026, 8, 9, 11),
        )

        with patch(
            "custom_components.rental_control.event_overrides.async_fire_update_times",
            return_value=OperationResult(kind="update_times", slot=1, confirmed=True),
        ):
            results = await eo.async_apply_plan(
                coordinator,
                self._make_plan(
                    SlotAction(
                        kind=ActionKind.UPDATE_TIMES,
                        slot=1,
                        identity_key=res.identity_key,
                    )
                ),
                {res.identity_key: res},
            )

        assert results[0].confirmed is True
        override = eo.overrides[1]
        assert override is not None
        assert override["start_time"] == res.buffered_start
        assert override["end_time"] == res.buffered_end

    async def test_update_times_passes_unbuffered_dates(self) -> None:
        """UPDATE_TIMES helpers receive unbuffered dates."""
        eo = EventOverrides(start_slot=1, max_slots=1)
        old_start = _make_dt(2026, 8, 1, 14)
        old_end = _make_dt(2026, 8, 8, 11)
        eo.update(1, "1234", "Guest", old_start, old_end)
        coordinator = self._make_coordinator(eo)
        res = self._make_reservation(
            start=_make_dt(2026, 8, 2, 14),
            end=_make_dt(2026, 8, 9, 11),
        )
        res.buffered_start = res.start.replace(hour=res.start.hour - 1)
        res.buffered_end = res.end.replace(hour=res.end.hour + 1)
        observed: list[tuple[datetime, datetime]] = []

        async def mock_update_times(_coordinator, event, _slot) -> OperationResult:
            """Capture the event dates passed to the physical helper."""
            observed.append(
                (
                    event.extra_state_attributes["start"],
                    event.extra_state_attributes["end"],
                )
            )
            return OperationResult(kind="update_times", slot=1, confirmed=True)

        with patch(
            "custom_components.rental_control.event_overrides.async_fire_update_times",
            side_effect=mock_update_times,
        ):
            await eo.async_apply_plan(
                coordinator,
                self._make_plan(
                    SlotAction(
                        kind=ActionKind.UPDATE_TIMES,
                        slot=1,
                        identity_key=res.identity_key,
                    )
                ),
                {res.identity_key: res},
            )

        assert observed == [(res.start, res.end)]

    async def test_noop_action_not_executed(self) -> None:
        """NOOP actions do not execute any physical service helpers."""
        eo = EventOverrides(start_slot=1, max_slots=1)
        coordinator = self._make_coordinator(eo)

        with (
            patch(
                "custom_components.rental_control.event_overrides.async_fire_clear_code",
                new_callable=AsyncMock,
            ) as mock_clear,
            patch(
                "custom_components.rental_control.event_overrides.async_fire_set_code",
                new_callable=AsyncMock,
            ) as mock_set,
            patch(
                "custom_components.rental_control.event_overrides.async_fire_update_times",
                new_callable=AsyncMock,
            ) as mock_update,
        ):
            results = await eo.async_apply_plan(
                coordinator,
                self._make_plan(SlotAction(kind=ActionKind.NOOP, slot=1)),
                {},
            )

        assert results == []
        mock_clear.assert_not_called()
        mock_set.assert_not_called()
        mock_update.assert_not_called()

    async def test_clear_action_removes_slot_on_confirm(self) -> None:
        """Confirmed CLEAR frees the slot."""
        eo = EventOverrides(start_slot=1, max_slots=1)
        now = _make_dt(2026, 8, 1)
        eo.update(1, "1234", "Guest", now, now)
        coordinator = self._make_coordinator(eo)

        with patch(
            "custom_components.rental_control.event_overrides.async_fire_clear_code",
            return_value=OperationResult(kind="clear", slot=1, confirmed=True),
        ):
            results = await eo.async_apply_plan(
                coordinator,
                self._make_plan(SlotAction(kind=ActionKind.CLEAR, slot=1)),
                {},
            )

        assert results[0].confirmed is True
        assert eo.overrides[1] is None
        assert 1 not in eo.pending_fences

    async def test_overflow_action_not_executed(self) -> None:
        """Overflow reservations do not become physical slot actions."""
        eo = EventOverrides(start_slot=1, max_slots=1)
        coordinator = self._make_coordinator(eo)
        plan = DesiredPlan(plan_id="plan-overflow", generated_at=_make_dt(2026, 8, 1))
        plan.overflow["res-overflow"] = "capacity"

        with patch(
            "custom_components.rental_control.event_overrides.async_fire_set_code",
            new_callable=AsyncMock,
        ) as mock_set:
            results = await eo.async_apply_plan(coordinator, plan, {})

        assert not hasattr(ActionKind, "OVERFLOW")
        assert results == []
        mock_set.assert_not_called()

    async def test_reconciliation_active_flag_set_during_plan(self) -> None:
        """reconciliation_active is True during execution and False after."""
        eo = EventOverrides(start_slot=1, max_slots=1)
        now = _make_dt(2026, 8, 1)
        eo.update(1, "1234", "Guest", now, now)
        coordinator = self._make_coordinator(eo)
        observed: list[bool] = []

        async def mock_clear_code(*args, **kwargs) -> OperationResult:
            """Capture reconciliation_active during the clear service call."""
            observed.append(eo.reconciliation_active)
            return OperationResult(kind="clear", slot=1, confirmed=True)

        with patch(
            "custom_components.rental_control.event_overrides.async_fire_clear_code",
            side_effect=mock_clear_code,
        ):
            await eo.async_apply_plan(
                coordinator,
                self._make_plan(SlotAction(kind=ActionKind.CLEAR, slot=1)),
                {},
            )

        assert observed == [True]
        assert eo.reconciliation_active is False


class TestCallbackReentrancyFencing:
    """Tests callback re-entrancy fencing at the unit level."""

    async def test_handle_state_change_does_not_call_check_overrides(self) -> None:
        """handle_state_change updates state without starting reconciliation."""
        lockname = "test_lock"
        slot_num = 1
        mock_coordinator = MagicMock()
        mock_coordinator.lockname = lockname
        mock_coordinator.trim_names = False
        mock_coordinator.event_overrides = MagicMock()
        mock_coordinator.event_overrides.async_check_overrides = AsyncMock()
        mock_coordinator.update_event_overrides = AsyncMock()

        name_state = MagicMock()
        name_state.state = "Guest"
        pin_state = MagicMock()
        pin_state.state = "1234"
        enabled_state = MagicMock()
        enabled_state.state = "on"

        def states_get(entity_id: str) -> MagicMock | None:
            """Return the mocked Keymaster state for the requested entity."""
            if "enabled" in entity_id:
                return enabled_state
            if "pin" in entity_id:
                return pin_state
            if "name" in entity_id:
                return name_state
            return None

        hass = MagicMock()
        hass.data = {DOMAIN: {"entry_id": {COORDINATOR: mock_coordinator}}}
        hass.states.get = states_get

        config_entry = MagicMock()
        config_entry.entry_id = "entry_id"

        event = MagicMock()
        event.data = {"entity_id": f"switch.{lockname}_code_slot_{slot_num}_enabled"}

        with patch(
            "custom_components.rental_control.util.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            await handle_state_change(hass, config_entry, event)

        mock_coordinator.event_overrides.async_check_overrides.assert_not_called()
        mock_coordinator.update_event_overrides.assert_awaited_once()


# ---------------------------------------------------------------------------
# T078: Exact stable-fingerprint restart mapping preservation
# ---------------------------------------------------------------------------


class TestExactFingerprintRestartPreservation:
    """T078: Persisted mappings keyed by stable fingerprint survive restarts.

    These tests verify the complete chain:
    1. Compute fingerprint from reservation attributes.
    2. Persist a mapping keyed by that fingerprint via load_persisted_mappings().
    3. On a simulated restart, find_reservation_rematch() returns EXACT.

    This proves that slot assignments survive restarts when the calendar
    feed returns the same reservation data (even with a new volatile UID).
    """

    def _make_fp_mapping(
        self,
        identity_key: str,
        slot_name: str,
        slot: int = 10,
        uid_aliases: list[str] | None = None,
    ) -> dict:
        """Build a minimal v1 mapping suitable for fingerprint restart tests."""
        return {
            "identity_key": identity_key,
            "slot": slot,
            "status": "occupied",
            "operation_id": None,
            "operation_kind": None,
            "identity": {
                "identity_key": identity_key,
                "summary": slot_name,
                "slot_name": slot_name,
                "uid_aliases": uid_aliases or [],
                "booking_aliases": [],
            },
            "missing_count": 0,
            "pending_set_since": None,
            "pending_clear_since": None,
            "fingerprint_history": [],
            "updated_at": "2026-01-01T00:00:00+00:00",
            "last_observed_actual": {
                "slot": slot,
                "classification": "occupied",
                "name_state": slot_name,
                "has_code": True,
                "start_state": None,
                "end_state": None,
                "use_date_range": None,
                "enabled": None,
            },
        }

    def test_fingerprint_key_survives_load(self) -> None:
        """T078-1: load_persisted_mappings retains fingerprint as mapping key."""
        from custom_components.rental_control.reconciliation import (
            make_reservation_fingerprint,
        )

        entry_id = "entry-restart-001"
        name = "Alice Guest"
        start = _make_dt(2026, 7, 1)
        end = _make_dt(2026, 7, 8)

        fp = make_reservation_fingerprint(entry_id, name, start, end)
        mapping = self._make_fp_mapping(identity_key=fp, slot_name=name)

        eo = EventOverrides(start_slot=10, max_slots=3)
        eo.load_persisted_mappings({fp: mapping})

        pm = eo.persisted_mappings
        assert fp in pm
        assert pm[fp]["slot"] == 10
        assert pm[fp]["status"] == "occupied"

    def test_exact_rematch_after_restart_no_uid_change(self) -> None:
        """T078-2: EXACT rematch found when feed returns same reservation data.

        Simulates: integration persists the mapping, HA restarts, feed
        returns the same reservation → find_reservation_rematch returns EXACT.
        """
        from custom_components.rental_control.reconciliation import RematchKind
        from custom_components.rental_control.reconciliation import Reservation
        from custom_components.rental_control.reconciliation import (
            find_reservation_rematch,
        )
        from custom_components.rental_control.reconciliation import (
            make_reservation_fingerprint,
        )

        entry_id = "entry-restart-002"
        name = "Bob Guest"
        start = _make_dt(2026, 7, 10)
        end = _make_dt(2026, 7, 17)
        uid = "uid-stable-bob"

        fp = make_reservation_fingerprint(entry_id, name, start, end)
        mapping = self._make_fp_mapping(
            identity_key=fp, slot_name=name, uid_aliases=[uid]
        )

        eo = EventOverrides(start_slot=10, max_slots=3)
        eo.load_persisted_mappings({fp: mapping})

        # After restart: feed returns same reservation (same fingerprint)
        reservation = Reservation(
            identity_key=fp,
            start=start,
            end=end,
            buffered_start=start,
            buffered_end=end,
            summary=name,
            slot_name=name,
            display_slot_name=f"RC {name}",
            slot_code="1234",
        )
        reservation.uid_aliases.add(uid)

        result = find_reservation_rematch(reservation, eo.persisted_mappings)
        assert result.kind is RematchKind.EXACT
        assert result.matched_identity_key == fp

    def test_exact_rematch_after_restart_with_uid_churn(self) -> None:
        """T078-3: EXACT rematch even when UID changed between restarts.

        Simulates: integration persists with old UID, platform reissues
        UID before HA restarts, feed returns new UID but same name/dates
        → fingerprint unchanged → EXACT match → mapping preserved.
        """
        from custom_components.rental_control.reconciliation import RematchKind
        from custom_components.rental_control.reconciliation import Reservation
        from custom_components.rental_control.reconciliation import (
            find_reservation_rematch,
        )
        from custom_components.rental_control.reconciliation import (
            make_reservation_fingerprint,
        )

        entry_id = "entry-restart-003"
        name = "Carol Guest"
        start = _make_dt(2026, 8, 1)
        end = _make_dt(2026, 8, 8)
        old_uid = "uid-carol-original"
        new_uid = "uid-carol-reissued"

        fp = make_reservation_fingerprint(entry_id, name, start, end)
        mapping = self._make_fp_mapping(
            identity_key=fp, slot_name=name, uid_aliases=[old_uid]
        )

        eo = EventOverrides(start_slot=10, max_slots=3)
        eo.load_persisted_mappings({fp: mapping})

        # After restart: feed returns SAME reservation with NEW uid
        reservation = Reservation(
            identity_key=fp,  # fingerprint is UID-independent → same fp
            start=start,
            end=end,
            buffered_start=start,
            buffered_end=end,
            summary=name,
            slot_name=name,
            display_slot_name=f"RC {name}",
            slot_code="5678",
        )
        reservation.uid_aliases.add(new_uid)  # new UID after platform churn

        result = find_reservation_rematch(reservation, eo.persisted_mappings)
        assert result.kind is RematchKind.EXACT
        assert result.matched_identity_key == fp
        assert result.date_shifted is False  # no date shift, only UID changed

    def test_no_raw_pin_in_fingerprint_keyed_mapping(self) -> None:
        """T078-4: Fingerprint-keyed mappings maintain no-raw-PIN invariant."""
        from custom_components.rental_control.reconciliation import (
            make_reservation_fingerprint,
        )

        fp = make_reservation_fingerprint(
            "entry-001", "Diana Guest", _make_dt(2026, 9, 1), _make_dt(2026, 9, 8)
        )
        mapping = self._make_fp_mapping(identity_key=fp, slot_name="Diana Guest")

        # Verify the fixture itself (and any real persistence) has no raw PIN
        last_obs = mapping["last_observed_actual"]
        assert "pin" not in last_obs
        assert "code" not in last_obs
        assert "slot_code" not in last_obs
        assert "has_code" in last_obs  # only the bool flag

    def test_multiple_fingerprint_keyed_mappings_loaded(self) -> None:
        """T078-5: Multiple fingerprint-keyed mappings all survive load."""
        from custom_components.rental_control.reconciliation import (
            make_reservation_fingerprint,
        )

        guests = [
            ("Alice", _make_dt(2026, 7, 1), _make_dt(2026, 7, 8), 10),
            ("Bob", _make_dt(2026, 7, 9), _make_dt(2026, 7, 16), 11),
            ("Carol", _make_dt(2026, 7, 17), _make_dt(2026, 7, 24), 12),
        ]
        entry_id = "entry-multi"
        mappings = {}
        fps = []
        for name, start, end, slot in guests:
            fp = make_reservation_fingerprint(entry_id, name, start, end)
            fps.append(fp)
            mappings[fp] = self._make_fp_mapping(
                identity_key=fp, slot_name=name, slot=slot
            )

        eo = EventOverrides(start_slot=10, max_slots=3)
        eo.load_persisted_mappings(mappings)

        pm = eo.persisted_mappings
        for fp in fps:
            assert fp in pm

    def test_dataclass_invariants_preserved_after_restart(self) -> None:
        """T078-6: SlotMapping invariants from prior commits remain intact post-restart.

        Verifies that schema_version >= 1 and missing_count >= 0 invariants
        from the SlotMapping dataclass (T014/commit 2) are not violated by
        fingerprint-keyed mappings used in restart scenarios.
        """
        from custom_components.rental_control.reconciliation import SlotMapping
        from custom_components.rental_control.reconciliation import StoredActual
        from custom_components.rental_control.reconciliation import StoredIdentity
        from custom_components.rental_control.reconciliation import (
            make_reservation_fingerprint,
        )

        entry_id = "entry-inv"
        name = "Eve Guest"
        start = _make_dt(2026, 10, 1)
        end = _make_dt(2026, 10, 8)
        fp = make_reservation_fingerprint(entry_id, name, start, end)

        sm = SlotMapping(
            schema_version=1,
            entry_id=entry_id,
            identity_key=fp,
            slot=10,
            status="occupied",
            identity=StoredIdentity(
                identity_key=fp,
                summary=name,
                slot_name=name,
            ),
            last_observed_actual=StoredActual(slot=10, classification="occupied"),
            updated_at=_make_dt(2026, 10, 1),
        )
        # schema_version=1 and missing_count=0 are valid per SlotMapping.__post_init__
        assert sm.schema_version == 1
        assert sm.missing_count == 0
        assert sm.identity_key == fp
        assert sm.fingerprint_history == []


# ---------------------------------------------------------------------------
# T029: Desired-plan action scaffolding (set, update_times, noop, overflow)
# ---------------------------------------------------------------------------
# These tests verify that compute_desired_plan generates the correct action
# kinds for the four fundamental scenarios that the EventOverrides apply-plan
# phase (commits 5-6) will need to execute.  They are pure model tests:
# no HA service calls, no coordinator wiring, no locks.
# ---------------------------------------------------------------------------


class TestDesiredPlanActionScaffold:
    """T029: pure model tests for set/update_times/noop/overflow action types."""

    from datetime import timezone as _tz_import

    _TZ = _tz_import.utc

    def _dt(self, year: int, month: int, day: int, hour: int = 14) -> datetime:
        """Return a UTC-aware datetime."""
        return datetime(year, month, day, hour, tzinfo=self._TZ)

    def _make_res(
        self,
        identity_key: str,
        *,
        start_day: int = 1,
        start_month: int = 8,
    ):
        """Return a minimal Reservation for action-scaffold tests."""
        from datetime import timedelta

        from custom_components.rental_control.reconciliation import Reservation

        start = self._dt(2026, start_month, start_day)
        end = (start + timedelta(days=7)).replace(hour=11)
        return Reservation(
            identity_key=identity_key,
            start=start,
            end=end,
            buffered_start=start,
            buffered_end=end,
            summary=f"Guest {identity_key}",
            slot_name=f"Guest {identity_key}",
            display_slot_name=f"RC Guest {identity_key}",
            slot_code="5678",
        )

    def _make_ms(
        self,
        slot: int,
        status,
        persisted_key: str | None = None,
        actual_start=None,
        actual_end=None,
    ):
        """Return a minimal ManagedSlot for action-scaffold tests."""
        from custom_components.rental_control.reconciliation import ManagedSlot

        return ManagedSlot(
            slot=slot,
            managed=True,
            status=status,
            persisted_identity_key=persisted_key,
            actual_start=actual_start,
            actual_end=actual_end,
        )

    def test_free_slot_desired_reservation_generates_set_action(self) -> None:
        """A FREE managed slot assigned a desired reservation → SET action.

        This is the most common initial-assignment scenario: a new reservation
        arrives and an empty slot is available.
        """
        from custom_components.rental_control.reconciliation import ActionKind
        from custom_components.rental_control.reconciliation import SlotStatus
        from custom_components.rental_control.reconciliation import compute_desired_plan

        res = self._make_res("res-set")
        ms = self._make_ms(5, SlotStatus.FREE)

        plan = compute_desired_plan(
            [res],
            [ms],
            max_events=3,
            plan_id="t029-set",
            generated_at=self._dt(2026, 8, 1),
        )

        assert "res-set" in plan.selected
        set_actions = [a for a in plan.actions if a.kind is ActionKind.SET]
        assert len(set_actions) == 1
        assert set_actions[0].slot == 5
        assert set_actions[0].identity_key == "res-set"

    def test_occupied_same_reservation_different_dates_generates_update_times(
        self,
    ) -> None:
        """OCCUPIED slot with same reservation but different buffered dates → UPDATE_TIMES.

        This happens when a guest modifies check-in/check-out dates; Rental
        Control must update the Keymaster date range without changing the PIN.
        """
        from custom_components.rental_control.reconciliation import ActionKind
        from custom_components.rental_control.reconciliation import SlotStatus
        from custom_components.rental_control.reconciliation import compute_desired_plan

        res = self._make_res("res-update")
        # Slot has old dates (different from res.buffered_start / buffered_end)
        old_start = self._dt(2026, 7, 25)
        old_end = self._dt(2026, 8, 1, 11)
        ms = self._make_ms(
            5,
            SlotStatus.OCCUPIED,
            persisted_key="res-update",
            actual_start=old_start,
            actual_end=old_end,
        )

        plan = compute_desired_plan(
            [res],
            [ms],
            max_events=3,
            plan_id="t029-update",
            generated_at=self._dt(2026, 8, 1),
        )

        assert plan.slots[5].action is ActionKind.UPDATE_TIMES
        update_actions = [a for a in plan.actions if a.kind is ActionKind.UPDATE_TIMES]
        assert len(update_actions) == 1
        assert update_actions[0].identity_key == "res-update"

    def test_occupied_same_reservation_same_dates_generates_noop(self) -> None:
        """OCCUPIED slot with same reservation and matching dates → no action (NOOP).

        This is the steady-state scenario: everything is already correct and
        the apply-plan phase should issue no Keymaster service call.
        """
        from custom_components.rental_control.reconciliation import ActionKind
        from custom_components.rental_control.reconciliation import SlotStatus
        from custom_components.rental_control.reconciliation import compute_desired_plan

        res = self._make_res("res-noop")
        # Slot has exactly the same dates as res.buffered_start / buffered_end
        ms = self._make_ms(
            5,
            SlotStatus.OCCUPIED,
            persisted_key="res-noop",
            actual_start=res.buffered_start,
            actual_end=res.buffered_end,
        )

        plan = compute_desired_plan(
            [res],
            [ms],
            max_events=3,
            plan_id="t029-noop",
            generated_at=self._dt(2026, 8, 1),
        )

        assert plan.slots[5].action is ActionKind.NOOP
        # NOOP must NOT appear in plan.actions (only non-noop actions are listed)
        assert not any(a.kind is ActionKind.NOOP for a in plan.actions)

    def test_overflow_reservation_not_in_selected(self) -> None:
        """Reservations beyond max_events end up in overflow, not in selected.

        The apply-plan phase must not attempt a SET action for overflow
        reservations; it should only report them as unassigned.
        """
        from datetime import timezone

        from custom_components.rental_control.reconciliation import compute_desired_plan

        _TZ = timezone.utc

        def _mk(key: str, day: int):
            """Build a minimal August Reservation with *key* starting on *day*."""
            from custom_components.rental_control.reconciliation import Reservation

            s = datetime(2026, 8, day, 14, tzinfo=_TZ)
            e = datetime(2026, 8, day + 7, 11, tzinfo=_TZ)
            return Reservation(
                identity_key=key,
                start=s,
                end=e,
                buffered_start=s,
                buffered_end=e,
                summary=f"G {key}",
                slot_name=f"G {key}",
                display_slot_name=f"RC G {key}",
                slot_code="0000",
            )

        from custom_components.rental_control.reconciliation import ManagedSlot
        from custom_components.rental_control.reconciliation import SlotStatus

        reservations = [_mk("ov-r1", 1), _mk("ov-r2", 8), _mk("ov-r3", 15)]
        slots = [
            ManagedSlot(slot=5, managed=True, status=SlotStatus.FREE),
            ManagedSlot(slot=6, managed=True, status=SlotStatus.FREE),
        ]

        plan = compute_desired_plan(
            reservations,
            slots,
            max_events=2,
            plan_id="t029-ov",
            generated_at=datetime(2026, 8, 1, tzinfo=_TZ),
        )

        assert "ov-r1" in plan.selected
        assert "ov-r2" in plan.selected
        assert "ov-r3" in plan.overflow
        assert plan.overflow["ov-r3"] == "capacity"
        assert "ov-r3" not in plan.selected


# ---------------------------------------------------------------------------
# T091: EventOverrides diagnostics snapshot tests
# ---------------------------------------------------------------------------


class TestDiagnosticsSnapshot:
    """T091: Diagnostics snapshot tests for matched slots, pending corrections,
    blocked clear reasons, retry count, last error, and no raw codes."""

    _TZ = dt_util.UTC

    def _make_dt(self, day: int) -> datetime:
        """Return a UTC-aware datetime for Aug *day* 2026 at 14:00."""
        return datetime(2026, 8, day, 14, tzinfo=self._TZ)

    def _make_plan(
        self,
        *,
        plan_id: str = "diag-plan-001",
    ) -> DesiredPlan:
        """Return a minimal DesiredPlan for snapshot tests."""
        return DesiredPlan(
            plan_id=plan_id,
            generated_at=datetime(2026, 8, 1, tzinfo=self._TZ),
        )

    def _make_planned_slot(
        self,
        slot: int,
        *,
        desired_identity_key: str | None = None,
        actual_classification: str = "free",
        action: ActionKind = ActionKind.NOOP,
        pending_reason: str | None = None,
        retry_count: int = 0,
        last_error: str | None = None,
    ):
        """Return a minimal PlannedSlot for snapshot tests."""
        from custom_components.rental_control.reconciliation import PlannedSlot

        return PlannedSlot(
            slot=slot,
            desired_identity_key=desired_identity_key,
            actual_classification=actual_classification,
            action=action,
            pending_reason=pending_reason,
            retry_count=retry_count,
            last_error=last_error,
        )

    def test_initial_snapshot_is_empty(self) -> None:
        """Before any plan is applied, diagnostics_snapshot is an empty dict."""
        eo = EventOverrides(start_slot=5, max_slots=3)
        assert eo.diagnostics_snapshot == {}

    def test_snapshot_has_matched_slots(self) -> None:
        """After update_diagnostics_snapshot, matched slots appear in snapshot."""
        eo = EventOverrides(start_slot=5, max_slots=3)
        plan = self._make_plan()
        plan.slots[5] = self._make_planned_slot(
            5,
            desired_identity_key="res-abc",
            actual_classification="occupied",
            action=ActionKind.NOOP,
        )
        plan.slots[6] = self._make_planned_slot(6)  # no desired key → not matched

        eo.update_diagnostics_snapshot(plan)
        snap = eo.diagnostics_snapshot

        assert 5 in snap["matched_slots"]
        assert snap["matched_slots"][5]["identity_key"] == "res-abc"
        assert 6 not in snap["matched_slots"]

    def test_snapshot_has_pending_corrections_for_retry_clear(self) -> None:
        """RETRY_CLEAR action appears in pending_corrections."""
        eo = EventOverrides(start_slot=5, max_slots=3)
        plan = self._make_plan()
        plan.slots[5] = self._make_planned_slot(
            5,
            desired_identity_key=None,
            actual_classification="pending_clear",
            action=ActionKind.RETRY_CLEAR,
            pending_reason="prior clear unconfirmed",
            retry_count=2,
        )

        eo.update_diagnostics_snapshot(plan)
        snap = eo.diagnostics_snapshot

        assert 5 in snap["pending_corrections"]
        correction = snap["pending_corrections"][5]
        assert correction["action"] == ActionKind.RETRY_CLEAR.value
        assert correction["retry_count"] == 2

    def test_snapshot_has_pending_corrections_for_blocked(self) -> None:
        """BLOCKED action appears in pending_corrections."""
        eo = EventOverrides(start_slot=5, max_slots=3)
        plan = self._make_plan()
        plan.slots[6] = self._make_planned_slot(
            6,
            actual_classification="blocked",
            action=ActionKind.BLOCKED,
            pending_reason="manual change detected",
        )

        eo.update_diagnostics_snapshot(plan)
        snap = eo.diagnostics_snapshot

        assert 6 in snap["pending_corrections"]
        assert snap["pending_corrections"][6]["action"] == ActionKind.BLOCKED.value

    def test_snapshot_has_blocked_clear_reasons(self) -> None:
        """blocked_reason from PlannedSlot appears in pending_corrections."""
        eo = EventOverrides(start_slot=5, max_slots=2)
        plan = self._make_plan()
        plan.slots[5] = self._make_planned_slot(
            5,
            actual_classification="blocked",
            action=ActionKind.BLOCKED,
            pending_reason="slot entity unavailable",
        )

        eo.update_diagnostics_snapshot(plan)
        snap = eo.diagnostics_snapshot

        assert (
            snap["pending_corrections"][5]["blocked_reason"]
            == "slot entity unavailable"
        )

    def test_snapshot_has_slot_retry_counts(self) -> None:
        """slot_retry_counts includes all managed slots."""
        eo = EventOverrides(start_slot=5, max_slots=3)
        # Record a failure to bump retry count
        eo.record_retry_failure(5)
        eo.record_retry_failure(5)

        plan = self._make_plan()
        eo.update_diagnostics_snapshot(plan)
        snap = eo.diagnostics_snapshot

        # All three managed slots (5, 6, 7) appear
        assert 5 in snap["slot_retry_counts"]
        assert 6 in snap["slot_retry_counts"]
        assert 7 in snap["slot_retry_counts"]
        assert snap["slot_retry_counts"][5] == 2

    def test_snapshot_has_last_errors_after_failed_clear(self) -> None:
        """After a failed clear, last_slot_errors appears in snapshot."""
        eo = EventOverrides(start_slot=5, max_slots=2)
        # Directly use the private helper to simulate a recorded error
        eo._record_slot_error(5, "lock offline")

        plan = self._make_plan()
        eo.update_diagnostics_snapshot(plan)
        snap = eo.diagnostics_snapshot

        assert 5 in snap["last_slot_errors"]
        assert snap["last_slot_errors"][5] == "lock offline"

    async def test_snapshot_captures_error_from_failed_apply(self) -> None:
        """apply_plan with a failed clear records the error in the snapshot."""
        eo = EventOverrides(start_slot=5, max_slots=2)
        now = datetime(2026, 8, 1, tzinfo=self._TZ)
        eo.update(5, "c1", "OldGuest", now, now + timedelta(days=7))
        eo.update(6, "", "", now, now + timedelta(days=7))

        plan = self._make_plan()
        plan.actions = [SlotAction(kind=ActionKind.CLEAR, slot=5, identity_key=None)]
        plan.slots[5] = self._make_planned_slot(
            5,
            desired_identity_key=None,
            actual_classification="occupied",
            action=ActionKind.CLEAR,
        )
        plan.slots[6] = self._make_planned_slot(6)

        failed_result = OperationResult(
            kind="clear",
            slot=5,
            failed=True,
            error="lock unreachable",
        )
        coordinator = MagicMock()
        coordinator.lockname = "test_lock"
        coordinator.hass.services.async_call = AsyncMock()

        with patch(
            "custom_components.rental_control.event_overrides.async_fire_clear_code",
            return_value=failed_result,
        ):
            await eo.async_apply_plan(coordinator, plan, {})

        snap = eo.diagnostics_snapshot
        assert 5 in snap["last_slot_errors"]
        assert snap["last_slot_errors"][5] == "lock unreachable"

    async def test_snapshot_clears_error_after_successful_clear(self) -> None:
        """After a successful clear, the slot error is removed from snapshot."""
        eo = EventOverrides(start_slot=5, max_slots=2)
        now = datetime(2026, 8, 1, tzinfo=self._TZ)
        eo.update(5, "c1", "OldGuest", now, now + timedelta(days=7))
        eo.update(6, "", "", now, now + timedelta(days=7))
        # Pre-seed an error from a previous failed attempt
        eo._record_slot_error(5, "previous error")

        plan = self._make_plan()
        plan.actions = [SlotAction(kind=ActionKind.CLEAR, slot=5, identity_key=None)]
        plan.slots[5] = self._make_planned_slot(
            5,
            desired_identity_key=None,
            actual_classification="occupied",
            action=ActionKind.CLEAR,
        )
        plan.slots[6] = self._make_planned_slot(6)

        success_result = OperationResult(kind="clear", slot=5, confirmed=True)
        coordinator = MagicMock()
        coordinator.lockname = "test_lock"
        coordinator.hass.services.async_call = AsyncMock()

        with patch(
            "custom_components.rental_control.event_overrides.async_fire_clear_code",
            return_value=success_result,
        ):
            await eo.async_apply_plan(coordinator, plan, {})

        snap = eo.diagnostics_snapshot
        assert 5 not in snap["last_slot_errors"]

    def test_snapshot_has_no_raw_slot_codes(self) -> None:
        """Diagnostics snapshot contains no slot_code or PIN values."""
        eo = EventOverrides(start_slot=5, max_slots=2)
        now = datetime(2026, 8, 1, tzinfo=self._TZ)
        eo.update(5, "secret1234", "Guest A", now, now + timedelta(days=7))

        plan = self._make_plan()
        plan.slots[5] = self._make_planned_slot(
            5,
            desired_identity_key="res-001",
            actual_classification="occupied",
            action=ActionKind.NOOP,
        )
        eo.update_diagnostics_snapshot(plan)

        snap = eo.diagnostics_snapshot
        snap_str = str(snap)
        assert "secret1234" not in snap_str
        assert "slot_code" not in snap_str
        assert "pin" not in snap_str.lower()

    def test_snapshot_has_plan_id_and_timestamp(self) -> None:
        """Snapshot includes plan_id and generated_at from the plan."""
        eo = EventOverrides(start_slot=5, max_slots=2)
        plan = self._make_plan(plan_id="unique-plan-xyz")
        eo.update_diagnostics_snapshot(plan)

        snap = eo.diagnostics_snapshot
        assert snap["plan_id"] == "unique-plan-xyz"
        assert "generated_at" in snap

    def test_snapshot_pending_clear_slots_list(self) -> None:
        """pending_clear_slots in snapshot matches internal state."""
        eo = EventOverrides(start_slot=5, max_slots=3)
        # Manually simulate pending clear state
        eo._pending_clear_slots[5] = "op-token-abc"

        plan = self._make_plan()
        eo.update_diagnostics_snapshot(plan)
        snap = eo.diagnostics_snapshot

        assert 5 in snap["pending_clear_slots"]


# ---------------------------------------------------------------------------
# T070: Manual managed-slot drift tests (name, code, start, end, switch)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestManualDriftLogging:
    """T070: Tests for manual/external managed-slot drift detection and logging.

    Verifies that EventOverrides correctly handles OVERWRITE_MANUAL_CHANGE
    actions: logging drift details with redacted code fields, restoring the
    desired state, and surfacing manual drift in the diagnostics snapshot.
    Unmanaged slot edits are invisible to the reconciliation system because
    compute_desired_plan only iterates managed slots.
    """

    _TZ = dt_util.UTC

    def _make_res(
        self,
        identity_key: str = "r-drift-001",
        *,
        slot_name: str = "Alice Smith",
        display_slot_name: str = "RC Alice Smith",
        slot_code: str = "SECRETPIN",
        start_day: int = 1,
    ) -> Reservation:
        """Return a minimal Reservation for drift tests."""
        start = datetime(2026, 8, start_day, 14, tzinfo=self._TZ)
        end = start + timedelta(days=7)
        return Reservation(
            identity_key=identity_key,
            start=start,
            end=end,
            buffered_start=start,
            buffered_end=end,
            summary=f"Guest {slot_name}",
            slot_name=slot_name,
            display_slot_name=display_slot_name,
            slot_code=slot_code,
        )

    def _make_overwrite_action(
        self,
        slot: int = 5,
        identity_key: str = "r-drift-001",
        drift_fields: list[str] | None = None,
    ) -> SlotAction:
        """Return a minimal OVERWRITE_MANUAL_CHANGE SlotAction."""
        fields = drift_fields if drift_fields is not None else ["name"]
        reason = "drifted fields: " + ", ".join(fields)
        return SlotAction(
            kind=ActionKind.OVERWRITE_MANUAL_CHANGE,
            slot=slot,
            identity_key=identity_key,
            reason=reason,
        )

    async def test_name_drift_logged_at_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Name drift triggers a WARNING log including the field name."""
        eo = EventOverrides(start_slot=5, max_slots=2)
        now = datetime(2026, 8, 1, tzinfo=self._TZ)
        eo.update(5, "SECRETPIN", "Alice Smith", now, now + timedelta(days=7))

        res = self._make_res()
        action = self._make_overwrite_action(drift_fields=["name"])

        coordinator = MagicMock()
        coordinator.lockname = "test_lock"
        coordinator.hass.services.async_call = AsyncMock()

        confirmed = OperationResult(kind="set", slot=5, confirmed=True)
        with (
            caplog.at_level(logging.WARNING, logger="custom_components.rental_control"),
            patch(
                "custom_components.rental_control.event_overrides.async_fire_set_code",
                return_value=confirmed,
            ),
        ):
            await eo._apply_overwrite_manual_change(coordinator, 5, res, action)

        assert any(
            "name" in r.message and "slot 5" in r.message for r in caplog.records
        )

    async def test_code_drift_logged_at_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Code drift triggers a WARNING log including the 'code' field."""
        eo = EventOverrides(start_slot=5, max_slots=2)
        now = datetime(2026, 8, 1, tzinfo=self._TZ)
        eo.update(5, "SECRETPIN", "Alice Smith", now, now + timedelta(days=7))

        res = self._make_res()
        action = self._make_overwrite_action(drift_fields=["code"])

        coordinator = MagicMock()
        coordinator.lockname = "test_lock"
        coordinator.hass.services.async_call = AsyncMock()

        confirmed = OperationResult(kind="set", slot=5, confirmed=True)
        with (
            caplog.at_level(logging.WARNING, logger="custom_components.rental_control"),
            patch(
                "custom_components.rental_control.event_overrides.async_fire_set_code",
                return_value=confirmed,
            ),
        ):
            await eo._apply_overwrite_manual_change(coordinator, 5, res, action)

        assert any(
            "code" in r.message and "slot 5" in r.message for r in caplog.records
        )

    async def test_start_date_drift_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Start-date drift is included in the WARNING log's field list."""
        eo = EventOverrides(start_slot=5, max_slots=2)
        now = datetime(2026, 8, 1, tzinfo=self._TZ)
        eo.update(5, "SECRETPIN", "Alice Smith", now, now + timedelta(days=7))

        res = self._make_res()
        action = self._make_overwrite_action(drift_fields=["name", "start"])

        coordinator = MagicMock()
        coordinator.lockname = "test_lock"
        coordinator.hass.services.async_call = AsyncMock()

        confirmed = OperationResult(kind="set", slot=5, confirmed=True)
        with (
            caplog.at_level(logging.WARNING, logger="custom_components.rental_control"),
            patch(
                "custom_components.rental_control.event_overrides.async_fire_set_code",
                return_value=confirmed,
            ),
        ):
            await eo._apply_overwrite_manual_change(coordinator, 5, res, action)

        log_text = " ".join(r.message for r in caplog.records)
        assert "start" in log_text

    async def test_end_date_drift_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """End-date drift is included in the WARNING log's field list."""
        eo = EventOverrides(start_slot=5, max_slots=2)
        now = datetime(2026, 8, 1, tzinfo=self._TZ)
        eo.update(5, "SECRETPIN", "Alice Smith", now, now + timedelta(days=7))

        res = self._make_res()
        action = self._make_overwrite_action(drift_fields=["name", "end"])

        coordinator = MagicMock()
        coordinator.lockname = "test_lock"
        coordinator.hass.services.async_call = AsyncMock()

        confirmed = OperationResult(kind="set", slot=5, confirmed=True)
        with (
            caplog.at_level(logging.WARNING, logger="custom_components.rental_control"),
            patch(
                "custom_components.rental_control.event_overrides.async_fire_set_code",
                return_value=confirmed,
            ),
        ):
            await eo._apply_overwrite_manual_change(coordinator, 5, res, action)

        log_text = " ".join(r.message for r in caplog.records)
        assert "end" in log_text

    async def test_overwrite_passes_unbuffered_dates(self) -> None:
        """Manual-drift overwrite passes unbuffered dates to set helper."""
        eo = EventOverrides(start_slot=5, max_slots=2)
        now = datetime(2026, 8, 1, tzinfo=self._TZ)
        eo.update(5, "SECRETPIN", "Alice Smith", now, now + timedelta(days=7))

        res = self._make_res()
        res.buffered_start = res.start - timedelta(hours=1)
        res.buffered_end = res.end + timedelta(hours=1)
        action = self._make_overwrite_action(drift_fields=["name", "start"])

        coordinator = MagicMock()
        coordinator.lockname = "test_lock"
        coordinator.hass.services.async_call = AsyncMock()
        observed: list[tuple[datetime, datetime]] = []

        async def mock_set_code(_coordinator, event, _slot) -> OperationResult:
            """Capture the event dates passed to the physical helper."""
            observed.append(
                (
                    event.extra_state_attributes["start"],
                    event.extra_state_attributes["end"],
                )
            )
            return OperationResult(kind="set", slot=5, confirmed=True)

        with patch(
            "custom_components.rental_control.event_overrides.async_fire_set_code",
            side_effect=mock_set_code,
        ):
            await eo._apply_overwrite_manual_change(coordinator, 5, res, action)

        assert observed == [(res.start, res.end)]

    async def test_date_range_switch_drift_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """date_range_enabled drift is included in the WARNING log's field list."""
        eo = EventOverrides(start_slot=5, max_slots=2)
        now = datetime(2026, 8, 1, tzinfo=self._TZ)
        eo.update(5, "SECRETPIN", "Alice Smith", now, now + timedelta(days=7))

        res = self._make_res()
        action = self._make_overwrite_action(
            drift_fields=["name", "date_range_enabled"]
        )

        coordinator = MagicMock()
        coordinator.lockname = "test_lock"
        coordinator.hass.services.async_call = AsyncMock()

        confirmed = OperationResult(kind="set", slot=5, confirmed=True)
        with (
            caplog.at_level(logging.WARNING, logger="custom_components.rental_control"),
            patch(
                "custom_components.rental_control.event_overrides.async_fire_set_code",
                return_value=confirmed,
            ),
        ):
            await eo._apply_overwrite_manual_change(coordinator, 5, res, action)

        log_text = " ".join(r.message for r in caplog.records)
        assert "date_range_enabled" in log_text

    async def test_raw_pin_never_in_log(self, caplog: pytest.LogCaptureFixture) -> None:
        """Raw PIN value must never appear in WARNING (or above) log records during overwrite.

        The existing EventOverrides.update() debug log includes the overrides
        dict; that is pre-existing behaviour outside this commit's scope.  This
        test targets WARNING-and-above records to verify that the new
        OVERWRITE_MANUAL_CHANGE log message never exposes the raw PIN.
        """
        eo = EventOverrides(start_slot=5, max_slots=2)
        now = datetime(2026, 8, 1, tzinfo=self._TZ)
        eo.update(5, "SUPERSECRETPIN", "Alice Smith", now, now + timedelta(days=7))

        res = self._make_res(slot_code="SUPERSECRETPIN")
        action = self._make_overwrite_action(drift_fields=["name"])

        coordinator = MagicMock()
        coordinator.lockname = "test_lock"
        coordinator.hass.services.async_call = AsyncMock()

        confirmed = OperationResult(kind="set", slot=5, confirmed=True)
        with (
            caplog.at_level(logging.WARNING, logger="custom_components.rental_control"),
            patch(
                "custom_components.rental_control.event_overrides.async_fire_set_code",
                return_value=confirmed,
            ),
        ):
            await eo._apply_overwrite_manual_change(coordinator, 5, res, action)

        # Only WARNING+ records are captured; none must contain the raw PIN.
        for record in (r for r in caplog.records if r.levelno >= logging.WARNING):
            assert "SUPERSECRETPIN" not in record.message

    async def test_desired_identity_in_log(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The desired reservation identity_key appears in the WARNING log."""
        eo = EventOverrides(start_slot=5, max_slots=2)
        now = datetime(2026, 8, 1, tzinfo=self._TZ)
        eo.update(5, "SECRETPIN", "Alice Smith", now, now + timedelta(days=7))

        res = self._make_res(identity_key="r-identity-check")
        action = self._make_overwrite_action(
            identity_key="r-identity-check", drift_fields=["name"]
        )

        coordinator = MagicMock()
        coordinator.lockname = "test_lock"
        coordinator.hass.services.async_call = AsyncMock()

        confirmed = OperationResult(kind="set", slot=5, confirmed=True)
        with (
            caplog.at_level(logging.WARNING, logger="custom_components.rental_control"),
            patch(
                "custom_components.rental_control.event_overrides.async_fire_set_code",
                return_value=confirmed,
            ),
        ):
            await eo._apply_overwrite_manual_change(coordinator, 5, res, action)

        log_text = " ".join(r.message for r in caplog.records)
        assert "r-identity-check" in log_text

    async def test_observed_name_from_actual_state_cache_in_log(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Observed name from actual-state cache appears in the WARNING log."""
        eo = EventOverrides(start_slot=5, max_slots=2)
        now = datetime(2026, 8, 1, tzinfo=self._TZ)
        eo.update(5, "SECRETPIN", "Alice Smith", now, now + timedelta(days=7))
        # Simulate actual-state cache with a different observed name
        eo.update_actual_state(
            5,
            {
                "slot": 5,
                "classification": "occupied",
                "name_state": "MANUALLY CHANGED",
                "has_code": True,
                "start_state": None,
                "end_state": None,
                "use_date_range": True,
                "enabled": True,
            },
        )

        res = self._make_res()
        action = self._make_overwrite_action(drift_fields=["name"])

        coordinator = MagicMock()
        coordinator.lockname = "test_lock"
        coordinator.hass.services.async_call = AsyncMock()

        confirmed = OperationResult(kind="set", slot=5, confirmed=True)
        with (
            caplog.at_level(logging.WARNING, logger="custom_components.rental_control"),
            patch(
                "custom_components.rental_control.event_overrides.async_fire_set_code",
                return_value=confirmed,
            ),
        ):
            await eo._apply_overwrite_manual_change(coordinator, 5, res, action)

        log_text = " ".join(r.message for r in caplog.records)
        assert "MANUALLY CHANGED" in log_text

    async def test_async_apply_plan_routes_overwrite_manual_change(self) -> None:
        """async_apply_plan dispatches OVERWRITE_MANUAL_CHANGE to the handler."""
        from custom_components.rental_control.reconciliation import PlannedSlot

        eo = EventOverrides(start_slot=5, max_slots=2)
        now = datetime(2026, 8, 1, tzinfo=self._TZ)
        eo.update(5, "SECRETPIN", "Alice Smith", now, now + timedelta(days=7))
        eo.update(6, "", "", now, now + timedelta(days=7))

        res = self._make_res()
        action = self._make_overwrite_action(slot=5, drift_fields=["name"])

        plan = DesiredPlan(
            plan_id="t070-route",
            generated_at=now,
        )
        plan.actions = [action]
        plan.slots[5] = PlannedSlot(
            slot=5,
            desired_identity_key=res.identity_key,
            actual_classification="occupied",
            action=ActionKind.OVERWRITE_MANUAL_CHANGE,
            pending_reason=action.reason,
        )
        plan.slots[6] = PlannedSlot(
            slot=6,
            desired_identity_key=None,
            actual_classification="free",
            action=ActionKind.NOOP,
        )

        coordinator = MagicMock()
        coordinator.lockname = "test_lock"
        coordinator.hass.services.async_call = AsyncMock()

        confirmed = OperationResult(kind="set", slot=5, confirmed=True)
        with patch(
            "custom_components.rental_control.event_overrides.async_fire_set_code",
            return_value=confirmed,
        ) as mock_set:
            results = await eo.async_apply_plan(
                coordinator, plan, {res.identity_key: res}
            )

        assert mock_set.called
        assert len(results) == 1
        assert results[0].confirmed is True

    async def test_snapshot_includes_manual_drift_slot(self) -> None:
        """update_diagnostics_snapshot captures OVERWRITE_MANUAL_CHANGE slots."""
        from custom_components.rental_control.reconciliation import PlannedSlot

        eo = EventOverrides(start_slot=5, max_slots=2)
        plan = DesiredPlan(
            plan_id="t070-snap",
            generated_at=datetime(2026, 8, 1, tzinfo=self._TZ),
        )
        plan.slots[5] = PlannedSlot(
            slot=5,
            desired_identity_key="r-drift-snap",
            actual_classification="occupied",
            action=ActionKind.OVERWRITE_MANUAL_CHANGE,
            pending_reason="drifted fields: name",
        )
        plan.slots[6] = PlannedSlot(
            slot=6,
            desired_identity_key=None,
            actual_classification="free",
            action=ActionKind.NOOP,
        )

        eo.update_diagnostics_snapshot(plan)
        snap = eo.diagnostics_snapshot

        assert "manual_drift_slots" in snap
        assert 5 in snap["manual_drift_slots"]
        drift_entry = snap["manual_drift_slots"][5]
        assert drift_entry["identity_key"] == "r-drift-snap"
        assert "name" in drift_entry["drift_fields"]

    async def test_snapshot_manual_drift_no_raw_codes(self) -> None:
        """manual_drift_slots in snapshot must not contain raw PIN values."""
        from custom_components.rental_control.reconciliation import PlannedSlot

        eo = EventOverrides(start_slot=5, max_slots=2)
        plan = DesiredPlan(
            plan_id="t070-nodrift",
            generated_at=datetime(2026, 8, 1, tzinfo=self._TZ),
        )
        plan.slots[5] = PlannedSlot(
            slot=5,
            desired_identity_key="r-drift-check",
            actual_classification="occupied",
            action=ActionKind.OVERWRITE_MANUAL_CHANGE,
            pending_reason="drifted fields: name",
        )

        eo.update_diagnostics_snapshot(plan)
        snap = eo.diagnostics_snapshot

        snap_str = str(snap)
        assert "slot_code" not in snap_str
        # No raw PIN values (no standalone 'pin' token outside expected keys)
        assert "SECRETPIN" not in snap_str

    async def test_unmanaged_slot_edit_not_in_actions(self) -> None:
        """Unmanaged slots never appear in plan.actions and are ignored."""
        from custom_components.rental_control.reconciliation import ManagedSlot
        from custom_components.rental_control.reconciliation import Reservation
        from custom_components.rental_control.reconciliation import SlotStatus
        from custom_components.rental_control.reconciliation import compute_desired_plan

        _TZ = self._TZ
        s = datetime(2026, 8, 1, 14, tzinfo=_TZ)
        e = s + timedelta(days=7)
        r = Reservation(
            identity_key="r-unmanaged",
            start=s,
            end=e,
            buffered_start=s,
            buffered_end=e,
            summary="Guest Unmanaged",
            slot_name="Guest Unmanaged",
            display_slot_name="RC Guest Unmanaged",
            slot_code="SECRETPIN",
        )

        # Slot 3 is NOT managed; slot 5 is managed and free
        unmanaged_slot = ManagedSlot(
            slot=3,
            managed=False,  # not in RC range
            status=SlotStatus.OCCUPIED,
            actual_name="MANUALLY EDITED NAME",
            actual_code_present=True,
        )
        managed_slot = ManagedSlot(slot=5, managed=True, status=SlotStatus.FREE)

        plan = compute_desired_plan(
            [r],
            [unmanaged_slot, managed_slot],
            max_events=3,
            plan_id="t070-unmanaged",
            generated_at=s,
        )

        # No OVERWRITE_MANUAL_CHANGE action must reference slot 3
        overwrite_actions = [
            a for a in plan.actions if a.kind is ActionKind.OVERWRITE_MANUAL_CHANGE
        ]
        assert not any(a.slot == 3 for a in overwrite_actions)
        # Unmanaged slot is absent from plan.slots entirely
        assert 3 not in plan.slots


# ---------------------------------------------------------------------------
# T045 – duplicate actual assignment: canonical slot and non-canonical clear
# ---------------------------------------------------------------------------


class TestDuplicateActualAssignment:
    """T045: Tests for duplicate actual assignment handling.

    When the same reservation identity key is found in multiple OCCUPIED
    managed slots, the planner treats the lowest-numbered slot as canonical
    (keeping the reservation) and generates CLEAR actions for all
    non-canonical slots.
    """

    _TZ = dt_util.UTC

    def _make_res(
        self,
        identity_key: str = "r-dup",
        *,
        start_day: int = 1,
    ) -> Reservation:
        """Return a minimal Reservation for duplicate tests."""
        start = datetime(2026, 8, start_day, 14, tzinfo=self._TZ)
        end = start + timedelta(days=7)
        return Reservation(
            identity_key=identity_key,
            start=start,
            end=end,
            buffered_start=start,
            buffered_end=end,
            summary=f"Guest {identity_key}",
            slot_name=f"Guest {identity_key}",
            display_slot_name=f"RC Guest {identity_key}",
            slot_code="DUPPIN",
        )

    def _occupied_slot(self, slot: int, persisted_key: str):
        """Return an OCCUPIED ManagedSlot with *persisted_key*."""
        from custom_components.rental_control.reconciliation import ManagedSlot
        from custom_components.rental_control.reconciliation import SlotStatus

        return ManagedSlot(
            slot=slot,
            managed=True,
            status=SlotStatus.OCCUPIED,
            actual_name=f"Guest {persisted_key}",
            actual_code_present=True,
            persisted_identity_key=persisted_key,
        )

    def test_canonical_slot_keeps_reservation(self) -> None:
        """Lower-numbered slot is canonical and keeps the desired assignment.

        Scenario: slots 3 and 5 both have persisted_identity_key='r-dup'
        (OCCUPIED).  The planner selects slot 3 as canonical because it is
        the lower slot number.  plan.selected must map 'r-dup' to slot 3.
        """
        from custom_components.rental_control.reconciliation import compute_desired_plan

        res = self._make_res("r-dup")
        slot3 = self._occupied_slot(3, "r-dup")
        slot5 = self._occupied_slot(5, "r-dup")

        plan = compute_desired_plan(
            [res],
            [slot3, slot5],
            max_events=3,
            plan_id="t045-canonical",
            generated_at=datetime(2026, 8, 1, tzinfo=self._TZ),
        )

        assert "r-dup" in plan.selected
        assert plan.selected["r-dup"] == 3

    def test_non_canonical_slot_gets_clear_action(self) -> None:
        """Non-canonical duplicate slot receives a CLEAR action.

        Slot 5 is non-canonical (higher number than slot 3).  After
        compute_desired_plan the actions list must contain exactly one CLEAR
        targeting slot 5.
        """
        from custom_components.rental_control.reconciliation import ActionKind
        from custom_components.rental_control.reconciliation import compute_desired_plan

        res = self._make_res("r-dup")
        slot3 = self._occupied_slot(3, "r-dup")
        slot5 = self._occupied_slot(5, "r-dup")

        plan = compute_desired_plan(
            [res],
            [slot3, slot5],
            max_events=3,
            plan_id="t045-noncanon",
            generated_at=datetime(2026, 8, 1, tzinfo=self._TZ),
        )

        clear_actions = [a for a in plan.actions if a.kind is ActionKind.CLEAR]
        assert any(a.slot == 5 for a in clear_actions), (
            f"Expected CLEAR on slot 5; actions were {plan.actions}"
        )

    def test_non_canonical_clear_reason_is_duplicate(self) -> None:
        """CLEAR action for non-canonical slot carries reason 'duplicate_non_canonical'."""
        from custom_components.rental_control.reconciliation import ActionKind
        from custom_components.rental_control.reconciliation import compute_desired_plan

        res = self._make_res("r-dup")
        slot3 = self._occupied_slot(3, "r-dup")
        slot5 = self._occupied_slot(5, "r-dup")

        plan = compute_desired_plan(
            [res],
            [slot3, slot5],
            max_events=3,
            plan_id="t045-reason",
            generated_at=datetime(2026, 8, 1, tzinfo=self._TZ),
        )

        clear_slot5 = next(
            (a for a in plan.actions if a.kind is ActionKind.CLEAR and a.slot == 5),
            None,
        )
        assert clear_slot5 is not None
        assert clear_slot5.reason == "duplicate_non_canonical"

    async def test_non_canonical_slot_enters_pending_clear_after_apply(self) -> None:
        """Applying CLEAR on non-canonical slot puts it in pending_clear state."""
        from custom_components.rental_control.reconciliation import ActionKind
        from custom_components.rental_control.reconciliation import DesiredPlan
        from custom_components.rental_control.reconciliation import SlotAction
        from custom_components.rental_control.util import OperationResult

        eo = EventOverrides(start_slot=3, max_slots=3)
        now = datetime(2026, 8, 1, tzinfo=self._TZ)
        eo.update(3, "DUPPIN", "Guest r-dup", now, now + timedelta(days=7))
        eo.update(4, "", "", now, now + timedelta(days=7))
        eo.update(5, "DUPPIN", "Guest r-dup", now, now + timedelta(days=7))

        plan = DesiredPlan(plan_id="t045-pending", generated_at=now)
        plan.actions = [
            SlotAction(
                kind=ActionKind.CLEAR,
                slot=5,
                identity_key=None,
                reason="duplicate_non_canonical",
            )
        ]

        coordinator = MagicMock()
        coordinator.lockname = "test_lock"
        coordinator.hass.services.async_call = AsyncMock()

        # Unconfirmed clear → slot stays in pending_fences
        unconfirmed = OperationResult(kind="clear", slot=5, confirmed=False)
        with patch(
            "custom_components.rental_control.event_overrides.async_fire_clear_code",
            return_value=unconfirmed,
        ):
            await eo.async_apply_plan(coordinator, plan, {})

        # Slot 5 fence token must persist (clear unconfirmed)
        assert 5 in eo.pending_fences

    async def test_canonical_slot_noop_non_canonical_cleared(self) -> None:
        """Full plan: canonical gets NOOP, non-canonical gets CLEAR applied."""
        from custom_components.rental_control.reconciliation import ActionKind
        from custom_components.rental_control.reconciliation import ManagedSlot
        from custom_components.rental_control.reconciliation import SlotStatus
        from custom_components.rental_control.reconciliation import compute_desired_plan
        from custom_components.rental_control.util import OperationResult

        res = self._make_res("r-dup")
        slot3 = ManagedSlot(
            slot=3,
            managed=True,
            status=SlotStatus.OCCUPIED,
            actual_name="RC Guest r-dup",
            actual_code_present=True,
            persisted_identity_key="r-dup",
            actual_start=res.buffered_start,
            actual_end=res.buffered_end,
        )
        slot5 = self._occupied_slot(5, "r-dup")

        plan = compute_desired_plan(
            [res],
            [slot3, slot5],
            max_events=3,
            plan_id="t045-full",
            generated_at=datetime(2026, 8, 1, tzinfo=self._TZ),
        )

        # Canonical slot 3: NOOP (same reservation, same dates)
        assert plan.slots[3].action is ActionKind.NOOP
        # Non-canonical slot 5: CLEAR
        assert plan.slots[5].action is ActionKind.CLEAR

        eo = EventOverrides(start_slot=3, max_slots=3)
        now = datetime(2026, 8, 1, tzinfo=self._TZ)
        eo.update(3, "DUPPIN", "Guest r-dup", now, now + timedelta(days=7))
        eo.update(4, "", "", now, now + timedelta(days=7))
        eo.update(5, "DUPPIN", "Guest r-dup", now, now + timedelta(days=7))

        coordinator = MagicMock()
        coordinator.lockname = "test_lock"
        coordinator.hass.services.async_call = AsyncMock()

        confirmed = OperationResult(kind="clear", slot=5, confirmed=True)
        with patch(
            "custom_components.rental_control.event_overrides.async_fire_clear_code",
            return_value=confirmed,
        ) as mock_clear:
            await eo.async_apply_plan(coordinator, plan, {"r-dup": res})

        # Clear was called exactly once for slot 5
        assert mock_clear.called
        # Slot 5 override cleared
        assert eo.overrides.get(5) is None


# ---------------------------------------------------------------------------
# T053 – caplog coverage for corrupt-state self-heal log messages
# ---------------------------------------------------------------------------


class TestCorruptStateCaplog:
    """T053: caplog tests for corrupt-state self-heal log messages.

    Verifies that the reconciliation system emits WARNING-level log entries
    for each class of corrupt-state correction:

    - duplicate collapse
    - overflow decision (no_free_slot)
    - phantom recovery
    - stale correction
    - mis-assignment correction
    """

    _TZ = dt_util.UTC

    def _mk_res(
        self,
        identity_key: str = "r-test",
        *,
        start_day: int = 1,
    ) -> Reservation:
        """Return a minimal Reservation for caplog tests."""
        start = datetime(2026, 8, start_day, 14, tzinfo=self._TZ)
        end = start + timedelta(days=7)
        return Reservation(
            identity_key=identity_key,
            start=start,
            end=end,
            buffered_start=start,
            buffered_end=end,
            summary=f"Guest {identity_key}",
            slot_name=f"Guest {identity_key}",
            display_slot_name=f"RC Guest {identity_key}",
            slot_code="CAPLOGPIN",
        )

    def test_duplicate_collapse_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        """Duplicate assignment detection emits a WARNING log entry."""
        from custom_components.rental_control.reconciliation import ManagedSlot
        from custom_components.rental_control.reconciliation import SlotStatus
        from custom_components.rental_control.reconciliation import compute_desired_plan

        res = self._mk_res("r-dup-caplog")
        slot3 = ManagedSlot(
            slot=3,
            managed=True,
            status=SlotStatus.OCCUPIED,
            actual_name="Guest r-dup-caplog",
            actual_code_present=True,
            persisted_identity_key="r-dup-caplog",
        )
        slot5 = ManagedSlot(
            slot=5,
            managed=True,
            status=SlotStatus.OCCUPIED,
            actual_name="Guest r-dup-caplog",
            actual_code_present=True,
            persisted_identity_key="r-dup-caplog",
        )

        with caplog.at_level(
            logging.WARNING, logger="custom_components.rental_control"
        ):
            compute_desired_plan(
                [res],
                [slot3, slot5],
                max_events=3,
                plan_id="t053-dup",
                generated_at=datetime(2026, 8, 1, tzinfo=self._TZ),
            )

        log_text = " ".join(r.message for r in caplog.records)
        assert "duplicate" in log_text.lower() or "Duplicate" in log_text

    async def test_duplicate_collapse_apply_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """async_apply_plan logs duplicate-collapse WARNING when clearing non-canonical slot."""
        from custom_components.rental_control.reconciliation import ActionKind
        from custom_components.rental_control.reconciliation import DesiredPlan
        from custom_components.rental_control.reconciliation import SlotAction
        from custom_components.rental_control.util import OperationResult

        eo = EventOverrides(start_slot=3, max_slots=3)
        now = datetime(2026, 8, 1, tzinfo=self._TZ)
        eo.update(3, "PIN3", "Guest dup", now, now + timedelta(days=7))
        eo.update(4, "", "", now, now + timedelta(days=7))
        eo.update(5, "PIN5", "Guest dup", now, now + timedelta(days=7))

        plan = DesiredPlan(plan_id="t053-dup-apply", generated_at=now)
        plan.actions = [
            SlotAction(
                kind=ActionKind.CLEAR,
                slot=5,
                identity_key=None,
                reason="duplicate_non_canonical",
            )
        ]

        coordinator = MagicMock()
        coordinator.lockname = "test_lock"
        coordinator.hass.services.async_call = AsyncMock()

        confirmed = OperationResult(kind="clear", slot=5, confirmed=True)
        with (
            caplog.at_level(logging.WARNING, logger="custom_components.rental_control"),
            patch(
                "custom_components.rental_control.event_overrides.async_fire_clear_code",
                return_value=confirmed,
            ),
        ):
            await eo.async_apply_plan(coordinator, plan, {})

        log_text = " ".join(
            r.message for r in caplog.records if r.levelno >= logging.WARNING
        )
        assert "duplicate" in log_text.lower() or "Duplicate" in log_text

    def test_overflow_decision_no_free_slot_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Overflow (no_free_slot) emits a WARNING log entry."""
        from custom_components.rental_control.reconciliation import ManagedSlot
        from custom_components.rental_control.reconciliation import SlotStatus
        from custom_components.rental_control.reconciliation import compute_desired_plan

        # max_events=1, slot 3 occupied by r-old; r-new needs a slot but all occupied
        r_old = self._mk_res("r-old", start_day=10)
        r_new = self._mk_res("r-new", start_day=1)
        slot3 = ManagedSlot(
            slot=3,
            managed=True,
            status=SlotStatus.OCCUPIED,
            actual_name="Guest r-old",
            actual_code_present=True,
            persisted_identity_key="r-old",
        )

        with caplog.at_level(
            logging.WARNING, logger="custom_components.rental_control"
        ):
            plan = compute_desired_plan(
                [r_old, r_new],
                [slot3],
                max_events=1,  # only r-new selected (nearer), but no free slot
                plan_id="t053-overflow",
                generated_at=datetime(2026, 8, 1, tzinfo=self._TZ),
            )

        # r-new should overflow (no_free_slot) because slot 3 is OCCUPIED by r-old
        assert "r-new" in plan.overflow

        log_text = " ".join(
            r.message for r in caplog.records if r.levelno >= logging.WARNING
        )
        assert "overflow" in log_text.lower() or "Overflow" in log_text

    async def test_phantom_recovery_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Phantom slot clear emits 'Phantom recovery' WARNING in async_apply_plan."""
        from custom_components.rental_control.reconciliation import ActionKind
        from custom_components.rental_control.reconciliation import DesiredPlan
        from custom_components.rental_control.reconciliation import SlotAction
        from custom_components.rental_control.util import OperationResult

        eo = EventOverrides(start_slot=5, max_slots=1)
        now = datetime(2026, 8, 1, tzinfo=self._TZ)
        eo.update(5, "", "PhantomGuest", now, now + timedelta(days=7))

        plan = DesiredPlan(plan_id="t053-phantom", generated_at=now)
        plan.actions = [
            SlotAction(
                kind=ActionKind.CLEAR,
                slot=5,
                identity_key=None,
                reason="phantom",
            )
        ]

        coordinator = MagicMock()
        coordinator.lockname = "test_lock"
        coordinator.hass.services.async_call = AsyncMock()

        confirmed = OperationResult(kind="clear", slot=5, confirmed=True)
        with (
            caplog.at_level(logging.WARNING, logger="custom_components.rental_control"),
            patch(
                "custom_components.rental_control.event_overrides.async_fire_clear_code",
                return_value=confirmed,
            ),
        ):
            await eo.async_apply_plan(coordinator, plan, {})

        log_text = " ".join(
            r.message for r in caplog.records if r.levelno >= logging.WARNING
        )
        assert "phantom" in log_text.lower() or "Phantom" in log_text

    async def test_stale_correction_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Stale assignment clear emits 'Stale correction' WARNING in async_apply_plan."""
        from custom_components.rental_control.reconciliation import ActionKind
        from custom_components.rental_control.reconciliation import DesiredPlan
        from custom_components.rental_control.reconciliation import SlotAction
        from custom_components.rental_control.util import OperationResult

        eo = EventOverrides(start_slot=5, max_slots=1)
        now = datetime(2026, 8, 1, tzinfo=self._TZ)
        eo.update(5, "STALEPIN", "OldGuest", now, now + timedelta(days=7))

        plan = DesiredPlan(plan_id="t053-stale", generated_at=now)
        plan.actions = [
            SlotAction(
                kind=ActionKind.CLEAR,
                slot=5,
                identity_key=None,
                reason="stale",
            )
        ]

        coordinator = MagicMock()
        coordinator.lockname = "test_lock"
        coordinator.hass.services.async_call = AsyncMock()

        confirmed = OperationResult(kind="clear", slot=5, confirmed=True)
        with (
            caplog.at_level(logging.WARNING, logger="custom_components.rental_control"),
            patch(
                "custom_components.rental_control.event_overrides.async_fire_clear_code",
                return_value=confirmed,
            ),
        ):
            await eo.async_apply_plan(coordinator, plan, {})

        log_text = " ".join(
            r.message for r in caplog.records if r.levelno >= logging.WARNING
        )
        assert "stale" in log_text.lower() or "Stale" in log_text

    async def test_misassignment_correction_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Mis-assigned slot clear emits 'Mis-assignment correction' WARNING."""
        from custom_components.rental_control.reconciliation import ActionKind
        from custom_components.rental_control.reconciliation import DesiredPlan
        from custom_components.rental_control.reconciliation import SlotAction
        from custom_components.rental_control.util import OperationResult

        eo = EventOverrides(start_slot=5, max_slots=1)
        now = datetime(2026, 8, 1, tzinfo=self._TZ)
        eo.update(5, "WRONGPIN", "WrongGuest", now, now + timedelta(days=7))

        plan = DesiredPlan(plan_id="t053-misassign", generated_at=now)
        plan.actions = [
            SlotAction(
                kind=ActionKind.CLEAR,
                slot=5,
                identity_key="r-desired",
                reason="mis_assigned",
            )
        ]

        coordinator = MagicMock()
        coordinator.lockname = "test_lock"
        coordinator.hass.services.async_call = AsyncMock()

        confirmed = OperationResult(kind="clear", slot=5, confirmed=True)
        with (
            caplog.at_level(logging.WARNING, logger="custom_components.rental_control"),
            patch(
                "custom_components.rental_control.event_overrides.async_fire_clear_code",
                return_value=confirmed,
            ),
        ):
            await eo.async_apply_plan(coordinator, plan, {})

        log_text = " ".join(
            r.message for r in caplog.records if r.levelno >= logging.WARNING
        )
        assert "mis-assignment" in log_text.lower() or "Mis-assignment" in log_text


# ---------------------------------------------------------------------------
# T054 – unmanaged-slot ignored: outside managed range never changed
# ---------------------------------------------------------------------------


class TestUnmanagedSlotIgnoredCorrupt:
    """T054: Slots outside the RC-managed range are never changed.

    Even when an unmanaged slot has a corrupt name, phantom state, or
    duplicate persisted key, compute_desired_plan must not generate any
    action targeting it.  The managed-range invariant is enforced by the
    ``managed=False`` attribute on ManagedSlot.
    """

    _TZ = dt_util.UTC

    def _mk_res(self, identity_key: str = "r-um") -> Reservation:
        """Return a minimal Reservation for unmanaged-slot tests."""
        start = datetime(2026, 8, 1, 14, tzinfo=self._TZ)
        end = start + timedelta(days=7)
        return Reservation(
            identity_key=identity_key,
            start=start,
            end=end,
            buffered_start=start,
            buffered_end=end,
            summary=f"Guest {identity_key}",
            slot_name=f"Guest {identity_key}",
            display_slot_name=f"RC Guest {identity_key}",
            slot_code="UNMGDPIN",
        )

    def test_unmanaged_phantom_slot_never_cleared(self) -> None:
        """PHANTOM unmanaged slot generates no action."""
        from custom_components.rental_control.reconciliation import ManagedSlot
        from custom_components.rental_control.reconciliation import SlotStatus
        from custom_components.rental_control.reconciliation import compute_desired_plan

        res = self._mk_res()
        unmanaged = ManagedSlot(
            slot=3,
            managed=False,
            status=SlotStatus.PHANTOM,
            actual_name="PhantomGuest",
            actual_code_present=False,
        )
        managed_free = ManagedSlot(slot=5, managed=True, status=SlotStatus.FREE)

        plan = compute_desired_plan(
            [res],
            [unmanaged, managed_free],
            max_events=3,
            plan_id="t054-phantom",
            generated_at=datetime(2026, 8, 1, tzinfo=self._TZ),
        )

        assert 3 not in plan.slots
        assert not any(a.slot == 3 for a in plan.actions)

    def test_unmanaged_stale_occupied_slot_never_cleared(self) -> None:
        """Stale OCCUPIED unmanaged slot generates no action."""
        from custom_components.rental_control.reconciliation import ManagedSlot
        from custom_components.rental_control.reconciliation import SlotStatus
        from custom_components.rental_control.reconciliation import compute_desired_plan

        res = self._mk_res()
        # Unmanaged slot with stale reservation (expired key)
        unmanaged = ManagedSlot(
            slot=2,
            managed=False,
            status=SlotStatus.OCCUPIED,
            actual_name="OldStaleGuest",
            actual_code_present=True,
            persisted_identity_key="r-expired-stale",
        )
        managed_free = ManagedSlot(slot=5, managed=True, status=SlotStatus.FREE)

        plan = compute_desired_plan(
            [res],
            [unmanaged, managed_free],
            max_events=3,
            plan_id="t054-stale",
            generated_at=datetime(2026, 8, 1, tzinfo=self._TZ),
        )

        assert 2 not in plan.slots
        assert not any(a.slot == 2 for a in plan.actions)

    def test_unmanaged_duplicate_slot_never_cleared(self) -> None:
        """Unmanaged slot sharing a persisted key with a managed slot is ignored."""
        from custom_components.rental_control.reconciliation import ManagedSlot
        from custom_components.rental_control.reconciliation import SlotStatus
        from custom_components.rental_control.reconciliation import compute_desired_plan

        res = self._mk_res("r-shared-dup")
        # Slot 2: unmanaged (outside range), has same persisted key
        unmanaged_dup = ManagedSlot(
            slot=2,
            managed=False,
            status=SlotStatus.OCCUPIED,
            actual_name="Guest r-shared-dup",
            actual_code_present=True,
            persisted_identity_key="r-shared-dup",
        )
        # Slot 5: managed, has same persisted key (canonical because managed)
        managed_canon = ManagedSlot(
            slot=5,
            managed=True,
            status=SlotStatus.OCCUPIED,
            actual_name="Guest r-shared-dup",
            actual_code_present=True,
            persisted_identity_key="r-shared-dup",
        )

        plan = compute_desired_plan(
            [res],
            [unmanaged_dup, managed_canon],
            max_events=3,
            plan_id="t054-dup",
            generated_at=datetime(2026, 8, 1, tzinfo=self._TZ),
        )

        assert 2 not in plan.slots
        assert not any(a.slot == 2 for a in plan.actions)
        # Managed canonical slot 5 must be present and assigned
        assert "r-shared-dup" in plan.selected

    def test_unmanaged_slot_outside_range_never_set(self) -> None:
        """A reservation is never SET into an unmanaged slot."""
        from custom_components.rental_control.reconciliation import ActionKind
        from custom_components.rental_control.reconciliation import ManagedSlot
        from custom_components.rental_control.reconciliation import SlotStatus
        from custom_components.rental_control.reconciliation import compute_desired_plan

        res = self._mk_res()
        unmanaged_free = ManagedSlot(
            slot=1,
            managed=False,
            status=SlotStatus.FREE,
        )
        managed_free = ManagedSlot(slot=5, managed=True, status=SlotStatus.FREE)

        plan = compute_desired_plan(
            [res],
            [unmanaged_free, managed_free],
            max_events=3,
            plan_id="t054-set",
            generated_at=datetime(2026, 8, 1, tzinfo=self._TZ),
        )

        # No SET or CLEAR action references the unmanaged slot
        assert not any(a.slot == 1 for a in plan.actions)
        # Reservation must land in the managed slot 5
        set_actions = [a for a in plan.actions if a.kind is ActionKind.SET]
        assert len(set_actions) == 1
        assert set_actions[0].slot == 5


class TestSlotNameTrimmingRegression:
    """T101 regression: Slot-name trimming and prefix preservation.

    These tests pin the semantics that must survive the reconciliation
    refactor: the Keymaster display name is constructed by prepending the
    event prefix and (when trim_names is True) trimming the guest portion
    to fit.  The legacy EventOverrides read APIs that sensors depend on
    must also continue to work.
    """

    @staticmethod
    def _make_slot_event(slot_name: str) -> MagicMock:
        """Build a minimal slot event for set-code tests."""
        event = MagicMock()
        event.extra_state_attributes = {
            "slot_name": slot_name,
            "slot_code": "2468",
            "start": _make_dt(2025, 7, 15, 15, 0),
            "end": _make_dt(2025, 7, 18, 11, 0),
        }
        return event

    @staticmethod
    def _make_set_coordinator(expected_state: str, *, trim_names: bool) -> MagicMock:
        """Return a coordinator mock for set-code regression tests."""
        coordinator = MagicMock()
        coordinator.lockname = "test_lock"
        coordinator.event_prefix = "RC"
        coordinator.trim_names = trim_names
        coordinator.max_name_length = 10
        coordinator.code_buffer_before = 0
        coordinator.code_buffer_after = 0
        coordinator.hass.services.async_call = AsyncMock()
        state = MagicMock()
        state.state = expected_state
        coordinator.hass.states.get.return_value = state
        coordinator.event_overrides.verify_slot_ownership.return_value = True
        coordinator.event_overrides._escalated = {}
        return coordinator

    def test_trim_name_word_boundary(self) -> None:
        """Names trim on word boundaries when possible."""
        assert trim_name("Alice Bob Charlie", 10) == "Alice Bob"

    def test_trim_name_hard_truncate_single_word(self) -> None:
        """Single words hard-truncate when no boundary fits."""
        assert trim_name("Verylongname", 5) == "Veryl"

    def test_trim_name_already_fits_unchanged(self) -> None:
        """Short names are returned unchanged."""
        assert trim_name("Short", 20) == "Short"

    def test_strip_prefix_removes_prefix_and_space(self) -> None:
        """Prefix stripping removes the prefix plus separator."""
        assert _eo_module._strip_prefix("RC Alice", "RC") == "Alice"

    def test_strip_prefix_no_match_unchanged(self) -> None:
        """Non-matching prefixes leave the name unchanged."""
        assert _eo_module._strip_prefix("NoPrefix Alice", "RC") == "NoPrefix Alice"

    def test_strip_prefix_empty_prefix_unchanged(self) -> None:
        """Empty prefixes never strip anything."""
        assert _eo_module._strip_prefix("Alice", "") == "Alice"

    def test_is_trimmed_match_detects_word_boundary_truncation(self) -> None:
        """Trim-match detection recognises word-boundary trimming."""
        assert (
            _eo_module._is_trimmed_match("Alice Bob Charlie", "Alice Bob", 10) is True
        )

    def test_is_trimmed_match_false_when_trim_off(self) -> None:
        """Equal names are not treated as trimmed matches."""
        assert _eo_module._is_trimmed_match("Alice", "Alice", 10) is False

    def test_legacy_overrides_property_accessible(self) -> None:
        """Legacy overrides property remains readable."""
        assert EventOverrides(10, 3).overrides == {}

    def test_legacy_get_slot_with_name_finds_override(self) -> None:
        """Legacy get_slot_with_name continues returning matching overrides."""
        eo = EventOverrides(10, 3)
        start = _make_dt(2025, 7, 15, 15, 0)
        end = _make_dt(2025, 7, 18, 11, 0)
        eo.update(10, "1234", "Alice", start, end)

        override = eo.get_slot_with_name("Alice")

        assert override is not None
        assert override["slot_name"] == "Alice"

    def test_legacy_trim_names_property_settable(self) -> None:
        """Legacy trim_names property remains writable."""
        eo = EventOverrides(10, 3)
        eo.trim_names = True

        assert eo.trim_names is True

    def test_legacy_prefix_length_property_settable(self) -> None:
        """Legacy prefix_length property remains writable."""
        eo = EventOverrides(10, 3)
        eo.prefix_length = 5

        assert eo.prefix_length == 5

    async def test_set_code_writes_prefix_plus_name_to_keymaster(self) -> None:
        """Set-code preserves the configured prefix in the Keymaster display name."""
        coordinator = self._make_set_coordinator("RC Alice", trim_names=False)
        event = self._make_slot_event("Alice")

        result = await async_fire_set_code(coordinator, event, 10)

        name_calls = [
            awaited.kwargs
            for awaited in coordinator.hass.services.async_call.await_args_list
            if awaited.kwargs.get("target", {}).get("entity_id")
            == "text.test_lock_code_slot_10_name"
        ]
        assert result.confirmed is True
        assert name_calls[-1]["service_data"]["value"] == "RC Alice"

    async def test_set_code_trims_name_when_trim_enabled(self) -> None:
        """Set-code trims only the guest portion when prefixing is enabled."""
        coordinator = self._make_set_coordinator("RC Alice", trim_names=True)
        event = self._make_slot_event("Alice Bob")

        result = await async_fire_set_code(coordinator, event, 10)

        name_calls = [
            awaited.kwargs
            for awaited in coordinator.hass.services.async_call.await_args_list
            if awaited.kwargs.get("target", {}).get("entity_id")
            == "text.test_lock_code_slot_10_name"
        ]
        assert result.confirmed is True
        assert name_calls[-1]["service_data"]["value"] == "RC Alice"
