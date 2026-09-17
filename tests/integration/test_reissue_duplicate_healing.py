# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""End-to-end forced re-issue duplicate healing coverage."""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from typing import cast

from homeassistant.util import dt as dt_util
import pytest

from custom_components.rental_control.allocator.allocator import DoorCodeAllocator
from custom_components.rental_control.allocator.models import AdoptionRequest
from custom_components.rental_control.allocator.models import AllocationRequest
from custom_components.rental_control.allocator.models import CycleObservation
from custom_components.rental_control.allocator.models import CycleRequest
from custom_components.rental_control.allocator.reissue import is_forced_release_hold
from custom_components.rental_control.reconciliation import ActionKind
from custom_components.rental_control.reconciliation import ManagedSlot
from custom_components.rental_control.reconciliation import Reservation
from custom_components.rental_control.reconciliation import SlotStatus
from custom_components.rental_control.reconciliation import compute_desired_plan

_START = datetime(2026, 9, 17, 16, tzinfo=dt_util.UTC)
_END = _START + timedelta(days=3)
_OLD = "1234"


async def test_sc002_cross_entry_duplicate_heals_one_side(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SC-002: identical dates across entries heal to distinct codes."""
    monkeypatch.setattr(
        "custom_components.rental_control.allocator.allocator.async_create",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "custom_components.rental_control.allocator.services.async_dismiss",
        lambda *args, **kwargs: None,
    )
    allocator = _allocator()
    await _adopt_duplicate(allocator)

    assert len(allocator._registry.records[_OLD].owners) == 2
    assert len(allocator._registry.conflicts()) == 1

    first = await allocator.async_resolve_cycle(
        CycleRequest(
            observation=_observation("entry-a", {1: _OLD}),
            adoptions=[],
            rekeys=[],
            allocations=[_allocation("entry-a", "identity-a", _OLD, 1)],
            active_keys={"identity-a"},
            forced_reissues=(_directive("entry-a", "identity-a", 1),),
        )
    )
    replacement = first.allocated["identity-a"].code
    assert replacement is not None and replacement != _OLD
    assert first.reissues[0].replacement_code_ref == allocator.code_ref(replacement)
    assert first.reissues[0].retention_reason == "code_still_programmed"

    old_record = allocator._registry.records[_OLD]
    assert len(old_record.owners) == 2
    assert any(
        is_forced_release_hold(owner.identity_key) for owner in old_record.owners
    )
    assert allocator._registry.is_available(_OLD, 4, "identity-c") is False

    reservation = _reservation("identity-a", "Target", replacement)
    plan = compute_desired_plan(
        [_reservation("identity-b", "Untargeted", _OLD), reservation],
        [
            _slot(1, _OLD, "Target", "identity-a"),
            _slot(11, _OLD, "Untargeted", "identity-b"),
        ],
        2,
        "plan",
        _START,
        entry_id="entry-a",
        lockname="front",
        start_slot=1,
    )
    overwrite_slots = [
        action.slot
        for action in plan.actions
        if action.kind is ActionKind.OVERWRITE_MANUAL_CHANGE
    ]
    assert overwrite_slots == [1]
    assert (
        _published_sensor_code(lock_backed=True, observed=_OLD, desired=replacement)
        == _OLD
    )

    final = await allocator.async_resolve_cycle(
        CycleRequest(
            observation=_observation("entry-a", {1: replacement}),
            adoptions=[],
            rekeys=[],
            allocations=[_allocation("entry-a", "identity-a", replacement, 1)],
            active_keys={"identity-a"},
        )
    )

    assert {allocator._registry.code_for_identity("identity-a"), _OLD} == {
        replacement,
        _OLD,
    }
    assert len(allocator._registry.records[_OLD].owners) == 1
    assert allocator._registry.records[_OLD].owners[0].identity_key == "identity-b"
    assert not any(
        is_forced_release_hold(owner.identity_key)
        for record in allocator._registry.records.values()
        for owner in record.owners
    )
    assert final.reissues[0].disposition == "released"
    assert len(allocator._registry.conflicts()) == 0


def _published_sensor_code(*, lock_backed: bool, observed: str, desired: str) -> str:
    """Mirror the existing sensor lag contract for lock-backed entries."""
    return observed if lock_backed and observed != desired else desired


async def _adopt_duplicate(allocator: DoorCodeAllocator) -> None:
    """Adopt the same physical code for two entries on one parent lock."""
    await allocator.async_adopt(_adoption("entry-a", "identity-a", 1))
    await allocator.async_adopt(_adoption("entry-b", "identity-b", 11))


def _adoption(entry_id: str, identity_key: str, slot: int) -> AdoptionRequest:
    """Build an adoption request for the shared old code."""
    return AdoptionRequest(entry_id, identity_key, _OLD, 4, "front", slot)


def _allocation(
    entry_id: str, identity_key: str, preferred: str, slot: int
) -> AllocationRequest:
    """Build an allocation request for a reservation."""
    return AllocationRequest(
        entry_id, identity_key, preferred, 4, lockname="front", slot=slot
    )


def _directive(entry_id: str, identity_key: str, slot: int) -> Any:
    """Build a forced re-issue directive."""
    from custom_components.rental_control.allocator.models import ForcedReissueDirective

    return ForcedReissueDirective(entry_id, identity_key, "front", slot)


def _observation(entry_id: str, codes: dict[int, str]) -> CycleObservation:
    """Build one cycle observation from slot-to-code data."""
    return CycleObservation(
        entry_id=entry_id,
        lockname="front",
        managed_slots=frozenset(codes),
        observed_codes={code: slot for slot, code in codes.items()},
        unreadable_slots=frozenset(),
    )


def _reservation(identity_key: str, name: str, code: str) -> Reservation:
    """Build one test reservation with identical date ranges."""
    return Reservation(identity_key, _START, _END, _START, _END, name, name, name, code)


def _slot(slot: int, code: str, name: str, identity: str) -> ManagedSlot:
    """Build one occupied managed slot."""
    return ManagedSlot(
        slot=slot,
        managed=True,
        status=SlotStatus.OCCUPIED,
        actual_name=name,
        actual_code=code,
        actual_code_present=True,
        persisted_identity_key=identity,
    )


def _allocator() -> DoorCodeAllocator:
    """Build an allocator with persistence disabled."""
    allocator = DoorCodeAllocator(
        cast(
            Any,
            SimpleNamespace(
                data={},
                config=SimpleNamespace(config_dir="."),
                config_entries=SimpleNamespace(async_entries=lambda _domain=None: []),
            ),
        )
    )
    allocator._store = cast(Any, SimpleNamespace(async_save=lambda _registry: None))
    return allocator
