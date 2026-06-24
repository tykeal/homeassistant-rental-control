# SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the Rental Control util module."""

from __future__ import annotations

import asyncio
from datetime import date
from datetime import datetime
from datetime import timedelta
from importlib import import_module
import logging
from typing import TYPE_CHECKING
from typing import cast
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

from homeassistant.components.calendar import CalendarEvent
from homeassistant.const import CONF_NAME
from homeassistant.core import Event
from homeassistant.exceptions import ServiceNotFound
from homeassistant.util import dt as dt_util
import pytest

from custom_components.rental_control import util as util_module
from custom_components.rental_control.const import COORDINATOR
from custom_components.rental_control.const import DEFAULT_PATH
from custom_components.rental_control.const import DOMAIN
from custom_components.rental_control.const import NAME
from custom_components.rental_control.util import EventIdentity
from custom_components.rental_control.util import OperationResult
from custom_components.rental_control.util import add_call
from custom_components.rental_control.util import apply_buffer
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
from custom_components.rental_control.util import is_cleared_keymaster_text_state
from custom_components.rental_control.util import normalize_uid
from custom_components.rental_control.util import trim_name

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant


def test_keymaster_none_text_token_is_cleared() -> None:
    """The literal None text token is treated as a cleared slot state."""
    assert is_cleared_keymaster_text_state("None")
    assert is_cleared_keymaster_text_state(" none ")


# ---------------------------------------------------------------------------
# get_entry_data tests
# ---------------------------------------------------------------------------


def _get_entry_data_helper() -> Callable[
    [HomeAssistant, str], dict[str, object] | None
]:
    """Return the entry data helper under test."""
    helper = import_module("custom_components.rental_control.util").get_entry_data
    return cast("Callable[[HomeAssistant, str], dict[str, object] | None]", helper)


class TestGetEntryData:
    """Tests for the shared entry-data lookup helper."""

    def test_present_entry_data_returns_existing_dict(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Verify present entry data is returned by identity."""
        entry_data: dict[str, object] = {COORDINATOR: object()}
        hass.data[DOMAIN] = {"entry-id": entry_data}

        assert _get_entry_data_helper()(hass, "entry-id") is entry_data

    def test_missing_domain_data_returns_none(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Verify a missing domain bucket returns None."""
        hass.data.pop(DOMAIN, None)

        assert _get_entry_data_helper()(hass, "entry-id") is None
        assert DOMAIN not in hass.data

    def test_missing_entry_data_returns_none(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Verify a missing entry inside domain data returns None."""
        domain_data: dict[str, dict[str, object]] = {}
        hass.data[DOMAIN] = domain_data

        assert _get_entry_data_helper()(hass, "entry-id") is None
        assert hass.data[DOMAIN] is domain_data

    def test_missing_entry_does_not_create_throwaway_state(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Verify missing lookups do not create phantom entry state."""
        domain_data: dict[str, dict[str, object]] = {}
        hass.data[DOMAIN] = domain_data

        assert _get_entry_data_helper()(hass, "missing-entry") is None

        assert hass.data[DOMAIN] is domain_data
        assert "missing-entry" not in domain_data


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

    def test_bare_date_normalised_to_datetime(self) -> None:
        """Verify bare date values become aware datetime at midnight."""
        from datetime import datetime as dt_class

        rc = MagicMock()
        rc.event_prefix = None
        event = CalendarEvent(
            summary="AllDay",
            start=date(2025, 6, 1),
            end=date(2025, 6, 5),
        )
        rc.data = [event]

        result = get_event_identities(rc)
        assert len(result) == 1
        assert isinstance(result[0].start, dt_class)
        assert isinstance(result[0].end, dt_class)
        assert result[0].start.hour == 0
        assert result[0].start.minute == 0
        assert result[0].start.tzinfo is not None

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
        mock_coordinator.trim_names = False
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
        mock_coordinator.trim_names = False
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

    async def test_unavailable_slot_name_not_assumed_empty(self) -> None:
        """Verify unavailable slot_name state is not assumed empty.

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
        mock_coordinator.trim_names = False
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
        mock_coordinator.update_event_overrides.assert_not_awaited()

    async def test_real_pin_without_name_not_marked_free(self) -> None:
        """Verify a real PIN with cleared name does not clear the override."""
        lockname = "test_lock"
        slot_num = 10
        mock_slot_code_state = MagicMock()
        mock_slot_code_state.state = "9876"
        mock_slot_name_state = MagicMock()
        mock_slot_name_state.state = "unknown"
        mock_slot_enabled = MagicMock()
        mock_slot_enabled.state = "on"

        mock_coordinator = MagicMock()
        mock_coordinator.lockname = lockname
        mock_coordinator.trim_names = False
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

        mock_coordinator.update_event_overrides.assert_not_awaited()

    async def test_trim_preserve_guest_starting_with_prefix(self) -> None:
        """Verify no double prefix stripping when guest name starts with prefix.

        Bug scenario: prefix="Rental", guest="Rental Guest".
        Keymaster shows "Rental Renta" (trimmed).  handle_state_change
        restores full name as "Rental Rental Guest" before passing to
        update_event_overrides which strips the prefix once → stored as
        "Rental Guest".  Without the fix the guest name would be
        double-stripped to just "Guest".
        """
        lockname = "test_lock"
        slot_num = 10
        prefix = "Rental"
        # guest_max = 12 - 7 = 5  →  trim_name("Rental Guest", 5) = "Renta"
        # Keymaster shows "Rental Renta"
        mock_slot_code_state = MagicMock()
        mock_slot_code_state.state = "1234"
        mock_slot_name_state = MagicMock()
        mock_slot_name_state.state = "Rental Renta"
        mock_slot_enabled = MagicMock()
        mock_slot_enabled.state = "on"

        mock_overrides = MagicMock()
        mock_overrides.overrides = {
            slot_num: {
                "slot_name": "Rental Guest",
                "slot_code": "1234",
                "start_time": dt_util.now(),
                "end_time": dt_util.now() + timedelta(days=1),
            }
        }
        mock_overrides.async_check_overrides = AsyncMock()

        mock_coordinator = MagicMock()
        mock_coordinator.lockname = lockname
        mock_coordinator.event_prefix = prefix
        mock_coordinator.trim_names = True
        mock_coordinator.max_name_length = 12
        mock_coordinator.event_overrides = mock_overrides
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

        mock_coordinator.update_event_overrides.assert_awaited_once()
        call_args = mock_coordinator.update_event_overrides.call_args
        # The name passed should be "Rental Rental Guest" so that
        # _strip_prefix("Rental Rental Guest", "Rental") → "Rental Guest"
        assert call_args[0][2] == "Rental Rental Guest"


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
        mock_coordinator.trim_names = False
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
        mock_coordinator.trim_names = False
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
        mock_coordinator.trim_names = False
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
        coordinator.hass.states.get.return_value = None

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
# async_fire_clear_code post-clear verification tests
# ---------------------------------------------------------------------------


class TestClearCodePostClearVerification:
    """Tests for post-clear name verification in async_fire_clear_code."""

    async def test_clear_code_verifies_name_cleared(self) -> None:
        """Verify text.set_value is NOT called when name is empty."""
        coordinator = MagicMock()
        coordinator.name = "Test Rental"
        coordinator.lockname = "front_door"
        coordinator.hass.services.async_call = AsyncMock()

        name_state = MagicMock()
        name_state.state = ""
        coordinator.hass.states.get.return_value = name_state

        await async_fire_clear_code(coordinator, 10)

        # Only the button.press call should have been made
        coordinator.hass.services.async_call.assert_awaited_once_with(
            domain="button",
            service="press",
            target={
                "entity_id": "button.front_door_code_slot_10_reset",
            },
            blocking=True,
        )

    async def test_clear_code_forces_name_clear_on_persist(
        self,
    ) -> None:
        """Verify text.set_value is called when name persists."""
        coordinator = MagicMock()
        coordinator.name = "Test Rental"
        coordinator.lockname = "front_door"
        coordinator.hass.services.async_call = AsyncMock()

        name_state = MagicMock()
        name_state.state = "Ghost"
        coordinator.hass.states.get.return_value = name_state

        await async_fire_clear_code(coordinator, 10)

        calls = coordinator.hass.services.async_call.await_args_list
        assert len(calls) == 2
        set_call = calls[1]
        assert set_call.kwargs["domain"] == "text"
        assert set_call.kwargs["service"] == "set_value"
        assert set_call.kwargs["target"] == {
            "entity_id": "text.front_door_code_slot_10_name",
        }
        assert set_call.kwargs["service_data"] == {"value": ""}

    async def test_clear_code_handles_force_clear_failure(
        self,
    ) -> None:
        """Verify force-clear exception is logged, not propagated."""
        coordinator = MagicMock()
        coordinator.name = "Test Rental"
        coordinator.lockname = "front_door"

        name_state = MagicMock()
        name_state.state = "Ghost"
        coordinator.hass.states.get.return_value = name_state

        # First call (button.press) succeeds; second (set_value) fails
        coordinator.hass.services.async_call = AsyncMock(
            side_effect=[None, RuntimeError("set_value failed")],
        )

        # Should not raise
        await async_fire_clear_code(coordinator, 10)

        assert coordinator.hass.services.async_call.await_count == 2

    async def test_clear_code_skips_unknown_name_state(
        self,
    ) -> None:
        """Verify no force-clear when name state is 'unknown'."""
        coordinator = MagicMock()
        coordinator.name = "Test Rental"
        coordinator.lockname = "front_door"
        coordinator.hass.services.async_call = AsyncMock()

        name_state = MagicMock()
        name_state.state = "unknown"
        coordinator.hass.states.get.return_value = name_state

        result = await async_fire_clear_code(coordinator, 10)

        coordinator.hass.services.async_call.assert_awaited_once()
        assert result.confirmed is True

    async def test_clear_code_skips_unavailable_name_state(
        self,
    ) -> None:
        """Verify unavailable name state leaves the clear unconfirmed."""
        coordinator = MagicMock()
        coordinator.name = "Test Rental"
        coordinator.lockname = "front_door"
        coordinator.hass.services.async_call = AsyncMock()

        name_state = MagicMock()
        name_state.state = "unavailable"
        coordinator.hass.states.get.return_value = name_state

        result = await async_fire_clear_code(coordinator, 10)

        coordinator.hass.services.async_call.assert_awaited_once()
        assert result.unconfirmed is True

    async def test_clear_code_keeps_real_pin_unconfirmed(
        self,
    ) -> None:
        """Verify a real PIN prevents clear confirmation with unknown name."""
        coordinator = MagicMock()
        coordinator.name = "Test Rental"
        coordinator.lockname = "front_door"
        coordinator.hass.services.async_call = AsyncMock()

        name_state = MagicMock()
        name_state.state = "unknown"
        pin_state = MagicMock()
        pin_state.state = "9876"

        def states_get(entity_id: str) -> MagicMock:
            """Return name or PIN state for the clear confirmation."""
            return pin_state if entity_id.endswith("_pin") else name_state

        coordinator.hass.states.get.side_effect = states_get

        result = await async_fire_clear_code(coordinator, 10)

        coordinator.hass.services.async_call.assert_awaited_once()
        assert result.unconfirmed is True
        assert result.lingering_pin is True


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
        coordinator.trim_names = False
        coordinator.code_buffer_before = 0
        coordinator.code_buffer_after = 0
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
        coordinator.trim_names = False
        coordinator.code_buffer_before = 0
        coordinator.code_buffer_after = 0
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
        coordinator.trim_names = False
        coordinator.code_buffer_before = 0
        coordinator.code_buffer_after = 0
        coordinator.hass.services.async_call = AsyncMock()

        event = self._make_event(slot_name="Guest")
        await async_fire_set_code(coordinator, event, 10)

        calls = coordinator.hass.services.async_call.await_args_list
        name_calls = [
            c for c in calls if c.kwargs.get("service_data", {}).get("value") == "Guest"
        ]
        assert len(name_calls) == 1

    async def test_trim_names_trims_slot_name(self) -> None:
        """Verify slot name is trimmed when trim_names is enabled."""
        coordinator = MagicMock()
        coordinator.lockname = "front_door"
        coordinator.event_prefix = "Rental"
        coordinator.trim_names = True
        coordinator.max_name_length = 12
        coordinator.code_buffer_before = 0
        coordinator.code_buffer_after = 0
        coordinator.hass.services.async_call = AsyncMock()

        event = self._make_event(slot_name="Very Long Guest Name")
        await async_fire_set_code(coordinator, event, 10)

        # "Rental Very Long Guest Name" trimmed to 12 → "Rental Very"
        calls = coordinator.hass.services.async_call.await_args_list
        name_calls = [
            c
            for c in calls
            if c.kwargs.get("service_data", {}).get("value") == "Rental Very"
        ]
        assert len(name_calls) == 1

    async def test_trim_names_disabled_no_trim(self) -> None:
        """Verify slot name is not trimmed when trim_names is disabled."""
        coordinator = MagicMock()
        coordinator.lockname = "front_door"
        coordinator.event_prefix = "Rental"
        coordinator.trim_names = False
        coordinator.code_buffer_before = 0
        coordinator.code_buffer_after = 0
        coordinator.hass.services.async_call = AsyncMock()

        event = self._make_event(slot_name="Very Long Guest Name")
        await async_fire_set_code(coordinator, event, 10)

        calls = coordinator.hass.services.async_call.await_args_list
        name_calls = [
            c
            for c in calls
            if c.kwargs.get("service_data", {}).get("value")
            == "Rental Very Long Guest Name"
        ]
        assert len(name_calls) == 1

    async def test_gather_exception_returns_failed_result_for_retry(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Verify a failing gather call is logged and returned as failed."""
        coordinator = MagicMock()
        coordinator.lockname = "front_door"
        coordinator.event_prefix = ""
        coordinator.trim_names = False
        coordinator.code_buffer_before = 0
        coordinator.code_buffer_after = 0
        coordinator.event_overrides.verify_slot_ownership.return_value = True
        coordinator.event_overrides.record_retry_failure.return_value = False

        error = ServiceNotFound("switch", "turn_off")
        coordinator.hass.services.async_call = AsyncMock(side_effect=error)

        event = self._make_event()
        with caplog.at_level(
            logging.ERROR, logger="custom_components.rental_control.util"
        ):
            result = await async_fire_set_code(coordinator, event, 10)

        assert "Lock slot operation" in caplog.text
        assert result.failed is True
        assert result.error is not None
        coordinator.event_overrides.record_retry_failure.assert_called_once_with(10)

    async def test_set_code_confirms_without_blocking_whole_loop(self) -> None:
        """Verify set_code confirms without flushing every pending HA job."""
        coordinator = MagicMock()
        coordinator.lockname = "front_door"
        coordinator.event_prefix = ""
        coordinator.trim_names = False
        coordinator.code_buffer_before = 0
        coordinator.code_buffer_after = 0
        coordinator.event_overrides.verify_slot_ownership.return_value = True
        coordinator.event_overrides._escalated = {}
        coordinator.event_overrides.record_retry_success.return_value = None
        coordinator.hass.services.async_call = AsyncMock()
        coordinator.hass.async_block_till_done = AsyncMock(
            side_effect=AssertionError("whole-loop flush must not be used")
        )
        coordinator.hass.states.get.return_value = MagicMock(state="Guest")

        result = await async_fire_set_code(coordinator, self._make_event(), 10)

        coordinator.hass.async_block_till_done.assert_not_called()
        assert result.confirmed is True

    async def test_set_code_unconfirmed_on_confirmation_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify confirmation timeout leaves a set unconfirmed, not failed."""
        coordinator = MagicMock()
        coordinator.lockname = "front_door"
        coordinator.event_prefix = ""
        coordinator.trim_names = False
        coordinator.code_buffer_before = 0
        coordinator.code_buffer_after = 0
        coordinator.event_overrides.verify_slot_ownership.return_value = True
        coordinator.event_overrides.record_retry_failure.return_value = False
        coordinator.hass.services.async_call = AsyncMock()
        coordinator.hass.states.get.return_value = MagicMock(state="Old Guest")
        monkeypatch.setattr(util_module, "_SET_CODE_CONFIRMATION_TIMEOUT", 0.01)
        unsub = MagicMock()
        monkeypatch.setattr(
            util_module,
            "async_track_state_change_event",
            MagicMock(return_value=unsub),
        )

        result = await async_fire_set_code(coordinator, self._make_event(), 10)

        assert result == OperationResult(kind="set", slot=10, unconfirmed=True)
        coordinator.event_overrides.record_retry_failure.assert_not_called()
        unsub.assert_called_once_with()

    async def test_set_code_confirmed_when_name_updates_after_short_delay(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify a matching name update confirms through a targeted wait."""
        coordinator = MagicMock()
        coordinator.lockname = "front_door"
        coordinator.event_prefix = ""
        coordinator.trim_names = False
        coordinator.code_buffer_before = 0
        coordinator.code_buffer_after = 0
        coordinator.event_overrides.verify_slot_ownership.return_value = True
        coordinator.event_overrides._escalated = {}
        coordinator.event_overrides.record_retry_success.return_value = None
        coordinator.hass.services.async_call = AsyncMock()
        coordinator.hass.states.get.return_value = MagicMock(state="Old Guest")
        monkeypatch.setattr(util_module, "_SET_CODE_CONFIRMATION_TIMEOUT", 0.5)
        callbacks = []
        unsub = MagicMock()

        def track_state_change(hass, entity_ids, action):
            """Capture the targeted listener registered by the wait helper."""
            callbacks.append((entity_ids, action))
            return unsub

        monkeypatch.setattr(
            util_module,
            "async_track_state_change_event",
            track_state_change,
        )

        async def update_name() -> None:
            """Fire the captured listener after the wait helper is armed."""
            await asyncio.sleep(0.01)
            _entity_ids, action = callbacks[0]
            coordinator.hass.states.get.return_value = MagicMock(state="Guest")
            event = MagicMock()
            event.data = {"new_state": MagicMock(state="Guest")}
            action(event)

        update_task = asyncio.create_task(update_name())
        result = await async_fire_set_code(coordinator, self._make_event(), 10)
        await update_task

        assert result == OperationResult(kind="set", slot=10, confirmed=True)
        assert callbacks[0][0] == ["text.front_door_code_slot_10_name"]
        unsub.assert_called_once_with()

    async def test_set_code_unconfirmed_when_matching_event_is_stale(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify confirmation re-checks state after a matching event."""
        coordinator = MagicMock()
        coordinator.lockname = "front_door"
        coordinator.event_prefix = ""
        coordinator.trim_names = False
        coordinator.code_buffer_before = 0
        coordinator.code_buffer_after = 0
        coordinator.event_overrides.verify_slot_ownership.return_value = True
        coordinator.event_overrides.record_retry_failure.return_value = False
        coordinator.hass.services.async_call = AsyncMock()
        coordinator.hass.states.get.return_value = MagicMock(state="Old Guest")
        monkeypatch.setattr(util_module, "_SET_CODE_CONFIRMATION_TIMEOUT", 0.5)
        callbacks = []

        def track_state_change(hass, entity_ids, action):
            """Capture the targeted listener registered by the wait helper."""
            callbacks.append(action)
            return MagicMock()

        monkeypatch.setattr(
            util_module,
            "async_track_state_change_event",
            track_state_change,
        )

        async def update_name() -> None:
            """Fire a stale matching event while current state is mismatched."""
            await asyncio.sleep(0.01)
            coordinator.hass.states.get.return_value = MagicMock(state="Other")
            event = MagicMock()
            event.data = {"new_state": MagicMock(state="Guest")}
            callbacks[0](event)

        update_task = asyncio.create_task(update_name())
        result = await async_fire_set_code(coordinator, self._make_event(), 10)
        await update_task

        assert result == OperationResult(kind="set", slot=10, unconfirmed=True)
        coordinator.event_overrides.record_retry_failure.assert_not_called()

    async def test_set_code_cancelled_error_propagates(self) -> None:
        """Verify set_code does not swallow task cancellation."""
        coordinator = MagicMock()
        coordinator.lockname = "front_door"
        coordinator.event_prefix = ""
        coordinator.trim_names = False
        coordinator.event_overrides.verify_slot_ownership.return_value = True
        coordinator.hass.services.async_call = AsyncMock(
            side_effect=asyncio.CancelledError()
        )

        with pytest.raises(asyncio.CancelledError):
            await async_fire_set_code(coordinator, self._make_event(), 10)


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
        coordinator.code_buffer_before = 0
        coordinator.code_buffer_after = 0
        coordinator.hass.services.async_call = AsyncMock()

        event = self._make_event()
        await async_fire_update_times(coordinator, event, 10)

        calls = coordinator.hass.services.async_call.await_args_list
        assert len(calls) == 2

        targets = {c.kwargs["target"]["entity_id"] for c in calls}
        assert "datetime.front_door_code_slot_10_date_range_end" in targets
        assert "datetime.front_door_code_slot_10_date_range_start" in targets

    async def test_no_lockname_returns_early(self) -> None:
        """Verify no service calls when lockname is empty."""
        coordinator = MagicMock()
        coordinator.lockname = ""
        coordinator.hass.services.async_call = AsyncMock()

        await async_fire_update_times(coordinator, self._make_event(), 10)

        coordinator.hass.services.async_call.assert_not_awaited()

    async def test_no_slot_found_returns_early(self) -> None:
        """Verify no service calls when slot is zero/falsy."""
        coordinator = MagicMock()
        coordinator.lockname = "front_door"
        coordinator.hass.services.async_call = AsyncMock()

        await async_fire_update_times(coordinator, self._make_event(), 0)

        coordinator.hass.services.async_call.assert_not_awaited()

    async def test_gather_exception_logged_not_raised(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Verify a failing service call is logged but does not crash."""
        coordinator = MagicMock()
        coordinator.lockname = "front_door"
        coordinator.code_buffer_before = 0
        coordinator.code_buffer_after = 0
        coordinator.hass.services.async_call = AsyncMock(
            side_effect=ServiceNotFound("datetime", "set_value")
        )

        with caplog.at_level(
            logging.ERROR, logger="custom_components.rental_control.util"
        ):
            await async_fire_update_times(coordinator, self._make_event(), 10)

        assert "Lock slot operation" in caplog.text

    async def test_cancelled_error_propagates(self) -> None:
        """Verify update_times does not swallow task cancellation."""
        coordinator = MagicMock()
        coordinator.lockname = "front_door"
        coordinator.code_buffer_before = 0
        coordinator.code_buffer_after = 0
        coordinator.hass.services.async_call = AsyncMock(
            side_effect=asyncio.CancelledError()
        )

        with pytest.raises(asyncio.CancelledError):
            await async_fire_update_times(coordinator, self._make_event(), 10)


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
        coordinator.trim_names = False
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
            await async_fire_update_times(coordinator, event, 10)

        coordinator.hass.services.async_call.assert_not_awaited()
        assert "ownership" in caplog.text.lower()

    async def test_set_code_proceeds_when_ownership_matches(self) -> None:
        """Verify async_fire_set_code executes when ownership passes."""
        coordinator = MagicMock()
        coordinator.lockname = "front_door"
        coordinator.event_prefix = ""
        coordinator.trim_names = False
        coordinator.code_buffer_before = 0
        coordinator.code_buffer_after = 0
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
        coordinator.trim_names = False
        coordinator.code_buffer_before = 0
        coordinator.code_buffer_after = 0
        coordinator.hass.services.async_call = AsyncMock()
        coordinator.hass.states.get.return_value = MagicMock(state="Guest")
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
        coordinator.trim_names = False
        coordinator.code_buffer_before = 0
        coordinator.code_buffer_after = 0
        coordinator.hass.services.async_call = AsyncMock()
        coordinator.hass.states.get.return_value = MagicMock(state="Guest")
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
        coordinator.trim_names = False
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

        with patch(
            "custom_components.rental_control.util.pn_create",
        ) as mock_create:
            result = await async_fire_set_code(coordinator, event, 10)

        coordinator.event_overrides.record_retry_failure.assert_called_once_with(10)
        assert result.failed is True
        assert result.error is not None
        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args
        assert call_kwargs[1]["notification_id"] == "rental_control_slot_10_failure"

    async def test_set_code_failure_no_notification_below_threshold(self) -> None:
        """Verify no notification when below escalation threshold."""
        coordinator = MagicMock()
        coordinator.lockname = "front_door"
        coordinator.event_prefix = ""
        coordinator.trim_names = False
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

        with patch(
            "custom_components.rental_control.util.pn_create",
        ) as mock_create:
            result = await async_fire_set_code(coordinator, event, 10)

        coordinator.event_overrides.record_retry_failure.assert_called_once_with(10)
        assert result.failed is True
        assert result.error is not None
        mock_create.assert_not_called()

    async def test_clear_code_records_success_and_dismisses(self) -> None:
        """Verify clear_code success resets retry and dismisses notification."""
        coordinator = MagicMock()
        coordinator.name = "Test Rental"
        coordinator.lockname = "front_door"
        coordinator.hass.services.async_call = AsyncMock()
        coordinator.hass.states.get.return_value = MagicMock(state="")
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

    async def test_set_code_unconfirmed_does_not_reset_retry(self) -> None:
        """Verify unconfirmed set_code leaves retry tracking intact."""
        coordinator = MagicMock()
        coordinator.lockname = "front_door"
        coordinator.event_prefix = ""
        coordinator.trim_names = False
        coordinator.code_buffer_before = 0
        coordinator.code_buffer_after = 0
        coordinator.hass.services.async_call = AsyncMock()
        coordinator.hass.states.get.return_value = MagicMock(state="Other")
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
            result = await async_fire_set_code(coordinator, event, 10)

        assert result.unconfirmed is True
        coordinator.event_overrides.record_retry_success.assert_not_called()
        mock_dismiss.assert_not_called()

    async def test_clear_code_unconfirmed_does_not_reset_retry(self) -> None:
        """Verify unconfirmed clear_code leaves retry tracking intact."""
        coordinator = MagicMock()
        coordinator.name = "Test Rental"
        coordinator.lockname = "front_door"
        coordinator.hass.services.async_call = AsyncMock()
        coordinator.hass.states.get.return_value = None
        coordinator.event_overrides.verify_slot_ownership.return_value = True
        coordinator.event_overrides._escalated = {10: True}

        with patch(
            "custom_components.rental_control.util.pn_dismiss",
        ) as mock_dismiss:
            result = await async_fire_clear_code(coordinator, 10, expected_name="Guest")

        assert result.unconfirmed is True
        coordinator.event_overrides.record_retry_success.assert_not_called()
        mock_dismiss.assert_not_called()

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

        with patch(
            "custom_components.rental_control.util.pn_create",
        ) as mock_create:
            result = await async_fire_clear_code(coordinator, 10, expected_name="Guest")

        coordinator.event_overrides.record_retry_failure.assert_called_once_with(10)
        assert result.failed is True
        assert result.error is not None
        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args
        assert (
            call_kwargs[1]["notification_id"] == "rental_control_slot_10_clear_failure"
        )

    async def test_clear_code_failure_returns_failed_result(self) -> None:
        """Verify failure is returned after recording retry state."""
        coordinator = MagicMock()
        coordinator.name = "Test Rental"
        coordinator.lockname = "front_door"
        coordinator.event_overrides.verify_slot_ownership.return_value = True
        coordinator.event_overrides.record_retry_failure.return_value = False

        error = RuntimeError("hardware fault")
        coordinator.hass.services.async_call = AsyncMock(side_effect=error)

        result = await async_fire_clear_code(coordinator, 10, expected_name="Guest")
        assert result.failed is True
        assert result.error == "hardware fault"

    async def test_clear_code_cancelled_error_propagates(self) -> None:
        """Verify clear_code does not swallow task cancellation."""
        coordinator = MagicMock()
        coordinator.name = "Test Rental"
        coordinator.lockname = "front_door"
        coordinator.event_overrides.verify_slot_ownership.return_value = True
        coordinator.hass.services.async_call = AsyncMock(
            side_effect=asyncio.CancelledError()
        )

        with pytest.raises(asyncio.CancelledError):
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
        coordinator.trim_names = False
        coordinator.code_buffer_before = 0
        coordinator.code_buffer_after = 0
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
        coordinator.hass.states.get.return_value = None

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
        mock_coordinator.trim_names = False
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
        mock_coordinator.trim_names = False
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


class TestNormalizeUid:
    """Tests for normalize_uid helper."""

    def test_none_returns_none(self) -> None:
        """Verify None input returns None."""
        assert normalize_uid(None) is None

    def test_empty_string_returns_none(self) -> None:
        """Verify empty string returns None."""
        assert normalize_uid("") is None

    def test_whitespace_only_returns_none(self) -> None:
        """Verify whitespace-only string returns None."""
        assert normalize_uid("   ") is None

    def test_strips_whitespace(self) -> None:
        """Verify leading/trailing whitespace is stripped."""
        assert normalize_uid("  abc123  ") == "abc123"

    def test_preserves_normal_uid(self) -> None:
        """Verify normal UID is preserved unchanged."""
        assert normalize_uid("abc123@example.com") == "abc123@example.com"

    def test_strips_newlines(self) -> None:
        """Verify trailing newlines are stripped."""
        assert normalize_uid("abc123\n") == "abc123"


# ---------------------------------------------------------------------------
# trim_name tests
# ---------------------------------------------------------------------------


class TestTrimName:
    """Tests for the trim_name function."""

    def test_short_name_returned_unchanged(self) -> None:
        """Verify a name under the limit is returned as-is."""
        assert trim_name("Rental Chris", 16) == "Rental Chris"

    def test_exact_length_returned_unchanged(self) -> None:
        """Verify a name exactly at the limit is returned as-is."""
        assert trim_name("Hello World12345", 16) == "Hello World12345"

    def test_word_boundary_trim(self) -> None:
        """Verify trimming drops last word that would exceed limit."""
        assert trim_name("Rental Christopher Montgomery", 16) == "Rental"

    def test_word_boundary_trim_longer_limit(self) -> None:
        """Verify trimming at a longer limit keeps more words."""
        assert trim_name("Rental Christopher Montgomery", 28) == "Rental Christopher"

    def test_single_word_exceeding_limit_hard_truncated(self) -> None:
        """Verify a single long word is hard-truncated."""
        assert trim_name("Superlongname", 8) == "Superlon"

    def test_empty_string_returns_empty(self) -> None:
        """Verify empty string returns empty string."""
        assert trim_name("", 16) == ""

    def test_short_string_under_limit(self) -> None:
        """Verify a short string well under the limit passes through."""
        assert trim_name("Hi", 16) == "Hi"

    def test_first_word_longer_than_max(self) -> None:
        """Verify first word longer than max is hard-truncated."""
        assert trim_name("VacationHome Christopher", 12) == "VacationHome"

    def test_whitespace_normalization(self) -> None:
        """Verify leading, trailing, and multiple spaces are normalized."""
        assert trim_name("  spaced  name  ", 16) == "spaced name"

    def test_result_never_exceeds_max_length(self) -> None:
        """Verify postcondition: result length is always <= max_length."""
        names = [
            "Rental Christopher Montgomery",
            "Superlongname",
            "A B C D E F G H I J K",
            "  lots   of   spaces  ",
        ]
        for name in names:
            for max_len in range(4, 30):
                result = trim_name(name, max_len)
                assert len(result) <= max_len

    def test_result_has_no_trailing_whitespace(self) -> None:
        """Verify postcondition: result has no trailing whitespace."""
        result = trim_name("  spaced  name  extra  ", 16)
        assert result == result.rstrip()

    def test_multiple_words_only_first_fits(self) -> None:
        """Verify only first word returned when others exceed limit."""
        assert trim_name("Hello WorldExtra", 8) == "Hello"

    def test_skips_long_middle_word(self) -> None:
        """Verify accumulation stops at first non-fitting word."""
        assert trim_name("Hello LongWord A", 8) == "Hello"


# ---------------------------------------------------------------------------
# Lock code buffer tests (spec 009)
# ---------------------------------------------------------------------------


class TestBufferInSetCode:
    """Tests for buffer offsets in async_fire_set_code."""

    @staticmethod
    def _make_event(
        slot_name: str = "Guest",
        slot_code: str = "1234",
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> MagicMock:
        """Build a mock event with slot attributes."""
        if start is None:
            start = datetime(2025, 1, 15, 16, 0, 0)
        if end is None:
            end = datetime(2025, 1, 17, 11, 0, 0)
        event = MagicMock()
        event.extra_state_attributes = {
            "slot_name": slot_name,
            "slot_code": slot_code,
            "start": start,
            "end": end,
        }
        return event

    async def test_before_buffer_shifts_start(self) -> None:
        """Verify date_range_start is 30 minutes earlier."""
        coordinator = MagicMock()
        coordinator.lockname = "front_door"
        coordinator.event_prefix = ""
        coordinator.trim_names = False
        coordinator.code_buffer_before = 30
        coordinator.code_buffer_after = 0
        coordinator.hass.services.async_call = AsyncMock()
        coordinator.event_overrides.verify_slot_ownership.return_value = True

        event = self._make_event()
        await async_fire_set_code(coordinator, event, 10)

        calls = coordinator.hass.services.async_call.await_args_list
        start_calls = [
            c
            for c in calls
            if "date_range_start" in c.kwargs.get("target", {}).get("entity_id", "")
        ]
        assert len(start_calls) == 1
        sent = start_calls[0].kwargs["service_data"]["datetime"]
        assert sent == datetime(2025, 1, 15, 15, 30, 0, tzinfo=dt_util.UTC)

    async def test_after_buffer_extends_end(self) -> None:
        """Verify date_range_end is 15 minutes later."""
        coordinator = MagicMock()
        coordinator.lockname = "front_door"
        coordinator.event_prefix = ""
        coordinator.trim_names = False
        coordinator.code_buffer_before = 0
        coordinator.code_buffer_after = 15
        coordinator.hass.services.async_call = AsyncMock()
        coordinator.event_overrides.verify_slot_ownership.return_value = True

        event = self._make_event()
        await async_fire_set_code(coordinator, event, 10)

        calls = coordinator.hass.services.async_call.await_args_list
        end_calls = [
            c
            for c in calls
            if "date_range_end" in c.kwargs.get("target", {}).get("entity_id", "")
        ]
        assert len(end_calls) == 1
        sent = end_calls[0].kwargs["service_data"]["datetime"]
        assert sent == datetime(2025, 1, 17, 11, 15, 0, tzinfo=dt_util.UTC)

    async def test_both_buffers_applied(self) -> None:
        """Verify both offsets applied simultaneously."""
        coordinator = MagicMock()
        coordinator.lockname = "front_door"
        coordinator.event_prefix = ""
        coordinator.trim_names = False
        coordinator.code_buffer_before = 60
        coordinator.code_buffer_after = 30
        coordinator.hass.services.async_call = AsyncMock()
        coordinator.event_overrides.verify_slot_ownership.return_value = True

        event = self._make_event()
        await async_fire_set_code(coordinator, event, 10)

        calls = coordinator.hass.services.async_call.await_args_list
        start_calls = [
            c
            for c in calls
            if "date_range_start" in c.kwargs.get("target", {}).get("entity_id", "")
        ]
        end_calls = [
            c
            for c in calls
            if "date_range_end" in c.kwargs.get("target", {}).get("entity_id", "")
        ]
        assert start_calls[0].kwargs["service_data"]["datetime"] == (
            datetime(2025, 1, 15, 15, 0, 0, tzinfo=dt_util.UTC)
        )
        assert end_calls[0].kwargs["service_data"]["datetime"] == (
            datetime(2025, 1, 17, 11, 30, 0, tzinfo=dt_util.UTC)
        )

    async def test_zero_buffer_unchanged(self) -> None:
        """Verify zero buffers send unbuffered times."""
        coordinator = MagicMock()
        coordinator.lockname = "front_door"
        coordinator.event_prefix = ""
        coordinator.trim_names = False
        coordinator.code_buffer_before = 0
        coordinator.code_buffer_after = 0
        coordinator.hass.services.async_call = AsyncMock()
        coordinator.event_overrides.verify_slot_ownership.return_value = True

        start = datetime(2025, 1, 15, 16, 0, 0, tzinfo=dt_util.UTC)
        end = datetime(2025, 1, 17, 11, 0, 0, tzinfo=dt_util.UTC)
        event = self._make_event(start=start, end=end)
        await async_fire_set_code(coordinator, event, 10)

        calls = coordinator.hass.services.async_call.await_args_list
        start_calls = [
            c
            for c in calls
            if "date_range_start" in c.kwargs.get("target", {}).get("entity_id", "")
        ]
        end_calls = [
            c
            for c in calls
            if "date_range_end" in c.kwargs.get("target", {}).get("entity_id", "")
        ]
        assert start_calls[0].kwargs["service_data"]["datetime"] == start
        assert end_calls[0].kwargs["service_data"]["datetime"] == end


class TestBufferInUpdateTimes:
    """Tests for buffer offsets in async_fire_update_times."""

    @staticmethod
    def _make_event(
        slot_name: str = "Guest",
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> MagicMock:
        """Build a mock event with slot attributes."""
        if start is None:
            start = datetime(2025, 1, 15, 16, 0, 0)
        if end is None:
            end = datetime(2025, 1, 17, 11, 0, 0)
        event = MagicMock()
        event.extra_state_attributes = {
            "slot_name": slot_name,
            "start": start,
            "end": end,
        }
        return event

    async def test_before_buffer_shifts_start(self) -> None:
        """Verify date_range_start is 30 minutes earlier."""
        coordinator = MagicMock()
        coordinator.lockname = "front_door"
        coordinator.code_buffer_before = 30
        coordinator.code_buffer_after = 0
        coordinator.hass.services.async_call = AsyncMock()
        coordinator.event_overrides.verify_slot_ownership.return_value = True

        event = self._make_event()
        await async_fire_update_times(coordinator, event, 10)

        calls = coordinator.hass.services.async_call.await_args_list
        start_calls = [
            c
            for c in calls
            if "date_range_start" in c.kwargs.get("target", {}).get("entity_id", "")
        ]
        assert start_calls[0].kwargs["service_data"]["datetime"] == (
            datetime(2025, 1, 15, 15, 30, 0, tzinfo=dt_util.UTC)
        )

    async def test_after_buffer_extends_end(self) -> None:
        """Verify date_range_end is 15 minutes later."""
        coordinator = MagicMock()
        coordinator.lockname = "front_door"
        coordinator.code_buffer_before = 0
        coordinator.code_buffer_after = 15
        coordinator.hass.services.async_call = AsyncMock()
        coordinator.event_overrides.verify_slot_ownership.return_value = True

        event = self._make_event()
        await async_fire_update_times(coordinator, event, 10)

        calls = coordinator.hass.services.async_call.await_args_list
        end_calls = [
            c
            for c in calls
            if "date_range_end" in c.kwargs.get("target", {}).get("entity_id", "")
        ]
        assert end_calls[0].kwargs["service_data"]["datetime"] == (
            datetime(2025, 1, 17, 11, 15, 0, tzinfo=dt_util.UTC)
        )

    async def test_zero_buffer_unchanged(self) -> None:
        """Verify zero buffers produce unbuffered date ranges."""
        coordinator = MagicMock()
        coordinator.lockname = "front_door"
        coordinator.code_buffer_before = 0
        coordinator.code_buffer_after = 0
        coordinator.hass.services.async_call = AsyncMock()
        coordinator.event_overrides.verify_slot_ownership.return_value = True

        start = datetime(2025, 1, 15, 16, 0, 0, tzinfo=dt_util.UTC)
        end = datetime(2025, 1, 17, 11, 0, 0, tzinfo=dt_util.UTC)
        event = self._make_event(start=start, end=end)
        await async_fire_update_times(coordinator, event, 10)

        calls = coordinator.hass.services.async_call.await_args_list
        start_calls = [
            c
            for c in calls
            if "date_range_start" in c.kwargs.get("target", {}).get("entity_id", "")
        ]
        end_calls = [
            c
            for c in calls
            if "date_range_end" in c.kwargs.get("target", {}).get("entity_id", "")
        ]
        assert start_calls[0].kwargs["service_data"]["datetime"] == start
        assert end_calls[0].kwargs["service_data"]["datetime"] == end

    async def test_event_attributes_unmodified(self) -> None:
        """Verify event attributes remain unbuffered after call."""
        coordinator = MagicMock()
        coordinator.lockname = "front_door"
        coordinator.code_buffer_before = 30
        coordinator.code_buffer_after = 15
        coordinator.hass.services.async_call = AsyncMock()
        coordinator.event_overrides.verify_slot_ownership.return_value = True

        original_start = datetime(2025, 1, 15, 16, 0, 0)
        original_end = datetime(2025, 1, 17, 11, 0, 0)
        event = self._make_event(start=original_start, end=original_end)
        await async_fire_update_times(coordinator, event, 10)

        assert event.extra_state_attributes["start"] == original_start
        assert event.extra_state_attributes["end"] == original_end


class TestAsyncFireClearCodeOperationResult:
    """Tests for async_fire_clear_code OperationResult outcomes."""

    def _make_coordinator(self) -> MagicMock:
        """Return a coordinator mock for clear-code tests."""
        coordinator = MagicMock()
        coordinator.name = "Test Rental"
        coordinator.lockname = "front_door"
        coordinator.hass.services.async_call = AsyncMock()
        coordinator.event_overrides.verify_slot_ownership.return_value = True
        coordinator.event_overrides._escalated = {}
        return coordinator

    async def test_confirmed_when_name_and_pin_cleared(self) -> None:
        """Clear is confirmed when both name and PIN entities are cleared."""
        coordinator = self._make_coordinator()

        def states_get(entity_id: str) -> MagicMock:
            """Return a cleared mock state for any requested entity."""
            state = MagicMock()
            state.state = ""
            return state

        coordinator.hass.states.get.side_effect = states_get

        with patch(
            "custom_components.rental_control.util.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            result = await async_fire_clear_code(coordinator, 10, expected_name="Guest")

        assert result == OperationResult(kind="clear", slot=10, confirmed=True)

    async def test_unconfirmed_when_name_state_none(self) -> None:
        """Clear is unconfirmed when the name entity cannot be read."""
        coordinator = self._make_coordinator()
        coordinator.hass.states.get.return_value = None

        with patch(
            "custom_components.rental_control.util.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            result = await async_fire_clear_code(coordinator, 10, expected_name="Guest")

        assert result.unconfirmed is True
        assert result.confirmed is False

    async def test_lingering_name_when_name_persists(self) -> None:
        """Persistent name after reset yields lingering_name."""
        coordinator = self._make_coordinator()

        def _state(value: str) -> MagicMock:
            """Return a mock state with the provided value."""
            state = MagicMock()
            state.state = value
            return state

        name_reads = [_state("Ghost"), _state("Ghost")]
        pin_state = _state("")

        def states_get(entity_id: str) -> MagicMock:
            """Return persistent name reads and a cleared PIN state."""
            if entity_id.endswith("_name"):
                return name_reads.pop(0)
            return pin_state

        coordinator.hass.states.get.side_effect = states_get

        with patch(
            "custom_components.rental_control.util.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            result = await async_fire_clear_code(coordinator, 10, expected_name="Guest")

        assert result.unconfirmed is True
        assert result.lingering_name is True
        assert result.confirmed is False

    async def test_lingering_pin_when_pin_persists(self) -> None:
        """Persistent PIN after reset yields lingering_pin."""
        coordinator = self._make_coordinator()

        def states_get(entity_id: str) -> MagicMock:
            """Return a cleared name state and lingering PIN state."""
            state = MagicMock()
            if entity_id.endswith("_name"):
                state.state = ""
            else:
                state.state = "5678"
            return state

        coordinator.hass.states.get.side_effect = states_get

        with patch(
            "custom_components.rental_control.util.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            result = await async_fire_clear_code(coordinator, 10, expected_name="Guest")

        assert result.unconfirmed is True
        assert result.lingering_pin is True
        assert result.confirmed is False

    async def test_service_failure_returns_failed(self) -> None:
        """Button press failure is returned as failed."""
        coordinator = self._make_coordinator()
        coordinator.hass.services.async_call = AsyncMock(
            side_effect=RuntimeError("lock offline")
        )
        coordinator.event_overrides.record_retry_failure.return_value = False

        result = await async_fire_clear_code(coordinator, 10, expected_name="Guest")

        assert result.failed is True
        assert result.error == "lock offline"

    async def test_no_lockname_returns_unconfirmed(self) -> None:
        """Missing lockname returns an unconfirmed result."""
        coordinator = self._make_coordinator()
        coordinator.lockname = ""

        result = await async_fire_clear_code(coordinator, 10)

        assert result.unconfirmed is True

    async def test_ownership_failure_returns_unconfirmed(self) -> None:
        """Ownership mismatch returns an unconfirmed result."""
        coordinator = self._make_coordinator()
        coordinator.event_overrides.verify_slot_ownership.return_value = False

        result = await async_fire_clear_code(coordinator, 10, expected_name="Guest")

        assert result.unconfirmed is True


class TestAsyncFireSetCodeOperationResult:
    """Tests for async_fire_set_code OperationResult outcomes."""

    @staticmethod
    def _make_event() -> MagicMock:
        """Return a set-code event payload."""
        event = MagicMock()
        event.extra_state_attributes = {
            "slot_name": "Guest",
            "slot_code": "1234",
            "start": "2025-01-15T16:00:00",
            "end": "2025-01-17T11:00:00",
        }
        return event

    def _make_coordinator(self) -> MagicMock:
        """Return a coordinator mock for set-code tests."""
        coordinator = MagicMock()
        coordinator.lockname = "front_door"
        coordinator.event_prefix = ""
        coordinator.trim_names = False
        coordinator.code_buffer_before = 0
        coordinator.code_buffer_after = 0
        coordinator.hass.services.async_call = AsyncMock()
        coordinator.event_overrides.verify_slot_ownership.return_value = True
        coordinator.event_overrides._escalated = {}
        return coordinator

    async def test_confirmed_when_name_matches(self) -> None:
        """Set is confirmed when the written name can be read back."""
        coordinator = self._make_coordinator()
        state = MagicMock()
        state.state = "Guest"
        coordinator.hass.states.get.return_value = state

        result = await async_fire_set_code(coordinator, self._make_event(), 10)

        assert result == OperationResult(kind="set", slot=10, confirmed=True)

    async def test_unconfirmed_when_name_state_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Set is unconfirmed when the name entity is unreadable."""
        coordinator = self._make_coordinator()
        coordinator.hass.states.get.return_value = None
        monkeypatch.setattr(util_module, "_SET_CODE_CONFIRMATION_TIMEOUT", 0.01)

        result = await async_fire_set_code(coordinator, self._make_event(), 10)

        assert result.unconfirmed is True

    async def test_service_failure_returns_failed(self) -> None:
        """Service failure is returned as failed."""
        coordinator = self._make_coordinator()
        coordinator.hass.services.async_call = AsyncMock(
            side_effect=RuntimeError("service unavailable")
        )
        coordinator.event_overrides.record_retry_failure.return_value = False

        result = await async_fire_set_code(coordinator, self._make_event(), 10)

        assert result.failed is True
        assert result.error == "service unavailable"

    async def test_invalid_datetime_payload_returns_failed_set(self) -> None:
        """Invalid set-code dates fail as set operations."""
        coordinator = self._make_coordinator()
        event = self._make_event()
        event.extra_state_attributes["start"] = object()

        result = await async_fire_set_code(coordinator, event, 10)

        assert result.failed is True
        assert result.kind == "set"
        coordinator.hass.services.async_call.assert_not_awaited()

    async def test_no_lockname_returns_unconfirmed(self) -> None:
        """Missing lockname returns an unconfirmed result."""
        coordinator = self._make_coordinator()
        coordinator.lockname = ""

        result = await async_fire_set_code(coordinator, self._make_event(), 10)

        assert result.unconfirmed is True


class TestAsyncFireUpdateTimesOperationResult:
    """Tests for async_fire_update_times OperationResult outcomes."""

    @staticmethod
    def _make_event() -> MagicMock:
        """Return an update-times event payload."""
        event = MagicMock()
        event.extra_state_attributes = {
            "slot_name": "Guest",
            "start": datetime(2025, 1, 15, 16, tzinfo=dt_util.UTC),
            "end": datetime(2025, 1, 17, 11, tzinfo=dt_util.UTC),
        }
        return event

    def _make_coordinator(self) -> MagicMock:
        """Return a coordinator mock for update-times tests."""
        coordinator = MagicMock()
        coordinator.lockname = "front_door"
        coordinator.code_buffer_before = 0
        coordinator.code_buffer_after = 0
        coordinator.hass.services.async_call = AsyncMock()
        coordinator.event_overrides.verify_slot_ownership.return_value = True
        return coordinator

    @staticmethod
    def _confirm_datetime_states(coordinator: MagicMock) -> None:
        """Configure coordinator state reads to confirm updated datetimes."""
        expected = {
            "datetime.front_door_code_slot_10_date_range_start": (
                "2025-01-15T16:00:00+00:00"
            ),
            "datetime.front_door_code_slot_10_date_range_end": (
                "2025-01-17T11:00:00+00:00"
            ),
        }

        def _get_state(entity_id: str) -> MagicMock | None:
            """Return a matching state object for datetime confirmation."""
            value = expected.get(entity_id)
            if value is None:
                return None
            return MagicMock(state=value)

        coordinator.hass.states.get.side_effect = _get_state

    async def test_confirmed_on_success(self) -> None:
        """Successful service calls return confirmed."""
        coordinator = self._make_coordinator()
        self._confirm_datetime_states(coordinator)

        result = await async_fire_update_times(coordinator, self._make_event(), 10)

        assert result == OperationResult(
            kind="update_times",
            slot=10,
            confirmed=True,
        )

    async def test_all_day_dates_are_confirmed_as_datetimes(self) -> None:
        """All-day date values are coerced before update confirmation."""
        coordinator = self._make_coordinator()
        coordinator.timezone = dt_util.UTC
        expected = {
            "datetime.front_door_code_slot_10_date_range_start": (
                "2025-01-15T00:00:00+00:00"
            ),
            "datetime.front_door_code_slot_10_date_range_end": (
                "2025-01-17T00:00:00+00:00"
            ),
        }
        coordinator.hass.states.get.side_effect = lambda entity_id: MagicMock(
            state=expected[entity_id]
        )
        event = MagicMock()
        event.extra_state_attributes = {
            "slot_name": "Guest",
            "start": date(2025, 1, 15),
            "end": date(2025, 1, 17),
        }

        result = await async_fire_update_times(coordinator, event, 10)

        assert result.confirmed is True
        calls = coordinator.hass.services.async_call.await_args_list
        payloads = [call.kwargs["service_data"] for call in calls]
        assert all(isinstance(payload["datetime"], datetime) for payload in payloads)

    async def test_invalid_datetime_payload_fails_safely(self) -> None:
        """Invalid update dates fail instead of using nondeterministic times."""
        coordinator = self._make_coordinator()
        event = MagicMock()
        event.extra_state_attributes = {
            "slot_name": "Guest",
            "start": object(),
            "end": date(2025, 1, 17),
        }

        result = await async_fire_update_times(coordinator, event, 10)

        assert result.failed is True
        coordinator.hass.services.async_call.assert_not_awaited()

    async def test_failed_on_service_exception(self) -> None:
        """Gather failures are returned as failed."""
        coordinator = self._make_coordinator()
        coordinator.hass.services.async_call = AsyncMock(
            side_effect=ServiceNotFound("datetime", "set_value")
        )

        result = await async_fire_update_times(coordinator, self._make_event(), 10)

        assert result.failed is True
        assert result.error is not None

    async def test_no_lockname_returns_unconfirmed(self) -> None:
        """Missing lockname returns an unconfirmed result."""
        coordinator = self._make_coordinator()
        coordinator.lockname = ""

        result = await async_fire_update_times(coordinator, self._make_event(), 10)

        assert result.unconfirmed is True


class TestBufferRegressionSemantics:
    """T102 regression: Lock-code buffer semantics preserved after reconciliation.

    These tests pin the apply_buffer behaviour: the buffered window
    sent to Keymaster differs from the original event window, and the
    original event attributes are never mutated.
    """

    @staticmethod
    def _make_coordinator() -> MagicMock:
        """Return a coordinator mock for buffered set-code tests."""
        from zoneinfo import ZoneInfo

        coordinator = MagicMock()
        coordinator.lockname = "front_door"
        coordinator.timezone = ZoneInfo("America/New_York")
        coordinator.trim_names = False
        coordinator.max_name_length = 0
        coordinator.event_prefix = ""
        coordinator.code_buffer_before = 30
        coordinator.code_buffer_after = 15
        coordinator.hass.services.async_call = AsyncMock()
        state = MagicMock()
        state.state = "Guest"
        coordinator.hass.states.get.return_value = state
        coordinator.event_overrides.verify_slot_ownership.return_value = True
        coordinator.event_overrides._escalated = {}
        return coordinator

    def test_apply_buffer_zero_returns_inputs_unchanged(self) -> None:
        """Zero-minute buffers return the original objects unchanged."""
        rc = MagicMock()
        start = datetime(2025, 1, 15, 16, 0, tzinfo=dt_util.UTC)
        end = datetime(2025, 1, 17, 11, 0, tzinfo=dt_util.UTC)

        buffered_start, buffered_end = apply_buffer(start, end, 0, 0, rc)

        assert buffered_start is start
        assert buffered_end is end

    def test_apply_buffer_before_shifts_start_only(self) -> None:
        """Before-buffer moves only the start backward."""
        rc = MagicMock()
        start = datetime(2025, 1, 15, 16, 0, tzinfo=dt_util.UTC)
        end = datetime(2025, 1, 17, 11, 0, tzinfo=dt_util.UTC)

        buffered_start, buffered_end = apply_buffer(start, end, 60, 0, rc)

        assert buffered_start == start - timedelta(minutes=60)
        assert buffered_end == end

    def test_apply_buffer_after_extends_end_only(self) -> None:
        """After-buffer moves only the end forward."""
        rc = MagicMock()
        start = datetime(2025, 1, 15, 16, 0, tzinfo=dt_util.UTC)
        end = datetime(2025, 1, 17, 11, 0, tzinfo=dt_util.UTC)

        buffered_start, buffered_end = apply_buffer(start, end, 0, 30, rc)

        assert buffered_start == start
        assert buffered_end == end + timedelta(minutes=30)

    def test_apply_buffer_converts_date_to_datetime(self) -> None:
        """Date-only values are normalised before buffer arithmetic."""
        from zoneinfo import ZoneInfo

        rc = MagicMock()
        rc.timezone = ZoneInfo("America/New_York")

        buffered_start, buffered_end = apply_buffer(
            date(2025, 1, 15),
            date(2025, 1, 17),
            30,
            15,
            rc,
        )

        assert buffered_start == datetime(2025, 1, 14, 23, 30, tzinfo=rc.timezone)
        assert buffered_end == datetime(2025, 1, 17, 0, 15, tzinfo=rc.timezone)

    async def test_set_code_event_attributes_unchanged_after_buffer(self) -> None:
        """Buffered writes do not mutate the original event attributes."""
        coordinator = self._make_coordinator()
        original_start = datetime(2025, 1, 15, 16, 0, tzinfo=dt_util.UTC)
        original_end = datetime(2025, 1, 17, 11, 0, tzinfo=dt_util.UTC)
        event = MagicMock()
        event.extra_state_attributes = {
            "slot_name": "Guest",
            "slot_code": "1234",
            "start": original_start,
            "end": original_end,
        }

        await async_fire_set_code(coordinator, event, 10)

        assert event.extra_state_attributes["start"] == original_start
        assert event.extra_state_attributes["end"] == original_end
