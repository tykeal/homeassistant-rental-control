# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Registry-loss recovery for generator-only (no-lock) config entries.

A generator-only entry drives no lock, so it observes no slots and has
nothing to adopt.  Waiting for adoption before it may re-issue therefore
waits for something that can never happen, which wedged such entries
permanently after a registry loss (issue #845).  These tests pin the
recovery path open for lockless entries while keeping it shut for
lock-backed ones.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from typing import cast

from homeassistant.util import dt as dt_util
import pytest

from custom_components.rental_control.allocator.allocator import DoorCodeAllocator
from custom_components.rental_control.allocator.models import AllocationRequest
from custom_components.rental_control.coordinator_helpers import code_allocation
from custom_components.rental_control.coordinator_helpers import reissue
from custom_components.rental_control.reconciliation import ManagedSlot
from custom_components.rental_control.reconciliation import Reservation
from custom_components.rental_control.reconciliation import SlotStatus

_START = datetime(2026, 9, 17, 16, tzinfo=dt_util.UTC)
_END = _START + timedelta(days=3)
_CODE = "9999"


def _fake_hass(*lock_entry_ids: str) -> Any:
    """Build a minimal hass object seeded with lock-backed entries."""
    hass = SimpleNamespace()
    hass.data = {}
    hass.config = SimpleNamespace(config_dir=".")
    entries = [
        SimpleNamespace(
            entry_id=entry_id,
            disabled_by=None,
            data={"keymaster_entry_id": "front"},
        )
        for entry_id in lock_entry_ids
    ]
    hass.config_entries = SimpleNamespace(async_entries=lambda _domain=None: entries)
    return hass


def _allocator(*lock_entry_ids: str) -> DoorCodeAllocator:
    """Build an allocator whose persistence is a no-op."""
    allocator = DoorCodeAllocator(_fake_hass(*lock_entry_ids))
    allocator._store = cast(Any, SimpleNamespace(async_save=lambda _registry: None))
    return allocator


def _reservation(
    identity_key: str = "identity-a",
    code: str = _CODE,
    *,
    published: bool = True,
) -> Reservation:
    """Build a reservation carrying a deterministic generated code."""
    reservation = Reservation(
        identity_key=identity_key,
        start=_START,
        end=_END,
        buffered_start=_START,
        buffered_end=_END,
        summary="Reserved: Test Guest",
        slot_name="Test Guest",
        display_slot_name="RC Test Guest",
        slot_code=code,
    )
    reservation.published_once = published
    return reservation


def _slot(slot: int, code: str) -> ManagedSlot:
    """Build a readable occupied managed slot holding *code*."""
    return ManagedSlot(
        slot=slot,
        managed=True,
        status=SlotStatus.OCCUPIED,
        actual_name="RC Test Guest",
        actual_code=code,
        actual_code_present=True,
        actual_start=_START,
        actual_end=_END,
    )


async def _cycle(
    allocator: DoorCodeAllocator,
    reservations: list[Reservation],
    *,
    entry_id: str = "entry-a",
    lockname: str | None = None,
    managed_slots: list[ManagedSlot] | None = None,
) -> None:
    """Drive one real allocation cycle through the shared allocator."""
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(code_allocation, "get_allocator", lambda _hass: allocator)
        await code_allocation.async_resolve_codes(
            reissue.CodeResolutionRequest(
                hass=cast(Any, _fake_hass()),
                entry_id=entry_id,
                lockname=lockname,
                code_length=4,
                managed_slots=managed_slots or [],
                reservations=reservations,
            )
        )


async def test_lockless_entry_recovers_from_missing_registry() -> None:
    """A no-lock entry re-issues its code instead of wedging forever."""
    allocator = _allocator()
    allocator._registry_lost = True
    allocator._registry_missing = True

    first = [_reservation()]
    await _cycle(allocator, first)

    assert first[0].slot_code == _CODE
    assert allocator._registry_lost is False
    assert allocator._registry_missing is False

    second = [_reservation()]
    await _cycle(allocator, second)

    assert second[0].slot_code == _CODE


async def test_lockless_entry_recovers_from_corrupt_registry() -> None:
    """A corrupt registry no longer declines every no-lock reservation."""
    allocator = _allocator()
    allocator._registry_lost = True
    allocator._registry_missing = False

    reservations = [_reservation(published=False)]
    await _cycle(allocator, reservations)

    assert reservations[0].slot_code == _CODE
    assert allocator._registry_lost is False


async def test_lockless_recovery_keeps_the_same_code() -> None:
    """Recovery restores the published code rather than reshuffling it."""
    allocator = _allocator()
    allocator._registry_lost = True
    allocator._registry_missing = True

    healthy = _allocator()
    await _cycle(healthy, [_reservation()])
    expected = healthy._registry.code_for_identity("identity-a")

    await _cycle(allocator, [_reservation()])

    assert allocator._registry.code_for_identity("identity-a") == expected == _CODE


async def test_lock_backed_entry_still_fails_closed_after_loss() -> None:
    """Fail-closed is preserved for entries that drive a physical lock."""
    allocator = _allocator()
    allocator._registry_lost = True
    allocator._registry_missing = True

    reservations = [_reservation(code="1234")]
    await _cycle(
        allocator,
        reservations,
        lockname="front",
        managed_slots=[_slot(1, "")],
    )

    assert reservations[0].slot_code is None
    assert reservations[0].code_source == "unallocated"
    assert allocator._registry_lost is True


async def test_lockless_recovery_avoids_a_programmed_lock_code() -> None:
    """Recovery never hands out a code another entry has in a lock."""
    allocator = _allocator()
    allocator._registry_lost = True
    allocator._registry_missing = False

    # A lock-backed entry re-registers the code physically present in its
    # slot through adoption, which runs even while the registry is lost.
    await _cycle(
        allocator,
        [_reservation("identity-lock", code=_CODE)],
        entry_id="entry-lock",
        lockname="front",
        managed_slots=[_slot(1, _CODE)],
    )
    assert allocator._registry.code_for_identity("identity-lock") == _CODE

    lockless = [_reservation("identity-free", code=_CODE)]
    await _cycle(allocator, lockless, entry_id="entry-free")

    assert lockless[0].slot_code is not None
    assert lockless[0].slot_code != _CODE


async def test_lockless_recovery_waits_for_pending_adoption() -> None:
    """Recovery holds while any lock-backed entry has yet to adopt."""
    allocator = _allocator("entry-lock")
    allocator._registry_lost = True
    allocator._registry_missing = False

    reservations = [_reservation()]
    await _cycle(allocator, reservations, entry_id="entry-free")

    assert reservations[0].slot_code is None
    assert allocator._registry_lost is True

    # Once the lock-backed entry has re-registered what its lock holds,
    # the lockless entry is free to recover.
    await _cycle(
        allocator,
        [_reservation("identity-lock", code="1234")],
        entry_id="entry-lock",
        lockname="front",
        managed_slots=[_slot(1, "1234")],
    )
    recovered = [_reservation()]
    await _cycle(allocator, recovered, entry_id="entry-free")

    assert recovered[0].slot_code == _CODE
    assert allocator._registry_lost is False


async def test_single_allocation_outside_a_cycle_still_fails_closed() -> None:
    """A bare allocation request cannot claim lockless recovery."""
    allocator = _allocator()
    allocator._registry_lost = True
    allocator._registry_missing = True

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
