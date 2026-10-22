# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for reserving observed codes held by suppressed managed slots."""

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
from custom_components.rental_control.coordinator_helpers import code_allocation
from custom_components.rental_control.coordinator_helpers import slot_exclusions
from custom_components.rental_control.coordinator_helpers.reissue import (
    AdoptionRequests,
)
from custom_components.rental_control.coordinator_helpers.reissue import (
    CodeResolutionRequest,
)
from custom_components.rental_control.coordinator_helpers.reissue import (
    ReissueSuppression,
)
from custom_components.rental_control.reconciliation import ManagedSlot
from custom_components.rental_control.reconciliation import Reservation
from custom_components.rental_control.reconciliation import SlotStatus


@pytest.fixture(autouse=True)
def _inert_notifications(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep persistent notifications out of these allocator tests."""
    monkeypatch.setattr(slot_exclusions, "async_create", lambda *_a, **_k: None)
    monkeypatch.setattr(slot_exclusions, "async_dismiss", lambda *_a, **_k: None)
    monkeypatch.setattr(code_allocation, "async_create", lambda *_a, **_k: None)
    monkeypatch.setattr(code_allocation, "async_dismiss", lambda *_a, **_k: None)
    monkeypatch.setattr(allocator_module, "async_create", lambda *_a, **_k: None)


def _hass() -> Any:
    """Build a minimal hass object for allocator unit tests."""
    hass = SimpleNamespace()
    hass.data = {}
    hass.config = SimpleNamespace(config_dir=".")
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


def _coded_slot(slot: int, code: str, name: str) -> ManagedSlot:
    """Build a readable occupied managed slot holding ``code``."""
    start = datetime(2026, 9, 17, 16, tzinfo=dt_util.UTC)
    end = start + timedelta(days=3)
    return ManagedSlot(
        slot=slot,
        managed=True,
        status=SlotStatus.OCCUPIED,
        actual_name=f"RC {name}",
        actual_code=code,
        actual_code_present=True,
        actual_start=start,
        actual_end=end,
    )


async def _resolve(
    allocator: DoorCodeAllocator,
    monkeypatch: pytest.MonkeyPatch,
    managed_slots: list[ManagedSlot],
    reservations: list[Reservation],
    *,
    entry_id: str = "entry-a",
    code_length: int = 4,
    suppression: ReissueSuppression | None = None,
) -> None:
    """Run one coordinator allocation cycle against the real allocator."""
    monkeypatch.setattr(code_allocation, "get_allocator", lambda _hass: allocator)
    await code_allocation.async_resolve_codes(
        CodeResolutionRequest(
            hass=_hass(),
            entry_id=entry_id,
            lockname="front",
            code_length=code_length,
            managed_slots=managed_slots,
            reservations=reservations,
            suppression=suppression or ReissueSuppression(),
        )
    )


def _owner_keys(allocator: DoorCodeAllocator, code: str) -> set[str]:
    """Return the identity keys owning ``code`` in the shared registry."""
    record = allocator._registry.records.get(code)
    return {owner.identity_key for owner in record.owners} if record else set()


async def test_reissue_suppressed_code_is_not_issued_to_a_peer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reissue-suppressed slot's observed code stays off the free list."""
    allocator = _allocator()
    target = _reservation("identity-a", "Target Guest", "3333")
    peer = _reservation("identity-b", "Other Guest", "1111")
    target_slot = _coded_slot(1, "1111", "Target Guest")
    target_slot.persisted_identity_key = "identity-a"
    peer_slot = ManagedSlot(slot=2, managed=True, status=SlotStatus.FREE)

    await _resolve(
        allocator,
        monkeypatch,
        [target_slot, peer_slot],
        [target, peer],
        suppression=ReissueSuppression(frozenset({"identity-a"}), frozenset()),
    )

    assert peer.slot_code is not None
    assert peer.slot_code != "1111"
    assert _owner_keys(allocator, "1111") == {"identity-a:observed:entry-a:front:1"}


def test_rotation_pending_slot_reserves_its_observed_code() -> None:
    """The rotation-pending branch records the code it declines to adopt.

    The rotation guard only lets this branch run when the registry
    already owns the observed code for this entry, lock and slot, so no
    collision is reachable today. The reservation is recorded anyway so
    that the branch does not depend on that invariant holding.
    """
    target = _reservation("identity-a", "Target Guest", "3333")
    target.code_rotation_pending = True
    target_slot = _coded_slot(1, "1111", "Target Guest")
    target_slot.persisted_identity_key = "identity-a"

    requests = cast(
        AdoptionRequests,
        code_allocation.build_adoption_requests(
            "entry-a", "front", 4, [target_slot], [target]
        ),
    )

    assert list(requests) == []
    assert requests.suppressed_slots == {1}
    assert [
        (item.code, item.code_length, item.slot)
        for item in requests.observed_reservations
    ] == [("1111", 4, 1)]


def test_invalid_code_slot_reserves_nothing() -> None:
    """The invalid-code branch cannot collide, so it reserves nothing."""
    for observed in ("22222", "12AB"):
        requests = cast(
            AdoptionRequests,
            code_allocation.build_adoption_requests(
                "entry-a",
                "front",
                4,
                [_coded_slot(1, observed, "Target Guest")],
                [_reservation("identity-a", "Target Guest", "1111")],
            ),
        )

        assert requests.suppressed_slots == {1}
        assert requests.observed_reservations == []


async def test_reserved_observed_code_is_released_when_it_disappears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The observed-code reservation ends once the lock no longer holds it."""
    allocator = _allocator()
    target = _reservation("identity-a", "Target Guest", "3333")
    target_slot = _coded_slot(1, "1111", "Target Guest")
    target_slot.persisted_identity_key = "identity-a"

    await _resolve(
        allocator,
        monkeypatch,
        [target_slot],
        [target],
        suppression=ReissueSuppression(frozenset({"identity-a"}), frozenset()),
    )

    assert _owner_keys(allocator, "1111") == {"identity-a:observed:entry-a:front:1"}

    cleared_slot = ManagedSlot(slot=1, managed=True, status=SlotStatus.FREE)
    await _resolve(allocator, monkeypatch, [cleared_slot], [])

    assert allocator._registry.records.get("1111") is None


async def test_non_decimal_observed_code_is_not_reserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-decimal observed code can never collide, so it is skipped."""
    allocator = _allocator()
    target = _reservation("identity-a", "Target Guest", "3333")
    target_slot = _coded_slot(1, "12AB", "Target Guest")
    target_slot.persisted_identity_key = "identity-a"

    await _resolve(
        allocator,
        monkeypatch,
        [target_slot],
        [target],
        suppression=ReissueSuppression(frozenset({"identity-a"}), frozenset()),
    )

    assert allocator._registry.records.get("12AB") is None


async def test_observed_reservation_spans_entries_sharing_a_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A longer observed code still blocks a peer entry that can issue it.

    The suppression branch runs before the code-length check, so the
    held code need not match the observing entry's configured length.
    It is reserved at its own length, which is what keeps a second entry
    configured for that length from issuing it onto the shared lock.
    """
    allocator = _allocator()
    target = _reservation("identity-a", "Target Guest", "3333")
    target_slot = _coded_slot(1, "123456", "Target Guest")
    target_slot.persisted_identity_key = "identity-a"

    await _resolve(
        allocator,
        monkeypatch,
        [target_slot],
        [target],
        suppression=ReissueSuppression(frozenset({"identity-a"}), frozenset()),
    )

    assert _owner_keys(allocator, "123456") == {"identity-a:observed:entry-a:front:1"}

    peer = _reservation("identity-b", "Other Guest", "123456")
    peer_slot = ManagedSlot(slot=5, managed=True, status=SlotStatus.FREE)
    await _resolve(
        allocator,
        monkeypatch,
        [peer_slot],
        [peer],
        entry_id="entry-b",
        code_length=6,
    )

    assert peer.slot_code is not None
    assert peer.slot_code != "123456"


async def test_adoption_supersedes_this_slot_s_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adopting the reserved code replaces its placeholder, not joins it.

    When the gate withholds the replacement allocation, the suppressed
    identity owns nothing but its observed code is reserved. Once the
    suppression and the gate clear, the unchanged code is adopted by that
    same identity and slot, and the adoption must take the placeholder
    over: two owners on one record would make every later sweep report an
    adoption conflict and never release the code.
    """
    allocator = _allocator()
    target = _reservation("identity-a", "Target Guest", "3333")
    target_slot = _coded_slot(1, "1111", "Target Guest")
    target_slot.persisted_identity_key = "identity-a"
    unreadable = ManagedSlot(slot=2, managed=True, status=SlotStatus.UNKNOWN)

    await _resolve(
        allocator,
        monkeypatch,
        [target_slot, unreadable],
        [target],
        suppression=ReissueSuppression(frozenset({"identity-a"}), frozenset()),
    )

    assert _owner_keys(allocator, "1111") == {"identity-a:observed:entry-a:front:1"}
    assert target.slot_code is None

    adopting = _reservation("identity-a", "Target Guest", "3333")
    adopted_slot = _coded_slot(1, "1111", "Target Guest")
    adopted_slot.persisted_identity_key = "identity-a"
    await _resolve(allocator, monkeypatch, [adopted_slot], [adopting])

    assert _owner_keys(allocator, "1111") == {"identity-a"}
    assert allocator._registry.conflicts() == []

    cleared_slot = ManagedSlot(slot=1, managed=True, status=SlotStatus.FREE)
    await _resolve(allocator, monkeypatch, [cleared_slot], [])

    assert allocator._registry.records.get("1111") is None
