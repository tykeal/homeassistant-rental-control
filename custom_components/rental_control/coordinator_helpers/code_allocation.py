# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Allocator adoption step for coordinator refresh cycles."""

from __future__ import annotations

from dataclasses import replace
import logging
from typing import TYPE_CHECKING
from typing import Any

from homeassistant.components.persistent_notification import async_create

from ..allocator import code_origin
from ..allocator import entry_info
from ..allocator import get_allocator
from ..allocator.models import CycleObservation
from ..allocator.models import CycleRequest
from ..allocator.models import CycleResult
from ..const import DOMAIN
from ..const import NAME
from . import adoption_gate
from . import cycle_requests
from . import cycle_results
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
    consumed_target_keys = tuple(
        reissue.target_key(directive.identity_key, directive.lockname, directive.slot)
        for directive in request.forced_reissues
    )
    observation = cycle_requests.build_cycle_observation(
        request.entry_id, request.lockname, request.managed_slots
    )
    request = _with_revalidated_exclusions(request, observation)
    allocator = get_allocator(request.hass)
    if allocator is None:
        _withhold_without_allocator(request)
        return CodeResolutionResult(observation, (), consumed_target_keys)
    request = _guard_and_rotate(request, allocator)
    result = await allocator.async_resolve_cycle(
        _gated_cycle_request(request, observation)
    )
    _record_cycle_outcome(request, observation, allocator, result)
    return CodeResolutionResult(
        observation, getattr(result, "reissues", ()), consumed_target_keys
    )


def _with_revalidated_exclusions(
    request: CodeResolutionRequest, observation: CycleObservation
) -> CodeResolutionRequest:
    """Return the request carrying this cycle's still-justified exclusions."""
    exclusions = slot_exclusions.revalidate_persisted(
        request.hass, request.entry_id, request.lockname, observation
    )
    slot_exclusions.report_excluded_slots(
        request.hass, request.entry_id, request.lockname, exclusions
    )
    return replace(request, excluded_slots=frozenset(exclusions))


def _withhold_without_allocator(request: CodeResolutionRequest) -> None:
    """Log the missing shared allocator and hold every reservation codeless."""
    _LOGGER.error(
        "Rental Control entry %s cannot issue door codes because the "
        "shared door code allocator is not available. No door codes will "
        "be created or updated for any entry until Home Assistant is "
        "restarted; please report this with the preceding log lines",
        request.entry_id,
    )
    cycle_results.withhold_codes(request.reservations)


def _guard_and_rotate(
    request: CodeResolutionRequest, allocator: Any
) -> CodeResolutionRequest:
    """Enforce owned-code rotation and add earned generator rotations."""
    require_owned_code_for_rotation(
        RotationGuardTarget(
            allocator, request.entry_id, request.lockname, request.managed_slots
        ),
        request.reservations,
        cycle_requests.matched_code_slot,
    )
    code_origin.record_cycle_evidence(request.entry_id, request.reservations)
    return generator_divergence.rotate_stale_codes(
        request, allocator, cycle_requests.matched_code_slot
    )


def _gated_cycle_request(
    request: CodeResolutionRequest, observation: CycleObservation
) -> CycleRequest:
    """Build the allocator cycle request, allocating only past the gate."""
    entry_id = request.entry_id
    lockname = request.lockname
    adoptions = cycle_requests.build_adoption_requests(
        entry_id,
        lockname,
        request.code_length,
        request.managed_slots,
        request.reservations,
        request.suppression,
    )
    gate_status = adoption_gate.evaluate(
        observation,
        adoptions,
        request.managed_slots,
        request.suppression,
        excluded=request.excluded_slots,
    )
    adoption_gate.report(entry_id, lockname, gate_status)
    allocations = (
        cycle_requests.build_allocation_requests(
            entry_id,
            lockname,
            request.code_length,
            request.managed_slots,
            request.reservations,
        )
        if gate_status.complete
        else []
    )
    return CycleRequest(
        observation=observation,
        adoptions=adoptions,
        rekeys=cycle_requests.build_rekey_requests(request.reservations),
        allocations=allocations,
        active_keys=cycle_requests.active_keys(request.reservations, lockname),
        adoption_complete=gate_status.complete,
        forced_reissues=request.forced_reissues,
        excluded_slots=request.excluded_slots,
        observed_reservations=tuple(getattr(adoptions, "observed_reservations", ())),
    )


def _record_cycle_outcome(
    request: CodeResolutionRequest,
    observation: CycleObservation,
    allocator: Any,
    result: CycleResult,
) -> None:
    """Apply the cycle result to reservations and report what it withheld."""
    entry_id = request.entry_id
    reservations = request.reservations
    cycle_results.apply_adoption_results(allocator, entry_id, reservations, result)
    if result.unaccounted_slots:
        _LOGGER.warning(
            "Shared code allocator withheld issuance for entry %s because "
            "managed slots are unreadable and unaccounted: %s",
            entry_id,
            sorted(result.unaccounted_slots),
        )
    unreadable_slots.report_unreadable_slots(
        request.hass,
        entry_id,
        request.lockname,
        sorted(observation.unreadable_slots - request.excluded_slots),
    )
    if cycle_results.apply_allocation_results(
        allocator, entry_id, reservations, result
    ):
        _notify_exhausted(request.hass, entry_id)
    cycle_results.clear_unresolved(reservations, result)


def _notify_exhausted(hass: Any, entry_id: str) -> None:
    """Warn that the code space ran out for an entry this cycle."""
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
