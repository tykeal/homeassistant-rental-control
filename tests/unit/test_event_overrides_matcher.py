# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>

"""Focused matcher and cleanup tests for EventOverrides decomposition."""

from __future__ import annotations

from datetime import datetime
import importlib
import sys
from types import SimpleNamespace
from typing import Any
from typing import cast
from zoneinfo import ZoneInfo

from homeassistant.util import dt as dt_util

from custom_components.rental_control.event_overrides import EventOverrides
from custom_components.rental_control.event_overrides_helpers.greedy_cleanup import (
    compute_eviction_decisions,
)
from custom_components.rental_control.event_overrides_helpers.matcher import (
    build_match_catalog,
)
from custom_components.rental_control.event_overrides_helpers.matcher import (
    find_exact_name_strict_overlap,
)
from custom_components.rental_control.event_overrides_helpers.matcher import (
    find_trim_aware_fallback,
)
from custom_components.rental_control.event_overrides_helpers.matcher import (
    find_uid_positive_exact_name,
)
from custom_components.rental_control.event_overrides_helpers.matcher import match_slot
from custom_components.rental_control.event_overrides_helpers.models import (
    EvictionAction,
)
from custom_components.rental_control.event_overrides_helpers.models import (
    EvictionDecision,
)
from custom_components.rental_control.event_overrides_helpers.models import (
    EvictionReason,
)
from custom_components.rental_control.event_overrides_helpers.models import MatchCatalog
from custom_components.rental_control.event_overrides_helpers.models import MatchPhase
from custom_components.rental_control.event_overrides_helpers.models import MatchRequest
from custom_components.rental_control.event_overrides_helpers.models import MatchResult
from custom_components.rental_control.event_overrides_helpers.models import (
    OverrideSnapshot,
)
from custom_components.rental_control.event_overrides_helpers.models import (
    SlotReservationRequest,
)
from custom_components.rental_control.event_overrides_helpers.models import (
    SlotUpdateRequest,
)
from custom_components.rental_control.event_overrides_helpers.models import TrimConfig
from custom_components.rental_control.event_overrides_helpers.slot_bookkeeping import (
    compute_next_slot,
)
from custom_components.rental_control.event_overrides_helpers.slot_bookkeeping import (
    get_slots_with_values,
)
from custom_components.rental_control.event_overrides_helpers.slot_bookkeeping import (
    get_slots_without_values,
)
from custom_components.rental_control.event_overrides_helpers.trim import (
    is_trimmed_match,
)
from custom_components.rental_control.event_overrides_helpers.trim import (
    make_trim_config,
)
from custom_components.rental_control.event_overrides_helpers.trim import strip_prefix
from custom_components.rental_control.util import EventIdentity
from custom_components.rental_control.util import trim_name


def _dt(day: int, hour: int = 14) -> datetime:
    """Return a UTC-aware datetime for January 2026."""
    return datetime(2026, 1, day, hour, tzinfo=dt_util.UTC)


def _ready_eo(max_slots: int = 3) -> EventOverrides:
    """Return a ready EventOverrides with empty managed slots."""
    eo = EventOverrides(start_slot=1, max_slots=max_slots)
    now = _dt(1)
    for slot in range(1, max_slots + 1):
        eo.update(slot, "", "", now, now)
    return eo


def _catalog_from_eo(
    eo: EventOverrides, exclude_slot: int | None = None
) -> MatchCatalog:
    """Serialize current EventOverrides state into a MatchCatalog."""
    return build_match_catalog(
        cast(dict[int, dict[str, Any] | None], eo.overrides),
        eo._slot_uids,
        make_trim_config(
            eo.trim_names,
            eo.max_name_length,
            eo.event_prefix,
            eo.prefix_length,
        ),
        exclude_slot,
    )


class TestHelperModels:
    """T012/T014: foundational helper model tests."""

    def test_model_construction(self) -> None:
        """All foundational helper models construct with expected values."""
        start = _dt(2)
        end = _dt(5)
        snapshot = OverrideSnapshot(1, "Guest", True, start, end, "uid-1")
        trim = TrimConfig(True, 12, "Rental", 7)
        catalog = MatchCatalog([snapshot], trim, exclude_slot=3)
        request = MatchRequest(
            "Guest", start, end, "uid-1", exclude_slot=2, target_slot=1
        )
        result = MatchResult(1, MatchPhase.UID_EXACT_NAME, "Guest")
        update = SlotUpdateRequest(1, "1234", "Guest", start, end, "Rental")
        reserve = SlotReservationRequest("Guest", "1234", start, end, "uid-1", "Rental")
        decision = EvictionDecision(
            1, EvictionAction.CLEAR, 2, EvictionReason.THRESHOLD
        )

        assert trim.guest_max == 5
        assert catalog.exclude_slot == 3
        assert request.target_slot == 1
        assert result.phase is MatchPhase.UID_EXACT_NAME
        assert update.prefix == "Rental"
        assert reserve.uid == "uid-1"
        assert decision.reason is EvictionReason.THRESHOLD

    def test_models_import_without_forbidden_runtime_modules(self) -> None:
        """Importing models adds no HA/coordinator/store/keymaster modules."""
        module_name = "custom_components.rental_control.event_overrides_helpers.models"
        forbidden = (
            "homeassistant",
            "custom_components.rental_control.coordinator",
            "custom_components.rental_control.coordinator_helpers",
            "custom_components.rental_control.store",
            "keymaster",
        )
        saved = {
            name: module
            for name, module in sys.modules.items()
            if any(
                name == prefix or name.startswith(f"{prefix}.") for prefix in forbidden
            )
        }
        sys.modules.pop(module_name, None)
        for name in saved:
            sys.modules.pop(name, None)
        try:
            before = set(sys.modules)
            importlib.import_module(module_name)
            loaded = set(sys.modules) - before

            for prefix in forbidden:
                assert not any(
                    name == prefix or name.startswith(f"{prefix}.") for name in loaded
                )
        finally:
            for name in list(sys.modules):
                if any(
                    name == prefix or name.startswith(f"{prefix}.")
                    for prefix in forbidden
                ):
                    sys.modules.pop(name, None)
            sys.modules.update(saved)


class TestSharedMatcher:
    """T022-T032/T058: focused shared matcher tests."""

    def test_uid_positive_exact_name_ignores_overlap(self) -> None:
        """UID+name wins even when the time window no longer overlaps."""
        eo = _ready_eo()
        eo.update(1, "1111", "Guest", _dt(1), _dt(5))
        eo._slot_uids[1] = "uid-1"

        request = MatchRequest("Guest", _dt(10), _dt(12), " uid-1 ")
        result = find_uid_positive_exact_name(_catalog_from_eo(eo), request)

        assert result == MatchResult(1, MatchPhase.UID_EXACT_NAME)

    def test_exact_name_overlap_respects_exclude_slot(self) -> None:
        """Strict overlap matches unless the slot is explicitly excluded."""
        eo = _ready_eo()
        eo.update(1, "1111", "Guest", _dt(1), _dt(5))
        request = MatchRequest("Guest", _dt(3), _dt(6), None)

        assert find_exact_name_strict_overlap(
            _catalog_from_eo(eo), request
        ) == MatchResult(
            1,
            MatchPhase.EXACT_NAME_STRICT_OVERLAP,
        )
        assert (
            find_exact_name_strict_overlap(
                _catalog_from_eo(eo, exclude_slot=1),
                request,
            )
            is None
        )

    def test_same_start_uid_bypass_prefers_best_slot(self) -> None:
        """Same-start UID bypass keeps PR #566 preferred-slot tie-breaking."""
        eo = _ready_eo()
        eo.update(1, "1111", "Guest", _dt(1), _dt(4))
        eo._slot_uids[1] = "old-1"
        eo.update(2, "2222", "Guest", _dt(1), _dt(6))
        eo._slot_uids[2] = "old-2"

        event = EventIdentity("Guest", _dt(1), _dt(5), "new-uid")
        assert eo._get_same_start_uid_bypass_slot(event) == 1
        assert (
            eo._find_overlapping_slot(event.name, event.start, event.end, event.uid)
            == 1
        )

    def test_trim_fallback_restores_full_name(self) -> None:
        """Trim-aware fallback returns and applies the longer full name."""
        eo = _ready_eo()
        eo.trim_names = True
        eo.max_name_length = 18
        eo.event_prefix = "Rental"
        full_name = "Alice Bob Charlie"
        stored_name = trim_name(full_name, eo.max_name_length - eo.prefix_length)
        eo.update(1, "1111", stored_name, _dt(1), _dt(5))
        eo._slot_uids[1] = "uid-1"

        result = find_trim_aware_fallback(
            _catalog_from_eo(eo),
            MatchRequest(full_name, _dt(9), _dt(10), "uid-1"),
        )

        assert result == MatchResult(1, MatchPhase.TRIM_UID, full_name)
        assert eo._find_overlapping_slot(full_name, _dt(9), _dt(10), "uid-1") == 1
        override = eo.overrides[1]
        assert override is not None
        assert override["slot_name"] == full_name

    def test_mirror_methods_share_matcher_semantics(self) -> None:
        """The shell mirror methods agree on the shared matcher winner."""
        eo = _ready_eo()
        eo.update(1, "1111", "Guest", _dt(1), _dt(5))
        eo._slot_uids[1] = "uid-1"
        eo.update(2, "2222", "Other", _dt(6), _dt(8))
        eo._slot_uids[2] = "uid-2"
        event = EventIdentity("Guest", _dt(3), _dt(4), "uid-1")
        result = match_slot(
            _catalog_from_eo(eo),
            MatchRequest(event.name, event.start, event.end, event.uid),
        )

        assert result.slot == eo._find_overlapping_slot(
            event.name, event.start, event.end, event.uid
        )
        assert eo._slot_has_matching_event(1, [event]) is True
        assert eo._slot_has_matching_event(2, [event]) is False

    def test_slot_has_matching_event_is_slot_anchored_for_duplicates(self) -> None:
        """Duplicate non-UID slot matches stay true for each matching slot."""
        eo = _ready_eo(max_slots=2)
        eo.update(1, "1111", "Guest", _dt(1), _dt(5))
        eo.update(2, "2222", "Guest", _dt(1), _dt(5))
        event = EventIdentity("Guest", _dt(2), _dt(3), None)

        assert eo._find_overlapping_slot(event.name, event.start, event.end) == 1
        assert eo._slot_has_matching_event(1, [event]) is True
        assert eo._slot_has_matching_event(2, [event]) is True

    def test_trim_and_slot_bookkeeping_helpers(self) -> None:
        """Trim, free-slot ordering, and next-slot helpers stay deterministic."""
        eo = _ready_eo()
        eo.update(1, "1111", "Guest", _dt(1), _dt(2))
        eo.update(3, "3333", "Other", _dt(3), _dt(4))

        assert strip_prefix("Rental Guest", "Rental") == "Guest"
        assert is_trimmed_match("Alice Bob Charlie", "Alice Bob", 10) is True
        assert get_slots_with_values(eo.overrides) == [1, 3]
        assert get_slots_without_values(eo.overrides) == [2]
        assert compute_next_slot(eo.overrides, eo.start_slot, eo.max_slots) == 2

    def test_greedy_cleanup_decisions_cover_match_miss_and_clears(self) -> None:
        """Greedy cleanup returns reset, increment, threshold, and immediate clears."""
        eo = _ready_eo(max_slots=2)
        eo.update(1, "1111", "Guest", _dt(5), _dt(7))
        eo._slot_uids[1] = "uid-1"
        eo.update(2, "2222", "Other", _dt(9), _dt(11))
        catalog = _catalog_from_eo(eo)
        event_ids = [EventIdentity("Guest", _dt(5), _dt(7), "uid-1")]
        calendar = [SimpleNamespace(end=_dt(12))]

        decisions = compute_eviction_decisions(
            catalog, event_ids, calendar, 2, {1: 1}, _dt(4).date()
        )
        assert decisions[0].action is EvictionAction.RESET_MISS
        assert decisions[1].action is EvictionAction.INCREMENT_MISS
        assert decisions[1].new_miss_count == 1

        threshold = compute_eviction_decisions(
            catalog, [], calendar, 2, {2: 1}, _dt(4).date()
        )
        assert threshold[1] == EvictionDecision(
            2, EvictionAction.CLEAR, 2, EvictionReason.THRESHOLD
        )

        empty = compute_eviction_decisions(catalog, event_ids, [], 2, {}, _dt(4).date())
        assert all(
            decision.reason is EvictionReason.EMPTY_CALENDAR for decision in empty
        )

        malformed = compute_eviction_decisions(
            MatchCatalog(
                [
                    OverrideSnapshot(9, "Bad", True, _dt(8), _dt(7), None),
                ],
                TrimConfig(False, 0, "", 0),
            ),
            [],
            calendar,
            2,
            {},
            _dt(4).date(),
        )
        assert malformed[0].reason is EvictionReason.MALFORMED_WINDOW

    def test_greedy_cleanup_uses_stored_local_dates_for_miss_tolerance(self) -> None:
        """Eviction tolerance uses original stored dates, not UTC-shifted dates."""
        tokyo = ZoneInfo("Asia/Tokyo")
        start = datetime(2026, 1, 5, 0, 30, tzinfo=tokyo)
        end = datetime(2026, 1, 6, 10, 0, tzinfo=tokyo)
        start_utc = start.astimezone(dt_util.UTC)
        end_utc = end.astimezone(dt_util.UTC)
        catalog = build_match_catalog(
            {
                1: {
                    "slot_name": "Guest",
                    "slot_code": "1111",
                    "start_time": start_utc,
                    "end_time": end_utc,
                }
            },
            {},
            TrimConfig(False, 0, "", 0),
            slot_dates={1: (start.date(), end.date())},
        )
        calendar = [SimpleNamespace(end=end)]

        decisions = compute_eviction_decisions(
            catalog, [], calendar, 1, {}, start.date()
        )

        assert decisions == [
            EvictionDecision(
                1,
                EvictionAction.INCREMENT_MISS,
                1,
                EvictionReason.MISSING_EVENT,
            )
        ]
