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
from types import MappingProxyType
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


def _fake_hass(
    *lock_entry_ids: str,
    disabled_lock: bool = False,
    broken_lock: bool = False,
) -> Any:
    """Build a minimal hass object seeded with lock-backed entries.

    Entry data is a read-only ``MappingProxyType`` exactly as Home
    Assistant exposes it, so the lock detection used by the recovery
    gates is exercised against the real type.
    """
    hass = SimpleNamespace()
    hass.data = {}
    hass.config = SimpleNamespace(config_dir=".")
    entries = [
        SimpleNamespace(
            entry_id=entry_id,
            disabled_by=None,
            data=MappingProxyType({"keymaster_entry_id": "front"}),
        )
        for entry_id in lock_entry_ids
    ]
    if disabled_lock:
        entries.append(
            SimpleNamespace(
                entry_id="entry-disabled",
                disabled_by="user",
                data=MappingProxyType({"keymaster_entry_id": "back"}),
            )
        )
    if broken_lock:
        entries.append(
            SimpleNamespace(
                entry_id="entry-broken",
                disabled_by=None,
                data=MappingProxyType({"keymaster_entry_id": "side"}),
            )
        )
    hass.config_entries = SimpleNamespace(async_entries=lambda _domain=None: entries)
    return hass


def _allocator(
    *lock_entry_ids: str,
    disabled_lock: bool = False,
    broken_lock: bool = False,
) -> DoorCodeAllocator:
    """Build an allocator whose persistence is a no-op."""
    allocator = DoorCodeAllocator(
        _fake_hass(
            *lock_entry_ids, disabled_lock=disabled_lock, broken_lock=broken_lock
        )
    )
    allocator._store = cast(Any, SimpleNamespace(async_save=lambda _registry: None))
    return allocator


def _reservation(
    identity_key: str = "identity-a",
    code: str | None = _CODE,
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
    """Recovery never hands out a code another entry has in a lock.

    The lockless entry prefers exactly the code the lock holds, so the
    only codes recovery could reach it through are collision fallbacks.
    Those cannot be proven to be the code this guest already has, so
    recovery declines rather than publish a different one.
    """
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

    assert lockless[0].slot_code != _CODE
    assert lockless[0].slot_code is None
    assert lockless[0].code_source == "unallocated"
    assert allocator._registry_lost is True


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


async def test_recovery_declines_a_ghost_without_a_real_code() -> None:
    """A code-less ghost is never recovered onto its placeholder code.

    ``ghost_reservations`` retains a published booking through a feed
    miss with ``slot_code=None``, and ``build_allocation_requests``
    substitutes an all-zero placeholder for the missing preference.
    Allocating that placeholder would publish a fabricated code and the
    existing-owner shortcut would then keep it when the booking returns.
    """
    allocator = _allocator()
    allocator._registry_lost = True
    allocator._registry_missing = True

    ghost = _reservation("identity-ghost", code=None)
    await _cycle(allocator, [ghost], entry_id="entry-free")

    assert ghost.slot_code is None
    assert ghost.code_source == "unallocated"
    assert allocator._registry.code_for_identity("identity-ghost") is None
    assert allocator._registry_lost is True


async def test_ghost_recovers_once_its_real_code_returns() -> None:
    """The booking's return restores the generated code, not a placeholder."""
    allocator = _allocator()
    allocator._registry_lost = True
    allocator._registry_missing = True

    ghost = _reservation("identity-ghost", code=None)
    await _cycle(allocator, [ghost], entry_id="entry-free")
    assert ghost.slot_code is None

    live = _reservation("identity-ghost", code=_CODE)
    await _cycle(allocator, [live], entry_id="entry-free")

    assert live.slot_code == _CODE


async def test_recovery_waits_for_a_disabled_lock_entry() -> None:
    """A disabled lock-backed entry blocks recovery and the flag clear.

    A disabled entry is never enrolled in the adoption gate, yet
    unloading it leaves its codes programmed in the lock. An empty
    pending set therefore cannot prove every physical code is in the
    registry, so recovery must stay shut while one is configured.
    """
    allocator = _allocator(disabled_lock=True)
    allocator._registry_lost = True
    allocator._registry_missing = True

    reservations = [_reservation()]
    await _cycle(allocator, reservations, entry_id="entry-free")

    assert reservations[0].slot_code is None
    assert reservations[0].code_source == "unallocated"
    assert allocator._registry_lost is True
    assert allocator._registry_missing is True


async def test_recovery_waits_for_a_lock_entry_that_failed_setup() -> None:
    """An enabled lock entry that never adopted still blocks recovery.

    ``async_setup_entry`` unregisters an entry from the adoption gate
    when its setup raises, which an initial calendar fetch failure can
    do before any reconciliation runs. The entry stays enabled, so
    neither the gate nor a disabled-entry filter catches it, while its
    codes remain programmed in the lock.
    """
    allocator = _allocator(broken_lock=True)
    await allocator.async_unregister_entry("entry-broken")
    allocator._registry_lost = True
    allocator._registry_missing = True

    reservations = [_reservation()]
    await _cycle(allocator, reservations, entry_id="entry-free")

    assert reservations[0].slot_code is None
    assert reservations[0].code_source == "unallocated"
    assert allocator._registry_lost is True
    assert allocator._registry_missing is True


async def test_a_published_ghost_is_withheld_after_flags_clear() -> None:
    """A lost code is never replaced by a placeholder, flags or not.

    The recovery flags are global while a decline is per entry, so a
    clean cycle for one entry can clear them while another entry still
    has a published reservation whose code was lost. Normal issuance
    must not then accept that reservation's all-zero placeholder.
    """
    allocator = _allocator()
    allocator._registry_lost = True
    allocator._registry_missing = False

    ghost = _reservation("identity-ghost", code=None)
    await _cycle(allocator, [ghost], entry_id="entry-a")

    # A clean cycle for an unrelated entry clears the shared flags.
    await _cycle(allocator, [], entry_id="entry-b")
    allocator._registry_lost = False
    allocator._registry_missing = False

    still_ghosted = _reservation("identity-ghost", code=None)
    await _cycle(allocator, [still_ghosted], entry_id="entry-a")

    assert still_ghosted.slot_code is None
    assert allocator._registry.code_for_identity("identity-ghost") is None

    returned = _reservation("identity-ghost", code=_CODE)
    await _cycle(allocator, [returned], entry_id="entry-a")
    assert returned.slot_code == _CODE


async def test_an_empty_cycle_cannot_clear_flags_for_an_unobserved_lock() -> None:
    """An empty cycle declines nothing, so it must not clear the flags.

    A missing registry previously cleared the flags on any sound cycle,
    which an empty lockless cycle trivially is. That returned the
    allocator to normal issuance while an unobserved lock still held
    codes, letting a later published reservation be handed one of them.
    """
    allocator = _allocator(broken_lock=True)
    await allocator.async_unregister_entry("entry-broken")
    allocator._registry_lost = True
    allocator._registry_missing = True

    await _cycle(allocator, [], entry_id="entry-free")

    assert allocator._registry_lost is True
    assert allocator._registry_missing is True

    reservations = [_reservation()]
    await _cycle(allocator, reservations, entry_id="entry-free")

    assert reservations[0].slot_code is None
    assert reservations[0].code_source == "unallocated"
