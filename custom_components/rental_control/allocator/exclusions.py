# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Pure helpers for operator slot exclusions.

An exclusion is operator intent that one managed slot is left out of the
allocator's unreadable-slot accounting.  Nothing in this module imports Home
Assistant, reads a config entry, or makes a decision from persisted state:
the parser is total, the serialiser is deterministic, and the guard
evaluation is a function of values its caller supplies.
"""

from __future__ import annotations

from typing import Any

REFUSAL_SLOT_OUT_OF_RANGE = "slot_out_of_range"
REFUSAL_SLOT_READABLE = "slot_readable"
REFUSAL_SLOT_CLAIMED = "slot_claimed"


def parse_excluded_slots(raw: Any) -> dict[int, str | None]:
    """Return persisted exclusions as a slot-to-reason mapping.

    The parser never raises.  A persisted value this function cannot place is
    dropped rather than reported, so a malformed entry can never stop an entry
    from setting up.
    """
    if not isinstance(raw, list):
        return {}
    exclusions: dict[int, str | None] = {}
    for member in raw:
        if not isinstance(member, dict):
            continue
        slot = member.get("slot")
        if isinstance(slot, bool) or not isinstance(slot, int) or slot < 1:
            continue
        reason = member.get("reason")
        exclusions[slot] = reason if isinstance(reason, str) else None
    return exclusions


def serialize_excluded_slots(
    exclusions: dict[int, str | None],
) -> list[dict[str, Any]]:
    """Return the persisted form of an exclusion mapping, slots ascending."""
    return [{"slot": slot, "reason": exclusions[slot]} for slot in sorted(exclusions)]


def refusal_for_exclusion(
    slot: int,
    *,
    start_slot: int,
    max_events: int,
    unreadable_slots: frozenset[int],
    managed_slots: frozenset[int],
    claimed_slots: frozenset[int],
) -> str | None:
    """Return the refusal reason for excluding one slot, or None.

    The guards have no override.  An out-of-range slot is reported before a
    readable one, because a slot outside the managed range has no meaningful
    observed status.
    """
    if slot < start_slot or slot >= start_slot + max_events:
        return REFUSAL_SLOT_OUT_OF_RANGE
    if slot not in managed_slots:
        return REFUSAL_SLOT_OUT_OF_RANGE
    if slot not in unreadable_slots:
        return REFUSAL_SLOT_READABLE
    if slot in claimed_slots:
        return REFUSAL_SLOT_CLAIMED
    return None


__all__ = [
    "REFUSAL_SLOT_CLAIMED",
    "REFUSAL_SLOT_OUT_OF_RANGE",
    "REFUSAL_SLOT_READABLE",
    "parse_excluded_slots",
    "refusal_for_exclusion",
    "serialize_excluded_slots",
]
