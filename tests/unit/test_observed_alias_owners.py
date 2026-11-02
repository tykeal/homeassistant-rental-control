# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Observed aliases reserve a code without becoming its owners (#842).

A suppressed slot's observed code is recorded under an alias even when
another booking already owns that code, so the code stays out of
issuance for as long as the slot presents it. The alias is not an
issuance owner: it must not form an ``adoption_conflict`` and must not
stop the real owner from being released once its own slot clears.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from typing import cast

from homeassistant.util import dt as dt_util
import pytest

from custom_components.rental_control.allocator import adoption
from custom_components.rental_control.allocator import allocator as allocator_module
from custom_components.rental_control.allocator.allocator import DoorCodeAllocator
from custom_components.rental_control.allocator.models import AdoptionRequest
from custom_components.rental_control.allocator.models import AllocationOrigin
from custom_components.rental_control.allocator.models import AllocationOwner
from custom_components.rental_control.allocator.models import CycleObservation
from custom_components.rental_control.allocator.registry import is_observed_alias
from custom_components.rental_control.coordinator_helpers import code_allocation
from custom_components.rental_control.coordinator_helpers import slot_notifications
from custom_components.rental_control.coordinator_helpers.reissue import (
    CodeResolutionRequest,
)
from custom_components.rental_control.coordinator_helpers.reissue import (
    ReissueSuppression,
)
from custom_components.rental_control.reconciliation import ManagedSlot
from custom_components.rental_control.reconciliation import Reservation
from custom_components.rental_control.reconciliation import SlotStatus

from tests.fixtures.fake_hass import fake_config

_ALIAS = "identity-a:observed:entry-a:front:1"


@pytest.fixture(autouse=True)
def _inert_notifications(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record allocator notifications and silence the others."""
    raised: list[str] = []
    monkeypatch.setattr(code_allocation, "async_create", lambda *_a, **_k: None)
    monkeypatch.setattr(slot_notifications, "async_create", lambda *_a, **_k: None)
    monkeypatch.setattr(slot_notifications, "async_dismiss", lambda *_a, **_k: None)
    monkeypatch.setattr(
        allocator_module,
        "async_create",
        lambda _hass, message, **_k: raised.append(message),
    )
    return raised


def _hass() -> Any:
    """Build a minimal hass object for allocator unit tests."""
    hass = SimpleNamespace()
    hass.data = {}
    hass.config = fake_config()
    hass.config_entries = SimpleNamespace(async_entries=lambda _domain=None: [])
    return hass


def _allocator() -> DoorCodeAllocator:
    """Build an allocator with persistence disabled for unit tests."""
    allocator = DoorCodeAllocator(_hass())
    allocator._store = cast(Any, SimpleNamespace(async_save=lambda _registry: None))
    return allocator


def _reservation(identity_key: str, name: str, code: str) -> Reservation:
    """Build a reservation whose observed slot name matches ``name``."""
    start = datetime(2026, 9, 17, 16, tzinfo=dt_util.UTC)
    end = start + timedelta(days=3)
    return Reservation(
        identity_key=identity_key,
        start=start,
        end=end,
        buffered_start=start,
        buffered_end=end,
        summary=f"Reserved: {name}",
        slot_name=name,
        display_slot_name=f"RC {name}",
        slot_code=code,
    )


def _coded_slot(slot: int, code: str, name: str, identity_key: str) -> ManagedSlot:
    """Build a readable occupied managed slot holding ``code``."""
    start = datetime(2026, 9, 17, 16, tzinfo=dt_util.UTC)
    end = start + timedelta(days=3)
    managed = ManagedSlot(
        slot=slot,
        managed=True,
        status=SlotStatus.OCCUPIED,
        actual_name=f"RC {name}",
        actual_code=code,
        actual_code_present=True,
        actual_start=start,
        actual_end=end,
    )
    managed.persisted_identity_key = identity_key
    return managed


async def _resolve(
    allocator: DoorCodeAllocator,
    monkeypatch: pytest.MonkeyPatch,
    managed_slots: list[ManagedSlot],
    reservations: list[Reservation],
    *,
    entry_id: str,
    lockname: str | None = "front",
    suppression: ReissueSuppression | None = None,
) -> None:
    """Run one coordinator allocation cycle against the real allocator."""
    monkeypatch.setattr(code_allocation, "get_allocator", lambda _hass: allocator)
    await code_allocation.async_resolve_codes(
        CodeResolutionRequest(
            hass=_hass(),
            entry_id=entry_id,
            lockname=lockname,
            code_length=4,
            managed_slots=managed_slots,
            reservations=reservations,
            suppression=suppression or ReissueSuppression(),
        )
    )


def _owner_keys(allocator: DoorCodeAllocator, code: str) -> set[str]:
    """Return the identity keys owning ``code`` in the shared registry."""
    record = allocator._registry.records.get(code)
    return {owner.identity_key for owner in record.owners} if record else set()


async def test_alias_outlives_the_real_owner_it_shares_a_code_with(
    monkeypatch: pytest.MonkeyPatch,
    _inert_notifications: list[str],
) -> None:
    """The #842 hole: a stale duplicate keeps its code off the free list.

    entry-b owns ``1111`` at slot 5 while entry-a's suppressed slot 1
    also presents it. Once entry-b's booking ends and its slot clears,
    entry-b's owner must be released, yet ``1111`` must stay reserved
    because entry-a's slot still presents it, so a third booking is
    never handed a code that opens that slot.
    """
    allocator = _allocator()
    owner_b = _reservation("identity-b", "Guest B", "1111")
    await _resolve(
        allocator,
        monkeypatch,
        [_coded_slot(5, "1111", "Guest B", "identity-b")],
        [owner_b],
        entry_id="entry-b",
    )
    assert _owner_keys(allocator, "1111") == {"identity-b"}

    target = _reservation("identity-a", "Guest A", "3333")
    await _resolve(
        allocator,
        monkeypatch,
        [_coded_slot(1, "1111", "Guest A", "identity-a")],
        [target],
        entry_id="entry-a",
        suppression=ReissueSuppression(frozenset({"identity-a"}), frozenset()),
    )
    assert _owner_keys(allocator, "1111") == {"identity-b", _ALIAS}
    assert allocator._registry.conflicts() == []

    await _resolve(
        allocator,
        monkeypatch,
        [ManagedSlot(slot=5, managed=True, status=SlotStatus.FREE)],
        [],
        entry_id="entry-b",
    )
    assert _owner_keys(allocator, "1111") == {_ALIAS}

    third = _reservation("identity-c", "Guest C", "1111")
    await _resolve(
        allocator, monkeypatch, [], [third], entry_id="entry-c", lockname=None
    )
    assert third.slot_code is not None
    assert third.slot_code != "1111"

    await _resolve(
        allocator,
        monkeypatch,
        [ManagedSlot(slot=1, managed=True, status=SlotStatus.FREE)],
        [],
        entry_id="entry-a",
    )
    assert "1111" not in allocator._registry.records
    assert _inert_notifications == []


def _shared_record(allocator: DoorCodeAllocator) -> Any:
    """Return a record owned by entry-b and aliased by entry-a's slot 1."""
    adoption.adopt_unlocked(
        allocator,
        AdoptionRequest("entry-b", "identity-b", "1111", 4, "back", 5),
    )
    adoption.reserve_observed_code(
        allocator,
        AdoptionRequest("entry-a", "identity-a", "1111", 4, "front", 1),
    )
    return allocator._registry.records["1111"]


def _observation(entry_id: str, lockname: str, slot: int, code: str | None) -> Any:
    """Return a one-slot observation, holding ``code`` when given."""
    return CycleObservation(
        entry_id=entry_id,
        lockname=lockname,
        managed_slots=frozenset({slot}),
        observed_codes={code: slot} if code is not None else {},
        unreadable_slots=frozenset(),
    )


def test_release_guard_ignores_aliases_when_judging_conflict() -> None:
    """Neither side of an alias-shared record is an adoption conflict."""
    allocator = _allocator()
    record = _shared_record(allocator)
    real = next(o for o in record.owners if o.identity_key == "identity-b")
    alias = next(o for o in record.owners if is_observed_alias(o.identity_key))

    assert (
        allocator._release_guard_reason(
            record, [real], [_observation("entry-b", "back", 5, None)]
        )
        is None
    )
    assert (
        allocator._release_guard_reason(
            record, [alias], [_observation("entry-a", "front", 1, "1111")]
        )
        == "code_still_programmed"
    )
    assert (
        allocator._release_guard_reason(
            record, [alias], [_observation("entry-a", "front", 1, None)]
        )
        is None
    )


def test_two_issuance_owners_remain_a_conflict_beside_an_alias() -> None:
    """An alias does not dilute a real conflict between two bookings."""
    allocator = _allocator()
    record = _shared_record(allocator)
    allocator._registry.add_owner(
        "1111",
        4,
        AllocationOwner("entry-c", "identity-c", AllocationOrigin.ADOPTED, "side", 2),
        record.code_ref,
        "now",
    )
    real = next(o for o in record.owners if o.identity_key == "identity-b")
    alias = next(o for o in record.owners if is_observed_alias(o.identity_key))

    assert allocator._registry.conflicts() == [record]
    assert allocator._release_guard_reason(record, [real], []) == "adoption_conflict"
    assert (
        allocator._release_guard_reason(
            record, [alias], [_observation("entry-a", "front", 1, None)]
        )
        is None
    )


def test_no_alias_when_an_owner_already_stands_for_the_slot() -> None:
    """A slot already accounted for by an owner needs no placeholder."""
    allocator = _allocator()
    adoption.adopt_unlocked(
        allocator,
        AdoptionRequest("entry-a", "identity-a", "1111", 4, "front", 1),
    )
    adoption.reserve_observed_code(
        allocator,
        AdoptionRequest("entry-a", "identity-b", "1111", 4, "front", 1),
    )

    assert _owner_keys(allocator, "1111") == {"identity-a"}
