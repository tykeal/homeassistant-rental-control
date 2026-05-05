# SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Parse check-in/check-out times from iCal event descriptions."""

from __future__ import annotations

from datetime import time
import re

_CHECKIN_PATTERN = re.compile(
    r"check-?in(?:\s+time)?\s*:\s*(\d{1,2}(?::\d{2})?)(?!\d|:)\s*(AM|PM)?",
    re.IGNORECASE,
)

_CHECKOUT_PATTERN = re.compile(
    r"check-?out(?:\s+time)?\s*:\s*(\d{1,2}(?::\d{2})?)(?!\d|:)\s*(AM|PM)?",
    re.IGNORECASE,
)


def _parse_time_match(time_str: str, ampm: str | None) -> time | None:
    """Convert regex capture groups to a validated time object.

    Args:
        time_str: Matched time string (e.g., "16" or "16:30").
        ampm: "AM", "PM", or None.

    Returns:
        A valid datetime.time or None if values are out of range.
    """
    parts = time_str.split(":")
    hour = int(parts[0])
    minute = int(parts[1]) if len(parts) > 1 else 0

    if ampm:
        ampm_upper = ampm.upper()
        if hour < 1 or hour > 12:
            return None
        if ampm_upper == "AM":
            hour = 0 if hour == 12 else hour
        else:  # PM
            hour = hour if hour == 12 else hour + 12
    else:
        if hour < 0 or hour > 23:
            return None

    if minute < 0 or minute > 59:
        return None

    return time(hour, minute)


def extract_checkin_time(description: str) -> time | None:
    """Extract check-in time from an event description.

    Searches for patterns like "Checkin time: 16", "Check-in: 4 PM",
    "Check-in time: 16:30" (case-insensitive). Returns the first match.

    Args:
        description: The event DESCRIPTION field as a string.

    Returns:
        A datetime.time object if a valid check-in time is found, None otherwise.
    """
    match = _CHECKIN_PATTERN.search(description)
    if not match:
        return None
    return _parse_time_match(match.group(1), match.group(2))


def extract_checkout_time(description: str) -> time | None:
    """Extract check-out time from an event description.

    Searches for patterns like "Checkout time: 11", "Check-out: 11 AM",
    "Check-out time: 11:30" (case-insensitive). Returns the first match.

    Args:
        description: The event DESCRIPTION field as a string.

    Returns:
        A datetime.time object if a valid check-out time is found, None otherwise.
    """
    match = _CHECKOUT_PATTERN.search(description)
    if not match:
        return None
    return _parse_time_match(match.group(1), match.group(2))
