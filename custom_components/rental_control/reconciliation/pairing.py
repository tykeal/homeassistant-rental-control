# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Slot pairing helpers for duplicate disambiguation."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
from functools import lru_cache

from .identity import _names_match
from .plan_models import ManagedSlot
from .plan_models import Reservation
from .stateless_models import DesiredReservation
from .stateless_models import ObservedSlot


def _slot_times_match(
    actual_start: datetime | None,
    actual_end: datetime | None,
    desired_start: datetime,
    desired_end: datetime,
) -> bool:
    """Return whether observed Keymaster dates exactly match desired dates."""
    return actual_start == desired_start and actual_end == desired_end


def _datetime_distance(left: datetime | None, right: datetime) -> float:
    """Return absolute seconds between two datetimes, or infinity if absent."""
    if left is None:
        return float("inf")
    return abs((left - right).total_seconds())


def _managed_slot_distance(slot: ManagedSlot, reservation: Reservation) -> float:
    """Return date distance between a managed slot and desired reservation."""
    return _datetime_distance(
        slot.actual_start, reservation.buffered_start
    ) + _datetime_distance(slot.actual_end, reservation.buffered_end)


def _observed_slot_distance(slot: ObservedSlot, desired: DesiredReservation) -> float:
    """Return date distance between an observed slot and desired reservation."""
    return _datetime_distance(
        slot.actual_start, desired.buffered_start
    ) + _datetime_distance(slot.actual_end, desired.buffered_end)


def _select_managed_subset(
    slots: list[ManagedSlot], desired: list[Reservation]
) -> list[ManagedSlot]:
    """Return the minimum-distance ordered slot subset for reservations."""

    @lru_cache(maxsize=None)
    def _best(slot_index: int, desired_index: int) -> tuple[float, tuple[int, ...]]:
        """Return best ordered subset cost and indices from this position."""
        if desired_index == len(desired):
            return 0.0, ()
        if slot_index == len(slots):
            return float("inf"), ()
        skip_cost, skip_indices = _best(slot_index + 1, desired_index)
        take_rest_cost, take_indices = _best(slot_index + 1, desired_index + 1)
        take_cost = (
            _managed_slot_distance(slots[slot_index], desired[desired_index])
            + take_rest_cost
        )
        if take_cost < skip_cost:
            return take_cost, (slot_index, *take_indices)
        return skip_cost, skip_indices

    _, indices = _best(0, 0)
    return [slots[index] for index in indices]


def _select_observed_subset(
    slots: list[ObservedSlot], desired: list[DesiredReservation]
) -> list[ObservedSlot]:
    """Return the minimum-distance ordered observed subset for reservations."""

    @lru_cache(maxsize=None)
    def _best(slot_index: int, desired_index: int) -> tuple[float, tuple[int, ...]]:
        """Return best ordered subset cost and indices from this position."""
        if desired_index == len(desired):
            return 0.0, ()
        if slot_index == len(slots):
            return float("inf"), ()
        skip_cost, skip_indices = _best(slot_index + 1, desired_index)
        take_rest_cost, take_indices = _best(slot_index + 1, desired_index + 1)
        take_cost = (
            _observed_slot_distance(slots[slot_index], desired[desired_index])
            + take_rest_cost
        )
        if take_cost < skip_cost:
            return take_cost, (slot_index, *take_indices)
        return skip_cost, skip_indices

    _, indices = _best(0, 0)
    return [slots[index] for index in indices]


def _pair_partial_managed(
    slots: list[ManagedSlot], desired: list[Reservation]
) -> list[tuple[ManagedSlot, Reservation]]:
    """Pair fewer managed slots to the best ordered desired reservations."""

    @lru_cache(maxsize=None)
    def _best(slot_index: int, desired_index: int) -> tuple[float, tuple[int, ...]]:
        """Return best desired indices for remaining managed slots."""
        if slot_index == len(slots):
            return 0.0, ()
        if desired_index == len(desired):
            return float("inf"), ()
        skip_cost, skip_indices = _best(slot_index, desired_index + 1)
        take_rest_cost, take_indices = _best(slot_index + 1, desired_index + 1)
        take_cost = (
            _managed_slot_distance(slots[slot_index], desired[desired_index])
            + take_rest_cost
        )
        if take_cost < skip_cost:
            return take_cost, (desired_index, *take_indices)
        return skip_cost, skip_indices

    _, desired_indices = _best(0, 0)
    return [
        (slots[slot_index], desired[desired_index])
        for slot_index, desired_index in enumerate(desired_indices)
    ]


def _pair_partial_observed(
    slots: list[ObservedSlot], desired: list[DesiredReservation]
) -> list[tuple[ObservedSlot, DesiredReservation]]:
    """Pair fewer observed slots to the best ordered desired reservations."""

    @lru_cache(maxsize=None)
    def _best(slot_index: int, desired_index: int) -> tuple[float, tuple[int, ...]]:
        """Return best desired indices for remaining observed slots."""
        if slot_index == len(slots):
            return 0.0, ()
        if desired_index == len(desired):
            return float("inf"), ()
        skip_cost, skip_indices = _best(slot_index, desired_index + 1)
        take_rest_cost, take_indices = _best(slot_index + 1, desired_index + 1)
        take_cost = (
            _observed_slot_distance(slots[slot_index], desired[desired_index])
            + take_rest_cost
        )
        if take_cost < skip_cost:
            return take_cost, (desired_index, *take_indices)
        return skip_cost, skip_indices

    _, desired_indices = _best(0, 0)
    return [
        (slots[slot_index], desired[desired_index])
        for slot_index, desired_index in enumerate(desired_indices)
    ]


def managed_physical_group(
    desired_group: list[Reservation],
    occupied_slots: list[ManagedSlot],
    matched_slots: dict[int, str],
) -> list[ManagedSlot]:
    """Return occupied slots that identify the desired group."""
    desired_keys = {desired.identity_key for desired in desired_group}
    group = [
        slot
        for slot in occupied_slots
        if slot.slot not in matched_slots
        and (
            _names_match(
                slot.actual_name,
                desired_group[0].slot_name,
                desired_group[0].display_slot_name,
            )
            or slot.persisted_identity_key in desired_keys
        )
    ]
    group.sort(
        key=lambda slot: (
            slot.actual_start or datetime.max.replace(tzinfo=timezone.utc),
            slot.actual_end or datetime.max.replace(tzinfo=timezone.utc),
            slot.slot,
        )
    )
    return group


def _pair_exact_managed(
    physical: list[ManagedSlot], desired: list[Reservation]
) -> tuple[list[tuple[ManagedSlot, Reservation]], set[int], set[str]]:
    """Pair exact date matches between physical slots and reservations."""
    pairs: list[tuple[ManagedSlot, Reservation]] = []
    paired_slots: set[int] = set()
    paired_reservations: set[str] = set()
    for res in desired:
        exact_matches = [
            slot
            for slot in physical
            if slot.slot not in paired_slots
            and _slot_times_match(
                slot.actual_start,
                slot.actual_end,
                res.buffered_start,
                res.buffered_end,
            )
        ]
        if exact_matches:
            slot = exact_matches[0]
            pairs.append((slot, res))
            paired_slots.add(slot.slot)
            paired_reservations.add(res.identity_key)
    return pairs, paired_slots, paired_reservations


def pair_managed_group(
    physical: list[ManagedSlot], desired: list[Reservation]
) -> tuple[list[tuple[ManagedSlot, Reservation]], list[ManagedSlot], list[Reservation]]:
    """Pair a physical group to a stable-name desired group."""
    if (
        len(desired) > 1
        and len(physical) == len(desired)
        and all(
            slot.actual_start is not None and slot.actual_end is not None
            for slot in physical
        )
    ):
        return list(zip(physical, desired, strict=False)), [], []
    pairs, paired_slots, paired_res = _pair_exact_managed(physical, desired)
    remaining_physical = [slot for slot in physical if slot.slot not in paired_slots]
    remaining_desired = [res for res in desired if res.identity_key not in paired_res]
    if len(remaining_physical) > len(remaining_desired) and remaining_desired:
        canonical = _select_managed_subset(remaining_physical, remaining_desired)
        canonical_slots = {slot.slot for slot in canonical}
        remaining_physical = [
            *canonical,
            *[slot for slot in remaining_physical if slot.slot not in canonical_slots],
        ]
    if len(remaining_physical) < len(remaining_desired):
        pairs.extend(_pair_partial_managed(remaining_physical, remaining_desired))
    else:
        pairs.extend(zip(remaining_physical, remaining_desired, strict=False))
    return pairs, remaining_physical, remaining_desired
