# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Pure restored-state reconciliation decisions for the check-in sensor."""

from __future__ import annotations

from datetime import timedelta

from ...const import CHECKIN_STATE_AWAITING
from ...const import CHECKIN_STATE_CHECKED_IN
from ...const import CHECKIN_STATE_CHECKED_OUT
from ...const import CHECKIN_STATE_NO_RESERVATION
from .event_selection import event_key
from .event_selection import get_relevant_event
from .models import CoordinatorUpdateContext
from .models import DecisionEffect
from .models import LogIntent
from .models import RestoreReconciliationDecision

_SELF_HEAL_FUTURE_THRESHOLD = timedelta(hours=24)


def _decision(
    *effects: DecisionEffect,
    write: bool = False,
    reason: str = "",
    logs: tuple[LogIntent, ...] = (),
) -> RestoreReconciliationDecision:
    """Build a restore reconciliation decision."""
    return RestoreReconciliationDecision(
        effects=effects,
        write_state=write,
        reason=reason,
        log_records=logs,
    )


def decide_restore_state(
    ctx: CoordinatorUpdateContext,
) -> RestoreReconciliationDecision:
    """Choose silent effects for restored-state validation."""
    state = ctx.snapshot.state
    if state == CHECKIN_STATE_CHECKED_IN:
        return decide_restore_checked_in(ctx)
    if state == CHECKIN_STATE_AWAITING:
        return decide_restore_awaiting(ctx)
    if state == CHECKIN_STATE_CHECKED_OUT:
        return decide_restore_checked_out(ctx)
    if state == CHECKIN_STATE_NO_RESERVATION:
        return decide_restore_no_reservation(ctx)
    return decide_restore_unknown(ctx)


def decide_restore_checked_in(
    ctx: CoordinatorUpdateContext,
) -> RestoreReconciliationDecision:
    """Reconcile a restored checked-in state."""
    snap = ctx.snapshot
    now = ctx.clock()
    if (
        snap.tracked_event_start
        and snap.tracked_event_start > now + _SELF_HEAL_FUTURE_THRESHOLD
    ):
        log = LogIntent(
            "debug",
            "Stale restore: checked_in but tracked event starts %s (>24h away), "
            "forcing checkout (silent)",
            (snap.tracked_event_start,),
        )
        return _decision(
            DecisionEffect(
                "silent_checked_out", source="automatic", linger_baseline=now
            ),
            DecisionEffect("compute_linger"),
            write=True,
            reason="far_future_checked_in",
            logs=(log,),
        )
    if snap.tracked_event_end and snap.tracked_event_end <= now:
        log = LogIntent(
            "debug",
            "Stale restore: checked_in but event ended, transitioning to checked_out (silent)",
        )
        return _decision(
            DecisionEffect(
                "silent_checked_out",
                source="automatic",
                linger_baseline=snap.tracked_event_end,
            ),
            DecisionEffect("compute_linger"),
            write=True,
            reason="ended_checked_in",
            logs=(log,),
        )
    if snap.tracked_event_end is not None:
        return _decision(
            DecisionEffect("cancel_timer"),
            DecisionEffect("schedule_auto_checkout", end_time=snap.tracked_event_end),
            write=True,
            reason="reschedule_auto_checkout",
        )
    return _decision(
        DecisionEffect("cancel_timer"),
        DecisionEffect("set_transition_target", value=None),
        write=True,
        reason="checked_in_missing_end",
    )


def decide_restore_awaiting(
    ctx: CoordinatorUpdateContext,
) -> RestoreReconciliationDecision:
    """Reconcile a restored awaiting-check-in state."""
    snap = ctx.snapshot
    now = ctx.clock()
    if snap.tracked_event_start and snap.tracked_event_start <= now:
        if not ctx.monitoring_enabled:
            return _restore_awaiting_past_start(ctx, now)
        return _decision(
            DecisionEffect("cancel_timer"),
            DecisionEffect("set_transition_target", value=None),
            write=True,
            reason="awaiting_monitoring_on_past_start",
        )
    if snap.tracked_event_start is not None:
        return _decision(
            DecisionEffect("cancel_timer"),
            DecisionEffect(
                "schedule_auto_checkin", target_time=snap.tracked_event_start
            ),
            write=True,
            reason="reschedule_auto_checkin",
        )
    return _decision(
        DecisionEffect("cancel_timer"),
        DecisionEffect("set_transition_target", value=None),
        write=True,
        reason="awaiting_missing_start",
    )


def _restore_awaiting_past_start(
    ctx: CoordinatorUpdateContext, now
) -> RestoreReconciliationDecision:
    """Build silent check-in effects for a past awaiting start."""
    snap = ctx.snapshot
    effects = [DecisionEffect("silent_checked_in", source="automatic")]
    log = LogIntent(
        "debug",
        "Stale restore: awaiting_checkin but start passed, transitioning to checked_in (silent)",
    )
    if snap.tracked_event_end is not None and snap.tracked_event_end <= now:
        effects.append(
            DecisionEffect(
                "silent_checked_out",
                source="automatic",
                linger_baseline=snap.tracked_event_end,
            )
        )
        effects.append(DecisionEffect("compute_linger"))
    elif snap.tracked_event_end is not None:
        effects.append(
            DecisionEffect("schedule_auto_checkout", end_time=snap.tracked_event_end)
        )
    return _decision(
        DecisionEffect("cancel_timer"),
        DecisionEffect("set_transition_target", value=None),
        *effects,
        write=True,
        reason="awaiting_past_start",
        logs=(log,),
    )


def decide_restore_checked_out(
    ctx: CoordinatorUpdateContext,
) -> RestoreReconciliationDecision:
    """Reconcile a restored checked-out state."""
    event = get_relevant_event(ctx.events, ctx.clock())
    snap = ctx.snapshot
    if (
        event is not None
        and event_key(event.summary, event.start) != snap.checked_out_event_key
    ):
        log = LogIntent(
            "debug",
            "Stale restore: checked_out but new event available, transitioning to awaiting_checkin",
        )
        return _decision(
            DecisionEffect("transition_awaiting", event=event),
            reason="new_event_handoff",
            logs=(log,),
        )
    if _checked_out_linger_expired(ctx):
        log = LogIntent(
            "debug",
            "Stale restore: checked_out and linger expired, transitioning to no_reservation",
        )
        return _decision(
            DecisionEffect("transition_no_reservation"),
            reason="expired_linger",
            logs=(log,),
        )
    return _decision(
        DecisionEffect("compute_linger"), write=True, reason="recompute_linger"
    )


def _checked_out_linger_expired(ctx: CoordinatorUpdateContext) -> bool:
    """Return whether restored checked-out linger has expired."""
    snap = ctx.snapshot
    now = ctx.clock()
    if snap.transition_target_time is not None and snap.transition_target_time <= now:
        return True
    if snap.checkout_time is None:
        return False
    return snap.checkout_time + timedelta(hours=ctx.cleaning_window_hours) <= now


def decide_restore_no_reservation(
    ctx: CoordinatorUpdateContext,
) -> RestoreReconciliationDecision:
    """Reconcile a restored no-reservation state."""
    snap = ctx.snapshot
    if (
        snap.next_event_start_day is not None
        and snap.next_event_start_day > ctx.clock()
    ):
        log = LogIntent(
            "debug",
            "Restored FR-006c follow-up timer at %s for %s",
            (snap.next_event_start_day.isoformat(), ctx.coordinator_name),
        )
        return _decision(
            DecisionEffect("cancel_timer"),
            DecisionEffect(
                "schedule_no_reservation_to_awaiting",
                target_time=snap.next_event_start_day,
            ),
            write=True,
            reason="followup_timer",
            logs=(log,),
        )
    if snap.next_event_start_day is not None:
        return _decision(
            DecisionEffect("set_next_event_start_day", value=None),
            DecisionEffect("set_transition_target", value=None),
            write=True,
            reason="stale_followup_clear",
        )
    return _decision(write=True, reason="no_reservation")


def decide_restore_unknown(
    ctx: CoordinatorUpdateContext,
) -> RestoreReconciliationDecision:
    """Reconcile an unknown restored state."""
    log = LogIntent(
        "warning",
        "Unknown restored state '%s', resetting to no_reservation",
        (ctx.snapshot.state,),
    )
    return _decision(
        DecisionEffect("transition_no_reservation"),
        reason="unknown_state",
        logs=(log,),
    )
