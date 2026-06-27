# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Focused tests for reconciliation actions and diagnostics."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone

from custom_components.rental_control.reconciliation import ActionKind
from custom_components.rental_control.reconciliation import ManagedSlot
from custom_components.rental_control.reconciliation import Reservation
from custom_components.rental_control.reconciliation import SlotStatus
from custom_components.rental_control.reconciliation import compute_desired_plan


def test_drift_diagnostics_are_redacted_and_structured() -> None:
    """Drift actions include fields without exposing raw slot codes."""
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    end = start.replace(day=2)
    reservation = Reservation(
        "id", start, end, start, end, "Guest", "Guest", "RC Guest", "9999"
    )
    slot = ManagedSlot(
        1,
        True,
        SlotStatus.OCCUPIED,
        "Wrong",
        actual_code="1234",
        actual_code_present=True,
        actual_start=start,
        actual_end=end,
        persisted_identity_key="id",
    )
    plan = compute_desired_plan([reservation], [slot], 1, "plan", start)
    assert plan.actions[0].kind is ActionKind.OVERWRITE_MANUAL_CHANGE
    assert "drift_fields" in plan.diagnostics["slots"][1]
    assert "9999" not in repr(plan.diagnostics)
