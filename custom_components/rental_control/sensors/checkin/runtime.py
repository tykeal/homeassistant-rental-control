# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Entity-owned runtime helpers for the check-in sensor shell."""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Any

from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util

from ...const import CHECKIN_STATE_AWAITING
from ...const import CHECKIN_STATE_CHECKED_IN
from ...const import CHECKIN_STATE_CHECKED_OUT
from ...const import CHECKIN_STATE_NO_RESERVATION
from ...const import EVENT_RENTAL_CONTROL_CHECKIN
from ...const import EVENT_RENTAL_CONTROL_CHECKOUT
from ...util import compute_early_expiry_time as compute_early_expiry_time
from .checkout_expiry import (
    _legacy_reservation_is_current as _legacy_reservation_is_current,
)
from .checkout_expiry import _maybe_shorten_lock_expiry as _maybe_shorten_lock_expiry
from .checkout_expiry import async_set_state as async_set_state
from .checkout_expiry import (
    async_update_lock_code_expiry as async_update_lock_code_expiry,
)
from .checkout_expiry import handle_keymaster_unlock as handle_keymaster_unlock
from .checkout_expiry_retry import (
    _async_run_early_expiry_retry as _async_run_early_expiry_retry,
)
from .checkout_expiry_retry import (
    _schedule_early_expiry_retry as _schedule_early_expiry_retry,
)
from .checkout_expiry_retry import (
    cancel_early_expiry_retry as cancel_early_expiry_retry,
)
from .checkout_expiry_retry import (
    early_expiry_snapshot_fields as early_expiry_snapshot_fields,
)
from .checkout_expiry_retry import (
    initialize_early_expiry_retry as initialize_early_expiry_retry,
)
from .checkout_expiry_retry import (
    resume_early_expiry_retry as resume_early_expiry_retry,
)

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
            "Force checkout: reservation boundaries unknown, proceeding anyway"
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
    _LOGGER.warning("Force checkout: overriding the reservation last-day guard")


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
    _LOGGER.debug("Transitioning to awaiting_checkin")
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
        "Transitioning to checked_in (source=%s)",
        source,
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
        "Transitioning to checked_out (source=%s)",
        source,
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
