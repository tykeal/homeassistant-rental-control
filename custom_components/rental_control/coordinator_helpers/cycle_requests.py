# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Build the allocator cycle inputs from one entry's observed lock state."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
import uuid

from homeassistant.util import dt

from ..allocator.models import AdoptionRequest
from ..allocator.models import AllocationOrigin
from ..allocator.models import AllocationRequest
from ..allocator.models import CycleObservation
from ..reconciliation import SlotStatus
from ..reconciliation import compute_desired_plan
from ..reconciliation.desired import select_eligible_reservations
from . import reissue
from .models import ObservedSlotQuery
from .reissue import ReissueSuppression
from .slot_matching import find_observed_slot

if TYPE_CHECKING:
    from ..reconciliation import ManagedSlot
    from ..reconciliation import Reservation

_LOGGER = logging.getLogger(__name__)
_ISSUED_ORIGINS = {
    origin.value: origin
    for origin in (AllocationOrigin.PREFERRED, AllocationOrigin.COLLISION_RESOLVED)
}


def active_keys(reservations: list[Reservation], lockname: str | None) -> set[str]:
    """Return the identities whose codes the allocator keeps this cycle."""
    kept = select_eligible_reservations(
        reservations, include_checked_out=lockname is None
    )
    return {reservation.identity_key for reservation in kept}


def build_cycle_observation(
    entry_id: str,
    lockname: str | None,
    managed_slots: list[ManagedSlot],
) -> CycleObservation:
    """Build the allocator's physical-state observation for this entry."""
    managed = [slot for slot in managed_slots if slot.managed]
    observed_codes: dict[str, int] = {}
    for slot in managed:
        if slot.actual_code is not None and slot.status is not SlotStatus.UNKNOWN:
            observed_codes.setdefault(slot.actual_code, slot.slot)
    return CycleObservation(
        entry_id=entry_id,
        lockname=lockname,
        managed_slots=frozenset(slot.slot for slot in managed),
        observed_codes=observed_codes,
        unreadable_slots=frozenset(
            slot.slot for slot in managed if slot.status is SlotStatus.UNKNOWN
        ),
    )


def build_adoption_requests(
    entry_id: str,
    lockname: str | None,
    code_length: int,
    managed_slots: list[ManagedSlot],
    reservations: list[Reservation],
    suppression: ReissueSuppression | None = None,
) -> list[AdoptionRequest]:
    """Return adoption requests for reservations with readable physical codes."""
    suppression = suppression or ReissueSuppression()
    if lockname is None:
        return []
    consumed_slots: set[int] = set()
    requests = reissue.AdoptionRequests()
    for reservation in reservations:
        slot = matched_code_slot(reservation, managed_slots, consumed_slots)
        if slot is None or slot.actual_code is None:
            continue
        if reservation.identity_key in suppression.identity_keys or (
            slot.slot in suppression.slots and reservation.protected_active
        ):
            requests.suppressed_slots.add(slot.slot)
            _reserve_observed_code(requests, entry_id, lockname, reservation, slot)
            continue
        if reservation.code_rotation_pending:
            _LOGGER.info(
                "Skipping adoption for %s in slot %d because its date-based "
                "code must rotate for the updated booking window",
                reservation.identity_key,
                slot.slot,
            )
            requests.suppressed_slots.add(slot.slot)
            _reserve_observed_code(requests, entry_id, lockname, reservation, slot)
            continue
        if not slot.actual_code.isdecimal() or len(slot.actual_code) != code_length:
            _LOGGER.warning(
                "Skipping adoption for %s in slot %d because the observed code "
                "does not match the configured code length",
                reservation.identity_key,
                slot.slot,
            )
            requests.suppressed_slots.add(slot.slot)
            _reserve_observed_code(requests, entry_id, lockname, reservation, slot)
            continue
        requests.append(
            AdoptionRequest(
                entry_id=entry_id,
                identity_key=reservation.identity_key,
                code=slot.actual_code,
                code_length=code_length,
                lockname=lockname,
                slot=slot.slot,
                fingerprint_history=frozenset(reservation.fingerprint_history),
            )
        )
    return requests


def _reserve_observed_code(
    requests: reissue.AdoptionRequests,
    entry_id: str,
    lockname: str,
    reservation: Reservation,
    slot: ManagedSlot,
) -> None:
    """Hold a suppressed slot's observed code against re-issue elsewhere.

    A suppressed slot is exempt from adoption, so nothing records the code
    it still holds. Without a reservation the allocator is free to hand
    that same code to another booking, which would program a duplicate
    code onto the lock. Non-decimal codes are skipped because the
    allocator can never produce one, so they cannot collide.
    """
    code = slot.actual_code
    if not code or not code.isdecimal():
        return
    requests.observed_reservations.append(
        AdoptionRequest(
            entry_id=entry_id,
            identity_key=reservation.identity_key,
            code=code,
            code_length=len(code),
            lockname=lockname,
            slot=slot.slot,
        )
    )


def build_allocation_requests(
    entry_id: str,
    lockname: str | None,
    code_length: int,
    managed_slots: list[ManagedSlot],
    reservations: list[Reservation],
) -> list[AllocationRequest]:
    """Return deterministic allocation requests for selected reservations."""
    planned_slots = _planned_slots(lockname, managed_slots, reservations)
    requests: list[AllocationRequest] = []
    for reservation in sorted(reservations, key=lambda item: item.identity_key):
        if reservation.identity_key not in planned_slots:
            continue
        planned_slot = planned_slots.get(reservation.identity_key)
        if lockname is not None and planned_slot is None:
            continue
        preferred = reservation.slot_code
        if preferred is None and not reservation.published_once:
            continue
        requests.append(
            AllocationRequest(
                entry_id=entry_id,
                identity_key=reservation.identity_key,
                preferred_code=preferred or ("0" * code_length),
                code_length=code_length,
                fingerprint_history=frozenset(reservation.fingerprint_history),
                previously_published=reservation.published_once,
                lockname=lockname if planned_slot is not None else None,
                slot=planned_slot,
                preferred_code_known=preferred is not None,
                issued_origin=_ISSUED_ORIGINS.get(reservation.issued_origin or ""),
            )
        )
    return requests


def build_rekey_requests(reservations: list[Reservation]) -> list[tuple[str, str]]:
    """Return historical fingerprint moves for active reservations."""
    requests: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for reservation in sorted(reservations, key=lambda item: item.identity_key):
        if reservation.code_rotation_pending:
            continue
        for historical_key in sorted(reservation.fingerprint_history):
            item = (historical_key, reservation.identity_key)
            if historical_key != reservation.identity_key and item not in seen:
                requests.append(item)
                seen.add(item)
    return requests


def _planned_slots(
    lockname: str | None,
    managed_slots: list[ManagedSlot],
    reservations: list[Reservation],
) -> dict[str, int | None]:
    """Return planned physical slots for lock-backed selected reservations."""
    if lockname is None:
        return {
            reservation.identity_key: None
            for reservation in select_eligible_reservations(
                reservations, include_checked_out=True
            )
        }
    if not managed_slots:
        return {}
    plan = compute_desired_plan(
        reservations=reservations,
        managed_slots=managed_slots,
        max_events=len([slot for slot in managed_slots if slot.managed]),
        plan_id=str(uuid.uuid4()),
        generated_at=dt.now(),
    )
    return dict(plan.selected)


def matched_code_slot(
    reservation: Reservation,
    managed_slots: list[ManagedSlot],
    consumed_slots: set[int],
) -> ManagedSlot | None:
    """Return the readable coded slot that physically matches a reservation."""
    for slot in managed_slots:
        if (
            slot.managed
            and slot.persisted_identity_key == reservation.identity_key
            and slot.actual_code
            and slot.status is not SlotStatus.UNKNOWN
            and slot.slot not in consumed_slots
        ):
            consumed_slots.add(slot.slot)
            return slot
    matched = find_observed_slot(
        ObservedSlotQuery(
            managed_slots=managed_slots,
            slot_name=reservation.slot_name,
            display_slot_name=reservation.display_slot_name,
            consumed_slots=consumed_slots,
            desired_start=reservation.buffered_start,
            desired_end=reservation.buffered_end,
            event_prefix="",
        )
    )
    if (
        matched is not None
        and matched.actual_code
        and matched.status is not SlotStatus.UNKNOWN
    ):
        return matched
    return None


__all__ = [
    "active_keys",
    "build_adoption_requests",
    "build_allocation_requests",
    "build_cycle_observation",
    "build_rekey_requests",
    "matched_code_slot",
]
