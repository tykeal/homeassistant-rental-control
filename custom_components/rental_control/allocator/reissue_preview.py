# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Read-only preview helpers for forced door-code re-issue."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import Any

from ..coordinator_helpers import code_allocation
from ..coordinator_helpers import keymaster_observation
from ..coordinator_helpers import reissue as coordinator_reissue
from ..coordinator_helpers import reservations as reservations_helper
from ..coordinator_helpers import slot_matching
from . import issuance
from . import reissue_state
from .models import ReissueOutcome
from .models import ReissuePreview
from .models import ReissuePreviewRequest


def preview_reissue(allocator: Any, request: ReissuePreviewRequest) -> ReissuePreview:
    """Select the replacement a re-issue would allocate without mutation."""
    if allocator._pending_adoption:
        return ReissuePreview(reason="adoption_pending")
    if allocator._registry_lost:
        return ReissuePreview(reason="recovery_fail_closed")
    replaced_codes = _replacement_exclusions(allocator, request)
    replaced_ref = allocator.code_ref(replaced_codes[0]) if replaced_codes else None
    if request.identity_key is None:
        return ReissuePreview(reason="clear_only", replaced_code_ref=replaced_ref)
    if request.preferred_code is None:
        return ReissuePreview(reason="no_existing_allocation")
    code, origin = issuance.select_code(
        allocator._registry,
        request.preferred_code,
        request.code_length,
        request.identity_key,
        exclude=frozenset(replaced_codes),
    )
    if code is None or origin is None:
        return ReissuePreview(reason="exhausted", replaced_code_ref=replaced_ref)
    return ReissuePreview(
        replacement_code=code,
        replacement_code_ref=allocator.code_ref(code),
        origin=origin,
        replaced_code_ref=replaced_ref,
    )


def forced_hold_matches_target(
    allocator: Any,
    entry_id: str,
    identity_key: str | None,
    lockname: str | None,
    slot: int | None,
) -> ReissueOutcome | None:
    """Return the outstanding hold outcome for one target, if present."""
    from .reissue import is_forced_release_hold

    for record in allocator._registry.records.values():
        for owner in record.owners:
            if owner.entry_id != entry_id or not is_forced_release_hold(
                owner.identity_key
            ):
                continue
            if identity_key is not None and (
                reissue_state.hold_base_identity(owner.identity_key) != identity_key
            ):
                continue
            if identity_key is None and (
                owner.lockname != lockname or owner.slot != slot
            ):
                continue
            return ReissueOutcome(
                entry_id,
                identity_key,
                owner.lockname,
                owner.slot,
                record.code_ref,
                None,
                None,
                "held_pending_release",
                None,
            )
    return None


async def preview_request_and_result(
    allocator: Any,
    coordinator: Any,
    target: coordinator_reissue.ReissueTarget,
) -> tuple[ReissuePreviewRequest, ReissuePreview]:
    """Build and execute the allocator preview request."""
    observed_slots = (
        observe_managed_slots_without_mutation(coordinator)
        if target.lockname is not None
        else []
    )
    request = build_preview_request(coordinator, target, observed_slots)
    if allocator._pending_adoption:
        return request, ReissuePreview(reason="adoption_pending")
    if allocator._registry_lost:
        return request, ReissuePreview(reason="recovery_fail_closed")
    if target.identity_key is not None and request.preferred_code is None:
        return request, ReissuePreview(reason="no_existing_allocation")
    observation = code_allocation.build_cycle_observation(
        target.entry_id, target.lockname, observed_slots
    )
    if observation.unreadable_slots:
        return request, ReissuePreview(reason="unaccounted_slots")
    if issuance.unaccounted_slots(allocator, observation):
        return request, ReissuePreview(reason="unaccounted_slots")
    if not _adoption_would_complete(coordinator, target, observed_slots, observation):
        return request, ReissuePreview(reason="adoption_pending")
    return request, await allocator.async_preview_reissue(request)


def build_preview_request(
    coordinator: Any,
    target: coordinator_reissue.ReissueTarget,
    observed_slots: list[Any] | None = None,
) -> ReissuePreviewRequest:
    """Build a read-only allocator preview request from coordinator state."""
    preferred_code = None
    observed_code = target.observed_code
    observed_slots = observed_slots or (
        observe_managed_slots_without_mutation(coordinator)
        if target.lockname is not None
        else []
    )
    for slot in observed_slots:
        if slot.slot == target.slot and slot.actual_code:
            observed_code = slot.actual_code
            break
    if target.identity_key is not None:
        for reservation in preview_reservations(coordinator, target, observed_slots):
            if reservation.identity_key == target.identity_key:
                preferred_code = reservation.slot_code
                break
    return ReissuePreviewRequest(
        entry_id=target.entry_id,
        identity_key=target.identity_key,
        preferred_code=preferred_code,
        code_length=coordinator.code_length,
        lockname=target.lockname,
        slot=target.slot,
        observed_code=observed_code,
    )


def _adoption_would_complete(
    coordinator: Any,
    target: coordinator_reissue.ReissueTarget,
    observed_slots: list[Any],
    observation: Any,
) -> bool:
    """Return whether preview state would pass the cycle adoption gate."""
    if target.lockname is None:
        return True
    suppression = coordinator_reissue.ReissueSuppression(
        frozenset({target.identity_key}) if target.identity_key else frozenset(),
        frozenset({target.slot})
        if target.identity_key is None and target.slot
        else frozenset(),
    )
    reservations = preview_reservations(coordinator, target, observed_slots)
    adoptions = code_allocation.build_adoption_requests(
        target.entry_id,
        target.lockname,
        coordinator.code_length,
        observed_slots,
        reservations,
        suppression,
    )
    return code_allocation._adoption_complete(
        observation, adoptions, observed_slots, suppression
    )


def preview_reservations(
    coordinator: Any,
    target: coordinator_reissue.ReissueTarget,
    observed_slots: list[Any] | None = None,
) -> list[Any]:
    """Build reservations for preview using only isolated mutable copies."""
    observed_slots = observed_slots or (
        observe_managed_slots_without_mutation(coordinator)
        if target.lockname is not None
        else []
    )
    ctx = replace(
        coordinator._reservation_build_context(),
        reissue_suppression=coordinator_reissue.ReissueSuppression(
            frozenset({target.identity_key}) if target.identity_key else frozenset(),
            frozenset({target.slot})
            if target.identity_key is None and target.slot
            else frozenset(),
        ),
    )
    reservations = reservations_helper.build_reservations(
        deepcopy(list(coordinator.data or [])), deepcopy(observed_slots), ctx
    )
    persisted = deepcopy(getattr(coordinator, "_slot_mappings", {}).get("mappings", {}))
    observed_mapping_keys = {
        slot.persisted_identity_key
        for slot in observed_slots
        if slot.persisted_identity_key is not None
    }
    actual_slot_names = {
        slot.slot: slot.actual_name
        for slot in observed_slots
        if isinstance(slot.actual_name, str)
    }
    observed_mapping_keys = (
        slot_matching.remap_observed_mappings_to_physical_reservations(
            persisted,
            reservations,
            actual_slot_names,
            observed_mapping_keys,
        )
    )
    hydrate_reservations_from_mappings(reservations, persisted)
    prefix = f"{coordinator.event_prefix} " if coordinator.event_prefix else ""
    reservations.extend(
        reservations_helper.build_ghost_reservations(
            {reservation.identity_key for reservation in reservations},
            persisted,
            prefix,
            observed_mapping_keys,
            ctx,
        ).reservations
    )
    apply_checkin = getattr(coordinator, "_apply_checkin_protection", None)
    if callable(apply_checkin):
        apply_checkin(reservations, deepcopy(observed_slots))
    return reservations


def observe_managed_slots_without_mutation(coordinator: Any) -> list[Any]:
    """Read managed slots without updating Keymaster actual-state caches."""
    if coordinator.lockname is None:
        return []
    slots = []
    for slot in range(
        coordinator.start_slot, coordinator.start_slot + coordinator.max_events
    ):
        last_error = None
        if coordinator.event_overrides is not None:
            last_error = coordinator.event_overrides.get_last_slot_error(slot)
        managed_slot, _actual_state = keymaster_observation.classify_slot(
            coordinator._read_slot_snapshot(slot), last_error
        )
        slots.append(managed_slot)
    return slots


def hydrate_reservations_from_mappings(
    reservations: list[Any], persisted: dict[str, Any]
) -> None:
    """Copy persisted fields onto preview-only reservation objects."""
    for reservation in reservations:
        mapping = persisted.get(reservation.identity_key)
        if not isinstance(mapping, dict):
            continue
        history = mapping.get("fingerprint_history", [])
        if isinstance(history, (list, set, tuple)):
            reservation.fingerprint_history.update(
                item for item in history if isinstance(item, str)
            )
        reservation.missing_count = 0
        reservation.published_once = mapping.get("published_once") is True


def _replacement_exclusions(
    allocator: Any, request: ReissuePreviewRequest
) -> list[str]:
    """Return physical codes that a real re-issue would keep held."""
    codes: list[str] = []
    for record in allocator._registry.records.values():
        for owner in record.owners:
            if owner.entry_id != request.entry_id:
                continue
            if owner.lockname == request.lockname and owner.slot == request.slot:
                codes.append(record.code)
    if request.identity_key is not None:
        code = allocator._registry.code_for_identity(request.identity_key)
        if code is not None:
            codes.append(code)
    if request.observed_code is not None:
        codes.append(request.observed_code)
    return list(dict.fromkeys(codes))
