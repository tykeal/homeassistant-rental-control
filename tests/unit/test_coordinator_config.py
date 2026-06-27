# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Tests for the pure config-update coordinator helpers."""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta
from datetime import timezone

from custom_components.rental_control.coordinator_helpers import config_update


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
