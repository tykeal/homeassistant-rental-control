# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Timer manager preserving check-in sensor single-handle semantics."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from homeassistant.core import CALLBACK_TYPE
from homeassistant.core import HomeAssistant

from .models import ScheduledTransition
from .models import TimerPurpose


class CheckinTimerManager:
    """Manage one active check-in scheduled transition handle."""

    def __init__(
        self,
        hass: HomeAssistant,
        tracker: Callable[
            [HomeAssistant, Callable[[datetime], None], datetime], CALLBACK_TYPE
        ],
    ) -> None:
        """Initialize the timer manager."""
        self._hass = hass
        self._tracker = tracker
        self.scheduled: ScheduledTransition | None = None

    @property
    def handle(self) -> CALLBACK_TYPE | None:
        """Return the active unsubscribe handle."""
        return self.scheduled.cancel_handle if self.scheduled else None

    @handle.setter
    def handle(self, value: CALLBACK_TYPE | None) -> None:
        """Set the active unsubscribe handle for compatibility."""
        if value is None:
            self.scheduled = None
        else:
            self.scheduled = ScheduledTransition(
                "auto_checkin", datetime.min, None, value
            )

    def cancel(self) -> None:
        """Cancel any active timer exactly once."""
        if self.scheduled and self.scheduled.cancel_handle is not None:
            self.scheduled.cancel_handle()
        self.scheduled = None

    def clear_callback_handle(self) -> None:
        """Clear the active handle at callback entry."""
        self.scheduled = None

    def schedule(
        self,
        purpose: TimerPurpose,
        callback: Callable[[datetime], None],
        target_time: datetime,
        followon_start_day: datetime | None = None,
    ) -> None:
        """Cancel the old handle and schedule a new absolute-time callback."""
        self.cancel()
        handle = self._tracker(self._hass, callback, target_time)
        self.scheduled = ScheduledTransition(
            purpose=purpose,
            target_time=target_time,
            followon_start_day=followon_start_day,
            cancel_handle=handle,
        )
