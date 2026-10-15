# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Coordinator sensor-code publication semantics."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
from types import SimpleNamespace

from custom_components.rental_control.coordinator import RentalControlCoordinator
from custom_components.rental_control.helpers import OperationResult
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


def _with_lock_pin(coordinator: RentalControlCoordinator, pin: str | None) -> None:
    """Attach a fake Keymaster PIN entity state to the coordinator."""
    coordinator.lockname = "front_door"
    state = None if pin is None else SimpleNamespace(state=pin)
    coordinator.hass = SimpleNamespace(
        states=SimpleNamespace(get=lambda _entity_id: state)
    )


def _apply_results(
    coordinator: RentalControlCoordinator,
    reservation: Reservation,
    results: list[OperationResult],
) -> None:
    """Replay physical operation results through the coordinator helper."""
    assert coordinator._latest_plan is not None
    coordinator._apply_observed_code_results(
        coordinator._latest_plan,
        {reservation.identity_key: reservation},
        results,
    )


def test_confirmed_replacement_publishes_new_code() -> None:
    """A confirmed date-shift replacement publishes the new PIN at once."""
    reservation = _reservation("identity-1", "2430")
    coordinator = _coordinator(reservation)
    _with_lock_pin(coordinator, "2430")
    _record_observed(coordinator, reservation, code="2530")
    assert coordinator.get_slot_code("identity-1") == "2530"

    _apply_results(
        coordinator,
        reservation,
        [
            OperationResult(kind="clear", slot=10, confirmed=True),
            OperationResult(kind="set", slot=10, confirmed=True),
        ],
    )

    assert coordinator.get_slot_code("identity-1") == "2430"


def test_unconfirmed_replacement_keeps_observed_code() -> None:
    """An unconfirmed write must not publish a code that may not exist."""
    reservation = _reservation("identity-1", "2430")
    coordinator = _coordinator(reservation)
    _with_lock_pin(coordinator, "2430")
    _record_observed(coordinator, reservation, code="2530")

    _apply_results(
        coordinator,
        reservation,
        [
            OperationResult(kind="clear", slot=10, confirmed=True),
            OperationResult(kind="set", slot=10, unconfirmed=True),
        ],
    )

    assert coordinator.get_slot_code("identity-1") is None


def test_confirmed_set_without_matching_pin_publishes_nothing() -> None:
    """A confirmed set whose PIN entity disagrees publishes no code.

    Set confirmation only verifies the slot name, so the requested PIN
    is not evidence that it reached the lock.
    """
    for lock_pin in (None, "", "9999"):
        reservation = _reservation("identity-1", "2430")
        coordinator = _coordinator(reservation)
        _with_lock_pin(coordinator, lock_pin)
        _record_observed(coordinator, reservation, code="2530")

        _apply_results(
            coordinator,
            reservation,
            [
                OperationResult(kind="clear", slot=10, confirmed=True),
                OperationResult(kind="set", slot=10, confirmed=True),
            ],
        )

        assert coordinator.get_slot_code("identity-1") is None
