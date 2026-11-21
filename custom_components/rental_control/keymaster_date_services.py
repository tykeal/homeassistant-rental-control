# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Verified and legacy Keymaster date-range operations."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine

from homeassistant.components.datetime import DOMAIN as DATETIME

from .helpers import OperationResult
from .keymaster_operations import VerifiedOperationIntent
from .keymaster_operations import VerifiedOperationRejected
from .keymaster_service_helpers import KeymasterServiceDeps
from .keymaster_service_helpers import (
    _require_legacy_address as _require_legacy_address,
)
from .keymaster_service_helpers import (
    async_wait_for_expected_datetime as _async_wait_for_expected_datetime,
)
from .keymaster_service_helpers import run_gathered as _run_gathered
from .keymaster_service_values import buffered_window as _buffered_window
from .keymaster_slot_operations import async_slot_operation


async def _async_fire_update_times_locked(
    coordinator,
    event,
    slot: int,
    deps: KeymasterServiceDeps,
    *,
    verified_intent: VerifiedOperationIntent | None = None,
) -> OperationResult:
    """Update times on slot."""
    lockname: str = coordinator.lockname
    slot_name: str = event.extra_state_attributes["slot_name"]
    if not slot or (verified_intent is None and not lockname):
        return OperationResult(kind="update_times", slot=slot, unconfirmed=True)
    if verified_intent is None:
        try:
            _require_legacy_address(coordinator)
        except VerifiedOperationRejected:
            return OperationResult(kind="update_times", slot=slot, unconfirmed=True)
    if verified_intent is not None:
        try:
            if verified_intent.slot != slot:
                raise VerifiedOperationRejected("operation_not_current")
            verified_intent.entity_id(
                f"datetime.code_slots:{slot}.accesslimit_date_range_start"
            )
            verified_intent.entity_id(
                f"datetime.code_slots:{slot}.accesslimit_date_range_end"
            )
        except VerifiedOperationRejected:
            return OperationResult(kind="update_times", slot=slot, unconfirmed=True)
    if not coordinator.event_overrides.verify_slot_ownership(slot, slot_name):
        deps.logger.warning(
            "Slot %d ownership verification failed; aborting update_times",
            slot,
        )
        return OperationResult(kind="update_times", slot=slot, unconfirmed=True)
    try:
        window = _buffered_window(coordinator, event, deps)
    except (TypeError, ValueError) as exc:
        return OperationResult(
            kind="update_times", slot=slot, failed=True, error=str(exc)
        )
    try:
        await _write_update_time_calls(
            coordinator, slot, lockname, window, deps, intent=verified_intent
        )
    except VerifiedOperationRejected:
        return OperationResult(kind="update_times", slot=slot, unconfirmed=True)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return OperationResult(
            kind="update_times", slot=slot, failed=True, error=str(exc)
        )
    return await _confirm_update_times(
        coordinator, slot, lockname, window, deps, intent=verified_intent
    )


async def _write_update_time_calls(
    coordinator,
    slot: int,
    lockname: str,
    window,
    deps,
    *,
    intent: VerifiedOperationIntent | None = None,
) -> None:
    """Write Keymaster end then start datetime values."""
    buffered_start, buffered_end = window
    if intent is not None:
        await intent.async_call(
            f"datetime.code_slots:{slot}.accesslimit_date_range_end",
            "set_value",
            {"datetime": buffered_end},
        )
        await intent.async_call(
            f"datetime.code_slots:{slot}.accesslimit_date_range_start",
            "set_value",
            {"datetime": buffered_start},
        )
        return
    _require_legacy_address(coordinator)
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
    coordinator,
    slot: int,
    lockname: str,
    window,
    deps,
    *,
    intent: VerifiedOperationIntent | None = None,
) -> OperationResult:
    """Confirm start and end datetime entity values."""
    buffered_start, buffered_end = window
    if intent is None:
        start_entity_id = f"datetime.{lockname}_code_slot_{slot}_date_range_start"
        end_entity_id = f"datetime.{lockname}_code_slot_{slot}_date_range_end"
    else:
        try:
            start_entity_id = intent.entity_id(
                f"datetime.code_slots:{slot}.accesslimit_date_range_start"
            )
            end_entity_id = intent.entity_id(
                f"datetime.code_slots:{slot}.accesslimit_date_range_end"
            )
        except VerifiedOperationRejected:
            return OperationResult(kind="update_times", slot=slot, unconfirmed=True)
    start_confirmed, end_confirmed = await asyncio.gather(
        _async_wait_for_expected_datetime(
            coordinator.hass,
            start_entity_id,
            buffered_start,
            deps.confirmation_timeout,
            deps,
            intent=intent,
        ),
        _async_wait_for_expected_datetime(
            coordinator.hass,
            end_entity_id,
            buffered_end,
            deps.confirmation_timeout,
            deps,
            intent=intent,
        ),
    )
    if not start_confirmed or not end_confirmed:
        return OperationResult(kind="update_times", slot=slot, unconfirmed=True)
    return OperationResult(kind="update_times", slot=slot, confirmed=True)


async def async_fire_update_times(
    coordinator,
    event,
    slot: int,
    deps: KeymasterServiceDeps,
    *,
    verified_intent: VerifiedOperationIntent | None = None,
) -> OperationResult:
    """Serialize date writes and both confirmations for one slot."""
    async with async_slot_operation(coordinator, slot):
        return await _async_fire_update_times_locked(
            coordinator, event, slot, deps, verified_intent=verified_intent
        )
