# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Event-derived names and buffered windows for Keymaster operations."""

from __future__ import annotations

from datetime import datetime

from .keymaster_service_helpers import KeymasterServiceDeps


def slot_display_name(coordinator, event, deps: KeymasterServiceDeps) -> str:
    """Return the Keymaster display name for a set-code operation."""
    prefix = f"{coordinator.event_prefix} " if coordinator.event_prefix else ""
    slot_name = f"{prefix}{event.extra_state_attributes['slot_name']}"
    if coordinator.trim_names:
        guest = event.extra_state_attributes["slot_name"]
        guest_max = coordinator.max_name_length - len(prefix)
        slot_name = f"{prefix}{deps.trim_name(guest, guest_max)}"
    return slot_name


def buffered_window(
    coordinator, event, deps: KeymasterServiceDeps, sanitize_buffers: bool = False
) -> tuple[datetime, datetime]:
    """Return buffered start and end datetimes for an event."""
    if sanitize_buffers:
        before = getattr(coordinator, "code_buffer_before", 0)
        after = getattr(coordinator, "code_buffer_after", 0)
        before = before if isinstance(before, int) else 0
        after = after if isinstance(after, int) else 0
    else:
        before = coordinator.code_buffer_before
        after = coordinator.code_buffer_after
    buffered_start, buffered_end = deps.apply_buffer(
        event.extra_state_attributes["start"],
        event.extra_state_attributes["end"],
        before,
        after,
        coordinator,
    )
    return (
        deps.ensure_datetime(buffered_start, coordinator),
        deps.ensure_datetime(buffered_end, coordinator),
    )
