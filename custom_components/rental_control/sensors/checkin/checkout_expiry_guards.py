# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Reservation identity and retry-safety guards for checkout expiry."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from importlib import import_module
from typing import Any
from typing import cast

from homeassistant.util import dt as dt_util

from ...helpers import apply_buffer
from ...keymaster_topology import association_requires_verified_routing
from .event_selection import event_datetime
from .event_selection import extract_slot_name


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
    start = getattr(entity, "_early_expiry_reservation_start", None)
    if start is None:
        start = entity._tracked_event_start
    end = identity_end
    slot_name = getattr(entity, "_early_expiry_reservation_slot_name", None)
    if slot_name is None:
        slot_name = entity._tracked_event_slot_name
    if overrides is None or start is None or end is None or not slot_name:
        return False
    override = getattr(overrides, "overrides", {}).get(slot)
    if not isinstance(override, dict):
        return False
    same_slot = bool(
        override.get("slot_name") == slot_name
        and overrides.get_slot_key_by_name(slot_name) == slot
    )
    if not same_slot:
        return False
    event_match = _legacy_calendar_event_matches(entity, end, start, slot_name)
    return bool(
        _legacy_slot_reservation_matches(entity, override, start, end, new_end)
        and event_match is not False
    )


def _legacy_slot_reservation_matches(
    entity: Any,
    override: dict[str, Any],
    start: datetime,
    original_end: datetime,
    new_end: datetime,
) -> bool:
    """Require the assigned legacy slot's date window to match this stay."""
    coordinator = entity.coordinator
    before = getattr(coordinator, "code_buffer_before", 0)
    after = getattr(coordinator, "code_buffer_after", 0)
    if not isinstance(before, int) or not isinstance(after, int):
        return False
    expected_start, expected_end = apply_buffer(
        start, original_end, before, after, coordinator
    )
    expected_ends = {expected_end}
    if getattr(entity, "_early_expiry_retry_was_dispatched", False):
        expected_ends.add(new_end)
    return (
        override.get("start_time") == expected_start
        and override.get("end_time") in expected_ends
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
    start = getattr(entity, "_early_expiry_reservation_start", None)
    if start is None:
        start = entity._tracked_event_start
    slot_name = getattr(entity, "_early_expiry_reservation_slot_name", None)
    if slot_name is None:
        slot_name = entity._tracked_event_slot_name
    if original_end is None or new_end is None or start is None or not slot_name:
        return False
    coordinator = entity.coordinator
    overrides = coordinator.event_overrides
    if overrides is None or not getattr(overrides, "ready", False):
        return True
    event_match = _legacy_calendar_event_matches(entity, original_end, start, slot_name)
    if event_match is False:
        return False
    slot = _retry_slot(entity, original_end, start, slot_name, new_end)
    if slot is None:
        if isinstance(previous_slot, int) and not isinstance(previous_slot, bool):
            previous_override = getattr(overrides, "overrides", {}).get(previous_slot)
            if isinstance(previous_override, dict) and not (
                previous_override.get("slot_name") == slot_name
                and _legacy_slot_reservation_matches(
                    entity, previous_override, start, original_end, new_end
                )
            ):
                return False
        return True
    override = getattr(overrides, "overrides", {}).get(slot)
    if not isinstance(override, dict):
        return event_match is not False
    if (
        override.get("slot_name") != slot_name
        or overrides.get_slot_name(slot) != slot_name
        or not coordinator.start_slot
        <= slot
        < coordinator.start_slot + coordinator.max_events
    ):
        return False
    if association_requires_verified_routing(entity._config_entry):
        assignment = getattr(coordinator, "get_slot_assignment", None)
        if not callable(assignment):
            return True
        return (
            _verified_retry_identity_end(entity, slot, assignment) is not None
            and event_match is not False
        )
    return (
        overrides.get_slot_key_by_name(slot_name) == slot
        and _legacy_slot_reservation_matches(
            entity, override, start, original_end, new_end
        )
        and event_match is not False
    )


def _retry_dispatch_is_current(
    entity: Any,
    generation: int,
    original_end: datetime,
    new_end: datetime,
) -> bool:
    """Recheck retry identity, reservation ownership, and deadline before dispatch."""
    return (
        entity._early_expiry_retry_generation == generation
        and entity._early_expiry_original_end == original_end
        and entity._early_expiry_new_end == new_end
        and not getattr(entity, "_early_expiry_retry_entity_removed", False)
        and dt_util.utcnow() < dt_util.as_utc(original_end)
        and _retry_reservation_is_current(entity)
    )


def _verified_retry_identity_end(
    entity: Any, slot: int, assignment: Callable[[str], int | None]
) -> datetime | None:
    """Find which persisted reservation identity still owns the verified slot."""
    start = getattr(entity, "_early_expiry_reservation_start", None)
    if start is None:
        start = entity._tracked_event_start
    slot_name = getattr(entity, "_early_expiry_reservation_slot_name", None)
    if slot_name is None:
        slot_name = entity._tracked_event_slot_name
    for identity_end in (
        entity._early_expiry_original_end,
        entity._early_expiry_new_end,
    ):
        if not isinstance(identity_end, datetime) or start is None or not slot_name:
            continue
        identity_key = _reservation_fingerprint(
            entity._config_entry.entry_id,
            slot_name,
            start,
            identity_end,
        )
        if assignment(identity_key) == slot:
            return identity_end
    return None


def _legacy_calendar_event_matches(
    entity: Any,
    identity_end: datetime,
    identity_start: datetime | None = None,
    slot_name: str | None = None,
) -> bool | None:
    """Check the live calendar identity when data is authoritative and available."""
    coordinator = entity.coordinator
    data = getattr(coordinator, "data", None)
    if (
        not getattr(coordinator, "last_update_success", False)
        or data is None
        or not data
    ):
        return None
    start = identity_start or getattr(entity, "_early_expiry_reservation_start", None)
    name = slot_name or getattr(entity, "_early_expiry_reservation_slot_name", None)
    if start is None:
        start = entity._tracked_event_start
    if name is None:
        name = entity._tracked_event_slot_name
    if start is not None and name:
        prefix = getattr(coordinator, "event_prefix", "") or ""
        bookings_only = bool(getattr(coordinator, "bookings_only_feed", False))
        extractor = getattr(entity, "_extract_slot_name", None)
        for event in data:
            try:
                event_name = (
                    extractor(event)
                    if callable(extractor)
                    else extract_slot_name(event, prefix, bookings_only)
                )
                if (
                    event_datetime(event.start) == start
                    and event_datetime(event.end) == identity_end
                    and event_name == name
                ):
                    return True
            except TypeError:
                continue
        return False
    finder = getattr(entity, "_find_tracked_event", None)
    if not callable(finder):
        return None
    event = finder()
    if event is None:
        return False
    return event_datetime(event.end) == identity_end
