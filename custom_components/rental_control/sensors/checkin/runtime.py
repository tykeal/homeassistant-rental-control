# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Entity-owned runtime helpers for the check-in sensor shell."""

from __future__ import annotations

import asyncio
from datetime import datetime
from importlib import import_module
import logging
from typing import Any

from homeassistant.components.datetime import DOMAIN as DATETIME
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util

from ...const import CHECKIN_STATE_AWAITING
from ...const import CHECKIN_STATE_CHECKED_IN
from ...const import CHECKIN_STATE_CHECKED_OUT
from ...const import CHECKIN_STATE_NO_RESERVATION
from ...const import EARLY_CHECKOUT_EXPIRY_SWITCH
from ...const import EVENT_RENTAL_CONTROL_CHECKIN
from ...const import EVENT_RENTAL_CONTROL_CHECKOUT
from ...util import add_call
from ...util import check_gather_results
from ...util import compute_early_expiry_time
from ...util import get_entry_data

_LOGGER = logging.getLogger("custom_components.rental_control.sensors.checkinsensor")


async def async_checkout(entity: Any, force: bool = False) -> None:
    """Handle manual checkout service call."""
    _validate_checkout_allowed(entity, force)
    now = dt_util.now()
    end = entity._tracked_event_end
    await _maybe_shorten_lock_expiry(entity, now)
    baseline = min(now, end) if end else now
    entity._transition_to_checked_out(source="manual", linger_baseline=baseline)


def _validate_checkout_allowed(entity: Any, force: bool) -> None:
    """Validate manual checkout guards."""
    if entity._state != CHECKIN_STATE_CHECKED_IN:
        raise ServiceValidationError(
            f"Checkout is only available when the guest is checked in "
            f"(current state: {entity._state})"
        )
    end = entity._tracked_event_end
    if (entity._tracked_event_start is None or end is None) and not force:
        raise ServiceValidationError("Checkout requires known reservation boundaries")
    if entity._tracked_event_start is None or end is None:
        _LOGGER.warning(
            "Force checkout: reservation boundaries unknown for %s, proceeding anyway",
            entity.coordinator.name,
        )
        return
    _validate_checkout_day(entity, end, force)


def _validate_checkout_day(entity: Any, end: datetime, force: bool) -> None:
    """Validate checkout date guard."""
    local_now = dt_util.as_local(dt_util.now())
    local_end = dt_util.as_local(end)
    if local_now.date() >= local_end.date():
        return
    if not force:
        raise ServiceValidationError(
            "Checkout is only available on the last day of the reservation "
            f"or later (current: {local_now.date().isoformat()}, "
            f"checkout day: {local_end.date().isoformat()})"
        )
    _LOGGER.warning(
        "Force checkout: overriding last-day guard for %s (current: %s, checkout day: %s)",
        entity.coordinator.name,
        local_now.date().isoformat(),
        local_end.date().isoformat(),
    )


async def _maybe_shorten_lock_expiry(entity: Any, now: datetime) -> None:
    """Shorten lock code expiry when early checkout expiry is enabled."""
    entry_data = get_entry_data(entity._hass, entity._config_entry.entry_id)
    switch = entry_data.get(EARLY_CHECKOUT_EXPIRY_SWITCH) if entry_data else None
    if switch is None or not switch.is_on or entity._tracked_event_end is None:
        return
    new_end = compute_early_expiry_time(now, entity._tracked_event_end)
    if new_end >= entity._tracked_event_end:
        return
    _LOGGER.info(
        "Early checkout expiry: shortening end time from %s to %s for %s",
        entity._tracked_event_end,
        new_end,
        entity._tracked_event_summary,
    )
    entity._tracked_event_end = new_end
    await async_update_lock_code_expiry(entity, new_end)


async def async_set_state(entity: Any, state: str) -> None:
    """Force-set the sensor to an arbitrary valid state."""
    valid = entity._attr_options
    if valid is None or state not in valid:
        raise ServiceValidationError(
            f"Invalid state '{state}'. Valid states: {', '.join(valid or [])}"
        )
    _LOGGER.warning(
        "DEBUG override: forcing %s from '%s' to '%s'",
        entity.entity_id,
        entity._state,
        state,
    )
    entity._state = state
    entity._tracked_event_summary = entity._tracked_event_start = (
        entity._tracked_event_end
    ) = None
    entity._tracked_event_slot_name = entity._checkin_source = (
        entity._checkout_source
    ) = None
    entity._checkout_time = entity._transition_target_time = (
        entity._checked_out_event_key
    ) = None
    entity._next_event_start_day = entity._checkin_lock_name = None
    entity._linger_followon_key = entity._linger_baseline = None
    entity._event_missing_warned = False
    entity._cancel_timer()
    entity.async_write_ha_state()


def handle_keymaster_unlock(
    entity: Any, code_slot_num: int, lock_name: str = ""
) -> None:
    """Handle a keymaster unlock event."""
    if _ignore_keymaster_unlock(entity, code_slot_num):
        return
    if entity._state == CHECKIN_STATE_CHECKED_IN:
        _ignore_checked_in_unlock(entity, code_slot_num)
        return
    if entity._state != CHECKIN_STATE_AWAITING:
        _LOGGER.debug(
            "Ignoring keymaster unlock: sensor state is %s, not awaiting_checkin",
            entity._state,
        )
        return
    if _unlock_slot_mismatch(entity, code_slot_num):
        return
    _LOGGER.info(
        "Keymaster unlock detected for slot %d (lock=%s), transitioning to checked_in for %s",
        code_slot_num,
        lock_name or entity.coordinator.lockname,
        entity._tracked_event_summary,
    )
    entity._transition_to_checked_in(source="keymaster", lock_name=lock_name)


def _ignore_keymaster_unlock(entity: Any, code_slot_num: int) -> bool:
    """Return true when an unlock event is out of scope."""
    if code_slot_num == 0:
        _LOGGER.debug("Ignoring keymaster unlock: code_slot_num == 0 (manual/RF)")
        return True
    start_slot = entity.coordinator.start_slot
    if start_slot <= code_slot_num < start_slot + entity.coordinator.max_events:
        return False
    _LOGGER.debug(
        "Ignoring keymaster unlock: code_slot_num %d outside managed range [%d, %d)",
        code_slot_num,
        start_slot,
        start_slot + entity.coordinator.max_events,
    )
    return True


def _ignore_checked_in_unlock(entity: Any, code_slot_num: int) -> None:
    """Preserve checked-in unlock ignore behavior."""
    tracked_slot = 0
    if entity._tracked_event_slot_name and entity.coordinator.event_overrides:
        tracked_slot = entity.coordinator.event_overrides.get_slot_key_by_name(
            entity._tracked_event_slot_name
        )
    if tracked_slot != code_slot_num:
        _LOGGER.debug(
            "Ignoring keymaster unlock: code_slot_num %d does not match tracked event slot %d",
            code_slot_num,
            tracked_slot,
        )


def _unlock_slot_mismatch(entity: Any, code_slot_num: int) -> bool:
    """Return true when override slot name does not match tracked guest."""
    overrides = entity.coordinator.event_overrides
    if not overrides or not overrides.ready:
        return False
    incoming_slot_name = overrides.get_slot_name(code_slot_num)
    if not incoming_slot_name or not entity._tracked_event_slot_name:
        return False
    if incoming_slot_name == entity._tracked_event_slot_name:
        return False
    _LOGGER.debug(
        "Ignoring keymaster unlock: slot %d belongs to '%s' but tracked event is for '%s'",
        code_slot_num,
        incoming_slot_name,
        entity._tracked_event_slot_name,
    )
    return True


async def async_update_lock_code_expiry(entity: Any, new_end: datetime) -> None:
    """Update keymaster lock code expiry after early checkout."""
    if (
        entity._tracked_event_slot_name is None
        or not entity.coordinator.event_overrides
    ):
        return
    slot = entity.coordinator.event_overrides.get_slot_key_by_name(
        entity._tracked_event_slot_name
    )
    lockname = entity.coordinator.lockname
    if not slot or not lockname:
        return
    module = import_module("custom_components.rental_control.sensors.checkinsensor")
    caller = getattr(module, "add_call", add_call)
    coro = caller(
        entity._hass,
        [],
        DATETIME,
        "set_value",
        f"datetime.{lockname}_code_slot_{slot}_date_range_end",
        {"datetime": new_end.isoformat()},
    )
    results = await asyncio.gather(*coro, return_exceptions=True)
    check_gather_results(results, "Early checkout lock code expiry update", _LOGGER)


def transition_to_no_reservation(entity: Any) -> None:
    """Transition to no_reservation state and clear tracked data."""
    _LOGGER.debug("Transitioning to no_reservation")
    entity._state = CHECKIN_STATE_NO_RESERVATION
    entity._tracked_event_summary = entity._tracked_event_start = None
    entity._tracked_event_end = entity._tracked_event_slot_name = None
    entity._checkin_source = entity._checkout_source = entity._checkout_time = None
    entity._transition_target_time = entity._checked_out_event_key = None
    entity._checkin_lock_name = entity._linger_followon_key = None
    entity._linger_baseline = None
    entity._event_missing_warned = False
    entity._cancel_timer()
    entity.async_write_ha_state()


def apply_silent_checked_in(entity: Any, source: str) -> None:
    """Apply a restore-silent checked-in transition."""
    entity._state = CHECKIN_STATE_CHECKED_IN
    entity._checkin_source = source
    entity._cancel_timer()
    entity._transition_target_time = None


def apply_silent_checked_out(
    entity: Any, source: str, checkout_time: datetime | None
) -> None:
    """Apply a restore-silent checked-out transition."""
    entity._state = CHECKIN_STATE_CHECKED_OUT
    entity._checkout_source = source
    entity._checkout_time = checkout_time or dt_util.now()
    if entity._tracked_event_summary and entity._tracked_event_start:
        entity._checked_out_event_key = entity._event_key(
            entity._tracked_event_summary, entity._tracked_event_start
        )


def transition_to_awaiting(entity: Any, event: Any) -> None:
    """Transition to awaiting_checkin state."""
    _LOGGER.debug("Transitioning to awaiting_checkin for event: %s", event.summary)
    entity._state = CHECKIN_STATE_AWAITING
    entity._tracked_event_summary = event.summary
    entity._tracked_event_start = event.start
    entity._tracked_event_end = event.end
    entity._tracked_event_slot_name = entity._extract_slot_name(event)
    entity._checkin_source = entity._checkout_source = entity._checkout_time = None
    entity._next_event_start_day = entity._checkin_lock_name = None
    entity._linger_followon_key = entity._linger_baseline = None
    entity._event_missing_warned = False
    entity._schedule_auto_checkin(event.start)
    if event.start <= dt_util.now() and not entity._is_keymaster_monitoring_enabled():
        entity._transition_to_checked_in(source="automatic")
        return
    entity.async_write_ha_state()


def checkin_payload(entity: Any, source: str) -> dict[str, Any]:
    """Return a check-in or checkout event payload."""
    return {
        "entity_id": entity.entity_id,
        "summary": entity._tracked_event_summary or "",
        "start": entity._tracked_event_start.isoformat()
        if entity._tracked_event_start
        else "",
        "end": entity._tracked_event_end.isoformat()
        if entity._tracked_event_end
        else "",
        "guest_name": entity._tracked_event_slot_name or "",
        "source": source,
    }


def transition_to_checked_in(entity: Any, source: str, lock_name: str = "") -> None:
    """Transition to checked_in state and fire the check-in event."""
    _LOGGER.debug(
        "Transitioning to checked_in (source=%s, lock=%s) for event: %s",
        source,
        lock_name or "(none)",
        entity._tracked_event_summary,
    )
    entity._state = CHECKIN_STATE_CHECKED_IN
    entity._checkin_source = source
    entity._checkin_lock_name = lock_name or None
    entity._transition_target_time = None
    entity._event_missing_warned = False
    payload = checkin_payload(entity, source)
    if lock_name:
        payload["lock_name"] = lock_name
    entity._hass.bus.async_fire(EVENT_RENTAL_CONTROL_CHECKIN, payload)
    entity._cancel_timer()
    if entity._tracked_event_end is not None:
        entity._schedule_auto_checkout(entity._tracked_event_end)
    entity.async_write_ha_state()


def transition_to_checked_out(
    entity: Any, source: str, linger_baseline: datetime | None = None
) -> None:
    """Transition to checked_out state."""
    _LOGGER.debug(
        "Transitioning to checked_out (source=%s) for event: %s",
        source,
        entity._tracked_event_summary,
    )
    entity._state = CHECKIN_STATE_CHECKED_OUT
    entity._checkout_source = source
    entity._checkout_time = dt_util.now()
    entity._event_missing_warned = False
    if entity._tracked_event_summary and entity._tracked_event_start:
        entity._checked_out_event_key = entity._event_key(
            entity._tracked_event_summary, entity._tracked_event_start
        )
    entity._hass.bus.async_fire(
        EVENT_RENTAL_CONTROL_CHECKOUT, checkin_payload(entity, source)
    )
    entity._cancel_timer()
    entity._linger_baseline = linger_baseline or entity._checkout_time
    entity._compute_linger_timing(entity._linger_baseline)
    entity.async_write_ha_state()
