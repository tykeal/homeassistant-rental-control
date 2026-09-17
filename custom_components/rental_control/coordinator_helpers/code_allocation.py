# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Allocator adoption step for coordinator refresh cycles."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant

from ..allocator import get_allocator
from ..allocator.models import AdoptionRequest
from ..allocator.models import AllocationOrigin
from ..allocator.models import AllocationResult
from ..allocator.models import CycleObservation
from ..reconciliation import SlotStatus
from .models import ObservedSlotQuery
from .slot_matching import find_observed_slot

if TYPE_CHECKING:
    from ..reconciliation import ManagedSlot
    from ..reconciliation import Reservation

_LOGGER = logging.getLogger(__name__)


async def async_resolve_codes(
    hass: HomeAssistant,
    entry_id: str,
    lockname: str | None,
    code_length: int,
    managed_slots: list[ManagedSlot],
    reservations: list[Reservation],
) -> CycleObservation:
    """Adopt observed lock codes and hold unadopted reservations codeless."""
    observation = build_cycle_observation(entry_id, lockname, managed_slots)
    allocator = get_allocator(hass)
    if allocator is None:
        for reservation in reservations:
            reservation.slot_code = None
            reservation.code_source = "unallocated"
        return observation
    adoptions = build_adoption_requests(
        entry_id, lockname, code_length, managed_slots, reservations
    )
    adopted: dict[str, AllocationResult] = {}
    for request in adoptions:
        result = await allocator.async_adopt(request)
        adopted[request.identity_key] = result
        if result.code is not None:
            _apply_adoption_result(reservations, request.identity_key, result)
    if _adoption_complete(observation, adoptions):
        await allocator.async_unregister_entry(entry_id)
    for reservation in reservations:
        if reservation.identity_key in adopted:
            continue
        reservation.slot_code = None
        reservation.code_source = "unallocated"
    return observation


def _adoption_complete(
    observation: CycleObservation,
    adoptions: list[AdoptionRequest],
) -> bool:
    """Return whether all readable managed codes were accounted for."""
    if observation.unreadable_slots:
        return False
    adopted_slots = {request.slot for request in adoptions}
    return set(observation.observed_codes.values()) <= adopted_slots


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
) -> list[AdoptionRequest]:
    """Return adoption requests for reservations with readable physical codes."""
    if lockname is None:
        return []
    consumed_slots: set[int] = set()
    requests: list[AdoptionRequest] = []
    for reservation in reservations:
        slot = _matched_code_slot(reservation, managed_slots, consumed_slots)
        if slot is None or slot.actual_code is None:
            continue
        if not slot.actual_code.isdecimal() or len(slot.actual_code) != code_length:
            _LOGGER.warning(
                "Skipping adoption for %s in slot %d because the observed code "
                "does not match the configured code length",
                reservation.identity_key,
                slot.slot,
            )
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


def _matched_code_slot(
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


def _apply_adoption_result(
    reservations: list[Reservation],
    identity_key: str,
    result: AllocationResult,
) -> None:
    """Write an adoption result back to the in-cycle reservation object."""
    for reservation in reservations:
        if reservation.identity_key != identity_key:
            continue
        reservation.slot_code = result.code
        if result.origin is AllocationOrigin.ADOPTED:
            reservation.code_source = "adopted"
        elif result.origin is not None:
            reservation.code_source = result.origin.value
        return
