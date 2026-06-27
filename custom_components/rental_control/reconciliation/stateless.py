# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Stateless reconciliation planner."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from datetime import timezone

from .action_models import SlotAction
from .diagnostics import build_stateless_diagnostics as _build_stateless_diagnostics
from .enums import ActionKind
from .enums import ObservedSlotStatus
from .identity import _desired_name_key
from .identity import _names_match
from .pairing import _pair_partial_observed
from .pairing import _select_observed_subset
from .pairing import _slot_times_match
from .stateless_models import DesiredReservation
from .stateless_models import ObservedSlot
from .stateless_models import StatelessPlan


@dataclass(slots=True)
class StatelessPlanRequest:
    """Bundled inputs for stateless-plan computation."""

    observed_slots: list[ObservedSlot]
    desired_reservations: list[DesiredReservation]
    max_events: int
    plan_id: str
    generated_at: datetime
    prefix: str = ""


@dataclass(slots=True)
class StatelessPlanState:
    """Mutable phase state for stateless-plan computation."""

    request: StatelessPlanRequest
    plan: StatelessPlan
    selected: list[DesiredReservation]
    slot_to_desired: dict[int, str]
    matched_desired: set[str]
    duplicate_slots: set[int]


def build_stateless_plan_request(
    observed_slots: list[ObservedSlot],
    desired_reservations: list[DesiredReservation],
    max_events: int,
    plan_id: str,
    generated_at: datetime,
    *,
    prefix: str = "",
) -> StatelessPlanRequest:
    """Return a stateless-plan request from public arguments."""
    return StatelessPlanRequest(
        observed_slots=observed_slots,
        desired_reservations=desired_reservations,
        max_events=max_events,
        plan_id=plan_id,
        generated_at=generated_at,
        prefix=prefix,
    )


def initialize_stateless_plan(request: StatelessPlanRequest) -> StatelessPlan:
    """Create the initial stateless plan containers."""
    plan = StatelessPlan(plan_id=request.plan_id, generated_at=request.generated_at)
    plan.observed_slots = {
        slot.slot: slot for slot in request.observed_slots if slot.managed
    }
    plan.desired_reservations = {
        desired.desired_id: desired for desired in request.desired_reservations
    }
    return plan


def select_stateless_reservations(
    request: StatelessPlanRequest, plan: StatelessPlan
) -> list[DesiredReservation]:
    """Select stateless desired reservations and record capacity overflow."""
    eligible = [
        desired
        for desired in request.desired_reservations
        if desired.eligible and not desired.checked_out
    ]
    protected = sorted(
        [desired for desired in eligible if desired.protected_active],
        key=lambda desired: (desired.start, desired.desired_id),
    )
    non_protected = sorted(
        [desired for desired in eligible if not desired.protected_active],
        key=lambda desired: (desired.start, desired.desired_id),
    )
    selected = [
        *protected,
        *non_protected[: max(0, request.max_events - len(protected))],
    ]
    for rank, desired in enumerate(selected, start=1):
        desired.selected_rank = rank
    for desired in non_protected[max(0, request.max_events - len(protected)) :]:
        desired.overflow_reason = "capacity"
        plan.overflow[desired.desired_id] = "capacity"
    return selected


def group_stateless_reservations_by_name(
    selected: list[DesiredReservation],
) -> dict[str, list[DesiredReservation]]:
    """Group stateless reservations by normalized stable slot name."""
    selected_by_name: dict[str, list[DesiredReservation]] = {}
    for desired in selected:
        selected_by_name.setdefault(_desired_name_key(desired), []).append(desired)
    for group in selected_by_name.values():
        group.sort(key=lambda desired: (desired.start, desired.end, desired.desired_id))
    return selected_by_name


def _observed_physical_group(
    group: list[DesiredReservation],
    occupied: list[ObservedSlot],
    state: StatelessPlanState,
) -> list[ObservedSlot]:
    """Return observed occupied slots that identify a desired group."""
    physical = [
        slot
        for slot in occupied
        if slot.slot not in state.slot_to_desired
        and _names_match(
            slot.raw_name,
            group[0].stable_slot_name,
            group[0].display_slot_name,
            prefix=state.request.prefix,
        )
    ]
    physical.sort(
        key=lambda slot: (
            slot.actual_start or datetime.max.replace(tzinfo=timezone.utc),
            slot.actual_end or datetime.max.replace(tzinfo=timezone.utc),
            slot.slot,
        )
    )
    return physical


def _pair_exact_observed(
    physical: list[ObservedSlot], group: list[DesiredReservation]
) -> tuple[list[tuple[ObservedSlot, DesiredReservation]], set[int], set[str]]:
    """Pair exact observed date matches for a desired group."""
    pairs: list[tuple[ObservedSlot, DesiredReservation]] = []
    paired_slots: set[int] = set()
    paired_desired: set[str] = set()
    for desired in group:
        exact_matches = [
            slot
            for slot in physical
            if slot.slot not in paired_slots
            and _slot_times_match(
                slot.actual_start,
                slot.actual_end,
                desired.buffered_start,
                desired.buffered_end,
            )
        ]
        if exact_matches:
            slot = exact_matches[0]
            pairs.append((slot, desired))
            paired_slots.add(slot.slot)
            paired_desired.add(desired.desired_id)
    return pairs, paired_slots, paired_desired


def _pair_observed_group(
    physical: list[ObservedSlot], group: list[DesiredReservation]
) -> tuple[
    list[tuple[ObservedSlot, DesiredReservation]],
    list[ObservedSlot],
    list[DesiredReservation],
]:
    """Pair a physical observed group to desired reservations."""
    if (
        len(group) > 1
        and len(physical) == len(group)
        and all(
            slot.actual_start is not None and slot.actual_end is not None
            for slot in physical
        )
    ):
        return list(zip(physical, group, strict=False)), [], []
    pairs, paired_slots, paired_desired = _pair_exact_observed(physical, group)
    remaining_physical = [slot for slot in physical if slot.slot not in paired_slots]
    remaining_desired = [
        desired for desired in group if desired.desired_id not in paired_desired
    ]
    if len(remaining_physical) > len(remaining_desired) and remaining_desired:
        canonical = _select_observed_subset(remaining_physical, remaining_desired)
        canonical_slots = {slot.slot for slot in canonical}
        remaining_physical = [
            *canonical,
            *[slot for slot in remaining_physical if slot.slot not in canonical_slots],
        ]
    if len(remaining_physical) < len(remaining_desired):
        pairs.extend(_pair_partial_observed(remaining_physical, remaining_desired))
    else:
        pairs.extend(zip(remaining_physical, remaining_desired, strict=False))
    return pairs, remaining_physical, remaining_desired


def _record_observed_pairs(
    state: StatelessPlanState,
    pairs: list[tuple[ObservedSlot, DesiredReservation]],
    duplicates: list[ObservedSlot],
) -> None:
    """Record observed matches and non-canonical duplicate slots."""
    for slot, desired in pairs:
        slot.matched_desired_id = desired.desired_id
        desired.matched_slot = slot.slot
        desired.assigned_slot = slot.slot
        state.slot_to_desired[slot.slot] = desired.desired_id
        state.matched_desired.add(desired.desired_id)
        state.plan.selected[desired.desired_id] = slot.slot
    for extra_slot in duplicates:
        state.duplicate_slots.add(extra_slot.slot)


def match_observed_slots_by_name(state: StatelessPlanState) -> None:
    """Match occupied observed slots to desired reservations by stable name."""
    occupied = [
        slot
        for slot in state.plan.observed_slots.values()
        if slot.classification
        in {ObservedSlotStatus.OCCUPIED, ObservedSlotStatus.PHANTOM}
        and slot.raw_name
    ]
    for group in group_stateless_reservations_by_name(state.selected).values():
        physical = _observed_physical_group(group, occupied, state)
        extras: list[ObservedSlot] = []
        if len(group) > 1 and len(physical) > len(group):
            canonical = _select_observed_subset(physical, group)
            canonical_slots = {slot.slot for slot in canonical}
            extras = [slot for slot in physical if slot.slot not in canonical_slots]
            physical = canonical
        pairs, remaining_physical, remaining_desired = _pair_observed_group(
            physical, group
        )
        duplicates = [*remaining_physical[len(remaining_desired) :], *extras]
        _record_observed_pairs(state, pairs, duplicates)


def assign_unmatched_stateless_reservations(state: StatelessPlanState) -> None:
    """Assign selected unmatched reservations to confirmed-empty observed slots."""
    free_slots = sorted(
        slot.slot
        for slot in state.plan.observed_slots.values()
        if slot.classification is ObservedSlotStatus.EMPTY and slot.empty_confirmed
    )
    for desired in state.selected:
        if desired.desired_id in state.matched_desired:
            continue
        if free_slots:
            slot_number = free_slots.pop(0)
            desired.assigned_slot = slot_number
            state.slot_to_desired[slot_number] = desired.desired_id
            state.plan.selected[desired.desired_id] = slot_number
        else:
            desired.overflow_reason = "no_empty_slot"
            state.plan.overflow[desired.desired_id] = "no_empty_slot"


def _action_for_slot(
    slot: ObservedSlot, desired: DesiredReservation | None, duplicate: bool
) -> SlotAction | None:
    """Return the stateless action for one observed slot, if any."""
    if slot.classification is ObservedSlotStatus.UNKNOWN:
        return SlotAction(
            kind=ActionKind.BLOCKED,
            slot=slot.slot,
            desired_id=desired.desired_id if desired else None,
            blocked_reason="unreadable",
            reason="unreadable",
        )
    if duplicate:
        return SlotAction(
            kind=ActionKind.RESET,
            slot=slot.slot,
            reason="duplicate_non_canonical",
            preflight_read=True,
        )
    if desired is None:
        if slot.classification is not ObservedSlotStatus.EMPTY:
            return SlotAction(
                kind=ActionKind.RESET,
                slot=slot.slot,
                reason="stale",
                preflight_read=True,
            )
        return None
    if slot.classification is ObservedSlotStatus.EMPTY:
        return SlotAction(
            kind=ActionKind.ASSIGN,
            slot=slot.slot,
            identity_key=desired.desired_id,
            desired_id=desired.desired_id,
            requires_confirmed_empty=True,
            preflight_read=True,
        )
    if slot.raw_pin != desired.slot_code or (
        slot.raw_name and slot.raw_name != desired.display_slot_name
    ):
        return SlotAction(
            kind=ActionKind.UPDATE_IN_PLACE,
            slot=slot.slot,
            identity_key=desired.desired_id,
            desired_id=desired.desired_id,
            matched_by="name_exact",
            requires_confirmed_empty=True,
            preflight_read=True,
            reason="replace_code_or_name",
        )
    if (
        slot.actual_start != desired.buffered_start
        or slot.actual_end != desired.buffered_end
    ):
        return SlotAction(
            kind=ActionKind.UPDATE_TIMES,
            slot=slot.slot,
            identity_key=desired.desired_id,
            desired_id=desired.desired_id,
            matched_by="name_exact",
            reason="date_drift",
        )
    return None


def build_stateless_actions(state: StatelessPlanState) -> None:
    """Build stateless actions preserving slot order."""
    for slot in sorted(state.plan.observed_slots.values(), key=lambda item: item.slot):
        desired_id = state.slot_to_desired.get(slot.slot)
        desired = (
            state.plan.desired_reservations.get(desired_id) if desired_id else None
        )
        action = _action_for_slot(slot, desired, slot.slot in state.duplicate_slots)
        if action is not None:
            state.plan.actions.append(action)


def build_stateless_diagnostics(state: StatelessPlanState) -> None:
    """Populate stateless-plan diagnostics."""
    state.plan.diagnostics = _build_stateless_diagnostics(state.plan)


def _compute_stateless_plan_from_request(
    request: StatelessPlanRequest,
) -> StatelessPlan:
    """Compute a stateless plan from a request object."""
    plan = initialize_stateless_plan(request)
    state = StatelessPlanState(
        request=request,
        plan=plan,
        selected=select_stateless_reservations(request, plan),
        slot_to_desired={},
        matched_desired=set(),
        duplicate_slots=set(),
    )
    match_observed_slots_by_name(state)
    assign_unmatched_stateless_reservations(state)
    build_stateless_actions(state)
    build_stateless_diagnostics(state)
    return plan


def compute_stateless_plan(
    observed_slots: list[ObservedSlot],
    desired_reservations: list[DesiredReservation],
    max_events: int,
    plan_id: str,
    generated_at: datetime,
    *,
    prefix: str = "",
) -> StatelessPlan:
    """Compute a pure stateless slot plan from physical slots and calendar stays."""
    return _compute_stateless_plan_from_request(
        build_stateless_plan_request(
            observed_slots,
            desired_reservations,
            max_events,
            plan_id,
            generated_at,
            prefix=prefix,
        )
    )
