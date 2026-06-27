# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Reservation rematch dispatcher and rule helpers."""

from __future__ import annotations

import logging
from typing import Any

from .plan_models import Reservation
from .rematch_continuity import _has_competing_reservation
from .rematch_continuity import _is_continuity_compatible
from .rematch_dates import _mapping_dates_match_reservation
from .rematch_models import RematchKind
from .rematch_models import RematchResult
from .rematch_names import _fresh_observed_name_conflicts
from .rematch_names import _get_nested
from .rematch_names import _mapping_name_matches_reservation
from .rematch_names import _should_include_observed_mapping

_LOGGER = logging.getLogger(__name__)


def _candidate_mappings(
    reservation: Reservation,
    persisted_mappings: dict[str, dict[str, Any]],
    actual_slot_names: dict[int, str] | None,
    observed_mapping_keys: set[str] | None,
) -> list[tuple[str, dict[str, Any]]]:
    """Return rematch candidates not contradicted by fresh physical names."""
    return [
        (mapping_key, mapping)
        for mapping_key, mapping in persisted_mappings.items()
        if not _fresh_observed_name_conflicts(
            reservation,
            mapping_key,
            mapping,
            actual_slot_names,
            observed_mapping_keys,
        )
    ]


def _exact_match_result(
    reservation: Reservation,
    persisted_mappings: dict[str, dict[str, Any]],
    actual_slot_names: dict[int, str] | None,
    observed_mapping_keys: set[str] | None,
) -> RematchResult | None:
    """Return the exact-fingerprint rematch result when it is still valid."""
    if reservation.identity_key not in persisted_mappings:
        return None
    exact_mapping = persisted_mappings[reservation.identity_key]
    if not _fresh_observed_name_conflicts(
        reservation,
        reservation.identity_key,
        exact_mapping,
        actual_slot_names,
        observed_mapping_keys,
    ):
        return RematchResult(
            kind=RematchKind.EXACT,
            matched_identity_key=reservation.identity_key,
        )
    _LOGGER.debug(
        "Exact persisted mapping %s skipped because current physical slot name "
        "conflicts with the reservation",
        reservation.identity_key,
    )
    return None


def _mapping_matches_name(
    mapping_key: str,
    mapping: dict[str, Any],
    reservation: Reservation,
    actual_slot_names: dict[int, str] | None,
    observed_mapping_keys: set[str] | None,
) -> bool:
    """Return whether a candidate mapping's names match the reservation."""
    return _mapping_name_matches_reservation(
        mapping,
        reservation,
        actual_slot_names,
        include_observed=_should_include_observed_mapping(
            mapping_key, mapping, observed_mapping_keys
        ),
    )


def _alias_rule_result(
    matches: list[str], kind: RematchKind, *, date_shifted: bool = False
) -> RematchResult | None:
    """Return a rematch result for an alias rule's candidate keys."""
    if len(matches) == 1:
        return RematchResult(
            kind=kind,
            matched_identity_key=matches[0],
            date_shifted=date_shifted,
        )
    if len(matches) > 1:
        return RematchResult(
            kind=RematchKind.AMBIGUOUS,
            matched_identity_key=None,
            ambiguous_keys=matches,
        )
    return None


def _uid_alias_result(
    reservation: Reservation,
    candidates: list[tuple[str, dict[str, Any]]],
    actual_slot_names: dict[int, str] | None,
    observed_mapping_keys: set[str] | None,
) -> RematchResult | None:
    """Return the UID-alias rematch result when a unique match exists."""
    matches: list[str] = []
    for mapping_key, mapping in candidates:
        persisted_uids: set[str] = set(
            _get_nested(mapping, "identity", "uid_aliases") or []
        )
        if persisted_uids & reservation.uid_aliases and _mapping_matches_name(
            mapping_key, mapping, reservation, actual_slot_names, observed_mapping_keys
        ):
            matches.append(mapping_key)
    return _alias_rule_result(matches, RematchKind.UID_ALIAS, date_shifted=True)


def _booking_alias_result(
    reservation: Reservation,
    candidates: list[tuple[str, dict[str, Any]]],
    actual_slot_names: dict[int, str] | None,
    observed_mapping_keys: set[str] | None,
) -> RematchResult | None:
    """Return the booking-alias rematch result when a unique match exists."""
    matches: list[str] = []
    for mapping_key, mapping in candidates:
        persisted_booking: set[str] = set(
            _get_nested(mapping, "identity", "booking_aliases") or []
        )
        if persisted_booking & reservation.booking_aliases and _mapping_matches_name(
            mapping_key, mapping, reservation, actual_slot_names, observed_mapping_keys
        ):
            matches.append(mapping_key)
    return _alias_rule_result(matches, RematchKind.BOOKING_ALIAS)


def _name_time_result(
    reservation: Reservation,
    candidates: list[tuple[str, dict[str, Any]]],
    actual_slot_names: dict[int, str] | None,
    observed_mapping_keys: set[str] | None,
) -> RematchResult | None:
    """Return the name-plus-time rematch result when a unique match exists."""
    matches: list[str] = []
    for mapping_key, mapping in candidates:
        if not _mapping_matches_name(
            mapping_key, mapping, reservation, actual_slot_names, observed_mapping_keys
        ):
            continue
        if _mapping_dates_match_reservation(mapping, reservation):
            matches.append(mapping_key)
    return _alias_rule_result(matches, RematchKind.NAME_TIME)


def _continuity_date_matches(
    candidates: list[str],
    reservation: Reservation,
    persisted_mappings: dict[str, dict[str, Any]],
    observed_mapping_keys: set[str] | None,
) -> list[str]:
    """Return continuity candidates whose stored or observed dates match."""
    return [
        candidate
        for candidate in candidates
        if _mapping_dates_match_reservation(
            persisted_mappings[candidate],
            reservation,
            include_observed=_should_include_observed_mapping(
                candidate, persisted_mappings[candidate], observed_mapping_keys
            ),
        )
    ]


def _continuity_result(
    reservation: Reservation,
    persisted_mappings: dict[str, dict[str, Any]],
    current_reservations: list[Reservation] | None,
    actual_slot_names: dict[int, str] | None,
    observed_mapping_keys: set[str] | None,
    candidates: list[tuple[str, dict[str, Any]]],
) -> RematchResult | None:
    """Return the conservative-continuity rematch result when applicable."""
    keys = [
        key
        for key, mapping in candidates
        if _is_continuity_compatible(
            reservation, key, mapping, actual_slot_names, observed_mapping_keys
        )
    ]
    if len(keys) == 1:
        if not _has_competing_reservation(
            keys[0],
            persisted_mappings,
            current_reservations,
            reservation,
            observed_mapping_keys,
        ):
            return RematchResult(
                kind=RematchKind.CONTINUITY, matched_identity_key=keys[0]
            )
        return RematchResult(
            kind=RematchKind.AMBIGUOUS,
            matched_identity_key=None,
            ambiguous_keys=list(keys),
        )
    if len(keys) <= 1:
        return None
    date_matches = _continuity_date_matches(
        keys, reservation, persisted_mappings, observed_mapping_keys
    )
    if len(date_matches) == 1:
        return RematchResult(
            kind=RematchKind.CONTINUITY, matched_identity_key=date_matches[0]
        )
    return RematchResult(
        kind=RematchKind.AMBIGUOUS, matched_identity_key=None, ambiguous_keys=list(keys)
    )


def find_reservation_rematch(
    reservation: Reservation,
    persisted_mappings: dict[str, dict[str, Any]],
    current_reservations: list[Reservation] | None = None,
    actual_slot_names: dict[int, str] | None = None,
    observed_mapping_keys: set[str] | None = None,
) -> RematchResult:
    """Find the best identity rematch for *reservation* in persisted mappings."""
    exact = _exact_match_result(
        reservation, persisted_mappings, actual_slot_names, observed_mapping_keys
    )
    if exact is not None:
        return exact
    candidates = _candidate_mappings(
        reservation, persisted_mappings, actual_slot_names, observed_mapping_keys
    )
    for result in (
        _uid_alias_result(
            reservation, candidates, actual_slot_names, observed_mapping_keys
        ),
        _booking_alias_result(
            reservation, candidates, actual_slot_names, observed_mapping_keys
        ),
        _name_time_result(
            reservation, candidates, actual_slot_names, observed_mapping_keys
        ),
        _continuity_result(
            reservation,
            persisted_mappings,
            current_reservations,
            actual_slot_names,
            observed_mapping_keys,
            candidates,
        ),
    ):
        if result is not None:
            return result
    return RematchResult(kind=RematchKind.NO_MATCH, matched_identity_key=None)
