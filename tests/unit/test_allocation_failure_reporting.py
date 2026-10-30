# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Operator reporting for refresh-cycle failures (issues #848, #852)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

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


class _LockedCoordinator(_LocklessCoordinator):
    """Coordinator stand-in whose reconciliation can be made to fail."""

    lockname = "front_door"
    max_events = 3
    start_slot = 10

    def __init__(self) -> None:
        """Attach a lock whose plan application always succeeds."""
        super().__init__()
        self.event_overrides = SimpleNamespace(
            async_apply_plan=AsyncMock(return_value=[])
        )

    def _observe_managed_slots(self) -> list[ManagedSlot]:
        """Report no managed slots."""
        return []

    def _record_observed_slot_codes(self, *_args: Any) -> None:
        """Ignore observed-code bookkeeping."""

    def _must_defer_for_checkin_restore(self, *_args: Any) -> bool:
        """Never defer the cycle."""
        return False

    def _apply_observed_code_results(self, *_args: Any) -> None:
        """Ignore operation results."""

    def _sync_slot_store_from_plan(self, *_args: Any) -> None:
        """Ignore store persistence."""


_RECONCILIATION = allocation_failure.RECONCILIATION


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


async def test_reconciliation_failure_raises_one_notification(
    monkeypatch: pytest.MonkeyPatch, _notifications: _Notifications
) -> None:
    """A repeating reconciliation failure announces once, as its own kind."""
    coordinator = _LockedCoordinator()
    monkeypatch.setattr(code_allocation, "async_resolve_codes", _failing())

    for _ in range(5):
        await coordinator._run_reconciliation([])

    assert len(_notifications.created) == 1
    notification_id, message = _notifications.created[0]
    assert notification_id == allocation_failure.notification_id(
        "entry-a", _RECONCILIATION
    )
    assert notification_id != allocation_failure.notification_id("entry-a")
    assert "could not reconcile door codes for Entry A" in message
    assert "RuntimeError: boom" in message
    assert allocation_failure.diagnostics("entry-a", _RECONCILIATION) == {
        "consecutive_failures": 5,
        "last_error": "RuntimeError: boom",
    }
    assert allocation_failure.diagnostics("entry-a") is None


async def test_reconciliation_recovery_dismisses_the_notification(
    monkeypatch: pytest.MonkeyPatch, _notifications: _Notifications
) -> None:
    """A successful reconciliation clears its failure report."""
    coordinator = _LockedCoordinator()
    monkeypatch.setattr(code_allocation, "async_resolve_codes", _failing())
    await coordinator._run_reconciliation([])
    assert allocation_failure.has_report("entry-a", _RECONCILIATION) is True

    monkeypatch.setattr(code_allocation, "async_resolve_codes", _succeeding)
    await coordinator._run_reconciliation([])

    assert allocation_failure.has_report("entry-a", _RECONCILIATION) is False
    assert (
        allocation_failure.notification_id("entry-a", _RECONCILIATION)
        in _notifications.dismissed
    )


async def test_a_successful_reconciliation_alone_raises_nothing(
    monkeypatch: pytest.MonkeyPatch, _notifications: _Notifications
) -> None:
    """A healthy lock-backed entry never touches either notification."""
    coordinator = _LockedCoordinator()
    monkeypatch.setattr(code_allocation, "async_resolve_codes", _succeeding)

    await coordinator._run_reconciliation([])

    assert _notifications.created == []
    assert _notifications.dismissed == []


async def test_failure_kinds_do_not_overwrite_each_other(
    _notifications: _Notifications,
) -> None:
    """Each kind keeps its own run, and resolving one leaves the other."""
    hass = SimpleNamespace(bus=SimpleNamespace())
    allocation_failure.report(hass, "entry-a", "Entry A", RuntimeError("lockless"))
    allocation_failure.report(
        hass, "entry-a", "Entry A", RuntimeError("reconcile"), _RECONCILIATION
    )

    allocation_failure.resolve(hass, "entry-a", "Entry A")

    assert allocation_failure.diagnostics("entry-a") is None
    assert allocation_failure.diagnostics("entry-a", _RECONCILIATION) == {
        "consecutive_failures": 1,
        "last_error": "RuntimeError: reconcile",
    }
    assert _notifications.dismissed == [allocation_failure.notification_id("entry-a")]


async def test_losing_a_lock_clears_a_reconciliation_failure(
    monkeypatch: pytest.MonkeyPatch, _notifications: _Notifications
) -> None:
    """Switching an entry to lockless mode clears its reconciliation report."""
    coordinator = _LockedCoordinator()
    monkeypatch.setattr(code_allocation, "async_resolve_codes", _failing())
    await coordinator._run_reconciliation([])
    assert allocation_failure.has_report("entry-a", _RECONCILIATION) is True

    await coordinator._run_lockless_allocation([])

    assert allocation_failure.has_report("entry-a", _RECONCILIATION) is False
    assert (
        allocation_failure.notification_id("entry-a", _RECONCILIATION)
        in _notifications.dismissed
    )


async def test_unload_forgets_every_failure_kind(
    _notifications: _Notifications,
) -> None:
    """Entry teardown drops and dismisses both kinds of report."""
    hass = SimpleNamespace(bus=SimpleNamespace())
    allocation_failure.report(hass, "entry-a", "Entry A", RuntimeError("boom"))
    allocation_failure.report(
        hass, "entry-a", "Entry A", RuntimeError("boom"), _RECONCILIATION
    )

    allocation_failure.forget_entry(hass, "entry-a")

    assert allocation_failure.diagnostics("entry-a") is None
    assert allocation_failure.diagnostics("entry-a", _RECONCILIATION) is None
    assert set(_notifications.dismissed) == {
        allocation_failure.notification_id("entry-a"),
        allocation_failure.notification_id("entry-a", _RECONCILIATION),
    }
