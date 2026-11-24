# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Reservation identity and retry-safety guards for checkout expiry."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from importlib import import_module
from typing import Any
from typing import cast

from ...keymaster_topology import association_requires_verified_routing
from .event_selection import event_datetime


def _retry_slot(
    entity: Any,
    identity_end: datetime,
    start: datetime | None,
    slot_name: str | None,
    alternate_end: datetime | None = None,
) -> int | None:
    """Return the assigned managed slot for the reservation being shortened."""
    coordinator = entity.coordinator
    overrides = coordinator.event_overrides
    if overrides is None or start is None or not slot_name:
        return None
    if association_requires_verified_routing(entity._config_entry):
        assignment = getattr(coordinator, "get_slot_assignment", None)
        if not callable(assignment):
            return None
        for reservation_end in (identity_end, alternate_end):
            if reservation_end is None:
                continue
            identity_key = _reservation_fingerprint(
                entity._config_entry.entry_id, slot_name, start, reservation_end
            )
            slot = assignment(identity_key)
            if (
                isinstance(slot, int)
                and not isinstance(slot, bool)
                and coordinator.start_slot
                <= slot
                < coordinator.start_slot + coordinator.max_events
                and overrides.get_slot_name(slot) == slot_name
            ):
                return slot
        return None
    slot = overrides.get_slot_key_by_name(slot_name)
    if (
        not isinstance(slot, int)
        or isinstance(slot, bool)
        or not coordinator.start_slot
        <= slot
        < coordinator.start_slot + coordinator.max_events
        or overrides.get_slot_name(slot) != slot_name
    ):
        return None
    return slot


def _legacy_reservation_is_current(
    entity: Any,
    slot: int,
    identity_end: datetime | None,
    new_end: datetime,
) -> bool:
    """Keep legacy service routing attached to the original reservation."""
    overrides = entity.coordinator.event_overrides
    start = entity._tracked_event_start
    end = identity_end
    slot_name = entity._tracked_event_slot_name
    if overrides is None or start is None or end is None or not slot_name:
        return False
    override = getattr(overrides, "overrides", {}).get(slot)
    if not isinstance(override, dict):
        return False
    same_slot = bool(
        override.get("slot_name") == slot_name
        and overrides.get_slot_key_by_name(slot_name) == slot
        and entity._tracked_event_end in {end, new_end}
    )
    if not same_slot:
        return False
    event_match = _legacy_calendar_event_matches(entity, end)
    if event_match is not None:
        return event_match
    return bool(
        override.get("start_time") == start
        and override.get("end_time") in {end, new_end}
    )


def _reservation_fingerprint(
    entry_id: str, slot_name: str, start: datetime, end: datetime
) -> str:
    """Use the verified date updater's reservation identity implementation."""
    verified_date = import_module(
        "custom_components.rental_control.sensors.checkin.verified_date"
    )
    return cast(
        str,
        verified_date.make_reservation_fingerprint(entry_id, slot_name, start, end),
    )


def _retry_reservation_is_current(entity: Any) -> bool:
    """Return whether current data disproves a pending retry's reservation."""
    original_end = getattr(entity, "_early_expiry_original_end", None)
    new_end = getattr(entity, "_early_expiry_new_end", None)
    previous_slot = getattr(entity, "_early_expiry_retry_slot", None)
    start = entity._tracked_event_start
    slot_name = entity._tracked_event_slot_name
    if (
        original_end is None
        or new_end is None
        or start is None
        or not slot_name
        or entity._tracked_event_end not in {original_end, new_end}
    ):
        return False
    coordinator = entity.coordinator
    overrides = coordinator.event_overrides
    if overrides is None or not getattr(overrides, "ready", False):
        return True
    event_match = _legacy_calendar_event_matches(entity, original_end)
    if event_match is False:
        return False
    slot = _retry_slot(entity, original_end, start, slot_name, new_end)
    if slot is None:
        if isinstance(previous_slot, int) and not isinstance(previous_slot, bool):
            previous_override = getattr(overrides, "overrides", {}).get(previous_slot)
            if isinstance(previous_override, dict) and (
                previous_override.get("slot_name") != slot_name
                or previous_override.get("start_time") != start
                or previous_override.get("end_time") not in {original_end, new_end}
            ):
                return False
        return True
    override = getattr(overrides, "overrides", {}).get(slot)
    if not isinstance(override, dict):
        return event_match is not False
    if (
        override.get("slot_name") != slot_name
        or overrides.get_slot_name(slot) != slot_name
        or overrides.get_slot_key_by_name(slot_name) != slot
        or not coordinator.start_slot
        <= slot
        < coordinator.start_slot + coordinator.max_events
    ):
        return event_match is not False
    if association_requires_verified_routing(entity._config_entry):
        assignment = getattr(coordinator, "get_slot_assignment", None)
        if not callable(assignment):
            return True
        return _verified_retry_identity_end(entity, slot, assignment) is not None or (
            event_match is not False
        )
    if event_match is not None:
        return event_match
    if override.get("start_time") == start and override.get("end_time") in {
        original_end,
        new_end,
    }:
        return True
    return False


def _verified_retry_identity_end(
    entity: Any, slot: int, assignment: Callable[[str], int | None]
) -> datetime | None:
    """Find which persisted reservation identity still owns the verified slot."""
    for identity_end in (
        entity._early_expiry_original_end,
        entity._early_expiry_new_end,
    ):
        if not isinstance(identity_end, datetime):
            continue
        identity_key = _reservation_fingerprint(
            entity._config_entry.entry_id,
            entity._tracked_event_slot_name,
            entity._tracked_event_start,
            identity_end,
        )
        if assignment(identity_key) == slot:
            return identity_end
    return None


def _legacy_calendar_event_matches(entity: Any, identity_end: datetime) -> bool | None:
    """Check the live calendar identity when data is authoritative and available."""
    finder = getattr(entity, "_find_tracked_event", None)
    if not callable(finder):
        return None
    coordinator = entity.coordinator
    data = getattr(coordinator, "data", None)
    if (
        not getattr(coordinator, "last_update_success", False)
        or data is None
        or not data
    ):
        return None
    event = finder()
    if event is None:
        return False
    return event_datetime(event.end) == identity_end
