# SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Mock configuration entry fixtures for testing."""

from typing import Any

# Minimal valid configuration (required fields only)
CONFIG_MINIMAL: dict[str, Any] = {
    "name": "Minimal Rental",
    "url": "https://example.com/minimal.ics",
}

# Complete configuration (all fields with custom values)
CONFIG_COMPLETE: dict[str, Any] = {
    "name": "Complete Rental",
    "url": "https://example.com/complete.ics",
    "verify_ssl": True,
}

OPTIONS_COMPLETE: dict[str, Any] = {
    "refresh_frequency": 15,
    "max_events": 7,
    "days": 180,
    "checkin": "15:00",
    "checkout": "10:00",
    "code_generation": "static_random",
    "code_length": 6,
    "start_slot": 20,
    "event_prefix": "Vacation",
    "timezone": "America/Chicago",
    "ignore_non_reserved": True,
    "should_update_code": False,
}

# Invalid configurations for validation testing
CONFIG_MISSING_NAME: dict[str, Any] = {
    "url": "https://example.com/noname.ics",
}

CONFIG_MISSING_URL: dict[str, Any] = {
    "name": "No URL Rental",
}

CONFIG_INVALID_URL: dict[str, Any] = {
    "name": "Bad URL",
    "url": "not-a-valid-url",
}

OPTIONS_INVALID_REFRESH: dict[str, Any] = {
    "refresh_frequency": 0,  # Below minimum of 2
    "max_events": 3,
    "days": 90,
}

OPTIONS_INVALID_REFRESH_HIGH: dict[str, Any] = {
    "refresh_frequency": 2000,  # Above maximum of 1440
    "max_events": 3,
    "days": 90,
}

OPTIONS_INVALID_MAX_EVENTS_LOW: dict[str, Any] = {
    "refresh_frequency": 5,
    "max_events": 0,  # Below minimum of 1
    "days": 90,
}

OPTIONS_INVALID_MAX_EVENTS_HIGH: dict[str, Any] = {
    "refresh_frequency": 5,
    "max_events": 15,  # Above maximum of 10
    "days": 90,
}

OPTIONS_INVALID_CODE_LENGTH_LOW: dict[str, Any] = {
    "code_generation": "date_based",
    "code_length": 3,  # Below minimum of 4
}

OPTIONS_INVALID_CODE_LENGTH_HIGH: dict[str, Any] = {
    "code_generation": "date_based",
    "code_length": 9,  # Above maximum of 8
}

# Realistic configuration scenarios
CONFIG_AIRBNB_SCENARIO: dict[str, Any] = {
    "name": "Airbnb Beach House",
    "url": "https://www.airbnb.com/calendar/ical/12345.ics",
}

OPTIONS_AIRBNB_SCENARIO: dict[str, Any] = {
    "refresh_frequency": 30,
    "max_events": 5,
    "days": 365,
    "checkin": "16:00",
    "checkout": "11:00",
    "code_generation": "last_four",
    "code_length": 4,
    "start_slot": 10,
    "ignore_non_reserved": True,
}

CONFIG_VRBO_SCENARIO: dict[str, Any] = {
    "name": "VRBO Mountain Cabin",
    "url": "https://www.vrbo.com/icalendar/abc123def456.ics",
}

OPTIONS_VRBO_SCENARIO: dict[str, Any] = {
    "refresh_frequency": 60,
    "max_events": 3,
    "days": 180,
    "checkin": "17:00",
    "checkout": "10:00",
    "code_generation": "static_random",
    "code_length": 6,
    "start_slot": 15,
    "timezone": "America/Denver",
}
