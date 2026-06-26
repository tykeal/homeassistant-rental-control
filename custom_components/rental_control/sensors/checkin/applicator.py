# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Apply ordered check-in decisions through the entity boundary."""

from __future__ import annotations

import logging
from typing import Any

from .models import DecisionEffect
from .models import LogIntent
from .models import RestoreReconciliationDecision
from .models import TransitionDecision

_LOGGER = logging.getLogger("custom_components.rental_control.sensors.checkinsensor")


def apply_transition_decision(entity: Any, decision: TransitionDecision) -> None:
    """Apply coordinator-update effects to the check-in entity."""
    _emit_logs(decision.log_records)
    for effect in decision.effects:
        _apply_effect(entity, effect, silent=False)
    if decision.write_state:
        entity.async_write_ha_state()


def apply_restore_decision(
    entity: Any, decision: RestoreReconciliationDecision
) -> None:
    """Apply restore reconciliation effects to the check-in entity."""
    _emit_logs(decision.log_records)
    for effect in decision.effects:
        _apply_effect(entity, effect, silent=True)
    if decision.write_state:
        entity.async_write_ha_state()


def _emit_logs(logs: tuple[LogIntent, ...]) -> None:
    """Emit selected log records."""
    for record in logs:
        log = getattr(_LOGGER, record.level)
        log(record.message, *record.args)


def _apply_effect(entity: Any, effect: DecisionEffect, *, silent: bool) -> None:
    """Apply one effect by calling entity-owned mutation methods."""
    match effect.kind:
        case "transition_awaiting":
            entity._transition_to_awaiting(effect.event)
        case "transition_checked_in":
            entity._transition_to_checked_in(
                effect.source or "automatic", effect.lock_name
            )
        case "transition_checked_out":
            entity._transition_to_checked_out(
                effect.source or "automatic", effect.linger_baseline
            )
        case "transition_no_reservation":
            entity._transition_to_no_reservation()
        case "silent_checked_in":
            entity._apply_silent_checked_in(effect.source or "automatic")
        case "silent_checked_out":
            entity._apply_silent_checked_out(
                effect.source or "automatic", effect.linger_baseline
            )
        case _:
            _apply_non_transition_effect(entity, effect, silent=silent)


def _apply_non_transition_effect(
    entity: Any, effect: DecisionEffect, *, silent: bool
) -> None:
    """Apply a non-transition effect."""
    match effect.kind:
        case "update_tracked_event":
            entity._update_tracked_event(effect.event)
        case "update_slot_name":
            entity._update_tracked_slot_name(effect.event)
        case "cancel_timer":
            entity._cancel_timer()
        case "schedule_auto_checkout":
            entity._schedule_auto_checkout(effect.end_time)
        case "schedule_auto_checkin":
            entity._schedule_auto_checkin(effect.target_time or effect.end_time)
        case "schedule_no_reservation_to_awaiting":
            entity._schedule_no_reservation_to_awaiting(
                effect.target_time or effect.end_time
            )
        case "compute_linger":
            entity._compute_linger_timing()
        case "set_event_missing":
            entity._event_missing_warned = bool(effect.value)
        case "set_linger_followon_key":
            entity._linger_followon_key = effect.value
        case "set_transition_target":
            entity._transition_target_time = effect.value
        case "set_next_event_start_day":
            entity._next_event_start_day = effect.value
        case _:
            raise ValueError(f"Unknown check-in decision effect: {effect.kind}")
