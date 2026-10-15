# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Safety gate for date-shift door code rotation."""

from __future__ import annotations

from collections.abc import Callable
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..reconciliation import ManagedSlot
    from ..reconciliation import Reservation

_LOGGER = logging.getLogger(__name__)

_SlotMatcher = Callable[
    ["Reservation", list["ManagedSlot"], set[int]], "ManagedSlot | None"
]


def require_owned_code_for_rotation(
    allocator: object,
    entry_id: str,
    managed_slots: list[ManagedSlot],
    reservations: list[Reservation],
    match_slot: _SlotMatcher,
) -> None:
    """Give up rotation unless the registry already owns the observed code.

    Skipping adoption also stops the observed code from being rebuilt as
    an owned allocation.  That is safe only when the registry already
    owns it: the release guard then retains it while it stays
    physically programmed, so it cannot be issued to another booking
    before the replacement write is confirmed.  An unowned code - a
    lost or still-recovering registry, or a slot this allocator has
    never seen - has no such protection, so rotation gives way to
    adoption and the existing code is retained.

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
    owned = _owned_registry_codes(allocator)
    consumed: set[int] = set()
    for reservation in reservations:
        slot = match_slot(reservation, managed_slots, consumed)
        if not reservation.code_rotation_pending:
            continue
        if slot is not None and slot.actual_code in owned:
            continue
        reservation.code_rotation_pending = False
        _LOGGER.warning(
            "Keeping the existing door code for entry %s identity %s despite "
            "a booking date change because the shared code registry does not "
            "own the programmed code; use rental_control.force_reissue to "
            "change it",
            entry_id,
            reservation.identity_key,
        )


def _owned_registry_codes(allocator: object) -> frozenset[str]:
    """Return codes the shared registry currently owns for some identity."""
    registry = getattr(allocator, "_registry", None)
    records = getattr(registry, "records", None)
    if not isinstance(records, dict):
        return frozenset()
    return frozenset(
        code for code, record in records.items() if getattr(record, "owners", None)
    )
