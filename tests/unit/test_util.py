# SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the Rental Control util module."""

from __future__ import annotations

from datetime import date
from datetime import timedelta
import logging
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

from homeassistant.components.calendar import CalendarEvent
from homeassistant.const import CONF_NAME
from homeassistant.core import Event
from homeassistant.exceptions import ServiceNotFound
from homeassistant.util import dt as dt_util
import pytest

from custom_components.rental_control.const import COORDINATOR
from custom_components.rental_control.const import DEFAULT_PATH
from custom_components.rental_control.const import DOMAIN
from custom_components.rental_control.const import NAME
from custom_components.rental_control.util import EventIdentity
from custom_components.rental_control.util import add_call
from custom_components.rental_control.util import async_fire_clear_code
from custom_components.rental_control.util import async_fire_set_code
from custom_components.rental_control.util import async_fire_update_times
from custom_components.rental_control.util import async_reload_package_platforms
from custom_components.rental_control.util import compute_early_expiry_time
from custom_components.rental_control.util import delete_folder
from custom_components.rental_control.util import delete_rc_and_base_folder
from custom_components.rental_control.util import gen_uuid
from custom_components.rental_control.util import get_event_identities
from custom_components.rental_control.util import get_event_names
from custom_components.rental_control.util import get_slot_name
from custom_components.rental_control.util import handle_state_change

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


class TestGetSlotNameAirbnbVrboReservedWithName:
    """Tests for get_slot_name with 'Reserved - Name' format (Airbnb and VRBO)."""

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

    def test_returns_names_from_events(self) -> None:
        """Verify event names are collected from coordinator data."""
        rc = MagicMock()
        rc.event_prefix = None
        rc.data = [
            CalendarEvent(
                summary="Alice",
                start=date(2025, 3, 15),
                end=date(2025, 3, 20),
            ),
            CalendarEvent(
                summary="Bob",
                start=date(2025, 3, 21),
                end=date(2025, 3, 25),
            ),
        ]
        assert get_event_names(rc) == ["Alice", "Bob"]

    def test_filters_out_empty_slot_names(self) -> None:
        """Verify events with empty/falsy slot names are excluded."""
        rc = MagicMock()
        rc.event_prefix = None
        rc.data = [
            CalendarEvent(
                summary="Alice",
                start=date(2025, 3, 15),
                end=date(2025, 3, 20),
            ),
            CalendarEvent(
                summary="  ",
                start=date(2025, 3, 21),
                end=date(2025, 3, 25),
            ),
            CalendarEvent(
                summary="Bob",
                start=date(2025, 3, 26),
                end=date(2025, 3, 30),
            ),
        ]
        assert get_event_names(rc) == ["Alice", "Bob"]

    def test_filters_out_none_slot_names(self) -> None:
        """Verify events producing None slot names are excluded."""
        rc = MagicMock()
        rc.event_prefix = None
        rc.data = [
            CalendarEvent(
                summary="Blocked",
                start=date(2025, 3, 15),
                end=date(2025, 3, 20),
            ),
            CalendarEvent(
                summary="Carol",
                start=date(2025, 3, 21),
                end=date(2025, 3, 25),
            ),
        ]
        assert get_event_names(rc) == ["Carol"]

    def test_empty_data_list(self) -> None:
        """Verify empty list is returned when there are no events."""
        rc = MagicMock()
        rc.event_prefix = None
        rc.data = []
        assert get_event_names(rc) == []


# ---------------------------------------------------------------------------
# get_event_identities tests
# ---------------------------------------------------------------------------


class TestGetEventIdentities:
    """Tests for the get_event_identities function."""

    def test_returns_identities_with_times_and_uid(self) -> None:
        """Verify structured identities include name, times, uid."""
        rc = MagicMock()
        rc.event_prefix = None
        event = MagicMock()
        event.summary = "Alice"
        event.description = ""
        event.start = dt_util.now()
        event.end = dt_util.now() + timedelta(days=5)
        event.uid = "uid-123"
        rc.data = [event]

        result = get_event_identities(rc)
        assert len(result) == 1
        assert result[0] == EventIdentity("Alice", event.start, event.end, "uid-123")

    def test_filters_blocked_events(self) -> None:
        """Verify blocked events are excluded."""
        rc = MagicMock()
        rc.event_prefix = None
        blocked = MagicMock()
        blocked.summary = "Blocked"
        blocked.description = ""
        blocked.start = dt_util.now()
        blocked.end = dt_util.now() + timedelta(days=1)
        blocked.uid = None
        rc.data = [blocked]

        assert get_event_identities(rc) == []

    def test_empty_calendar(self) -> None:
        """Verify empty list when no events."""
        rc = MagicMock()
        rc.event_prefix = None
        rc.data = []
        assert get_event_identities(rc) == []

    def test_event_with_default_uid_returns_none(self) -> None:
        """Verify uid is None when CalendarEvent has default uid."""
        rc = MagicMock()
        rc.event_prefix = None
        event = CalendarEvent(
            summary="Bob",
            start=date(2025, 3, 15),
            end=date(2025, 3, 20),
        )
        rc.data = [event]

        result = get_event_identities(rc)
        assert len(result) == 1
        assert result[0].uid is None

    def test_event_object_without_uid_attribute(self) -> None:
        """Verify uid is None when event object lacks uid attribute."""
        rc = MagicMock()
        rc.event_prefix = None
        event = MagicMock(spec=["summary", "description", "start", "end"])
        event.summary = "Bob"
        event.description = ""
        event.start = date(2025, 3, 15)
        event.end = date(2025, 3, 20)
        rc.data = [event]

        result = get_event_identities(rc)
        assert len(result) == 1
        assert result[0].uid is None

    def test_uses_calendar_parameter(self) -> None:
        """Verify calendar parameter overrides coordinator data."""
        rc = MagicMock()
        rc.event_prefix = None
        rc.data = []

        event = MagicMock()
        event.summary = "Override"
        event.description = ""
        event.start = dt_util.now()
        event.end = dt_util.now() + timedelta(days=3)
        event.uid = "uid-456"

        result = get_event_identities(rc, calendar=[event])
        assert len(result) == 1
        assert result[0].name == "Override"


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


# ---------------------------------------------------------------------------
# handle_state_change logging tests
# ---------------------------------------------------------------------------


class TestHandleStateChangeLogging:
    """Tests for handle_state_change debug logging correctness."""

    async def test_debug_log_contains_all_override_fields(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Verify the override update log message includes all fields.

        Prior to the fix, extra f-string arguments were passed as
        positional *args to _LOGGER.debug() and silently dropped.
        This test ensures lockname, slot_num, slot_name, slot_code,
        start_time, and end_time all appear in the emitted message.
        """
        lockname = "test_lock"
        slot_num = 10
        mock_slot_code_state = MagicMock()
        mock_slot_code_state.state = "1234"
        mock_slot_name_state = MagicMock()
        mock_slot_name_state.state = "Guest Name"
        mock_slot_enabled = MagicMock()
        mock_slot_enabled.state = "on"

        mock_coordinator = MagicMock()
        mock_coordinator.lockname = lockname
        mock_coordinator.event_overrides = MagicMock()
        mock_coordinator.event_overrides.async_check_overrides = AsyncMock()
        mock_coordinator.update_event_overrides = AsyncMock()

        def states_get(entity_id: str) -> MagicMock | None:
            """Return mock states for various entities."""
            if "enabled" in entity_id:
                return mock_slot_enabled
            if "pin" in entity_id:
                return mock_slot_code_state
            if "name" in entity_id:
                return mock_slot_name_state
            if "use_date_range" in entity_id:
                return None
            return None

        hass = MagicMock()
        hass.data = {
            DOMAIN: {
                "entry_id": {COORDINATOR: mock_coordinator},
            }
        }
        hass.states.get = states_get

        config_entry = MagicMock()
        config_entry.entry_id = "entry_id"

        event = MagicMock(spec=Event)
        event.data = {"entity_id": f"switch.{lockname}_code_slot_{slot_num}_enabled"}

        with (
            patch("custom_components.rental_control.util.asyncio.sleep"),
            caplog.at_level(
                logging.DEBUG, logger="custom_components.rental_control.util"
            ),
        ):
            await handle_state_change(hass, config_entry, event)

        log_messages = " ".join(caplog.messages)
        assert lockname in log_messages
        assert str(slot_num) in log_messages
        # Verify slot_name and slot_code objects appear (as repr strings)
        assert "slot_name:" in log_messages
        assert "slot_code:" in log_messages
        # Verify start_time and end_time appear in the same message
        assert "start_time:" in log_messages
        assert "end_time:" in log_messages
        # The critical assertion: all fields must be in a SINGLE log message.
        # The original bug caused slot_name/slot_code/start_time/end_time to
        # be silently dropped because they were separate f-string *args.
        override_msgs = [m for m in caplog.messages if "updating overrides for" in m]
        assert len(override_msgs) == 1
        single_msg = override_msgs[0]
        assert "slot_name:" in single_msg
        assert "slot_code:" in single_msg
        assert "start_time:" in single_msg
        assert "end_time:" in single_msg


# ---------------------------------------------------------------------------
# handle_state_change state mutation tests
# ---------------------------------------------------------------------------


class TestHandleStateChangeStateMutation:
    """Tests that handle_state_change does not mutate HA State objects."""

    async def test_unknown_slot_code_not_mutated(self) -> None:
        """Verify State.state is not mutated when slot_code is 'unknown'.

        The original code directly set slot_code.state = "" which mutates
        Home Assistant internal State objects. The fix uses local variables.
        """
        lockname = "test_lock"
        slot_num = 10
        mock_slot_code_state = MagicMock()
        mock_slot_code_state.state = "unknown"
        mock_slot_name_state = MagicMock()
        mock_slot_name_state.state = "Guest"
        mock_slot_enabled = MagicMock()
        mock_slot_enabled.state = "on"

        mock_coordinator = MagicMock()
        mock_coordinator.lockname = lockname
        mock_coordinator.event_overrides = MagicMock()
        mock_coordinator.event_overrides.async_check_overrides = AsyncMock()
        mock_coordinator.update_event_overrides = AsyncMock()

        def states_get(entity_id: str) -> MagicMock | None:
            """Return mock states for various entities."""
            if "enabled" in entity_id:
                return mock_slot_enabled
            if "pin" in entity_id:
                return mock_slot_code_state
            if "name" in entity_id:
                return mock_slot_name_state
            if "use_date_range" in entity_id:
                return None
            return None

        hass = MagicMock()
        hass.data = {DOMAIN: {"entry_id": {COORDINATOR: mock_coordinator}}}
        hass.states.get = states_get

        config_entry = MagicMock()
        config_entry.entry_id = "entry_id"

        event = MagicMock(spec=Event)
        event.data = {"entity_id": f"switch.{lockname}_code_slot_{slot_num}_enabled"}

        with patch("custom_components.rental_control.util.asyncio.sleep"):
            await handle_state_change(hass, config_entry, event)

        # State object must NOT have been mutated
        assert mock_slot_code_state.state == "unknown"
        # But update_event_overrides should have been called with ""
        mock_coordinator.update_event_overrides.assert_awaited_once()
        call_args = mock_coordinator.update_event_overrides.call_args
        assert call_args[0][1] == ""  # slot_code_value should be ""

    async def test_unavailable_slot_name_not_mutated(self) -> None:
        """Verify State.state is not mutated when slot_name is 'unavailable'.

        The original code directly set slot_name.state = "" which mutates
        Home Assistant internal State objects. The fix uses local variables.
        """
        lockname = "test_lock"
        slot_num = 10
        mock_slot_code_state = MagicMock()
        mock_slot_code_state.state = "1234"
        mock_slot_name_state = MagicMock()
        mock_slot_name_state.state = "unavailable"
        mock_slot_enabled = MagicMock()
        mock_slot_enabled.state = "on"

        mock_coordinator = MagicMock()
        mock_coordinator.lockname = lockname
        mock_coordinator.event_overrides = MagicMock()
        mock_coordinator.event_overrides.async_check_overrides = AsyncMock()
        mock_coordinator.update_event_overrides = AsyncMock()

        def states_get(entity_id: str) -> MagicMock | None:
            """Return mock states for various entities."""
            if "enabled" in entity_id:
                return mock_slot_enabled
            if "pin" in entity_id:
                return mock_slot_code_state
            if "name" in entity_id:
                return mock_slot_name_state
            if "use_date_range" in entity_id:
                return None
            return None

        hass = MagicMock()
        hass.data = {DOMAIN: {"entry_id": {COORDINATOR: mock_coordinator}}}
        hass.states.get = states_get

        config_entry = MagicMock()
        config_entry.entry_id = "entry_id"

        event = MagicMock(spec=Event)
        event.data = {"entity_id": f"switch.{lockname}_code_slot_{slot_num}_enabled"}

        with patch("custom_components.rental_control.util.asyncio.sleep"):
            await handle_state_change(hass, config_entry, event)

        # State object must NOT have been mutated
        assert mock_slot_name_state.state == "unavailable"
        # But update_event_overrides should have been called with ""
        mock_coordinator.update_event_overrides.assert_awaited_once()
        call_args = mock_coordinator.update_event_overrides.call_args
        assert call_args[0][2] == ""  # slot_name_value should be ""


# ---------------------------------------------------------------------------
# handle_state_change unbound variable tests
# ---------------------------------------------------------------------------


class TestHandleStateChangeUnboundVars:
    """Tests that handle_state_change does not raise UnboundLocalError."""

    async def test_unparseable_start_time_uses_default(self) -> None:
        """Verify start_time defaults when parse_datetime returns None.

        When use_date_range is on but the start time state cannot be
        parsed, start_time must fall back to start_of_local_day instead
        of raising UnboundLocalError.
        """
        lockname = "test_lock"
        slot_num = 10

        mock_slot_code_state = MagicMock()
        mock_slot_code_state.state = "1234"
        mock_slot_name_state = MagicMock()
        mock_slot_name_state.state = "Guest"
        mock_slot_enabled = MagicMock()
        mock_slot_enabled.state = "on"
        mock_use_date_range = MagicMock()
        mock_use_date_range.state = "on"
        mock_start_time = MagicMock()
        mock_start_time.state = "not-a-datetime"
        mock_end_time = MagicMock()
        mock_end_time.state = "not-a-datetime"

        mock_coordinator = MagicMock()
        mock_coordinator.lockname = lockname
        mock_coordinator.event_overrides = MagicMock()
        mock_coordinator.event_overrides.async_check_overrides = AsyncMock()
        mock_coordinator.update_event_overrides = AsyncMock()

        def states_get(entity_id: str) -> MagicMock | None:
            """Return mock states for various entities."""
            if "enabled" in entity_id:
                return mock_slot_enabled
            if "pin" in entity_id:
                return mock_slot_code_state
            if "name" in entity_id and "date_range" not in entity_id:
                return mock_slot_name_state
            if "use_date_range" in entity_id:
                return mock_use_date_range
            if "date_range_start" in entity_id:
                return mock_start_time
            if "date_range_end" in entity_id:
                return mock_end_time
            return None

        hass = MagicMock()
        hass.data = {DOMAIN: {"entry_id": {COORDINATOR: mock_coordinator}}}
        hass.states.get = states_get

        config_entry = MagicMock()
        config_entry.entry_id = "entry_id"

        event = MagicMock(spec=Event)
        event.data = {"entity_id": f"switch.{lockname}_code_slot_{slot_num}_enabled"}

        # This must not raise UnboundLocalError
        with patch("custom_components.rental_control.util.asyncio.sleep"):
            await handle_state_change(hass, config_entry, event)

        # update_event_overrides should still be called with default times
        mock_coordinator.update_event_overrides.assert_awaited_once()
        call_args = mock_coordinator.update_event_overrides.call_args
        # start_time and end_time should be start_of_local_day defaults
        expected_default = dt_util.start_of_local_day()
        assert call_args[0][3] == expected_default  # start_time
        assert call_args[0][4] == expected_default  # end_time


# ---------------------------------------------------------------------------
# handle_state_change slot number extraction tests
# ---------------------------------------------------------------------------


class TestHandleStateChangeSlotExtraction:
    """Tests that handle_state_change extracts slot numbers correctly."""

    async def test_numeric_lockname_extracts_correct_slot(self) -> None:
        """Verify slot extraction when lockname contains numeric segments.

        Prior to the fix, digit segments in the lockname could be
        incorrectly selected as the slot number. The regex-based
        extraction targets the _code_slot_N_ pattern specifically.
        """
        lockname = "lock_2_front"
        expected_slot = 5

        mock_slot_code_state = MagicMock()
        mock_slot_code_state.state = "1234"
        mock_slot_name_state = MagicMock()
        mock_slot_name_state.state = "Guest"
        mock_slot_enabled = MagicMock()
        mock_slot_enabled.state = "on"

        mock_coordinator = MagicMock()
        mock_coordinator.lockname = lockname
        mock_coordinator.event_overrides = MagicMock()
        mock_coordinator.event_overrides.async_check_overrides = AsyncMock()
        mock_coordinator.update_event_overrides = AsyncMock()

        def states_get(entity_id: str) -> MagicMock | None:
            """Return mock states for various entities."""
            if "enabled" in entity_id:
                return mock_slot_enabled
            if "pin" in entity_id:
                return mock_slot_code_state
            if "name" in entity_id:
                return mock_slot_name_state
            if "use_date_range" in entity_id:
                return None
            return None

        hass = MagicMock()
        hass.data = {DOMAIN: {"entry_id": {COORDINATOR: mock_coordinator}}}
        hass.states.get = states_get

        config_entry = MagicMock()
        config_entry.entry_id = "entry_id"

        event = MagicMock(spec=Event)
        event.data = {
            "entity_id": f"switch.{lockname}_code_slot_{expected_slot}_enabled"
        }

        with patch("custom_components.rental_control.util.asyncio.sleep"):
            await handle_state_change(hass, config_entry, event)

        mock_coordinator.update_event_overrides.assert_awaited_once()
        call_args = mock_coordinator.update_event_overrides.call_args
        assert call_args[0][0] == expected_slot

    async def test_malformed_entity_id_returns_early(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Verify early return with warning when slot number cannot be extracted."""
        lockname = "test_lock"

        mock_coordinator = MagicMock()
        mock_coordinator.lockname = lockname

        hass = MagicMock()
        hass.data = {DOMAIN: {"entry_id": {COORDINATOR: mock_coordinator}}}

        config_entry = MagicMock()
        config_entry.entry_id = "entry_id"

        event = MagicMock(spec=Event)
        event.data = {"entity_id": "switch.malformed_entity_no_slot"}

        with (
            patch("custom_components.rental_control.util.asyncio.sleep"),
            caplog.at_level(
                logging.WARNING, logger="custom_components.rental_control.util"
            ),
        ):
            await handle_state_change(hass, config_entry, event)

        assert "Could not extract slot number" in caplog.text
        mock_coordinator.update_event_overrides.assert_not_called()

    async def test_reset_entity_with_numeric_lockname(self) -> None:
        """Verify reset path extracts correct slot from numeric lockname."""
        lockname = "lock_2_front"
        expected_slot = 3

        mock_coordinator = MagicMock()
        mock_coordinator.lockname = lockname
        mock_coordinator.event_overrides = MagicMock()
        mock_coordinator.event_overrides.async_update = AsyncMock()

        hass = MagicMock()
        hass.data = {DOMAIN: {"entry_id": {COORDINATOR: mock_coordinator}}}

        config_entry = MagicMock()
        config_entry.entry_id = "entry_id"

        event = MagicMock(spec=Event)
        event.data = {"entity_id": f"button.{lockname}_code_slot_{expected_slot}_reset"}

        with patch("custom_components.rental_control.util.asyncio.sleep"):
            await handle_state_change(hass, config_entry, event)

        mock_coordinator.event_overrides.async_update.assert_awaited_once()
        call_args = mock_coordinator.event_overrides.async_update.call_args
        assert call_args[0][0] == expected_slot


# ---------------------------------------------------------------------------
# async_fire_clear_code tests (T021)
# ---------------------------------------------------------------------------


class TestAsyncFireClearCode:
    """Tests for async_fire_clear_code lock slot reset."""

    async def test_calls_button_press_on_reset_entity(self) -> None:
        """Verify the reset button entity is pressed for the given slot."""
        coordinator = MagicMock()
        coordinator.name = "Test Rental"
        coordinator.lockname = "front_door"
        coordinator.hass.services.async_call = AsyncMock()

        await async_fire_clear_code(coordinator, 10)

        coordinator.hass.services.async_call.assert_awaited_once_with(
            domain="button",
            service="press",
            target={"entity_id": "button.front_door_code_slot_10_reset"},
            blocking=True,
        )

    async def test_no_lockname_returns_early(self) -> None:
        """Verify no service call when lockname is empty."""
        coordinator = MagicMock()
        coordinator.name = "Test Rental"
        coordinator.lockname = ""
        coordinator.hass.services.async_call = AsyncMock()

        await async_fire_clear_code(coordinator, 10)

        coordinator.hass.services.async_call.assert_not_awaited()

    async def test_none_lockname_returns_early(self) -> None:
        """Verify no service call when lockname is None."""
        coordinator = MagicMock()
        coordinator.name = "Test Rental"
        coordinator.lockname = None
        coordinator.hass.services.async_call = AsyncMock()

        await async_fire_clear_code(coordinator, 10)

        coordinator.hass.services.async_call.assert_not_awaited()


# ---------------------------------------------------------------------------
# async_fire_set_code tests (T021)
# ---------------------------------------------------------------------------


class TestAsyncFireSetCode:
    """Tests for async_fire_set_code lock slot programming."""

    @staticmethod
    def _make_event(
        slot_name: str = "Guest",
        slot_code: str = "1234",
        start: str = "2025-01-15T16:00:00",
        end: str = "2025-01-17T11:00:00",
    ) -> MagicMock:
        """Build a mock event with slot attributes."""
        event = MagicMock()
        event.extra_state_attributes = {
            "slot_name": slot_name,
            "slot_code": slot_code,
            "start": start,
            "end": end,
        }
        return event

    async def test_no_lockname_returns_early(self) -> None:
        """Verify no service calls when lockname is empty."""
        coordinator = MagicMock()
        coordinator.lockname = ""
        coordinator.hass.services.async_call = AsyncMock()

        await async_fire_set_code(coordinator, self._make_event(), 10)

        coordinator.hass.services.async_call.assert_not_awaited()

    async def test_service_calls_sequence(self) -> None:
        """Verify all service calls are made in the correct order."""
        coordinator = MagicMock()
        coordinator.lockname = "front_door"
        coordinator.event_prefix = ""
        coordinator.hass.services.async_call = AsyncMock()

        event = self._make_event()
        await async_fire_set_code(coordinator, event, 10)

        calls = coordinator.hass.services.async_call.await_args_list

        # Phase 1: disable slot
        assert calls[0].kwargs["domain"] == "switch"
        assert calls[0].kwargs["service"] == "turn_off"
        assert "enabled" in calls[0].kwargs["target"]["entity_id"]

        # Phase 2: enable date range
        assert calls[1].kwargs["domain"] == "switch"
        assert calls[1].kwargs["service"] == "turn_on"
        assert "use_date_range" in calls[1].kwargs["target"]["entity_id"]

        # Phase 3: 4 parallel calls (end, start, pin, name)
        phase3_domains = {c.kwargs["domain"] for c in calls[2:6]}
        assert "datetime" in phase3_domains
        assert "text" in phase3_domains

        # Phase 4: enable slot
        assert calls[6].kwargs["domain"] == "switch"
        assert calls[6].kwargs["service"] == "turn_on"
        assert "enabled" in calls[6].kwargs["target"]["entity_id"]

    async def test_event_prefix_prepended(self) -> None:
        """Verify event_prefix is prepended to the slot name."""
        coordinator = MagicMock()
        coordinator.lockname = "front_door"
        coordinator.event_prefix = "Rental"
        coordinator.hass.services.async_call = AsyncMock()

        event = self._make_event(slot_name="Guest")
        await async_fire_set_code(coordinator, event, 10)

        # Find the name set_value call
        calls = coordinator.hass.services.async_call.await_args_list
        name_calls = [
            c
            for c in calls
            if c.kwargs.get("service_data", {}).get("value") == "Rental Guest"
        ]
        assert len(name_calls) == 1

    async def test_no_prefix_uses_bare_name(self) -> None:
        """Verify bare slot name when no prefix is set."""
        coordinator = MagicMock()
        coordinator.lockname = "front_door"
        coordinator.event_prefix = ""
        coordinator.hass.services.async_call = AsyncMock()

        event = self._make_event(slot_name="Guest")
        await async_fire_set_code(coordinator, event, 10)

        calls = coordinator.hass.services.async_call.await_args_list
        name_calls = [
            c for c in calls if c.kwargs.get("service_data", {}).get("value") == "Guest"
        ]
        assert len(name_calls) == 1

    async def test_gather_exception_propagates_for_retry(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Verify a failing gather call is logged and re-raised for retry."""
        coordinator = MagicMock()
        coordinator.lockname = "front_door"
        coordinator.event_prefix = ""
        coordinator.event_overrides.verify_slot_ownership.return_value = True
        coordinator.event_overrides.record_retry_failure.return_value = False

        error = ServiceNotFound("switch", "turn_off")
        coordinator.hass.services.async_call = AsyncMock(side_effect=error)

        event = self._make_event()
        with (
            caplog.at_level(
                logging.ERROR, logger="custom_components.rental_control.util"
            ),
            pytest.raises(ServiceNotFound),
        ):
            await async_fire_set_code(coordinator, event, 10)

        assert "Lock slot operation" in caplog.text
        coordinator.event_overrides.record_retry_failure.assert_called_once_with(10)


# ---------------------------------------------------------------------------
# async_fire_update_times tests (T021)
# ---------------------------------------------------------------------------


class TestAsyncFireUpdateTimes:
    """Tests for async_fire_update_times slot time updates."""

    @staticmethod
    def _make_event(
        slot_name: str = "Guest",
        start: str = "2025-01-15T16:00:00",
        end: str = "2025-01-17T11:00:00",
    ) -> MagicMock:
        """Build a mock event with slot attributes."""
        event = MagicMock()
        event.extra_state_attributes = {
            "slot_name": slot_name,
            "start": start,
            "end": end,
        }
        return event

    async def test_updates_start_and_end_times(self) -> None:
        """Verify both datetime entities are updated."""
        coordinator = MagicMock()
        coordinator.lockname = "front_door"
        coordinator.event_overrides.get_slot_key_by_name.return_value = 10
        coordinator.hass.services.async_call = AsyncMock()

        event = self._make_event()
        await async_fire_update_times(coordinator, event)

        calls = coordinator.hass.services.async_call.await_args_list
        assert len(calls) == 2

        targets = {c.kwargs["target"]["entity_id"] for c in calls}
        assert "datetime.front_door_code_slot_10_date_range_end" in targets
        assert "datetime.front_door_code_slot_10_date_range_start" in targets

    async def test_no_lockname_returns_early(self) -> None:
        """Verify no service calls when lockname is empty."""
        coordinator = MagicMock()
        coordinator.lockname = ""
        coordinator.event_overrides.get_slot_key_by_name.return_value = 10
        coordinator.hass.services.async_call = AsyncMock()

        await async_fire_update_times(coordinator, self._make_event())

        coordinator.hass.services.async_call.assert_not_awaited()

    async def test_no_slot_found_returns_early(self) -> None:
        """Verify no service calls when slot name is not found."""
        coordinator = MagicMock()
        coordinator.lockname = "front_door"
        coordinator.event_overrides.get_slot_key_by_name.return_value = None
        coordinator.hass.services.async_call = AsyncMock()

        await async_fire_update_times(coordinator, self._make_event())

        coordinator.hass.services.async_call.assert_not_awaited()

    async def test_gather_exception_logged_not_raised(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Verify a failing service call is logged but does not crash."""
        coordinator = MagicMock()
        coordinator.lockname = "front_door"
        coordinator.event_overrides.get_slot_key_by_name.return_value = 10
        coordinator.hass.services.async_call = AsyncMock(
            side_effect=ServiceNotFound("datetime", "set_value")
        )

        with caplog.at_level(
            logging.ERROR, logger="custom_components.rental_control.util"
        ):
            await async_fire_update_times(coordinator, self._make_event())

        assert "Lock slot operation" in caplog.text


# ---------------------------------------------------------------------------
# Pre-execution verification abort tests (T020)
# ---------------------------------------------------------------------------


class TestPreExecutionVerification:
    """Tests for ownership verification before lock commands."""

    async def test_set_code_aborts_on_ownership_mismatch(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Verify async_fire_set_code returns early when ownership fails."""
        coordinator = MagicMock()
        coordinator.lockname = "front_door"
        coordinator.event_prefix = ""
        coordinator.hass.services.async_call = AsyncMock()
        coordinator.event_overrides.verify_slot_ownership.return_value = False

        event = MagicMock()
        event.extra_state_attributes = {
            "slot_name": "Guest",
            "slot_code": "1234",
            "start": "2025-01-15T16:00:00",
            "end": "2025-01-17T11:00:00",
        }

        with caplog.at_level(
            logging.WARNING, logger="custom_components.rental_control.util"
        ):
            await async_fire_set_code(coordinator, event, 10)

        coordinator.hass.services.async_call.assert_not_awaited()
        assert "ownership" in caplog.text.lower()

    async def test_clear_code_aborts_on_ownership_mismatch(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Verify async_fire_clear_code returns early when ownership fails."""
        coordinator = MagicMock()
        coordinator.name = "Test Rental"
        coordinator.lockname = "front_door"
        coordinator.hass.services.async_call = AsyncMock()
        coordinator.event_overrides.verify_slot_ownership.return_value = False

        with caplog.at_level(
            logging.WARNING, logger="custom_components.rental_control.util"
        ):
            await async_fire_clear_code(coordinator, 10, expected_name="Guest")

        coordinator.hass.services.async_call.assert_not_awaited()
        assert "ownership" in caplog.text.lower()

    async def test_update_times_aborts_on_ownership_mismatch(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Verify async_fire_update_times returns early when ownership fails."""
        coordinator = MagicMock()
        coordinator.lockname = "front_door"
        coordinator.event_overrides.get_slot_key_by_name.return_value = 10
        coordinator.event_overrides.verify_slot_ownership.return_value = False
        coordinator.hass.services.async_call = AsyncMock()

        event = MagicMock()
        event.extra_state_attributes = {
            "slot_name": "Guest",
            "start": "2025-01-15T16:00:00",
            "end": "2025-01-17T11:00:00",
        }

        with caplog.at_level(
            logging.WARNING, logger="custom_components.rental_control.util"
        ):
            await async_fire_update_times(coordinator, event)

        coordinator.hass.services.async_call.assert_not_awaited()
        assert "ownership" in caplog.text.lower()

    async def test_set_code_proceeds_when_ownership_matches(self) -> None:
        """Verify async_fire_set_code executes when ownership passes."""
        coordinator = MagicMock()
        coordinator.lockname = "front_door"
        coordinator.event_prefix = ""
        coordinator.hass.services.async_call = AsyncMock()
        coordinator.event_overrides.verify_slot_ownership.return_value = True

        event = MagicMock()
        event.extra_state_attributes = {
            "slot_name": "Guest",
            "slot_code": "1234",
            "start": "2025-01-15T16:00:00",
            "end": "2025-01-17T11:00:00",
        }

        await async_fire_set_code(coordinator, event, 10)

        coordinator.hass.services.async_call.assert_awaited()


# ---------------------------------------------------------------------------
# Retry escalation tests (T020)
# ---------------------------------------------------------------------------


class TestRetryEscalation:
    """Tests for retry tracking and persistent notification escalation."""

    async def test_set_code_records_success_and_dismisses_notification(
        self,
    ) -> None:
        """Verify success path resets retry and dismisses notification."""
        coordinator = MagicMock()
        coordinator.lockname = "front_door"
        coordinator.event_prefix = ""
        coordinator.hass.services.async_call = AsyncMock()
        coordinator.event_overrides.verify_slot_ownership.return_value = True
        coordinator.event_overrides._escalated = {10: True}

        event = MagicMock()
        event.extra_state_attributes = {
            "slot_name": "Guest",
            "slot_code": "1234",
            "start": "2025-01-15T16:00:00",
            "end": "2025-01-17T11:00:00",
        }

        with patch(
            "custom_components.rental_control.util.pn_dismiss",
        ) as mock_dismiss:
            await async_fire_set_code(coordinator, event, 10)

        coordinator.event_overrides.record_retry_success.assert_called_once_with(10)
        mock_dismiss.assert_called_once_with(
            coordinator.hass,
            notification_id="rental_control_slot_10_failure",
        )

    async def test_set_code_no_dismiss_when_not_escalated(self) -> None:
        """Verify no dismiss call when slot was not escalated."""
        coordinator = MagicMock()
        coordinator.lockname = "front_door"
        coordinator.event_prefix = ""
        coordinator.hass.services.async_call = AsyncMock()
        coordinator.event_overrides.verify_slot_ownership.return_value = True
        coordinator.event_overrides._escalated = {}

        event = MagicMock()
        event.extra_state_attributes = {
            "slot_name": "Guest",
            "slot_code": "1234",
            "start": "2025-01-15T16:00:00",
            "end": "2025-01-17T11:00:00",
        }

        with patch(
            "custom_components.rental_control.util.pn_dismiss",
        ) as mock_dismiss:
            await async_fire_set_code(coordinator, event, 10)

        coordinator.event_overrides.record_retry_success.assert_called_once_with(10)
        mock_dismiss.assert_not_called()

    async def test_set_code_failure_records_and_escalates(self) -> None:
        """Verify failure creates persistent notification on escalation."""
        coordinator = MagicMock()
        coordinator.lockname = "front_door"
        coordinator.event_prefix = ""
        coordinator.event_overrides.verify_slot_ownership.return_value = True
        coordinator.event_overrides.record_retry_failure.return_value = True
        coordinator.event_overrides._escalated = {}

        error = Exception("service unavailable")
        coordinator.hass.services.async_call = AsyncMock(side_effect=error)

        event = MagicMock()
        event.extra_state_attributes = {
            "slot_name": "Guest",
            "slot_code": "1234",
            "start": "2025-01-15T16:00:00",
            "end": "2025-01-17T11:00:00",
        }

        with (
            patch(
                "custom_components.rental_control.util.pn_create",
            ) as mock_create,
            pytest.raises(Exception, match="service unavailable"),
        ):
            await async_fire_set_code(coordinator, event, 10)

        coordinator.event_overrides.record_retry_failure.assert_called_once_with(10)
        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args
        assert call_kwargs[1]["notification_id"] == "rental_control_slot_10_failure"

    async def test_set_code_failure_no_notification_below_threshold(self) -> None:
        """Verify no notification when below escalation threshold."""
        coordinator = MagicMock()
        coordinator.lockname = "front_door"
        coordinator.event_prefix = ""
        coordinator.event_overrides.verify_slot_ownership.return_value = True
        coordinator.event_overrides.record_retry_failure.return_value = False

        error = Exception("service unavailable")
        coordinator.hass.services.async_call = AsyncMock(side_effect=error)

        event = MagicMock()
        event.extra_state_attributes = {
            "slot_name": "Guest",
            "slot_code": "1234",
            "start": "2025-01-15T16:00:00",
            "end": "2025-01-17T11:00:00",
        }

        with (
            patch(
                "custom_components.rental_control.util.pn_create",
            ) as mock_create,
            pytest.raises(Exception, match="service unavailable"),
        ):
            await async_fire_set_code(coordinator, event, 10)

        coordinator.event_overrides.record_retry_failure.assert_called_once_with(10)
        mock_create.assert_not_called()

    async def test_clear_code_records_success_and_dismisses(self) -> None:
        """Verify clear_code success resets retry and dismisses notification."""
        coordinator = MagicMock()
        coordinator.name = "Test Rental"
        coordinator.lockname = "front_door"
        coordinator.hass.services.async_call = AsyncMock()
        coordinator.event_overrides.verify_slot_ownership.return_value = True
        coordinator.event_overrides._escalated = {10: True}

        with patch(
            "custom_components.rental_control.util.pn_dismiss",
        ) as mock_dismiss:
            await async_fire_clear_code(coordinator, 10, expected_name="Guest")

        coordinator.event_overrides.record_retry_success.assert_called_once_with(10)
        mock_dismiss.assert_called_once_with(
            coordinator.hass,
            notification_id="rental_control_slot_10_clear_failure",
        )

    async def test_clear_code_failure_escalates(self) -> None:
        """Verify clear_code failure creates notification on escalation."""
        coordinator = MagicMock()
        coordinator.name = "Test Rental"
        coordinator.lockname = "front_door"
        coordinator.event_overrides.verify_slot_ownership.return_value = True
        coordinator.event_overrides.record_retry_failure.return_value = True
        coordinator.event_overrides._escalated = {}

        error = Exception("lock offline")
        coordinator.hass.services.async_call = AsyncMock(side_effect=error)

        with (
            patch(
                "custom_components.rental_control.util.pn_create",
            ) as mock_create,
            pytest.raises(Exception, match="lock offline"),
        ):
            await async_fire_clear_code(coordinator, 10, expected_name="Guest")

        coordinator.event_overrides.record_retry_failure.assert_called_once_with(10)
        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args
        assert (
            call_kwargs[1]["notification_id"] == "rental_control_slot_10_clear_failure"
        )

    async def test_clear_code_failure_reraises(self) -> None:
        """Verify exception is re-raised after recording failure."""
        coordinator = MagicMock()
        coordinator.name = "Test Rental"
        coordinator.lockname = "front_door"
        coordinator.event_overrides.verify_slot_ownership.return_value = True
        coordinator.event_overrides.record_retry_failure.return_value = False

        error = RuntimeError("hardware fault")
        coordinator.hass.services.async_call = AsyncMock(side_effect=error)

        with pytest.raises(RuntimeError, match="hardware fault"):
            await async_fire_clear_code(coordinator, 10, expected_name="Guest")


# ---------------------------------------------------------------------------
# Slugified lockname entity ID construction tests
# ---------------------------------------------------------------------------


class TestSlugifiedLocknameEntityIds:
    """Verify entity IDs use slugified locknames throughout util."""

    async def test_set_code_constructs_slugified_entity_ids(
        self,
    ) -> None:
        """Verify async_fire_set_code builds correct entity IDs.

        When the coordinator lockname has already been slugified from
        a friendly name like 'Front Door' to 'front_door', all entity
        IDs in service calls must use the slugified form.
        """
        coordinator = MagicMock()
        coordinator.lockname = "front_door"
        coordinator.event_prefix = ""
        coordinator.hass.services.async_call = AsyncMock()

        event = MagicMock()
        event.extra_state_attributes = {
            "slot_name": "Guest",
            "slot_code": "1234",
            "start": "2025-01-15T16:00:00",
            "end": "2025-01-17T11:00:00",
        }

        await async_fire_set_code(coordinator, event, 10)

        calls = coordinator.hass.services.async_call.await_args_list
        entity_ids = []
        for call in calls:
            target = call.kwargs.get("target", {})
            eid = target.get("entity_id", "")
            if eid:
                entity_ids.append(eid)

        assert entity_ids, "Expected at least one service call with entity_id"
        for eid in entity_ids:
            assert "front_door" in eid
            assert " " not in eid

    async def test_clear_code_constructs_slugified_entity_id(
        self,
    ) -> None:
        """Verify async_fire_clear_code uses slugified lockname."""
        coordinator = MagicMock()
        coordinator.name = "Test Rental"
        coordinator.lockname = "front_door"
        coordinator.hass.services.async_call = AsyncMock()

        await async_fire_clear_code(coordinator, 10)

        call = coordinator.hass.services.async_call.await_args
        entity_id = call.kwargs["target"]["entity_id"]
        assert entity_id == "button.front_door_code_slot_10_reset"
        assert " " not in entity_id

    async def test_state_change_matches_slugified_entity_ids(
        self,
    ) -> None:
        """Verify handle_state_change finds states with slugified IDs.

        Simulates a lock originally named 'Front Door' that has been
        slugified to 'front_door' by the coordinator. The entity IDs
        in Home Assistant use the slugified form, and the state change
        handler must look up those same IDs.
        """
        lockname = "front_door"
        slot_num = 10

        mock_slot_code = MagicMock()
        mock_slot_code.state = "5678"
        mock_slot_name = MagicMock()
        mock_slot_name.state = "Visitor"
        mock_slot_enabled = MagicMock()
        mock_slot_enabled.state = "on"

        mock_coordinator = MagicMock()
        mock_coordinator.lockname = lockname
        mock_coordinator.event_overrides = MagicMock()
        mock_coordinator.event_overrides.async_check_overrides = AsyncMock()
        mock_coordinator.update_event_overrides = AsyncMock()

        def states_get(entity_id: str) -> MagicMock | None:
            """Return mock states keyed by slugified entity IDs."""
            assert " " not in entity_id, f"Entity ID contains spaces: {entity_id}"
            if "enabled" in entity_id:
                return mock_slot_enabled
            if "pin" in entity_id:
                return mock_slot_code
            if "name" in entity_id:
                return mock_slot_name
            if "use_date_range" in entity_id:
                return None
            return None

        hass = MagicMock()
        hass.data = {
            DOMAIN: {
                "entry_id": {COORDINATOR: mock_coordinator},
            }
        }
        hass.states.get = states_get

        config_entry = MagicMock()
        config_entry.entry_id = "entry_id"

        event = MagicMock(spec=Event)
        event.data = {"entity_id": (f"switch.{lockname}_code_slot_{slot_num}_enabled")}

        with patch(
            "custom_components.rental_control.util.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            await handle_state_change(hass, config_entry, event)

        mock_coordinator.update_event_overrides.assert_awaited_once()
        call_args = mock_coordinator.update_event_overrides.call_args[0]
        assert call_args[1] == "5678"
        assert call_args[2] == "Visitor"

    async def test_state_change_returns_early_when_no_lockname(
        self,
    ) -> None:
        """Verify handle_state_change exits early when lockname is None."""
        mock_coordinator = MagicMock()
        mock_coordinator.lockname = None
        mock_coordinator.event_overrides = MagicMock()

        hass = MagicMock()
        hass.data = {DOMAIN: {"entry_id": {COORDINATOR: mock_coordinator}}}

        config_entry = MagicMock()
        config_entry.entry_id = "entry_id"

        event = MagicMock(spec=Event)
        event.data = {"entity_id": "switch.test_code_slot_1_enabled"}

        await handle_state_change(hass, config_entry, event)

        mock_coordinator.event_overrides.async_update.assert_not_called()

    async def test_state_change_returns_early_when_no_event_overrides(
        self,
    ) -> None:
        """Verify handle_state_change exits early when event_overrides is None."""
        mock_coordinator = MagicMock()
        mock_coordinator.lockname = "front_door"
        mock_coordinator.event_overrides = None

        hass = MagicMock()
        hass.data = {DOMAIN: {"entry_id": {COORDINATOR: mock_coordinator}}}

        config_entry = MagicMock()
        config_entry.entry_id = "entry_id"

        event = MagicMock(spec=Event)
        event.data = {"entity_id": "switch.front_door_code_slot_1_enabled"}

        await handle_state_change(hass, config_entry, event)


# ---------------------------------------------------------------------------
# compute_early_expiry_time tests (T031)
# ---------------------------------------------------------------------------


class TestComputeEarlyExpiryTime:
    """Tests for the compute_early_expiry_time helper (T031)."""

    def test_returns_now_plus_grace_when_more_than_grace_remain(self) -> None:
        """Verify returns now + 15min when more than 15min remain."""
        now = dt_util.now()
        original_end = now + timedelta(hours=2)  # 120 min remain
        result = compute_early_expiry_time(now, original_end)
        expected = now + timedelta(minutes=15)
        assert result == expected

    def test_returns_original_end_when_less_than_grace_remain(self) -> None:
        """Verify returns original_end when less than 15min remain."""
        now = dt_util.now()
        original_end = now + timedelta(minutes=10)  # 10 min remain
        result = compute_early_expiry_time(now, original_end)
        assert result == original_end

    def test_returns_original_end_when_exactly_grace_remain(self) -> None:
        """Verify returns original_end when exactly 15min remain."""
        now = dt_util.now()
        original_end = now + timedelta(minutes=15)  # exactly 15 min
        result = compute_early_expiry_time(now, original_end)
        assert result == original_end

    def test_custom_grace_minutes(self) -> None:
        """Verify custom grace_minutes parameter is respected."""
        now = dt_util.now()
        original_end = now + timedelta(hours=2)
        result = compute_early_expiry_time(now, original_end, grace_minutes=30)
        expected = now + timedelta(minutes=30)
        assert result == expected

    def test_custom_grace_when_less_than_custom_remain(self) -> None:
        """Verify original_end returned when less than custom grace remain."""
        now = dt_util.now()
        original_end = now + timedelta(minutes=20)
        result = compute_early_expiry_time(now, original_end, grace_minutes=30)
        assert result == original_end
