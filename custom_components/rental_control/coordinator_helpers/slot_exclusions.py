# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Config-entry access for operator slot exclusions."""

from __future__ import annotations

from typing import Any

from homeassistant.components.persistent_notification import async_create
from homeassistant.components.persistent_notification import async_dismiss

from ..allocator.exclusions import parse_excluded_slots
from ..allocator.exclusions import serialize_excluded_slots
from ..const import CONF_EXCLUDED_SLOTS
from ..const import DOMAIN
from ..const import NAME

_EXCLUDED_NOTIFICATION_ID = f"{DOMAIN}_excluded_slots"
# aislop-ignore-file ai-slop/hallucinated-import -- Provided by Home Assistant runtime.


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


def excluded_slots_notification_id(entry_id: str) -> str:
    """Return the persistent notification id for an entry's exclusions."""
    return f"{_EXCLUDED_NOTIFICATION_ID}_{entry_id}"


def dismiss_excluded_slots(hass: Any, entry_id: str) -> None:
    """Clear an entry's slot-exclusion notification."""
    if not hasattr(hass, "bus"):
        return
    async_dismiss(hass, excluded_slots_notification_id(entry_id))


def report_excluded_slots(
    hass: Any,
    entry_id: str,
    lockname: str | None,
    exclusions: dict[int, str | None],
) -> None:
    """Reconcile the standing notification for excluded managed slots."""
    if not hasattr(hass, "bus"):
        return
    if not exclusions:
        dismiss_excluded_slots(hass, entry_id)
        return
    label = "slots" if len(exclusions) > 1 else "slot"
    lock_label = lockname or "unknown lock"
    detail = "; ".join(
        f"slot {slot} ({exclusions[slot] or 'no reason recorded'})"
        for slot in sorted(exclusions)
    )
    message = (
        f"Rental Control entry {entry_id} is issuing door codes while "
        f"excluding unreadable managed {label} on lock {lock_label}: "
        f"{detail}. An excluded slot is still unreadable, so it may still "
        "hold a code this integration cannot see; treat that code as live "
        "until the slot is verified or cleared at the lock. The exclusion "
        "clears itself as soon as the slot becomes readable again or leaves "
        "the managed range. Saving this entry's options also clears it, "
        "which returns the entry to withholding issuance."
    )
    async_create(
        hass,
        message,
        title=f"{NAME} excluded lock slots",
        notification_id=excluded_slots_notification_id(entry_id),
    )


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


__all__ = [
    "dismiss_excluded_slots",
    "excluded_slots_notification_id",
    "load",
    "persist_excluded_slots",
    "report_excluded_slots",
    "revalidate",
]
