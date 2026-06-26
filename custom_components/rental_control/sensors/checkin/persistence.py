# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""RestoreEntity persistence helpers for the check-in sensor."""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Any

from homeassistant.helpers.restore_state import ExtraStoredData
from homeassistant.util import dt as dt_util

from ...const import CHECKIN_STATE_NO_RESERVATION
from .models import CheckinStateSnapshot

_LOGGER = logging.getLogger(__name__)

_STORED_DT_FIELDS = (
    "tracked_event_start",
    "tracked_event_end",
    "checkout_time",
    "transition_target_time",
    "next_event_start_day",
)
_STORED_KEYS = (
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
)


def _parse_dt(value: str | None) -> datetime | None:
    """Parse an ISO 8601 string to datetime or return None."""
    if value is None:
        return None
    try:
        result: datetime | None = dt_util.parse_datetime(value)
    except TypeError:
        _LOGGER.warning("Failed to parse stored datetime: %s", value)
        return None
    except ValueError:
        _LOGGER.warning("Failed to parse stored datetime: %s", value)
        return None
    if result is None:
        _LOGGER.warning("Failed to parse stored datetime: %s", value)
    return result


class CheckinExtraStoredData(ExtraStoredData):
    """Extra stored data for persisting CheckinTrackingSensor state."""

    def __init__(
        self,
        snapshot: CheckinStateSnapshot | None = None,
        **legacy_fields: Any,
    ) -> None:
        """Initialize from a snapshot or legacy keyword fields."""
        if snapshot is None:
            snapshot = CheckinStateSnapshot(**legacy_fields)
        self.snapshot = snapshot

    def __getattr__(self, name: str) -> Any:
        """Delegate legacy field attribute access to the snapshot."""
        return getattr(self.snapshot, name)

    @classmethod
    def from_snapshot(cls, snapshot: CheckinStateSnapshot) -> CheckinExtraStoredData:
        """Build stored data from a check-in state snapshot."""
        return cls(snapshot=snapshot)

    def as_dict(self) -> dict[str, Any]:
        """Return the unchanged JSON-serialisable dict representation."""
        data = {key: getattr(self.snapshot, key) for key in _STORED_KEYS}
        for key in _STORED_DT_FIELDS:
            value = data[key]
            data[key] = value.isoformat() if value else None
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CheckinExtraStoredData:
        """Reconstruct an instance from the persisted dictionary shape."""
        fields: dict[str, Any] = {key: data.get(key) for key in _STORED_KEYS}
        fields["state"] = data.get("state", CHECKIN_STATE_NO_RESERVATION)
        for key in _STORED_DT_FIELDS:
            fields[key] = _parse_dt(data.get(key))
        return cls(snapshot=CheckinStateSnapshot(**fields))
