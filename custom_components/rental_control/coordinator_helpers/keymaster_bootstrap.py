# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Pure bootstrap and adoption decisions for Keymaster slots.

The coordinator shell reads Home Assistant state into
:class:`~.models.KeymasterSlotSnapshot` objects and performs all async
service calls and Store writes. This module decides *what* to do for each
slot during first-load bootstrap (override setup) and first-upgrade
adoption (Store mapping creation).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..const import SLOT_STATUS_OCCUPIED
from ..const import SLOT_STATUS_PENDING_CLEAR
from ..util import dt as _dt
from ..util import is_cleared_keymaster_text_state
from ..util import is_unreadable_keymaster_text_state
from .models import AdoptionMappingDecision
from .models import BootstrapDecision
from .models import EventOverrideUpdate
from .models import KeymasterSlotSnapshot
from .models import _adopted_slot_placeholder
from .models import _store_datetime


def _resolve_bootstrap_times(
    snapshot: KeymasterSlotSnapshot,
    default_start: datetime,
    default_end: datetime,
) -> tuple[datetime, datetime] | None:
    """Resolve the override window for a slot, or None to skip the slot."""
    if snapshot.use_date_range_state == "on":
        if snapshot.start_state is None:
            return None
        start = _dt.parse_datetime(snapshot.start_state)
        if start is None:
            return None
        if snapshot.end_state is None:
            return None
        end = _dt.parse_datetime(snapshot.end_state)
        if end is None:
            return None
        return start, end
    return default_start, default_end


def plan_bootstrap_slot(
    snapshot: KeymasterSlotSnapshot,
    default_start: datetime,
    default_end: datetime,
) -> BootstrapDecision:
    """Decide the override bootstrap action for a single slot.

    Args:
        snapshot: Raw per-slot Keymaster entity states.
        default_start: Local-day start used when date-range limits are off.
        default_end: Local-day end used when date-range limits are off.

    Returns:
        A :class:`BootstrapDecision`. ``override_update`` is ``None`` when the
        slot must be skipped (see ``skip_reason``).
    """
    slot = snapshot.slot
    if snapshot.pin_state is None:
        return BootstrapDecision(slot=slot, skip_reason="missing_pin")
    if is_unreadable_keymaster_text_state(snapshot.pin_state):
        return BootstrapDecision(slot=slot, skip_reason="unreadable_pin")
    slot_code_value = (
        ""
        if is_cleared_keymaster_text_state(snapshot.pin_state)
        else snapshot.pin_state
    )

    if snapshot.name_state is None:
        return BootstrapDecision(slot=slot, skip_reason="missing_name")
    if is_unreadable_keymaster_text_state(snapshot.name_state):
        return BootstrapDecision(slot=slot, skip_reason="unreadable_name")
    slot_name_value = (
        ""
        if is_cleared_keymaster_text_state(snapshot.name_state)
        else snapshot.name_state
    )

    force_clear = False
    placeholder_name: str | None = None
    date_off = snapshot.use_date_range_state in (None, "off")
    if (
        slot_name_value
        and is_cleared_keymaster_text_state(snapshot.pin_state)
        and not slot_code_value
        and date_off
    ):
        force_clear = True
        slot_name_value = ""
        slot_code_value = ""
    elif slot_code_value and not slot_name_value:
        placeholder_name = _adopted_slot_placeholder(slot)
        slot_name_value = placeholder_name

    times = _resolve_bootstrap_times(snapshot, default_start, default_end)
    if times is None:
        return BootstrapDecision(slot=slot, skip_reason="incomplete_date_range")

    start_time, end_time = times
    return BootstrapDecision(
        slot=slot,
        override_update=EventOverrideUpdate(
            slot=slot,
            slot_code=slot_code_value,
            slot_name=slot_name_value,
            start_time=start_time,
            end_time=end_time,
        ),
        force_clear=force_clear,
        placeholder_name=placeholder_name,
    )


def _adoption_mapping(
    slot: int,
    identity_key: str,
    slot_name: str,
    has_code: bool,
    window: tuple[datetime | None, datetime | None],
    meta: tuple[str, bool, str, str | None],
) -> dict[str, Any]:
    """Build the Store mapping payload for an adopted slot."""
    actual_start, actual_end = window
    name_value, date_range_on, now_str, pending_clear_since = meta
    status = SLOT_STATUS_OCCUPIED if has_code else SLOT_STATUS_PENDING_CLEAR
    return {
        "slot": slot,
        "status": status,
        "operation_id": None,
        "operation_kind": None,
        "identity": {
            "identity_key": identity_key,
            "summary": slot_name,
            "slot_name": slot_name,
            "start": _store_datetime(actual_start),
            "end": _store_datetime(actual_end),
            "uid_aliases": [],
            "booking_aliases": [],
        },
        "missing_count": 0,
        "pending_set_since": None,
        "pending_clear_since": pending_clear_since,
        "fingerprint_history": [],
        "updated_at": now_str,
        "last_observed_actual": {
            "slot": slot,
            "classification": "adopted",
            "name_state": name_value,
            "has_code": has_code,
            "start_state": _store_datetime(actual_start),
            "end_state": _store_datetime(actual_end),
            "use_date_range": date_range_on,
            "enabled": None,
        },
    }


def plan_adoption(
    snapshot: KeymasterSlotSnapshot,
    existing_slots: set[int],
    entry_id: str,
    prefix: str,
    now_str: str,
) -> AdoptionMappingDecision | None:
    """Decide the Store adoption mapping for a single slot, or None to skip.

    Args:
        snapshot: Raw per-slot Keymaster entity states.
        existing_slots: Slot numbers already present in the Store.
        entry_id: Config entry id used to build the identity key.
        prefix: Event prefix (with trailing space) to strip from names.
        now_str: ISO timestamp used for mapping metadata.

    Returns:
        An :class:`AdoptionMappingDecision`, or ``None`` when the slot is
        skipped.
    """
    slot = snapshot.slot
    if slot in existing_slots:
        return None
    if snapshot.name_state is None:
        return None
    if is_unreadable_keymaster_text_state(snapshot.name_state):
        return None
    name_value = (
        ""
        if is_cleared_keymaster_text_state(snapshot.name_state)
        else snapshot.name_state
    )

    has_code = False
    if snapshot.pin_state is not None:
        if is_unreadable_keymaster_text_state(snapshot.pin_state):
            return None
        code_value = (
            ""
            if is_cleared_keymaster_text_state(snapshot.pin_state)
            else snapshot.pin_state
        )
        has_code = bool(code_value)
    if not name_value and not has_code:
        return None

    date_range_on = snapshot.use_date_range_state == "on"
    actual_start: datetime | None = None
    actual_end: datetime | None = None
    if date_range_on:
        if snapshot.start_state is not None:
            actual_start = _dt.parse_datetime(snapshot.start_state)
        if snapshot.end_state is not None:
            actual_end = _dt.parse_datetime(snapshot.end_state)

    slot_name = name_value or _adopted_slot_placeholder(slot)
    if prefix and slot_name.startswith(prefix):
        slot_name = slot_name[len(prefix) :]

    pending_clear_since = now_str if not has_code else None
    identity_key = f"adopted.{entry_id}.slot{slot}"
    mapping = _adoption_mapping(
        slot,
        identity_key,
        slot_name,
        has_code,
        (actual_start, actual_end),
        (name_value, date_range_on, now_str, pending_clear_since),
    )
    status = SLOT_STATUS_OCCUPIED if has_code else SLOT_STATUS_PENDING_CLEAR
    return AdoptionMappingDecision(
        identity_key=identity_key,
        mapping=mapping,
        slot=slot,
        status=status,
    )
