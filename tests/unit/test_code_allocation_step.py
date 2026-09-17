# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the coordinator allocator adoption step."""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from typing import cast

from homeassistant.util import dt as dt_util
import pytest

from custom_components.rental_control.allocator import allocator as allocator_module
from custom_components.rental_control.allocator.allocator import DoorCodeAllocator
from custom_components.rental_control.allocator.models import AdoptionRequest
from custom_components.rental_control.allocator.models import AllocationOrigin
from custom_components.rental_control.allocator.models import AllocationRequest
from custom_components.rental_control.allocator.models import CycleObservation
from custom_components.rental_control.allocator.models import CycleRequest
from custom_components.rental_control.coordinator_helpers import code_allocation
from custom_components.rental_control.reconciliation import ManagedSlot
from custom_components.rental_control.reconciliation import Reservation
from custom_components.rental_control.reconciliation import SlotStatus


class FakeAllocator:
    """Allocator test double that records cycle ordering."""

    def __init__(self) -> None:
        """Initialize recorded calls."""
        self.calls: list[str] = []

    async def async_resolve_cycle(self, request: CycleRequest) -> Any:
        """Record a cycle and echo adoption/allocation results."""
        adopted: dict[str, Any] = {}
        allocated: dict[str, Any] = {}
        for adoption in request.adoptions:
            self.calls.append(f"adopt:{adoption.identity_key}")
            adopted[adoption.identity_key] = SimpleNamespace(
                code=adoption.code, origin=AllocationOrigin.ADOPTED, reason=None
            )
        for allocation in request.allocations:
            self.calls.append(f"allocate:{allocation.identity_key}")
            adopted_result = adopted.get(allocation.identity_key)
            if adopted_result is not None:
                allocated[allocation.identity_key] = adopted_result
                continue
            allocated[allocation.identity_key] = SimpleNamespace(
                code=allocation.preferred_code,
                origin=AllocationOrigin.PREFERRED,
                reason=None,
            )
        if request.allocations:
            self.calls.append(f"unregister:{request.observation.entry_id}")
        return SimpleNamespace(
            adopted=adopted,
            allocated=allocated,
            unaccounted_slots=frozenset(),
        )


def _reservation(identity_key: str, code: str = "9999") -> Reservation:
    """Build a reservation for allocation-step tests."""
    start = datetime(2026, 9, 17, 16, tzinfo=dt_util.UTC)
    end = start + timedelta(days=3)
    return Reservation(
        identity_key=identity_key,
        start=start,
        end=end,
        buffered_start=start,
        buffered_end=end,
        summary="Reserved: Test Guest",
        slot_name="Test Guest",
        display_slot_name="RC Test Guest",
        slot_code=code,
    )


def _slot(slot: int, code: str, name: str = "RC Test Guest") -> ManagedSlot:
    """Build a readable occupied managed slot."""
    start = datetime(2026, 9, 17, 16, tzinfo=dt_util.UTC)
    end = start + timedelta(days=3)
    return ManagedSlot(
        slot=slot,
        managed=True,
        status=SlotStatus.OCCUPIED,
        actual_name=name,
        actual_code=code,
        actual_code_present=True,
        actual_start=start,
        actual_end=end,
    )


async def test_phase3_adopts_without_allocation(
    monkeypatch: Any,
) -> None:
    """The allocation step adopts observed codes before issuing codes."""
    allocator = FakeAllocator()
    monkeypatch.setattr(
        code_allocation,
        "get_allocator",
        lambda _hass: allocator,
    )
    reservation = _reservation("identity-a", code="1111")

    await code_allocation.async_resolve_codes(
        SimpleNamespace(),
        "entry-a",
        "front",
        4,
        [_slot(1, "2222")],
        [reservation],
    )

    assert allocator.calls == [
        "adopt:identity-a",
        "allocate:identity-a",
        "unregister:entry-a",
    ]
    assert reservation.slot_code == "2222"
    assert reservation.code_source == "adopted"


async def test_unmatched_reservation_is_held_codeless(
    monkeypatch: Any,
) -> None:
    """Reservations without an observed adoption are not left generated."""
    allocator = FakeAllocator()
    monkeypatch.setattr(
        code_allocation,
        "get_allocator",
        lambda _hass: allocator,
    )
    reservation = _reservation("identity-a", code="1111")

    await code_allocation.async_resolve_codes(
        SimpleNamespace(),
        "entry-a",
        "front",
        4,
        [],
        [reservation],
    )

    assert allocator.calls == []
    assert reservation.slot_code is None
    assert reservation.code_source == "unallocated"


async def test_missing_allocator_holds_reservations_codeless(
    monkeypatch: Any,
) -> None:
    """Allocator-unavailable fallback never leaves generated codes issuable."""
    monkeypatch.setattr(
        code_allocation,
        "get_allocator",
        lambda _hass: None,
    )
    reservation = _reservation("identity-a", code="1111")

    await code_allocation.async_resolve_codes(
        SimpleNamespace(),
        "entry-a",
        "front",
        4,
        [],
        [reservation],
    )

    assert reservation.slot_code is None
    assert reservation.code_source == "unallocated"


async def test_unmatched_coded_slot_keeps_adoption_gate_pending(
    monkeypatch: Any,
) -> None:
    """Readable coded slots must be accounted before adoption completes."""
    allocator = FakeAllocator()
    monkeypatch.setattr(
        code_allocation,
        "get_allocator",
        lambda _hass: allocator,
    )

    await code_allocation.async_resolve_codes(
        SimpleNamespace(),
        "entry-a",
        "front",
        4,
        [_slot(1, "2222")],
        [],
    )

    assert allocator.calls == []


async def test_duplicate_unmatched_slot_keeps_adoption_gate_pending(
    monkeypatch: Any,
) -> None:
    """Duplicate-code slot identities still count toward adoption completeness."""
    allocator = FakeAllocator()
    monkeypatch.setattr(
        code_allocation,
        "get_allocator",
        lambda _hass: allocator,
    )
    reservation = _reservation("identity-a", code="1111")

    await code_allocation.async_resolve_codes(
        SimpleNamespace(),
        "entry-a",
        "front",
        4,
        [_slot(1, "2222"), _slot(2, "2222", "RC Other Guest")],
        [reservation],
    )

    assert allocator.calls == ["adopt:identity-a"]


async def test_unreadable_slots_keep_adoption_gate_pending(
    monkeypatch: Any,
) -> None:
    """Incomplete physical reads do not complete allocator adoption."""
    allocator = FakeAllocator()
    monkeypatch.setattr(
        code_allocation,
        "get_allocator",
        lambda _hass: allocator,
    )
    reservation = _reservation("identity-a", code="1111")
    unreadable = ManagedSlot(
        slot=1,
        managed=True,
        status=SlotStatus.UNKNOWN,
        actual_name=None,
        actual_code=None,
        actual_code_present=False,
    )

    await code_allocation.async_resolve_codes(
        SimpleNamespace(),
        "entry-a",
        "front",
        4,
        [unreadable],
        [reservation],
    )

    assert allocator.calls == []
    assert reservation.slot_code is None
    assert reservation.code_source == "unallocated"


async def test_invalid_code_length_keeps_adoption_gate_pending(
    monkeypatch: Any,
) -> None:
    """Skipped readable codes leave adoption pending for a later cycle."""
    allocator = FakeAllocator()
    monkeypatch.setattr(
        code_allocation,
        "get_allocator",
        lambda _hass: allocator,
    )
    reservation = _reservation("identity-a", code="1111")

    await code_allocation.async_resolve_codes(
        SimpleNamespace(),
        "entry-a",
        "front",
        4,
        [_slot(1, "22222")],
        [reservation],
    )

    assert allocator.calls == []
    assert reservation.slot_code is None
    assert reservation.code_source == "unallocated"


async def test_unknown_slot_with_stale_code_is_not_adopted(
    monkeypatch: Any,
) -> None:
    """UNKNOWN status is unreadable even if stale code data remains."""
    allocator = FakeAllocator()
    monkeypatch.setattr(
        code_allocation,
        "get_allocator",
        lambda _hass: allocator,
    )
    reservation = _reservation("identity-a", code="1111")
    unreadable = _slot(1, "2222")
    unreadable.status = SlotStatus.UNKNOWN

    await code_allocation.async_resolve_codes(
        SimpleNamespace(),
        "entry-a",
        "front",
        4,
        [unreadable],
        [reservation],
    )

    assert allocator.calls == []
    assert reservation.slot_code is None
    assert reservation.code_source == "unallocated"


async def test_repeated_adoption_is_idempotent() -> None:
    """Repeating the same adoption does not duplicate registry owners."""
    allocator = _allocator()
    request = AdoptionRequest(
        entry_id="entry-a",
        identity_key="identity-a",
        code="2222",
        code_length=4,
        lockname="front",
        slot=1,
    )

    first = await allocator.async_adopt(request)
    second = await allocator.async_adopt(request)

    assert first.code == second.code == "2222"
    assert len(allocator._registry.records["2222"].owners) == 1


async def test_fingerprint_history_is_not_self_conflict() -> None:
    """A historical identity match is re-keyed before conflict detection."""
    allocator = _allocator()
    await allocator.async_adopt(
        AdoptionRequest(
            entry_id="entry-a",
            identity_key="identity-old",
            code="2222",
            code_length=4,
            lockname="front",
            slot=1,
        )
    )

    await allocator.async_adopt(
        AdoptionRequest(
            entry_id="entry-a",
            identity_key="identity-new",
            code="2222",
            code_length=4,
            lockname="front",
            slot=1,
            fingerprint_history=frozenset({"identity-old"}),
        )
    )

    record = allocator._registry.records["2222"]
    assert [owner.identity_key for owner in record.owners] == ["identity-new"]
    assert allocator._registry.conflicts() == []


async def test_fingerprint_history_moves_from_old_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Historical owners are found across records when the code changes."""
    notifications: list[str] = []
    monkeypatch.setattr(
        allocator_module,
        "async_create",
        lambda _hass, message, **_kwargs: notifications.append(message),
    )
    allocator = _allocator()
    await allocator.async_adopt(
        AdoptionRequest(
            entry_id="entry-a",
            identity_key="identity-old",
            code="2222",
            code_length=4,
            lockname="front",
            slot=1,
        )
    )
    await allocator.async_adopt(
        AdoptionRequest(
            entry_id="entry-c",
            identity_key="identity-c",
            code="2222",
            code_length=4,
            lockname="front",
            slot=3,
        )
    )
    await allocator.async_adopt(
        AdoptionRequest(
            entry_id="entry-b",
            identity_key="identity-b",
            code="3333",
            code_length=4,
            lockname="front",
            slot=2,
        )
    )

    result = await allocator.async_adopt(
        AdoptionRequest(
            entry_id="entry-a",
            identity_key="identity-new",
            code="3333",
            code_length=4,
            lockname="front",
            slot=1,
            fingerprint_history=frozenset({"identity-old"}),
        )
    )

    assert result.code == "3333"
    assert allocator._registry.code_for_identity("identity-old") is None
    assert allocator._registry.code_for_identity("identity-new") == "3333"
    assert [
        owner.identity_key for owner in allocator._registry.records["2222"].owners
    ] == ["identity-c"]
    assert {
        owner.identity_key for owner in allocator._registry.records["3333"].owners
    } == {
        "identity-b",
        "identity-new",
    }
    assert len(notifications) == 2


async def test_fingerprint_history_removes_old_observed_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Identity coalescing drops stale observed aliases for prior keys."""
    notifications: list[str] = []
    monkeypatch.setattr(
        allocator_module,
        "async_create",
        lambda _hass, message, **_kwargs: notifications.append(message),
    )
    allocator = _allocator()
    await allocator.async_adopt(
        AdoptionRequest(
            entry_id="entry-a",
            identity_key="identity-old",
            code="1111",
            code_length=4,
            lockname="front",
            slot=1,
        )
    )
    await allocator.async_adopt(
        AdoptionRequest(
            entry_id="entry-a",
            identity_key="identity-old",
            code="2222",
            code_length=4,
            lockname="front",
            slot=1,
        )
    )
    await allocator.async_adopt(
        AdoptionRequest(
            entry_id="entry-c",
            identity_key="identity-c",
            code="2222",
            code_length=4,
            lockname="front",
            slot=3,
        )
    )

    await allocator.async_adopt(
        AdoptionRequest(
            entry_id="entry-a",
            identity_key="identity-new",
            code="3333",
            code_length=4,
            lockname="front",
            slot=1,
            fingerprint_history=frozenset({"identity-old"}),
        )
    )

    old_alias = "identity-old:observed:entry-a:front:1"
    assert allocator._registry.code_for_identity(old_alias) is None
    assert allocator._registry.code_for_identity("identity-old") is None
    assert allocator._registry.code_for_identity("identity-new") == "3333"
    assert [
        owner.identity_key for owner in allocator._registry.records["2222"].owners
    ] == ["identity-c"]


async def test_identity_mismatch_keeps_observed_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Observed lock code wins when registry has stale code for identity."""
    notifications: list[str] = []
    monkeypatch.setattr(
        allocator_module,
        "async_create",
        lambda _hass, message, **_kwargs: notifications.append(message),
    )
    allocator = _allocator()
    await allocator.async_adopt(
        AdoptionRequest(
            entry_id="entry-a",
            identity_key="identity-a",
            code="1111",
            code_length=4,
            lockname="front",
            slot=1,
        )
    )
    request = AdoptionRequest(
        entry_id="entry-a",
        identity_key="identity-a",
        code="2222",
        code_length=4,
        lockname="front",
        slot=1,
    )

    first = await allocator.async_adopt(request)
    second = await allocator.async_adopt(request)

    assert first.code == second.code == "2222"
    assert first.reason == second.reason == "identity_code_mismatch"
    assert allocator._registry.code_for_identity("identity-a") == "1111"
    assert len(notifications) == 1

    await allocator.async_adopt(
        AdoptionRequest(
            entry_id="entry-b",
            identity_key="identity-b",
            code="2222",
            code_length=4,
            lockname="front",
            slot=2,
        )
    )

    observed = allocator._registry.records["2222"]
    assert len(observed.owners) == 2
    assert any(
        owner.identity_key.startswith("identity-a:observed:")
        for owner in observed.owners
    )
    assert len(notifications) == 2


async def test_mismatched_alias_rekeys_on_manual_code_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Manual code changes move only the observed alias bookkeeping."""
    notifications: list[str] = []
    monkeypatch.setattr(
        allocator_module,
        "async_create",
        lambda _hass, message, **_kwargs: notifications.append(message),
    )
    allocator = _allocator()
    await allocator.async_adopt(
        AdoptionRequest(
            entry_id="entry-a",
            identity_key="identity-a",
            code="1111",
            code_length=4,
            lockname="front",
            slot=1,
        )
    )
    await allocator.async_adopt(
        AdoptionRequest(
            entry_id="entry-a",
            identity_key="identity-a",
            code="2222",
            code_length=4,
            lockname="front",
            slot=1,
        )
    )
    await allocator.async_adopt(
        AdoptionRequest(
            entry_id="entry-c",
            identity_key="identity-c",
            code="2222",
            code_length=4,
            lockname="front",
            slot=3,
        )
    )
    await allocator.async_adopt(
        AdoptionRequest(
            entry_id="entry-b",
            identity_key="identity-b",
            code="3333",
            code_length=4,
            lockname="front",
            slot=2,
        )
    )
    changed = AdoptionRequest(
        entry_id="entry-a",
        identity_key="identity-a",
        code="3333",
        code_length=4,
        lockname="front",
        slot=1,
    )

    result = await allocator.async_adopt(changed)
    repeated = await allocator.async_adopt(changed)

    alias_key = "identity-a:observed:entry-a:front:1"
    assert result.code == repeated.code == "3333"
    assert allocator._registry.code_for_identity(alias_key) == "3333"
    assert allocator._registry.code_for_identity("identity-a") == "1111"
    assert [
        owner.identity_key for owner in allocator._registry.records["2222"].owners
    ] == ["identity-c"]
    assert {
        owner.identity_key for owner in allocator._registry.records["3333"].owners
    } == {
        "identity-b",
        alias_key,
    }
    assert len(notifications) == 4


async def test_mismatched_alias_releases_when_code_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Returning to the primary code removes stale observed-alias ownership."""
    notifications: list[str] = []
    monkeypatch.setattr(
        allocator_module,
        "async_create",
        lambda _hass, message, **_kwargs: notifications.append(message),
    )
    allocator = _allocator()
    await allocator.async_adopt(
        AdoptionRequest(
            entry_id="entry-a",
            identity_key="identity-a",
            code="1111",
            code_length=4,
            lockname="front",
            slot=1,
        )
    )
    await allocator.async_adopt(
        AdoptionRequest(
            entry_id="entry-a",
            identity_key="identity-a",
            code="2222",
            code_length=4,
            lockname="front",
            slot=1,
        )
    )
    await allocator.async_adopt(
        AdoptionRequest(
            entry_id="entry-c",
            identity_key="identity-c",
            code="2222",
            code_length=4,
            lockname="front",
            slot=3,
        )
    )

    result = await allocator.async_adopt(
        AdoptionRequest(
            entry_id="entry-a",
            identity_key="identity-a",
            code="1111",
            code_length=4,
            lockname="front",
            slot=1,
        )
    )

    alias_key = "identity-a:observed:entry-a:front:1"
    assert result.code == "1111"
    assert allocator._registry.code_for_identity(alias_key) is None
    assert [
        owner.identity_key for owner in allocator._registry.records["2222"].owners
    ] == ["identity-c"]
    assert allocator._registry.code_for_identity("identity-a") == "1111"
    assert len(notifications) == 2


async def test_adoption_gate_warns_without_opening(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Expired adoption gate reports pending entries but still blocks issuance."""
    notifications: list[str] = []
    monkeypatch.setattr(
        allocator_module,
        "async_create",
        lambda _hass, message, **_kwargs: notifications.append(message),
    )
    allocator = DoorCodeAllocator(_fake_hass("entry-a"))
    allocator._store = cast(Any, SimpleNamespace(async_save=lambda _registry: None))
    allocator._gate_deadline = 0.0

    await allocator.async_register_entry("entry-a")
    allocator._gate_deadline = 1.0
    result = await allocator.async_allocate(
        AllocationRequest(
            entry_id="entry-a",
            identity_key="identity-a",
            preferred_code="1111",
            code_length=4,
        )
    )

    assert result.code is None
    assert result.reason == "adoption_pending"
    assert allocator.diagnostics["pending_adoption"] == ["entry-a"]
    assert notifications
    await allocator.async_unregister_entry("entry-a")
    assert allocator.diagnostics["pending_adoption"] == []


async def test_allocate_repeats_existing_identity() -> None:
    """Repeating allocation returns the same existing registry owner."""
    allocator = _allocator()
    request = AllocationRequest(
        entry_id="entry-a",
        identity_key="identity-a",
        preferred_code="1111",
        code_length=4,
    )

    first = await allocator.async_allocate(request)
    second = await allocator.async_allocate(
        AllocationRequest(
            entry_id="entry-a",
            identity_key="identity-a",
            preferred_code="2222",
            code_length=4,
        )
    )

    assert first.code == second.code == "1111"
    assert first.origin is second.origin is AllocationOrigin.PREFERRED
    assert len(allocator._registry.records) == 1


async def test_collision_resolution_is_deterministic() -> None:
    """The same identity and registry produce the same replacement code."""
    first_allocator = _allocator()
    second_allocator = _allocator()
    blocker = AllocationRequest(
        entry_id="entry-a",
        identity_key="identity-a",
        preferred_code="1111",
        code_length=4,
    )
    request = AllocationRequest(
        entry_id="entry-b",
        identity_key="identity-b",
        preferred_code="1111",
        code_length=4,
    )

    await first_allocator.async_allocate(blocker)
    await second_allocator.async_allocate(blocker)
    first = await first_allocator.async_allocate(request)
    second = await second_allocator.async_allocate(request)

    assert first.code == second.code
    assert first.code != "1111"
    assert first.origin is AllocationOrigin.COLLISION_RESOLVED


async def test_feed_order_independence_from_identity_sort(
    monkeypatch: Any,
) -> None:
    """Allocation requests are submitted in identity-key order."""
    allocator = FakeAllocator()
    monkeypatch.setattr(code_allocation, "get_allocator", lambda _hass: allocator)
    later = _reservation("identity-b", code="2222")
    earlier = _reservation("identity-a", code="1111")

    await code_allocation.async_resolve_codes(
        SimpleNamespace(),
        "entry-a",
        None,
        4,
        [],
        [later, earlier],
    )

    assert allocator.calls == [
        "allocate:identity-a",
        "allocate:identity-b",
        "unregister:entry-a",
    ]


async def test_exhaustion_notifies_once_per_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A full code space declines issuance and reports once per cycle."""
    notifications: list[str] = []
    monkeypatch.setattr(
        code_allocation,
        "async_create",
        lambda _hass, message, **_kwargs: notifications.append(message),
    )
    allocator = _allocator()
    monkeypatch.setattr(code_allocation, "get_allocator", lambda _hass: allocator)
    for digit in range(10):
        await allocator.async_allocate(
            AllocationRequest(
                entry_id=f"entry-{digit}",
                identity_key=f"taken-{digit}",
                preferred_code=str(digit),
                code_length=1,
            )
        )
    first = _reservation("identity-z", code="0")
    second = _reservation("identity-y", code="1")

    await code_allocation.async_resolve_codes(
        _fake_hass(),
        "entry-z",
        None,
        1,
        [],
        [first, second],
    )

    assert first.slot_code is None
    assert second.slot_code is None
    assert [message for message in notifications if "exhausted" in message] == [
        notifications[0]
    ]


async def test_unaccounted_slots_suppress_issuance() -> None:
    """Unreadable slots without registry owners fail closed."""
    allocator = _allocator()

    result = await allocator.async_resolve_cycle(
        CycleRequest(
            observation=CycleObservation(
                entry_id="entry-a",
                lockname="front",
                managed_slots=frozenset({1}),
                observed_codes={},
                unreadable_slots=frozenset({1}),
            ),
            adoptions=[],
            rekeys=[],
            allocations=[
                AllocationRequest(
                    entry_id="entry-a",
                    identity_key="identity-a",
                    preferred_code="1111",
                    code_length=4,
                    lockname="front",
                    slot=1,
                )
            ],
            active_keys={"identity-a"},
        )
    )

    allocation = result.allocated["identity-a"]
    assert allocation.code is None
    assert allocation.reason == "unaccounted_slots"
    assert result.unaccounted_slots == frozenset({1})


async def test_recovery_fail_closed_for_published_identity() -> None:
    """A lost registry never replaces a previously published code."""
    allocator = _allocator()
    allocator._registry_lost = True

    result = await allocator.async_allocate(
        AllocationRequest(
            entry_id="entry-a",
            identity_key="identity-a",
            preferred_code="1111",
            code_length=4,
            previously_published=True,
        )
    )

    assert result.code is None
    assert result.reason == "recovery_fail_closed"


async def test_disabled_entries_do_not_hold_adoption_gate() -> None:
    """Entries that will not adopt are not seeded into the gate."""
    hass = _fake_hass("entry-a")
    hass.config_entries.async_entries = lambda _domain=None: [
        SimpleNamespace(
            entry_id="entry-a",
            disabled_by=None,
            data={"keymaster_entry_id": "front"},
        ),
        SimpleNamespace(
            entry_id="entry-disabled",
            disabled_by="user",
            data={"keymaster_entry_id": "front"},
        ),
        SimpleNamespace(
            entry_id="entry-lockless",
            disabled_by=None,
            data={"keymaster_entry_id": None},
        ),
        SimpleNamespace(
            entry_id="entry-none",
            disabled_by=None,
            data={"keymaster_entry_id": "(none)"},
        ),
    ]

    allocator = DoorCodeAllocator(hass)

    assert allocator.diagnostics["pending_adoption"] == []


def _fake_hass(*entry_ids: str) -> Any:
    """Build a minimal hass object for allocator unit tests."""
    hass = SimpleNamespace()
    hass.data = {}
    hass.config = SimpleNamespace(config_dir=".")
    entries = [SimpleNamespace(entry_id=entry_id) for entry_id in entry_ids]
    hass.config_entries = SimpleNamespace(async_entries=lambda _domain=None: entries)
    return hass


def _allocator() -> DoorCodeAllocator:
    """Build an allocator with persistence disabled for unit tests."""
    allocator = DoorCodeAllocator(_fake_hass())
    allocator._store = cast(Any, SimpleNamespace(async_save=lambda _registry: None))
    return allocator
