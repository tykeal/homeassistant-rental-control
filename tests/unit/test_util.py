# SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the Rental Control util module."""

from __future__ import annotations

from unittest.mock import AsyncMock
from unittest.mock import MagicMock

from homeassistant.const import CONF_NAME
from homeassistant.exceptions import ServiceNotFound
import pytest

from custom_components.rental_control.const import DEFAULT_PATH
from custom_components.rental_control.const import NAME
from custom_components.rental_control.util import add_call
from custom_components.rental_control.util import async_reload_package_platforms
from custom_components.rental_control.util import delete_folder
from custom_components.rental_control.util import delete_rc_and_base_folder
from custom_components.rental_control.util import gen_uuid
from custom_components.rental_control.util import get_event_names
from custom_components.rental_control.util import get_slot_name

# ---------------------------------------------------------------------------
# gen_uuid tests
# ---------------------------------------------------------------------------


class TestGenUuid:
    """Tests for the gen_uuid function."""

    def test_returns_valid_uuid_string(self) -> None:
        """Verify gen_uuid returns a properly formatted UUID string."""
        result = gen_uuid("2025-01-01T00:00:00")
        # UUID format: 8-4-4-4-12 hex characters
        parts = result.split("-")
        assert len(parts) == 5
        assert len(parts[0]) == 8
        assert len(parts[1]) == 4
        assert len(parts[2]) == 4
        assert len(parts[3]) == 4
        assert len(parts[4]) == 12

    def test_deterministic_output(self) -> None:
        """Verify gen_uuid produces the same UUID for the same input."""
        created = "2025-06-15T10:30:00"
        assert gen_uuid(created) == gen_uuid(created)

    def test_different_inputs_produce_different_uuids(self) -> None:
        """Verify different creation times produce different UUIDs."""
        assert gen_uuid("2025-01-01") != gen_uuid("2025-01-02")

    def test_uses_name_constant_in_hash(self) -> None:
        """Verify the NAME constant is incorporated in the hash."""
        import hashlib
        import uuid

        created = "2025-01-01"
        expected_md5 = hashlib.md5(f"{NAME} {created}".encode("utf-8")).hexdigest()
        expected = str(uuid.UUID(expected_md5))
        assert gen_uuid(created) == expected

    def test_empty_created_string(self) -> None:
        """Verify gen_uuid handles an empty created string."""
        result = gen_uuid("")
        assert isinstance(result, str)
        assert len(result.split("-")) == 5


# ---------------------------------------------------------------------------
# get_slot_name tests — "Not available" / "Blocked" paths
# ---------------------------------------------------------------------------


class TestGetSlotNameBlocked:
    """Tests for get_slot_name returning None on blocked/unavailable events."""

    def test_not_available_returns_none(self) -> None:
        """Verify 'Not available' summary returns None."""
        assert get_slot_name("Not available", "", "") is None

    def test_blocked_returns_none(self) -> None:
        """Verify 'Blocked' summary returns None."""
        assert get_slot_name("Blocked", "", "") is None

    def test_not_available_with_prefix(self) -> None:
        """Verify 'Not available' returns None even when prefixed."""
        assert get_slot_name("Rental Not available", "", "Rental") is None

    def test_blocked_with_prefix(self) -> None:
        """Verify 'Blocked' returns None even when prefixed."""
        assert get_slot_name("Rental Blocked", "", "Rental") is None

    def test_blocked_partial_match(self) -> None:
        """Verify Blocked pattern matches within a string."""
        assert get_slot_name("Event Blocked for cleaning", "", "") is None


# ---------------------------------------------------------------------------
# get_slot_name tests — Airbnb exact "Reserved"
# ---------------------------------------------------------------------------


class TestGetSlotNameAirbnbExactReserved:
    """Tests for get_slot_name with exact 'Reserved' summary (Airbnb)."""

    def test_reserved_with_confirmation_code_in_description(self) -> None:
        """Verify confirmation code is extracted from description."""
        desc = "Confirmation Code: HMABCDEF12\nSome other text"
        result = get_slot_name("Reserved", desc, "")
        assert result == "HMABCDEF12"

    def test_reserved_with_no_description(self) -> None:
        """Verify None when description is empty/falsy."""
        assert get_slot_name("Reserved", "", "") is None
        assert get_slot_name("Reserved", None, "") is None  # type: ignore[arg-type]

    def test_reserved_description_no_match(self) -> None:
        """Verify None when description has no 10-char code."""
        assert get_slot_name("Reserved", "just some random text", "") is None

    def test_reserved_description_lowercase_code(self) -> None:
        """Verify lowercase codes are not matched (all characters must be uppercase letters or digits)."""
        assert get_slot_name("Reserved", "hmabcdef12", "") is None

    def test_reserved_with_prefix(self) -> None:
        """Verify 'Reserved' extraction works with prefix stripping."""
        desc = "HMCODE1234 details"
        result = get_slot_name("Rental Reserved", desc, "Rental")
        assert result == "HMCODE1234"

    def test_reserved_code_exactly_10_chars(self) -> None:
        """Verify a code of exactly 10 characters is extracted."""
        desc = "Code: A123456789"
        result = get_slot_name("Reserved", desc, "")
        assert result == "A123456789"


# ---------------------------------------------------------------------------
# get_slot_name tests — Airbnb "Reserved - X"
# ---------------------------------------------------------------------------


class TestGetSlotNameAirbnbReservedWithName:
    """Tests for get_slot_name with 'Reserved - Name' format."""

    def test_reserved_dash_name(self) -> None:
        """Verify guest name is extracted from 'Reserved - Name' format."""
        result = get_slot_name("Reserved - John Doe", "", "")
        assert result == "John Doe"

    def test_reserved_dash_name_with_prefix(self) -> None:
        """Verify extraction works with prefix."""
        result = get_slot_name("Rental Reserved - Jane", "", "Rental")
        assert result == "Jane"

    def test_reserved_dash_name_trailing_spaces(self) -> None:
        """Verify trailing spaces are stripped from the extracted name."""
        result = get_slot_name("Reserved - Alice  ", "", "")
        assert result == "Alice"

    def test_reserved_dash_empty_name(self) -> None:
        """Verify 'Reserved - ' with only whitespace after dash returns empty."""
        result = get_slot_name("Reserved - ", "", "")
        assert result == ""


# ---------------------------------------------------------------------------
# get_slot_name tests — Tripadvisor
# ---------------------------------------------------------------------------


class TestGetSlotNameTripadvisor:
    """Tests for get_slot_name with Tripadvisor-format summaries."""

    def test_tripadvisor_standard(self) -> None:
        """Verify name extraction from standard Tripadvisor format."""
        result = get_slot_name("Tripadvisor Booking: Guest Name", "", "")
        assert result == "Guest Name"

    def test_tripadvisor_variation(self) -> None:
        """Verify name extraction from alternative Tripadvisor format."""
        result = get_slot_name("Tripadvisor reservation: Bob Smith", "", "")
        assert result == "Bob Smith"

    def test_tripadvisor_with_prefix(self) -> None:
        """Verify Tripadvisor extraction works with prefix."""
        result = get_slot_name("Rental Tripadvisor Booking: Alice", "", "Rental")
        assert result == "Alice"


# ---------------------------------------------------------------------------
# get_slot_name tests — Booking.com (CLOSED)
# ---------------------------------------------------------------------------


class TestGetSlotNameBookingCom:
    """Tests for get_slot_name with Booking.com 'CLOSED - X' format."""

    def test_closed_dash_name(self) -> None:
        """Verify guest name is extracted from 'CLOSED - Name'."""
        result = get_slot_name("CLOSED - Mark Johnson", "", "")
        assert result == "Mark Johnson"

    def test_closed_with_prefix(self) -> None:
        """Verify CLOSED extraction works with prefix."""
        result = get_slot_name("Rental CLOSED - Sarah", "", "Rental")
        assert result == "Sarah"


# ---------------------------------------------------------------------------
# get_slot_name tests — Guesty API ("Reservation X")
# ---------------------------------------------------------------------------


class TestGetSlotNameGuestyApi:
    """Tests for get_slot_name with Guesty API 'Reservation X' format."""

    def test_reservation_name(self) -> None:
        """Verify name extraction from 'Reservation X' format."""
        result = get_slot_name("Reservation John Smith", "", "")
        assert result == "John Smith"

    def test_reservation_with_prefix(self) -> None:
        """Verify Reservation extraction works with prefix."""
        result = get_slot_name("Rental Reservation Jane Doe", "", "Rental")
        assert result == "Jane Doe"


# ---------------------------------------------------------------------------
# get_slot_name tests — Guesty ("-Name-...-")
# ---------------------------------------------------------------------------


class TestGetSlotNameGuesty:
    """Tests for get_slot_name with Guesty dash-separated format."""

    def test_guesty_dash_pattern(self) -> None:
        """Verify name extraction from Guesty '-Name-XYZ-' pattern."""
        result = get_slot_name("ABC-John Doe-booking-ref", "", "")
        assert result == "John Doe"

    def test_guesty_dash_pattern_with_prefix(self) -> None:
        """Verify Guesty pattern extraction works with prefix."""
        result = get_slot_name("Rental ABC-Jane-booking-ref", "", "Rental")
        assert result == "Jane"


# ---------------------------------------------------------------------------
# get_slot_name tests — Fallback / degenerative case
# ---------------------------------------------------------------------------


class TestGetSlotNameFallback:
    """Tests for get_slot_name fallback behavior."""

    def test_plain_name_returned_as_is(self) -> None:
        """Verify a plain name is returned when no pattern matches."""
        result = get_slot_name("John Doe", "", "")
        assert result == "John Doe"

    def test_plain_name_stripped(self) -> None:
        """Verify leading/trailing whitespace is stripped from fallback."""
        result = get_slot_name("  Jane Smith  ", "", "")
        assert result == "Jane Smith"

    def test_fallback_with_prefix(self) -> None:
        """Verify fallback works with prefix stripping."""
        result = get_slot_name("Rental Plain Name", "", "Rental")
        assert result == "Plain Name"

    def test_prefix_not_matching_summary_raises(self) -> None:
        """Verify prefix that does not match summary raises IndexError.

        This documents a known deficiency in get_slot_name: when a
        non-empty prefix is supplied but does not appear in the summary,
        the regex returns an empty list and accessing index 0 crashes.
        """
        with pytest.raises(IndexError):
            get_slot_name("Guest Q", "", "Vacation")


# ---------------------------------------------------------------------------
# get_event_names tests
# ---------------------------------------------------------------------------


class TestGetEventNames:
    """Tests for the get_event_names function."""

    def _make_sensor(self, slot_name: str) -> MagicMock:
        """Create a mock event sensor with the given slot_name attribute.

        Args:
            slot_name: The slot_name to assign.

        Returns:
            MagicMock: Mock sensor with extra_state_attributes.
        """
        sensor = MagicMock()
        sensor.extra_state_attributes = {"slot_name": slot_name}
        return sensor

    def test_returns_names_from_sensors(self) -> None:
        """Verify event names are collected from event sensors."""
        rc = MagicMock()
        rc.event_sensors = [
            self._make_sensor("Alice"),
            self._make_sensor("Bob"),
        ]
        assert get_event_names(rc) == ["Alice", "Bob"]

    def test_filters_out_empty_slot_names(self) -> None:
        """Verify sensors with empty/falsy slot names are excluded."""
        rc = MagicMock()
        rc.event_sensors = [
            self._make_sensor("Alice"),
            self._make_sensor(""),
            self._make_sensor("Bob"),
        ]
        assert get_event_names(rc) == ["Alice", "Bob"]

    def test_filters_out_none_slot_names(self) -> None:
        """Verify sensors with None slot names are excluded."""
        rc = MagicMock()
        rc.event_sensors = [
            self._make_sensor(None),  # type: ignore[arg-type]
            self._make_sensor("Carol"),
        ]
        assert get_event_names(rc) == ["Carol"]

    def test_empty_sensor_list(self) -> None:
        """Verify empty list is returned when there are no event sensors."""
        rc = MagicMock()
        rc.event_sensors = []
        assert get_event_names(rc) == []


# ---------------------------------------------------------------------------
# delete_folder tests
# ---------------------------------------------------------------------------


class TestDeleteFolder:
    """Tests for the delete_folder function."""

    def test_nonexistent_path_is_noop(self, tmp_path) -> None:
        """Verify no error when path does not exist."""
        delete_folder(str(tmp_path), "nonexistent")

    def test_deletes_single_file(self, tmp_path) -> None:
        """Verify a single file is deleted."""
        f = tmp_path / "file.txt"
        f.write_text("data")
        delete_folder(str(tmp_path), "file.txt")
        assert not f.exists()

    def test_deletes_nested_directory(self, tmp_path) -> None:
        """Verify recursive deletion of nested directories."""
        sub = tmp_path / "subdir"
        sub.mkdir()
        (sub / "a.txt").write_text("a")
        inner = sub / "inner"
        inner.mkdir()
        (inner / "b.txt").write_text("b")

        delete_folder(str(tmp_path), "subdir")
        assert not sub.exists()

    def test_deletes_empty_directory(self, tmp_path) -> None:
        """Verify an empty directory is deleted."""
        sub = tmp_path / "empty"
        sub.mkdir()
        delete_folder(str(tmp_path), "empty")
        assert not sub.exists()


# ---------------------------------------------------------------------------
# delete_rc_and_base_folder tests
# ---------------------------------------------------------------------------


class TestDeleteRcAndBaseFolder:
    """Tests for the delete_rc_and_base_folder function."""

    def test_deletes_rc_folder_and_empty_base(self, tmp_path) -> None:
        """Verify RC folder is deleted and empty base folder is removed."""
        base = tmp_path / "packages" / "rental_control"
        rc_dir = base / "test_rental"
        rc_dir.mkdir(parents=True)
        (rc_dir / "file.yaml").write_text("data")

        hass = MagicMock()
        hass.config.path.return_value = str(tmp_path)

        entry = MagicMock()
        entry.data = {
            CONF_NAME: "Test Rental",
            "packages_path": "packages/rental_control",
        }

        delete_rc_and_base_folder(hass, entry)

        assert not rc_dir.exists()
        assert not base.exists()

    def test_preserves_base_when_not_empty(self, tmp_path) -> None:
        """Verify base folder is kept when it contains other items."""
        base = tmp_path / "packages" / "rental_control"
        rc_dir = base / "test_rental"
        rc_dir.mkdir(parents=True)
        (rc_dir / "file.yaml").write_text("data")
        other = base / "other_rental"
        other.mkdir()

        hass = MagicMock()
        hass.config.path.return_value = str(tmp_path)

        entry = MagicMock()
        entry.data = {
            CONF_NAME: "Test Rental",
            "packages_path": "packages/rental_control",
        }

        delete_rc_and_base_folder(hass, entry)

        assert not rc_dir.exists()
        assert base.exists()

    def test_uses_default_path_when_not_configured(self, tmp_path) -> None:
        """Verify DEFAULT_PATH is used when CONF_PATH absent from data."""
        base = tmp_path / DEFAULT_PATH
        rc_dir = base / "test_rental"
        rc_dir.mkdir(parents=True)

        hass = MagicMock()
        hass.config.path.return_value = str(tmp_path)

        entry = MagicMock()
        entry.data = {CONF_NAME: "Test Rental"}

        delete_rc_and_base_folder(hass, entry)

        assert not rc_dir.exists()

    def test_handles_nonexistent_base_path(self, tmp_path) -> None:
        """Verify no error when neither RC nor base paths exist."""
        hass = MagicMock()
        hass.config.path.return_value = str(tmp_path)

        entry = MagicMock()
        entry.data = {
            CONF_NAME: "Missing Rental",
            "packages_path": "packages/rental_control",
        }

        # Should not raise
        delete_rc_and_base_folder(hass, entry)


# ---------------------------------------------------------------------------
# async_reload_package_platforms tests
# ---------------------------------------------------------------------------


class TestAsyncReloadPackagePlatforms:
    """Tests for the async_reload_package_platforms function."""

    async def test_returns_true_on_success(self) -> None:
        """Verify True is returned when reload succeeds."""
        mock_hass = MagicMock()
        mock_hass.services.async_call = AsyncMock()
        result = await async_reload_package_platforms(mock_hass)
        assert result is True

    async def test_returns_false_on_service_not_found(self) -> None:
        """Verify False is returned when ServiceNotFound is raised."""
        mock_hass = MagicMock()
        mock_hass.services.async_call = AsyncMock(
            side_effect=ServiceNotFound("automation", "reload")
        )
        result = await async_reload_package_platforms(mock_hass)
        assert result is False

    async def test_calls_automation_reload(self) -> None:
        """Verify the automation domain reload service is called."""
        mock_hass = MagicMock()
        mock_hass.services.async_call = AsyncMock()
        await async_reload_package_platforms(mock_hass)
        mock_hass.services.async_call.assert_called_once_with(
            "automation", "reload", blocking=True
        )


# ---------------------------------------------------------------------------
# add_call tests
# ---------------------------------------------------------------------------


class TestAddCall:
    """Tests for the add_call function."""

    def test_appends_coroutine_to_list(self) -> None:
        """Verify a coroutine is appended to the provided list."""
        hass = MagicMock()
        hass.services.async_call = MagicMock(return_value="coro_placeholder")

        coro_list: list = []
        result = add_call(
            hass, coro_list, "switch", "turn_on", "switch.test_entity", {}
        )

        assert len(result) == 1
        assert result is coro_list

    def test_passes_correct_arguments(self) -> None:
        """Verify async_call receives domain, service, target, and data."""
        hass = MagicMock()
        hass.services.async_call = MagicMock(return_value="coro")

        add_call(
            hass,
            [],
            "text",
            "set_value",
            "text.lock_code_slot_1_pin",
            {"value": "1234"},
        )

        hass.services.async_call.assert_called_once_with(
            domain="text",
            service="set_value",
            target={"entity_id": "text.lock_code_slot_1_pin"},
            service_data={"value": "1234"},
            blocking=True,
        )

    def test_multiple_calls_accumulate(self) -> None:
        """Verify multiple add_call invocations build up the list."""
        hass = MagicMock()
        hass.services.async_call = MagicMock(return_value="coro")

        coro_list: list = []
        add_call(hass, coro_list, "switch", "turn_on", "switch.a", {})
        add_call(hass, coro_list, "switch", "turn_off", "switch.b", {})

        assert len(coro_list) == 2
