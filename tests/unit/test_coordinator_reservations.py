# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Tests for the pure reservation-building coordinator helpers."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone

from custom_components.rental_control.const import SLOT_STATUS_OCCUPIED
from custom_components.rental_control.coordinator_helpers import reservations
from custom_components.rental_control.coordinator_helpers.models import (
    GhostReservationResult,
)
from custom_components.rental_control.coordinator_helpers.models import (
    ReservationBuildContext,
)


def _ctx() -> ReservationBuildContext:
    """Return a minimal reservation build context for tests."""
    return ReservationBuildContext(
        entry_id="entry-1",
        timezone=timezone.utc,
        event_prefix="RC",
        trim_names=False,
        max_name_length=0,
        code_buffer_before=0,
        code_buffer_after=0,
        should_update_code=False,
        code_generator="date",
        code_length=4,
        active_windows_for_name=lambda _name: set(),
    )


def test_build_reservations_empty_calendar() -> None:
    """An empty calendar yields no reservations."""
    assert reservations.build_reservations([], None, _ctx()) == []


def test_build_ghost_reservations_empty_persisted() -> None:
    """No persisted mappings yields an empty ghost result."""
    result = reservations.build_ghost_reservations(set(), {}, "RC ", None, _ctx())
    assert isinstance(result, GhostReservationResult)
    assert result.reservations == []


def test_build_ghost_reservations_fences_corrupt_mapping() -> None:
    """Corrupt persisted ghost fields are treated as empty values."""
    persisted: dict[str, dict[str, object]] = {
        "none-fields": {
            "status": SLOT_STATUS_OCCUPIED,
            "missing_count": None,
            "identity": None,
            "last_observed_actual": None,
        },
        "bad-fields": {
            "status": SLOT_STATUS_OCCUPIED,
            "missing_count": "x",
            "identity": "bad",
            "last_observed_actual": "bad",
        },
    }

    result = reservations.build_ghost_reservations(
        set(), persisted, "RC ", None, _ctx()
    )

    assert result.reservations == []
    assert persisted["none-fields"]["missing_count"] == 1
    assert persisted["bad-fields"]["missing_count"] == 1


def test_build_ghost_reservations_fences_corrupt_nested_values() -> None:
    """Corrupt nested ghost values are fenced instead of raising."""
    persisted: dict[str, dict[str, object]] = {
        "bad-nested": {
            "status": SLOT_STATUS_OCCUPIED,
            "missing_count": 0,
            "identity": {
                "slot_name": 12,
                "summary": object(),
                "uid_aliases": object(),
            },
            "last_observed_actual": {
                "name_state": "RC Alice",
                "start_state": 1,
                "end_state": 2,
            },
            "fingerprint_history": object(),
        }
    }

    result = reservations.build_ghost_reservations(
        set(), persisted, "RC ", {"bad-nested"}, _ctx()
    )

    assert result.reservations == []
    assert persisted["bad-nested"]["missing_count"] == 1


def test_build_ghost_reservations_handles_mixed_datetime_awareness() -> None:
    """Mixed naive and aware persisted ghost datetimes are fenced."""
    persisted: dict[str, dict[str, object]] = {
        "mixed-dates": {
            "status": SLOT_STATUS_OCCUPIED,
            "missing_count": 0,
            "identity": {"slot_name": "Alice", "summary": "Alice"},
            "last_observed_actual": {
                "name_state": "RC Alice",
                "start_state": datetime(2026, 1, 2),
                "end_state": datetime(2026, 1, 1, tzinfo=timezone.utc),
            },
        }
    }

    result = reservations.build_ghost_reservations(
        set(), persisted, "RC ", {"mixed-dates"}, _ctx()
    )

    assert result.reservations == []
    assert persisted["mixed-dates"]["missing_count"] == 1
