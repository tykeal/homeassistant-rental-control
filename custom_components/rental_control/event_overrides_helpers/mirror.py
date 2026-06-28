# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Slot-anchored mirror matcher helpers for EventOverrides."""

from __future__ import annotations

from ..util import normalize_uid
from .matcher import _excluded
from .matcher import _preferred_same_start_slot
from .matcher import _strict_overlap
from .matcher import _uid_conflict_blocks
from .models import MatchCatalog
from .models import MatchPhase
from .models import MatchRequest
from .models import MatchResult
from .models import OverrideSnapshot
from .trim import is_trimmed_match
from .trim import restored_name


def match_target_slot(
    catalog: MatchCatalog, slot: int, request: MatchRequest
) -> MatchResult:
    """Return whether one target slot matches an event in mirror orientation."""
    snapshot = next(
        (
            candidate
            for candidate in catalog.snapshots
            if candidate.slot == slot and not _excluded(slot, catalog, request)
        ),
        None,
    )
    if snapshot is None:
        return MatchResult(None, None)
    request_uid = normalize_uid(request.uid)
    if (
        snapshot.uid is not None
        and snapshot.uid == request_uid
        and snapshot.slot_name == request.slot_name
    ):
        return MatchResult(slot, MatchPhase.UID_EXACT_NAME)
    exact = _target_exact_overlap(catalog, snapshot, request, request_uid)
    if exact is not None:
        return exact
    return _target_trim_fallback(catalog, snapshot, request, request_uid)


def _target_exact_overlap(
    catalog: MatchCatalog,
    snapshot: OverrideSnapshot,
    request: MatchRequest,
    request_uid: str | None,
) -> MatchResult | None:
    """Return an anchored exact-name overlap match."""
    if snapshot.slot_name != request.slot_name or not _strict_overlap(
        request.start_time, request.end_time, snapshot.start_time, snapshot.end_time
    ):
        return None
    if _uid_conflict_blocks(
        catalog,
        request,
        snapshot,
        request_uid,
        _preferred_same_start_slot(catalog, request),
    ):
        return None
    return MatchResult(snapshot.slot, MatchPhase.EXACT_NAME_STRICT_OVERLAP)


def _target_trim_fallback(
    catalog: MatchCatalog,
    snapshot: OverrideSnapshot,
    request: MatchRequest,
    request_uid: str | None,
) -> MatchResult:
    """Return an anchored trim-aware match."""
    config = catalog.trim_config
    if not config.trim_names:
        return MatchResult(None, None)
    if (
        snapshot.uid is not None
        and snapshot.uid == request_uid
        and snapshot.slot_name != request.slot_name
        and is_trimmed_match(snapshot.slot_name, request.slot_name, config.guest_max)
    ):
        return MatchResult(
            snapshot.slot,
            MatchPhase.TRIM_UID,
            restored_name(snapshot.slot_name, request.slot_name, config.guest_max),
        )
    return _target_trim_overlap(catalog, snapshot, request, request_uid)


def _target_trim_overlap(
    catalog: MatchCatalog,
    snapshot: OverrideSnapshot,
    request: MatchRequest,
    request_uid: str | None,
) -> MatchResult:
    """Return an anchored trim+overlap match."""
    config = catalog.trim_config
    if (
        snapshot.slot_name == request.slot_name
        or not is_trimmed_match(snapshot.slot_name, request.slot_name, config.guest_max)
        or not _strict_overlap(
            request.start_time, request.end_time, snapshot.start_time, snapshot.end_time
        )
        or _uid_conflict_blocks(
            catalog,
            request,
            snapshot,
            request_uid,
            _preferred_same_start_slot(catalog, request),
        )
    ):
        return MatchResult(None, None)
    return MatchResult(
        snapshot.slot,
        MatchPhase.TRIM_STRICT_OVERLAP,
        restored_name(snapshot.slot_name, request.slot_name, config.guest_max),
    )
