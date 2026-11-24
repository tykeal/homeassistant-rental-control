# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Persistent retry lifecycle for early checkout expiry updates."""

from __future__ import annotations

import asyncio
from datetime import datetime
from datetime import timedelta
from importlib import import_module
import logging
from typing import Any

from homeassistant.components.persistent_notification import async_create
from homeassistant.components.persistent_notification import async_dismiss
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.util import dt as dt_util

from ...keymaster_topology import association_requires_verified_routing
from .checkout_expiry import _retry_reservation_is_current
from .checkout_expiry import _verified_retry_identity_end

_LOGGER = logging.getLogger("custom_components.rental_control.sensors.checkinsensor")
_EARLY_EXPIRY_NOTIFICATION_PREFIX = "rental_control_early_checkout_expiry"
_EARLY_EXPIRY_RETRY_DELAYS = (timedelta(minutes=5), timedelta(minutes=15))


def _notification_id(entity: Any) -> str:
    """Return a stable notification ID without reservation or PIN data."""
    return _notification_id_for_entry(entity._config_entry.entry_id)


def _notification_id_for_entry(entry_id: str) -> str:
    """Return a notification ID scoped to one config entry."""
    return f"{_EARLY_EXPIRY_NOTIFICATION_PREFIX}_{entry_id}"


def initialize_early_expiry_retry(entity: Any) -> None:
    """Initialize persisted and runtime retry lifecycle fields."""
    entity._early_expiry_original_end = None
    entity._early_expiry_new_end = None
    entity._early_expiry_retry_attempt = 0
    entity._early_expiry_retry_slot = None
    entity._early_expiry_reservation_start = None
    entity._early_expiry_reservation_slot_name = None
    entity._early_expiry_retry_was_dispatched = False
    entity._early_expiry_retry_initial_pending = False
    entity._early_expiry_notice_published = False
    entity._early_expiry_notice_mode = None
    entity._early_expiry_retry_dispatched = False
    entity._early_expiry_retry_clear_when_done = False
    entity._early_expiry_retry_entity_removed = False
    entity._early_expiry_retry_handle = None
    entity._early_expiry_retry_task = None
    entity._early_expiry_retry_generation = 0


def early_expiry_snapshot_fields(entity: Any) -> dict[str, Any]:
    """Return retry identity fields for inclusion in the sensor snapshot."""
    return {
        "early_expiry_original_end": entity._early_expiry_original_end,
        "early_expiry_new_end": entity._early_expiry_new_end,
        "early_expiry_retry_attempt": entity._early_expiry_retry_attempt,
        "early_expiry_retry_slot": entity._early_expiry_retry_slot,
        "early_expiry_reservation_start": entity._early_expiry_reservation_start,
        "early_expiry_reservation_slot_name": (
            entity._early_expiry_reservation_slot_name
        ),
        "early_expiry_retry_was_dispatched": (
            entity._early_expiry_retry_was_dispatched
        ),
        "early_expiry_retry_initial_pending": (
            entity._early_expiry_retry_initial_pending
        ),
    }


def dismiss_early_expiry_notice(hass: Any, entry_id: str) -> None:
    """Dismiss a retry notice when its config entry is permanently removed."""
    async_dismiss(hass, _notification_id_for_entry(entry_id))


def _publish_early_expiry_notice(entity: Any) -> None:
    """Explain why expiry remains pending without overstating dispatch."""
    if not hasattr(entity._hass, "bus"):
        return
    dispatched = getattr(entity, "_early_expiry_retry_was_dispatched", False)
    if not association_requires_verified_routing(entity._config_entry):
        if dispatched:
            mode = "legacy-after-dispatch"
            message = (
                "Rental Control cannot safely retry the lock-code expiry while "
                "this legacy association can be reassigned between slots. No "
                "further write was sent; an earlier verified update may have "
                "reached the lock. Retry will wait until verified routing and "
                "slot serialization are available, up to the reservation's "
                "original end time."
            )
        else:
            mode = "legacy-blocked"
            message = (
                "Rental Control did not change the lock-code expiry because "
                "this legacy association cannot safely serialize against slot "
                "reassignment. No expiry write was sent. The pending retry "
                "will wait until verified routing and slot serialization are "
                "available, up to the reservation's original end time."
            )
    elif dispatched:
        mode = "verified-unconfirmed"
        message = (
            "Rental Control could not confirm the early checkout lock-code "
            "expiry update. Checkout is complete; it will retry when it can "
            "safely match the original reservation, up to that reservation's "
            "original end time. The update may already have reached the lock."
        )
    else:
        mode = "verified-not-dispatched"
        message = (
            "Rental Control has not changed the lock-code expiry because it "
            "could not establish a safe verified update path. No expiry write "
            "was sent. It will retry when the original reservation can be "
            "safely verified, up to its original end time."
        )
    if getattr(entity, "_early_expiry_notice_mode", None) == mode:
        return
    async_create(
        entity._hass,
        message,
        title=(
            "Early checkout expiry waiting for verified routing"
            if mode.startswith("legacy")
            else "Early checkout expiry not confirmed"
        ),
        notification_id=_notification_id(entity),
    )
    entity._early_expiry_notice_published = True
    entity._early_expiry_notice_mode = mode


def _clear_early_expiry_retry(entity: Any, *, dispatch_finished: bool = False) -> None:
    """Cancel retry state and dismiss its standing notification."""
    task = getattr(entity, "_early_expiry_retry_task", None)
    try:
        current_task = asyncio.current_task()
    except RuntimeError:
        current_task = None
    if (
        task is not None
        and task is not current_task
        and getattr(entity, "_early_expiry_retry_dispatched", False)
        and not dispatch_finished
    ):
        cancel_early_expiry_retry(entity, dismiss=False)
        entity._early_expiry_retry_clear_when_done = True
        return
    had_pending_retry = getattr(entity, "_early_expiry_original_end", None) is not None
    cancel_early_expiry_retry(entity, dismiss=False)
    entity._early_expiry_original_end = None
    entity._early_expiry_new_end = None
    entity._early_expiry_retry_attempt = 0
    entity._early_expiry_retry_slot = None
    entity._early_expiry_reservation_start = None
    entity._early_expiry_reservation_slot_name = None
    entity._early_expiry_retry_was_dispatched = False
    entity._early_expiry_retry_initial_pending = False
    entity._early_expiry_notice_published = False
    entity._early_expiry_notice_mode = None
    entity._early_expiry_retry_clear_when_done = False
    if had_pending_retry and hasattr(entity._hass, "bus"):
        async_dismiss(entity._hass, _notification_id(entity))


def cancel_early_expiry_retry(
    entity: Any, *, dismiss: bool = True, entity_removed: bool = False
) -> None:
    """Cancel scheduled and active retry work for entity removal."""
    if entity_removed:
        entity._early_expiry_retry_entity_removed = True
    entity._early_expiry_retry_generation = (
        getattr(entity, "_early_expiry_retry_generation", 0) + 1
    )
    handle = getattr(entity, "_early_expiry_retry_handle", None)
    if handle is not None:
        handle()
        entity._early_expiry_retry_handle = None
    task = getattr(entity, "_early_expiry_retry_task", None)
    try:
        current_task = asyncio.current_task()
    except RuntimeError:
        current_task = None
    dispatched = (
        task is not None
        and task is not current_task
        and getattr(entity, "_early_expiry_retry_dispatched", False)
    )
    if task is not None and task is not current_task and not dispatched:
        task.cancel()
        entity._early_expiry_retry_task = None
    if dismiss and not dispatched and hasattr(entity._hass, "bus"):
        async_dismiss(entity._hass, _notification_id(entity))


def resume_early_expiry_retry(entity: Any) -> None:
    """Re-arm a persisted retry after reservation data becomes available."""
    entity._early_expiry_retry_entity_removed = False
    if getattr(entity, "_early_expiry_original_end", None) is None:
        return
    if entity._early_expiry_original_end <= dt_util.now():
        _clear_early_expiry_retry(entity)
        return
    if getattr(entity, "_early_expiry_retry_initial_pending", False):
        return
    overrides = entity.coordinator.event_overrides
    if overrides is None or not getattr(overrides, "ready", False):
        _publish_early_expiry_notice(entity)
        if (
            getattr(entity, "_early_expiry_retry_handle", None) is None
            and getattr(entity, "_early_expiry_retry_task", None) is None
        ):
            _schedule_early_expiry_retry(entity)
        return
    if not _retry_reservation_is_current(entity):
        _clear_early_expiry_retry(entity)
        return
    _publish_early_expiry_notice(entity)
    if (
        getattr(entity, "_early_expiry_retry_handle", None) is None
        and getattr(entity, "_early_expiry_retry_task", None) is None
    ):
        _schedule_early_expiry_retry(entity)


def _schedule_early_expiry_retry(entity: Any) -> None:
    """Schedule a bounded-interval retry, capped at the original reservation end."""
    original_end = entity._early_expiry_original_end
    if original_end is None:
        return
    attempt = entity._early_expiry_retry_attempt
    delay = _EARLY_EXPIRY_RETRY_DELAYS[
        min(attempt, len(_EARLY_EXPIRY_RETRY_DELAYS) - 1)
    ]
    target = min(dt_util.now() + delay, original_end)
    if target <= dt_util.now():
        _clear_early_expiry_retry(entity)
        return
    cancel = getattr(entity, "_early_expiry_retry_handle", None)
    if cancel is not None:
        cancel()
    generation = getattr(entity, "_early_expiry_retry_generation", 0) + 1
    entity._early_expiry_retry_generation = generation

    @callback
    def retry_callback(now: datetime) -> None:
        """Start the async retry after validating its generation."""
        entity._early_expiry_retry_handle = None
        if entity._early_expiry_retry_generation != generation:
            return
        entity._early_expiry_retry_task = entity._hass.async_create_task(
            _async_run_early_expiry_retry(entity, generation, now)
        )

    entity._early_expiry_retry_handle = async_track_point_in_time(
        entity._hass, retry_callback, target
    )


def _reschedule_early_expiry_retry(entity: Any) -> None:
    """Keep the notice visible and schedule the next bounded retry."""
    entity._early_expiry_retry_attempt += 1
    _publish_early_expiry_notice(entity)
    _schedule_early_expiry_retry(entity)
    _write_retry_state(entity)


def _write_retry_state(entity: Any) -> None:
    """Write retry state only while its sensor remains loaded in Home Assistant."""
    if not getattr(entity, "_early_expiry_retry_entity_removed", False):
        entity.async_write_ha_state()


async def _async_run_early_expiry_retry(
    entity: Any, generation: int, now: datetime
) -> None:
    """Retry the shortening once, then reschedule or clear its notice."""
    try:
        if entity._early_expiry_retry_generation != generation:
            return
        original_end = entity._early_expiry_original_end
        new_end = entity._early_expiry_new_end
        tracked_start = (
            getattr(entity, "_early_expiry_reservation_start", None)
            or entity._tracked_event_start
        )
        tracked_name = (
            getattr(entity, "_early_expiry_reservation_slot_name", None)
            or entity._tracked_event_slot_name
        )
        if original_end is None or new_end is None or now >= original_end:
            _clear_early_expiry_retry(entity)
            return
        if not _retry_reservation_is_current(entity):
            _clear_early_expiry_retry(entity)
            return
        if not association_requires_verified_routing(entity._config_entry):
            _reschedule_early_expiry_retry(entity)
            return
        checkout_expiry = import_module(
            "custom_components.rental_control.sensors.checkin.checkout_expiry"
        )
        slot = checkout_expiry._retry_slot(
            entity,
            original_end,
            tracked_start,
            tracked_name,
            new_end,
        )
        entity._early_expiry_retry_slot = slot
        if slot is None:
            _reschedule_early_expiry_retry(entity)
            return
        if not _retry_reservation_is_current(entity):
            _clear_early_expiry_retry(entity)
            return
        updated, dispatched, previous_end = await _async_dispatch_retry(
            entity, checkout_expiry, slot, original_end, new_end
        )
        if updated:
            if (
                entity._early_expiry_original_end == original_end
                and entity._early_expiry_new_end == new_end
            ):
                _clear_early_expiry_retry(entity, dispatch_finished=True)
                _write_retry_state(entity)
            return
        _finish_failed_early_expiry_retry(
            entity,
            generation,
            (original_end, new_end, tracked_start, tracked_name),
            dispatched,
            previous_end,
        )
    finally:
        if getattr(entity, "_early_expiry_retry_task", None) is asyncio.current_task():
            entity._early_expiry_retry_task = None
        entity._early_expiry_retry_dispatched = False


def _finish_failed_early_expiry_retry(
    entity: Any,
    generation: int,
    reservation_identity: tuple[datetime, datetime, datetime | None, str | None],
    dispatched: bool,
    previous_end: datetime | None,
) -> None:
    """Restore safe tracking and only retry the same current reservation."""
    original_end, new_end, tracked_start, tracked_name = reservation_identity
    if (
        not dispatched
        and previous_end == original_end
        and entity._tracked_event_start == tracked_start
        and entity._tracked_event_slot_name == tracked_name
        and entity._tracked_event_end == new_end
    ):
        entity._tracked_event_end = original_end
    if entity._early_expiry_retry_generation != generation:
        if getattr(entity, "_early_expiry_retry_clear_when_done", False):
            _clear_early_expiry_retry(entity)
            _write_retry_state(entity)
        return
    if not _retry_reservation_is_current(entity):
        _clear_early_expiry_retry(entity)
        _write_retry_state(entity)
        return
    _reschedule_early_expiry_retry(entity)


async def _async_dispatch_retry(
    entity: Any,
    checkout_expiry: Any,
    slot: int,
    original_end: datetime,
    new_end: datetime,
) -> tuple[bool, bool, datetime | None]:
    """Dispatch one retry and report whether it may have reached the lock."""
    identity_start = (
        getattr(entity, "_early_expiry_reservation_start", None)
        or entity._tracked_event_start
    )
    identity_name = (
        getattr(entity, "_early_expiry_reservation_slot_name", None)
        or entity._tracked_event_slot_name
    )
    previous_end = entity._tracked_event_end
    tracked_identity = (
        entity._tracked_event_start == identity_start
        and entity._tracked_event_slot_name == identity_name
        and previous_end in {original_end, new_end}
    )
    if tracked_identity:
        entity._tracked_event_end = new_end
    dispatched = False
    entity._early_expiry_retry_dispatched = False
    identity_end = original_end
    if association_requires_verified_routing(entity._config_entry):
        assignment = getattr(entity.coordinator, "get_slot_assignment", None)
        if callable(assignment):
            identity_end = (
                _verified_retry_identity_end(entity, slot, assignment) or original_end
            )

    def mark_dispatched() -> None:
        """Record that this retry may have reached the lock."""
        nonlocal dispatched
        dispatched = True
        entity._early_expiry_retry_dispatched = True
        entity._early_expiry_retry_was_dispatched = True

    try:
        updated = await checkout_expiry.async_update_lock_code_expiry(
            entity,
            new_end,
            identity_end=identity_end,
            on_dispatch=mark_dispatched,
        )
    except Exception:
        _LOGGER.warning(
            "Early checkout expiry retry failed for entry %s slot %d",
            entity._config_entry.entry_id,
            slot,
        )
        updated = False
    return updated, dispatched, previous_end
