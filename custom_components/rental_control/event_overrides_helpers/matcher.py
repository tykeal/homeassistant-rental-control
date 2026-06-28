# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Pure shared matcher helpers for EventOverrides."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
from typing import Any

from ..util import normalize_uid
from .models import MatchCatalog
from .models import MatchPhase
from .models import MatchRequest
from .models import MatchResult
from .models import OverrideSnapshot
from .slot_bookkeeping import get_slots_with_values
from .trim import is_trimmed_match
from .trim import restored_name


def build_match_catalog(
    overrides: dict[int, dict[str, Any] | None],
    slot_uids: dict[int, str | None],
    trim_config,
    exclude_slot: int | None = None,
    slot_dates: dict[int, tuple[Any, Any]] | None = None,
) -> MatchCatalog:
    """Serialize shell override state into a pure matcher catalog."""
    snapshots: list[OverrideSnapshot] = []
    for slot in get_slots_with_values(overrides):
        override = overrides.get(slot)
        if override is None:
            continue
        snapshots.append(
            OverrideSnapshot(
                slot=slot,
                slot_name=override["slot_name"],
                slot_code_present=bool(override["slot_code"]),
                start_time=override["start_time"],
                end_time=override["end_time"],
                uid=normalize_uid(slot_uids.get(slot)),
            )
        )
    return MatchCatalog(
        snapshots=snapshots,
        trim_config=trim_config,
        exclude_slot=exclude_slot,
        slot_dates=slot_dates,
    )


def find_uid_positive_exact_name(
    catalog: MatchCatalog,
    request: MatchRequest,
) -> MatchResult | None:
    """Return a UID-positive exact-name match without overlap checks."""
    uid = normalize_uid(request.uid)
    if uid is None:
        return None
    for snapshot in catalog.snapshots:
        if _excluded(snapshot.slot, catalog, request):
            continue
        if snapshot.uid == uid and snapshot.slot_name == request.slot_name:
            return MatchResult(snapshot.slot, MatchPhase.UID_EXACT_NAME)
    return None


def find_exact_name_strict_overlap(
    catalog: MatchCatalog,
    request: MatchRequest,
) -> MatchResult | None:
    """Return an exact-name overlap match with same-start UID bypass."""
    request_uid = normalize_uid(request.uid)
    preferred_slot = _preferred_same_start_slot(catalog, request)
    for snapshot in catalog.snapshots:
        if _excluded(snapshot.slot, catalog, request):
            continue
        if snapshot.slot_name != request.slot_name:
            continue
        if not _strict_overlap(
            request.start_time, request.end_time, snapshot.start_time, snapshot.end_time
        ):
            continue
        if _uid_conflict_blocks(
            catalog, request, snapshot, request_uid, preferred_slot
        ):
            continue
        return MatchResult(snapshot.slot, MatchPhase.EXACT_NAME_STRICT_OVERLAP)
    return None


def find_trim_aware_fallback(
    catalog: MatchCatalog,
    request: MatchRequest,
) -> MatchResult | None:
    """Return a trim-aware fallback match across UID and overlap phases."""
    config = catalog.trim_config
    if not config.trim_names:
        return None
    request_uid = normalize_uid(request.uid)
    preferred_slot = _preferred_same_start_slot(catalog, request)
    if request_uid is not None:
        for snapshot in catalog.snapshots:
            if _excluded(snapshot.slot, catalog, request):
                continue
            if snapshot.uid != request_uid or snapshot.slot_name == request.slot_name:
                continue
            if not is_trimmed_match(
                snapshot.slot_name, request.slot_name, config.guest_max
            ):
                continue
            return MatchResult(
                snapshot.slot,
                MatchPhase.TRIM_UID,
                restored_name(snapshot.slot_name, request.slot_name, config.guest_max),
            )
    for snapshot in catalog.snapshots:
        if _excluded(snapshot.slot, catalog, request):
            continue
        if snapshot.slot_name == request.slot_name:
            continue
        if not is_trimmed_match(
            snapshot.slot_name, request.slot_name, config.guest_max
        ):
            continue
        if not _strict_overlap(
            request.start_time, request.end_time, snapshot.start_time, snapshot.end_time
        ):
            continue
        if _uid_conflict_blocks(
            catalog, request, snapshot, request_uid, preferred_slot
        ):
            continue
        return MatchResult(
            snapshot.slot,
            MatchPhase.TRIM_STRICT_OVERLAP,
            restored_name(snapshot.slot_name, request.slot_name, config.guest_max),
        )
    return None


def _has_other_uid_owner(
    catalog: MatchCatalog,
    slot_name: str,
    uid: str | None,
    exclude_slot: int | None,
) -> bool:
    """Return whether another snapshot already owns ``uid`` for ``slot_name``."""
    normalized_uid = normalize_uid(uid)
    if normalized_uid is None:
        return False
    guest_max = catalog.trim_config.guest_max
    for snapshot in catalog.snapshots:
        if snapshot.slot == exclude_slot or snapshot.uid != normalized_uid:
            continue
        if snapshot.slot_name == slot_name:
            return True
        if catalog.trim_config.trim_names and is_trimmed_match(
            snapshot.slot_name, slot_name, guest_max
        ):
            return True
    return False


def _get_same_start_uid_bypass_slot(
    catalog: MatchCatalog,
    event_slot_name: str,
    event_start_utc: datetime,
    event_end_utc: datetime,
    event_uid: str | None,
    exclude_slot: int | None,
) -> int | None:
    """Return the preferred same-start fallback slot for an incoming event."""
    del event_uid
    best_slot: int | None = None
    best_distance: float | None = None
    best_exact = False
    guest_max = catalog.trim_config.guest_max
    for snapshot in catalog.snapshots:
        if snapshot.slot == exclude_slot:
            continue
        exact_name = snapshot.slot_name == event_slot_name
        if not exact_name and (
            not catalog.trim_config.trim_names
            or not is_trimmed_match(snapshot.slot_name, event_slot_name, guest_max)
        ):
            continue
        if not _strict_overlap(
            event_start_utc, event_end_utc, snapshot.start_time, snapshot.end_time
        ):
            continue
        candidate_start = _to_utc(snapshot.start_time)
        if candidate_start != event_start_utc:
            continue
        distance = abs((_to_utc(snapshot.end_time) - event_end_utc).total_seconds())
        if _better_same_start_choice(
            best_slot,
            best_distance,
            best_exact,
            snapshot.slot,
            distance,
            exact_name,
        ):
            best_slot = snapshot.slot
            best_distance = distance
            best_exact = exact_name
    return best_slot


def match_slot(catalog: MatchCatalog, request: MatchRequest) -> MatchResult:
    """Run the full three-phase matcher and return the selected slot."""
    for finder in (
        find_uid_positive_exact_name,
        find_exact_name_strict_overlap,
        find_trim_aware_fallback,
    ):
        result = finder(catalog, request)
        if result is not None:
            return result
    return MatchResult(None, None)


def _better_same_start_choice(
    best_slot: int | None,
    best_distance: float | None,
    best_exact: bool,
    slot: int,
    distance: float,
    exact_name: bool,
) -> bool:
    """Return whether a candidate wins the same-start tie-break."""
    return (
        best_slot is None
        or best_distance is None
        or distance < best_distance
        or (
            distance == best_distance
            and (
                (exact_name and not best_exact)
                or (exact_name == best_exact and slot < best_slot)
            )
        )
    )


def _excluded(slot: int, catalog: MatchCatalog, request: MatchRequest) -> bool:
    """Return whether ``slot`` is excluded for the current request."""
    return slot in {catalog.exclude_slot, request.exclude_slot}


def _preferred_same_start_slot(
    catalog: MatchCatalog, request: MatchRequest
) -> int | None:
    """Return the preferred same-start slot for a request with a UID."""
    request_uid = normalize_uid(request.uid)
    if request_uid is None:
        return None
    return _get_same_start_uid_bypass_slot(
        catalog,
        request.slot_name,
        _to_utc(request.start_time),
        _to_utc(request.end_time),
        request_uid,
        request.exclude_slot
        if request.exclude_slot is not None
        else catalog.exclude_slot,
    )


def _strict_overlap(
    start_a: datetime,
    end_a: datetime,
    start_b: datetime,
    end_b: datetime,
) -> bool:
    """Return whether two windows strictly overlap in UTC."""
    return _to_utc(start_a) < _to_utc(end_b) and _to_utc(start_b) < _to_utc(end_a)


def _to_utc(value: datetime) -> datetime:
    """Normalize a datetime to UTC without Home Assistant imports."""
    if value.tzinfo is None or value.utcoffset() is None:
        return value.astimezone().astimezone(timezone.utc)
    return value.astimezone(timezone.utc)


def _uid_conflict_blocks(
    catalog: MatchCatalog,
    request: MatchRequest,
    snapshot: OverrideSnapshot,
    request_uid: str | None,
    preferred_slot: int | None,
) -> bool:
    """Return whether UID ownership rules reject a candidate snapshot."""
    if request_uid is None:
        return False
    if snapshot.uid is None:
        return _has_other_uid_owner(
            catalog, request.slot_name, request_uid, snapshot.slot
        ) or (preferred_slot is not None and preferred_slot != snapshot.slot)
    if request_uid == snapshot.uid:
        return False
    same_start = _to_utc(request.start_time) == _to_utc(snapshot.start_time)
    return (
        not same_start
        or _has_other_uid_owner(catalog, request.slot_name, request_uid, snapshot.slot)
        or preferred_slot != snapshot.slot
    )
