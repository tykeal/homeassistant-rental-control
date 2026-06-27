# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Pure physical-slot matching helpers for the coordinator.

These helpers perform no Home Assistant state reads, Store writes,
refresh requests, or service calls.  They operate only on
``ManagedSlot`` observations, reservation data, and the
:class:`~.models.ObservedSlotQuery` value object.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
from functools import lru_cache
from typing import TYPE_CHECKING
from typing import Any

from ..reconciliation import normalize_slot_name_for_fingerprint
from ..util import dt as _dt

if TYPE_CHECKING:
    from ..reconciliation import ManagedSlot
    from ..reconciliation import Reservation
    from .models import ObservedSlotQuery


def find_observed_slot(query: ObservedSlotQuery) -> ManagedSlot | None:
    """Return the current physical slot matching a stable/display name."""
    prefix = query.event_prefix
    desired_forms = {
        normalize_slot_name_for_fingerprint(query.slot_name),
        normalize_slot_name_for_fingerprint(query.display_slot_name),
    }
    consumed = query.consumed_slots if query.consumed_slots is not None else set()
    all_candidates: list[ManagedSlot] = []
    candidates: list[ManagedSlot] = []
    matching_candidate_count = 0
    for slot in sorted(
        query.managed_slots,
        key=lambda observed: (
            observed.actual_start or datetime.max.replace(tzinfo=timezone.utc),
            observed.actual_end or datetime.max.replace(tzinfo=timezone.utc),
            observed.slot,
        ),
    ):
        if not slot.managed or not slot.actual_name:
            continue
        actual = slot.actual_name
        actual_forms = {normalize_slot_name_for_fingerprint(actual)}
        if prefix and actual.startswith(prefix):
            actual_forms.add(normalize_slot_name_for_fingerprint(actual[len(prefix) :]))
        if actual_forms & desired_forms:
            matching_candidate_count += 1
            all_candidates.append(slot)
            if slot.slot in consumed:
                continue
            candidates.append(slot)
    if query.require_date_match:
        return _match_with_date_constraint(
            query, consumed, all_candidates, candidates, matching_candidate_count
        )
    return _match_without_date_constraint(query, consumed, candidates)


def _match_with_date_constraint(
    query: ObservedSlotQuery,
    consumed: set[int],
    all_candidates: list[ManagedSlot],
    candidates: list[ManagedSlot],
    matching_candidate_count: int,
) -> ManagedSlot | None:
    """Return the date-constrained physical match for duplicate names."""
    desired_start = query.desired_start
    desired_end = query.desired_end
    ordered_date_windows = query.ordered_date_windows
    reserved_date_windows = query.reserved_date_windows
    if matching_candidate_count < query.expected_name_count:
        if desired_start is not None and desired_end is not None:
            for slot in candidates:
                if (
                    slot.actual_start == desired_start
                    and slot.actual_end == desired_end
                ):
                    consumed.add(slot.slot)
                    return slot
        if ordered_date_windows:
            pairings = select_partial_ordered_pairings(
                all_candidates, ordered_date_windows
            )
            desired_window = (
                (desired_start, desired_end)
                if desired_start is not None and desired_end is not None
                else None
            )
            matched_slot = (
                pairings.get(desired_window) if desired_window is not None else None
            )
            if matched_slot is not None and matched_slot.slot not in consumed:
                consumed.add(matched_slot.slot)
                return matched_slot
            return None
        shifted_candidates = [
            slot
            for slot in candidates
            if slot.actual_start is not None
            and slot.actual_end is not None
            and (
                not reserved_date_windows
                or (slot.actual_start, slot.actual_end) not in reserved_date_windows
            )
        ]
        if len(shifted_candidates) == 1:
            consumed.add(shifted_candidates[0].slot)
            return shifted_candidates[0]
        return None
    if any(
        slot.actual_start is None or slot.actual_end is None for slot in all_candidates
    ):
        return None
    if ordered_date_windows and matching_candidate_count > query.expected_name_count:
        canonical = select_ordered_physical_subset(all_candidates, ordered_date_windows)
        desired_window = (
            (desired_start, desired_end)
            if desired_start is not None and desired_end is not None
            else None
        )
        for slot, window in zip(canonical, ordered_date_windows, strict=False):
            if slot.slot not in consumed and (
                desired_window is None or window == desired_window
            ):
                consumed.add(slot.slot)
                return slot
        return None
    if candidates:
        consumed.add(candidates[0].slot)
        return candidates[0]
    return None


def _match_without_date_constraint(
    query: ObservedSlotQuery,
    consumed: set[int],
    candidates: list[ManagedSlot],
) -> ManagedSlot | None:
    """Return the unique-name physical match with unknown-date fencing."""
    desired_start = query.desired_start
    desired_end = query.desired_end
    if desired_start is not None and desired_end is not None:
        for slot in candidates:
            if slot.actual_start == desired_start and slot.actual_end == desired_end:
                consumed.add(slot.slot)
                return slot
    fallback_candidates = candidates
    reserved_date_windows = query.reserved_date_windows
    if reserved_date_windows and query.block_unknown_date_fallback:
        fallback_candidates = [
            slot
            for slot in candidates
            if slot.actual_start is not None
            and slot.actual_end is not None
            and (slot.actual_start, slot.actual_end) not in reserved_date_windows
        ]
    if fallback_candidates:
        consumed.add(fallback_candidates[0].slot)
        return fallback_candidates[0]
    return None


def select_ordered_physical_subset(
    slots: list[ManagedSlot], desired_windows: list[tuple[datetime, datetime]]
) -> list[ManagedSlot]:
    """Return minimum-distance ordered physical subset for desired windows."""

    def _distance(slot: ManagedSlot, window: tuple[datetime, datetime]) -> float:
        """Return absolute date distance for one physical/desired pair."""
        assert slot.actual_start is not None
        assert slot.actual_end is not None
        return abs((slot.actual_start - window[0]).total_seconds()) + abs(
            (slot.actual_end - window[1]).total_seconds()
        )

    @lru_cache(maxsize=None)
    def _best(slot_index: int, desired_index: int) -> tuple[float, tuple[int, ...]]:
        """Return best ordered subset cost and indices from this position."""
        if desired_index == len(desired_windows):
            return 0.0, ()
        if slot_index == len(slots):
            return float("inf"), ()
        skip_cost, skip_indices = _best(slot_index + 1, desired_index)
        take_rest_cost, take_indices = _best(slot_index + 1, desired_index + 1)
        take_cost = (
            _distance(slots[slot_index], desired_windows[desired_index])
            + take_rest_cost
        )
        if take_cost < skip_cost:
            return take_cost, (slot_index, *take_indices)
        return skip_cost, skip_indices

    _, indices = _best(0, 0)
    return [slots[index] for index in indices]


def select_partial_ordered_pairings(
    slots: list[ManagedSlot], desired_windows: list[tuple[datetime, datetime]]
) -> dict[tuple[datetime, datetime], ManagedSlot]:
    """Return ordered pairings when physical duplicates are missing."""
    dated_slots = [
        slot
        for slot in slots
        if slot.actual_start is not None and slot.actual_end is not None
    ]
    if not dated_slots:
        return {}

    def _distance(slot: ManagedSlot, window: tuple[datetime, datetime]) -> float:
        """Return absolute date distance for one physical/desired pair."""
        assert slot.actual_start is not None
        assert slot.actual_end is not None
        return abs((slot.actual_start - window[0]).total_seconds()) + abs(
            (slot.actual_end - window[1]).total_seconds()
        )

    @lru_cache(maxsize=None)
    def _best(slot_index: int, desired_index: int) -> tuple[float, tuple[int, ...]]:
        """Return best desired-window indices for remaining physical slots."""
        if slot_index == len(dated_slots):
            return 0.0, ()
        if desired_index == len(desired_windows):
            return float("inf"), ()
        skip_cost, skip_indices = _best(slot_index, desired_index + 1)
        take_rest_cost, take_indices = _best(slot_index + 1, desired_index + 1)
        take_cost = (
            _distance(dated_slots[slot_index], desired_windows[desired_index])
            + take_rest_cost
        )
        if take_cost < skip_cost:
            return take_cost, (desired_index, *take_indices)
        return skip_cost, skip_indices

    _, indices = _best(0, 0)
    return {
        desired_windows[desired_index]: dated_slots[slot_index]
        for slot_index, desired_index in enumerate(indices)
    }


def physical_slot_name_matches_name(
    actual_name: str | None,
    slot_name: str,
    display_slot_name: str,
    prefix: str,
) -> bool:
    """Return whether a physical display name matches a logical name."""
    if not actual_name:
        return False
    actual_forms = {normalize_slot_name_for_fingerprint(actual_name)}
    if prefix and actual_name.startswith(prefix):
        actual_forms.add(
            normalize_slot_name_for_fingerprint(actual_name[len(prefix) :])
        )
    desired_forms = {
        normalize_slot_name_for_fingerprint(slot_name),
        normalize_slot_name_for_fingerprint(display_slot_name),
    }
    return bool(actual_forms & desired_forms)


def observed_value_as_datetime(value: Any) -> datetime | None:
    """Return a datetime for an observed Store value, if parseable."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = _dt.parse_datetime(value)
        if not isinstance(parsed, datetime):
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def physical_mapping_name_matches_reservation(
    mapping: dict[str, Any],
    reservation: Reservation,
    actual_slot_names: dict[int, str],
) -> bool:
    """Return whether a fresh physical slot name matches a reservation."""
    slot_num = mapping.get("slot")
    if not isinstance(slot_num, int):
        return False
    actual_name = actual_slot_names.get(slot_num)
    if not actual_name:
        return False
    actual_name_form = normalize_slot_name_for_fingerprint(actual_name)
    reservation_name_forms = {
        normalize_slot_name_for_fingerprint(reservation.slot_name),
        normalize_slot_name_for_fingerprint(reservation.display_slot_name),
    }
    return actual_name_form in reservation_name_forms


def physical_mapping_matches_reservation(
    mapping: dict[str, Any],
    reservation: Reservation,
    actual_slot_names: dict[int, str],
) -> bool:
    """Return whether fresh physical state identifies a reservation."""
    if not physical_mapping_name_matches_reservation(
        mapping, reservation, actual_slot_names
    ):
        return False

    actual = mapping.get("last_observed_actual", {})
    if not isinstance(actual, dict):
        return True
    actual_start = observed_value_as_datetime(actual.get("start_state"))
    actual_end = observed_value_as_datetime(actual.get("end_state"))
    if actual_start is None or actual_end is None:
        return True
    return (
        actual_start == reservation.buffered_start
        and actual_end == reservation.buffered_end
    )


def remap_observed_mappings_to_physical_reservations(
    persisted: dict[str, Any],
    current_reservations: list[Reservation],
    actual_slot_names: dict[int, str],
    observed_mapping_keys: set[str],
) -> set[str]:
    """Atomically re-key stale Store mappings to current physical occupants."""
    remap: dict[str, str] = {}
    target_counts: dict[str, int] = {}
    for mapping_key in observed_mapping_keys:
        mapping = persisted.get(mapping_key)
        if not isinstance(mapping, dict):
            continue
        matches = [
            res.identity_key
            for res in current_reservations
            if physical_mapping_matches_reservation(mapping, res, actual_slot_names)
        ]
        if len(matches) != 1:
            continue
        target_key = matches[0]
        remap[mapping_key] = target_key
        target_counts[target_key] = target_counts.get(target_key, 0) + 1

    remap = {
        source: target
        for source, target in remap.items()
        if target_counts.get(target) == 1
    }
    if not remap:
        return observed_mapping_keys

    sources = set(remap)
    safe_remap = {
        source: target
        for source, target in remap.items()
        if target not in persisted
        or target in sources
        or target == source
        or target not in observed_mapping_keys
    }
    if not safe_remap:
        return observed_mapping_keys

    original_items = list(persisted.items())
    rebuilt: dict[str, Any] = {}
    replaced_stale_targets = {
        target
        for source, target in safe_remap.items()
        if target != source and target in persisted and target not in sources
    }
    for source_key, mapping in original_items:
        if source_key in replaced_stale_targets:
            continue
        target_key = safe_remap.get(source_key, source_key)
        if target_key in rebuilt:
            return observed_mapping_keys
        if target_key != source_key:
            history = set(mapping.get("fingerprint_history", []))
            history.add(source_key)
            mapping["fingerprint_history"] = sorted(history)
            identity = mapping.setdefault("identity", {})
            if isinstance(identity, dict):
                identity["identity_key"] = target_key
        rebuilt[target_key] = mapping

    persisted.clear()
    persisted.update(rebuilt)
    return {safe_remap.get(key, key) for key in observed_mapping_keys}
