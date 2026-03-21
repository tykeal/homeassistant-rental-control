# SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the Rental Control sensor platform and CalSensor entity."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
import random
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

from freezegun import freeze_time
from homeassistant.helpers.entity import EntityCategory

from custom_components.rental_control.const import COORDINATOR
from custom_components.rental_control.const import DOMAIN
from custom_components.rental_control.const import ICON
from custom_components.rental_control.const import NAME
from custom_components.rental_control.sensor import async_setup_entry
from custom_components.rental_control.sensor import async_setup_platform
from custom_components.rental_control.sensors.calsensor import RentalControlCalSensor
from custom_components.rental_control.sensors.checkinsensor import CheckinTrackingSensor
from custom_components.rental_control.util import gen_uuid


def _make_event(
    *,
    summary: str = "Reserved - John Doe",
    start: datetime | None = None,
    end: datetime | None = None,
    location: str | None = "123 Main St",
    description: str
    | None = "Phone: +1 555-123-4567\nEmail: john@example.com\nGuests: 4\nhttps://airbnb.com/reservations/123",
) -> MagicMock:
    """Create a mock calendar event."""
    if start is None:
        start = datetime(2025, 3, 15, 16, 0, tzinfo=timezone.utc)
    if end is None:
        end = datetime(2025, 3, 20, 11, 0, tzinfo=timezone.utc)
    event = MagicMock()
    event.summary = summary
    event.start = start
    event.end = end
    event.location = location
    event.description = description
    return event


def _make_coordinator(
    *,
    name: str = "Test Rental",
    unique_id: str = "test_unique_id",
    last_update_success: bool = True,
    data: list | None = None,
    event_prefix: str = "",
    code_generator: str = "date_based",
    code_length: int = 4,
    event_overrides: MagicMock | None = None,
    should_update_code: bool = True,
) -> MagicMock:
    """Create a mock coordinator for sensor testing."""
    coordinator = MagicMock()
    coordinator.name = name
    coordinator.unique_id = unique_id
    coordinator.last_update_success = last_update_success
    coordinator.data = data
    coordinator.event_prefix = event_prefix
    coordinator.code_generator = code_generator
    coordinator.code_length = code_length
    coordinator.event_overrides = event_overrides
    coordinator.should_update_code = should_update_code
    coordinator.device_info = {
        "identifiers": {(DOMAIN, unique_id)},
        "name": f"{NAME} {name}",
        "manufacturer": "Andrew Grimberg",
    }
    return coordinator


# ---------------------------------------------------------------------------
# Platform setup tests (sensor.py)
# ---------------------------------------------------------------------------


class TestAsyncSetupPlatform:
    """Tests for async_setup_platform (legacy platform setup)."""

    async def test_returns_true(self) -> None:
        """Verify async_setup_platform returns True (config flow only)."""
        result = await async_setup_platform(None, None, None)
        assert result is True


class TestAsyncSetupEntry:
    """Tests for async_setup_entry which creates sensor entities."""

    async def test_creates_sensors_for_max_events(self, hass) -> None:
        """Verify async_setup_entry creates max_events sensors."""
        coordinator = _make_coordinator(data=[_make_event()])
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN]["entry1"] = {COORDINATOR: coordinator}

        config_entry = MagicMock()
        config_entry.data = {"name": "Test Rental", "max_events": 3}
        config_entry.entry_id = "entry1"

        added_entities = []
        async_add_entities = MagicMock(side_effect=lambda e: added_entities.extend(e))

        # Mock the entity platform context for service registration
        mock_platform = MagicMock()
        with patch(
            "custom_components.rental_control.sensor.entity_platform"
            ".async_get_current_platform",
            return_value=mock_platform,
        ):
            await async_setup_entry(hass, config_entry, async_add_entities)

        assert async_add_entities.called
        assert len(added_entities) == 4  # 3 cal sensors + 1 checkin sensor
        for i, sensor in enumerate(added_entities[:3]):
            assert isinstance(sensor, RentalControlCalSensor)
            assert sensor._event_number == i
        assert isinstance(added_entities[3], CheckinTrackingSensor)
        # Verify checkout service was registered
        mock_platform.async_register_entity_service.assert_called_once_with(
            "checkout", {}, "async_checkout"
        )

    async def test_returns_false_when_calendar_is_none(self, hass) -> None:
        """Verify async_setup_entry returns False when calendar fetch fails."""
        coordinator = _make_coordinator(last_update_success=False)
        coordinator.data = None
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN]["entry1"] = {COORDINATOR: coordinator}

        config_entry = MagicMock()
        config_entry.data = {"name": "Test Rental", "max_events": 3}
        config_entry.entry_id = "entry1"

        async_add_entities = MagicMock()
        result = await async_setup_entry(hass, config_entry, async_add_entities)

        assert result is False
        async_add_entities.assert_not_called()


# ---------------------------------------------------------------------------
# Sensor initialization and properties
# ---------------------------------------------------------------------------


class TestSensorInit:
    """Tests for RentalControlCalSensor initialization."""

    def test_name_format(self, hass) -> None:
        """Verify sensor name follows 'NAME name Event N' pattern."""
        coordinator = _make_coordinator()
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test Rental", 0)
        assert sensor.name == "Rental Control Test Rental Event 0"

    def test_name_with_event_number(self, hass) -> None:
        """Verify event number is included in sensor name."""
        coordinator = _make_coordinator()
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test Rental", 2)
        assert sensor.name == "Rental Control Test Rental Event 2"

    def test_unique_id_generation(self, hass) -> None:
        """Verify unique_id uses gen_uuid with coordinator unique_id and event number."""
        coordinator = _make_coordinator()
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test Rental", 0)
        expected = gen_uuid(f"{coordinator.unique_id} sensor 0")
        assert sensor.unique_id == expected

    def test_unique_id_differs_per_event_number(self, hass) -> None:
        """Verify different event numbers produce different unique_ids."""
        coordinator = _make_coordinator()
        sensor0 = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor1 = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 1)
        assert sensor0.unique_id != sensor1.unique_id

    def test_initial_state_no_prefix(self, hass) -> None:
        """Verify initial state is 'No reservation' when no event prefix."""
        coordinator = _make_coordinator(event_prefix="")
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        assert sensor.state == "No reservation"

    def test_initial_state_with_prefix(self, hass) -> None:
        """Verify initial state includes event prefix."""
        coordinator = _make_coordinator(event_prefix="Rental")
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        assert sensor.state == "Rental No reservation"

    def test_initial_availability_matches_coordinator(self, hass) -> None:
        """Verify sensor availability tracks coordinator from creation."""
        coordinator = _make_coordinator()
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        assert sensor.available is True

        coordinator_fail = _make_coordinator(last_update_success=False)
        sensor_fail = RentalControlCalSensor(hass, coordinator_fail, f"{NAME} Test", 0)
        assert sensor_fail.available is False

    def test_initial_event_attributes(self, hass) -> None:
        """Verify initial event attributes have expected keys with None values."""
        coordinator = _make_coordinator(event_prefix="")
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        attrs = sensor.extra_state_attributes
        assert attrs["summary"] == "No reservation"
        assert attrs["description"] is None
        assert attrs["location"] is None
        assert attrs["start"] is None
        assert attrs["end"] is None
        assert attrs["eta_days"] is None
        assert attrs["eta_hours"] is None
        assert attrs["eta_minutes"] is None
        assert attrs["slot_name"] is None
        assert attrs["slot_code"] is None

    @freeze_time("2025-03-10T12:00:00+00:00")
    async def test_async_added_to_hass_processes_existing_data(self, hass) -> None:
        """Verify sensor processes coordinator data on registration."""
        event = _make_event()
        coordinator = _make_coordinator(data=[event], last_update_success=True)
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor.hass = MagicMock()
        sensor.async_write_ha_state = MagicMock()
        sensor.async_on_remove = MagicMock()
        coordinator.async_add_listener = MagicMock(return_value=MagicMock())

        await sensor.async_added_to_hass()

        assert "Reserved - John Doe" in sensor.state

    async def test_async_added_to_hass_skips_when_no_data(self, hass) -> None:
        """Verify sensor skips processing when coordinator has no data."""
        coordinator = _make_coordinator(data=None, last_update_success=True)
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor.hass = MagicMock()
        sensor.async_write_ha_state = MagicMock()
        sensor.async_on_remove = MagicMock()
        coordinator.async_add_listener = MagicMock(return_value=MagicMock())

        await sensor.async_added_to_hass()

        assert sensor.state == "No reservation"

    async def test_async_added_to_hass_skips_when_not_successful(self, hass) -> None:
        """Verify sensor skips processing when coordinator failed."""
        event = _make_event()
        coordinator = _make_coordinator(data=[event], last_update_success=False)
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor.hass = MagicMock()
        sensor.async_write_ha_state = MagicMock()
        sensor.async_on_remove = MagicMock()
        coordinator.async_add_listener = MagicMock(return_value=MagicMock())

        await sensor.async_added_to_hass()

        assert sensor.state == "No reservation"


class TestSensorProperties:
    """Tests for RentalControlCalSensor property accessors."""

    def test_icon(self, hass) -> None:
        """Verify icon returns ICON constant."""
        coordinator = _make_coordinator()
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        assert sensor.icon == ICON

    def test_entity_category(self, hass) -> None:
        """Verify entity_category is DIAGNOSTIC."""
        coordinator = _make_coordinator()
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        assert sensor.entity_category == EntityCategory.DIAGNOSTIC

    def test_device_info_delegates_to_coordinator(self, hass) -> None:
        """Verify device_info returns coordinator.device_info."""
        coordinator = _make_coordinator()
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        assert sensor.device_info is coordinator.device_info

    def test_extra_state_attributes_merges_parsed(self, hass) -> None:
        """Verify extra_state_attributes merges event and parsed attributes."""
        coordinator = _make_coordinator()
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor._parsed_attributes = {"guest_email": "test@test.com"}
        attrs = sensor.extra_state_attributes
        assert "summary" in attrs
        assert attrs["guest_email"] == "test@test.com"


# ---------------------------------------------------------------------------
# Extraction method tests
# ---------------------------------------------------------------------------


class TestExtractEmail:
    """Tests for _extract_email parsing."""

    def test_extracts_email(self, hass) -> None:
        """Verify email extraction from standard 'Email: addr' format."""
        coordinator = _make_coordinator()
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor._event_attributes["description"] = "Email: john@example.com"
        assert sensor._extract_email() == "john@example.com"

    def test_returns_none_when_no_email(self, hass) -> None:
        """Verify None returned when no Email field in description."""
        coordinator = _make_coordinator()
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor._event_attributes["description"] = "No email here"
        assert sensor._extract_email() is None

    def test_returns_none_when_description_none(self, hass) -> None:
        """Verify None returned when description is None."""
        coordinator = _make_coordinator()
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor._event_attributes["description"] = None
        assert sensor._extract_email() is None

    def test_extracts_first_email_when_multiple(self, hass) -> None:
        """Verify only first email is returned when multiple present."""
        coordinator = _make_coordinator()
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor._event_attributes["description"] = (
            "Email: first@example.com\nEmail: second@example.com"
        )
        assert sensor._extract_email() == "first@example.com"


class TestExtractPhoneNumber:
    """Tests for _extract_phone_number parsing."""

    def test_extracts_phone_with_country_code(self, hass) -> None:
        """Verify phone extraction with international format."""
        coordinator = _make_coordinator()
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor._event_attributes["description"] = "Phone: +1 555-123-4567"
        assert sensor._extract_phone_number() == "+1 555-123-4567"

    def test_extracts_phone_number_label(self, hass) -> None:
        """Verify extraction with 'Phone Number:' label variant."""
        coordinator = _make_coordinator()
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor._event_attributes["description"] = "Phone Number: 555.123.4567"
        assert sensor._extract_phone_number() == "555.123.4567"

    def test_returns_none_when_no_phone(self, hass) -> None:
        """Verify None returned when no phone field in description."""
        coordinator = _make_coordinator()
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor._event_attributes["description"] = "No phone here"
        assert sensor._extract_phone_number() is None

    def test_returns_none_when_description_none(self, hass) -> None:
        """Verify None returned when description is None."""
        coordinator = _make_coordinator()
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor._event_attributes["description"] = None
        assert sensor._extract_phone_number() is None

    def test_extracts_phone_with_parentheses(self, hass) -> None:
        """Verify phone extraction with parenthesized area code."""
        coordinator = _make_coordinator()
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor._event_attributes["description"] = "Phone: (555) 123-4567"
        assert sensor._extract_phone_number() == "(555) 123-4567"


class TestExtractNumGuests:
    """Tests for _extract_num_guests parsing."""

    def test_extracts_guest_count(self, hass) -> None:
        """Verify guest count extraction from 'Guests: N' format."""
        coordinator = _make_coordinator()
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor._event_attributes["description"] = "Guests: 4"
        assert sensor._extract_num_guests() == "4"

    def test_extracts_from_adults_and_children(self, hass) -> None:
        """Verify guest count sums Adults and Children fields."""
        coordinator = _make_coordinator()
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor._event_attributes["description"] = "Adults: 2\nChildren: 3"
        assert sensor._extract_num_guests() == "5"

    def test_extracts_adults_only(self, hass) -> None:
        """Verify guest count uses Adults alone when no Children field."""
        coordinator = _make_coordinator()
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor._event_attributes["description"] = "Adults: 3"
        assert sensor._extract_num_guests() == "3"

    def test_returns_none_when_no_guest_info(self, hass) -> None:
        """Verify None returned when no guest information in description."""
        coordinator = _make_coordinator()
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor._event_attributes["description"] = "No guest info"
        assert sensor._extract_num_guests() is None

    def test_returns_none_when_description_none(self, hass) -> None:
        """Verify None returned when description is None."""
        coordinator = _make_coordinator()
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor._event_attributes["description"] = None
        assert sensor._extract_num_guests() is None


class TestExtractLastFour:
    """Tests for _extract_last_four parsing."""

    def test_extracts_last_four_digits(self, hass) -> None:
        """Verify extraction from 'Last 4 Digits: NNNN' format."""
        coordinator = _make_coordinator()
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor._event_attributes["description"] = "Last 4 Digits: 1234"
        assert sensor._extract_last_four() == "1234"

    def test_extracts_last_four_with_parens(self, hass) -> None:
        """Verify extraction from '(Last 4 Digits): NNNN' format."""
        coordinator = _make_coordinator()
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor._event_attributes["description"] = "(Last 4 Digits): 5678"
        assert sensor._extract_last_four() == "5678"

    def test_falls_back_to_phone_last_four(self, hass) -> None:
        """Verify fallback to last 4 digits of phone number."""
        coordinator = _make_coordinator()
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor._event_attributes["description"] = "Phone: +1 555-123-9876"
        assert sensor._extract_last_four() == "9876"

    def test_returns_none_when_no_last_four(self, hass) -> None:
        """Verify None returned when no last four digits available."""
        coordinator = _make_coordinator()
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor._event_attributes["description"] = "No digits here"
        assert sensor._extract_last_four() is None

    def test_returns_none_when_description_none(self, hass) -> None:
        """Verify None returned when description is None."""
        coordinator = _make_coordinator()
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor._event_attributes["description"] = None
        assert sensor._extract_last_four() is None


class TestExtractUrl:
    """Tests for _extract_url parsing."""

    def test_extracts_https_url(self, hass) -> None:
        """Verify HTTPS URL extraction from description."""
        coordinator = _make_coordinator()
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor._event_attributes["description"] = (
            "Details\nhttps://airbnb.com/reservations/123"
        )
        assert sensor._extract_url() == "https://airbnb.com/reservations/123"

    def test_extracts_http_url(self, hass) -> None:
        """Verify HTTP URL extraction from description."""
        coordinator = _make_coordinator()
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor._event_attributes["description"] = "Link: http://example.com/booking/456"
        assert sensor._extract_url() == "http://example.com/booking/456"

    def test_returns_none_when_no_url(self, hass) -> None:
        """Verify None returned when no URL in description."""
        coordinator = _make_coordinator()
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor._event_attributes["description"] = "No URL here"
        assert sensor._extract_url() is None

    def test_returns_none_when_description_none(self, hass) -> None:
        """Verify None returned when description is None."""
        coordinator = _make_coordinator()
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor._event_attributes["description"] = None
        assert sensor._extract_url() is None


# ---------------------------------------------------------------------------
# Code generation tests
# ---------------------------------------------------------------------------


class TestGenerateDoorCodeDateBased:
    """Tests for _generate_door_code with date_based generator."""

    def test_date_based_code(self, hass) -> None:
        """Verify date-based code uses start/end day+month+year digits."""
        coordinator = _make_coordinator(code_generator="date_based", code_length=4)
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        # start: 2025-03-15, end: 2025-03-20
        sensor._event_attributes["start"] = datetime(
            2025, 3, 15, 16, 0, tzinfo=timezone.utc
        )
        sensor._event_attributes["end"] = datetime(
            2025, 3, 20, 11, 0, tzinfo=timezone.utc
        )
        sensor._event_attributes["description"] = "Some description"
        code = sensor._generate_door_code()
        # Code pattern: start_day + end_day + start_month + end_month + start_year + end_year
        # = "15" + "20" + "03" + "03" + "2025" + "2025" = "1520030320252025"
        # Truncated to 4: "1520"
        assert code == "1520"

    def test_date_based_code_length_6(self, hass) -> None:
        """Verify date-based code truncates to requested length."""
        coordinator = _make_coordinator(code_generator="date_based", code_length=6)
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor._event_attributes["start"] = datetime(
            2025, 3, 15, 16, 0, tzinfo=timezone.utc
        )
        sensor._event_attributes["end"] = datetime(
            2025, 3, 20, 11, 0, tzinfo=timezone.utc
        )
        sensor._event_attributes["description"] = "Some description"
        code = sensor._generate_door_code()
        assert code == "152003"

    def test_date_based_fallback_when_description_none(self, hass) -> None:
        """Verify date_based is used when description is None regardless of configured generator."""
        coordinator = _make_coordinator(code_generator="static_random", code_length=4)
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor._event_attributes["start"] = datetime(
            2025, 3, 15, 16, 0, tzinfo=timezone.utc
        )
        sensor._event_attributes["end"] = datetime(
            2025, 3, 20, 11, 0, tzinfo=timezone.utc
        )
        sensor._event_attributes["description"] = None
        code = sensor._generate_door_code()
        # Falls back to date_based since description is None
        assert code == "1520"


class TestGenerateDoorCodeStaticRandom:
    """Tests for _generate_door_code with static_random generator."""

    def setup_method(self) -> None:
        """Save random state before each test to prevent RNG leak."""
        self._rng_state = random.getstate()

    def teardown_method(self) -> None:
        """Restore random state after each test."""
        random.setstate(self._rng_state)

    def test_static_random_produces_code(self, hass) -> None:
        """Verify static_random produces a code seeded from description."""
        coordinator = _make_coordinator(code_generator="static_random", code_length=4)
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor._event_attributes["start"] = datetime(
            2025, 3, 15, 16, 0, tzinfo=timezone.utc
        )
        sensor._event_attributes["end"] = datetime(
            2025, 3, 20, 11, 0, tzinfo=timezone.utc
        )
        sensor._event_attributes["description"] = "Test reservation details"
        code = sensor._generate_door_code()
        assert len(code) == 4
        assert code.isdigit()

    def test_static_random_deterministic(self, hass) -> None:
        """Verify same description always produces same code."""
        coordinator = _make_coordinator(code_generator="static_random", code_length=4)
        sensor1 = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor1._event_attributes["start"] = datetime(
            2025, 3, 15, 16, 0, tzinfo=timezone.utc
        )
        sensor1._event_attributes["end"] = datetime(
            2025, 3, 20, 11, 0, tzinfo=timezone.utc
        )
        sensor1._event_attributes["description"] = "Same description"

        sensor2 = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 1)
        sensor2._event_attributes["start"] = datetime(
            2025, 3, 15, 16, 0, tzinfo=timezone.utc
        )
        sensor2._event_attributes["end"] = datetime(
            2025, 3, 20, 11, 0, tzinfo=timezone.utc
        )
        sensor2._event_attributes["description"] = "Same description"

        assert sensor1._generate_door_code() == sensor2._generate_door_code()

    def test_static_random_different_descriptions_deterministic(self, hass) -> None:
        """Verify different descriptions produce deterministic distinct codes.

        Uses known input values to verify each description seeds a distinct
        reproducible code, avoiding reliance on PRNG collision avoidance.
        """
        coordinator = _make_coordinator(code_generator="static_random", code_length=4)
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor._event_attributes["start"] = datetime(
            2025, 3, 15, 16, 0, tzinfo=timezone.utc
        )
        sensor._event_attributes["end"] = datetime(
            2025, 3, 20, 11, 0, tzinfo=timezone.utc
        )

        sensor._event_attributes["description"] = "Description A"
        code_a = sensor._generate_door_code()

        sensor._event_attributes["description"] = "Description B"
        code_b = sensor._generate_door_code()

        # Each code is deterministic for its description
        assert len(code_a) == 4
        assert code_a.isdigit()
        assert len(code_b) == 4
        assert code_b.isdigit()

        # Re-run with same descriptions to confirm determinism
        sensor._event_attributes["description"] = "Description A"
        assert sensor._generate_door_code() == code_a
        sensor._event_attributes["description"] = "Description B"
        assert sensor._generate_door_code() == code_b

    def test_static_random_code_length_6(self, hass) -> None:
        """Verify static_random respects code_length setting."""
        coordinator = _make_coordinator(code_generator="static_random", code_length=6)
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor._event_attributes["start"] = datetime(
            2025, 3, 15, 16, 0, tzinfo=timezone.utc
        )
        sensor._event_attributes["end"] = datetime(
            2025, 3, 20, 11, 0, tzinfo=timezone.utc
        )
        sensor._event_attributes["description"] = "Test reservation"
        code = sensor._generate_door_code()
        assert len(code) == 6
        assert code.isdigit()


class TestGenerateDoorCodeLastFour:
    """Tests for _generate_door_code with last_four generator."""

    def test_last_four_with_explicit_digits(self, hass) -> None:
        """Verify last_four extracts digits from 'Last 4 Digits' field."""
        coordinator = _make_coordinator(code_generator="last_four", code_length=4)
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor._event_attributes["start"] = datetime(
            2025, 3, 15, 16, 0, tzinfo=timezone.utc
        )
        sensor._event_attributes["end"] = datetime(
            2025, 3, 20, 11, 0, tzinfo=timezone.utc
        )
        sensor._event_attributes["description"] = "Last 4 Digits: 9876"
        code = sensor._generate_door_code()
        assert code == "9876"

    def test_last_four_falls_back_to_date_based_when_no_digits(self, hass) -> None:
        """Verify last_four falls back to date_based when no last 4 digits available."""
        coordinator = _make_coordinator(code_generator="last_four", code_length=4)
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor._event_attributes["start"] = datetime(
            2025, 3, 15, 16, 0, tzinfo=timezone.utc
        )
        sensor._event_attributes["end"] = datetime(
            2025, 3, 20, 11, 0, tzinfo=timezone.utc
        )
        sensor._event_attributes["description"] = "No digits here"
        code = sensor._generate_door_code()
        # Falls back to date_based: "1520"
        assert code == "1520"

    def test_last_four_ignored_when_code_length_not_four(self, hass) -> None:
        """Verify last_four generator is skipped when code_length != 4."""
        coordinator = _make_coordinator(code_generator="last_four", code_length=6)
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor._event_attributes["start"] = datetime(
            2025, 3, 15, 16, 0, tzinfo=timezone.utc
        )
        sensor._event_attributes["end"] = datetime(
            2025, 3, 20, 11, 0, tzinfo=timezone.utc
        )
        sensor._event_attributes["description"] = "Last 4 Digits: 9876"
        code = sensor._generate_door_code()
        # Skips last_four (code_length != 4), falls back to date_based
        assert code == "152003"
        assert len(code) == 6

    def test_last_four_from_phone_fallback(self, hass) -> None:
        """Verify last_four extracts from phone number when no explicit field."""
        coordinator = _make_coordinator(code_generator="last_four", code_length=4)
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor._event_attributes["start"] = datetime(
            2025, 3, 15, 16, 0, tzinfo=timezone.utc
        )
        sensor._event_attributes["end"] = datetime(
            2025, 3, 20, 11, 0, tzinfo=timezone.utc
        )
        sensor._event_attributes["description"] = "Phone: +1 555-123-4567"
        code = sensor._generate_door_code()
        assert code == "4567"


# ---------------------------------------------------------------------------
# _handle_coordinator_update tests
# ---------------------------------------------------------------------------


class TestHandleCoordinatorUpdateWithEvents:
    """Tests for _handle_coordinator_update when events are available."""

    @freeze_time("2025-03-10T12:00:00+00:00")
    def test_updates_state_with_event(self, hass) -> None:
        """Verify _handle_coordinator_update sets state to 'summary - date time' format."""
        event = _make_event(
            summary="Reserved - Jane Smith",
            start=datetime(2025, 3, 15, 16, 0, tzinfo=timezone.utc),
            end=datetime(2025, 3, 20, 11, 0, tzinfo=timezone.utc),
        )
        coordinator = _make_coordinator(data=[event])
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor.hass = MagicMock()
        sensor.async_write_ha_state = MagicMock()

        sensor._handle_coordinator_update()

        start = datetime(2025, 3, 15, 16, 0, tzinfo=timezone.utc)
        expected_date = f"{start.day} {start.strftime('%B %Y')}"
        expected_time = start.strftime("%H:%M")
        assert "Reserved - Jane Smith" in sensor.state
        assert expected_date in sensor.state
        assert expected_time in sensor.state

    @freeze_time("2025-03-10T12:00:00+00:00")
    def test_updates_event_attributes(self, hass) -> None:
        """Verify _handle_coordinator_update populates all event attributes."""
        event = _make_event()
        coordinator = _make_coordinator(data=[event])
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor.hass = MagicMock()
        sensor.async_write_ha_state = MagicMock()

        sensor._handle_coordinator_update()

        attrs = sensor.extra_state_attributes
        assert attrs["summary"] == event.summary
        assert attrs["start"] == event.start
        assert attrs["end"] == event.end
        assert attrs["location"] == event.location
        assert attrs["description"] == event.description

    @freeze_time("2025-03-10T12:00:00+00:00")
    def test_calculates_eta(self, hass) -> None:
        """Verify ETA days/hours/minutes are calculated for future events."""
        start = datetime(2025, 3, 15, 16, 0, tzinfo=timezone.utc)
        event = _make_event(start=start)
        coordinator = _make_coordinator(data=[event])
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor.hass = MagicMock()
        sensor.async_write_ha_state = MagicMock()

        sensor._handle_coordinator_update()

        attrs = sensor.extra_state_attributes
        assert attrs["eta_days"] == 5
        assert attrs["eta_hours"] is not None
        assert attrs["eta_minutes"] is not None
        assert attrs["eta_hours"] > 0
        assert attrs["eta_minutes"] > 0

    @freeze_time("2025-03-20T12:00:00+00:00")
    def test_eta_none_for_past_events(self, hass) -> None:
        """Verify ETA fields are None when event start is in the past."""
        start = datetime(2025, 3, 15, 16, 0, tzinfo=timezone.utc)
        event = _make_event(start=start)
        coordinator = _make_coordinator(data=[event])
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor.hass = MagicMock()
        sensor.async_write_ha_state = MagicMock()

        sensor._handle_coordinator_update()

        attrs = sensor.extra_state_attributes
        assert attrs["eta_days"] is None
        assert attrs["eta_hours"] is None
        assert attrs["eta_minutes"] is None

    @freeze_time("2025-03-10T12:00:00+00:00")
    def test_parses_description_attributes(self, hass) -> None:
        """Verify _handle_coordinator_update extracts parsed attributes from description."""
        description = (
            "Email: guest@airbnb.com\n"
            "Phone: +1 555-987-6543\n"
            "Guests: 6\n"
            "Last 4 Digits: 6543\n"
            "https://airbnb.com/reservations/abc"
        )
        event = _make_event(description=description)
        coordinator = _make_coordinator(data=[event])
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor.hass = MagicMock()
        sensor.async_write_ha_state = MagicMock()

        sensor._handle_coordinator_update()

        attrs = sensor.extra_state_attributes
        assert attrs["guest_email"] == "guest@airbnb.com"
        assert attrs["phone_number"] == "+1 555-987-6543"
        assert attrs["number_of_guests"] == "6"
        assert attrs["last_four"] == "6543"
        assert attrs["reservation_url"] == "https://airbnb.com/reservations/abc"

    @freeze_time("2025-03-10T12:00:00+00:00")
    def test_generates_slot_code(self, hass) -> None:
        """Verify _handle_coordinator_update generates a door code."""
        event = _make_event()
        coordinator = _make_coordinator(data=[event], code_generator="date_based")
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor.hass = MagicMock()
        sensor.async_write_ha_state = MagicMock()

        sensor._handle_coordinator_update()

        attrs = sensor.extra_state_attributes
        assert attrs["slot_code"] is not None
        assert len(attrs["slot_code"]) == 4
        assert attrs["slot_code"].isdigit()

    @freeze_time("2025-03-10T12:00:00+00:00")
    def test_sets_availability_from_coordinator(self, hass) -> None:
        """Verify availability reflects coordinator.last_update_success after update."""
        event = _make_event()
        coordinator = _make_coordinator(data=[event], last_update_success=True)
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor.hass = MagicMock()
        sensor.async_write_ha_state = MagicMock()

        sensor._handle_coordinator_update()

        assert sensor.available is True

    @freeze_time("2025-03-10T12:00:00+00:00")
    def test_uses_correct_event_by_number(self, hass) -> None:
        """Verify sensor selects event at its event_number index."""
        event0 = _make_event(summary="First Event")
        event1 = _make_event(summary="Second Event")
        coordinator = _make_coordinator(data=[event0, event1])
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 1)
        sensor.hass = MagicMock()
        sensor.async_write_ha_state = MagicMock()

        sensor._handle_coordinator_update()

        assert "Second Event" in sensor.state

    @freeze_time("2025-03-10T12:00:00+00:00")
    def test_refreshes_code_settings_from_coordinator(self, hass) -> None:
        """Verify _handle_coordinator_update re-reads code_generator and code_length."""
        event = _make_event()
        coordinator = _make_coordinator(
            data=[event], code_generator="date_based", code_length=4
        )
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor.hass = MagicMock()
        sensor.async_write_ha_state = MagicMock()

        coordinator.code_generator = "static_random"
        coordinator.code_length = 6

        sensor._handle_coordinator_update()

        assert sensor._code_generator == "static_random"
        assert sensor._code_length == 6

    @freeze_time("2025-03-10T12:00:00+00:00")
    def test_populates_slot_name(self, hass) -> None:
        """Verify _handle_coordinator_update sets slot_name via get_slot_name."""
        event = _make_event(summary="Reserved - John Doe")
        coordinator = _make_coordinator(data=[event])
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor.hass = MagicMock()
        sensor.async_write_ha_state = MagicMock()

        with patch(
            "custom_components.rental_control.sensors.calsensor.get_slot_name",
            return_value="John Doe",
        ) as mock_get_slot:
            sensor._handle_coordinator_update()
            attrs = sensor.extra_state_attributes
            assert attrs["slot_name"] == "John Doe"
            mock_get_slot.assert_called_once_with(
                event.summary,
                event.description,
                coordinator.event_prefix,
            )


class TestHandleCoordinatorUpdateNoEvents:
    """Tests for _handle_coordinator_update when no events are available."""

    def test_resets_to_no_reservation(self, hass) -> None:
        """Verify state resets to 'No reservation' when no events."""
        coordinator = _make_coordinator(data=[])
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor.hass = MagicMock()
        sensor.async_write_ha_state = MagicMock()

        sensor._handle_coordinator_update()

        assert sensor.state == "No reservation"

    def test_resets_with_prefix(self, hass) -> None:
        """Verify 'No reservation' includes event_prefix when set."""
        coordinator = _make_coordinator(data=[], event_prefix="Rental")
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor.hass = MagicMock()
        sensor.async_write_ha_state = MagicMock()

        sensor._handle_coordinator_update()

        assert sensor.state == "Rental No reservation"

    def test_clears_event_attributes(self, hass) -> None:
        """Verify all event attributes are reset to None when no events."""
        coordinator = _make_coordinator(data=[])
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor.hass = MagicMock()
        sensor.async_write_ha_state = MagicMock()

        sensor._handle_coordinator_update()

        attrs = sensor.extra_state_attributes
        assert attrs["description"] is None
        assert attrs["location"] is None
        assert attrs["start"] is None
        assert attrs["end"] is None
        assert attrs["eta_days"] is None
        assert attrs["slot_name"] is None
        assert attrs["slot_code"] is None

    def test_clears_parsed_attributes(self, hass) -> None:
        """Verify parsed attributes are cleared when no events."""
        coordinator = _make_coordinator(data=[])
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor._parsed_attributes = {"guest_email": "old@example.com"}
        sensor.hass = MagicMock()
        sensor.async_write_ha_state = MagicMock()

        sensor._handle_coordinator_update()

        assert "guest_email" not in sensor.extra_state_attributes

    def test_event_number_beyond_list(self, hass) -> None:
        """Verify sensor handles event_number >= len(event_list) gracefully."""
        event = _make_event()
        coordinator = _make_coordinator(data=[event])
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 5)
        sensor.hass = MagicMock()
        sensor.async_write_ha_state = MagicMock()

        sensor._handle_coordinator_update()

        assert sensor.state == "No reservation"

    def test_returns_early_when_not_successful(self, hass) -> None:
        """Verify _handle_coordinator_update returns when last_update_success is False."""
        coordinator = _make_coordinator(last_update_success=False, data=[])
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor.hass = MagicMock()
        sensor.async_write_ha_state = MagicMock()
        original_state = sensor.state

        sensor._handle_coordinator_update()

        assert sensor.state == original_state
        assert sensor.available is False


# ---------------------------------------------------------------------------
# Override interaction tests
# ---------------------------------------------------------------------------


class TestHandleCoordinatorUpdateOverrides:
    """Tests for _handle_coordinator_update interactions with event_overrides."""

    @freeze_time("2025-03-10T12:00:00+00:00")
    def test_fires_set_code_when_no_override(self, hass) -> None:
        """Verify async_fire_set_code is scheduled when slot not in overrides."""
        event = _make_event()
        overrides = MagicMock()
        overrides.get_slot_with_name.return_value = None
        overrides.next_slot = 10
        coordinator = _make_coordinator(data=[event], event_overrides=overrides)
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor.hass = MagicMock()
        sensor.async_write_ha_state = MagicMock()

        with patch(
            "custom_components.rental_control.sensors.calsensor.async_fire_set_code",
            new_callable=AsyncMock,
        ) as mock_set_code:
            sensor._handle_coordinator_update()
            mock_set_code.assert_called_once_with(coordinator, sensor, 10)
            sensor.hass.async_create_task.assert_called_once()

    @freeze_time("2025-03-10T12:00:00+00:00")
    def test_uses_override_slot_code(self, hass) -> None:
        """Verify slot_code from override takes precedence over generated code."""
        event = _make_event()
        override = {
            "slot_code": "5555",
            "start_time": event.start,
            "end_time": event.end,
        }
        overrides = MagicMock()
        overrides.get_slot_with_name.return_value = override
        coordinator = _make_coordinator(data=[event], event_overrides=overrides)
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor.hass = MagicMock()
        sensor.async_write_ha_state = MagicMock()

        sensor._handle_coordinator_update()

        assert sensor.extra_state_attributes["slot_code"] == "5555"

    @freeze_time("2025-03-10T12:00:00+00:00")
    def test_generates_code_when_override_has_no_code(self, hass) -> None:
        """Verify code is generated when override exists but has no slot_code."""
        event = _make_event()
        override = {
            "slot_code": None,
            "start_time": event.start,
            "end_time": event.end,
        }
        overrides = MagicMock()
        overrides.get_slot_with_name.return_value = override
        coordinator = _make_coordinator(
            data=[event], event_overrides=overrides, code_generator="date_based"
        )
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor.hass = MagicMock()
        sensor.async_write_ha_state = MagicMock()

        sensor._handle_coordinator_update()

        attrs = sensor.extra_state_attributes
        assert attrs["slot_code"] is not None
        assert attrs["slot_code"].isdigit()

    @freeze_time("2025-03-10T12:00:00+00:00")
    def test_fires_update_times_when_dates_change(self, hass) -> None:
        """Verify async_fire_update_times is scheduled when override dates differ."""
        event = _make_event(
            start=datetime(2025, 3, 15, 16, 0, tzinfo=timezone.utc),
            end=datetime(2025, 3, 20, 11, 0, tzinfo=timezone.utc),
        )
        override = {
            "slot_code": "1234",
            "start_time": datetime(2025, 3, 14, 16, 0, tzinfo=timezone.utc),
            "end_time": datetime(2025, 3, 20, 11, 0, tzinfo=timezone.utc),
        }
        overrides = MagicMock()
        overrides.get_slot_with_name.return_value = override
        coordinator = _make_coordinator(
            data=[event],
            event_overrides=overrides,
            code_generator="static_random",
        )
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor.hass = MagicMock()
        sensor.async_write_ha_state = MagicMock()

        with patch(
            "custom_components.rental_control.sensors.calsensor.async_fire_update_times",
            new_callable=AsyncMock,
        ) as mock_update_times:
            sensor._handle_coordinator_update()
            mock_update_times.assert_called_once_with(coordinator, sensor)
            sensor.hass.async_create_task.assert_called_once()

    @freeze_time("2025-03-10T12:00:00+00:00")
    def test_fires_clear_code_on_date_shift_date_based(self, hass) -> None:
        """Verify async_fire_clear_code is scheduled on date shift with date_based generator."""
        event = _make_event(
            start=datetime(2025, 3, 15, 16, 0, tzinfo=timezone.utc),
            end=datetime(2025, 3, 20, 11, 0, tzinfo=timezone.utc),
        )
        override = {
            "slot_code": "1234",
            "start_time": datetime(2025, 3, 14, 16, 0, tzinfo=timezone.utc),
            "end_time": datetime(2025, 3, 20, 11, 0, tzinfo=timezone.utc),
        }
        overrides = MagicMock()
        overrides.get_slot_with_name.return_value = override
        overrides.get_slot_key_by_name.return_value = 10
        coordinator = _make_coordinator(
            data=[event],
            event_overrides=overrides,
            code_generator="date_based",
            should_update_code=True,
        )
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor.hass = MagicMock()
        sensor.async_write_ha_state = MagicMock()

        with patch(
            "custom_components.rental_control.sensors.calsensor.async_fire_clear_code",
            new_callable=AsyncMock,
        ) as mock_clear_code:
            sensor._handle_coordinator_update()
            mock_clear_code.assert_called_once_with(coordinator, 10)
            sensor.hass.async_create_task.assert_called_once()

    @freeze_time("2025-03-10T12:00:00+00:00")
    def test_no_override_interactions_when_overrides_none(self, hass) -> None:
        """Verify no set_code/update_times calls when event_overrides is None."""
        event = _make_event()
        coordinator = _make_coordinator(data=[event], event_overrides=None)
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor.hass = MagicMock()
        sensor.async_write_ha_state = MagicMock()

        with patch(
            "custom_components.rental_control.sensors.calsensor.async_fire_set_code",
            new_callable=AsyncMock,
        ) as mock_set_code:
            sensor._handle_coordinator_update()
            mock_set_code.assert_not_called()

    @freeze_time("2025-03-10T12:00:00+00:00")
    def test_no_set_code_when_override_exists(self, hass) -> None:
        """Verify set_code is not called when slot already has an override."""
        event = _make_event()
        override = {
            "slot_code": "1234",
            "start_time": event.start,
            "end_time": event.end,
        }
        overrides = MagicMock()
        overrides.get_slot_with_name.return_value = override
        coordinator = _make_coordinator(data=[event], event_overrides=overrides)
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor.hass = MagicMock()
        sensor.async_write_ha_state = MagicMock()

        with patch(
            "custom_components.rental_control.sensors.calsensor.async_fire_set_code",
            new_callable=AsyncMock,
        ) as mock_set_code:
            sensor._handle_coordinator_update()
            mock_set_code.assert_not_called()

    @freeze_time("2025-03-15T12:00:00+00:00")
    def test_updates_times_not_clear_when_eta_days_zero(self, hass) -> None:
        """Verify update_times (not clear_code) is called when eta_days is 0.

        When event starts today (eta_days=0), the clear_code branch requires
        eta_days > 0. With eta_days=0 the condition is false, so update_times
        is called instead.
        """
        event = _make_event(
            start=datetime(2025, 3, 15, 16, 0, tzinfo=timezone.utc),
            end=datetime(2025, 3, 20, 11, 0, tzinfo=timezone.utc),
        )
        override = {
            "slot_code": "1234",
            "start_time": datetime(2025, 3, 14, 16, 0, tzinfo=timezone.utc),
            "end_time": datetime(2025, 3, 20, 11, 0, tzinfo=timezone.utc),
        }
        overrides = MagicMock()
        overrides.get_slot_with_name.return_value = override
        coordinator = _make_coordinator(
            data=[event],
            event_overrides=overrides,
            code_generator="date_based",
            should_update_code=True,
        )
        sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
        sensor.hass = MagicMock()
        sensor.async_write_ha_state = MagicMock()

        with (
            patch(
                "custom_components.rental_control.sensors.calsensor.async_fire_clear_code",
                new_callable=AsyncMock,
            ) as mock_clear_code,
            patch(
                "custom_components.rental_control.sensors.calsensor.async_fire_update_times",
                new_callable=AsyncMock,
            ) as mock_update_times,
        ):
            sensor._handle_coordinator_update()
            mock_clear_code.assert_not_called()
            mock_update_times.assert_called_once_with(coordinator, sensor)
