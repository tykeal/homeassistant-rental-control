# SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for description_parser module."""

from __future__ import annotations

from datetime import time

from custom_components.rental_control.description_parser import _parse_time_match
from custom_components.rental_control.description_parser import extract_checkin_time
from custom_components.rental_control.description_parser import extract_checkout_time

# ---------------------------------------------------------------------------
# T003-T007: _parse_time_match unit tests
# ---------------------------------------------------------------------------


class TestParseTimeMatch:
    """Tests for _parse_time_match internal helper."""

    def test_24h_hour_only(self) -> None:
        """T003: 24-hour format with hour only returns correct time."""
        assert _parse_time_match("16", None, None) == time(16, 0)

    def test_24h_hour_and_minutes(self) -> None:
        """T003: 24-hour format with hour and minutes."""
        assert _parse_time_match("16", "30", None) == time(16, 30)

    def test_24h_midnight(self) -> None:
        """T003: 24-hour format midnight."""
        assert _parse_time_match("0", None, None) == time(0, 0)

    def test_24h_hour_23(self) -> None:
        """T003: 24-hour format hour 23."""
        assert _parse_time_match("23", "59", None) == time(23, 59)

    def test_12h_am(self) -> None:
        """T004: 12-hour AM format."""
        assert _parse_time_match("9", None, "AM") == time(9, 0)

    def test_12h_pm(self) -> None:
        """T004: 12-hour PM format."""
        assert _parse_time_match("4", None, "PM") == time(16, 0)

    def test_12h_pm_with_minutes(self) -> None:
        """T004: 12-hour PM format with minutes."""
        assert _parse_time_match("4", "30", "PM") == time(16, 30)

    def test_12h_12pm_is_noon(self) -> None:
        """T004: 12 PM is noon (12:00)."""
        assert _parse_time_match("12", None, "PM") == time(12, 0)

    def test_12h_12am_is_midnight(self) -> None:
        """T004: 12 AM is midnight (00:00)."""
        assert _parse_time_match("12", None, "AM") == time(0, 0)

    def test_12h_am_lowercase(self) -> None:
        """T004: Case-insensitive AM/PM."""
        assert _parse_time_match("9", None, "am") == time(9, 0)

    def test_12h_pm_lowercase(self) -> None:
        """T004: Case-insensitive PM."""
        assert _parse_time_match("4", None, "pm") == time(16, 0)

    def test_invalid_24h_hour_24(self) -> None:
        """T005: Hour 24 in 24h mode is invalid."""
        assert _parse_time_match("24", None, None) is None

    def test_invalid_24h_hour_25(self) -> None:
        """T005: Hour 25 in 24h mode is invalid."""
        assert _parse_time_match("25", None, None) is None

    def test_invalid_12h_hour_0(self) -> None:
        """T005: Hour 0 in 12h mode is invalid."""
        assert _parse_time_match("0", None, "AM") is None

    def test_invalid_12h_hour_13(self) -> None:
        """T005: Hour 13 in 12h mode is invalid."""
        assert _parse_time_match("13", None, "PM") is None

    def test_invalid_minute_60(self) -> None:
        """T006: Minute 60 is invalid."""
        assert _parse_time_match("10", "60", None) is None

    def test_invalid_minute_99(self) -> None:
        """T006: Minute 99 is invalid."""
        assert _parse_time_match("10", "99", None) is None

    def test_valid_minute_00(self) -> None:
        """T007: Minute 00 is valid."""
        assert _parse_time_match("10", "00", None) == time(10, 0)

    def test_valid_minute_59(self) -> None:
        """T007: Minute 59 is valid."""
        assert _parse_time_match("10", "59", None) == time(10, 59)

    def test_none_minute_defaults_to_0(self) -> None:
        """T007: None minute defaults to 0."""
        assert _parse_time_match("10", None, None) == time(10, 0)


# ---------------------------------------------------------------------------
# T018-T021: extract_checkin_time pattern matching tests
# ---------------------------------------------------------------------------


class TestExtractCheckinTime:
    """Tests for extract_checkin_time function."""

    def test_checkin_24h_no_minutes(self) -> None:
        """T018: 'Check-in time: 16' extracts 16:00."""
        assert extract_checkin_time("Check-in time: 16") == time(16, 0)

    def test_checkin_24h_with_minutes(self) -> None:
        """T018: 'Check-in time: 16:30' extracts 16:30."""
        assert extract_checkin_time("Check-in time: 16:30") == time(16, 30)

    def test_checkin_12h_pm(self) -> None:
        """T018: 'Check-in: 4 PM' extracts 16:00."""
        assert extract_checkin_time("Check-in: 4 PM") == time(16, 0)

    def test_checkin_12h_am(self) -> None:
        """T018: 'Checkin time: 9 AM' extracts 09:00."""
        assert extract_checkin_time("Checkin time: 9 AM") == time(9, 0)

    def test_checkin_no_hyphen(self) -> None:
        """T019: 'Checkin: 16' (no hyphen) extracts 16:00."""
        assert extract_checkin_time("Checkin: 16") == time(16, 0)

    def test_checkin_with_hyphen(self) -> None:
        """T019: 'Check-in: 16' (with hyphen) extracts 16:00."""
        assert extract_checkin_time("Check-in: 16") == time(16, 0)

    def test_checkin_case_insensitive_upper(self) -> None:
        """T019: 'CHECK-IN TIME: 16' (uppercase) extracts 16:00."""
        assert extract_checkin_time("CHECK-IN TIME: 16") == time(16, 0)

    def test_checkin_case_insensitive_mixed(self) -> None:
        """T019: 'ChEcK-In time: 16' (mixed case) extracts 16:00."""
        assert extract_checkin_time("ChEcK-In time: 16") == time(16, 0)

    def test_checkin_with_time_keyword(self) -> None:
        """T019: 'Check-in time: 16' (with 'time') extracts 16:00."""
        assert extract_checkin_time("Check-in time: 16") == time(16, 0)

    def test_checkin_without_time_keyword(self) -> None:
        """T019: 'Check-in: 16' (without 'time') extracts 16:00."""
        assert extract_checkin_time("Check-in: 16") == time(16, 0)

    def test_checkin_embedded_in_multiline(self) -> None:
        """T020: Check-in time extracted from multiline description."""
        desc = "Guest: John\nCheck-in time: 15\nCheckout: 11\nNotes: none"
        assert extract_checkin_time(desc) == time(15, 0)

    def test_checkin_first_match_wins(self) -> None:
        """T020: First check-in pattern match wins."""
        desc = "Check-in: 14\nCheck-in time: 16"
        assert extract_checkin_time(desc) == time(14, 0)

    def test_checkin_no_match_returns_none(self) -> None:
        """T021: No check-in pattern returns None."""
        assert extract_checkin_time("No times here") is None

    def test_checkin_empty_string_returns_none(self) -> None:
        """T021: Empty string returns None."""
        assert extract_checkin_time("") is None

    def test_checkin_invalid_time_returns_none(self) -> None:
        """T021: Invalid time value returns None."""
        assert extract_checkin_time("Check-in: 25") is None

    def test_checkin_partial_match_wrong_keyword(self) -> None:
        """T021: Similar but wrong keyword does not match."""
        assert extract_checkin_time("Checking: 16") is None

    def test_checkin_12h_with_minutes(self) -> None:
        """T018: '4:30 PM' format extracts correctly."""
        assert extract_checkin_time("Check-in: 4:30 PM") == time(16, 30)

    def test_checkin_noon_pm(self) -> None:
        """T018: 'Check-in: 12 PM' is noon."""
        assert extract_checkin_time("Check-in: 12 PM") == time(12, 0)

    def test_checkin_midnight_am(self) -> None:
        """T018: 'Check-in: 12 AM' is midnight."""
        assert extract_checkin_time("Check-in: 12 AM") == time(0, 0)


# ---------------------------------------------------------------------------
# T027-T034: extract_checkout_time pattern matching tests
# ---------------------------------------------------------------------------


class TestExtractCheckoutTime:
    """Tests for extract_checkout_time function."""

    def test_checkout_24h_no_minutes(self) -> None:
        """T027: 'Check-out time: 11' extracts 11:00."""
        assert extract_checkout_time("Check-out time: 11") == time(11, 0)

    def test_checkout_24h_with_minutes(self) -> None:
        """T027: 'Check-out time: 11:30' extracts 11:30."""
        assert extract_checkout_time("Check-out time: 11:30") == time(11, 30)

    def test_checkout_12h_am(self) -> None:
        """T027: 'Check-out: 11 AM' extracts 11:00."""
        assert extract_checkout_time("Check-out: 11 AM") == time(11, 0)

    def test_checkout_12h_pm(self) -> None:
        """T027: 'Check-out: 2 PM' extracts 14:00."""
        assert extract_checkout_time("Check-out: 2 PM") == time(14, 0)

    def test_checkout_no_hyphen(self) -> None:
        """T028: 'Checkout: 11' (no hyphen) extracts 11:00."""
        assert extract_checkout_time("Checkout: 11") == time(11, 0)

    def test_checkout_with_hyphen(self) -> None:
        """T028: 'Check-out: 11' (with hyphen) extracts 11:00."""
        assert extract_checkout_time("Check-out: 11") == time(11, 0)

    def test_checkout_case_insensitive_upper(self) -> None:
        """T028: 'CHECK-OUT TIME: 11' (uppercase) extracts 11:00."""
        assert extract_checkout_time("CHECK-OUT TIME: 11") == time(11, 0)

    def test_checkout_case_insensitive_mixed(self) -> None:
        """T028: 'ChEcK-Out time: 11' (mixed case) extracts 11:00."""
        assert extract_checkout_time("ChEcK-Out time: 11") == time(11, 0)

    def test_checkout_with_time_keyword(self) -> None:
        """T028: 'Check-out time: 11' (with 'time') extracts 11:00."""
        assert extract_checkout_time("Check-out time: 11") == time(11, 0)

    def test_checkout_without_time_keyword(self) -> None:
        """T028: 'Check-out: 11' (without 'time') extracts 11:00."""
        assert extract_checkout_time("Check-out: 11") == time(11, 0)

    def test_checkout_embedded_in_multiline(self) -> None:
        """T029: Check-out time extracted from multiline description."""
        desc = "Guest: John\nCheck-in: 16\nCheck-out time: 10\nNotes: none"
        assert extract_checkout_time(desc) == time(10, 0)

    def test_checkout_first_match_wins(self) -> None:
        """T029: First check-out pattern match wins."""
        desc = "Check-out: 10\nCheck-out time: 11"
        assert extract_checkout_time(desc) == time(10, 0)

    def test_checkout_no_match_returns_none(self) -> None:
        """T030: No check-out pattern returns None."""
        assert extract_checkout_time("No times here") is None

    def test_checkout_empty_string_returns_none(self) -> None:
        """T030: Empty string returns None."""
        assert extract_checkout_time("") is None

    def test_checkout_invalid_time_returns_none(self) -> None:
        """T030: Invalid time value returns None."""
        assert extract_checkout_time("Check-out: 25") is None

    def test_checkout_partial_match_wrong_keyword(self) -> None:
        """T030: Similar but wrong keyword does not match."""
        assert extract_checkout_time("Checking out: 11") is None

    def test_checkout_12h_with_minutes(self) -> None:
        """T027: '11:30 AM' format extracts correctly."""
        assert extract_checkout_time("Check-out: 11:30 AM") == time(11, 30)

    def test_checkout_noon_pm(self) -> None:
        """T027: 'Check-out: 12 PM' is noon."""
        assert extract_checkout_time("Check-out: 12 PM") == time(12, 0)

    def test_checkout_midnight_am(self) -> None:
        """T027: 'Check-out: 12 AM' is midnight."""
        assert extract_checkout_time("Check-out: 12 AM") == time(0, 0)


# ---------------------------------------------------------------------------
# T031-T034: Combined extraction from same description
# ---------------------------------------------------------------------------


class TestCombinedExtraction:
    """Tests for extracting both check-in and check-out from same text."""

    def test_both_times_in_description(self) -> None:
        """T031: Both check-in and check-out extracted from one text."""
        desc = "Check-in time: 15\nCheck-out time: 10"
        assert extract_checkin_time(desc) == time(15, 0)
        assert extract_checkout_time(desc) == time(10, 0)

    def test_only_checkin_present(self) -> None:
        """T032: Only check-in present, check-out is None."""
        desc = "Check-in time: 16\nGuest: John"
        assert extract_checkin_time(desc) == time(16, 0)
        assert extract_checkout_time(desc) is None

    def test_only_checkout_present(self) -> None:
        """T033: Only check-out present, check-in is None."""
        desc = "Check-out: 11\nGuest: John"
        assert extract_checkin_time(desc) is None
        assert extract_checkout_time(desc) == time(11, 0)

    def test_neither_present(self) -> None:
        """T034: Neither check-in nor check-out present."""
        desc = "Guest: John\nEmail: john@example.com"
        assert extract_checkin_time(desc) is None
        assert extract_checkout_time(desc) is None

    def test_both_with_12h_format(self) -> None:
        """T031: Both times in 12-hour format."""
        desc = "Check-in: 4 PM\nCheck-out: 11 AM"
        assert extract_checkin_time(desc) == time(16, 0)
        assert extract_checkout_time(desc) == time(11, 0)

    def test_both_with_minutes(self) -> None:
        """T031: Both times with minutes."""
        desc = "Check-in time: 15:30\nCheck-out time: 10:45"
        assert extract_checkin_time(desc) == time(15, 30)
        assert extract_checkout_time(desc) == time(10, 45)
