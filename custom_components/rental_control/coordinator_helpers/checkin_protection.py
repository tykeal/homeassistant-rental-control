# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Pure check-in protection decisions.

The coordinator shell reads the check-in tracking sensor from
``hass.data``, applies buffers, and performs slot matching. This module
holds the pure decision logic: choosing the reservation to protect,
synthesizing an active stay, and deciding whether apply must defer for a
pending check-in restore.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from ..reconciliation import ManagedSlot
from ..reconciliation import Reservation
from ..reconciliation import SlotStatus
from .models import CheckinProtectionSnapshot


def select_checkin_match(
    reservations: list[Reservation],
    guest_name: str,
    tracked_start: datetime | None,
    tracked_end: datetime | None,
) -> Reservation | None:
    """Return the reservation that matches the tracked check-in, if any.

    Exact start/end matches win for duplicate names. When tracked times are
    unavailable, a unique name match is allowed.
    """
    name_matches = [res for res in reservations if res.slot_name == guest_name]
    exact_matches = [
        res
        for res in name_matches
        if tracked_start is not None
        and tracked_end is not None
        and res.start == tracked_start
        and res.end == tracked_end
    ]
    matches = exact_matches
    if tracked_start is None or tracked_end is None:
        matches = name_matches if len(name_matches) == 1 else []
    return matches[0] if matches else None


def build_protected_reservation(
    snapshot: CheckinProtectionSnapshot,
    matched_physical: ManagedSlot | None,
    same_name_count: int,
    window: tuple[datetime, datetime],
    identity: tuple[str, str],
    display_slot_name: str,
) -> Reservation | None:
    """Synthesize a protected active reservation, or None when unsafe.

    Args:
        snapshot: Parsed check-in sensor snapshot.
        matched_physical: Physical slot matched to the active guest.
        same_name_count: Count of occupied physical slots sharing the name.
        window: Buffered ``(start, end)`` window for the synthesized stay.
        identity: ``(identity_key, slot_code)`` for the synthesized stay.
        display_slot_name: Display name for the synthesized reservation.

    Returns:
        The synthesized :class:`Reservation`, or ``None`` when no safe
        physical match exists.
    """
    if matched_physical is None:
        return None
    if snapshot.start is None or snapshot.end is None:
        return None
    buffered_start, buffered_end = window
    if (
        matched_physical.actual_start is not None
        and matched_physical.actual_end is not None
        and (
            matched_physical.actual_start != buffered_start
            or matched_physical.actual_end != buffered_end
        )
        and same_name_count != 1
    ):
        return None
    identity_key, slot_code = identity
    protected = Reservation(
        identity_key=identity_key,
        start=snapshot.start,
        end=snapshot.end,
        buffered_start=buffered_start,
        buffered_end=buffered_end,
        summary=snapshot.summary,
        slot_name=snapshot.guest_name,
        display_slot_name=display_slot_name,
        slot_code=slot_code,
        protected_active=True,
        code_source=(
            "manual_observed"
            if matched_physical.actual_code is not None
            else "generated"
        ),
    )
    protected.sensor_lookup_keys.add(identity_key)
    return protected


def _slot_blocks_restore(
    slot: ManagedSlot,
    reservations: list[Reservation],
    matches: Callable[[str | None, Reservation], bool],
) -> bool:
    """Return whether a managed slot blocks a deferred check-in restore."""
    if not (slot.managed and slot.status is SlotStatus.OCCUPIED):
        return False
    if not any(matches(slot.actual_name, res) for res in reservations):
        return True
    window_mismatch = (
        slot.actual_start is None
        or slot.actual_end is None
        or not any(
            slot.actual_start == res.buffered_start
            and slot.actual_end == res.buffered_end
            for res in reservations
            if matches(slot.actual_name, res)
        )
    )
    return window_mismatch


def should_defer_restore(
    reservations: list[Reservation],
    managed_slots: list[ManagedSlot],
    matches: Callable[[str | None, Reservation], bool],
) -> bool:
    """Return whether apply should wait for the check-in sensor restore."""
    return any(
        _slot_blocks_restore(slot, reservations, matches) for slot in managed_slots
    )


def checkin_windows(
    tracked_start: datetime,
    tracked_end: datetime,
    buffered_start: datetime,
    buffered_end: datetime,
) -> set[tuple[datetime, datetime]]:
    """Return the active check-in windows physical slots must reserve."""
    return {(tracked_start, tracked_end), (buffered_start, buffered_end)}
