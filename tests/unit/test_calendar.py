# SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the Rental Control calendar platform."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

from homeassistant.components.calendar import CalendarEvent
from homeassistant.helpers.entity import EntityCategory

from custom_components.rental_control.calendar import RentalControlCalendar
from custom_components.rental_control.calendar import async_setup_entry
from custom_components.rental_control.const import COORDINATOR
from custom_components.rental_control.const import DOMAIN
from custom_components.rental_control.const import NAME
from custom_components.rental_control.util import gen_uuid


def _mock_coordinator(
    *,
    name: str = "Test Rental",
    unique_id: str = "test_unique_id",
    calendar_ready: bool = False,
    event: CalendarEvent | None = None,
) -> MagicMock:
    """Create a mock RentalControlCoordinator for testing."""
    coordinator = MagicMock()
    coordinator.name = name
    coordinator.unique_id = unique_id
    coordinator.device_info = {
        "identifiers": {(DOMAIN, unique_id)},
        "name": f"{NAME} {name}",
        "manufacturer": "Andrew Grimberg",
    }
    coordinator.event = event
    coordinator.calendar_ready = calendar_ready
    coordinator.calendar = []
    coordinator.update = AsyncMock()
    coordinator.async_get_events = AsyncMock(return_value=[])
    return coordinator


# ---------------------------------------------------------------------------
# Entity creation and property tests (T040, T054)
# ---------------------------------------------------------------------------


class TestRentalControlCalendarInit:
    """Tests for RentalControlCalendar initialization."""

    def test_name_includes_coordinator_name(self) -> None:
        """Verify entity name is '{NAME} {coordinator.name}'."""
        coordinator = _mock_coordinator(name="Beach House")
        cal = RentalControlCalendar(coordinator)
        assert cal.name == f"{NAME} Beach House"

    def test_available_defaults_to_false(self) -> None:
        """Verify available is False after initialization."""
        coordinator = _mock_coordinator()
        cal = RentalControlCalendar(coordinator)
        assert cal.available is False

    def test_entity_category_is_diagnostic(self) -> None:
        """Verify entity_category is EntityCategory.DIAGNOSTIC."""
        coordinator = _mock_coordinator()
        cal = RentalControlCalendar(coordinator)
        assert cal.entity_category is EntityCategory.DIAGNOSTIC

    def test_event_defaults_to_none(self) -> None:
        """Verify event is None after initialization."""
        coordinator = _mock_coordinator()
        cal = RentalControlCalendar(coordinator)
        assert cal.event is None

    def test_unique_id_is_deterministic(self) -> None:
        """Verify unique_id matches gen_uuid with coordinator unique_id."""
        coordinator = _mock_coordinator(unique_id="my_rental_id")
        cal = RentalControlCalendar(coordinator)
        expected = gen_uuid("my_rental_id calendar")
        assert cal.unique_id == expected

    def test_unique_id_differs_for_different_coordinators(self) -> None:
        """Verify different coordinator unique_ids produce different entity IDs."""
        cal_a = RentalControlCalendar(_mock_coordinator(unique_id="rental_a"))
        cal_b = RentalControlCalendar(_mock_coordinator(unique_id="rental_b"))
        assert cal_a.unique_id != cal_b.unique_id

    def test_device_info_delegates_to_coordinator(self) -> None:
        """Verify device_info returns the coordinator's device_info."""
        coordinator = _mock_coordinator()
        cal = RentalControlCalendar(coordinator)
        assert cal.device_info is coordinator.device_info

    def test_coordinator_stored_on_instance(self) -> None:
        """Verify the coordinator reference is stored on the entity."""
        coordinator = _mock_coordinator()
        cal = RentalControlCalendar(coordinator)
        assert cal.coordinator is coordinator


# ---------------------------------------------------------------------------
# async_setup_entry tests (T040)
# ---------------------------------------------------------------------------


class TestAsyncSetupEntry:
    """Tests for the async_setup_entry platform function."""

    async def test_creates_entity_and_adds(self) -> None:
        """Verify async_setup_entry creates a calendar entity and registers it."""
        coordinator = _mock_coordinator()

        hass = MagicMock()
        hass.data = {
            DOMAIN: {
                "test_entry_id": {COORDINATOR: coordinator},
            },
        }

        config_entry = MagicMock()
        config_entry.entry_id = "test_entry_id"
        config_entry.data = {"name": "Test Rental"}

        async_add_entities = MagicMock()

        result = await async_setup_entry(hass, config_entry, async_add_entities)

        assert result is True
        async_add_entities.assert_called_once()
        args, kwargs = async_add_entities.call_args
        entities = args[0]
        assert len(entities) == 1
        assert isinstance(entities[0], RentalControlCalendar)
        # Second positional arg is update_before_add=True
        assert args[1] is True

    async def test_entity_uses_correct_coordinator(self) -> None:
        """Verify the created entity references the correct coordinator."""
        coordinator = _mock_coordinator(name="Lake Cabin")

        hass = MagicMock()
        hass.data = {
            DOMAIN: {
                "entry_123": {COORDINATOR: coordinator},
            },
        }

        config_entry = MagicMock()
        config_entry.entry_id = "entry_123"
        config_entry.data = {"name": "Lake Cabin"}

        async_add_entities = MagicMock()

        await async_setup_entry(hass, config_entry, async_add_entities)

        entity = async_add_entities.call_args[0][0][0]
        assert entity.coordinator is coordinator
        assert entity.name == f"{NAME} Lake Cabin"


# ---------------------------------------------------------------------------
# async_update tests (T041-T053)
# ---------------------------------------------------------------------------


class TestAsyncUpdate:
    """Tests for RentalControlCalendar.async_update."""

    async def test_calls_coordinator_update(self) -> None:
        """Verify async_update calls coordinator.update()."""
        coordinator = _mock_coordinator()
        cal = RentalControlCalendar(coordinator)

        await cal.async_update()

        coordinator.update.assert_awaited_once()

    async def test_sets_event_from_coordinator(self) -> None:
        """Verify async_update copies coordinator.event to the entity."""
        mock_event = CalendarEvent(
            summary="Guest Reservation",
            start=datetime(2025, 7, 1, 16, 0, tzinfo=timezone.utc),
            end=datetime(2025, 7, 5, 11, 0, tzinfo=timezone.utc),
        )
        coordinator = _mock_coordinator(calendar_ready=True, event=mock_event)
        cal = RentalControlCalendar(coordinator)

        assert cal.event is None
        await cal.async_update()
        assert cal.event is mock_event

    async def test_available_true_when_calendar_ready(self) -> None:
        """Verify available becomes True when coordinator.calendar_ready is True."""
        coordinator = _mock_coordinator(calendar_ready=True)
        cal = RentalControlCalendar(coordinator)

        assert cal.available is False
        await cal.async_update()
        assert cal.available is True

    async def test_available_stays_false_when_not_ready(self) -> None:
        """Verify available stays False when coordinator.calendar_ready is False."""
        coordinator = _mock_coordinator(calendar_ready=False)
        cal = RentalControlCalendar(coordinator)

        await cal.async_update()
        assert cal.available is False

    async def test_event_none_when_coordinator_event_none(self) -> None:
        """Verify event stays None when coordinator has no event."""
        coordinator = _mock_coordinator(calendar_ready=True, event=None)
        cal = RentalControlCalendar(coordinator)

        await cal.async_update()
        assert cal.event is None

    async def test_event_updates_on_successive_calls(self) -> None:
        """Verify event reflects latest coordinator state after each update."""
        event_1 = CalendarEvent(
            summary="First Guest",
            start=datetime(2025, 7, 1, 16, 0, tzinfo=timezone.utc),
            end=datetime(2025, 7, 3, 11, 0, tzinfo=timezone.utc),
        )
        event_2 = CalendarEvent(
            summary="Second Guest",
            start=datetime(2025, 7, 5, 16, 0, tzinfo=timezone.utc),
            end=datetime(2025, 7, 8, 11, 0, tzinfo=timezone.utc),
        )
        coordinator = _mock_coordinator(calendar_ready=True, event=event_1)
        cal = RentalControlCalendar(coordinator)

        await cal.async_update()
        assert cal.event is event_1

        coordinator.event = event_2
        await cal.async_update()
        assert cal.event is event_2

    async def test_available_not_reset_when_calendar_becomes_unready(self) -> None:
        """Verify available stays True once set even if calendar_ready flips to False.

        The implementation only sets _available = True; it never resets to False.
        """
        coordinator = _mock_coordinator(calendar_ready=True)
        cal = RentalControlCalendar(coordinator)

        await cal.async_update()
        assert cal.available is True

        # Calendar becomes unready — but implementation doesn't reset available
        coordinator.calendar_ready = False
        await cal.async_update()
        assert cal.available is True


# ---------------------------------------------------------------------------
# async_get_events tests (T055)
# ---------------------------------------------------------------------------


class TestAsyncGetEvents:
    """Tests for RentalControlCalendar.async_get_events."""

    async def test_delegates_to_coordinator(self) -> None:
        """Verify async_get_events calls coordinator.async_get_events."""
        coordinator = _mock_coordinator()
        cal = RentalControlCalendar(coordinator)

        hass = MagicMock()
        start = datetime(2025, 7, 1)
        end = datetime(2025, 7, 31)

        await cal.async_get_events(hass, start, end)

        coordinator.async_get_events.assert_awaited_once_with(hass, start, end)

    async def test_returns_events_from_coordinator(self) -> None:
        """Verify async_get_events returns events provided by the coordinator."""
        events = [
            CalendarEvent(
                summary="Booking A",
                start=datetime(2025, 7, 5, 16, 0, tzinfo=timezone.utc),
                end=datetime(2025, 7, 8, 11, 0, tzinfo=timezone.utc),
            ),
            CalendarEvent(
                summary="Booking B",
                start=datetime(2025, 7, 15, 16, 0, tzinfo=timezone.utc),
                end=datetime(2025, 7, 20, 11, 0, tzinfo=timezone.utc),
            ),
        ]
        coordinator = _mock_coordinator()
        coordinator.async_get_events = AsyncMock(return_value=events)
        cal = RentalControlCalendar(coordinator)

        hass = MagicMock()
        result = await cal.async_get_events(
            hass, datetime(2025, 7, 1), datetime(2025, 7, 31)
        )

        assert result is events
        assert len(result) == 2

    async def test_returns_empty_list_for_no_matching_events(self) -> None:
        """Verify async_get_events returns an empty list when no events match."""
        coordinator = _mock_coordinator()
        coordinator.async_get_events = AsyncMock(return_value=[])
        cal = RentalControlCalendar(coordinator)

        hass = MagicMock()
        result = await cal.async_get_events(
            hass, datetime(2025, 1, 1), datetime(2025, 1, 31)
        )

        assert result == []

    async def test_passes_exact_date_range(self) -> None:
        """Verify the start and end dates are passed through unchanged."""
        coordinator = _mock_coordinator()
        cal = RentalControlCalendar(coordinator)

        hass = MagicMock()
        start = datetime(2025, 12, 25, 0, 0, 0)
        end = datetime(2025, 12, 31, 23, 59, 59)

        await cal.async_get_events(hass, start, end)

        call_args = coordinator.async_get_events.call_args
        assert call_args[0][1] is start
        assert call_args[0][2] is end
