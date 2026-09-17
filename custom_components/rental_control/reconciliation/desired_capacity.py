# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Capacity and codeless-hold helpers for desired-plan computation."""

from __future__ import annotations

from .enums import SlotStatus
from .identity import _reservation_name_key
from .pairing import managed_physical_group
from .pairing import pair_managed_group
from .plan_models import DesiredPlan
from .plan_models import ManagedSlot
from .plan_models import Reservation


def retain_codeless_occupied_holds(
    selected: list[Reservation],
    overflow: list[Reservation],
    managed_slots: list[ManagedSlot],
) -> tuple[list[Reservation], list[Reservation], dict[int, str]]:
    """Move occupied codeless reservations from capacity overflow to selected."""
    held_slots = _codeless_occupied_holds(selected, overflow, managed_slots)
    if not held_slots:
        return selected, overflow, {}
    hold_keys = set(held_slots.values())
    retained = [res for res in overflow if res.identity_key in hold_keys]
    remaining_overflow = [res for res in overflow if res.identity_key not in hold_keys]
    return [*selected, *retained], remaining_overflow, held_slots


def record_capacity_overflow(
    plan: DesiredPlan,
    overflow: list[Reservation],
    remaining_capacity: int,
    rank_by_identity: dict[str, int] | None = None,
) -> None:
    """Record capacity overflow diagnostics exactly as the legacy planner did."""
    for rank_offset, res in enumerate(overflow):
        plan.overflow[res.identity_key] = "capacity"
        plan.diagnostics.setdefault("overflow_details", {})[res.identity_key] = {
            "rank": (
                rank_by_identity.get(res.identity_key)
                if rank_by_identity is not None
                else None
            )
            or remaining_capacity + rank_offset + 1,
            "reason": "capacity",
            "start": res.start.isoformat(),
            "identity_key": res.identity_key,
        }


def _codeless_occupied_holds(
    selected: list[Reservation],
    candidates: list[Reservation],
    managed_slots: list[ManagedSlot],
) -> dict[int, str]:
    """Return codeless overflow reservations already represented by slots."""
    codeless = [res for res in candidates if res.slot_code is None]
    occupied = [
        ms
        for ms in managed_slots
        if ms.managed and ms.status in {SlotStatus.OCCUPIED, SlotStatus.PHANTOM}
    ]
    holds = _codeless_identity_or_date_holds(codeless, occupied)
    held_keys = set(holds.values())
    unheld_occupied = [ms for ms in occupied if ms.slot not in holds]
    reserved_slots = _selected_occupied_slots(selected, unheld_occupied)
    available = [ms for ms in unheld_occupied if ms.slot not in reserved_slots]
    remaining_codeless = [res for res in codeless if res.identity_key not in held_keys]
    for desired_group in _group_codeless_by_stable_name(remaining_codeless).values():
        physical = managed_physical_group(desired_group, available, holds)
        if not physical:
            continue
        pairs, _remaining_physical, _remaining_desired = pair_managed_group(
            physical, desired_group
        )
        holds.update({ms.slot: res.identity_key for ms, res in pairs})
    return holds


def _codeless_identity_or_date_holds(
    codeless: list[Reservation], occupied: list[ManagedSlot]
) -> dict[int, str]:
    """Return unambiguous codeless holds before selected slots are reserved."""
    holds: dict[int, str] = {}
    for res in sorted(
        codeless, key=lambda item: (item.start, item.end, item.identity_key)
    ):
        available = [ms for ms in occupied if ms.slot not in holds]
        identity_matches = [
            ms for ms in available if ms.persisted_identity_key == res.identity_key
        ]
        physical = managed_physical_group([res], available, holds)
        date_matches = [
            ms
            for ms in physical
            if ms.actual_start == res.buffered_start
            and ms.actual_end == res.buffered_end
        ]
        matches = identity_matches or date_matches
        if matches:
            holds[sorted(matches, key=lambda ms: ms.slot)[0].slot] = res.identity_key
    return holds


def _selected_occupied_slots(
    selected: list[Reservation], occupied: list[ManagedSlot]
) -> set[int]:
    """Return occupied slots already attributable to selected reservations."""
    selected_keys = {res.identity_key for res in selected}
    matched_slots: dict[int, str] = {
        ms.slot: ms.persisted_identity_key
        for ms in occupied
        if ms.persisted_identity_key in selected_keys
    }
    matched_reservations = set(matched_slots.values())
    for desired_group in _group_by_stable_name(selected).values():
        desired_group = [
            res for res in desired_group if res.identity_key not in matched_reservations
        ]
        if not desired_group:
            continue
        physical = managed_physical_group(desired_group, occupied, matched_slots)
        if not physical:
            continue
        pairs, _remaining_physical, _remaining_desired = pair_managed_group(
            physical, desired_group
        )
        matched_slots.update({ms.slot: res.identity_key for ms, res in pairs})
        matched_reservations.update(res.identity_key for _ms, res in pairs)
    return set(matched_slots)


def _group_by_stable_name(
    candidates: list[Reservation],
    *,
    codeless_only: bool = False,
) -> dict[str, list[Reservation]]:
    """Group reservations by normalized stable slot name."""
    by_name: dict[str, list[Reservation]] = {}
    for res in candidates:
        if codeless_only and res.slot_code is not None:
            continue
        by_name.setdefault(_reservation_name_key(res), []).append(res)
    for group in by_name.values():
        group.sort(key=lambda res: (res.start, res.end, res.identity_key))
    return by_name


def _group_codeless_by_stable_name(
    candidates: list[Reservation],
) -> dict[str, list[Reservation]]:
    """Group codeless reservations by normalized stable slot name."""
    return _group_by_stable_name(candidates, codeless_only=True)
