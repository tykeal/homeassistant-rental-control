# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Tests for the pure reservation-building coordinator helpers."""

from __future__ import annotations

from datetime import timezone

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
