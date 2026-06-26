# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Pure coordinator-update decisions for the check-in sensor."""

from __future__ import annotations

from datetime import timedelta

from ...const import CHECKIN_STATE_AWAITING
from ...const import CHECKIN_STATE_CHECKED_IN
from ...const import CHECKIN_STATE_CHECKED_OUT
from ...const import CHECKIN_STATE_NO_RESERVATION
from .event_selection import event_key
from .event_selection import find_followon_event
from .event_selection import find_tracked_event
from .event_selection import get_relevant_event
from .models import CoordinatorUpdateContext
from .models import DecisionEffect
from .models import LogIntent
from .models import TransitionDecision

_SELF_HEAL_FUTURE_THRESHOLD = timedelta(hours=24)


def _decision(
    *effects: DecisionEffect,
    write: bool = False,
    logs: tuple[LogIntent, ...] = (),
) -> TransitionDecision:
    """Build a transition decision."""
    return TransitionDecision(effects=effects, write_state=write, log_records=logs)


def decide_coordinator_update(ctx: CoordinatorUpdateContext) -> TransitionDecision:
    """Choose effects for one coordinator update."""
    if not ctx.last_update_success:
        return _decision(write=True)
    state = ctx.snapshot.state
    if state == CHECKIN_STATE_NO_RESERVATION:
        return decide_no_reservation_update(ctx)
    if state == CHECKIN_STATE_AWAITING:
        return decide_awaiting_update(ctx)
    if state == CHECKIN_STATE_CHECKED_IN:
        return decide_checked_in_update(ctx)
    if state == CHECKIN_STATE_CHECKED_OUT:
        return decide_checked_out_update(ctx)
    return _decision(write=True)


def decide_no_reservation_update(ctx: CoordinatorUpdateContext) -> TransitionDecision:
    """Decide updates while no reservation is tracked."""
    event = get_relevant_event(ctx.events, ctx.clock())
    if event is None:
        return _decision(write=True)
    return _decision(DecisionEffect("transition_awaiting", event=event))


def decide_awaiting_update(ctx: CoordinatorUpdateContext) -> TransitionDecision:
    """Decide updates while awaiting check-in."""
    snap = ctx.snapshot
    tracked = find_tracked_event(
        ctx.events, snap.tracked_event_summary, snap.tracked_event_start
    )
    if tracked is None:
        event = get_relevant_event(ctx.events, ctx.clock())
        if event is not None:
            return _decision(DecisionEffect("transition_awaiting", event=event))
        return _decision(DecisionEffect("transition_no_reservation"))
    replacement = _awaiting_replacement(ctx)
    if replacement is not None:
        return replacement
    effects = [DecisionEffect("update_tracked_event", event=tracked)]
    if snap.tracked_event_start is not None and snap.tracked_event_start <= ctx.clock():
        if not ctx.monitoring_enabled:
            effects.append(DecisionEffect("transition_checked_in", source="automatic"))
            return _decision(*effects)
    return _decision(*effects, write=True)


def _awaiting_replacement(ctx: CoordinatorUpdateContext) -> TransitionDecision | None:
    """Return replacement decision for a more relevant awaiting event."""
    snap = ctx.snapshot
    relevant = get_relevant_event(ctx.events, ctx.clock())
    if relevant is None or snap.tracked_event_start is None:
        return None
    if snap.tracked_event_summary is None:
        return None
    relevant_key = event_key(relevant.summary, relevant.start)
    tracked_key = event_key(snap.tracked_event_summary, snap.tracked_event_start)
    if relevant_key == tracked_key or relevant_key == snap.checked_out_event_key:
        return None
    if relevant.start >= snap.tracked_event_start:
        return None
    log = LogIntent(
        "debug",
        "More relevant event found (%s starting %s) while awaiting %s "
        "starting %s; switching tracked event",
        (
            relevant.summary,
            relevant.start,
            snap.tracked_event_summary,
            snap.tracked_event_start,
        ),
    )
    return _decision(DecisionEffect("transition_awaiting", event=relevant), logs=(log,))


def decide_checked_in_update(ctx: CoordinatorUpdateContext) -> TransitionDecision:
    """Decide updates while checked in."""
    snap = ctx.snapshot
    tracked = find_tracked_event(
        ctx.events, snap.tracked_event_summary, snap.tracked_event_start
    )
    if tracked is None:
        return _decide_checked_in_missing(ctx)
    now = ctx.clock()
    if tracked.start > now + _SELF_HEAL_FUTURE_THRESHOLD:
        return _decide_checked_in_far_future(ctx, tracked, now)
    if tracked.end <= now:
        log = LogIntent(
            "debug",
            "Event end time %s has passed, forcing automatic checkout for %s",
            (tracked.end, ctx.coordinator_name),
        )
        return _decision(
            DecisionEffect("update_tracked_event", event=tracked),
            DecisionEffect("cancel_timer"),
            DecisionEffect(
                "transition_checked_out",
                source="automatic",
                linger_baseline=tracked.end,
            ),
            logs=(log,),
        )
    if tracked.end != snap.tracked_event_end:
        log = LogIntent(
            "debug",
            "Event end time changed from %s to %s, rescheduling auto check-out",
            (snap.tracked_event_end, tracked.end),
        )
        return _decision(
            DecisionEffect("set_event_missing", value=False),
            DecisionEffect("update_tracked_event", event=tracked),
            DecisionEffect("cancel_timer"),
            DecisionEffect("schedule_auto_checkout", end_time=tracked.end),
            write=True,
            logs=(log,),
        )
    return _decision(
        DecisionEffect("set_event_missing", value=False),
        DecisionEffect("update_slot_name", event=tracked),
        write=True,
    )


def _decide_checked_in_far_future(
    ctx: CoordinatorUpdateContext, tracked, now
) -> TransitionDecision:
    """Build self-healing checkout decision for far-future tracked event."""
    effects = [
        DecisionEffect(
            "transition_checked_out", source="automatic", linger_baseline=now
        )
    ]
    log = LogIntent(
        "warning",
        "Self-healing: sensor is checked_in but tracked event '%s' starts "
        "at %s which is more than 24h in the future; forcing checkout for %s",
        (tracked.summary, tracked.start, ctx.coordinator_name),
    )
    event = get_relevant_event(ctx.events, now)
    if event is not None and event_key(event.summary, event.start) != event_key(
        tracked.summary, tracked.start
    ):
        effects.append(DecisionEffect("transition_awaiting", event=event))
    return _decision(*effects, logs=(log,))


def _decide_checked_in_missing(ctx: CoordinatorUpdateContext) -> TransitionDecision:
    """Decide checked-in behavior when tracked event is missing."""
    snap = ctx.snapshot
    if snap.tracked_event_end is not None and snap.tracked_event_end <= ctx.clock():
        log = LogIntent(
            "warning",
            "Tracked event not found in coordinator data while checked_in for %s "
            "and stored end time %s has passed; forcing checkout",
            (ctx.coordinator_name, snap.tracked_event_end),
        )
        return _decision(
            DecisionEffect("cancel_timer"),
            DecisionEffect(
                "transition_checked_out",
                source="automatic",
                linger_baseline=snap.tracked_event_end,
            ),
            logs=(log,),
        )
    if snap.tracked_event_end is None and snap.tracked_event_start is None:
        if snap.tracked_event_summary is None:
            log = LogIntent(
                "warning",
                "All tracking data lost while checked_in for %s; resetting to no_reservation",
                (ctx.coordinator_name,),
            )
            return _decision(
                DecisionEffect("cancel_timer"),
                DecisionEffect("transition_no_reservation"),
                logs=(log,),
            )
    return _missing_preserve_decision(ctx)


def _missing_preserve_decision(ctx: CoordinatorUpdateContext) -> TransitionDecision:
    """Return warning/debug decision for transient missing event."""
    if not ctx.snapshot.event_missing_warned:
        log = LogIntent(
            "warning",
            "Tracked event not found in coordinator data while checked_in for %s; preserving state",
            (ctx.coordinator_name,),
        )
        return _decision(
            DecisionEffect("set_event_missing", value=True), write=True, logs=(log,)
        )
    log = LogIntent(
        "debug",
        "Tracked event still missing for %s; preserving checked_in state",
        (ctx.coordinator_name,),
    )
    return _decision(write=True, logs=(log,))


def decide_checked_out_update(ctx: CoordinatorUpdateContext) -> TransitionDecision:
    """Decide updates while checked out."""
    snap = ctx.snapshot
    checkout_time = snap.linger_baseline or snap.checkout_time or ctx.clock()
    followon = find_followon_event(
        ctx.events, checkout_time, snap.checked_out_event_key
    )
    if followon is not None:
        followon_key = event_key(followon.summary, followon.start)
        if ctx.active_timer and snap.linger_followon_key == followon_key:
            return _decision(write=True)
        return _decision(
            DecisionEffect("cancel_timer"),
            DecisionEffect("compute_linger"),
            write=True,
        )
    if snap.linger_followon_key is not None:
        return _decision(
            DecisionEffect("cancel_timer"),
            DecisionEffect("set_linger_followon_key", value=None),
            DecisionEffect("compute_linger"),
            write=True,
        )
    if not ctx.active_timer:
        return _decision(DecisionEffect("compute_linger"), write=True)
    return _decision(write=True)
