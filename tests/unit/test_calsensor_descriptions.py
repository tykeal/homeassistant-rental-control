# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Focused parity tests for calendar sensor description helpers."""

from __future__ import annotations

from custom_components.rental_control.sensors.calsensor_helpers import descriptions


def test_description_extractors_match_legacy_fields() -> None:
    """Verify dedicated helper extractors parse the legacy fields."""
    description = (
        "Email: first@example.com\n"
        "Email: second@example.com\n"
        "Phone: +1 555-123-4567\n"
        "Guests: 4\n"
        "Last 4 Digits: 9876\n"
        "Booking ID: abc123::def456\n"
        "https://airbnb.com/reservations/123"
    )

    assert descriptions.extract_email(description) == "first@example.com"
    assert descriptions.extract_phone_number(description) == "+1 555-123-4567"
    assert descriptions.extract_num_guests(description) == "4"
    assert descriptions.extract_last_four(description) == "9876"
    assert descriptions.extract_booking_id(description) == "abc123::def456"
    assert (
        descriptions.extract_url(description) == "https://airbnb.com/reservations/123"
    )


def test_guest_count_adults_and_children_match_legacy() -> None:
    """Verify adult and child counts are summed as before."""
    assert descriptions.extract_num_guests("Adults: 2\nChildren: 3") == "5"
    assert descriptions.extract_num_guests("Adults: 3") == "3"
    assert descriptions.extract_num_guests("Children: 3") is None


def test_last_four_fallbacks_match_legacy() -> None:
    """Verify phone last-four variants and fallback behavior."""
    assert descriptions.extract_last_four("Phone (Last 4): 9012") == "9012"
    assert descriptions.extract_last_four("Phone (last 4): 12345") is None
    assert descriptions.extract_last_four("Phone: +1 555-123-9876") == "9876"


def test_dynamic_attributes_match_legacy_filtering() -> None:
    """Verify dynamic fields skip known labels and URL-like labels."""
    description = (
        "Email: guest@example.com\n"
        "Adults: 2\n"
        "Children: 1\n"
        "https://example.com/booking/123\n"
        "Check-In Time: 3:00 PM\n"
        "Number Of Pets: 2"
    )

    assert descriptions.extract_dynamic_attributes(description) == {
        "check_in_time": "3:00 PM",
        "number_of_pets": "2",
    }


def test_build_parsed_attributes_preserves_order_and_no_overwrite() -> None:
    """Verify parsed attributes keep dedicated fields ahead of dynamic ones."""
    description = (
        "Last 4 Digits: 1111\n"
        "Guests: 2\n"
        "Email: guest@example.com\n"
        "Phone: +1 555-222-3333\n"
        "Booking ID: booking-1\n"
        "Custom Field: value"
    )

    assert descriptions.build_parsed_attributes(description) == {
        "last_four": "1111",
        "number_of_guests": "2",
        "guest_email": "guest@example.com",
        "phone_number": "+1 555-222-3333",
        "booking_id": "booking-1",
        "custom_field": "value",
    }


def test_none_descriptions_match_legacy_empty_results() -> None:
    """Verify helpers return legacy empty values for missing descriptions."""
    assert descriptions.extract_email(None) is None
    assert descriptions.extract_phone_number(None) is None
    assert descriptions.extract_num_guests(None) is None
    assert descriptions.extract_last_four(None) is None
    assert descriptions.extract_url(None) is None
    assert descriptions.extract_booking_id(None) is None
    assert descriptions.extract_dynamic_attributes(None) == {}
