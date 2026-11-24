# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Verified Keymaster date-property dispatch for early checkout."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import logging
from typing import Any

from ...keymaster_operations import VerifiedOperationRejected
from ...keymaster_operations import prepare_verified_intent
from ...keymaster_slot_operations import async_slot_operation
from ...keymaster_topology import verified_slot_source
from ...reconciliation import make_reservation_fingerprint
from ...util import _SET_CODE_CONFIRMATION_TIMEOUT
from ...util import _async_wait_for_expected_datetime

_LOGGER = logging.getLogger("custom_components.rental_control.sensors.checkinsensor")


async def async_update_verified_lock_code_expiry(
    entity: Any,
    new_end: datetime,
    *,
    identity_end: datetime | None,
    on_dispatch: Callable[[], None] | None = None,
) -> bool:
    """Set one date property through a current owner fence and exact capability.

    ``on_dispatch`` runs once the final guard has released the service call,
    so callers can tell an unconfirmed write from one never sent.
    """
    owned = _owned_slot(entity, identity_end)
    if owned is None:
        return False
    identity_key, slot = owned
    assignment_is_current = _assignment_guard(entity, slot, identity_key, new_end)
    if assignment_is_current is None:
        return False

    def release_dispatch() -> bool:
        """Run the final assignment check and record a released dispatch."""
        if not assignment_is_current(False):
            return False
        if on_dispatch is not None:
            on_dispatch()
        return True

    entry_id = entity._config_entry.entry_id
    try:
        return await _async_set_and_confirm(
            entity,
            slot,
            identity_key,
            new_end,
            assignment_is_current,
            release_dispatch,
        )
    except VerifiedOperationRejected:
        _log_unconfirmed(entry_id, slot)
        return False
    except Exception:
        _LOGGER.warning(
            "Verified early checkout expiry update failed for entry %s slot %d",
            entry_id,
            slot,
        )
        return False


def _log_unconfirmed(entry_id: str, slot: int) -> None:
    """Warn that a dispatched early-checkout expiry was not confirmed."""
    _LOGGER.warning(
        "Verified early checkout expiry update was not confirmed for entry %s slot %d",
        entry_id,
        slot,
    )


def _owned_slot(entity: Any, identity_end: datetime | None) -> tuple[str, int] | None:
    """Return the tracked reservation's identity and its managed slot."""
    coordinator = entity.coordinator
    overrides = coordinator.event_overrides
    slot_name = getattr(entity, "_early_expiry_reservation_slot_name", None)
    if slot_name is None:
        slot_name = entity._tracked_event_slot_name
    start = getattr(entity, "_early_expiry_reservation_start", None)
    if start is None:
        start = entity._tracked_event_start
    reservation_end = identity_end or entity._tracked_event_end
    if (
        overrides is None
        or not isinstance(slot_name, str)
        or start is None
        or reservation_end is None
    ):
        return None
    identity_key = make_reservation_fingerprint(
        entity._config_entry.entry_id,
        slot_name,
        start,
        reservation_end,
    )
    assignment = getattr(coordinator, "get_slot_assignment", None)
    if not callable(assignment):
        return None
    slot = assignment(identity_key)
    if (
        not isinstance(slot, int)
        or isinstance(slot, bool)
        or not coordinator.start_slot
        <= slot
        < coordinator.start_slot + coordinator.max_events
        or overrides.get_slot_name(slot) != slot_name
    ):
        return None
    return identity_key, slot


async def _async_set_and_confirm(
    entity: Any,
    slot: int,
    identity_key: str,
    new_end: datetime,
    assignment_is_current: Callable[[bool], bool],
    release_dispatch: Callable[[], bool],
) -> bool:
    """Dispatch the end date under the slot lock and await its confirmation."""
    coordinator = entity.coordinator
    property_key = f"datetime.code_slots:{slot}.accesslimit_date_range_end"
    async with async_slot_operation(coordinator, slot):
        if not assignment_is_current(False):
            return False
        intent = await prepare_verified_intent(
            coordinator,
            slot,
            identity_key,
            (property_key,),
            "allocation",
        )
        if intent is None or not assignment_is_current(False):
            return False
        entity_id = intent.entity_id(property_key)
        await intent.async_call(
            property_key,
            "set_value",
            {"datetime": new_end.isoformat()},
            pre_dispatch_guard=release_dispatch,
        )
        if not assignment_is_current(True):
            return False
        if not await _async_wait_for_expected_datetime(
            coordinator.hass,
            entity_id,
            new_end,
            _SET_CODE_CONFIRMATION_TIMEOUT,
            intent=intent,
        ):
            _log_unconfirmed(entity._config_entry.entry_id, slot)
            return False
        return assignment_is_current(True)


def _assignment_guard(
    entity: Any, slot: int, identity_key: str, new_end: datetime
) -> Callable[[bool], bool] | None:
    """Capture evidence needed to reject a replaced reservation."""
    coordinator = entity.coordinator
    config_entry = entity._config_entry
    overrides = coordinator.event_overrides
    slot_name = getattr(entity, "_early_expiry_reservation_slot_name", None)
    if slot_name is None:
        slot_name = entity._tracked_event_slot_name
    start = getattr(entity, "_early_expiry_reservation_start", None)
    if start is None:
        start = entity._tracked_event_start
    source = verified_slot_source(entity._hass, config_entry, slot)
    generation_reader = getattr(overrides, "get_feedback_generation", None)
    slot_overrides = getattr(overrides, "overrides", None)
    override_snapshot = _reservation_fields(
        slot_overrides.get(slot) if isinstance(slot_overrides, dict) else None
    )
    if source is None or not callable(generation_reader) or override_snapshot is None:
        return None
    generation = generation_reader(slot)

    def assignment_is_current(allow_expected_end: bool) -> bool:
        """Require the original reservation, assignment and source to remain live."""
        current_assignment = getattr(coordinator, "get_slot_assignment", None)
        if not callable(current_assignment):
            return False
        current_slot = current_assignment(identity_key)
        expected_override = dict(override_snapshot)
        current_override = _reservation_fields(overrides.overrides.get(slot))
        if (
            allow_expected_end
            and current_override is not None
            and current_override.get("end_time") == new_end
        ):
            expected_override["end_time"] = new_end
        return (
            coordinator.event_overrides is overrides
            and isinstance(current_slot, int)
            and not isinstance(current_slot, bool)
            and current_slot == slot
            and coordinator.start_slot
            <= slot
            < coordinator.start_slot + coordinator.max_events
            and overrides.get_feedback_generation(slot) == generation
            and current_override == expected_override
            and overrides.get_slot_name(slot) == slot_name
            and verified_slot_source(entity._hass, config_entry, slot) == source
        )

    return assignment_is_current


def _reservation_fields(override: Any) -> dict[str, Any] | None:
    """Return the override fields that identify which reservation owns a slot.

    The PIN is excluded: reservation identity is name, start and end, and a
    date-only write is unaffected by a PIN edit. A replacement reservation
    changes those fields or advances the feedback generation.
    """
    if not isinstance(override, dict):
        return None
    return {key: value for key, value in override.items() if key != "slot_code"}
