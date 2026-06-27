# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Name helpers for reservation rematching."""

from __future__ import annotations

from typing import Any

from .identity import normalize_slot_name_for_fingerprint
from .plan_models import Reservation


def _get_nested(d: dict[str, Any], *keys: str) -> Any:
    """Safely navigate nested dict keys; return ``None`` on any miss."""
    current: Any = d
    for k in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(k)
    return current


def _normalized_name_forms(reservation: Reservation) -> set[str]:
    """Return normalized reservation names that may appear in Keymaster."""
    forms = {normalize_slot_name_for_fingerprint(reservation.slot_name)}
    if reservation.display_slot_name:
        forms.add(normalize_slot_name_for_fingerprint(reservation.display_slot_name))
    return {form for form in forms if form}


def _mapping_name_forms(
    mapping: dict[str, Any],
    actual_slot_names: dict[int, str] | None,
    *,
    include_observed: bool = False,
) -> set[str]:
    """Return normalized persisted and observed names for a mapping."""
    forms: set[str] = set()
    persisted_name = _get_nested(mapping, "identity", "slot_name")
    if persisted_name:
        forms.add(normalize_slot_name_for_fingerprint(str(persisted_name)))

    if include_observed:
        last_observed_name = _get_nested(mapping, "last_observed_actual", "name_state")
        if last_observed_name:
            forms.add(normalize_slot_name_for_fingerprint(str(last_observed_name)))

    if include_observed and actual_slot_names is not None:
        slot_num: int | None = mapping.get("slot")
        if slot_num is not None:
            actual_name = actual_slot_names.get(slot_num)
            if actual_name:
                forms.add(normalize_slot_name_for_fingerprint(actual_name))

    return {form for form in forms if form}


def _mapping_name_matches_reservation(
    mapping: dict[str, Any],
    reservation: Reservation,
    actual_slot_names: dict[int, str] | None = None,
    *,
    include_observed: bool = False,
) -> bool:
    """Return whether a mapping name matches a reservation name form.

    Adopted slots often only have the observed Keymaster display name,
    which may be prefixed or trimmed compared with the full calendar feed
    name.  Compare all persisted/observed forms against both the full
    slot name and the display name the coordinator would write.
    """
    return bool(
        _mapping_name_forms(
            mapping, actual_slot_names, include_observed=include_observed
        )
        & _normalized_name_forms(reservation)
    )


def _is_adopted_mapping(mapping_key: str, mapping: dict[str, Any]) -> bool:
    """Return whether a persisted mapping was created by first-upgrade adoption."""
    identity_key = _get_nested(mapping, "identity", "identity_key")
    return mapping_key.startswith("adopted.") or (
        isinstance(identity_key, str) and identity_key.startswith("adopted.")
    )


def _should_include_observed_mapping(
    mapping_key: str,
    mapping: dict[str, Any],
    observed_mapping_keys: set[str] | None,
) -> bool:
    """Return whether observed physical fields may participate in rematch."""
    return _is_adopted_mapping(mapping_key, mapping) or (
        observed_mapping_keys is not None and mapping_key in observed_mapping_keys
    )


def _fresh_observed_name_conflicts(
    reservation: Reservation,
    mapping_key: str,
    mapping: dict[str, Any],
    actual_slot_names: dict[int, str] | None,
    observed_mapping_keys: set[str] | None,
) -> bool:
    """Return whether a fresh physical slot name contradicts an exact mapping."""
    if observed_mapping_keys is None or mapping_key not in observed_mapping_keys:
        return False
    if actual_slot_names is None:
        return False
    slot_num: int | None = mapping.get("slot")
    if slot_num is None:
        return False
    actual_name = actual_slot_names.get(slot_num)
    if not actual_name:
        return False
    return normalize_slot_name_for_fingerprint(
        actual_name
    ) not in _normalized_name_forms(reservation)
