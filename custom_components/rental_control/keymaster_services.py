# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Keymaster service helpers for Rental Control."""

from __future__ import annotations

import asyncio

from homeassistant.components.button import DOMAIN as BUTTON
from homeassistant.components.text import DOMAIN as TEXT

from .helpers import OperationResult
from .keymaster_date_services import (
    _async_wait_for_expected_datetime as _async_wait_for_expected_datetime,
)
from .keymaster_date_services import async_fire_update_times as async_fire_update_times
from .keymaster_operations import VerifiedOperationIntent
from .keymaster_operations import VerifiedOperationRejected
from .keymaster_service_helpers import KeymasterServiceDeps
from .keymaster_service_helpers import (
    _require_legacy_address as _require_legacy_address,
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
from .keymaster_service_values import buffered_window as _buffered_window
from .keymaster_service_values import slot_display_name as _slot_display_name
from .keymaster_slot_operations import async_slot_operation


async def _async_fire_set_code_locked(
    coordinator,
    event,
    slot: int,
    deps: KeymasterServiceDeps,
    *,
    verified_intent: VerifiedOperationIntent | None = None,
) -> OperationResult:
    """Set codes into a slot."""
    deps.logger.debug("In async_fire_set_code - slot: %s", slot)
    deps.logger.debug("Slot: %s", slot)
    lockname: str = coordinator.lockname
    if verified_intent is None:
        try:
            _require_legacy_address(coordinator)
        except VerifiedOperationRejected:
            return OperationResult(kind="set", slot=slot, unconfirmed=True)
    if verified_intent is None and not lockname:
        return OperationResult(kind="set", slot=slot, unconfirmed=True)
    if verified_intent is not None:
        if verified_intent.slot != slot:
            return OperationResult(kind="set", slot=slot, unconfirmed=True)
        try:
            for property_key in (
                f"switch.code_slots:{slot}.enabled",
                f"switch.code_slots:{slot}.accesslimit_date_range_enabled",
                f"datetime.code_slots:{slot}.accesslimit_date_range_start",
                f"datetime.code_slots:{slot}.accesslimit_date_range_end",
                f"text.code_slots:{slot}.pin",
                f"text.code_slots:{slot}.name",
            ):
                verified_intent.entity_id(property_key)
        except VerifiedOperationRejected:
            return OperationResult(kind="set", slot=slot, unconfirmed=True)
    slot_name = _slot_display_name(coordinator, event, deps)
    expected_name = event.extra_state_attributes["slot_name"]
    if not coordinator.event_overrides.verify_slot_ownership(slot, expected_name):
        deps.logger.warning(
            "Slot %d ownership verification failed; aborting set_code",
            slot,
        )
        return OperationResult(kind="set", slot=slot, unconfirmed=True)
    try:
        window = _buffered_window(coordinator, event, deps, sanitize_buffers=True)
    except (TypeError, ValueError) as exc:
        return OperationResult(kind="set", slot=slot, failed=True, error=str(exc))
    try:
        await _disable_slot(coordinator, slot, lockname, deps, intent=verified_intent)
        await _enable_date_range(coordinator, slot, lockname, intent=verified_intent)
        await _write_slot_payload(
            coordinator,
            slot,
            lockname,
            (event, window, slot_name),
            deps,
            intent=verified_intent,
        )
        await _enable_slot(coordinator, slot, lockname, deps, intent=verified_intent)
    except VerifiedOperationRejected:
        return OperationResult(kind="set", slot=slot, unconfirmed=True)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return _operation_failure_result(coordinator, slot, "set", exc, deps)
    return await _confirm_set_result(
        coordinator,
        slot,
        lockname,
        slot_name,
        deps,
        intent=verified_intent,
    )


async def _confirm_set_result(
    coordinator,
    slot: int,
    lockname: str,
    slot_name: str,
    deps: KeymasterServiceDeps,
    *,
    intent: VerifiedOperationIntent | None = None,
) -> OperationResult:
    """Confirm a set-code operation by reading the expected name."""
    if intent is None:
        name_entity = f"{TEXT}.{lockname}_code_slot_{slot}_name"
    else:
        try:
            name_entity = intent.entity_id(f"text.code_slots:{slot}.name")
        except VerifiedOperationRejected:
            return OperationResult(kind="set", slot=slot, unconfirmed=True)
    if not await _async_wait_for_expected_name(
        coordinator.hass,
        name_entity,
        slot_name,
        deps.confirmation_timeout,
        deps,
        intent=intent,
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


async def _async_fire_clear_code_locked(
    coordinator,
    slot: int,
    expected_name: str | None,
    deps: KeymasterServiceDeps,
    *,
    verified_intent: VerifiedOperationIntent | None = None,
) -> OperationResult:
    """Fire a clear_code signal."""
    deps.logger.debug(
        "In async_fire_clear_code - slot: %s, name: %s", slot, coordinator.name
    )
    hass = coordinator.hass
    if verified_intent is None:
        try:
            _require_legacy_address(coordinator)
        except VerifiedOperationRejected:
            return OperationResult(kind="clear", slot=slot, unconfirmed=True)
    if verified_intent is None:
        reset_entity = f"{BUTTON}.{coordinator.lockname}_code_slot_{slot}_reset"
    else:
        try:
            if verified_intent.slot != slot:
                raise VerifiedOperationRejected("operation_not_current")
            reset_entity = verified_intent.entity_id(f"button.code_slots:{slot}.reset")
            verified_intent.entity_id(f"text.code_slots:{slot}.name")
            verified_intent.entity_id(f"text.code_slots:{slot}.pin")
        except VerifiedOperationRejected:
            return OperationResult(kind="clear", slot=slot, unconfirmed=True)
    if verified_intent is None and not coordinator.lockname:
        return OperationResult(kind="clear", slot=slot, unconfirmed=True)
    if (
        expected_name is not None
        and not coordinator.event_overrides.verify_slot_ownership(slot, expected_name)
    ):
        deps.logger.warning(
            "Slot %d ownership verification failed; aborting clear_code",
            slot,
        )
        return OperationResult(kind="clear", slot=slot, unconfirmed=True)
    try:
        if verified_intent is None:
            await hass.services.async_call(
                domain=BUTTON,
                service="press",
                target={"entity_id": reset_entity},
                blocking=True,
            )
        else:
            await verified_intent.async_call(
                f"button.code_slots:{slot}.reset",
                "press",
            )
    except VerifiedOperationRejected:
        return OperationResult(kind="clear", slot=slot, unconfirmed=True)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return _operation_failure_result(coordinator, slot, "clear", exc, deps)
    await deps.sleep(0.5)
    if verified_intent is not None and not await verified_intent.async_is_current():
        return OperationResult(kind="clear", slot=slot, unconfirmed=True)
    unconfirmed, lingering_name = await _classify_name_after_reset(
        coordinator, slot, deps, intent=verified_intent
    )
    pin_unconfirmed, lingering_pin = _classify_pin_after_reset(
        coordinator, slot, deps, intent=verified_intent
    )
    return _finalize_clear_result(
        coordinator,
        slot,
        unconfirmed or pin_unconfirmed,
        lingering_name,
        lingering_pin,
        deps,
    )


async def _classify_name_after_reset(
    coordinator, slot: int, deps, *, intent: VerifiedOperationIntent | None = None
) -> tuple[bool, bool]:
    """Return unconfirmed and lingering flags for a reset name entity."""
    if intent is None:
        name_entity = f"{TEXT}.{coordinator.lockname}_code_slot_{slot}_name"
    else:
        try:
            name_entity = intent.entity_id(f"text.code_slots:{slot}.name")
        except VerifiedOperationRejected:
            return True, False
    name_state = coordinator.hass.states.get(name_entity)
    if name_state is None or deps.is_unreadable_text(name_state.state):
        return True, False
    if deps.is_cleared_text(name_state.state):
        return False, False
    deps.logger.warning(
        "Slot %d name persisted after reset; attempting verified text cleanup",
        slot,
    )
    try:
        await _force_clear_name(coordinator, slot, name_entity, deps, intent=intent)
    except VerifiedOperationRejected:
        return True, False
    name_state = coordinator.hass.states.get(name_entity)
    if name_state is None or deps.is_unreadable_text(name_state.state):
        return True, False
    return False, not deps.is_cleared_text(name_state.state)


def _classify_pin_after_reset(
    coordinator, slot: int, deps, *, intent: VerifiedOperationIntent | None = None
) -> tuple[bool, bool]:
    """Return unconfirmed and lingering flags for a reset PIN entity."""
    if intent is None:
        pin_entity = f"{TEXT}.{coordinator.lockname}_code_slot_{slot}_pin"
    else:
        try:
            pin_entity = intent.entity_id(f"text.code_slots:{slot}.pin")
        except VerifiedOperationRejected:
            return True, False
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


async def async_fire_set_code(
    coordinator,
    event,
    slot: int,
    deps: KeymasterServiceDeps,
    *,
    verified_intent: VerifiedOperationIntent | None = None,
) -> OperationResult:
    """Serialize a complete set service pipeline for a verified slot."""
    async with async_slot_operation(coordinator, slot):
        return await _async_fire_set_code_locked(
            coordinator, event, slot, deps, verified_intent=verified_intent
        )


async def async_fire_clear_code(
    coordinator,
    slot: int,
    expected_name: str | None,
    deps: KeymasterServiceDeps,
    *,
    verified_intent: VerifiedOperationIntent | None = None,
) -> OperationResult:
    """Serialize reset and all resulting name/PIN confirmations."""
    async with async_slot_operation(coordinator, slot):
        return await _async_fire_clear_code_locked(
            coordinator,
            slot,
            expected_name,
            deps,
            verified_intent=verified_intent,
        )
