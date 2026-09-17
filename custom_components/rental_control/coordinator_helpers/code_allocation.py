# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Allocator adoption step for coordinator refresh cycles."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
import uuid

from homeassistant.components.persistent_notification import async_create
from homeassistant.core import HomeAssistant
from homeassistant.util import dt

from ..allocator import get_allocator
from ..allocator.models import AdoptionRequest
from ..allocator.models import AllocationOrigin
from ..allocator.models import AllocationRequest
from ..allocator.models import AllocationResult
from ..allocator.models import CycleObservation
from ..allocator.models import CycleRequest
from ..const import DOMAIN
from ..const import NAME
from ..reconciliation import SlotStatus
from ..reconciliation import compute_desired_plan
from ..reconciliation.desired import select_eligible_reservations
from .models import ObservedSlotQuery
from .slot_matching import find_observed_slot

if TYPE_CHECKING:
    from ..reconciliation import ManagedSlot
    from ..reconciliation import Reservation

_LOGGER = logging.getLogger(__name__)
_EXHAUSTED_NOTIFICATION_ID = f"{DOMAIN}_code_registry_exhausted"


async def async_resolve_codes(
    hass: HomeAssistant,
    entry_id: str,
    lockname: str | None,
    code_length: int,
    managed_slots: list[ManagedSlot],
    reservations: list[Reservation],
) -> CycleObservation:
    """Adopt observed lock codes and allocate unique reservation codes."""
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
    adoption_complete = _adoption_complete(observation, adoptions, managed_slots)
    allocations = (
        build_allocation_requests(
            entry_id, lockname, code_length, managed_slots, reservations
        )
        if adoption_complete
        else []
    )
    result = await allocator.async_resolve_cycle(
        CycleRequest(
            observation=observation,
            adoptions=adoptions,
            rekeys=[],
            allocations=allocations,
            active_keys={reservation.identity_key for reservation in reservations},
            adoption_complete=adoption_complete,
        )
    )
    for identity_key, adoption_result in result.adopted.items():
        if adoption_result.code is not None:
            _apply_result(reservations, identity_key, adoption_result)
    if result.unaccounted_slots:
        _LOGGER.warning(
            "Shared code allocator withheld issuance for entry %s because "
            "managed slots are unreadable and unaccounted: %s",
            entry_id,
            sorted(result.unaccounted_slots),
        )
    exhausted = False
    for identity_key, allocation_result in result.allocated.items():
        if identity_key in result.adopted:
            continue
        if allocation_result.code is None:
            exhausted = exhausted or allocation_result.reason == "exhausted"
            continue
        _apply_result(reservations, identity_key, allocation_result)
    if exhausted:
        message = (
            f"Shared code allocator exhausted the configured code space for "
            f"entry {entry_id}. New duplicate codes were not issued."
        )
        _LOGGER.warning(message)
        async_create(
            hass,
            message,
            title=f"{NAME} code allocation exhausted",
            notification_id=f"{_EXHAUSTED_NOTIFICATION_ID}_{entry_id}",
        )
    for reservation in reservations:
        if reservation.identity_key in result.adopted or (
            reservation.identity_key in result.allocated
            and result.allocated[reservation.identity_key].code is not None
        ):
            continue
        reservation.slot_code = None
        reservation.code_source = "unallocated"
    return observation


def _adoption_complete(
    observation: CycleObservation,
    adoptions: list[AdoptionRequest],
    managed_slots: list[ManagedSlot],
) -> bool:
    """Return whether all readable managed codes were accounted for."""
    if observation.unreadable_slots:
        return False
    adopted_slots = {request.slot for request in adoptions}
    readable_coded_slots = {
        slot.slot
        for slot in managed_slots
        if slot.managed and slot.status is not SlotStatus.UNKNOWN and slot.actual_code
    }
    return readable_coded_slots <= adopted_slots


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
            )
        )
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
            for reservation in select_eligible_reservations(reservations)
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


def _apply_result(
    reservations: list[Reservation],
    identity_key: str,
    result: AllocationResult,
) -> None:
    """Write an allocator result back to the in-cycle reservation object."""
    for reservation in reservations:
        if reservation.identity_key != identity_key:
            continue
        reservation.slot_code = result.code
        if result.origin is AllocationOrigin.ADOPTED:
            reservation.code_source = "adopted"
        elif result.origin is AllocationOrigin.PREFERRED:
            reservation.code_source = "allocated"
        elif result.origin is AllocationOrigin.COLLISION_RESOLVED:
            reservation.code_source = "collision_resolved"
        elif result.origin is not None:
            reservation.code_source = result.origin.value
        else:
            reservation.code_source = "unallocated"
        return
