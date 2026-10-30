# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Pure bootstrap decisions for Keymaster slots.

The coordinator shell reads Home Assistant state into
:class:`~.models.KeymasterSlotSnapshot` objects and performs all async
service calls. This module decides *what* to do for each slot during
first-load bootstrap (override setup).
"""

from __future__ import annotations

from datetime import datetime

from ..util import dt as _dt
from ..util import is_cleared_keymaster_text_state
from ..util import is_unreadable_keymaster_text_state
from .models import BootstrapDecision
from .models import EventOverrideUpdate
from .models import KeymasterSlotSnapshot
from .models import _adopted_slot_placeholder


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
