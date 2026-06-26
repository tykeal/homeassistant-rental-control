# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Pure event-selection helpers for the check-in sensor."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from homeassistant.components.calendar import CalendarEvent

from ...util import get_slot_name


def event_key(summary: str, start: datetime) -> str:
    """Return the behavior-compatible identity key for an event."""
    return f"{summary}|{start.isoformat()}"


def get_relevant_event(
    events: Sequence[CalendarEvent] | None,
    now: datetime,
) -> CalendarEvent | None:
    """Return the first event whose end time has not passed."""
    for event in events or []:
        if event.end > now:
            return event
    return None


def find_followon_event(
    events: Sequence[CalendarEvent] | None,
    checkout_time: datetime,
    checked_out_event_key: str | None,
) -> CalendarEvent | None:
    """Return the first event starting after checkout, excluding checked-out."""
    for event in events or []:
        if checked_out_event_key is not None:
            if event_key(event.summary, event.start) == checked_out_event_key:
                continue
        if event.start >= checkout_time:
            return event
    return None


def find_tracked_event(
    events: Sequence[CalendarEvent] | None,
    tracked_summary: str | None,
    tracked_start: datetime | None,
) -> CalendarEvent | None:
    """Find a tracked event by summary and start identity."""
    if not events or tracked_summary is None or tracked_start is None:
        return None
    tracked_key = event_key(tracked_summary, tracked_start)
    for event in events:
        if event_key(event.summary, event.start) == tracked_key:
            return event
    return None


def extract_slot_name(event: CalendarEvent, event_prefix: str) -> str | None:
    """Extract the guest/slot name using existing Rental Control parsing."""
    return get_slot_name(event.summary, event.description or "", event_prefix or "")
