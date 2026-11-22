# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Keymaster state-change handling for Rental Control."""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event
from homeassistant.core import EventStateChangedData
from homeassistant.core import HomeAssistant
from homeassistant.util import dt

from .const import COORDINATOR
from .const import DOMAIN
from .keymaster_lifecycle import get_keymaster_lifecycle
from .keymaster_operations import protected_registry_active
from .state_handler_models import VERIFIED_FEEDBACK_PROPERTIES
from .state_handler_models import SlotStateSnapshot
from .state_handler_models import StateChangeContext
from .state_handler_models import StateHandlerDeps
from .state_handler_values import build_override_update as _build_override_update


async def handle_state_change(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    event: Event[EventStateChangedData],
    deps: StateHandlerDeps,
) -> None:
    """Listener to track state changes of Keymaster input entities."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id][COORDINATOR]
    lockname = coordinator.lockname
    lifecycle = get_keymaster_lifecycle(hass, config_entry.entry_id)
    if (not lockname and lifecycle is None) or not coordinator.event_overrides:
        return
    context = _resolve_context(
        hass,
        event,
        coordinator,
        lockname,
        deps,
        config_entry_id=config_entry.entry_id,
    )
    if context is None:
        return
    await deps.sleep(0.1)
    if not _context_is_current(context):
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
    *,
    config_entry_id: str | None = None,
) -> StateChangeContext | None:
    """Resolve state-change inputs and extract the Keymaster slot number."""
    entity_id = event.data["entity_id"]
    event_new_state = event.data.get("new_state")
    event_new_value = (
        getattr(event_new_state, "state", None) if event_new_state is not None else None
    )
    event_has_new_value = event_new_value is not None
    deps.logger.debug("Handling Keymaster capability feedback")
    entry_id = config_entry_id or coordinator.config_entry.entry_id
    lifecycle = get_keymaster_lifecycle(hass, entry_id)
    if lifecycle is None and protected_registry_active(hass):
        return None
    property_key: str | None = None
    capability_entity_ids: dict[str, str] | None = None
    lifecycle_generation: int | None = None
    capability_identity = None
    if lifecycle is not None:
        lifecycle_generation = lifecycle.generation
        current = lifecycle.current
        matched = next(
            (
                resolution
                for resolution in current.values()
                if resolution.entity_id == entity_id and resolution.identity is not None
            ),
            None,
        )
        if matched is None or not matched.ready or matched.identity is None:
            return None
        property_key = matched.identity.property_key
        capability_identity = matched.identity
        if property_key not in {
            template.format(slot=matched.identity.slot)
            for template in VERIFIED_FEEDBACK_PROPERTIES
        }:
            return None
        slot_num = matched.identity.slot
        capability_entity_ids = {
            key: resolution.entity_id
            for key, resolution in current.items()
            if resolution.identity is not None
            and resolution.identity.slot == slot_num
            and resolution.ready
            and resolution.entity_id is not None
        }
    else:
        slot_match = re.search(r"_code_slot_(\d+)_", entity_id)
        if slot_match is None:
            deps.logger.warning("Could not identify the slot for Keymaster feedback")
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
        property_key,
        capability_entity_ids,
        lifecycle_generation,
        capability_identity,
        entry_id,
    )


def _context_is_current(context: StateChangeContext) -> bool:
    """Return whether a captured feedback event still names the same capability."""
    domain_data = context.hass.data.get(DOMAIN)
    if not isinstance(domain_data, dict):
        return False
    entry_id = context.config_entry_id
    if entry_id is None:
        return False
    entry_data = domain_data.get(entry_id)
    if not isinstance(entry_data, dict):
        return False
    if entry_data.get(COORDINATOR) is not context.coordinator:
        return False
    lifecycle = get_keymaster_lifecycle(
        context.hass,
        entry_id,
    )
    if context.capability_identity is None:
        return lifecycle is None and context.lifecycle_generation is None
    if lifecycle is None or lifecycle.generation != context.lifecycle_generation:
        return False
    current = lifecycle.current.get(context.property_key or "")
    return (
        current is not None
        and current.ready
        and current.entity_id == context.entity_id
        and current.identity == context.capability_identity
    )


async def _handle_reset_entity(
    context: StateChangeContext, deps: StateHandlerDeps
) -> bool:
    """Handle reset-button state changes and report whether processing is done."""
    if not _context_is_current(context):
        return True
    if context.capability_entity_ids is not None:
        if context.property_key != f"button.code_slots:{context.slot_num}.reset":
            return False
        required = (
            f"text.code_slots:{context.slot_num}.name",
            f"text.code_slots:{context.slot_num}.pin",
        )
        if any(key not in context.capability_entity_ids for key in required):
            return True
    elif "_reset" not in context.entity_id:
        return False
    deps.logger.debug("Resetting overrides for slot %s.", context.slot_num)
    await context.coordinator.event_overrides.async_update(
        context.slot_num,
        "",
        "",
        dt.start_of_local_day(),
        dt.start_of_local_day(),
        guard=lambda: _context_is_current(context),
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
        "Ignoring coordinator feedback for slot %s.",
        context.slot_num,
    )
    return True


def _read_slot_state_snapshot(
    context: StateChangeContext, deps: StateHandlerDeps
) -> SlotStateSnapshot | None:
    """Read all Home Assistant states required for a slot override update."""
    slot = context.slot_num
    lockname = context.lockname
    slot_enabled_entity_id = _context_entity_id(
        context,
        f"switch.code_slots:{slot}.enabled",
        f"switch.{lockname}_code_slot_{slot}_enabled",
    )
    if slot_enabled_entity_id is None:
        return None
    slot_state = context.hass.states.get(slot_enabled_entity_id)
    deps.logger.debug("Read enabled state for slot %s", slot)
    if slot_state is None:
        return None
    slot_enabled_state = (
        context.event_new_value
        if context.event_has_new_value and context.entity_id == slot_enabled_entity_id
        else slot_state.state
    )
    if slot_enabled_state != "on":
        deps.logger.debug("Slot %s is not enabled; skipping feedback update.", slot)
        return None
    return _read_enabled_slot_states(context, deps)


def _read_enabled_slot_states(
    context: StateChangeContext, deps: StateHandlerDeps
) -> SlotStateSnapshot | None:
    """Read Keymaster code, name, and optional date-range states."""
    slot = context.slot_num
    lockname = context.lockname
    slot_code_entity_id = _context_entity_id(
        context, f"text.code_slots:{slot}.pin", f"text.{lockname}_code_slot_{slot}_pin"
    )
    slot_name_entity_id = _context_entity_id(
        context,
        f"text.code_slots:{slot}.name",
        f"text.{lockname}_code_slot_{slot}_name",
    )
    if slot_code_entity_id is None or slot_name_entity_id is None:
        return None
    slot_code = context.hass.states.get(slot_code_entity_id)
    slot_name = context.hass.states.get(slot_name_entity_id)
    use_date_range_entity_id = _context_entity_id(
        context,
        f"switch.code_slots:{slot}.accesslimit_date_range_enabled",
        f"switch.{lockname}_code_slot_{slot}_use_date_range_limits",
    )
    if use_date_range_entity_id is None:
        return None
    use_date_range = context.hass.states.get(use_date_range_entity_id)
    deps.logger.debug("Read date-range enablement for slot %s", slot)
    use_date_range_state = _effective_state(
        context, use_date_range_entity_id, use_date_range
    )
    if use_date_range_state == "on":
        start_entity = _context_entity_id(
            context,
            f"datetime.code_slots:{slot}.accesslimit_date_range_start",
            f"datetime.{lockname}_code_slot_{slot}_date_range_start",
        )
        end_entity = _context_entity_id(
            context,
            f"datetime.code_slots:{slot}.accesslimit_date_range_end",
            f"datetime.{lockname}_code_slot_{slot}_date_range_end",
        )
        if start_entity is None or end_entity is None:
            return None
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


def _context_entity_id(
    context: StateChangeContext, property_key: str, legacy_entity_id: str
) -> str | None:
    """Return one exact verified address or the unchanged legacy address."""
    if context.capability_entity_ids is None:
        return legacy_entity_id
    return context.capability_entity_ids.get(property_key)


def _effective_state(context: StateChangeContext, entity_id: str, state: Any) -> Any:
    """Return event value for the changed entity, otherwise the stored state."""
    if context.event_has_new_value and context.entity_id == entity_id:
        return context.event_new_value
    return state.state if state else None


async def _dispatch_override_update(
    context: StateChangeContext,
    update: tuple[str, str, datetime, datetime],
    deps: StateHandlerDeps,
) -> None:
    """Dispatch the final override update without launching reconciliation."""
    slot_code_value, slot_name_value, start_time, end_time = update
    deps.logger.debug(
        "Updating overrides for slot %s from state feedback.",
        context.slot_num,
    )
    if not _context_is_current(context):
        return
    await context.coordinator.update_event_overrides(
        context.slot_num,
        slot_code_value,
        slot_name_value,
        start_time,
        end_time,
        guard=lambda: _context_is_current(context),
    )
