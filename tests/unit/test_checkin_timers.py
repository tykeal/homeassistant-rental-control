# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Focused timer manager tests for the check-in sensor."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.util import dt as dt_util

from custom_components.rental_control.sensors.checkin.timers import CheckinTimerManager

NOW = datetime(2026, 6, 1, 12, tzinfo=dt_util.UTC)


class _Cancel:
    """Recording cancellation handle."""

    def __init__(self) -> None:
        """Initialize the cancellation recorder."""
        self.calls = 0

    def __call__(self) -> None:
        """Record cancellation."""
        self.calls += 1


def test_schedule_cancels_existing_handle_before_replace() -> None:
    """Scheduling a replacement cancels the old handle once."""
    handles = [_Cancel(), _Cancel()]
    seen: list[datetime] = []

    def tracker(*args: Any) -> _Cancel:
        """Record timer targets and return the next handle."""
        seen.append(args[2])
        return handles[len(seen) - 1]

    manager = CheckinTimerManager(object(), tracker)  # type: ignore[arg-type]
    manager.schedule("auto_checkin", lambda now: None, NOW)
    manager.schedule("auto_checkout", lambda now: None, NOW)

    assert handles[0].calls == 1
    assert handles[1].calls == 0
    assert manager.scheduled is not None
    assert manager.scheduled.purpose == "auto_checkout"


def test_clear_callback_handle_removes_active_handle() -> None:
    """Callback entry clears the active handle metadata."""
    manager = CheckinTimerManager(object(), lambda *args: _Cancel())  # type: ignore[arg-type]
    manager.schedule("auto_checkin", lambda now: None, NOW)

    manager.clear_callback_handle()

    assert manager.handle is None
    assert manager.scheduled is None


def test_cancel_without_handle_is_safe() -> None:
    """Cancelling with no active timer is a no-op."""
    manager = CheckinTimerManager(object(), lambda *args: _Cancel())  # type: ignore[arg-type]

    manager.cancel()

    assert manager.handle is None


def test_followup_metadata_is_recorded() -> None:
    """Scheduled transition records target and follow-up metadata."""
    followup = datetime(2026, 6, 2, tzinfo=dt_util.UTC)
    manager = CheckinTimerManager(object(), lambda *args: _Cancel())  # type: ignore[arg-type]

    manager.schedule("linger_to_no_reservation", lambda now: None, NOW, followup)

    assert manager.scheduled is not None
    assert manager.scheduled.target_time == NOW
    assert manager.scheduled.followon_start_day == followup
