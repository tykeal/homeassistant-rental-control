# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Focused persistence tests for decomposed check-in sensor helpers."""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta

from homeassistant.util import dt as dt_util

from custom_components.rental_control.const import CHECKIN_STATE_CHECKED_IN
from custom_components.rental_control.const import CHECKIN_STATE_NO_RESERVATION
from custom_components.rental_control.sensors.checkin.models import CheckinStateSnapshot
from custom_components.rental_control.sensors.checkin.persistence import (
    CheckinExtraStoredData,
)

_EXPECTED_KEYS = {
    "state",
    "tracked_event_summary",
    "tracked_event_start",
    "tracked_event_end",
    "tracked_event_slot_name",
    "checkin_source",
    "checkout_source",
    "checkout_time",
    "transition_target_time",
    "checked_out_event_key",
    "next_event_start_day",
    "checkin_lock_name",
}


def _snapshot() -> CheckinStateSnapshot:
    """Return a populated persistence snapshot."""
    start = datetime(2026, 6, 1, 16, tzinfo=dt_util.UTC)
    end = start + timedelta(days=3)
    return CheckinStateSnapshot(
        state=CHECKIN_STATE_CHECKED_IN,
        tracked_event_summary="Reserved - Ada",
        tracked_event_start=start,
        tracked_event_end=end,
        tracked_event_slot_name="Ada",
        checkin_source="keymaster",
        checkout_source=None,
        checkout_time=None,
        transition_target_time=end,
        checked_out_event_key="Reserved - Ada|2026-06-01T16:00:00+00:00",
        next_event_start_day=start,
        checkin_lock_name="front_door",
    )


def test_as_dict_emits_exact_key_set_and_iso_datetimes() -> None:
    """Stored data emits the legacy key set and ISO datetime strings."""
    data = CheckinExtraStoredData.from_snapshot(_snapshot()).as_dict()

    assert set(data) == _EXPECTED_KEYS
    assert data["tracked_event_start"] == "2026-06-01T16:00:00+00:00"
    assert data["transition_target_time"] == "2026-06-04T16:00:00+00:00"
    assert data["checkin_lock_name"] == "front_door"


def test_round_trip_and_legacy_keyword_construction() -> None:
    """Round-trip and legacy keyword construction preserve fields."""
    stored = CheckinExtraStoredData.from_snapshot(_snapshot())
    restored = CheckinExtraStoredData.from_dict(stored.as_dict())
    legacy = CheckinExtraStoredData(
        **stored.as_dict()
        | {
            "tracked_event_start": _snapshot().tracked_event_start,
            "tracked_event_end": _snapshot().tracked_event_end,
            "transition_target_time": _snapshot().transition_target_time,
            "next_event_start_day": _snapshot().next_event_start_day,
        }
    )

    assert restored.as_dict() == stored.as_dict()
    assert legacy.state == CHECKIN_STATE_CHECKED_IN


def test_missing_fields_default_to_no_reservation() -> None:
    """Older dictionaries with missing optional fields still load."""
    restored = CheckinExtraStoredData.from_dict({})

    assert restored.state == CHECKIN_STATE_NO_RESERVATION
    assert restored.checkin_lock_name is None
    assert restored.next_event_start_day is None


def test_invalid_datetimes_log_warning(caplog) -> None:  # type: ignore[no-untyped-def]
    """Invalid datetime strings log and restore as None."""
    restored = CheckinExtraStoredData.from_dict({"tracked_event_start": "not-a-date"})

    assert restored.tracked_event_start is None
    assert "Failed to parse stored datetime" in caplog.text
