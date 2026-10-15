# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Tests for the pure reservation-building coordinator helpers."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone

from custom_components.rental_control.const import OPERATION_KIND_CLEAR
from custom_components.rental_control.const import SLOT_STATUS_OCCUPIED
from custom_components.rental_control.const import SLOT_STATUS_PENDING_CLEAR
from custom_components.rental_control.const import SLOT_STATUS_PENDING_SET
from custom_components.rental_control.coordinator_helpers import reservations
from custom_components.rental_control.coordinator_helpers.coordinator_refresh_shell import (
    CoordinatorRefreshMixin,
)
from custom_components.rental_control.coordinator_helpers.models import (
    GhostReservationResult,
)
from custom_components.rental_control.coordinator_helpers.models import (
    ReservationBuildContext,
)
from custom_components.rental_control.reconciliation import ActionKind
from custom_components.rental_control.reconciliation import ManagedSlot
from custom_components.rental_control.reconciliation import Reservation
from custom_components.rental_control.reconciliation import SlotStatus
from custom_components.rental_control.reconciliation import compute_desired_plan


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


def test_pending_set_ghost_flips_to_pending_clear_on_third_miss() -> None:
    """A vanished pending-set mapping becomes pending-clear on the third miss."""
    persisted: dict[str, dict[str, object]] = {
        "pending-key": {
            "status": SLOT_STATUS_PENDING_SET,
            "missing_count": 2,
            "operation_id": "set-token",
            "operation_kind": "set",
            "pending_set_since": "2026-08-01T00:00:00+00:00",
            "pending_clear_since": None,
            "identity": {"slot_name": "Alice", "summary": "Alice"},
        }
    }

    result = reservations.build_ghost_reservations(
        set(), persisted, "RC ", None, _ctx()
    )

    mapping = persisted["pending-key"]
    assert result.reservations == []
    assert mapping["missing_count"] == 3
    assert mapping["status"] == SLOT_STATUS_PENDING_CLEAR
    assert mapping["operation_id"] is None
    assert mapping["operation_kind"] == OPERATION_KIND_CLEAR
    assert mapping["pending_set_since"] is None
    pending_clear_since = mapping["pending_clear_since"]
    assert isinstance(pending_clear_since, str)
    assert datetime.fromisoformat(pending_clear_since)


def test_hydration_resets_missing_count_on_reappearance() -> None:
    """A reservation back in the feed is reset to zero misses and retained."""
    start = datetime(2026, 8, 15, 16, 0, tzinfo=timezone.utc)
    end = datetime(2026, 8, 22, 11, 0, tzinfo=timezone.utc)
    reservation = Reservation(
        identity_key="reappear-key",
        start=start,
        end=end,
        buffered_start=start,
        buffered_end=end,
        summary="Reappear Guest",
        slot_name="Reappear Guest",
        display_slot_name="RC Reappear Guest",
        slot_code="1234",
        missing_count=3,
    )
    persisted: dict[str, object] = {
        "reappear-key": {
            "status": SLOT_STATUS_OCCUPIED,
            "missing_count": 3,
        }
    }

    CoordinatorRefreshMixin._hydrate_reservations_from_mappings(
        [reservation], persisted
    )

    assert reservation.missing_count == 0

    plan = compute_desired_plan(
        [reservation],
        [
            ManagedSlot(
                slot=10,
                managed=True,
                status=SlotStatus.OCCUPIED,
                actual_name="RC Reappear Guest",
                actual_code_present=True,
                persisted_identity_key="reappear-key",
                actual_start=start,
                actual_end=end,
            )
        ],
        max_events=1,
        plan_id="reappear",
        generated_at=start,
    )

    assert plan.selected["reappear-key"] == 10
    assert not [
        action
        for action in plan.actions
        if action.kind is ActionKind.CLEAR and action.slot == 10
    ]


def test_ghost_reservation_code_provenance_is_unknown() -> None:
    """A reconstructed ghost does not claim its code was generated.

    Ghosts carry no code, so a later allocator adoption of whatever PIN is
    still physically programmed must not be reported as generated.
    """
    persisted: dict[str, dict[str, object]] = {
        "ghost-key": {
            "status": SLOT_STATUS_OCCUPIED,
            "missing_count": 0,
            "identity": {"slot_name": "Alice", "summary": "Alice"},
            "last_observed_actual": {
                "name_state": "RC Alice",
                "start_state": "2026-08-15T16:00:00+00:00",
                "end_state": "2026-08-22T11:00:00+00:00",
            },
        }
    }

    result = reservations.build_ghost_reservations(
        set(), persisted, "RC ", None, _ctx()
    )

    assert len(result.reservations) == 1
    ghost = result.reservations[0]
    assert ghost.slot_code is None
    assert ghost.code_provenance == "unknown"
