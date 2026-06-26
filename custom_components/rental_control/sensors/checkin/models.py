# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Typed models for check-in sensor decomposition."""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from typing import Literal

from homeassistant.components.calendar import CalendarEvent
from homeassistant.core import CALLBACK_TYPE

from ...const import CHECKIN_STATE_AWAITING
from ...const import CHECKIN_STATE_CHECKED_IN
from ...const import CHECKIN_STATE_CHECKED_OUT
from ...const import CHECKIN_STATE_NO_RESERVATION

CheckinState = Literal[
    "no_reservation",
    "awaiting_checkin",
    "checked_in",
    "checked_out",
]
TimerPurpose = Literal[
    "auto_checkin",
    "auto_checkout",
    "linger_to_awaiting",
    "linger_to_no_reservation",
    "no_reservation_to_awaiting",
]


@dataclass(slots=True)
class CheckinStateSnapshot:
    """Snapshot of check-in sensor state and persisted fields."""

    state: str = CHECKIN_STATE_NO_RESERVATION
    tracked_event_summary: str | None = None
    tracked_event_start: datetime | None = None
    tracked_event_end: datetime | None = None
    tracked_event_slot_name: str | None = None
    checkin_source: str | None = None
    checkout_source: str | None = None
    checkout_time: datetime | None = None
    transition_target_time: datetime | None = None
    checked_out_event_key: str | None = None
    next_event_start_day: datetime | None = None
    checkin_lock_name: str | None = None
    linger_followon_key: str | None = None
    linger_baseline: datetime | None = None
    event_missing_warned: bool = False


@dataclass(frozen=True, slots=True)
class CoordinatorUpdateContext:
    """Inputs for pure coordinator-update decisions."""

    snapshot: CheckinStateSnapshot
    events: Sequence[CalendarEvent]
    clock: Callable[[], datetime]
    last_update_success: bool
    monitoring_enabled: bool
    cleaning_window_hours: float
    event_prefix: str
    active_timer: bool
    coordinator_name: str


@dataclass(frozen=True, slots=True)
class DecisionEffect:
    """Ordered effect for the entity shell to apply."""

    kind: str
    event: CalendarEvent | None = None
    source: str | None = None
    lock_name: str = ""
    linger_baseline: datetime | None = None
    end_time: datetime | None = None
    target_time: datetime | None = None
    value: Any = None


@dataclass(frozen=True, slots=True)
class LogIntent:
    """Log record selected by pure decision logic."""

    level: str
    message: str
    args: tuple[Any, ...] = ()


@dataclass(frozen=True, slots=True)
class TransitionDecision:
    """Decision from coordinator-update processing."""

    effects: tuple[DecisionEffect, ...] = ()
    write_state: bool = False
    log_records: tuple[LogIntent, ...] = ()


@dataclass(frozen=True, slots=True)
class RestoreReconciliationDecision:
    """Decision from restored-state reconciliation."""

    effects: tuple[DecisionEffect, ...] = ()
    write_state: bool = False
    reason: str = ""
    log_records: tuple[LogIntent, ...] = ()


@dataclass(slots=True)
class ScheduledTransition:
    """Runtime metadata for a scheduled check-in transition."""

    purpose: TimerPurpose
    target_time: datetime
    followon_start_day: datetime | None = None
    cancel_handle: CALLBACK_TYPE | None = None


VALID_CHECKIN_STATES = {
    CHECKIN_STATE_NO_RESERVATION,
    CHECKIN_STATE_AWAITING,
    CHECKIN_STATE_CHECKED_IN,
    CHECKIN_STATE_CHECKED_OUT,
}
