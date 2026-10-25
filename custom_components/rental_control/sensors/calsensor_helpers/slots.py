# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Read-only slot reconciliation helpers for calendar sensors."""

from __future__ import annotations

from typing import Any

from ...bookings_only import anonymous_slot_name
from .models import SlotReadContext
from .models import SlotReadResult


def read_slot(context: SlotReadContext, coordinator: Any) -> SlotReadResult:
    """Read slot name, number, and code without mutating reconciliation state."""
    slot_name = context.get_slot_name(
        context.summary,
        context.description,
        context.event_prefix,
    )
    if slot_name is None and context.bookings_only_feed:
        # An anonymous booking is still programmed into a slot, so the
        # sensor must name it exactly as the coordinator did or it would
        # publish no code for a code that is live on the lock.
        slot_name = anonymous_slot_name(context.uid, context.start, context.end)
    slot_number: int | None = None
    slot_code: str | None = None
    if slot_name is not None:
        identity_key = context.make_reservation_fingerprint(
            context.entry_id,
            slot_name,
            context.start,
            context.end,
        )
        slot_number = coordinator.get_slot_assignment(identity_key)
        slot_code = coordinator.get_slot_code(identity_key)
    return SlotReadResult(
        slot_name=slot_name,
        slot_number=slot_number,
        slot_code=slot_code,
    )
