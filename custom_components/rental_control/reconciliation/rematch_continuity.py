# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Continuity helpers for reservation rematching."""

from __future__ import annotations

from typing import Any

from .identity import normalize_slot_name_for_fingerprint
from .plan_models import Reservation
from .rematch_dates import _mapping_dates_match_reservation
from .rematch_names import _get_nested
from .rematch_names import _mapping_name_matches_reservation
from .rematch_names import _normalized_name_forms
from .rematch_names import _should_include_observed_mapping


def _is_continuity_compatible(
    reservation: Reservation,
    mapping_key: str,
    mapping: dict[str, Any],
    actual_slot_names: dict[int, str] | None,
    observed_mapping_keys: set[str] | None,
) -> bool:
    """Return ``True`` if *mapping* is continuity-compatible with *reservation*.

    A mapping is continuity-compatible when:

    1. Normalized names match (required baseline).
    2. At least one of the following weak signals is present:

       a. The reservation's current identity key appears in the mapping's
          ``fingerprint_history`` (a prior fingerprint for this mapping).
       b. The mapping's primary ``identity_key`` appears in the
          reservation's :attr:`Reservation.fingerprint_history`.
       c. A booking alias overlaps between the two records.
       d. The actual Keymaster slot name for the mapping's slot matches
          one of the reservation's normalized name forms.

    Args:
        reservation: The incoming reservation candidate.
        mapping_key: Identity key of the persisted mapping being tested.
        mapping: Raw Store mapping dict.
        actual_slot_names: Optional mapping of slot number → current
            Keymaster slot name for actual-slot continuity checks.
        observed_mapping_keys: Mapping keys whose observed state was
            refreshed from physical Keymaster entities this cycle.

    Returns:
        ``True`` if the mapping is continuity-compatible with the
        reservation; ``False`` otherwise.
    """
    if not _mapping_name_matches_reservation(
        mapping,
        reservation,
        actual_slot_names,
        include_observed=_should_include_observed_mapping(
            mapping_key, mapping, observed_mapping_keys
        ),
    ):
        return False

    # Signal (a): reservation's current fingerprint in mapping's history
    fp_history: list[str] = mapping.get("fingerprint_history") or []
    if reservation.identity_key in fp_history:
        return True

    # Signal (b): persisted key in reservation's fingerprint history
    if mapping_key in reservation.fingerprint_history:
        return True

    # Signal (c): booking alias overlap
    persisted_booking: set[str] = set(
        _get_nested(mapping, "identity", "booking_aliases") or []
    )
    if persisted_booking & reservation.booking_aliases:
        return True

    # Signal (d): actual Keymaster slot name matches
    if actual_slot_names is not None:
        slot_num: int | None = mapping.get("slot")
        if slot_num is not None:
            actual_name = actual_slot_names.get(slot_num)
            if actual_name is not None:
                if normalize_slot_name_for_fingerprint(
                    actual_name
                ) in _normalized_name_forms(reservation):
                    return True

    return False


def _has_competing_reservation(
    candidate_key: str,
    persisted_mappings: dict[str, dict[str, Any]],
    current_reservations: list[Reservation] | None,
    this_reservation: Reservation,
    observed_mapping_keys: set[str] | None,
) -> bool:
    """Return ``True`` if another current reservation also matches *candidate_key*.

    Used by :func:`find_reservation_rematch` to verify that a single
    continuity candidate is not also claimed by another current
    reservation (which would make the rematch ambiguous even though
    only one persisted mapping is compatible).

    Args:
        candidate_key: Identity key of the single compatible mapping.
        persisted_mappings: Full raw Store mapping dict.
        current_reservations: All current reservations being reconciled.
        this_reservation: The reservation being matched (excluded from
            the competition check).
        observed_mapping_keys: Mapping keys whose observed state was
            refreshed from physical Keymaster entities this cycle.

    Returns:
        ``True`` if at least one other current reservation competes for
        the candidate mapping by normalized name; ``False`` otherwise.
    """
    if current_reservations is None:
        return False
    candidate_mapping = persisted_mappings.get(candidate_key, {})
    include_observed = _should_include_observed_mapping(
        candidate_key, candidate_mapping, observed_mapping_keys
    )
    candidate_dates_match_this = _mapping_dates_match_reservation(
        candidate_mapping,
        this_reservation,
        include_observed=include_observed,
    )
    for other in current_reservations:
        if other.identity_key == this_reservation.identity_key:
            continue  # skip self
        if _mapping_name_matches_reservation(
            candidate_mapping,
            other,
            include_observed=include_observed,
        ):
            if candidate_dates_match_this and not _mapping_dates_match_reservation(
                candidate_mapping,
                other,
                include_observed=include_observed,
            ):
                continue
            return True
    return False
