# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Keymaster state-change handling for Rental Control."""

from __future__ import annotations

from collections.abc import Awaitable
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import logging
import re
from typing import Any
from typing import cast

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event
from homeassistant.core import EventStateChangedData
from homeassistant.core import HomeAssistant
from homeassistant.util import dt

from .const import COORDINATOR
from .const import DOMAIN


@dataclass(frozen=True, slots=True)
class StateHandlerDeps:
    """Runtime dependencies supplied by the util compatibility shell."""

    sleep: Callable[..., Awaitable[Any]]
    logger: logging.Logger
    normalize_keymaster_text_state: Callable[[Any], str | None]
    trim_name: Callable[[str, int], str]


@dataclass(frozen=True, slots=True)
class StateChangeContext:
    """Resolved state-change context for one Keymaster entity event."""

    hass: HomeAssistant
    event: Event[EventStateChangedData]
    coordinator: Any
    lockname: str
    entity_id: str
    event_new_value: Any
    event_has_new_value: bool
    slot_num: int
    existing_override: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class SlotStateSnapshot:
    """Home Assistant states needed to normalize a slot update."""

    slot_code_entity_id: str
    slot_name_entity_id: str
    start_time_entity_id: str
    end_time_entity_id: str
    slot_code: Any
    slot_name: Any
    g_start_time: Any
    g_end_time: Any


async def handle_state_change(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    event: Event[EventStateChangedData],
    deps: StateHandlerDeps,
) -> None:
    """Listener to track state changes of Keymaster input entities."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id][COORDINATOR]
    lockname = coordinator.lockname
    if not lockname or not coordinator.event_overrides:
        return
    await deps.sleep(0.1)
    context = _resolve_context(hass, event, coordinator, lockname, deps)
    if context is None:
        return
    if await _handle_reset_entity(context, deps):
        return
    if _should_ignore_suppressed_feedback(context, deps):
        return
    snapshot = _read_slot_state_snapshot(context, deps)
    if snapshot is None:
        return
    update = _build_override_update(context, snapshot, deps)
    if update is None:
        return
    await _dispatch_override_update(context, update, deps)


def _resolve_context(
    hass: HomeAssistant,
    event: Event[EventStateChangedData],
    coordinator: Any,
    lockname: str,
    deps: StateHandlerDeps,
) -> StateChangeContext | None:
    """Resolve state-change inputs and extract the Keymaster slot number."""
    entity_id = event.data["entity_id"]
    event_new_state = event.data.get("new_state")
    event_new_value = (
        getattr(event_new_state, "state", None) if event_new_state is not None else None
    )
    event_has_new_value = event_new_value is not None
    deps.logger.debug(
        "Handling state change for %s in %s with event: %s", entity_id, lockname, event
    )
    slot_match = re.search(r"_code_slot_(\d+)_", entity_id)
    if slot_match is None:
        deps.logger.warning(
            "Could not extract slot number from entity_id: %s", entity_id
        )
        return None
    slot_num = int(slot_match.group(1))
    existing_override: dict[str, Any] | None = None
    if event_has_new_value and coordinator.event_overrides:
        existing_override = coordinator.event_overrides.overrides.get(slot_num)
    return StateChangeContext(
        hass,
        event,
        coordinator,
        lockname,
        entity_id,
        event_new_value,
        event_has_new_value,
        slot_num,
        existing_override,
    )


async def _handle_reset_entity(
    context: StateChangeContext, deps: StateHandlerDeps
) -> bool:
    """Handle reset-button state changes and report whether processing is done."""
    if "_reset" not in context.entity_id:
        return False
    deps.logger.debug(
        "Resetting overrides %s for %s.", context.slot_num, context.lockname
    )
    await context.coordinator.event_overrides.async_update(
        context.slot_num, "", "", dt.start_of_local_day(), dt.start_of_local_day()
    )
    return True


def _should_ignore_suppressed_feedback(
    context: StateChangeContext, deps: StateHandlerDeps
) -> bool:
    """Return whether the current event is suppressed coordinator feedback."""
    if not context.event_has_new_value:
        return False
    if not context.coordinator.event_overrides.should_suppress_state_change(
        context.slot_num, context.entity_id, context.event_new_value
    ):
        return False
    deps.logger.debug(
        "Ignoring coordinator feedback for %s slot %s.",
        context.lockname,
        context.slot_num,
    )
    return True


def _read_slot_state_snapshot(
    context: StateChangeContext, deps: StateHandlerDeps
) -> SlotStateSnapshot | None:
    """Read all Home Assistant states required for a slot override update."""
    slot = context.slot_num
    lockname = context.lockname
    slot_enabled_entity_id = f"switch.{lockname}_code_slot_{slot}_enabled"
    slot_state = context.hass.states.get(slot_enabled_entity_id)
    deps.logger.debug("Slot %s state: %s", slot, slot_state)
    if slot_state is None:
        return None
    slot_enabled_state = (
        context.event_new_value
        if context.event_has_new_value and context.entity_id == slot_enabled_entity_id
        else slot_state.state
    )
    if slot_enabled_state != "on":
        deps.logger.debug(
            "Slot %s is not enabled, skipping update for %s.", slot, lockname
        )
        return None
    return _read_enabled_slot_states(context, deps)


def _read_enabled_slot_states(
    context: StateChangeContext, deps: StateHandlerDeps
) -> SlotStateSnapshot:
    """Read Keymaster code, name, and optional date-range states."""
    slot = context.slot_num
    lockname = context.lockname
    slot_code_entity_id = f"text.{lockname}_code_slot_{slot}_pin"
    slot_name_entity_id = f"text.{lockname}_code_slot_{slot}_name"
    slot_code = context.hass.states.get(slot_code_entity_id)
    slot_name = context.hass.states.get(slot_name_entity_id)
    use_date_range_entity_id = (
        f"switch.{lockname}_code_slot_{slot}_use_date_range_limits"
    )
    use_date_range = context.hass.states.get(use_date_range_entity_id)
    deps.logger.debug("Use Date Range: %s", use_date_range)
    use_date_range_state = _effective_state(
        context, use_date_range_entity_id, use_date_range
    )
    if use_date_range_state == "on":
        start_entity = f"datetime.{lockname}_code_slot_{slot}_date_range_start"
        end_entity = f"datetime.{lockname}_code_slot_{slot}_date_range_end"
        return SlotStateSnapshot(
            slot_code_entity_id,
            slot_name_entity_id,
            start_entity,
            end_entity,
            slot_code,
            slot_name,
            context.hass.states.get(start_entity),
            context.hass.states.get(end_entity),
        )
    return SlotStateSnapshot(
        slot_code_entity_id,
        slot_name_entity_id,
        "",
        "",
        slot_code,
        slot_name,
        None,
        None,
    )


def _effective_state(context: StateChangeContext, entity_id: str, state: Any) -> Any:
    """Return event value for the changed entity, otherwise the stored state."""
    if context.event_has_new_value and context.entity_id == entity_id:
        return context.event_new_value
    return state.state if state else None


def _build_override_update(
    context: StateChangeContext, snapshot: SlotStateSnapshot, deps: StateHandlerDeps
) -> tuple[str, str, datetime, datetime] | None:
    """Build normalized override update fields or return None to skip."""
    if snapshot.slot_code is None or snapshot.slot_name is None:
        return None
    slot_code_value = _slot_text_value(
        context, snapshot.slot_code_entity_id, snapshot.slot_code, "slot_code", deps
    )
    if slot_code_value is None:
        return None
    slot_name_value = _slot_text_value(
        context, snapshot.slot_name_entity_id, snapshot.slot_name, "slot_name", deps
    )
    if slot_name_value is None:
        return None
    if slot_code_value and not slot_name_value:
        deps.logger.warning(
            "Ignoring Keymaster slot %s state change with a code but no readable name; keeping the slot out of the free pool.",
            context.slot_num,
        )
        return None
    start_time, end_time = _parse_slot_times(context, snapshot)
    slot_name_value = _restore_full_name_for_trim_match(context, slot_name_value, deps)
    return slot_code_value, slot_name_value, start_time, end_time


def _slot_text_value(
    context: StateChangeContext,
    entity_id: str,
    state: Any,
    key: str,
    deps: StateHandlerDeps,
) -> str | None:
    """Return the normalized text value for one slot text entity."""
    if context.event_has_new_value and context.entity_id == entity_id:
        return deps.normalize_keymaster_text_state(context.event_new_value)
    if context.event_has_new_value and context.existing_override is not None:
        return str(context.existing_override[key])
    return deps.normalize_keymaster_text_state(state.state)


def _parse_slot_times(
    context: StateChangeContext, snapshot: SlotStateSnapshot
) -> tuple[datetime, datetime]:
    """Parse start and end times with existing override preservation."""
    start_time = dt.start_of_local_day()
    end_time = dt.start_of_local_day()
    if context.event_has_new_value and context.existing_override is not None:
        start_time = context.existing_override["start_time"]
        end_time = context.existing_override["end_time"]
    start_time = _parse_one_time(
        context, snapshot.g_start_time, snapshot.start_time_entity_id, start_time
    )
    end_time = _parse_one_time(
        context, snapshot.g_end_time, snapshot.end_time_entity_id, end_time
    )
    return start_time, end_time


def _parse_one_time(
    context: StateChangeContext, state: Any, entity_id: str, current: datetime
) -> datetime:
    """Parse one datetime state while preserving feedback semantics."""
    if state is None:
        return current
    if context.event_has_new_value and context.entity_id == entity_id:
        parsed = dt.parse_datetime(context.event_new_value)
        return parsed if parsed else current
    if not (context.event_has_new_value and context.existing_override is not None):
        parsed = dt.parse_datetime(state.state)
        return parsed if parsed else current
    return current


def _restore_full_name_for_trim_match(
    context: StateChangeContext, slot_name_value: str, deps: StateHandlerDeps
) -> str:
    """Restore an untrimmed override name when Keymaster shows its trim."""
    if not context.coordinator.trim_names or not slot_name_value:
        return slot_name_value
    existing = (
        context.coordinator.event_overrides.overrides.get(context.slot_num)
        if context.coordinator.event_overrides
        else None
    )
    if not existing or not existing["slot_name"]:
        return slot_name_value
    prefix = (
        f"{context.coordinator.event_prefix} "
        if context.coordinator.event_prefix
        else ""
    )
    guest_max = context.coordinator.max_name_length - len(prefix)
    expected_trimmed = deps.trim_name(existing["slot_name"], guest_max)
    incoming_guest = (
        slot_name_value[len(prefix) :]
        if prefix and slot_name_value.startswith(prefix)
        else slot_name_value
    )
    if incoming_guest == expected_trimmed:
        return prefix + cast("str", existing["slot_name"])
    return slot_name_value


async def _dispatch_override_update(
    context: StateChangeContext,
    update: tuple[str, str, datetime, datetime],
    deps: StateHandlerDeps,
) -> None:
    """Dispatch the final override update without launching reconciliation."""
    slot_code_value, slot_name_value, start_time, end_time = update
    deps.logger.debug(
        "updating overrides for %s slot %s. slot_name: '%s', slot_code: '%s', start_time: '%s', end_time: '%s'",
        context.lockname,
        context.slot_num,
        slot_name_value,
        slot_code_value,
        start_time,
        end_time,
    )
    await context.coordinator.update_event_overrides(
        context.slot_num,
        slot_code_value,
        slot_name_value,
        start_time,
        end_time,
    )
