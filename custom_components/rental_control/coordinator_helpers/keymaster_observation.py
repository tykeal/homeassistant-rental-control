# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Pure classification of raw Keymaster slot state into ManagedSlot.

The coordinator shell reads Home Assistant entity states and packages them
into :class:`~.models.KeymasterSlotSnapshot` instances. This module turns a
snapshot into a :class:`~..reconciliation.ManagedSlot` observation plus the
diagnostics ``actual_state`` dictionary, without touching Home Assistant.
"""

from __future__ import annotations

from datetime import datetime

from ..reconciliation import ManagedSlot
from ..reconciliation import SlotStatus
from ..util import dt as _dt
from ..util import is_cleared_keymaster_text_state
from ..util import is_unreadable_keymaster_text_state
from .models import KeymasterSlotSnapshot


def _unknown_state(slot: int) -> dict:
    """Build the diagnostics dict used for UNKNOWN observations."""
    return {
        "slot": slot,
        "classification": SlotStatus.UNKNOWN.value,
        "name_state": None,
        "has_code": None,
        "start_state": None,
        "end_state": None,
        "use_date_range": None,
        "enabled": None,
    }


def _bool_switch(state: str | None) -> bool | None:
    """Return the boolean value of an on/off switch state, else None."""
    if state in ("on", "off"):
        return state == "on"
    return None


def classify_slot(
    snapshot: KeymasterSlotSnapshot, last_error: str | None
) -> tuple[ManagedSlot, dict]:
    """Classify a raw slot snapshot into a ManagedSlot and diagnostics dict.

    Args:
        snapshot: Raw per-slot Keymaster entity states.
        last_error: Last recorded slot error from the override cache.

    Returns:
        Tuple of the observed :class:`ManagedSlot` and the diagnostics
        ``actual_state`` payload to store for the slot.
    """
    slot = snapshot.slot
    if snapshot.name_state is None or snapshot.pin_state is None:
        ms = ManagedSlot(slot=slot, managed=True, status=SlotStatus.UNKNOWN)
        return ms, _unknown_state(slot)

    name_empty = is_cleared_keymaster_text_state(snapshot.name_state)
    code_empty = is_cleared_keymaster_text_state(snapshot.pin_state)
    unreadable = is_unreadable_keymaster_text_state(
        snapshot.name_state
    ) or is_unreadable_keymaster_text_state(snapshot.pin_state)

    if unreadable:
        ms = ManagedSlot(
            slot=slot,
            managed=True,
            status=SlotStatus.UNKNOWN,
            blocked_reason="unreadable",
        )
        return ms, _unknown_state(slot)

    name_value = "" if name_empty else snapshot.name_state
    code_value = "" if code_empty else snapshot.pin_state
    has_code = bool(code_value)

    date_range_on = snapshot.use_date_range_state == "on"
    date_range_enabled = _bool_switch(snapshot.use_date_range_state)
    enabled = _bool_switch(snapshot.enabled_state)

    actual_start: datetime | None = None
    actual_end: datetime | None = None
    if date_range_on:
        if snapshot.start_state is not None:
            actual_start = _dt.parse_datetime(snapshot.start_state)
        if snapshot.end_state is not None:
            actual_end = _dt.parse_datetime(snapshot.end_state)

    if has_code:
        status = SlotStatus.OCCUPIED
    elif name_value:
        status = SlotStatus.PHANTOM
    else:
        status = SlotStatus.FREE

    ms = ManagedSlot(
        slot=slot,
        managed=True,
        status=status,
        actual_name=name_value or None,
        actual_code=code_value or None,
        actual_code_present=has_code,
        actual_start=actual_start,
        actual_end=actual_end,
        date_range_enabled=date_range_enabled,
        enabled=enabled,
        persisted_identity_key=None,
        blocked_reason=None,
        preserve_unmatched=False,
        last_error=last_error,
    )
    actual_state = _actual_state(ms)
    return ms, actual_state


def _actual_state(ms: ManagedSlot) -> dict:
    """Return the diagnostics ``actual_state`` payload for a slot."""
    return {
        "slot": ms.slot,
        "classification": ms.status.value,
        "name_state": ms.actual_name,
        "has_code": bool(ms.actual_code_present),
        "start_state": ms.actual_start,
        "end_state": ms.actual_end,
        "use_date_range": ms.date_range_enabled,
        "enabled": ms.enabled,
    }
