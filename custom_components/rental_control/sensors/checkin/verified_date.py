# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Verified Keymaster date-property dispatch for early checkout."""

from __future__ import annotations

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
) -> bool:
    """Set one date property through a current owner fence and exact capability."""
    coordinator = entity.coordinator
    config_entry = entity._config_entry
    overrides = coordinator.event_overrides
    slot_name = entity._tracked_event_slot_name
    start = entity._tracked_event_start
    reservation_end = identity_end or entity._tracked_event_end
    if (
        overrides is None
        or not isinstance(slot_name, str)
        or start is None
        or reservation_end is None
    ):
        return False
    matching_slots = [
        slot
        for slot in range(
            coordinator.start_slot,
            coordinator.start_slot + coordinator.max_events,
        )
        if overrides.get_slot_name(slot) == slot_name
    ]
    if len(matching_slots) != 1:
        return False
    slot = matching_slots[0]
    identity_key = make_reservation_fingerprint(
        config_entry.entry_id,
        slot_name,
        start,
        reservation_end,
    )
    assignment = getattr(coordinator, "get_slot_assignment", None)
    if not callable(assignment) or assignment(identity_key) != slot:
        return False
    if verified_slot_source(entity._hass, config_entry, slot) is None:
        return False

    property_key = f"datetime.code_slots:{slot}.accesslimit_date_range_end"
    try:
        async with async_slot_operation(coordinator, slot):
            intent = await prepare_verified_intent(
                coordinator,
                slot,
                identity_key,
                (property_key,),
                "allocation",
            )
            if intent is None:
                return False
            entity_id = intent.entity_id(property_key)
            await intent.async_call(
                property_key,
                "set_value",
                {"datetime": new_end.isoformat()},
            )
            if not await _async_wait_for_expected_datetime(
                coordinator.hass,
                entity_id,
                new_end,
                _SET_CODE_CONFIRMATION_TIMEOUT,
                intent=intent,
            ):
                _LOGGER.warning(
                    "Verified early checkout expiry update was not confirmed for entry %s slot %d",
                    config_entry.entry_id,
                    slot,
                )
                return False
    except VerifiedOperationRejected:
        _LOGGER.warning(
            "Verified early checkout expiry update was not confirmed for entry %s slot %d",
            config_entry.entry_id,
            slot,
        )
        return False
    except Exception:
        _LOGGER.warning(
            "Verified early checkout expiry update failed for entry %s slot %d",
            config_entry.entry_id,
            slot,
        )
        return False
    return True
