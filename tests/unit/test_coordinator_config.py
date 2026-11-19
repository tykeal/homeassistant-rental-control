# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Tests for the pure config-update coordinator helpers."""

from __future__ import annotations

from datetime import datetime
from datetime import time
from datetime import timedelta
from datetime import timezone
from zoneinfo import ZoneInfo

from custom_components.rental_control.coordinator_helpers import config_update
from custom_components.rental_control.coordinator_helpers.calendar_parsing import (
    physical_override_time,
)
from custom_components.rental_control.helpers import apply_buffer


def test_overrides_are_stale_when_missing() -> None:
    """A missing override map is always stale."""
    assert config_update.overrides_are_stale(True, False, 5, 5, 10, 10) is True


def test_overrides_are_stale_when_counts_change() -> None:
    """Changed max-events or start-slot triggers a rebuild."""
    assert config_update.overrides_are_stale(False, False, 6, 5, 10, 10) is True
    assert config_update.overrides_are_stale(False, False, 5, 5, 11, 10) is True


def test_overrides_are_stale_false_when_unchanged() -> None:
    """No relevant change means the map is fresh."""
    assert config_update.overrides_are_stale(False, False, 5, 5, 10, 10) is False


def test_buffer_changed() -> None:
    """A buffer change is detected on either side."""
    assert config_update.buffer_changed(5, 0, 0, 0) is True
    assert config_update.buffer_changed(0, 5, 0, 0) is True
    assert config_update.buffer_changed(0, 0, 0, 0) is False


def test_unbuffer_window_reverses_buffer() -> None:
    """Unbuffering recovers the original window."""
    start = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    end = datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)
    unbuffered_start, unbuffered_end = config_update.unbuffer_window(start, end, 30, 60)
    assert unbuffered_start == start + timedelta(minutes=30)
    assert unbuffered_end == end - timedelta(minutes=60)


def test_unbuffer_window_round_trips_repeated_hour_buffer() -> None:
    """Reversing a buffer preserves the instant across the repeated hour."""
    ny_timezone = ZoneInfo("America/New_York")
    start = datetime(2026, 11, 1, 1, 15, tzinfo=ny_timezone, fold=1)
    buffered_start, buffered_end = apply_buffer(start, start, 30, 0, object())
    assert isinstance(buffered_start, datetime)
    assert isinstance(buffered_end, datetime)

    unbuffered_start, _ = config_update.unbuffer_window(
        buffered_start, buffered_end, 30, 0
    )

    assert unbuffered_start.astimezone(timezone.utc) == start.astimezone(timezone.utc)


def test_physical_override_time_reverses_repeated_hour_buffer() -> None:
    """Recover the same local time when an elapsed buffer crosses fall-back."""
    ny_timezone = ZoneInfo("America/New_York")
    buffered_start = datetime(2026, 11, 1, 1, 45, tzinfo=ny_timezone, fold=0)

    unbuffered_time = physical_override_time(
        buffered_start, ny_timezone, 30, 0, start=True
    )

    assert unbuffered_time == time(1, 15, fold=1)
