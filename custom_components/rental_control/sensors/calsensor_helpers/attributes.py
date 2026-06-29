# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Attribute and ETA helpers for Rental Control calendar sensors."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ...const import SECONDS_PER_HOUR
from ...const import SECONDS_PER_MINUTE
from . import descriptions
from .models import EtaSnapshot
from .models import EventAttributeSnapshot
from .models import SlotReadResult


def build_no_reservation_summary(event_prefix: str | None) -> str:
    """Build the legacy no-reservation summary."""
    if event_prefix:
        return f"{event_prefix} No reservation"
    return "No reservation"


def build_no_reservation_attributes(event_prefix: str | None) -> dict[str, Any]:
    """Build event attributes for the no-reservation state."""
    return EventAttributeSnapshot(
        summary=build_no_reservation_summary(event_prefix),
        description=None,
        location=None,
        start=None,
        end=None,
        uid=None,
        eta_days=None,
        eta_hours=None,
        eta_minutes=None,
        slot_name=None,
        slot_code=None,
        slot_number=None,
    ).as_dict()


def normalize_uid(event: Any) -> Any:
    """Return the legacy normalized UID value from an event."""
    uid = getattr(event, "uid", None)
    if isinstance(uid, str):
        uid = uid.strip() or None
    return uid


def calculate_eta(start: datetime) -> EtaSnapshot:
    """Calculate ETA values using the existing calendar sensor semantics."""
    td = start - datetime.now(start.tzinfo)
    eta_days = None
    eta_hours = None
    eta_minutes = None
    if td.total_seconds() >= 0:
        eta_days = td.days
        eta_hours = round(td.total_seconds() // SECONDS_PER_HOUR)
        eta_minutes = round(td.total_seconds() // SECONDS_PER_MINUTE)
    return EtaSnapshot(
        eta_days=eta_days,
        eta_hours=eta_hours,
        eta_minutes=eta_minutes,
    )


def build_event_attributes(
    event: Any,
    eta: EtaSnapshot,
    slot: SlotReadResult,
) -> dict[str, Any]:
    """Build base event attributes for a renderable calendar event."""
    return EventAttributeSnapshot(
        summary=event.summary,
        description=event.description,
        location=event.location,
        start=event.start,
        end=event.end,
        uid=normalize_uid(event),
        eta_days=eta.eta_days,
        eta_hours=eta.eta_hours,
        eta_minutes=eta.eta_minutes,
        slot_name=slot.slot_name,
        slot_code=slot.slot_code,
        slot_number=slot.slot_number,
    ).as_dict()


def build_parsed_attributes(description: str | None) -> dict[str, str]:
    """Build parsed attributes from a description."""
    return descriptions.build_parsed_attributes(description)
