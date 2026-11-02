# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Shared persistent-notification scaffolding for per-entry slot notices."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.persistent_notification import async_create
from homeassistant.components.persistent_notification import async_dismiss

# aislop-ignore-file ai-slop/hallucinated-import -- Provided by Home Assistant runtime.


def can_notify(hass: Any) -> bool:
    """Return whether a hass object can carry persistent notifications."""
    return hasattr(hass, "bus")


@dataclass(frozen=True)
class SlotNotice:
    """One kind of standing, per-entry notification about lock slots."""

    prefix: str
    title: str

    def notification_id(self, entry_id: str) -> str:
        """Return the persistent notification id for one entry."""
        return f"{self.prefix}_{entry_id}"

    def dismiss(self, hass: Any, entry_id: str) -> None:
        """Clear one entry's notification of this kind."""
        if not can_notify(hass):
            return
        async_dismiss(hass, self.notification_id(entry_id))

    def publish(self, hass: Any, entry_id: str, message: str) -> None:
        """Create or replace one entry's notification of this kind."""
        if not can_notify(hass):
            return
        async_create(
            hass,
            message,
            title=self.title,
            notification_id=self.notification_id(entry_id),
        )


__all__ = ["SlotNotice", "can_notify"]
