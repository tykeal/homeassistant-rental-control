# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Early checkout lock-expiry dispatch and reservation guards."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from importlib import import_module
import logging
from typing import Any

from homeassistant.exceptions import ServiceValidationError

from ...const import CHECKIN_STATE_AWAITING
from ...const import CHECKIN_STATE_CHECKED_IN
from ...const import EARLY_CHECKOUT_EXPIRY_SWITCH
from ...keymaster_runtime import coordinator_keymaster_runtime_enabled
from ...keymaster_topology import association_requires_verified_routing
from ...util import compute_early_expiry_time
from ...util import get_entry_data
from .checkout_expiry_guards import (
    _legacy_reservation_is_current as _legacy_reservation_is_current,
)
from .checkout_expiry_guards import _reservation_fingerprint as _reservation_fingerprint
from .checkout_expiry_guards import (
    _retry_reservation_is_current as _retry_reservation_is_current,
)
from .checkout_expiry_guards import _retry_slot as _retry_slot
from .checkout_expiry_guards import (
    _verified_retry_identity_end as _verified_retry_identity_end,
)
from .verified_date import async_update_verified_lock_code_expiry

_LOGGER = logging.getLogger("custom_components.rental_control.sensors.checkinsensor")


def _record_checkout_expiry_identity(
    entity: Any,
    original_end: datetime,
    new_end: datetime,
    start: datetime | None,
    slot_name: str | None,
    slot: int | None,
) -> None:
    """Keep reservation identity separate from later sensor display tracking."""
    entity._early_expiry_retry_generation = (
        getattr(entity, "_early_expiry_retry_generation", 0) + 1
    )
    entity._early_expiry_original_end = original_end
    entity._early_expiry_new_end = new_end
    entity._early_expiry_retry_attempt = 0
    entity._early_expiry_retry_slot = slot
    entity._early_expiry_reservation_start = start
    entity._early_expiry_reservation_slot_name = slot_name
    entity._early_expiry_retry_was_dispatched = False
    entity._early_expiry_retry_initial_pending = True


def _schedule_legacy_pending_retry(entity: Any) -> None:
    """Keep the request pending without dispatching through legacy routing."""
    entity._early_expiry_retry_initial_pending = False
    retry = import_module(
        "custom_components.rental_control.sensors.checkin.checkout_expiry_retry"
    )
    if _retry_reservation_is_current(entity):
        retry._publish_early_expiry_notice(entity)
        retry._schedule_early_expiry_retry(entity)
    else:
        retry._clear_early_expiry_retry(entity)


def _initial_request_is_current(
    entity: Any,
    generation: int,
    original_end: datetime,
    new_end: datetime,
    start: datetime | None,
    slot_name: str | None,
) -> bool:
    """Reject an initial write after its checkout identity was invalidated."""
    return (
        entity._early_expiry_retry_generation == generation
        and entity._early_expiry_original_end == original_end
        and entity._early_expiry_new_end == new_end
        and entity._early_expiry_reservation_start == start
        and entity._early_expiry_reservation_slot_name == slot_name
        and entity._early_expiry_retry_initial_pending
        and not getattr(entity, "_early_expiry_retry_entity_removed", False)
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
    _LOGGER.info("Early checkout expiry: shortening the active lock-code window")
    previous_end = entity._tracked_event_end
    tracked_start = entity._tracked_event_start
    tracked_name = entity._tracked_event_slot_name
    retry_slot = _retry_slot(entity, previous_end, tracked_start, tracked_name)
    if retry_slot is None:
        _LOGGER.warning(
            "Early checkout expiry update could not identify a retry slot for entry %s",
            entity._config_entry.entry_id,
        )
    entity._early_expiry_retry_clear_when_done = False
    _record_checkout_expiry_identity(
        entity, previous_end, new_end, tracked_start, tracked_name, retry_slot
    )
    generation = entity._early_expiry_retry_generation
    if not association_requires_verified_routing(entity._config_entry):
        _schedule_legacy_pending_retry(entity)
        return
    entity._tracked_event_end = new_end
    dispatched = False

    def mark_dispatched() -> None:
        """Record that the new expiry may have reached the lock."""
        nonlocal dispatched
        dispatched = True
        entity._early_expiry_retry_was_dispatched = True

    try:
        updated = await async_update_lock_code_expiry(
            entity,
            new_end,
            identity_end=previous_end,
            on_dispatch=mark_dispatched,
            pre_dispatch_guard=lambda: _initial_request_is_current(
                entity,
                generation,
                previous_end,
                new_end,
                tracked_start,
                tracked_name,
            ),
        )
    except Exception:
        _LOGGER.warning(
            "Early checkout expiry update failed for entry %s slot %s",
            entity._config_entry.entry_id,
            retry_slot,
        )
        updated = False
    if updated and (
        entity._early_expiry_retry_generation == generation
        and entity._early_expiry_original_end == previous_end
        and entity._early_expiry_new_end == new_end
    ):
        entity._early_expiry_retry_initial_pending = False
        retry = import_module(
            "custom_components.rental_control.sensors.checkin.checkout_expiry_retry"
        )
        retry._clear_early_expiry_retry(entity, dispatch_finished=True)
        return
    if entity._early_expiry_retry_generation != generation:
        return
    if getattr(entity, "_early_expiry_retry_entity_removed", False):
        return
    # Once sent, the lock may hold new_end even if unconfirmed; keep it.
    if (
        not dispatched
        and entity._tracked_event_start == tracked_start
        and entity._tracked_event_slot_name == tracked_name
        and entity._tracked_event_end == new_end
    ):
        entity._tracked_event_end = previous_end
    entity._early_expiry_retry_initial_pending = False
    retry = import_module(
        "custom_components.rental_control.sensors.checkin.checkout_expiry_retry"
    )
    if _retry_reservation_is_current(entity):
        retry._publish_early_expiry_notice(entity)
        retry._schedule_early_expiry_retry(entity)
    else:
        retry._clear_early_expiry_retry(entity)


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
    retry = import_module(
        "custom_components.rental_control.sensors.checkin.checkout_expiry_retry"
    )
    retry._clear_early_expiry_retry(entity)
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
        "Keymaster unlock detected for managed slot %d; transitioning to checked_in",
        code_slot_num,
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
        "Ignoring keymaster unlock because slot %d does not match the tracked reservation",
        code_slot_num,
    )
    return True


async def async_update_lock_code_expiry(
    entity: Any,
    new_end: datetime,
    *,
    identity_end: datetime | None = None,
    on_dispatch: Callable[[], None] | None = None,
    pre_dispatch_guard: Callable[[], bool] | None = None,
) -> bool:
    """Update keymaster lock code expiry after early checkout."""
    if not association_requires_verified_routing(entity._config_entry):
        return False
    if not coordinator_keymaster_runtime_enabled(entity.coordinator):
        return False
    if (
        getattr(entity, "_early_expiry_reservation_slot_name", None)
        or entity._tracked_event_slot_name
    ) is None or not entity.coordinator.event_overrides:
        return False
    return await async_update_verified_lock_code_expiry(
        entity,
        new_end,
        identity_end=identity_end,
        on_dispatch=on_dispatch,
        pre_dispatch_guard=pre_dispatch_guard,
    )
