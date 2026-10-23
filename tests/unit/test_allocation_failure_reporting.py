# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Operator reporting for lockless allocation failures (issue #848)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

from homeassistant.util import dt as dt_util
import pytest

from custom_components.rental_control.coordinator_helpers import allocation_failure
from custom_components.rental_control.coordinator_helpers import code_allocation
from custom_components.rental_control.coordinator_helpers.coordinator_refresh_shell import (  # noqa: E501
    CoordinatorRefreshMixin,
)
from custom_components.rental_control.reconciliation import ManagedSlot
from custom_components.rental_control.reconciliation import Reservation

_START = datetime(2026, 9, 17, 16, tzinfo=dt_util.UTC)
_END = _START + timedelta(days=3)


class _Notifications:
    """Record persistent notifications raised and dismissed."""

    def __init__(self) -> None:
        """Initialize empty notification logs."""
        self.created: list[tuple[str, str]] = []
        self.dismissed: list[str] = []

    def create(self, _hass: Any, message: str, **kwargs: Any) -> None:
        """Record one created notification."""
        self.created.append((kwargs["notification_id"], message))

    def dismiss(self, _hass: Any, notification_id: str) -> None:
        """Record one dismissed notification."""
        self.dismissed.append(notification_id)


class _LocklessCoordinator(CoordinatorRefreshMixin):
    """Coordinator stand-in whose lockless allocation can be made to fail."""

    hass = SimpleNamespace(bus=SimpleNamespace())
    _entry_id = "entry-a"
    code_length = 4
    _name = "Entry A"
    _latest_plan = None
    event_overrides: Any = None

    def __init__(self) -> None:
        """Initialize a single reservation and empty published state."""
        self.reservation = Reservation(
            identity_key="identity-a",
            start=_START,
            end=_END,
            buffered_start=_START,
            buffered_end=_END,
            summary="Reserved: Test Guest",
            slot_name="Test Guest",
            display_slot_name="RC Test Guest",
            slot_code="1111",
        )
        self._latest_res_by_key: dict[str, Reservation] = {}

    def _prepare_reservations_for_adoption(
        self,
        _new_calendar: list[Any],
        _managed_slots: list[ManagedSlot],
    ) -> list[Reservation]:
        """Return the test reservation."""
        return [self.reservation]

    def _apply_checkin_protection(
        self,
        reservations: list[Reservation],
        managed_slots: list[ManagedSlot] | None = None,
    ) -> None:
        """Leave the reservation untouched."""
        del reservations, managed_slots

    def _sync_lockless_slot_store(
        self,
        res_by_key: dict[str, Reservation],
    ) -> None:
        """Ignore store persistence for these tests."""
        del res_by_key


@pytest.fixture(autouse=True)
def _notifications(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Notifications]:
    """Capture notifications raised while a test runs.

    Resetting the module's reporting state is handled globally by the
    autouse fixture in ``tests/conftest.py``.
    """
    recorder = _Notifications()
    monkeypatch.setattr(allocation_failure, "async_create", recorder.create)
    monkeypatch.setattr(allocation_failure, "async_dismiss", recorder.dismiss)
    yield recorder


def _failing(message: str = "boom") -> Any:
    """Return an ``async_resolve_codes`` replacement that always raises."""

    async def _resolve(_request: Any) -> Any:
        """Raise the configured failure."""
        raise RuntimeError(message)

    return _resolve


async def _succeeding(request: Any) -> Any:
    """Return an empty successful cycle result."""
    from custom_components.rental_control.allocator.models import CycleObservation

    return code_allocation.CodeResolutionResult(
        CycleObservation(
            entry_id=request.entry_id,
            lockname=None,
            managed_slots=frozenset(),
            observed_codes={},
            unreadable_slots=frozenset(),
        )
    )


async def test_failure_raises_one_notification_per_condition(
    monkeypatch: pytest.MonkeyPatch, _notifications: _Notifications
) -> None:
    """A repeating failure announces once rather than once per cycle."""
    coordinator = _LocklessCoordinator()
    monkeypatch.setattr(code_allocation, "async_resolve_codes", _failing())

    for _ in range(5):
        await coordinator._run_lockless_allocation([])

    assert len(_notifications.created) == 1
    notification_id, message = _notifications.created[0]
    assert notification_id == allocation_failure.notification_id("entry-a")
    assert "Entry A" in message
    assert "RuntimeError: boom" in message


async def test_a_changed_failure_announces_again(
    monkeypatch: pytest.MonkeyPatch, _notifications: _Notifications
) -> None:
    """A different failure condition is reported as its own notification."""
    coordinator = _LocklessCoordinator()
    monkeypatch.setattr(code_allocation, "async_resolve_codes", _failing("first"))
    await coordinator._run_lockless_allocation([])
    monkeypatch.setattr(code_allocation, "async_resolve_codes", _failing("second"))
    await coordinator._run_lockless_allocation([])

    assert len(_notifications.created) == 2
    assert "first" in _notifications.created[0][1]
    assert "second" in _notifications.created[1][1]


async def test_a_long_run_of_failures_escalates(
    monkeypatch: pytest.MonkeyPatch, _notifications: _Notifications
) -> None:
    """An unresolved failure is re-announced with its running count."""
    coordinator = _LocklessCoordinator()
    monkeypatch.setattr(code_allocation, "async_resolve_codes", _failing())

    for _ in range(10):
        await coordinator._run_lockless_allocation([])

    assert len(_notifications.created) == 2
    assert "on 10 consecutive refresh cycles" in _notifications.created[1][1]


async def test_recovery_dismisses_the_notification(
    monkeypatch: pytest.MonkeyPatch, _notifications: _Notifications
) -> None:
    """A successful cycle clears the failure report and the notification."""
    coordinator = _LocklessCoordinator()
    monkeypatch.setattr(code_allocation, "async_resolve_codes", _failing())
    await coordinator._run_lockless_allocation([])
    assert allocation_failure.diagnostics("entry-a") is not None

    monkeypatch.setattr(code_allocation, "async_resolve_codes", _succeeding)
    await coordinator._run_lockless_allocation([])

    assert allocation_failure.notification_id("entry-a") in _notifications.dismissed
    assert allocation_failure.diagnostics("entry-a") is None


async def test_a_successful_cycle_alone_raises_nothing(
    monkeypatch: pytest.MonkeyPatch, _notifications: _Notifications
) -> None:
    """A healthy entry never touches the failure notification."""
    coordinator = _LocklessCoordinator()
    monkeypatch.setattr(code_allocation, "async_resolve_codes", _succeeding)

    await coordinator._run_lockless_allocation([])

    assert _notifications.created == []
    assert _notifications.dismissed == []


async def test_failure_state_reaches_diagnostics(
    monkeypatch: pytest.MonkeyPatch, _notifications: _Notifications
) -> None:
    """Diagnostics carry the failure run so a report can explain itself."""
    coordinator = _LocklessCoordinator()
    monkeypatch.setattr(code_allocation, "async_resolve_codes", _failing())

    await coordinator._run_lockless_allocation([])
    await coordinator._run_lockless_allocation([])

    assert allocation_failure.diagnostics("entry-a") == {
        "consecutive_failures": 2,
        "last_error": "RuntimeError: boom",
    }


async def test_unload_forgets_and_dismisses(_notifications: _Notifications) -> None:
    """Entry teardown drops the state and clears the notification."""
    allocation_failure.report(
        SimpleNamespace(bus=SimpleNamespace()),
        "entry-a",
        "Entry A",
        RuntimeError("boom"),
    )

    allocation_failure.forget_entry(SimpleNamespace(bus=SimpleNamespace()), "entry-a")

    assert allocation_failure.diagnostics("entry-a") is None
    assert allocation_failure.notification_id("entry-a") in _notifications.dismissed


async def test_a_changed_failure_keeps_the_streak_running(
    monkeypatch: pytest.MonkeyPatch, _notifications: _Notifications
) -> None:
    """Only a successful cycle resets the consecutive-failure count.

    A different exception part way through an unbroken run is a changed
    condition, not a fresh run. Restarting the count there would hide
    how long the entry has been failing and would delay every
    escalation threshold.
    """
    coordinator = _LocklessCoordinator()
    monkeypatch.setattr(code_allocation, "async_resolve_codes", _failing("first"))
    for _ in range(9):
        await coordinator._run_lockless_allocation([])

    monkeypatch.setattr(code_allocation, "async_resolve_codes", _failing("second"))
    await coordinator._run_lockless_allocation([])

    assert allocation_failure.diagnostics("entry-a") == {
        "consecutive_failures": 10,
        "last_error": "RuntimeError: second",
    }
    assert "on 10 consecutive refresh cycles" in _notifications.created[-1][1]
    assert "second" in _notifications.created[-1][1]


async def test_gaining_a_lock_clears_a_lockless_failure(
    monkeypatch: pytest.MonkeyPatch, _notifications: _Notifications
) -> None:
    """Switching an entry to lock-backed mode clears its failure report.

    An options update that adds a lock reconfigures the coordinator in
    place, so later refreshes run reconciliation and never reach the
    lockless ``resolve`` call.  Without clearing it here, a stale
    notification would outlive the condition it describes forever.
    """
    coordinator = _LocklessCoordinator()
    monkeypatch.setattr(code_allocation, "async_resolve_codes", _failing())
    await coordinator._run_lockless_allocation([])
    assert allocation_failure.has_report("entry-a") is True

    await coordinator._run_reconciliation([])

    assert allocation_failure.has_report("entry-a") is False
    assert allocation_failure.diagnostics("entry-a") is None
    assert allocation_failure.notification_id("entry-a") in _notifications.dismissed
