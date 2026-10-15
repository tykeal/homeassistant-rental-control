# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the three issuance-gate members and the claim extraction."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from typing import cast

from custom_components.rental_control.allocator import issuance
from custom_components.rental_control.allocator.allocator import DoorCodeAllocator
from custom_components.rental_control.allocator.models import AllocationOrigin
from custom_components.rental_control.allocator.models import AllocationOwner
from custom_components.rental_control.allocator.models import AllocationRecord
from custom_components.rental_control.allocator.models import CycleObservation

_NOW = "2026-10-15T12:00:00+00:00"


def _allocator() -> DoorCodeAllocator:
    """Build an allocator with persistence disabled."""
    hass = SimpleNamespace(
        data={},
        config=SimpleNamespace(config_dir="."),
        config_entries=SimpleNamespace(async_entries=lambda _domain=None: []),
    )
    allocator = DoorCodeAllocator(cast(Any, hass))
    allocator._store = cast(Any, SimpleNamespace(async_save=lambda _registry: None))
    return allocator


def _with_owner(
    allocator: DoorCodeAllocator,
    *,
    code: str = "1234",
    entry_id: str = "entry-a",
    lockname: str | None = "front",
    slot: int | None = 1,
) -> DoorCodeAllocator:
    """Record one registry owner claiming a physical slot."""
    allocator._registry.records[code] = AllocationRecord(
        code=code,
        code_ref=f"ref-{code}",
        encoding_salt_value=entry_id,
        owners=[
            AllocationOwner(
                entry_id=entry_id,
                identity_key=f"identity-{slot}",
                origin=AllocationOrigin.ADOPTED,
                lockname=lockname,
                slot=slot,
                lock_observed=True,
                first_seen=_NOW,
                last_seen=_NOW,
            )
        ],
        code_length=len(code),
    )
    allocator._registry.rebuild_index()
    return allocator


def _observation(
    *,
    entry_id: str = "entry-a",
    lockname: str | None = "front",
    managed: frozenset[int] = frozenset({1, 2, 3}),
    unreadable: frozenset[int] = frozenset(),
) -> CycleObservation:
    """Build a cycle observation for the gate under test."""
    return CycleObservation(
        entry_id=entry_id,
        lockname=lockname,
        managed_slots=managed,
        observed_codes={},
        unreadable_slots=unreadable,
    )


def test_claimed_slots_returns_this_entry_and_lock_only() -> None:
    """The extracted claim helper scopes by entry id and lock name."""
    allocator = _with_owner(_allocator())
    _with_owner(allocator, code="5678", entry_id="entry-b", slot=2)
    _with_owner(allocator, code="9012", lockname="back", slot=3)

    assert issuance.claimed_slots(allocator, "entry-a", "front") == frozenset({1})


def test_claimed_slots_ignores_lockless_owners() -> None:
    """An owner with no physical slot claims nothing."""
    allocator = _with_owner(_allocator(), lockname=None, slot=None)

    assert issuance.claimed_slots(allocator, "entry-a", "front") == frozenset()


def test_unaccounted_slots_is_empty_without_a_lock() -> None:
    """A lockless observation can never withhold issuance."""
    allocator = _allocator()
    observation = _observation(lockname=None, unreadable=frozenset({1}))

    assert issuance.unaccounted_slots(allocator, observation) == frozenset()


def test_unaccounted_slots_is_empty_without_unreadable_slots() -> None:
    """A fully readable lock reports nothing unaccounted."""
    allocator = _allocator()

    assert issuance.unaccounted_slots(allocator, _observation()) == frozenset()


def test_unaccounted_slots_subtracts_claimed_slots() -> None:
    """An unreadable slot claimed by an owner is already accounted for."""
    allocator = _with_owner(_allocator())
    observation = _observation(unreadable=frozenset({1, 2}))

    assert issuance.unaccounted_slots(allocator, observation) == frozenset({2})


def test_unaccounted_slots_reports_unclaimed_unreadable_slots() -> None:
    """An unreadable slot with no owner withholds issuance."""
    allocator = _allocator()
    observation = _observation(unreadable=frozenset({2}))

    assert issuance.unaccounted_slots(allocator, observation) == frozenset({2})
