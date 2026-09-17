# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for one-cycle forced re-issue retention suppression."""

from __future__ import annotations

from datetime import date
from datetime import datetime
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from typing import cast

from homeassistant.util import dt as dt_util

from custom_components.rental_control.allocator.allocator import DoorCodeAllocator
from custom_components.rental_control.allocator.issuance import observed_alias_key
from custom_components.rental_control.allocator.models import AdoptionRequest
from custom_components.rental_control.allocator.models import AllocationOrigin
from custom_components.rental_control.allocator.models import AllocationOwner
from custom_components.rental_control.allocator.models import AllocationRecord
from custom_components.rental_control.allocator.reissue import forced_release_hold_key
from custom_components.rental_control.coordinator_helpers import code_allocation
from custom_components.rental_control.coordinator_helpers import reissue
from custom_components.rental_control.coordinator_helpers.checkin_protection import (
    build_protected_reservation,
)
from custom_components.rental_control.coordinator_helpers.models import (
    CheckinProtectionSnapshot,
)
from custom_components.rental_control.coordinator_helpers.models import (
    ReservationBuildContext,
)
from custom_components.rental_control.coordinator_helpers.reservations import (
    build_reservations,
)
from custom_components.rental_control.reconciliation import ActionKind
from custom_components.rental_control.reconciliation import ManagedSlot
from custom_components.rental_control.reconciliation import Reservation
from custom_components.rental_control.reconciliation import SlotStatus
from custom_components.rental_control.reconciliation import compute_desired_plan
from custom_components.rental_control.reconciliation import make_reservation_fingerprint

_START = datetime(2026, 9, 17, 16, tzinfo=dt_util.UTC)
_END = _START + timedelta(days=3)


def test_reservation_builder_suppresses_observed_code(
    monkeypatch: Any,
) -> None:
    """_resolve_observed_code keeps generated code only for the target."""
    monkeypatch.setattr(
        "custom_components.rental_control.coordinator_helpers.reservations."
        "generate_slot_code",
        lambda *_args: "1234",
    )
    target = _identity("Target Guest")
    calendar = [_event("Target Guest"), _event("Other Guest")]
    slots = [
        _slot(1, "9999", "RC Target Guest", identity=target),
        _slot(2, "8888", "RC Other Guest", identity=_identity("Other Guest")),
    ]

    suppressed = build_reservations(
        calendar,
        slots,
        _ctx(reissue.ReissueSuppression(frozenset({target}), frozenset())),
    )
    unsuppressed = build_reservations(calendar, slots, _ctx())

    by_name = {reservation.slot_name: reservation for reservation in suppressed}
    assert by_name["Target Guest"].slot_code == "1234"
    assert by_name["Target Guest"].code_source == "generated"
    assert by_name["Other Guest"].slot_code == "8888"
    assert by_name["Other Guest"].code_source == "manual_observed"
    assert {reservation.slot_code for reservation in unsuppressed} == {"8888", "9999"}
    assert {reservation.code_source for reservation in unsuppressed} == {
        "manual_observed"
    }


def test_checkin_protection_suppresses_observed_code() -> None:
    """Protected checked-in reservations can keep the generated code."""
    snapshot = CheckinProtectionSnapshot(
        "checked_in", "Target Guest", _START, _END, "Target Guest", {}
    )
    slot = _slot(1, "9999", "RC Target Guest")

    retained = build_protected_reservation(
        snapshot,
        slot,
        1,
        (_START, _END),
        ("identity-a", "9999", False),
        "RC Target Guest",
    )
    suppressed = build_protected_reservation(
        snapshot,
        slot,
        1,
        (_START, _END),
        ("identity-a", "1234", True),
        "RC Target Guest",
    )

    assert retained is not None
    assert retained.slot_code == "9999"
    assert retained.code_source == "manual_observed"
    assert suppressed is not None
    assert suppressed.slot_code == "1234"
    assert suppressed.code_source == "generated"


def test_adoption_requests_skip_only_suppressed_target() -> None:
    """Suppression skips the target's adoption and leaves peers unchanged."""
    first = _reservation("identity-a", "Target Guest", "1234")
    second = _reservation("identity-b", "Other Guest", "5678")
    requests = code_allocation.build_adoption_requests(
        "entry-a",
        "front",
        4,
        [
            _slot(1, "9999", "RC Target Guest", identity="identity-a"),
            _slot(2, "8888", "RC Other Guest", identity="identity-b"),
        ],
        [first, second],
        reissue.ReissueSuppression(frozenset({"identity-a"}), frozenset()),
    )

    assert [request.identity_key for request in requests] == ["identity-b"]
    assert requests[0].code == "8888"


async def test_adopt_unlocked_suppresses_reissue_pending_mismatch() -> None:
    """identity_code_mismatch preserves the new code while a hold exists."""
    allocator = _allocator()
    hold = forced_release_hold_key("identity-a", "entry-a", "front", 1)
    allocator._registry.records["1234"] = _record("1234", _owner("entry-a", hold))
    allocator._registry.records["5678"] = _record(
        "5678", _owner("entry-a", "identity-a")
    )
    allocator._registry.rebuild_index()
    request = AdoptionRequest("entry-a", "identity-a", "1234", 4, "front", 1)

    result = await allocator.async_adopt(request)

    assert result.code == "5678"
    assert result.reason == "reissue_pending"
    assert allocator._registry.code_for_identity(observed_alias_key(request)) is None


def test_pending_state_is_in_memory_and_next_cycle_only() -> None:
    """Suppression is consumed once and disappears across restarts."""
    coordinator = SimpleNamespace(_entry_id="entry-a", lockname="front")
    pending = reissue.PendingReissue(
        target_key="identity-a",
        identity_key="identity-a",
        lockname="front",
        slot=1,
    )
    reissue.pending_reissues(coordinator)["identity-a"] = pending

    first = reissue.current_suppression(coordinator)
    reissue.consume_cycle_result(
        coordinator,
        reissue.CodeResolutionResult(
            observation=None,
            reissues=(),
            consumed_target_keys=("identity-a",),
        ),
    )
    second = reissue.current_suppression(coordinator)
    restarted = reissue.current_suppression(SimpleNamespace())

    assert first.identity_keys == frozenset({"identity-a"})
    assert second == reissue.ReissueSuppression()
    assert restarted == reissue.ReissueSuppression()


def test_slot_suppression_does_not_affect_live_reservation(
    monkeypatch: Any,
) -> None:
    """Slot-only suppression is not inherited by a later reservation."""
    monkeypatch.setattr(
        "custom_components.rental_control.coordinator_helpers.reservations."
        "generate_slot_code",
        lambda *_args: "1234",
    )
    reservations = build_reservations(
        [_event("Target Guest")],
        [_slot(1, "9999", "RC Target Guest")],
        _ctx(reissue.ReissueSuppression(frozenset(), frozenset({1}))),
    )

    assert reservations[0].slot_code == "9999"
    assert reservations[0].code_source == "manual_observed"


def test_slot_suppression_skips_protected_adoption_only() -> None:
    """Slot targets skip protected adoption without affecting live bookings."""
    live = _reservation("identity-a", "Target Guest", "1234")
    protected = _reservation("identity-b", "Target Guest", "1234")
    protected.protected_active = True

    live_requests = code_allocation.build_adoption_requests(
        "entry-a",
        "front",
        4,
        [_slot(1, "9999", "RC Target Guest", identity="identity-a")],
        [live],
        reissue.ReissueSuppression(frozenset(), frozenset({1})),
    )
    protected_requests = code_allocation.build_adoption_requests(
        "entry-a",
        "front",
        4,
        [_slot(1, "9999", "RC Target Guest", identity="identity-b")],
        [protected],
        reissue.ReissueSuppression(frozenset(), frozenset({1})),
    )

    assert [request.identity_key for request in live_requests] == ["identity-a"]
    assert protected_requests == []


def test_suppressed_target_consumes_slot_before_peer_fallback() -> None:
    """A peer cannot adopt the suppressed target's old physical code."""
    first = _reservation("identity-a", "Same Guest", "1234")
    second = _reservation("identity-b", "Same Guest", "5678")
    requests = code_allocation.build_adoption_requests(
        "entry-a",
        "front",
        4,
        [
            _slot(1, "1111", "RC Same Guest", identity="identity-a"),
            _slot(2, "2222", "RC Same Guest", identity="identity-b"),
        ],
        [first, second],
        reissue.ReissueSuppression(frozenset({"identity-a"}), frozenset()),
    )

    assert [request.identity_key for request in requests] == ["identity-b"]
    assert requests[0].code == "2222"


async def test_reissue_pending_cycle_reemits_overwrite(
    monkeypatch: Any,
) -> None:
    """Cycle N+1 keeps the new code and retries overwrite of the old code."""
    allocator = _allocator()
    monkeypatch.setattr(code_allocation, "get_allocator", lambda _hass: allocator)
    hold = forced_release_hold_key("identity-a", "entry-a", "front", 1)
    allocator._registry.records["1234"] = _record("1234", _owner("entry-a", hold))
    allocator._registry.records["5678"] = _record(
        "5678", _owner("entry-a", "identity-a")
    )
    allocator._registry.rebuild_index()
    reservation = _reservation("identity-a", "Target Guest", "5678")
    old_physical = _slot(1, "1234", "RC Target Guest", identity="identity-a")

    await code_allocation.async_resolve_codes(
        code_allocation.CodeResolutionRequest(
            hass=SimpleNamespace(),
            entry_id="entry-a",
            lockname="front",
            code_length=4,
            managed_slots=[old_physical],
            reservations=[reservation],
        )
    )
    plan = compute_desired_plan(
        reservations=[reservation],
        managed_slots=[old_physical],
        max_events=1,
        plan_id="plan",
        generated_at=_START,
        entry_id="entry-a",
        lockname="front",
        start_slot=1,
    )

    assert reservation.slot_code == "5678"
    assert [action.kind for action in plan.actions] == [
        ActionKind.OVERWRITE_MANUAL_CHANGE
    ]
    assert plan.actions[0].slot == 1


def test_all_day_suppression_uses_coerced_identity(monkeypatch: Any) -> None:
    """Suppression identity matching supports all-day date calendar values."""
    monkeypatch.setattr(
        "custom_components.rental_control.coordinator_helpers.reservations."
        "generate_slot_code",
        lambda *_args: "1234",
    )
    event = SimpleNamespace(
        summary="Target Guest",
        description="",
        start=date(2026, 9, 17),
        end=date(2026, 9, 20),
        uid=None,
    )
    start = datetime(2026, 9, 17, tzinfo=dt_util.UTC)
    end = datetime(2026, 9, 20, tzinfo=dt_util.UTC)
    identity = make_reservation_fingerprint("entry-a", "Target Guest", start, end)

    reservations = build_reservations(
        [event],
        [_slot(1, "9999", "RC Target Guest")],
        _ctx(reissue.ReissueSuppression(frozenset({identity}), frozenset())),
    )

    assert reservations[0].slot_code == "1234"


def _ctx(
    suppression: reissue.ReissueSuppression | None = None,
) -> ReservationBuildContext:
    """Build a reservation context for suppression tests."""
    return ReservationBuildContext(
        entry_id="entry-a",
        timezone=dt_util.UTC,
        event_prefix="RC",
        trim_names=False,
        max_name_length=20,
        code_buffer_before=0,
        code_buffer_after=0,
        should_update_code=True,
        code_generator="date_based",
        code_length=4,
        active_windows_for_name=lambda _slot_name: set(),
        reissue_suppression=suppression or reissue.ReissueSuppression(),
    )


def _event(name: str) -> SimpleNamespace:
    """Build a calendar-like event for reservation tests."""
    return SimpleNamespace(
        summary=name,
        description="",
        start=_START,
        end=_END,
        uid=None,
    )


def _identity(name: str) -> str:
    """Return the reservation fingerprint for a test guest."""
    return make_reservation_fingerprint("entry-a", name, _START, _END)


def _reservation(identity_key: str, name: str, code: str) -> Reservation:
    """Build a reservation for allocator suppression tests."""
    return Reservation(
        identity_key=identity_key,
        start=_START,
        end=_END,
        buffered_start=_START,
        buffered_end=_END,
        summary=name,
        slot_name=name,
        display_slot_name=f"RC {name}",
        slot_code=code,
    )


def _slot(
    slot: int,
    code: str,
    name: str,
    *,
    identity: str | None = None,
) -> ManagedSlot:
    """Build one occupied managed slot."""
    return ManagedSlot(
        slot=slot,
        managed=True,
        status=SlotStatus.OCCUPIED,
        actual_name=name,
        actual_code=code,
        actual_code_present=True,
        actual_start=_START,
        actual_end=_END,
        persisted_identity_key=identity,
    )


def _owner(entry_id: str, identity_key: str) -> AllocationOwner:
    """Build one allocation owner."""
    return AllocationOwner(
        entry_id=entry_id,
        identity_key=identity_key,
        origin=AllocationOrigin.PREFERRED,
        lockname="front",
        slot=1,
        lock_observed=True,
        first_seen=_START.isoformat(),
        last_seen=_START.isoformat(),
    )


def _record(code: str, owner: AllocationOwner) -> AllocationRecord:
    """Build one allocation record."""
    return AllocationRecord(
        code=code,
        code_ref=f"ref-{code}",
        encoding_salt_value="entry-a",
        code_length=4,
        owners=[owner],
        created_at=_START.isoformat(),
        updated_at=_START.isoformat(),
    )


def _allocator() -> DoorCodeAllocator:
    """Build an allocator with persistence disabled."""
    allocator = DoorCodeAllocator(
        SimpleNamespace(
            data={},
            config=SimpleNamespace(config_dir="."),
            config_entries=SimpleNamespace(async_entries=lambda _domain=None: []),
        )
    )
    allocator._store = cast(Any, SimpleNamespace(async_save=lambda _registry: None))
    return allocator
