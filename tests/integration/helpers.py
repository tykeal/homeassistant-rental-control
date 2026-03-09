# SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Shared helpers for integration tests."""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta

from homeassistant.util import dt as dt_util

FROZEN_TIME = datetime(2024, 12, 20, 12, 0, 0, tzinfo=dt_util.UTC)
FROZEN_START_OF_DAY = datetime(2024, 12, 20, 0, 0, 0, tzinfo=dt_util.UTC)


def future_ics(
    summary: str = "Reserved: Test Guest",
    description: str = "Email: test@example.com\\nPhone: +1234567890\\nGuests: 2",
    days_ahead: int = 5,
    duration: int = 5,
    *,
    base_time: datetime = FROZEN_TIME,
) -> str:
    """Build a single-event ICS with dates relative to *base_time*.

    Event start/end times are fixed at 16:00:00Z and 11:00:00Z respectively;
    only date components are derived from *base_time*.
    """
    start = (base_time + timedelta(days=days_ahead)).strftime("%Y%m%d")
    end = (base_time + timedelta(days=days_ahead + duration)).strftime("%Y%m%d")
    return (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//Test//EN\r\n"
        "BEGIN:VEVENT\r\n"
        f"DTSTART:{start}T160000Z\r\n"
        f"DTEND:{end}T110000Z\r\n"
        "UID:future-test@example.com\r\n"
        f"SUMMARY:{summary}\r\n"
        f"DESCRIPTION:{description}\r\n"
        "STATUS:CONFIRMED\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )
