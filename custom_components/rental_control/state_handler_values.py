# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Normalize verified Keymaster feedback into reservation values."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from typing import cast

from homeassistant.util import dt

from .state_handler_models import SlotStateSnapshot
from .state_handler_models import StateChangeContext
from .state_handler_models import StateHandlerDeps


def build_override_update(
    context: StateChangeContext, snapshot: SlotStateSnapshot, deps: StateHandlerDeps
) -> tuple[str, str, datetime, datetime] | None:
    """Build normalized override fields or return None for unsafe feedback."""
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
    """Parse slot datetime values while preserving override timing."""
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
    """Parse one datetime state with the existing feedback precedence."""
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
    """Restore an untrimmed override when Keymaster reports its truncated name."""
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
