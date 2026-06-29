# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Typed values used by calendar sensor helper modules."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class EtaSnapshot:
    """ETA fields exposed as event state attributes."""

    eta_days: int | None
    eta_hours: int | None
    eta_minutes: int | None


@dataclass(frozen=True, slots=True)
class EventAttributeSnapshot:
    """Complete base event attributes before parsed fields are merged."""

    summary: str
    description: str | None
    location: str | None
    start: datetime | None
    end: datetime | None
    uid: Any
    eta_days: int | None
    eta_hours: int | None
    eta_minutes: int | None
    slot_name: str | None
    slot_code: str | None
    slot_number: int | None

    def as_dict(self) -> dict[str, Any]:
        """Return attributes with the existing calendar sensor keys."""
        return {
            "summary": self.summary,
            "description": self.description,
            "location": self.location,
            "start": self.start,
            "end": self.end,
            "uid": self.uid,
            "eta_days": self.eta_days,
            "eta_hours": self.eta_hours,
            "eta_minutes": self.eta_minutes,
            "slot_name": self.slot_name,
            "slot_code": self.slot_code,
            "slot_number": self.slot_number,
        }


@dataclass(frozen=True, slots=True)
class ParsedReservationAttributes:
    """Parsed guest attributes derived from event descriptions."""

    last_four: str | None = None
    number_of_guests: str | None = None
    guest_email: str | None = None
    phone_number: str | None = None
    reservation_url: str | None = None
    booking_id: str | None = None
    dynamic: dict[str, str] | None = None

    def as_dict(self) -> dict[str, str]:
        """Return parsed attributes in the existing insertion order."""
        attributes: dict[str, str] = {}
        if self.last_four is not None:
            attributes["last_four"] = self.last_four
        if self.number_of_guests is not None:
            attributes["number_of_guests"] = self.number_of_guests
        if self.guest_email is not None:
            attributes["guest_email"] = self.guest_email
        if self.phone_number is not None:
            attributes["phone_number"] = self.phone_number
        if self.reservation_url is not None:
            attributes["reservation_url"] = self.reservation_url
        if self.booking_id is not None:
            attributes["booking_id"] = self.booking_id
        for key, value in (self.dynamic or {}).items():
            if key not in attributes:
                attributes[key] = value
        return attributes


@dataclass(frozen=True, slots=True)
class DoorCodeRequest:
    """Inputs needed for behavior-compatible generated door codes."""

    generator: str
    code_length: int
    start: datetime
    end: datetime
    uid: str | None
    description: str | None
    last_four: str | None


@dataclass(frozen=True, slots=True)
class SlotReadContext:
    """Inputs for read-only reconciliation slot lookup."""

    entry_id: str
    summary: str
    description: str | None
    event_prefix: str
    start: datetime
    end: datetime
    event_overrides_present: bool
    get_slot_name: Callable[[str, Any, str], str | None]
    make_reservation_fingerprint: Callable[[str, str, datetime, datetime], str]


@dataclass(frozen=True, slots=True)
class SlotReadResult:
    """Slot values read from coordinator reconciliation state."""

    slot_name: str | None
    slot_number: int | None
    slot_code: str | None


@dataclass(frozen=True, slots=True)
class CalendarSensorRenderResult:
    """Pure render result assigned by the calendar sensor shell."""

    state: str
    event_attributes: dict[str, Any]
    parsed_attributes: dict[str, str]


@dataclass(frozen=True, slots=True)
class SlotAssignmentContext:
    """Grouped legacy values for the no-op slot-assignment shim."""

    slot_name: str
    slot_code: str
    start_time: datetime
    end_time: datetime
    uid: str | None
    prefix: str
    eta_days: int | None
