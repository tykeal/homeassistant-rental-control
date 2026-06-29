# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Support helpers for Keymaster service operations."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from collections.abc import Coroutine
from dataclasses import dataclass
from datetime import date
from datetime import datetime
import logging
from typing import Any

from homeassistant.components.datetime import DOMAIN as DATETIME
from homeassistant.components.switch import DOMAIN as SWITCH
from homeassistant.components.text import DOMAIN as TEXT
from homeassistant.core import Event
from homeassistant.core import EventStateChangedData
from homeassistant.core import HomeAssistant
from homeassistant.util import dt

from .helpers import OperationResult


@dataclass(frozen=True, slots=True)
class KeymasterServiceDeps:
    """Runtime dependencies supplied by the util compatibility shell."""

    sleep: Callable[..., Any]
    track_state_change: Callable[..., Callable[[], None]]
    confirmation_timeout: float
    create_notification: Callable[..., None]
    dismiss_notification: Callable[..., None]
    logger: logging.Logger
    add_call: Callable[..., list[Coroutine]]
    check_gather_results: Callable[..., None]
    raise_first_gather_exception: Callable[[list[object]], None]
    apply_buffer: Callable[..., tuple[date | datetime, date | datetime]]
    ensure_datetime: Callable[..., datetime]
    trim_name: Callable[[str, int], str]
    is_cleared_text: Callable[[Any], bool]
    is_unreadable_text: Callable[[Any], bool]


@dataclass(frozen=True, slots=True)
class _WaitContext:
    """State listener inputs for a targeted confirmation wait."""

    hass: HomeAssistant
    entity_ids: list[str]
    action: Callable[..., None]
    matches: Callable[[], bool]
    has_bad_value: Callable[[], bool]
    wait: Callable[[], Any]
    timeout: float


def state_matches_expected_name(hass: HomeAssistant, entity_id: str, name: str) -> bool:
    """Return whether the entity state currently matches the expected name."""
    state = hass.states.get(entity_id)
    return state is not None and isinstance(state.state, str) and state.state == name


def state_has_non_string_value(hass: HomeAssistant, entity_id: str) -> bool:
    """Return whether the entity exists with a non-HA state value."""
    state = hass.states.get(entity_id)
    return state is not None and not isinstance(state.state, str)


async def async_wait_for_expected_name(
    hass: HomeAssistant,
    entity_id: str,
    name: str,
    timeout: float,
    deps: KeymasterServiceDeps,
) -> bool:
    """Wait briefly for one name entity to match the expected slot name."""
    if state_matches_expected_name(hass, entity_id, name):
        return True
    if state_has_non_string_value(hass, entity_id):
        return False
    matched = asyncio.Event()

    def _handle_state_change(event: Event[EventStateChangedData]) -> None:
        """Set the wait flag when the target entity reaches the expected name."""
        new_state = event.data.get("new_state")
        if new_state is not None and new_state.state == name:
            matched.set()

    return await _wait_for_match(
        _WaitContext(
            hass,
            [entity_id],
            _handle_state_change,
            lambda: state_matches_expected_name(hass, entity_id, name),
            lambda: state_has_non_string_value(hass, entity_id),
            matched.wait,
            timeout,
        ),
        deps,
    )


def state_matches_expected_datetime(
    hass: HomeAssistant, entity_id: str, expected: datetime
) -> bool:
    """Return whether the entity state matches the expected datetime."""
    state = hass.states.get(entity_id)
    if state is None or not isinstance(state.state, str):
        return False
    parsed = dt.parse_datetime(state.state)
    return parsed is not None and dt.as_utc(parsed) == dt.as_utc(expected)


async def async_wait_for_expected_datetime(
    hass: HomeAssistant,
    entity_id: str,
    expected: datetime,
    timeout: float,
    deps: KeymasterServiceDeps,
) -> bool:
    """Wait briefly for one datetime entity to match the expected value."""
    if state_matches_expected_datetime(hass, entity_id, expected):
        return True
    if state_has_non_string_value(hass, entity_id):
        return False
    matched = asyncio.Event()

    def _handle_state_change(event: Event[EventStateChangedData]) -> None:
        """Set the wait flag when the target entity reaches the expected time."""
        new_state = event.data.get("new_state")
        if new_state is None or not isinstance(new_state.state, str):
            return
        parsed = dt.parse_datetime(new_state.state)
        if parsed is not None and dt.as_utc(parsed) == dt.as_utc(expected):
            matched.set()

    return await _wait_for_match(
        _WaitContext(
            hass,
            [entity_id],
            _handle_state_change,
            lambda: state_matches_expected_datetime(hass, entity_id, expected),
            lambda: state_has_non_string_value(hass, entity_id),
            matched.wait,
            timeout,
        ),
        deps,
    )


async def _wait_for_match(ctx: _WaitContext, deps: KeymasterServiceDeps) -> bool:
    """Register a targeted listener and wait for a matching state."""
    unsub = deps.track_state_change(ctx.hass, ctx.entity_ids, ctx.action)
    try:
        if ctx.matches():
            return True
        if ctx.has_bad_value():
            return False
        try:
            async with asyncio.timeout(ctx.timeout):
                await ctx.wait()
        except TimeoutError:
            return False
        return ctx.matches()
    finally:
        unsub()


def operation_failure_result(
    coordinator, slot: int, kind: str, exc: Exception, deps: KeymasterServiceDeps
) -> OperationResult:
    """Record a service operation failure and return its result."""
    escalated = coordinator.event_overrides.record_retry_failure(slot)
    if escalated:
        notification_id = f"rental_control_slot_{slot}_failure"
        if kind == "clear":
            notification_id = f"rental_control_slot_{slot}_clear_failure"
        deps.create_notification(
            coordinator.hass,
            f"Slot {slot} {kind.replace('_times', '')} command failed after repeated "
            f"retries. Manual intervention may be required.",
            title="Rental Control: Lock Command Failure",
            notification_id=notification_id,
        )
    return OperationResult(kind=kind, slot=slot, failed=True, error=str(exc))


async def run_gathered(coro: list[Coroutine], deps: KeymasterServiceDeps) -> None:
    """Run gathered service calls and propagate ordinary exceptions."""
    results = await asyncio.gather(*coro, return_exceptions=True)
    deps.check_gather_results(results, "Lock slot operation", deps.logger)
    deps.raise_first_gather_exception(results)


async def disable_slot(coordinator, slot: int, lockname: str, deps) -> None:
    """Disable a Keymaster code slot before writing new data."""
    coro: list[Coroutine] = []
    deps.add_call(
        coordinator.hass,
        coro,
        SWITCH,
        "turn_off",
        f"{SWITCH}.{lockname}_code_slot_{slot}_enabled",
        {},
    )
    await run_gathered(coro, deps)


async def enable_date_range(coordinator, slot: int, lockname: str) -> None:
    """Enable date-range limits before writing date values."""
    await coordinator.hass.services.async_call(
        domain=SWITCH,
        service="turn_on",
        target={
            "entity_id": f"{SWITCH}.{lockname}_code_slot_{slot}_use_date_range_limits"
        },
        blocking=True,
    )


async def write_slot_payload(
    coordinator, slot: int, lockname: str, payload, deps
) -> None:
    """Write end, start, PIN, and name values for a slot."""
    event, window, slot_name = payload
    buffered_start, buffered_end = window
    coro: list[Coroutine] = []
    deps.add_call(
        coordinator.hass,
        coro,
        DATETIME,
        "set_value",
        f"{DATETIME}.{lockname}_code_slot_{slot}_date_range_end",
        {"datetime": buffered_end},
    )
    deps.add_call(
        coordinator.hass,
        coro,
        DATETIME,
        "set_value",
        f"{DATETIME}.{lockname}_code_slot_{slot}_date_range_start",
        {"datetime": buffered_start},
    )
    deps.add_call(
        coordinator.hass,
        coro,
        TEXT,
        "set_value",
        f"{TEXT}.{lockname}_code_slot_{slot}_pin",
        {"value": event.extra_state_attributes["slot_code"]},
    )
    deps.add_call(
        coordinator.hass,
        coro,
        TEXT,
        "set_value",
        f"{TEXT}.{lockname}_code_slot_{slot}_name",
        {"value": slot_name},
    )
    await run_gathered(coro, deps)


async def enable_slot(coordinator, slot: int, lockname: str, deps) -> None:
    """Enable a Keymaster code slot after writes complete."""
    coro: list[Coroutine] = []
    deps.add_call(
        coordinator.hass,
        coro,
        SWITCH,
        "turn_on",
        f"{SWITCH}.{lockname}_code_slot_{slot}_enabled",
        {},
    )
    await run_gathered(coro, deps)


async def force_clear_name(coordinator, slot: int, name_entity: str, deps) -> None:
    """Force-clear a readable lingering Keymaster name entity."""
    try:
        await coordinator.hass.services.async_call(
            domain=TEXT,
            service="set_value",
            target={"entity_id": name_entity},
            service_data={"value": ""},
            blocking=True,
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        deps.logger.exception("Failed to force-clear name for slot %d", slot)
