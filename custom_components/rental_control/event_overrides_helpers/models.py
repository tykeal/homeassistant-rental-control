# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Shared internal models for EventOverrides helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from datetime import datetime
from enum import Enum


class MatchPhase(str, Enum):
    """Internal matcher phase identifiers."""

    UID_EXACT_NAME = "uid_exact_name"
    EXACT_NAME_STRICT_OVERLAP = "exact_name_strict_overlap"
    TRIM_UID = "trim_uid"
    TRIM_STRICT_OVERLAP = "trim_strict_overlap"


@dataclass(frozen=True, slots=True)
class OverrideSnapshot:
    """Read-only override state used by pure helpers."""

    slot: int
    slot_name: str
    slot_code_present: bool
    start_time: datetime
    end_time: datetime
    uid: str | None


@dataclass(frozen=True, slots=True)
class TrimConfig:
    """Trim and prefix configuration for name matching."""

    trim_names: bool
    max_name_length: int
    event_prefix: str
    prefix_length: int

    @property
    def guest_max(self) -> int:
        """Return the usable guest-name length after prefix expansion."""
        return self.max_name_length - self.prefix_length


@dataclass(frozen=True, slots=True)
class MatchCatalog:
    """Ordered override snapshots and shared trim configuration."""

    snapshots: list[OverrideSnapshot]
    trim_config: TrimConfig
    exclude_slot: int | None = None
    slot_dates: dict[int, tuple[date, date]] | None = None


@dataclass(frozen=True, slots=True)
class MatchRequest:
    """Incoming event identity used by the shared matcher."""

    slot_name: str
    start_time: datetime
    end_time: datetime
    uid: str | None
    exclude_slot: int | None = None
    target_slot: int | None = None


@dataclass(frozen=True, slots=True)
class MatchResult:
    """Shared matcher output for both mirror wrappers."""

    slot: int | None
    phase: MatchPhase | None
    restored_slot_name: str | None = None


@dataclass(frozen=True, slots=True)
class SlotReservationRequest:
    """Normalized retired-greedy reservation request."""

    slot_name: str
    slot_code: str
    start_time: datetime
    end_time: datetime
    uid: str | None = None
    prefix: str | None = None


@dataclass(frozen=True, slots=True)
class SlotUpdateRequest:
    """Normalized override update request."""

    slot: int
    slot_code: str
    slot_name: str
    start_time: datetime
    end_time: datetime
    prefix: str | None = None


class EvictionAction(str, Enum):
    """State mutation requested by greedy cleanup."""

    RESET_MISS = "reset_miss"
    INCREMENT_MISS = "increment_miss"
    CLEAR = "clear"
    PRESERVE = "preserve"


class EvictionReason(str, Enum):
    """Why greedy cleanup chose an eviction action."""

    MISSING_EVENT = "missing_event"
    THRESHOLD = "threshold"
    EMPTY_CALENDAR = "empty_calendar"
    MALFORMED_WINDOW = "malformed_window"
    PAST_END = "past_end"
    BEYOND_BOUNDARY = "beyond_boundary"
    MATCHED = "matched"


@dataclass(frozen=True, slots=True)
class EvictionDecision:
    """Pure decision returned by retired greedy cleanup."""

    slot: int
    action: EvictionAction
    new_miss_count: int | None = None
    reason: EvictionReason = EvictionReason.MATCHED
