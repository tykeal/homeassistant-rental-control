# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Pure configuration-update decisions.

The coordinator shell parses the raw config entry (which requires Home
Assistant validators) and performs Store/entity side effects. This module
holds the pure decisions derived from a config change: whether overrides
are stale, whether buffers changed, and how to recover unbuffered times.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta


def overrides_are_stale(
    overrides_missing: bool,
    lockname_changed: bool,
    max_events: int,
    previous_max_events: int,
    start_slot: int,
    previous_start_slot: int,
) -> bool:
    """Return whether the event-override map must be rebuilt."""
    return (
        overrides_missing
        or lockname_changed
        or max_events != previous_max_events
        or start_slot != previous_start_slot
    )


def buffer_changed(
    before: int,
    after: int,
    previous_before: int,
    previous_after: int,
) -> bool:
    """Return whether either code buffer value changed."""
    return before != previous_before or after != previous_after


def unbuffer_window(
    start: datetime,
    end: datetime,
    old_before: int,
    old_after: int,
) -> tuple[datetime, datetime]:
    """Reverse a previously-applied buffer to recover unbuffered times.

    Keymaster stores already-buffered datetimes. To re-apply a new buffer
    without double counting, the old buffer must first be reversed.
    """
    if old_before:
        start = start + timedelta(minutes=old_before)
    if old_after:
        end = end - timedelta(minutes=old_after)
    return start, end
