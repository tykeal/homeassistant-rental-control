# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Allocator adoption step for coordinator refresh cycles."""

from __future__ import annotations

from dataclasses import replace
import logging
from typing import TYPE_CHECKING
import uuid

from homeassistant.components.persistent_notification import async_create
from homeassistant.components.persistent_notification import async_dismiss
from homeassistant.util import dt

from ..allocator import get_allocator
from ..allocator.models import AdoptionRequest
from ..allocator.models import AllocationOrigin
from ..allocator.models import AllocationRequest
from ..allocator.models import AllocationResult
from ..allocator.models import CycleObservation
from ..allocator.models import CycleRequest
from ..allocator.models import CycleResult
from ..const import DOMAIN
from ..const import NAME
from ..reconciliation import SlotStatus
from ..reconciliation import compute_desired_plan
from ..reconciliation.desired import select_eligible_reservations
from . import reissue
from . import slot_exclusions
from .models import ObservedSlotQuery
from .reissue import CodeResolutionRequest
from .reissue import CodeResolutionResult
from .reissue import ReissueSuppression
from .rotation_guard import RotationGuardTarget
from .rotation_guard import require_owned_code_for_rotation
from .slot_matching import find_observed_slot

if TYPE_CHECKING:
    from ..reconciliation import ManagedSlot
    from ..reconciliation import Reservation

_LOGGER = logging.getLogger(__name__)
_EXHAUSTED_NOTIFICATION_ID = f"{DOMAIN}_code_registry_exhausted"
_UNREADABLE_NOTIFICATION_ID = f"{DOMAIN}_unreadable_slots"


async def async_resolve_codes(
    request: CodeResolutionRequest | object,
    entry_id: str | None = None,
    lockname: str | None = None,
    code_length: int | None = None,
    managed_slots: list[ManagedSlot] | None = None,
    reservations: list[Reservation] | None = None,
) -> CodeResolutionResult:
    """Adopt observed lock codes and allocate unique reservation codes."""
    request = reissue.coerce_code_resolution_request(
        request, entry_id, lockname, code_length, managed_slots, reservations
    )
    hass = request.hass
    entry_id = request.entry_id
    lockname = request.lockname
    code_length = request.code_length
    managed_slots = request.managed_slots
    reservations = request.reservations
    consumed_target_keys = tuple(
        reissue.target_key(directive.identity_key, directive.lockname, directive.slot)
        for directive in request.forced_reissues
    )
    observation = build_cycle_observation(entry_id, lockname, managed_slots)
    exclusions = _revalidated_exclusions(hass, entry_id, lockname, observation)
    request = replace(request, excluded_slots=frozenset(exclusions))
    allocator = get_allocator(hass)
    if allocator is None:
        for reservation in reservations:
            reservation.slot_code = None
            reservation.code_source = "unallocated"
        return CodeResolutionResult(observation, (), consumed_target_keys)
    require_owned_code_for_rotation(
        RotationGuardTarget(allocator, entry_id, lockname, managed_slots),
        reservations,
        _matched_code_slot,
    )
    adoptions = build_adoption_requests(
        entry_id,
        lockname,
        code_length,
        managed_slots,
        reservations,
        request.suppression,
    )
    adoption_complete = _adoption_complete(
        observation,
        adoptions,
        managed_slots,
        request.suppression,
        excluded=request.excluded_slots,
    )
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
            rekeys=build_rekey_requests(reservations),
            allocations=allocations,
            active_keys={
                reservation.identity_key
                for reservation in select_eligible_reservations(reservations)
            },
            adoption_complete=adoption_complete,
            forced_reissues=request.forced_reissues,
            excluded_slots=request.excluded_slots,
        )
    )
    _apply_adoption_results(allocator, entry_id, reservations, result)
    if result.unaccounted_slots:
        _LOGGER.warning(
            "Shared code allocator withheld issuance for entry %s because "
            "managed slots are unreadable and unaccounted: %s",
            entry_id,
            sorted(result.unaccounted_slots),
        )
    report_unreadable_slots(
        hass,
        entry_id,
        lockname,
        sorted(observation.unreadable_slots - request.excluded_slots),
    )
    if _apply_allocation_results(allocator, entry_id, reservations, result):
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
    return CodeResolutionResult(
        observation, getattr(result, "reissues", ()), consumed_target_keys
    )


def _revalidated_exclusions(
    hass: object,
    entry_id: str,
    lockname: str | None,
    observation: CycleObservation,
) -> dict[int, str | None]:
    """Re-derive an entry's exclusions from the observation this cycle used."""
    stored = slot_exclusions.load(hass, entry_id)
    if not stored:
        return {}
    kept, cleared = slot_exclusions.revalidate(stored, observation)
    slot_exclusions.report_excluded_slots(hass, entry_id, lockname, kept)
    for slot, reason in sorted(cleared.items()):
        _LOGGER.info(
            "Cleared the slot exclusion for entry %s slot %s on lock %s "
            "because the slot is %s (recorded reason: %s)",
            entry_id,
            slot,
            lockname or "unknown lock",
            "no longer in the managed range"
            if slot not in observation.managed_slots
            else "readable again",
            reason or "none given",
        )
    if cleared:
        slot_exclusions.persist_excluded_slots(hass, entry_id, kept)
    return kept


def unreadable_slots_notification_id(entry_id: str) -> str:
    """Return the persistent notification id for an entry's unreadable slots."""
    return f"{_UNREADABLE_NOTIFICATION_ID}_{entry_id}"


def dismiss_unreadable_slots(hass: object, entry_id: str) -> None:
    """Clear an entry's unreadable-slot notification."""
    if not hasattr(hass, "bus"):
        return
    async_dismiss(hass, unreadable_slots_notification_id(entry_id))


def report_unreadable_slots(
    hass: object,
    entry_id: str,
    lockname: str | None,
    slots: list[int],
) -> None:
    """Reconcile the persistent notification for unreadable managed slots."""
    if not hasattr(hass, "bus"):
        return
    if not slots:
        dismiss_unreadable_slots(hass, entry_id)
        return
    slot_list = ", ".join(str(slot) for slot in slots)
    label = "slots" if len(slots) > 1 else "slot"
    lock_label = lockname or "unknown lock"
    message = (
        f"Rental Control entry {entry_id} cannot read managed {label} "
        f"{slot_list} on lock {lock_label}. Door code issuance is withheld "
        "while those slots stay unreadable, because an unreadable slot may "
        "still hold a code the integration cannot see. If this condition is "
        "present when Home Assistant starts, issuance is withheld for ALL "
        "Rental Control entries, not just this one, because every entry "
        "shares one door code space. To recover, restore readable Keymaster "
        f"entities for {label} {slot_list}; deleting those entities does not "
        "help, because the slots stay in the managed range and report as "
        "unknown. If the slots are gone for good, reconfigure this entry's "
        "start slot or event count so that the managed range no longer "
        "covers them, or call the rental_control.set_slot_exclusion action "
        "to exclude each dead slot from the issuance gate."
    )
    async_create(
        hass,
        message,
        title=f"{NAME} unreadable lock slots",
        notification_id=unreadable_slots_notification_id(entry_id),
    )


def _apply_adoption_results(
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


def _apply_allocation_results(
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


def _adoption_complete(
    observation: CycleObservation,
    adoptions: list[AdoptionRequest],
    managed_slots: list[ManagedSlot],
    suppression: ReissueSuppression | None = None,
    *,
    excluded: frozenset[int] = frozenset(),
) -> bool:
    """Return whether all readable managed codes were accounted for."""
    suppression = suppression or ReissueSuppression()
    if observation.unreadable_slots - excluded:
        return False
    adopted_slots = {request.slot for request in adoptions}
    readable_coded_slots = {
        slot.slot
        for slot in managed_slots
        if slot.managed and slot.status is not SlotStatus.UNKNOWN and slot.actual_code
    }
    readable_coded_slots -= suppression.slots
    readable_coded_slots -= getattr(adoptions, "suppressed_slots", set())
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
    suppression: ReissueSuppression | None = None,
) -> list[AdoptionRequest]:
    """Return adoption requests for reservations with readable physical codes."""
    suppression = suppression or ReissueSuppression()
    if lockname is None:
        return []
    consumed_slots: set[int] = set()
    requests = reissue.AdoptionRequests()
    for reservation in reservations:
        slot = _matched_code_slot(reservation, managed_slots, consumed_slots)
        if slot is None or slot.actual_code is None:
            continue
        if reservation.identity_key in suppression.identity_keys or (
            slot.slot in suppression.slots and reservation.protected_active
        ):
            requests.suppressed_slots.add(slot.slot)
            continue
        if reservation.code_rotation_pending:
            _LOGGER.info(
                "Skipping adoption for %s in slot %d because its date-based "
                "code must rotate for the updated booking window",
                reservation.identity_key,
                slot.slot,
            )
            requests.suppressed_slots.add(slot.slot)
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


def _allocator_code_ref(allocator: object, code: str) -> str:
    """Return a masked allocator code reference for logs."""
    code_ref = getattr(allocator, "code_ref", None)
    if callable(code_ref):
        return str(code_ref(code))
    return "<unavailable>"
