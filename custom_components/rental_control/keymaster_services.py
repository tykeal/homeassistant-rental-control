# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Keymaster service helpers for Rental Control."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from datetime import datetime

from homeassistant.components.button import DOMAIN as BUTTON
from homeassistant.components.datetime import DOMAIN as DATETIME
from homeassistant.components.text import DOMAIN as TEXT

from .helpers import OperationResult
from .keymaster_service_helpers import KeymasterServiceDeps
from .keymaster_service_helpers import (
    async_wait_for_expected_datetime as _async_wait_for_expected_datetime,
)
from .keymaster_service_helpers import (
    async_wait_for_expected_name as _async_wait_for_expected_name,
)
from .keymaster_service_helpers import disable_slot as _disable_slot
from .keymaster_service_helpers import enable_date_range as _enable_date_range
from .keymaster_service_helpers import enable_slot as _enable_slot
from .keymaster_service_helpers import force_clear_name as _force_clear_name
from .keymaster_service_helpers import (
    operation_failure_result as _operation_failure_result,
)
from .keymaster_service_helpers import run_gathered as _run_gathered
from .keymaster_service_helpers import (
    state_has_non_string_value as _state_has_non_string_value,  # noqa: F401
)
from .keymaster_service_helpers import (
    state_matches_expected_datetime as _state_matches_expected_datetime,  # noqa: F401
)
from .keymaster_service_helpers import (
    state_matches_expected_name as _state_matches_expected_name,  # noqa: F401
)
from .keymaster_service_helpers import write_slot_payload as _write_slot_payload


def _slot_display_name(coordinator, event, deps: KeymasterServiceDeps) -> str:
    """Return the Keymaster display name for a set-code operation."""
    prefix = f"{coordinator.event_prefix} " if coordinator.event_prefix else ""
    slot_name = f"{prefix}{event.extra_state_attributes['slot_name']}"
    if coordinator.trim_names:
        guest = event.extra_state_attributes["slot_name"]
        guest_max = coordinator.max_name_length - len(prefix)
        slot_name = f"{prefix}{deps.trim_name(guest, guest_max)}"
    return slot_name


def _buffered_window(
    coordinator, event, deps: KeymasterServiceDeps, sanitize_buffers: bool = False
) -> tuple[datetime, datetime]:
    """Return buffered start and end datetimes for an event."""
    if sanitize_buffers:
        before = getattr(coordinator, "code_buffer_before", 0)
        after = getattr(coordinator, "code_buffer_after", 0)
        before = before if isinstance(before, int) else 0
        after = after if isinstance(after, int) else 0
    else:
        before = coordinator.code_buffer_before
        after = coordinator.code_buffer_after
    buffered_start, buffered_end = deps.apply_buffer(
        event.extra_state_attributes["start"],
        event.extra_state_attributes["end"],
        before,
        after,
        coordinator,
    )
    return (
        deps.ensure_datetime(buffered_start, coordinator),
        deps.ensure_datetime(buffered_end, coordinator),
    )


async def async_fire_set_code(
    coordinator, event, slot: int, deps: KeymasterServiceDeps
) -> OperationResult:
    """Set codes into a slot."""
    deps.logger.debug("In async_fire_set_code - slot: %s", slot)
    deps.logger.debug("Event: %s", event)
    deps.logger.debug("Slot: %s", slot)
    lockname: str = coordinator.lockname
    if not lockname:
        return OperationResult(kind="set", slot=slot, unconfirmed=True)
    slot_name = _slot_display_name(coordinator, event, deps)
    expected_name = event.extra_state_attributes["slot_name"]
    if not coordinator.event_overrides.verify_slot_ownership(slot, expected_name):
        deps.logger.warning(
            "Slot %d ownership verification failed for '%s'; aborting set_code",
            slot,
            expected_name,
        )
        return OperationResult(kind="set", slot=slot, unconfirmed=True)
    try:
        window = _buffered_window(coordinator, event, deps, sanitize_buffers=True)
    except (TypeError, ValueError) as exc:
        return OperationResult(kind="set", slot=slot, failed=True, error=str(exc))
    try:
        await _disable_slot(coordinator, slot, lockname, deps)
        await _enable_date_range(coordinator, slot, lockname)
        await _write_slot_payload(
            coordinator, slot, lockname, (event, window, slot_name), deps
        )
        await _enable_slot(coordinator, slot, lockname, deps)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return _operation_failure_result(coordinator, slot, "set", exc, deps)
    return await _confirm_set_result(coordinator, slot, lockname, slot_name, deps)


async def _confirm_set_result(
    coordinator, slot: int, lockname: str, slot_name: str, deps: KeymasterServiceDeps
) -> OperationResult:
    """Confirm a set-code operation by reading the expected name."""
    name_entity = f"{TEXT}.{lockname}_code_slot_{slot}_name"
    if not await _async_wait_for_expected_name(
        coordinator.hass, name_entity, slot_name, deps.confirmation_timeout, deps
    ):
        return OperationResult(kind="set", slot=slot, unconfirmed=True)
    was_escalated = coordinator.event_overrides._escalated.get(slot, False)
    coordinator.event_overrides.record_retry_success(slot)
    if was_escalated:
        deps.dismiss_notification(
            coordinator.hass,
            notification_id=f"rental_control_slot_{slot}_failure",
        )
    return OperationResult(kind="set", slot=slot, confirmed=True)


async def async_fire_clear_code(
    coordinator, slot: int, expected_name: str | None, deps: KeymasterServiceDeps
) -> OperationResult:
    """Fire a clear_code signal."""
    deps.logger.debug(
        "In async_fire_clear_code - slot: %s, name: %s", slot, coordinator.name
    )
    hass = coordinator.hass
    reset_entity = f"{BUTTON}.{coordinator.lockname}_code_slot_{slot}_reset"
    if not coordinator.lockname:
        return OperationResult(kind="clear", slot=slot, unconfirmed=True)
    if (
        expected_name is not None
        and not coordinator.event_overrides.verify_slot_ownership(slot, expected_name)
    ):
        deps.logger.warning(
            "Slot %d ownership verification failed for '%s'; aborting clear_code",
            slot,
            expected_name,
        )
        return OperationResult(kind="clear", slot=slot, unconfirmed=True)
    try:
        await hass.services.async_call(
            domain=BUTTON,
            service="press",
            target={"entity_id": reset_entity},
            blocking=True,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return _operation_failure_result(coordinator, slot, "clear", exc, deps)
    await deps.sleep(0.5)
    unconfirmed, lingering_name = await _classify_name_after_reset(
        coordinator, slot, deps
    )
    pin_unconfirmed, lingering_pin = _classify_pin_after_reset(coordinator, slot, deps)
    return _finalize_clear_result(
        coordinator,
        slot,
        unconfirmed or pin_unconfirmed,
        lingering_name,
        lingering_pin,
        deps,
    )


async def _classify_name_after_reset(coordinator, slot: int, deps) -> tuple[bool, bool]:
    """Return unconfirmed and lingering flags for a reset name entity."""
    name_entity = f"{TEXT}.{coordinator.lockname}_code_slot_{slot}_name"
    name_state = coordinator.hass.states.get(name_entity)
    if name_state is None or deps.is_unreadable_text(name_state.state):
        return True, False
    if deps.is_cleared_text(name_state.state):
        return False, False
    deps.logger.warning(
        "Slot %d name '%s' persisted after reset; forcing name clear via text.set_value",
        slot,
        name_state.state,
    )
    await _force_clear_name(coordinator, slot, name_entity, deps)
    name_state = coordinator.hass.states.get(name_entity)
    if name_state is None or deps.is_unreadable_text(name_state.state):
        return True, False
    return False, not deps.is_cleared_text(name_state.state)


def _classify_pin_after_reset(coordinator, slot: int, deps) -> tuple[bool, bool]:
    """Return unconfirmed and lingering flags for a reset PIN entity."""
    pin_entity = f"{TEXT}.{coordinator.lockname}_code_slot_{slot}_pin"
    pin_state = coordinator.hass.states.get(pin_entity)
    if pin_state is None or deps.is_unreadable_text(pin_state.state):
        return True, False
    return False, not deps.is_cleared_text(pin_state.state)


def _finalize_clear_result(
    coordinator,
    slot: int,
    unconfirmed: bool,
    lingering_name: bool,
    lingering_pin: bool,
    deps,
) -> OperationResult:
    """Return the final clear result and update retry bookkeeping."""
    if lingering_name or lingering_pin:
        return OperationResult(
            kind="clear",
            slot=slot,
            unconfirmed=True,
            lingering_name=lingering_name,
            lingering_pin=lingering_pin,
        )
    if unconfirmed:
        return OperationResult(kind="clear", slot=slot, unconfirmed=True)
    was_escalated = coordinator.event_overrides._escalated.get(slot, False)
    coordinator.event_overrides.record_retry_success(slot)
    if was_escalated:
        deps.dismiss_notification(
            coordinator.hass,
            notification_id=f"rental_control_slot_{slot}_clear_failure",
        )
    return OperationResult(kind="clear", slot=slot, confirmed=True)


async def async_fire_update_times(
    coordinator, event, slot: int, deps: KeymasterServiceDeps
) -> OperationResult:
    """Update times on slot."""
    lockname: str = coordinator.lockname
    slot_name: str = event.extra_state_attributes["slot_name"]
    if not slot or not lockname:
        return OperationResult(kind="update_times", slot=slot, unconfirmed=True)
    if not coordinator.event_overrides.verify_slot_ownership(slot, slot_name):
        deps.logger.warning(
            "Slot %d ownership verification failed for '%s'; aborting update_times",
            slot,
            slot_name,
        )
        return OperationResult(kind="update_times", slot=slot, unconfirmed=True)
    try:
        window = _buffered_window(coordinator, event, deps)
    except (TypeError, ValueError) as exc:
        return OperationResult(
            kind="update_times", slot=slot, failed=True, error=str(exc)
        )
    try:
        await _write_update_time_calls(coordinator, slot, lockname, window, deps)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return OperationResult(
            kind="update_times", slot=slot, failed=True, error=str(exc)
        )
    return await _confirm_update_times(coordinator, slot, lockname, window, deps)


async def _write_update_time_calls(
    coordinator, slot: int, lockname: str, window, deps
) -> None:
    """Write Keymaster end then start datetime values."""
    buffered_start, buffered_end = window
    coro: list[Coroutine] = []
    deps.add_call(
        coordinator.hass,
        coro,
        DATETIME,
        "set_value",
        f"datetime.{lockname}_code_slot_{slot}_date_range_end",
        {"datetime": buffered_end},
    )
    deps.add_call(
        coordinator.hass,
        coro,
        DATETIME,
        "set_value",
        f"datetime.{lockname}_code_slot_{slot}_date_range_start",
        {"datetime": buffered_start},
    )
    await _run_gathered(coro, deps)


async def _confirm_update_times(
    coordinator, slot: int, lockname: str, window, deps
) -> OperationResult:
    """Confirm start and end datetime entity values."""
    buffered_start, buffered_end = window
    start_entity_id = f"datetime.{lockname}_code_slot_{slot}_date_range_start"
    end_entity_id = f"datetime.{lockname}_code_slot_{slot}_date_range_end"
    start_confirmed, end_confirmed = await asyncio.gather(
        _async_wait_for_expected_datetime(
            coordinator.hass,
            start_entity_id,
            buffered_start,
            deps.confirmation_timeout,
            deps,
        ),
        _async_wait_for_expected_datetime(
            coordinator.hass,
            end_entity_id,
            buffered_end,
            deps.confirmation_timeout,
            deps,
        ),
    )
    if not start_confirmed or not end_confirmed:
        return OperationResult(kind="update_times", slot=slot, unconfirmed=True)
    return OperationResult(kind="update_times", slot=slot, confirmed=True)
