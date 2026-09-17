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
from custom_components.rental_control.coordinator_helpers import code_allocation
from custom_components.rental_control.reconciliation import ManagedSlot
from custom_components.rental_control.reconciliation import Reservation
from custom_components.rental_control.reconciliation import SlotStatus


class FakeAllocator:
    """Allocator test double that records adoption-only ordering."""

    def __init__(self) -> None:
        """Initialize recorded calls."""
        self.calls: list[str] = []

    async def async_adopt(self, request: AdoptionRequest) -> Any:
        """Record adoption and echo the observed code."""
        self.calls.append(f"adopt:{request.identity_key}")
        return SimpleNamespace(
            code=request.code, origin=SimpleNamespace(value="adopted")
        )

    async def async_allocate(self, _request: object) -> None:
        """Fail if Phase 3 attempts issuance."""
        raise AssertionError("Phase 3 must not allocate")

    async def async_unregister_entry(self, entry_id: str) -> None:
        """Record adoption-gate drainage for the completed pass."""
        self.calls.append(f"unregister:{entry_id}")


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
    """The Phase 3 step adopts observed codes and never issues new ones."""
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

    assert allocator.calls == ["adopt:identity-a", "unregister:entry-a"]
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
    result = await allocator.async_allocate(object())

    assert result.code is None
    assert result.reason == "adoption_pending"
    assert allocator.diagnostics["pending_adoption"] == ["entry-a"]
    assert notifications
    await allocator.async_unregister_entry("entry-a")
    assert allocator.diagnostics["pending_adoption"] == []


async def test_disabled_entries_do_not_hold_adoption_gate() -> None:
    """Disabled entries are not seeded because they will not run adoption."""
    hass = _fake_hass("entry-a")
    hass.config_entries.async_entries = lambda _domain=None: [
        SimpleNamespace(entry_id="entry-a", disabled_by=None),
        SimpleNamespace(entry_id="entry-disabled", disabled_by="user"),
    ]

    allocator = DoorCodeAllocator(hass)

    assert allocator.diagnostics["pending_adoption"] == ["entry-a"]


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
