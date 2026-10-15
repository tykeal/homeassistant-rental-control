# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Safety gate for date-shift door code rotation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from ..reconciliation import ManagedSlot
    from ..reconciliation import Reservation

_LOGGER = logging.getLogger(__name__)

_SlotMatcher = Callable[
    ["Reservation", list["ManagedSlot"], set[int]], "ManagedSlot | None"
]


@dataclass
class RotationGuardTarget:
    """The allocator and lock the rotation gate is being applied to."""

    allocator: object
    entry_id: str
    lockname: str | None
    managed_slots: list[ManagedSlot]


def require_owned_code_for_rotation(
    target: RotationGuardTarget,
    reservations: list[Reservation],
    match_slot: _SlotMatcher,
) -> None:
    """Give up rotation unless this very slot already owns the code.

    Skipping adoption also stops the observed code from being rebuilt as
    an owned allocation.  That is safe only when the registry already
    records an owner for this entry, lock and slot: the release guard
    then retains the code while it stays physically programmed here, so
    it cannot be issued to another booking before the replacement write
    is confirmed.

    Ownership held by some *other* entry, lock or slot is not enough.
    The same PIN can legitimately be programmed on two locks, and
    ``_release_guard_reason`` only inspects the lock recorded on the
    owner it is releasing.  Were the other owner swept away, the PIN
    would become issuable while still programmed on this slot.  Any
    code without a matching local owner - including a lost or
    still-recovering registry - therefore gives way to adoption and the
    existing code is retained.

    The rotation is given up rather than retried, because adoption
    binds the observed code to the shifted identity and the same cycle
    pushes the new window to the lock, after which the observed code
    reads as a manual PIN.  Retrying would need rotation intent to
    outlive the cycle, and durable cross-cycle slot state is exactly
    what the 3.5.0 regression established as unsafe here.  The outcome
    is the pre-existing retain-the-code behaviour, so nothing is made
    worse; an operator who wants the code changed can call
    ``rental_control.force_reissue``.
    """
    if not any(reservation.code_rotation_pending for reservation in reservations):
        return
    records = _registry_records(target.allocator)
    consumed: set[int] = set()
    for reservation in reservations:
        slot = match_slot(reservation, target.managed_slots, consumed)
        if not reservation.code_rotation_pending:
            continue
        if (
            slot is not None
            and _is_rotation_origin(reservation, slot)
            and _owned_by_slot(records, target, slot)
        ):
            continue
        reservation.code_rotation_pending = False
        _LOGGER.warning(
            "Keeping the existing door code for entry %s identity %s despite "
            "a booking date change because the shared code registry does not "
            "own the programmed code on this lock slot; use "
            "rental_control.force_reissue to change it",
            target.entry_id,
            reservation.identity_key,
        )


def _registry_records(allocator: object) -> dict[str, Any]:
    """Return the shared registry records keyed by code."""
    registry = getattr(allocator, "_registry", None)
    records = getattr(registry, "records", None)
    return records if isinstance(records, dict) else {}


def _is_rotation_origin(reservation: Reservation, slot: ManagedSlot) -> bool:
    """Return whether the allocator matched the slot the signal came from.

    The builder pairs same-name stays by date order while the allocator
    matches by identity, so a reordered pair can hand a reservation a
    different physical slot than the one whose code produced the
    rotation signal.  Suppressing adoption of that other slot could
    overwrite an unrelated PIN, so rotation only survives when both
    agree on the slot and the code.
    """
    origin = reservation.code_rotation_origin
    return origin is not None and origin == (slot.slot, slot.actual_code)


def _owned_by_slot(
    records: dict[str, Any], target: RotationGuardTarget, slot: ManagedSlot
) -> bool:
    """Return whether the registry owns this code for this entry/lock/slot."""
    record = records.get(slot.actual_code) if slot.actual_code else None
    return any(
        owner.entry_id == target.entry_id
        and owner.lockname == target.lockname
        and owner.slot == slot.slot
        for owner in getattr(record, "owners", None) or ()
    )
