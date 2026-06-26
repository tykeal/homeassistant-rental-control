# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Timer runtime helpers for the check-in sensor shell."""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta
import logging
from typing import Any

from homeassistant.util import dt as dt_util

from ...const import CHECKIN_STATE_AWAITING
from ...const import CHECKIN_STATE_CHECKED_IN
from ...const import CHECKIN_STATE_CHECKED_OUT
from ...const import CHECKIN_STATE_NO_RESERVATION

_LOGGER = logging.getLogger("custom_components.rental_control.sensors.checkinsensor")


def compute_linger_timing(entity: Any, baseline: datetime | None = None) -> None:
    """Compute and schedule post-checkout linger timing."""
    checkout_time = (
        baseline or entity._linger_baseline or entity._checkout_time or dt_util.now()
    )
    next_event = entity._find_followon_event(checkout_time)
    if next_event is None:
        target = checkout_time + timedelta(hours=entity._get_cleaning_window())
        entity._schedule_linger_to_no_reservation(target, None)
        _LOGGER.debug(
            "FR-006b: No follow-on, transition to no_reservation at %s",
            target.isoformat(),
        )
        return
    entity._linger_followon_key = entity._event_key(
        next_event.summary, next_event.start
    )
    if (
        dt_util.as_local(next_event.start).date()
        == dt_util.as_local(checkout_time).date()
    ):
        target = checkout_time + ((next_event.start - checkout_time) / 2)
        entity._next_event_start_day = None
        entity._schedule_linger_to_awaiting(target)
        _LOGGER.debug(
            "FR-006a: Same-day turnover, transition to awaiting at %s",
            target.isoformat(),
        )
        return
    midnight = dt_util.start_of_local_day(checkout_time + timedelta(days=1))
    entity._next_event_start_day = dt_util.start_of_local_day(next_event.start)
    entity._schedule_linger_to_no_reservation(midnight, entity._next_event_start_day)
    _LOGGER.debug(
        "FR-006c: Different-day follow-on, transition to no_reservation "
        "at %s, follow-up awaiting at %s",
        midnight.isoformat(),
        entity._next_event_start_day.isoformat(),
    )


def auto_checkin_callback(entity: Any, _now: datetime) -> None:
    """Run the automatic check-in callback."""
    _LOGGER.debug("Auto check-in timer fired for %s", entity.coordinator.name)
    entity._timer_manager.clear_callback_handle()
    if entity._state != CHECKIN_STATE_AWAITING:
        return
    if entity._is_keymaster_monitoring_enabled():
        _LOGGER.debug(
            "Keymaster monitoring is on; staying in awaiting_checkin "
            "until door code is used for %s",
            entity.coordinator.name,
        )
        entity._transition_target_time = None
        entity.async_write_ha_state()
        return
    entity._transition_to_checked_in(source="automatic")


def auto_checkout_callback(entity: Any, _now: datetime) -> None:
    """Run the automatic checkout callback."""
    _LOGGER.debug("Auto check-out timer fired for %s", entity.coordinator.name)
    entity._timer_manager.clear_callback_handle()
    if entity._state == CHECKIN_STATE_CHECKED_IN:
        entity._transition_to_checked_out(source="automatic")


def linger_to_awaiting_callback(entity: Any, _now: datetime) -> None:
    """Run the same-day linger callback."""
    _LOGGER.debug("Linger-to-awaiting timer fired for %s", entity.coordinator.name)
    entity._timer_manager.clear_callback_handle()
    if entity._state != CHECKIN_STATE_CHECKED_OUT:
        return
    event = entity._find_followon_event(
        entity._linger_baseline or entity._checkout_time or _now
    )
    if event is not None:
        entity._transition_to_awaiting(event)
    else:
        entity._transition_to_no_reservation()


def linger_to_no_reservation_callback(entity: Any, _now: datetime) -> None:
    """Run the linger expiry callback."""
    _LOGGER.debug(
        "Linger-to-no-reservation timer fired for %s", entity.coordinator.name
    )
    entity._timer_manager.clear_callback_handle()
    if entity._state != CHECKIN_STATE_CHECKED_OUT:
        return
    followon_start_day = entity._next_event_start_day
    entity._transition_to_no_reservation()
    if followon_start_day is None:
        return
    if followon_start_day > _now:
        entity._schedule_no_reservation_to_awaiting(followon_start_day)
        _LOGGER.debug(
            "FR-006c: Scheduled follow-up awaiting transition at %s for %s",
            followon_start_day.isoformat(),
            entity.coordinator.name,
        )
        entity.async_write_ha_state()
    else:
        _LOGGER.debug(
            "FR-006c: Follow-on start day %s already passed, relying on coordinator update",
            followon_start_day.isoformat(),
        )


def no_reservation_to_awaiting_callback(entity: Any, _now: datetime) -> None:
    """Run the FR-006c follow-up awaiting callback."""
    _LOGGER.debug(
        "FR-006c no-reservation-to-awaiting timer fired for %s", entity.coordinator.name
    )
    entity._timer_manager.clear_callback_handle()
    entity._next_event_start_day = None
    entity._transition_target_time = None
    if entity._state != CHECKIN_STATE_NO_RESERVATION:
        return
    event = entity._get_relevant_event()
    if event is not None:
        entity._transition_to_awaiting(event)
    else:
        _LOGGER.debug(
            "FR-006c: No event found in coordinator data at follow-up time, "
            "staying in no_reservation"
        )


def schedule_auto_checkin(entity: Any, start_time: datetime) -> None:
    """Schedule automatic check-in for a start time."""
    entity._cancel_timer()
    if start_time > dt_util.now():
        entity._transition_target_time = start_time
        entity._timer_manager.schedule(
            "auto_checkin", entity._async_auto_checkin_callback, start_time
        )
    else:
        entity._transition_target_time = None


def schedule_auto_checkout(entity: Any, end_time: datetime) -> None:
    """Schedule automatic checkout at the given end time."""
    if end_time > dt_util.now():
        entity._transition_target_time = end_time
        entity._timer_manager.schedule(
            "auto_checkout", entity._async_auto_checkout_callback, end_time
        )
    else:
        entity._transition_target_time = None
        entity._transition_to_checked_out(source="automatic", linger_baseline=end_time)


def schedule_linger_to_awaiting(entity: Any, target: datetime) -> None:
    """Schedule same-day linger transition."""
    entity._transition_target_time = target
    entity._timer_manager.schedule(
        "linger_to_awaiting", entity._async_linger_to_awaiting_callback, target
    )


def schedule_linger_to_no_reservation(
    entity: Any, target: datetime, followup: datetime | None
) -> None:
    """Schedule linger expiry transition."""
    entity._transition_target_time = target
    if followup is None:
        entity._next_event_start_day = None
        entity._linger_followon_key = None
    entity._timer_manager.schedule(
        "linger_to_no_reservation",
        entity._async_linger_to_no_reservation_callback,
        target,
        followup,
    )


def schedule_no_reservation_to_awaiting(entity: Any, target: datetime) -> None:
    """Schedule FR-006c follow-up awaiting transition."""
    entity._transition_target_time = target
    entity._next_event_start_day = target
    entity._timer_manager.schedule(
        "no_reservation_to_awaiting",
        entity._async_no_reservation_to_awaiting_callback,
        target,
    )
