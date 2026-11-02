# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Re-key stale Store mappings to the reservations physically on the lock."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING
from typing import Any

from .slot_matching import observed_value_as_datetime
from .slot_matching import physical_mapping_matches_reservation
from .slot_matching import physical_mapping_name_matches_reservation
from .store_sync import ADOPTED_KEY_PREFIX

if TYPE_CHECKING:
    from ..reconciliation import Reservation


def _unique_targets(remap: dict[str, str]) -> dict[str, str]:
    """Drop every remap whose target is claimed by more than one mapping."""
    counts = Counter(remap.values())
    return {source: target for source, target in remap.items() if counts[target] == 1}


def _amended_stay_remap(
    persisted: dict[str, Any],
    unmatched: list[str],
    current_reservations: list[Reservation],
    actual_slot_names: dict[int, str],
    claimed: dict[str, str],
) -> dict[str, str]:
    """Re-key adopted mappings whose stay was amended before the lock caught up.

    The lock keeps the old window until the amended dates are written, so
    the exact match fails and the mapping would otherwise ghost (#867).
    An adopted mapping follows the one current reservation carrying the
    slot's physical name, provided no current reservation still owns the
    lock's window and no other mapping claims that reservation. Mappings
    written from the calendar are left alone: an amended stay keeps its
    key through its UID, so if one is unmatched its stay really left the
    feed and the ghost path must keep its slot.
    """
    claimed_targets = set(claimed.values())
    remap: dict[str, str] = {}
    for mapping_key in unmatched:
        if not mapping_key.startswith(ADOPTED_KEY_PREFIX):
            continue
        mapping = persisted[mapping_key]
        if not physical_mapping_window_is_unowned(mapping, current_reservations):
            continue
        matches = [
            res.identity_key
            for res in current_reservations
            if physical_mapping_name_matches_reservation(
                mapping, res, actual_slot_names
            )
        ]
        if len(matches) == 1 and matches[0] not in claimed_targets:
            remap[mapping_key] = matches[0]
    return _unique_targets(remap)


def physical_mapping_window_is_unowned(
    mapping: dict[str, Any], current_reservations: list[Reservation]
) -> bool:
    """Return whether a known lock window belongs to no current reservation."""
    actual = mapping.get("last_observed_actual", {})
    if not isinstance(actual, dict):
        return False
    window = (
        observed_value_as_datetime(actual.get("start_state")),
        observed_value_as_datetime(actual.get("end_state")),
    )
    if None in window:
        return False
    return all(
        window != (res.buffered_start, res.buffered_end) for res in current_reservations
    )


def remap_observed_mappings_to_physical_reservations(
    persisted: dict[str, Any],
    current_reservations: list[Reservation],
    actual_slot_names: dict[int, str],
    observed_mapping_keys: set[str],
) -> set[str]:
    """Atomically re-key stale Store mappings to current physical occupants.

    A mapping is matched when the slot's physical name and programmed
    window both identify exactly one current reservation. A mapping with
    no such match can still follow an amended stay whose lock window is
    stale: see :func:`_amended_stay_remap`.
    """
    remap: dict[str, str] = {}
    unmatched: list[str] = []
    for mapping_key in observed_mapping_keys:
        mapping = persisted.get(mapping_key)
        if not isinstance(mapping, dict):
            continue
        matches = [
            res.identity_key
            for res in current_reservations
            if physical_mapping_matches_reservation(mapping, res, actual_slot_names)
        ]
        if len(matches) == 1:
            remap[mapping_key] = matches[0]
        elif not matches:
            unmatched.append(mapping_key)

    remap = _unique_targets(remap)
    remap.update(
        _amended_stay_remap(
            persisted, unmatched, current_reservations, actual_slot_names, remap
        )
    )
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
