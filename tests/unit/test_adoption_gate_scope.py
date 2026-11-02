# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Scope of the adoption gate across entries (#846).

Every entry shares one door code space, so a lock whose codes are not
known yet must withhold issuance from every entry. A pending lock whose
every slot has been read is different: its codes are known, so it only
withholds issuance from its own entry, and no other entry may receive
one of the codes read from it.
"""

from __future__ import annotations

from types import MappingProxyType

import pytest

from custom_components.rental_control.allocator.allocator import DoorCodeAllocator
from custom_components.rental_control.allocator.models import ReissuePreviewRequest
from custom_components.rental_control.const import CONF_EXCLUDED_SLOTS
from custom_components.rental_control.coordinator_helpers import slot_exclusions
from custom_components.rental_control.reconciliation import ManagedSlot
from custom_components.rental_control.reconciliation import SlotStatus

from tests.unit.test_lockless_registry_recovery import _CODE
from tests.unit.test_lockless_registry_recovery import _allocator
from tests.unit.test_lockless_registry_recovery import _cycle
from tests.unit.test_lockless_registry_recovery import _lock_data
from tests.unit.test_lockless_registry_recovery import _reservation
from tests.unit.test_lockless_registry_recovery import _slot

_UNMATCHED = "1234"


async def _read_unmatched_lock(
    allocator: DoorCodeAllocator, *, unreadable: bool = False
) -> None:
    """Cycle the lock entry with a code that matches no reservation."""
    slots = [_slot(1, _UNMATCHED)]
    if unreadable:
        slots.append(ManagedSlot(slot=2, managed=True, status=SlotStatus.UNKNOWN))
    await _cycle(
        allocator, [], entry_id="entry-lock", lockname="front", managed_slots=slots
    )


async def test_read_pending_lock_does_not_block_other_entries() -> None:
    """A lock read in full withholds issuance only for its own entry."""
    allocator = _allocator("entry-lock")
    await _read_unmatched_lock(allocator)

    reservations = [_reservation(published=False)]
    await _cycle(allocator, reservations, entry_id="entry-free")

    assert allocator._pending_adoption == {"entry-lock"}
    assert reservations[0].slot_code == _CODE
    assert allocator.diagnostics["pending_adoption_read"] == ["entry-lock"]


async def test_code_read_from_pending_lock_is_never_issued() -> None:
    """Another entry never receives a code sitting in a pending lock."""
    allocator = _allocator("entry-lock")
    await _read_unmatched_lock(allocator)

    reservations = [_reservation(code=_UNMATCHED, published=False)]
    await _cycle(allocator, reservations, entry_id="entry-free")

    assert reservations[0].slot_code not in (None, _UNMATCHED)
    preview = await allocator.async_preview_reissue(
        ReissuePreviewRequest(
            entry_id="entry-free",
            identity_key="identity-b",
            preferred_code=_UNMATCHED,
            code_length=4,
            lockname=None,
            slot=None,
        )
    )
    assert preview.reason != "adoption_pending"
    assert preview.replacement_code not in (None, _UNMATCHED)


async def test_unread_pending_lock_blocks_every_entry() -> None:
    """A lock not yet read, or with an unread slot, may hold any code."""
    allocator = _allocator("entry-lock")

    reservations = [_reservation(published=False)]
    await _cycle(allocator, reservations, entry_id="entry-free")
    assert reservations[0].slot_code is None

    await _read_unmatched_lock(allocator, unreadable=True)
    await _cycle(allocator, reservations, entry_id="entry-free")
    assert reservations[0].slot_code is None


async def test_reloading_the_entry_forgets_its_read_lock() -> None:
    """A re-registered entry must read its lock again before others issue."""
    allocator = _allocator("entry-lock")
    await _read_unmatched_lock(allocator)
    await allocator.async_register_entry("entry-lock")

    reservations = [_reservation(published=False)]
    await _cycle(allocator, reservations, entry_id="entry-free")

    assert reservations[0].slot_code is None


@pytest.mark.parametrize(
    ("lock", "max_events"), [("back", 1), ("front", 2)], ids=["moved", "widened"]
)
async def test_options_change_makes_the_read_lock_unread(
    lock: str, max_events: int
) -> None:
    """A lock or range changed in place must be read again (#856)."""
    allocator = _allocator("entry-lock")
    await _read_unmatched_lock(allocator)
    entry = allocator.hass.config_entries.async_get_entry("entry-lock")
    entry.data = MappingProxyType(_lock_data(lock, max_events=max_events))

    reservations = [_reservation(published=False)]
    await _cycle(allocator, reservations, entry_id="entry-free")

    assert reservations[0].slot_code is None
    assert allocator.diagnostics["pending_adoption_read"] == []


async def test_removed_exclusion_makes_the_read_lock_unread() -> None:
    """Clearing an exclusion makes its unreadable slot count again."""
    allocator = _allocator("entry-lock")
    entry = allocator.hass.config_entries.async_get_entry("entry-lock")
    data = _lock_data("front", max_events=2)
    entry.data = MappingProxyType(
        {**data, CONF_EXCLUDED_SLOTS: [{"slot": 2, "reason": None}]}
    )
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(
            slot_exclusions, "revalidate_persisted", lambda *_args: {2: None}
        )
        await _read_unmatched_lock(allocator, unreadable=True)
    assert allocator.diagnostics["pending_adoption_read"] == ["entry-lock"]

    entry.data = MappingProxyType(data)
    reservations = [_reservation(published=False)]
    await _cycle(allocator, reservations, entry_id="entry-free")

    assert reservations[0].slot_code is None
    assert allocator.diagnostics["pending_adoption_read"] == []


async def test_lost_registry_keeps_the_gate_global() -> None:
    """Registry-loss recovery still waits for every pending entry."""
    allocator = _allocator("entry-lock")
    allocator._registry_lost = True
    allocator._registry_missing = True
    await _read_unmatched_lock(allocator)

    reservations = [_reservation(published=False)]
    await _cycle(allocator, reservations, entry_id="entry-free")

    assert reservations[0].slot_code is None
