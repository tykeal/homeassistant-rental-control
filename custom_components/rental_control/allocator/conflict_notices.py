# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Lifecycle of the notification raised for one shared door code.

A conflict is announced once per distinct set of physical slots and
that announcement is persisted with the registry, so neither a restart
nor the per-cycle re-judgement raises it again: a notification the user
dismissed stays dismissed until the conflict itself changes. Once the
conflict resolves, or turns out to be explained, its notification is
withdrawn so nothing stale is left behind (#942).
"""

from __future__ import annotations

from collections.abc import Collection
from collections.abc import Sequence
from datetime import date
from datetime import datetime
from typing import TYPE_CHECKING
from typing import Any

from homeassistant.components.persistent_notification import async_dismiss

from ..const import DOMAIN
from . import code_origin
from . import entry_info
from .models import AllocationOwner
from .registry import SlotKey
from .registry import physical_slot

if TYPE_CHECKING:
    from .allocator import DoorCodeAllocator

CONFLICT_NOTIFICATION_PREFIX = f"{DOMAIN}_code_registry_conflict"


def conflict_notification_id(code_ref: str) -> str:
    """Return the persistent notification id for one code's conflict.

    Each conflicting code gets its own notification. A single shared id
    let one code's report overwrite another's, so only the newest was
    ever visible and dismissing it hid every other open conflict.
    """
    return f"{CONFLICT_NOTIFICATION_PREFIX}_{code_ref}"


def claim(
    allocator: DoorCodeAllocator, code_ref: str, slots: frozenset[SlotKey]
) -> bool:
    """Record an announcement and return whether it is a new one.

    The same code on the same physical slots was already announced, so
    announcing it again would only bring back a notification the user
    may have dismissed on purpose.
    """
    announced = allocator._registry.announced_conflicts
    if announced.get(code_ref) == slots:
        return False
    announced[code_ref] = slots
    return True


def withdraw(allocator: DoorCodeAllocator, code_ref: str) -> None:
    """Forget an announcement and dismiss its notification."""
    if allocator._registry.announced_conflicts.pop(code_ref, None) is None:
        return
    _dismiss(allocator, conflict_notification_id(code_ref))


def withdraw_resolved(allocator: DoorCodeAllocator, live: Collection[str]) -> None:
    """Withdraw every announcement whose code is no longer in conflict."""
    for code_ref in set(allocator._registry.announced_conflicts) - set(live):
        withdraw(allocator, code_ref)


def describe_slots(
    allocator: DoorCodeAllocator, owners: Sequence[AllocationOwner]
) -> list[str]:
    """Return one user-facing line per distinct physical slot.

    Several identities adopted on one slot are one code in one place,
    so they share a line, which names the bookings behind it that are
    still in the feed.
    """
    grouped: dict[SlotKey, list[AllocationOwner]] = {}
    for owner in owners:
        grouped.setdefault(physical_slot(owner), []).append(owner)
    lines = []
    for slot in sorted(grouped):
        first = grouped[slot][0]
        line = entry_info.describe_slot(
            allocator.hass, first.entry_id, first.lockname, first.slot
        )
        details = _booking_details(allocator, grouped[slot])
        lines.append(f"{line} ({'; '.join(details)})" if details else line)
    return lines


def _booking_details(
    allocator: DoorCodeAllocator, owners: Sequence[AllocationOwner]
) -> list[str]:
    """Return the distinct booking names and dates behind one slot."""
    details: list[str] = []
    for owner in owners:
        reservation = code_origin.owner_reservation(allocator.hass, owner)
        if reservation is None:
            continue
        detail = _describe_reservation(reservation)
        if detail and detail not in details:
            details.append(detail)
    return details


def _describe_reservation(reservation: Any) -> str:
    """Return a reservation's slot name and dates, as far as known."""
    name = next(
        (
            value.strip()
            for value in (
                getattr(reservation, "display_slot_name", None),
                getattr(reservation, "slot_name", None),
            )
            if isinstance(value, str) and value.strip()
        ),
        "",
    )
    start = _day(getattr(reservation, "start", None))
    end = _day(getattr(reservation, "end", None))
    dates = f"{start} to {end}" if start and end else ""
    return ", ".join(part for part in (name, dates) if part)


def _day(value: Any) -> str:
    """Return a date or datetime as an ISO day, or an empty string."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return ""


def _dismiss(allocator: DoorCodeAllocator, notification_id: str) -> None:
    """Dismiss one notification this module raised."""
    async_dismiss(allocator.hass, notification_id)
