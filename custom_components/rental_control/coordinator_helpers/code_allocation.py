# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Allocator adoption step for coordinator refresh cycles."""

from __future__ import annotations

from dataclasses import replace
import logging
from typing import TYPE_CHECKING

from homeassistant.components.persistent_notification import async_create

from ..allocator import code_origin
from ..allocator import entry_info
from ..allocator import get_allocator
from ..allocator.models import AllocationOrigin
from ..allocator.models import AllocationResult
from ..allocator.models import CycleRequest
from ..allocator.models import CycleResult
from ..const import DOMAIN
from ..const import NAME
from . import adoption_gate
from . import cycle_requests
from . import generator_divergence
from . import reissue
from . import slot_exclusions
from . import unreadable_slots
from .reissue import CodeResolutionRequest
from .reissue import CodeResolutionResult
from .rotation_guard import RotationGuardTarget
from .rotation_guard import require_owned_code_for_rotation

if TYPE_CHECKING:
    from ..reconciliation import ManagedSlot
    from ..reconciliation import Reservation

_LOGGER = logging.getLogger(__name__)
_EXHAUSTED_NOTIFICATION_ID = f"{DOMAIN}_code_registry_exhausted"


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
    observation = cycle_requests.build_cycle_observation(
        entry_id, lockname, managed_slots
    )
    exclusions = slot_exclusions.revalidate_persisted(
        hass, entry_id, lockname, observation
    )
    slot_exclusions.report_excluded_slots(hass, entry_id, lockname, exclusions)
    request = replace(request, excluded_slots=frozenset(exclusions))
    allocator = get_allocator(hass)
    if allocator is None:
        _LOGGER.error(
            "Rental Control entry %s cannot issue door codes because the "
            "shared door code allocator is not available. No door codes will "
            "be created or updated for any entry until Home Assistant is "
            "restarted; please report this with the preceding log lines",
            entry_id,
        )
        for reservation in reservations:
            reservation.slot_code = None
            reservation.code_source = "unallocated"
        return CodeResolutionResult(observation, (), consumed_target_keys)
    require_owned_code_for_rotation(
        RotationGuardTarget(allocator, entry_id, lockname, managed_slots),
        reservations,
        cycle_requests.matched_code_slot,
    )
    code_origin.record_cycle_evidence(entry_id, reservations)
    request = generator_divergence.rotate_stale_codes(
        request, allocator, cycle_requests.matched_code_slot
    )
    adoptions = cycle_requests.build_adoption_requests(
        entry_id,
        lockname,
        code_length,
        managed_slots,
        reservations,
        request.suppression,
    )
    gate_status = adoption_gate.evaluate(
        observation,
        adoptions,
        managed_slots,
        request.suppression,
        excluded=request.excluded_slots,
    )
    adoption_gate.report(entry_id, lockname, gate_status)
    adoption_complete = gate_status.complete
    allocations = (
        cycle_requests.build_allocation_requests(
            entry_id, lockname, code_length, managed_slots, reservations
        )
        if adoption_complete
        else []
    )
    result = await allocator.async_resolve_cycle(
        CycleRequest(
            observation=observation,
            adoptions=adoptions,
            rekeys=cycle_requests.build_rekey_requests(reservations),
            allocations=allocations,
            active_keys=cycle_requests.active_keys(reservations, lockname),
            adoption_complete=adoption_complete,
            forced_reissues=request.forced_reissues,
            excluded_slots=request.excluded_slots,
            observed_reservations=tuple(
                getattr(adoptions, "observed_reservations", ())
            ),
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
    unreadable_slots.report_unreadable_slots(
        hass,
        entry_id,
        lockname,
        sorted(observation.unreadable_slots - request.excluded_slots),
    )
    if _apply_allocation_results(allocator, entry_id, reservations, result):
        _LOGGER.warning(
            "Shared code allocator exhausted the configured code space for "
            "entry %s. New duplicate codes were not issued.",
            entry_id,
        )
        name = entry_info.entry_name(hass, entry_id)
        async_create(
            hass,
            (
                f"Rental Control ran out of unused door codes for {name}, so "
                "no code was issued for at least one booking. Shorten the "
                "booking window, reduce the number of tracked events, or use "
                "a longer code length to free up codes."
            ),
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
