# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Behavior-free data objects shared by coordinator helper modules.

This module intentionally avoids importing Home Assistant, the HA
``Store`` API, the coordinator, or Keymaster service helpers.  It holds
only dataclasses and small pure helpers used to pass parsed context and
decisions between :mod:`coordinator` and the extracted helper modules.
"""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import time
from datetime import tzinfo
from typing import TYPE_CHECKING
from typing import Any

from ..util import trim_name

if TYPE_CHECKING:
    from ..reconciliation import ManagedSlot
    from ..reconciliation import Reservation


def _store_datetime(value: Any) -> Any:
    """Return a JSON-serializable datetime value for Store payloads."""
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _adopted_slot_placeholder(slot: int) -> str:
    """Return a safe placeholder name for a code-bearing unnamed slot."""
    return f"Adopted Slot {slot}"


def _format_display_slot_name(
    slot_name: str,
    prefix: str,
    trim_names: bool,
    max_name_length: int,
) -> str:
    """Return the Keymaster display name for a reservation slot."""
    display_name = f"{prefix}{slot_name}"
    if not trim_names or max_name_length <= 0:
        return display_name
    if len(prefix) >= max_name_length:
        return trim_name(display_name, max_name_length)
    return f"{prefix}{trim_name(slot_name, max_name_length - len(prefix))}"


@dataclass
class CalendarParseContext:
    """Pure inputs required to interpret an ``icalendar`` calendar."""

    timezone: tzinfo
    checkin: time
    checkout: time
    event_prefix: str | None
    ignore_non_reserved: bool
    honor_event_times: bool
    code_buffer_before: int
    code_buffer_after: int
    override_lookup: Callable[[str], Mapping[str, Any] | None] | None = None


@dataclass
class ReservationBuildContext:
    """Pure inputs required to build reservations from calendar events."""

    entry_id: str
    timezone: tzinfo
    event_prefix: str | None
    trim_names: bool
    max_name_length: int
    code_buffer_before: int
    code_buffer_after: int
    should_update_code: bool
    code_generator: str
    code_length: int
    active_windows_for_name: Callable[[str], set[tuple[datetime, datetime]]]


@dataclass
class ObservedSlotQuery:
    """Query describing how to match a physical slot by name and dates."""

    managed_slots: list[ManagedSlot]
    slot_name: str
    display_slot_name: str
    consumed_slots: set[int] | None = None
    desired_start: datetime | None = None
    desired_end: datetime | None = None
    require_date_match: bool = False
    reserved_date_windows: set[tuple[datetime, datetime]] | None = None
    ordered_date_windows: list[tuple[datetime, datetime]] | None = None
    block_unknown_date_fallback: bool = False
    expected_name_count: int = 1
    event_prefix: str = ""


@dataclass
class EventOverrideUpdate:
    """Normalized payload for ``update_event_overrides``."""

    slot: int
    slot_code: str
    slot_name: str
    start_time: datetime
    end_time: datetime


_EVENT_OVERRIDE_FIELDS = ("slot", "slot_code", "slot_name", "start_time", "end_time")


def normalize_event_override_update(
    update: Any,
    values: tuple[Any, ...],
    legacy: Mapping[str, Any],
) -> EventOverrideUpdate:
    """Normalize all accepted call forms into an ``EventOverrideUpdate``.

    Accepts a direct :class:`EventOverrideUpdate`, the five positional
    values ``(slot, slot_code, slot_name, start_time, end_time)``, or the
    keyword equivalents.  Raises ``TypeError`` for missing, duplicate, or
    unknown arguments.
    """
    if isinstance(update, EventOverrideUpdate):
        if values or legacy:
            msg = "EventOverrideUpdate cannot be combined with extra values"
            raise TypeError(msg)
        return update

    unknown = set(legacy) - set(_EVENT_OVERRIDE_FIELDS)
    if unknown:
        msg = f"Unknown update_event_overrides arguments: {sorted(unknown)}"
        raise TypeError(msg)

    positional: list[Any] = []
    if update is not None:
        positional.append(update)
    positional.extend(values)

    if len(positional) > len(_EVENT_OVERRIDE_FIELDS):
        msg = "Too many positional values for update_event_overrides"
        raise TypeError(msg)

    resolved: dict[str, Any] = {}
    for name, value in zip(_EVENT_OVERRIDE_FIELDS, positional, strict=False):
        resolved[name] = value
    for name, value in legacy.items():
        if name in resolved:
            msg = f"Duplicate value for update_event_overrides argument {name!r}"
            raise TypeError(msg)
        resolved[name] = value

    missing = [name for name in _EVENT_OVERRIDE_FIELDS if name not in resolved]
    if missing:
        msg = f"Missing update_event_overrides arguments: {missing}"
        raise TypeError(msg)

    return EventOverrideUpdate(
        slot=resolved["slot"],
        slot_code=resolved["slot_code"],
        slot_name=resolved["slot_name"],
        start_time=resolved["start_time"],
        end_time=resolved["end_time"],
    )


@dataclass
class GhostReservationResult:
    """Ghost reservations plus the mutations applied to persisted mappings."""

    reservations: list[Reservation] = field(default_factory=list)


@dataclass
class KeymasterSlotSnapshot:
    """Raw per-slot Keymaster HA state captured by the coordinator shell."""

    slot: int
    name_state: str | None = None
    pin_state: str | None = None
    use_date_range_state: str | None = None
    enabled_state: str | None = None
    start_state: str | None = None
    end_state: str | None = None


@dataclass
class BootstrapDecision:
    """First-load override-setup decision for a single Keymaster slot."""

    slot: int
    override_update: EventOverrideUpdate | None = None
    force_clear: bool = False
    placeholder_name: str | None = None
    skip_reason: str | None = None


@dataclass
class AdoptionMappingDecision:
    """Cache-only Store adoption decision for a single Keymaster slot."""

    identity_key: str
    mapping: dict[str, Any]
    slot: int
    status: str
    skip_reason: str | None = None


@dataclass
class CheckinProtectionSnapshot:
    """Pure projection of the check-in tracking sensor state."""

    state: str
    guest_name: str
    start: datetime | None
    end: datetime | None
    summary: str
    attributes: Mapping[str, Any]


@dataclass
class StoreSyncPlan:
    """Cache-only Store mapping mutation plan derived from a desired plan."""

    remove_identity_keys: list[str] = field(default_factory=list)
    upsert_mappings: dict[str, dict[str, Any]] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
