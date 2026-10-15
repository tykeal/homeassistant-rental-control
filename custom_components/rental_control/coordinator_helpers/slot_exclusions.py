# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Config-entry access for operator slot exclusions."""

from __future__ import annotations

from typing import Any

from ..allocator.exclusions import parse_excluded_slots
from ..const import CONF_EXCLUDED_SLOTS
from ..const import DOMAIN


def load(hass: Any, entry_id: str) -> dict[int, str | None]:
    """Return one entry's persisted exclusions as a slot-to-reason mapping."""
    entry = _entry(hass, entry_id)
    if entry is None:
        return {}
    return parse_excluded_slots(dict(entry.data or {}).get(CONF_EXCLUDED_SLOTS))


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


__all__ = ["load"]
