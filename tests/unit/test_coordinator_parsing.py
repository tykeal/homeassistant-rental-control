# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Tests for the pure calendar-parsing coordinator helpers."""

from __future__ import annotations

from datetime import datetime
from datetime import time
from datetime import timezone

from custom_components.rental_control.coordinator_helpers import calendar_parsing


def test_datetimes_match_true_for_equal_instants() -> None:
    """Equal instants in different zones still match."""
    left = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    right = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    assert calendar_parsing.datetimes_match(left, right, timezone.utc) is True


def test_datetimes_match_false_for_different_instants() -> None:
    """Different instants do not match."""
    left = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    right = datetime(2026, 1, 1, 13, 0, tzinfo=timezone.utc)
    assert calendar_parsing.datetimes_match(left, right, timezone.utc) is False


def test_combine_event_time_returns_datetime() -> None:
    """Combining a date with a time produces an aware datetime."""
    base = datetime(2026, 6, 15, tzinfo=timezone.utc)
    result = calendar_parsing.combine_event_time(base, time(11, 30), timezone.utc)
    assert isinstance(result, datetime)
    assert result.hour == 11
    assert result.minute == 30
