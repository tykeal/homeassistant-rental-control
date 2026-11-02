# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Recovery from a corrupt registry for lock-backed entries (#851).

A corrupt registry cannot be told apart from one that recorded codes
now gone, so it used to wedge every lock-backed entry until a lockless
entry happened to run. The rebuilt registry is now trusted once every
configured lock-backed entry has adopted its current lock and slot
range, with excluded slots counting as read. A lock that cannot be
reached never adopts, so it keeps issuance withheld.
"""

from __future__ import annotations

from types import MappingProxyType
from types import SimpleNamespace
from typing import Any
from typing import cast

import pytest

from custom_components.rental_control.allocator import allocator as allocator_module
from custom_components.rental_control.allocator.allocator import DoorCodeAllocator
from custom_components.rental_control.allocator.models import AllocationRequest
from custom_components.rental_control.allocator.models import CycleObservation
from custom_components.rental_control.allocator.models import CycleRequest
from custom_components.rental_control.reconciliation import ManagedSlot
from custom_components.rental_control.reconciliation import SlotStatus

from tests.unit.test_lockless_registry_recovery import _allocator
from tests.unit.test_lockless_registry_recovery import _cycle
from tests.unit.test_lockless_registry_recovery import _lock_data
from tests.unit.test_lockless_registry_recovery import _reservation
from tests.unit.test_lockless_registry_recovery import _slot

_FREE = ManagedSlot(slot=1, managed=True, status=SlotStatus.FREE)


def _corrupt(allocator: DoorCodeAllocator) -> DoorCodeAllocator:
    """Mark the registry as present but unreadable."""
    allocator._registry_lost = True
    allocator._registry_missing = False
    return allocator


async def test_corrupt_registry_recovers_once_every_lock_is_adopted() -> None:
    """Lock-backed adoption alone rebuilds a trusted registry."""
    allocator = _corrupt(_allocator("entry-lock"))

    await _cycle(
        allocator,
        [_reservation("identity-lock", code="1234")],
        entry_id="entry-lock",
        lockname="front",
        managed_slots=[_slot(1, "1234")],
    )

    assert allocator._registry_lost is False
    reservations = [_reservation(published=False)]
    await _cycle(allocator, reservations, entry_id="entry-free")
    assert reservations[0].slot_code is not None


async def test_unobserved_lock_keeps_corrupt_registry_untrusted() -> None:
    """Every configured lock-backed entry must adopt, not just one."""
    allocator = _corrupt(_allocator("entry-lock", "entry-other"))

    await _cycle(
        allocator,
        [_reservation("identity-lock", code="1234")],
        entry_id="entry-lock",
        lockname="front",
        managed_slots=[_slot(1, "1234")],
    )
    allocator._pending_adoption.discard("entry-other")

    reservations = [_reservation(published=False)]
    await _cycle(allocator, reservations, entry_id="entry-free")

    assert reservations[0].slot_code is None
    assert reservations[0].code_source == "unallocated"
    assert allocator._registry_lost is True
    assert allocator.diagnostics["unobserved_lock_entries"] == ["entry-other"]


async def test_excluded_slot_counts_as_read() -> None:
    """An excluded unreadable slot does not wedge recovery."""
    allocator = _corrupt(_allocator("entry-lock"))
    entry = allocator.hass.config_entries.async_get_entry("entry-lock")
    entry.data = MappingProxyType(_lock_data("front", max_events=2))

    await allocator.async_resolve_cycle(
        CycleRequest(
            observation=CycleObservation(
                entry_id="entry-lock",
                lockname="front",
                managed_slots=frozenset({1, 2}),
                observed_codes={},
                unreadable_slots=frozenset({2}),
            ),
            adoptions=[],
            rekeys=[],
            allocations=[],
            active_keys=set(),
            adoption_complete=True,
            excluded_slots=frozenset({2}),
        )
    )

    assert allocator._pending_adoption == set()
    assert allocator._registry_lost is False


async def test_unreachable_lock_withholds_codes_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lock that cannot be read keeps issuance shut and is named."""
    raised: list[str] = []
    monkeypatch.setattr(
        allocator_module,
        "async_create",
        lambda _hass, message, **_kwargs: raised.append(message),
    )
    allocator = _corrupt(_allocator("entry-lock"))

    await _cycle(
        allocator,
        [],
        entry_id="entry-lock",
        lockname="front",
        managed_slots=[ManagedSlot(slot=1, managed=True, status=SlotStatus.UNKNOWN)],
    )
    reservations = [_reservation(published=False)]
    await _cycle(allocator, reservations, entry_id="entry-free")

    assert reservations[0].slot_code is None
    assert allocator._registry_lost is True
    allocator._gate_deadline = 1.0
    allocator._warn_if_gate_expired()
    assert "lock unreachable" in raised[0]
    assert "lost or found corrupt" in raised[0]


async def test_lock_lost_after_adoption_returns_to_the_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An adopted lock that stops reading is named in the notification."""
    raised: list[str] = []
    monkeypatch.setattr(
        allocator_module,
        "async_create",
        lambda _hass, message, **_kwargs: raised.append(message),
    )
    allocator = _corrupt(_allocator("entry-lock", "entry-other"))
    await _cycle(
        allocator,
        [],
        entry_id="entry-lock",
        lockname="front",
        managed_slots=[_FREE],
    )
    await _cycle(
        allocator,
        [],
        entry_id="entry-lock",
        lockname="front",
        managed_slots=[ManagedSlot(slot=1, managed=True, status=SlotStatus.UNKNOWN)],
    )
    await _cycle(
        allocator,
        [],
        entry_id="entry-other",
        lockname="front",
        managed_slots=[_FREE],
    )

    assert allocator._pending_adoption == {"entry-lock"}
    assert allocator._registry_lost is True
    assert allocator._gate_deadline > 0.0
    allocator._gate_deadline = 1.0
    allocator._warn_if_gate_expired()
    assert "lock unreachable" in raised[0]


async def test_trusted_registry_saves_a_single_allocation() -> None:
    """A code issued outside a cycle is saved once the registry is trusted."""
    allocator = _corrupt(_allocator())
    saved: list[object] = []
    allocator._store = cast(Any, SimpleNamespace(async_save=saved.append))

    result = await allocator.async_allocate(
        AllocationRequest(
            entry_id="entry-free",
            identity_key="identity-a",
            preferred_code="1111",
            code_length=4,
        )
    )

    assert result.code == "1111"
    assert saved
    assert allocator._registry_lost is False


async def test_reopened_lock_is_named_after_an_earlier_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lock returning to the gate re-arms a notice already sent."""
    raised: list[str] = []
    monkeypatch.setattr(
        allocator_module,
        "async_create",
        lambda _hass, message, **_kwargs: raised.append(message),
    )
    allocator = _corrupt(_allocator("entry-lock", "entry-other"))
    await _cycle(
        allocator, [], entry_id="entry-lock", lockname="front", managed_slots=[_FREE]
    )
    allocator._gate_deadline = 1.0
    allocator._warn_if_gate_expired()
    assert len(raised) == 1
    assert "entry entry-lo" not in raised[0]

    await _cycle(
        allocator,
        [],
        entry_id="entry-lock",
        lockname="front",
        managed_slots=[ManagedSlot(slot=1, managed=True, status=SlotStatus.UNKNOWN)],
    )
    allocator._gate_deadline = 1.0
    allocator._warn_if_gate_expired()

    assert len(raised) == 2
    assert "entry entry-lo" in raised[1]


async def test_trusted_cycle_saves_grants_beside_a_declined_recovery() -> None:
    """A declined identity keeps the flags set but not the grants unsaved."""
    allocator = _corrupt(_allocator())
    saved: list[object] = []
    allocator._store = cast(Any, SimpleNamespace(async_save=saved.append))
    declined = _reservation("identity-old", code="2222", origin=None)
    granted = _reservation(published=False)

    await _cycle(allocator, [declined, granted], entry_id="entry-free")

    assert declined.slot_code is None
    assert granted.slot_code is not None
    assert saved
    assert allocator._registry_lost is True
