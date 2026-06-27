# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Desired-plan planner and compatibility shim."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging

from .action_models import SlotAction
from .actions import classify_matched_desired_slot
from .diagnostics import _build_plan_diagnostics_snapshot
from .enums import ActionKind
from .enums import SlotStatus
from .identity import _reservation_name_key
from .pairing import _select_managed_subset
from .pairing import managed_physical_group
from .pairing import pair_managed_group
from .plan_models import DesiredPlan
from .plan_models import ManagedSlot
from .plan_models import PlannedSlot
from .plan_models import Reservation

_LOGGER = logging.getLogger(__name__)
_ALLOWED_CONTEXT = {"entry_id", "lockname", "start_slot"}


@dataclass(slots=True)
class DesiredPlanRequest:
    """Bundled inputs for desired-plan computation."""

    reservations: list[Reservation]
    managed_slots: list[ManagedSlot]
    max_events: int
    plan_id: str
    generated_at: datetime
    entry_id: str | None = None
    lockname: str | None = None
    start_slot: int | None = None


@dataclass(slots=True)
class DesiredPlanState:
    """Mutable phase state for desired-plan computation."""

    request: DesiredPlanRequest
    plan: DesiredPlan
    selected: list[Reservation]
    res_by_key: dict[str, Reservation]
    managed_by_slot: dict[int, ManagedSlot]
    matched_slots: dict[int, str]
    matched_reservations: set[str]
    duplicate_slots: set[int]
    occupied_matched_slots: set[int]
    action_rows: list[
        tuple[ManagedSlot, ActionKind, str | None, str | None, str | None]
    ]


def build_desired_plan_request(
    reservations: list[Reservation] | DesiredPlanRequest,
    managed_slots: list[ManagedSlot] | None = None,
    max_events: int | None = None,
    plan_id: str | None = None,
    generated_at: datetime | None = None,
    **context: object,
) -> DesiredPlanRequest:
    """Validate legacy arguments and return a desired-plan request."""
    unknown = set(context) - _ALLOWED_CONTEXT
    if unknown:
        raise TypeError(
            f"Unknown compute_desired_plan context keys: {sorted(unknown)!r}"
        )
    if isinstance(reservations, DesiredPlanRequest):
        if (
            any(
                value is not None
                for value in (managed_slots, max_events, plan_id, generated_at)
            )
            or context
        ):
            raise TypeError("DesiredPlanRequest cannot be combined with legacy args")
        return reservations
    if (
        managed_slots is None
        or max_events is None
        or plan_id is None
        or generated_at is None
    ):
        raise TypeError(
            "compute_desired_plan requires managed_slots, max_events, plan_id, and generated_at"
        )
    entry_id = context.get("entry_id")
    lockname = context.get("lockname")
    start_slot = context.get("start_slot")
    if entry_id is not None and not isinstance(entry_id, str):
        raise TypeError("entry_id must be a string or None")
    if lockname is not None and not isinstance(lockname, str):
        raise TypeError("lockname must be a string or None")
    if start_slot is not None and not isinstance(start_slot, int):
        raise TypeError("start_slot must be an integer or None")
    return DesiredPlanRequest(
        reservations=reservations,
        managed_slots=managed_slots,
        max_events=max_events,
        plan_id=plan_id,
        generated_at=generated_at,
        entry_id=entry_id,
        lockname=lockname,
        start_slot=start_slot,
    )


def select_eligible_reservations(reservations: list[Reservation]) -> list[Reservation]:
    """Return reservations eligible for slot planning."""
    result: list[Reservation] = []
    for res in reservations:
        if not res.eligible or res.checked_out:
            continue
        if res.missing_count >= 3 and not res.protected_active:
            continue
        result.append(res)
    return result


def select_desired_candidates(
    eligible: list[Reservation], max_events: int
) -> tuple[list[Reservation], list[Reservation], list[Reservation], int]:
    """Partition eligible reservations into selected and overflow groups."""
    protected = [res for res in eligible if res.protected_active]
    non_protected = [res for res in eligible if not res.protected_active]
    non_protected.sort(key=lambda res: (res.start, res.identity_key))
    remaining_capacity = max(0, max_events - len(protected))
    return (
        protected,
        non_protected[:remaining_capacity],
        non_protected[remaining_capacity:],
        remaining_capacity,
    )


def record_capacity_overflow(
    plan: DesiredPlan,
    overflow: list[Reservation],
    remaining_capacity: int,
) -> None:
    """Record capacity overflow diagnostics exactly as the legacy planner did."""
    for rank_offset, res in enumerate(overflow):
        plan.overflow[res.identity_key] = "capacity"
        plan.diagnostics.setdefault("overflow_details", {})[res.identity_key] = {
            "rank": remaining_capacity + rank_offset + 1,
            "reason": "capacity",
            "start": res.start.isoformat(),
            "identity_key": res.identity_key,
        }


def group_selected_by_stable_name(
    selected: list[Reservation],
) -> dict[str, list[Reservation]]:
    """Group selected reservations by normalized stable slot name."""
    selected_by_name: dict[str, list[Reservation]] = {}
    for res in selected:
        selected_by_name.setdefault(_reservation_name_key(res), []).append(res)
    for group in selected_by_name.values():
        group.sort(key=lambda res: (res.start, res.end, res.identity_key))
    return selected_by_name


def _record_matched_group(
    state: DesiredPlanState,
    desired_group: list[Reservation],
    pairs: list[tuple[ManagedSlot, Reservation]],
    duplicate_candidates: list[ManagedSlot],
) -> None:
    """Record matched pairs and duplicate physical slots for one group."""
    for ms, res in pairs:
        state.matched_slots[ms.slot] = res.identity_key
        state.matched_reservations.add(res.identity_key)
        state.duplicate_slots.discard(ms.slot)
        state.plan.diagnostics.setdefault("stable_name_matches", {})[ms.slot] = {
            "identity_key": res.identity_key,
            "slot_name": res.slot_name,
        }
    for extra_ms in duplicate_candidates:
        state.duplicate_slots.add(extra_ms.slot)
        _LOGGER.warning(
            "Duplicate physical slot-name match for %s in slot %d; non-canonical duplicate will reset",
            desired_group[0].slot_name,
            extra_ms.slot,
        )


def match_existing_managed_slots(state: DesiredPlanState) -> None:
    """Preserve stable-name and persisted-identity matches for occupied slots."""
    occupied = [
        ms
        for ms in state.managed_by_slot.values()
        if ms.status in {SlotStatus.OCCUPIED, SlotStatus.PHANTOM}
    ]
    for desired_group in group_selected_by_stable_name(state.selected).values():
        physical = managed_physical_group(desired_group, occupied, state.matched_slots)
        if not physical:
            continue
        extras: list[ManagedSlot] = []
        if len(desired_group) > 1 and len(physical) > len(desired_group):
            canonical = _select_managed_subset(physical, desired_group)
            canonical_slots = {ms.slot for ms in canonical}
            extras = [ms for ms in physical if ms.slot not in canonical_slots]
            physical = canonical
        pairs, remaining_physical, remaining_desired = pair_managed_group(
            physical, desired_group
        )
        duplicates = [*remaining_physical[len(remaining_desired) :], *extras]
        _record_matched_group(state, desired_group, pairs, duplicates)
    state.occupied_matched_slots = set(state.matched_slots)


def assign_unmatched_reservations(state: DesiredPlanState) -> None:
    """Assign unmatched reservations to matched or confirmed-free slots."""
    free_slots = sorted(
        ms.slot for ms in state.managed_by_slot.values() if ms.status is SlotStatus.FREE
    )
    for res in state.selected:
        if res.identity_key in state.matched_reservations:
            slot = next(
                (
                    slot
                    for slot, key in state.matched_slots.items()
                    if key == res.identity_key
                ),
                None,
            )
            if slot is not None:
                state.plan.selected[res.identity_key] = slot
                continue
        if free_slots:
            slot = free_slots.pop(0)
            state.plan.selected[res.identity_key] = slot
            state.matched_slots[slot] = res.identity_key
        else:
            state.plan.overflow[res.identity_key] = "no_empty_slot"
            _LOGGER.warning(
                "Overflow: reservation %s selected but no confirmed-empty managed slot is available",
                res.identity_key,
            )


def _classify_slot(
    state: DesiredPlanState, ms: ManagedSlot, desired_key: str | None
) -> tuple[ActionKind, str | None, str | None]:
    """Return action, pending reason, and reason for a managed slot."""
    if ms.status is SlotStatus.PENDING_CLEAR:
        if ms.persisted_identity_key in state.plan.protected:
            return ActionKind.BLOCKED, "protected_active_pending_clear", None
        return ActionKind.RETRY_CLEAR, ms.blocked_reason or "pending_clear", None
    if ms.status is SlotStatus.UNKNOWN:
        return ActionKind.BLOCKED, ms.blocked_reason or "unreadable", None
    if ms.status is SlotStatus.BLOCKED:
        return ActionKind.BLOCKED, ms.blocked_reason or "blocked", None
    if ms.slot in state.duplicate_slots and ms.slot not in state.matched_slots:
        return ActionKind.CLEAR, None, "duplicate_non_canonical"
    if desired_key is None:
        if ms.status is SlotStatus.FREE:
            return ActionKind.NOOP, None, None
        return (
            ActionKind.CLEAR,
            None,
            "phantom" if ms.status is SlotStatus.PHANTOM else "stale",
        )
    desired_res = state.res_by_key.get(desired_key)
    if ms.status is SlotStatus.FREE:
        return ActionKind.SET, None, None
    if ms.slot in state.occupied_matched_slots and desired_res is not None:
        action, reason = classify_matched_desired_slot(ms, desired_res)
        return action, None, reason
    return ActionKind.CLEAR, None, "mis_assigned"


def classify_desired_plan_slots(state: DesiredPlanState) -> None:
    """Classify each managed slot and capture action rows in slot order."""
    slot_to_identity = {slot: key for key, slot in state.plan.selected.items()}
    for ms in sorted(state.managed_by_slot.values(), key=lambda item: item.slot):
        desired_key = slot_to_identity.get(ms.slot)
        action, pending_reason, reason = _classify_slot(state, ms, desired_key)
        ms.desired_identity_key = desired_key
        state.plan.slots[ms.slot] = PlannedSlot(
            slot=ms.slot,
            desired_identity_key=desired_key,
            actual_classification=ms.status.value,
            action=action,
            pending_reason=pending_reason or reason,
            retry_count=ms.retry_count,
            last_error=ms.last_error,
        )
        state.action_rows.append((ms, action, reason, pending_reason, desired_key))


def assemble_desired_actions(state: DesiredPlanState) -> None:
    """Append non-NOOP actions preserving legacy slot order and metadata."""
    for ms, action, reason, pending_reason, desired_key in state.action_rows:
        if action is ActionKind.NOOP:
            continue
        state.plan.actions.append(
            SlotAction(
                kind=action,
                slot=ms.slot,
                identity_key=desired_key,
                reason=reason or pending_reason,
                desired_id=desired_key,
                matched_by="name_exact"
                if ms.slot in state.occupied_matched_slots
                else "none",
                requires_confirmed_empty=action
                in {ActionKind.SET, ActionKind.OVERWRITE_MANUAL_CHANGE},
                preflight_read=action
                in {
                    ActionKind.SET,
                    ActionKind.CLEAR,
                    ActionKind.OVERWRITE_MANUAL_CHANGE,
                },
            )
        )


def build_desired_diagnostics(state: DesiredPlanState) -> None:
    """Populate desired-plan diagnostics from the completed plan."""
    req = state.request
    state.plan.diagnostics = _build_plan_diagnostics_snapshot(
        state.plan,
        req.reservations,
        req.max_events,
        entry_id=req.entry_id,
        lockname=req.lockname,
        start_slot=req.start_slot,
    )


def _compute_desired_plan_from_request(req: DesiredPlanRequest) -> DesiredPlan:
    """Compute a desired plan from a validated request."""
    plan = DesiredPlan(plan_id=req.plan_id, generated_at=req.generated_at)
    eligible = select_eligible_reservations(req.reservations)
    protected, selected_np, overflow, capacity = select_desired_candidates(
        eligible, req.max_events
    )
    selected = sorted(
        [*protected, *selected_np],
        key=lambda res: (0 if res.protected_active else 1, res.start, res.identity_key),
    )
    plan.protected = {res.identity_key for res in protected}
    record_capacity_overflow(plan, overflow, capacity)
    state = DesiredPlanState(
        request=req,
        plan=plan,
        selected=selected,
        res_by_key={res.identity_key: res for res in req.reservations},
        managed_by_slot={ms.slot: ms for ms in req.managed_slots if ms.managed},
        matched_slots={},
        matched_reservations=set(),
        duplicate_slots=set(),
        occupied_matched_slots=set(),
        action_rows=[],
    )
    match_existing_managed_slots(state)
    assign_unmatched_reservations(state)
    classify_desired_plan_slots(state)
    assemble_desired_actions(state)
    build_desired_diagnostics(state)
    return plan


def compute_desired_plan(
    reservations: list[Reservation] | DesiredPlanRequest,
    managed_slots: list[ManagedSlot] | None = None,
    max_events: int | None = None,
    plan_id: str | None = None,
    generated_at: datetime | None = None,
    **context: object,
) -> DesiredPlan:
    """Compute the deterministic desired slot plan for current reservations."""
    return _compute_desired_plan_from_request(
        build_desired_plan_request(
            reservations,
            managed_slots,
            max_events,
            plan_id,
            generated_at,
            **context,
        )
    )
