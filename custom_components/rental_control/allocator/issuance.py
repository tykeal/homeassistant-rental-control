# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Issuance helpers for the shared door-code allocator."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC
from datetime import datetime
import logging
from typing import TYPE_CHECKING

from .candidates import candidate_codes
from .models import AdoptionRequest
from .models import AllocationOrigin
from .models import AllocationRequest
from .models import AllocationResult
from .models import CycleObservation
from .models import CycleRequest
from .models import CycleResult

if TYPE_CHECKING:
    from .allocator import DoorCodeAllocator

_LOGGER = logging.getLogger(__name__)


def observed_alias_key(
    request: AdoptionRequest,
    *,
    identity_key: str | None = None,
) -> str:
    """Return the stable observed-code alias for one physical slot."""
    owner_key = request.identity_key if identity_key is None else identity_key
    return f"{owner_key}:observed:{request.entry_id}:{request.lockname}:{request.slot}"


def allocate_request(
    allocator: DoorCodeAllocator, request: AllocationRequest
) -> AllocationResult:
    """Allocate a code while the allocator lock is already held."""
    existing = allocator._registry.record_for_identity(request.identity_key)
    if existing is not None:
        owner = next(
            owner
            for owner in existing.owners
            if owner.identity_key == request.identity_key
        )
        return AllocationResult(code=existing.code, origin=owner.origin)
    if not request.issuance_allowed:
        return AllocationResult(code=None, reason="unaccounted_slots")
    if allocator._pending_adoption:
        return AllocationResult(code=None, reason="adoption_pending")
    if allocator._registry_lost and (
        request.previously_published or not allocator._registry_missing
    ):
        _LOGGER.warning(
            "Declined allocation for %s:%s after registry loss",
            request.entry_id,
            request.identity_key,
        )
        return AllocationResult(code=None, reason="recovery_fail_closed")
    if allocator._registry.is_available(
        request.preferred_code,
        request.code_length,
        request.identity_key,
    ):
        record = allocator._registry.allocate(
            request,
            request.preferred_code,
            AllocationOrigin.PREFERRED,
            allocator.code_ref(request.preferred_code),
            datetime.now(UTC).isoformat(),
        )
        _LOGGER.info(
            "Allocated preferred shared code_ref %s for %s:%s",
            record.code_ref,
            request.entry_id,
            request.identity_key,
        )
        return AllocationResult(code=record.code, origin=AllocationOrigin.PREFERRED)
    preferred_ref = allocator.code_ref(request.preferred_code)
    for candidate in candidate_codes(request.identity_key, request.code_length):
        if not allocator._registry.is_available(
            candidate,
            request.code_length,
            request.identity_key,
        ):
            continue
        record = allocator._registry.allocate(
            request,
            candidate,
            AllocationOrigin.COLLISION_RESOLVED,
            allocator.code_ref(candidate),
            datetime.now(UTC).isoformat(),
        )
        _LOGGER.info(
            "Allocated collision-resolved shared code_ref %s for %s:%s "
            "after preferred code_ref %s was unavailable",
            record.code_ref,
            request.entry_id,
            request.identity_key,
            preferred_ref,
        )
        return AllocationResult(
            code=record.code,
            origin=AllocationOrigin.COLLISION_RESOLVED,
        )
    _LOGGER.warning(
        "Exhausted shared code space for %s:%s; preferred code_ref %s",
        request.entry_id,
        request.identity_key,
        preferred_ref,
    )
    return AllocationResult(code=None, reason="exhausted")


async def resolve_cycle(
    allocator: DoorCodeAllocator, request: CycleRequest
) -> CycleResult:
    """Resolve one refresh cycle atomically under the allocator lock."""
    async with allocator._lock:
        adopted: dict[str, AllocationResult] = {}
        for adoption in request.adoptions:
            adopted[adoption.identity_key] = allocator._adopt_unlocked(adoption)

        for old_key, new_key in request.rekeys:
            allocator._rekey_unlocked(old_key, new_key)

        unaccounted = unaccounted_slots(allocator, request.observation)
        if request.adoption_complete and not unaccounted:
            allocator._pending_adoption.discard(request.observation.entry_id)
            if not allocator._pending_adoption:
                allocator._gate_deadline = 0.0
                allocator._gate_warning_sent = False

        allocator._warn_if_gate_expired()
        issuance_allowed = not unaccounted
        allocated: dict[str, AllocationResult] = {}
        for allocation in request.allocations:
            if allocation.identity_key in adopted:
                continue
            allocation = replace(
                allocation,
                issuance_allowed=allocation.issuance_allowed and issuance_allowed,
            )
            allocated[allocation.identity_key] = allocate_request(allocator, allocation)
        released = allocator._sweep_unlocked(request.observation, request.active_keys)
        recovery_declined = any(
            result.reason == "recovery_fail_closed" for result in allocated.values()
        )
        if not allocator._registry_lost:
            allocator._store.async_save(allocator._registry)
        elif (
            allocator._registry_missing
            and request.adoption_complete
            and not unaccounted
            and not recovery_declined
        ):
            allocator._store.async_save(allocator._registry)
            allocator._registry_lost = False
            allocator._registry_missing = False
        return CycleResult(
            adopted=adopted,
            allocated=allocated,
            released=released,
            unaccounted_slots=unaccounted,
        )


def unaccounted_slots(
    allocator: DoorCodeAllocator, observation: CycleObservation
) -> frozenset[int]:
    """Return unreadable slots not claimed by this entry's registry owners."""
    if observation.lockname is None:
        return frozenset()
    claimed = {
        owner.slot
        for record in allocator._registry.records.values()
        for owner in record.owners
        if owner.entry_id == observation.entry_id
        and owner.lockname == observation.lockname
        and owner.slot is not None
    }
    return frozenset(observation.unreadable_slots - claimed)
