# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Config-entry access for operator slot exclusions."""

from __future__ import annotations

from typing import Any

from ..allocator.exclusions import parse_excluded_slots
from ..allocator.exclusions import serialize_excluded_slots
from ..const import CONF_EXCLUDED_SLOTS
from ..const import DOMAIN


def load(hass: Any, entry_id: str) -> dict[int, str | None]:
    """Return one entry's persisted exclusions as a slot-to-reason mapping."""
    entry = _entry(hass, entry_id)
    if entry is None:
        return {}
    return parse_excluded_slots(dict(entry.data or {}).get(CONF_EXCLUDED_SLOTS))


def revalidate(
    exclusions: dict[int, str | None], observation: Any
) -> tuple[dict[int, str | None], dict[int, str | None]]:
    """Split exclusions into those still justified and those now obsolete.

    An exclusion only ever describes an unreadable slot inside the entry's
    managed range. A slot that became readable, or that left the managed
    range, no longer needs one, so it is cleared rather than kept.
    """
    kept: dict[int, str | None] = {}
    cleared: dict[int, str | None] = {}
    for slot, reason in exclusions.items():
        if slot in observation.unreadable_slots and slot in observation.managed_slots:
            kept[slot] = reason
        else:
            cleared[slot] = reason
    return kept, cleared


def persist_excluded_slots(
    hass: Any, entry_id: str, exclusions: dict[int, str | None]
) -> bool:
    """Write an entry's exclusions to its data, returning whether it wrote."""
    entry = _entry(hass, entry_id)
    if entry is None:
        return False
    data = dict(entry.data or {})
    serialized = serialize_excluded_slots(exclusions)
    if parse_excluded_slots(data.get(CONF_EXCLUDED_SLOTS)) == exclusions:
        return False
    if serialized:
        data[CONF_EXCLUDED_SLOTS] = serialized
    else:
        data.pop(CONF_EXCLUDED_SLOTS, None)
    hass.config_entries.async_update_entry(entry, data=data)
    return True


def _entry(hass: Any, entry_id: str) -> Any | None:
    """Return the Rental Control config entry for an entry id, if loaded."""
    config_entries = getattr(hass, "config_entries", None)
    if config_entries is None:
        return None
    getter = getattr(config_entries, "async_get_entry", None)
    if callable(getter):
        entry = getter(entry_id)
        if entry is not None:
            return entry
    entries = getattr(config_entries, "async_entries", None)
    if not callable(entries):
        return None
    for entry in entries(DOMAIN) or []:
        if getattr(entry, "entry_id", None) == entry_id:
            return entry
    return None


__all__ = ["load", "persist_excluded_slots", "revalidate"]
