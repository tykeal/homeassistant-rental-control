# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Pure retired-greedy cleanup decisions."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any
from typing import cast

from .mirror import match_target_slot
from .models import EvictionAction
from .models import EvictionDecision
from .models import EvictionReason
from .models import MatchCatalog
from .models import MatchRequest
from .models import OverrideSnapshot
from .models import TrimConfig

_SLOT_MISS_THRESHOLD = 2


def compute_eviction_decisions(
    snapshots: MatchCatalog | Sequence[OverrideSnapshot],
    event_ids: Sequence[Any],
    calendar: Sequence[Any],
    max_events: int,
    slot_miss_counts: dict[int, int],
    cur_date: date,
) -> list[EvictionDecision]:
    """Return pure greedy cleanup decisions for assigned slots."""
    catalog = _catalog_from(snapshots)
    if not catalog.snapshots:
        return []
    last_end = _last_calendar_end(calendar, max_events)
    decisions: list[EvictionDecision] = []
    for snapshot in catalog.snapshots:
        decision = _match_decision(
            catalog, snapshot, event_ids, slot_miss_counts, cur_date
        )
        if not calendar:
            decision = EvictionDecision(
                snapshot.slot,
                EvictionAction.CLEAR,
                reason=EvictionReason.EMPTY_CALENDAR,
            )
        elif decision.action is not EvictionAction.CLEAR and _start_date(
            catalog, snapshot
        ) > _end_date(catalog, snapshot):
            decision = EvictionDecision(
                snapshot.slot,
                EvictionAction.CLEAR,
                reason=EvictionReason.MALFORMED_WINDOW,
            )
        elif (
            decision.action is not EvictionAction.CLEAR
            and _end_date(catalog, snapshot) < cur_date
        ):
            decision = EvictionDecision(
                snapshot.slot, EvictionAction.CLEAR, reason=EvictionReason.PAST_END
            )
        elif (
            decision.action is not EvictionAction.CLEAR
            and last_end is not None
            and _start_date(catalog, snapshot) > last_end
        ):
            decision = EvictionDecision(
                snapshot.slot,
                EvictionAction.CLEAR,
                reason=EvictionReason.BEYOND_BOUNDARY,
            )
        decisions.append(decision)
    return decisions


def _catalog_from(snapshots: MatchCatalog | Sequence[OverrideSnapshot]) -> MatchCatalog:
    """Return a matcher catalog for cleanup decisions."""
    if isinstance(snapshots, MatchCatalog):
        return snapshots
    return MatchCatalog(list(snapshots), TrimConfig(False, 0, "", 0))


def _last_calendar_end(calendar: Sequence[Any], max_events: int) -> date | None:
    """Return the terminal event end date within the managed sensor boundary."""
    if not calendar:
        return None
    index = min(len(calendar), max_events) - 1
    return cast(date, calendar[index].end.date())


def _match_decision(
    catalog: MatchCatalog,
    snapshot: OverrideSnapshot,
    event_ids: Sequence[Any],
    slot_miss_counts: dict[int, int],
    cur_date: date,
) -> EvictionDecision:
    """Return the base match or miss-count decision for one slot."""
    if _slot_has_matching_event(catalog, snapshot.slot, event_ids):
        action = (
            EvictionAction.RESET_MISS
            if snapshot.slot in slot_miss_counts
            else EvictionAction.PRESERVE
        )
        return EvictionDecision(snapshot.slot, action, reason=EvictionReason.MATCHED)
    if _start_date(catalog, snapshot) < cur_date:
        return EvictionDecision(
            snapshot.slot, EvictionAction.CLEAR, reason=EvictionReason.MISSING_EVENT
        )
    new_count = slot_miss_counts.get(snapshot.slot, 0) + 1
    if new_count >= _SLOT_MISS_THRESHOLD:
        return EvictionDecision(
            snapshot.slot, EvictionAction.CLEAR, new_count, EvictionReason.THRESHOLD
        )
    return EvictionDecision(
        snapshot.slot,
        EvictionAction.INCREMENT_MISS,
        new_count,
        EvictionReason.MISSING_EVENT,
    )


def _slot_has_matching_event(
    catalog: MatchCatalog,
    slot: int,
    event_ids: Sequence[Any],
) -> bool:
    """Return whether ``slot`` matches any event in mirror orientation."""
    for event in event_ids:
        result = match_target_slot(
            catalog,
            slot,
            MatchRequest(
                slot_name=event.name,
                start_time=event.start,
                end_time=event.end,
                uid=event.uid,
                target_slot=slot,
            ),
        )
        if result.slot == slot:
            return True
    return False


def _start_date(catalog: MatchCatalog, snapshot: OverrideSnapshot) -> date:
    """Return the stored local start date for cleanup decisions."""
    if catalog.slot_dates and snapshot.slot in catalog.slot_dates:
        return catalog.slot_dates[snapshot.slot][0]
    return snapshot.start_time.date()


def _end_date(catalog: MatchCatalog, snapshot: OverrideSnapshot) -> date:
    """Return the stored local end date for cleanup decisions."""
    if catalog.slot_dates and snapshot.slot in catalog.slot_dates:
        return catalog.slot_dates[snapshot.slot][1]
    return snapshot.end_time.date()
