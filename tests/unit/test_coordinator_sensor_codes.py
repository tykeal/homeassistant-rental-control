# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Coordinator sensor-code publication semantics."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone

from custom_components.rental_control.coordinator import RentalControlCoordinator
from custom_components.rental_control.reconciliation import DesiredPlan
from custom_components.rental_control.reconciliation import ManagedSlot
from custom_components.rental_control.reconciliation import Reservation
from custom_components.rental_control.reconciliation import SlotStatus

START = datetime(2026, 9, 17, 16, tzinfo=timezone.utc)
END = datetime(2026, 9, 20, 11, tzinfo=timezone.utc)


def _reservation(identity_key: str, slot_code: str | None) -> Reservation:
    """Build a minimal reservation for coordinator publication tests."""
    return Reservation(
        identity_key=identity_key,
        start=START,
        end=END,
        buffered_start=START,
        buffered_end=END,
        summary="Reserved - Jane Doe",
        slot_name="Jane Doe",
        display_slot_name="RC Jane Doe",
        slot_code=slot_code,
    )


def _coordinator(
    reservation: Reservation,
    *,
    slot: int = 10,
    event_overrides: object | None = object(),
) -> RentalControlCoordinator:
    """Build a coordinator shell with only publication state populated."""
    coordinator = object.__new__(RentalControlCoordinator)
    coordinator.event_overrides = event_overrides
    coordinator._latest_res_by_key = {reservation.identity_key: reservation}
    coordinator._observed_slot_codes = {}
    coordinator._latest_plan = None
    if event_overrides is not None:
        plan = DesiredPlan(plan_id="sensor-parity", generated_at=START)
        plan.selected[reservation.identity_key] = slot
        coordinator._latest_plan = plan
    return coordinator


def _record_observed(
    coordinator: RentalControlCoordinator,
    reservation: Reservation,
    *,
    slot: int = 10,
    code: str | None,
    status: SlotStatus = SlotStatus.OCCUPIED,
) -> None:
    """Record one physical slot observation through the coordinator helper."""
    assert coordinator._latest_plan is not None
    coordinator._record_observed_slot_codes(
        coordinator._latest_plan,
        {reservation.identity_key: reservation},
        [ManagedSlot(slot=slot, managed=True, status=status, actual_code=code)],
    )


def test_lock_backed_hides_unconfirmed_allocated_code() -> None:
    """Lock-backed sensors do not publish allocator codes before observation."""
    reservation = _reservation("identity-1", "2468")
    coordinator = _coordinator(reservation)

    _record_observed(coordinator, reservation, code=None, status=SlotStatus.FREE)

    assert coordinator.get_slot_code("identity-1") is None


def test_lock_backed_publishes_after_physical_confirmation() -> None:
    """Lock-backed sensors publish allocator codes after matching observation."""
    reservation = _reservation("identity-1", "2468")
    coordinator = _coordinator(reservation)

    _record_observed(coordinator, reservation, code="2468")

    assert coordinator.get_slot_code("identity-1") == "2468"


def test_lock_backed_retains_observed_code_while_unconfirmed() -> None:
    """A previously observed code stays visible while a new write is pending."""
    reservation = _reservation("identity-1", "1111")
    coordinator = _coordinator(reservation)
    _record_observed(coordinator, reservation, code="1111")
    reservation.slot_code = "2222"

    _record_observed(coordinator, reservation, code=None, status=SlotStatus.UNKNOWN)

    assert coordinator.get_slot_code("identity-1") == "1111"


def test_lock_backed_clears_code_after_empty_observation() -> None:
    """A readable empty slot clears the previously observed sensor code."""
    reservation = _reservation("identity-1", "1111")
    coordinator = _coordinator(reservation)
    _record_observed(coordinator, reservation, code="1111")

    _record_observed(coordinator, reservation, code=None, status=SlotStatus.FREE)

    assert coordinator.get_slot_code("identity-1") is None


def test_lock_backed_ignores_observed_code_from_old_slot() -> None:
    """A slot reassignment must not publish a code observed on the old slot."""
    reservation = _reservation("identity-1", "1111")
    coordinator = _coordinator(reservation, slot=10)
    _record_observed(coordinator, reservation, slot=10, code="1111")
    assert coordinator._latest_plan is not None
    coordinator._latest_plan.selected[reservation.identity_key] = 11

    _record_observed(
        coordinator,
        reservation,
        slot=11,
        code=None,
        status=SlotStatus.FREE,
    )

    assert coordinator.get_slot_code("identity-1") is None


def test_lock_backed_none_when_no_safe_code_was_confirmed() -> None:
    """Lock-backed sensors publish None when no physical code is safe."""
    reservation = _reservation("identity-1", None)
    coordinator = _coordinator(reservation)

    assert coordinator.get_slot_code("identity-1") is None


def test_lockless_entry_publishes_allocated_code_immediately() -> None:
    """Lockless sensors expose allocated codes without physical confirmation."""
    reservation = _reservation("identity-1", "1357")
    coordinator = _coordinator(reservation, event_overrides=None)

    assert coordinator.get_slot_code("identity-1") == "1357"
