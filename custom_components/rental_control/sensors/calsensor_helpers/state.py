# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""State render helpers for Rental Control calendar sensors."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from .attributes import build_no_reservation_attributes
from .attributes import build_no_reservation_summary
from .models import CalendarSensorRenderResult


def select_event(event_list: Sequence[Any] | None, event_number: int) -> Any | None:
    """Select the event for a sensor index using legacy truthiness rules."""
    if event_list and event_number < len(event_list):
        return event_list[event_number]
    return None


def build_event_state(summary: str, start: datetime) -> str:
    """Build the legacy event state string."""
    state = f"{summary} - {start.day} {start.strftime('%B %Y')}"
    return f"{state} {start.strftime('%H:%M')}"


def render_no_reservation(event_prefix: str | None) -> CalendarSensorRenderResult:
    """Build the no-reservation render result."""
    summary = build_no_reservation_summary(event_prefix)
    return CalendarSensorRenderResult(
        state=summary,
        event_attributes=build_no_reservation_attributes(event_prefix),
        parsed_attributes={},
    )


def render_event_result(
    event: Any,
    event_attributes: dict[str, Any],
    parsed_attributes: dict[str, str],
) -> CalendarSensorRenderResult:
    """Build the render result for a selected calendar event."""
    return CalendarSensorRenderResult(
        state=build_event_state(event.summary, event.start),
        event_attributes=event_attributes,
        parsed_attributes=parsed_attributes,
    )
