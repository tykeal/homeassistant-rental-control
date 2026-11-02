# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Operator notification for unreadable managed lock slots."""

from __future__ import annotations

import logging

from ..allocator import entry_info
from ..const import DOMAIN
from ..const import NAME
from . import slot_notifications

_LOGGER = logging.getLogger(__name__)
_UNREADABLE_NOTICE = slot_notifications.SlotNotice(
    prefix=f"{DOMAIN}_unreadable_slots", title=f"{NAME} unreadable lock slots"
)


def unreadable_slots_notification_id(entry_id: str) -> str:
    """Return the persistent notification id for an entry's unreadable slots."""
    return _UNREADABLE_NOTICE.notification_id(entry_id)


def dismiss_unreadable_slots(hass: object, entry_id: str) -> None:
    """Clear an entry's unreadable-slot notification."""
    _UNREADABLE_NOTICE.dismiss(hass, entry_id)


def report_unreadable_slots(
    hass: object,
    entry_id: str,
    lockname: str | None,
    slots: list[int],
) -> None:
    """Reconcile the persistent notification for unreadable managed slots."""
    if not slot_notifications.can_notify(hass):
        return
    if not slots:
        dismiss_unreadable_slots(hass, entry_id)
        return
    slot_list = ", ".join(str(slot) for slot in slots)
    label = "slots" if len(slots) > 1 else "slot"
    lock_label = lockname or "unknown lock"
    _LOGGER.warning(
        "Entry %s cannot read managed %s %s on lock %s; issuance is withheld",
        entry_id,
        label,
        slot_list,
        lock_label,
    )
    name = entry_info.entry_name(hass, entry_id)
    message = (
        f"Rental Control entry {name} cannot read managed {label} "
        f"{slot_list} on lock {lock_label}. Door code issuance is withheld "
        "while those slots stay unreadable, because an unreadable slot may "
        "still hold a code the integration cannot see. If this condition is "
        "present when Home Assistant starts, issuance is withheld for ALL "
        "Rental Control entries, not just this one, because every entry "
        "shares one door code space. To recover, restore readable Keymaster "
        f"entities for {label} {slot_list}; deleting those entities does not "
        "help, because the slots stay in the managed range and report as "
        "unknown. If the slots are gone for good, reconfigure this entry's "
        "start slot or event count so that the managed range no longer "
        "covers them, or call the rental_control.set_slot_exclusion action "
        "to exclude each dead slot from the issuance gate."
    )
    _UNREADABLE_NOTICE.publish(hass, entry_id, message)


__all__ = [
    "dismiss_unreadable_slots",
    "report_unreadable_slots",
    "unreadable_slots_notification_id",
]
