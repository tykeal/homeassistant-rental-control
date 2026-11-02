# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Apply allocator cycle results to one entry's in-cycle reservations."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..allocator.models import AllocationOrigin
from ..allocator.models import AllocationResult
from ..allocator.models import CycleResult

if TYPE_CHECKING:
    from ..reconciliation import Reservation

_LOGGER = logging.getLogger(__name__)


def apply_adoption_results(
    allocator: object,
    entry_id: str,
    reservations: list[Reservation],
    result: CycleResult,
) -> None:
    """Apply and log adoption results from a cycle."""
    for identity_key, adoption_result in result.adopted.items():
        if adoption_result.code is None:
            continue
        _LOGGER.info(
            "Adopted shared code_ref %s for entry %s identity %s",
            _allocator_code_ref(allocator, adoption_result.code),
            entry_id,
            identity_key,
        )
        _apply_result(reservations, identity_key, adoption_result)


def apply_allocation_results(
    allocator: object,
    entry_id: str,
    reservations: list[Reservation],
    result: CycleResult,
) -> bool:
    """Apply and log allocation results; return whether space exhausted."""
    exhausted = False
    for identity_key, allocation_result in result.allocated.items():
        if identity_key in result.adopted:
            continue
        if allocation_result.code is None:
            exhausted = exhausted or allocation_result.reason == "exhausted"
            continue
        _LOGGER.info(
            "Resolved shared code_ref %s for entry %s identity %s origin %s",
            _allocator_code_ref(allocator, allocation_result.code),
            entry_id,
            identity_key,
            allocation_result.origin,
        )
        _apply_result(reservations, identity_key, allocation_result)
    return exhausted


def _apply_result(
    reservations: list[Reservation],
    identity_key: str,
    result: AllocationResult,
) -> None:
    """Write an allocator result back to the in-cycle reservation object.

    The registry hands an identity its recorded code with the origin it
    had when first issued, even after the generator's preference has
    since drifted. Only a code equal to today's preference is recorded
    as preferred, and a known mismatch is recorded as unknown, so
    recovery withholds it. A ghost carries no preference and keeps the
    origin it was hydrated with.
    """
    for reservation in reservations:
        if reservation.identity_key != identity_key:
            continue
        preference = reservation.slot_code
        reservation.slot_code = result.code
        if result.origin is AllocationOrigin.ADOPTED:
            reservation.code_source = "adopted"
        elif result.origin is AllocationOrigin.PREFERRED:
            reservation.code_source = "allocated"
            if preference is not None:
                reservation.issued_origin = (
                    AllocationOrigin.PREFERRED.value
                    if result.code == preference
                    else None
                )
        elif result.origin is AllocationOrigin.COLLISION_RESOLVED:
            reservation.code_source = "collision_resolved"
            reservation.issued_origin = AllocationOrigin.COLLISION_RESOLVED.value
        elif result.origin is not None:
            reservation.code_source = result.origin.value
        else:
            reservation.code_source = "unallocated"
        return


def _allocator_code_ref(allocator: object, code: str) -> str:
    """Return a masked allocator code reference for logs."""
    code_ref = getattr(allocator, "code_ref", None)
    if callable(code_ref):
        return str(code_ref(code))
    return "<unavailable>"


def withhold_codes(reservations: list[Reservation]) -> None:
    """Hold every reservation codeless for this cycle."""
    for reservation in reservations:
        reservation.slot_code = None
        reservation.code_source = "unallocated"


def clear_unresolved(reservations: list[Reservation], result: CycleResult) -> None:
    """Hold codeless every reservation the cycle neither adopted nor issued."""
    for reservation in reservations:
        if reservation.identity_key in result.adopted or (
            reservation.identity_key in result.allocated
            and result.allocated[reservation.identity_key].code is not None
        ):
            continue
        reservation.slot_code = None
        reservation.code_source = "unallocated"


__all__ = [
    "apply_adoption_results",
    "apply_allocation_results",
    "clear_unresolved",
    "withhold_codes",
]
