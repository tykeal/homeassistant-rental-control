# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Tests for the pure check-in protection coordinator helpers."""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta
from datetime import timezone

from custom_components.rental_control.coordinator_helpers import checkin_protection
from custom_components.rental_control.reconciliation import Reservation


def _reservation(name: str, start: datetime, end: datetime) -> Reservation:
    """Return a minimal reservation for matching tests."""
    return Reservation(
        identity_key=f"res-{name}",
        start=start,
        end=end,
        buffered_start=start,
        buffered_end=end,
        summary=name,
        slot_name=name,
        display_slot_name=f"RC {name}",
        slot_code="1234",
    )


def test_select_checkin_match_exact_window() -> None:
    """An exact start/end match wins over duplicate names."""
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(days=2)
    other_end = start + timedelta(days=3)
    res_a = _reservation("Guest", start, end)
    res_b = _reservation("Guest", start, other_end)
    matched = checkin_protection.select_checkin_match(
        [res_a, res_b], "Guest", start, end
    )
    assert matched is res_a


def test_select_checkin_match_unique_name_without_times() -> None:
    """A unique name match is allowed when tracked times are absent."""
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(days=2)
    res = _reservation("Solo", start, end)
    assert checkin_protection.select_checkin_match([res], "Solo", None, None) is res


def test_select_checkin_match_none_when_ambiguous() -> None:
    """Ambiguous duplicate names without tracked times do not match."""
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(days=2)
    res_a = _reservation("Dup", start, end)
    res_b = _reservation("Dup", start, end)
    assert (
        checkin_protection.select_checkin_match([res_a, res_b], "Dup", None, None)
        is None
    )


def test_checkin_windows_includes_tracked_and_buffered() -> None:
    """The window set includes both tracked and buffered ranges."""
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(days=2)
    buf_start = start - timedelta(hours=1)
    buf_end = end + timedelta(hours=1)
    windows = checkin_protection.checkin_windows(start, end, buf_start, buf_end)
    assert (start, end) in windows
    assert (buf_start, buf_end) in windows


def test_should_defer_restore_empty_slots() -> None:
    """No managed slots means no deferral."""
    assert (
        checkin_protection.should_defer_restore([], [], lambda _n, _r: False) is False
    )
