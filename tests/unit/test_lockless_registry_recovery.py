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

from custom_components.rental_control.allocator import allocator as allocator_module
from custom_components.rental_control.allocator.allocator import DOMAIN
from custom_components.rental_control.allocator.allocator import DoorCodeAllocator
from custom_components.rental_control.allocator.models import AllocationRequest
from custom_components.rental_control.allocator.registry import AllocationRegistry
from custom_components.rental_control.allocator.store import RegistryLoadResult
from custom_components.rental_control.coordinator_helpers import code_allocation
from custom_components.rental_control.coordinator_helpers import lockless_store
from custom_components.rental_control.coordinator_helpers import reissue
from custom_components.rental_control.coordinator_helpers import (
    reservations as reservations_module,
)
from custom_components.rental_control.coordinator_helpers.coordinator_refresh_shell import (
    CoordinatorRefreshMixin,
)
from custom_components.rental_control.coordinator_helpers.models import (
    ReservationBuildContext,
)
from custom_components.rental_control.reconciliation import ManagedSlot
from custom_components.rental_control.reconciliation import Reservation
from custom_components.rental_control.reconciliation import SlotStatus

from tests.fixtures.fake_hass import fake_config

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
    hass.config = fake_config()
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
    hass.config_entries = SimpleNamespace(
        async_entries=lambda _domain=None: entries,
        async_get_entry=lambda entry_id: next(
            (entry for entry in entries if entry.entry_id == entry_id), None
        ),
    )
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
    origin: str | None = "preferred",
) -> Reservation:
    """Build a reservation carrying a deterministic generated code.

    A published reservation is recorded, by default, as having been
    issued its preferred code, as the per-entry store records it.
    """
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
    reservation.issued_origin = origin if published else None
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


async def test_a_reloaded_lock_entry_must_adopt_again() -> None:
    """Reloading a lock-backed entry withdraws its adopted standing.

    Unloading an entry clears its pending gate, and reloading it
    re-registers it. Treating the adoption it completed before the
    reload as still current would let an empty lockless cycle clear the
    recovery flags while the reloaded lock is unobserved, and a setup
    failure then removes the pending gate as well, leaving nothing to
    hold issuance back.
    """
    allocator = _allocator("entry-lock")
    await _cycle(
        allocator,
        [_reservation("identity-lock", code="1234")],
        entry_id="entry-lock",
        lockname="front",
        managed_slots=[_slot(1, "1234")],
    )
    assert allocator._adopted_locks["entry-lock"] == "front"

    await allocator.async_register_entry("entry-lock")
    allocator._registry_lost = True
    allocator._registry_missing = True

    await _cycle(allocator, [], entry_id="entry-free")

    assert allocator._registry_lost is True
    assert allocator._registry_missing is True

    await allocator.async_unregister_entry("entry-lock")
    reservations = [_reservation()]
    await _cycle(allocator, reservations, entry_id="entry-free")

    assert reservations[0].slot_code is None
    assert reservations[0].code_source == "unallocated"
    assert allocator._registry_lost is True


async def test_an_incomplete_cycle_withdraws_adopted_standing() -> None:
    """A lock-backed entry stops counting as observed once a cycle fails.

    Adoption proves the registry has seen what the lock holds, and that
    proof lapses the moment a later cycle finds a code it cannot
    account for. Leaving the entry marked as observed would let a
    lockless cycle issue that very code and clear the recovery flags.
    """
    allocator = _allocator("entry-lock")
    await _cycle(
        allocator,
        [_reservation("identity-lock", code="1234")],
        entry_id="entry-lock",
        lockname="front",
        managed_slots=[_slot(1, "1234")],
    )
    assert allocator._adopted_locks["entry-lock"] == "front"

    allocator._registry_lost = True
    allocator._registry_missing = False
    await _cycle(
        allocator,
        [],
        entry_id="entry-lock",
        lockname="front",
        managed_slots=[_slot(2, "8888")],
    )

    assert "entry-lock" not in allocator._adopted_locks

    reservations = [_reservation()]
    await _cycle(allocator, reservations, entry_id="entry-free")

    assert reservations[0].slot_code is None
    assert reservations[0].code_source == "unallocated"
    assert allocator._registry_lost is True


async def test_switching_locks_withdraws_adopted_standing() -> None:
    """Adoption is bound to the lock it read, not merely to the entry.

    Options updates change an entry's lock in place without
    re-registering it, so an entry that adopted its old lock would
    otherwise keep vouching for a lock nothing has ever observed.
    """
    allocator = _allocator("entry-lock")
    await _cycle(
        allocator,
        [_reservation("identity-lock", code="1234")],
        entry_id="entry-lock",
        lockname="front",
        managed_slots=[_slot(1, "1234")],
    )
    assert allocator._adopted_locks["entry-lock"] == "front"

    entry = next(
        entry
        for entry in allocator.hass.config_entries.async_entries(DOMAIN)
        if entry.entry_id == "entry-lock"
    )
    entry.data = MappingProxyType({"keymaster_entry_id": "Back Door"})
    allocator._registry_lost = True
    allocator._registry_missing = False

    reservations = [_reservation()]
    await _cycle(allocator, reservations, entry_id="entry-free")

    assert reservations[0].slot_code is None
    assert reservations[0].code_source == "unallocated"
    assert allocator._registry_lost is True


async def test_collision_resolved_identity_is_not_given_the_preference() -> None:
    """Recovery restores a preference only to the identity issued it (#855).

    Two entries prefer one code; the second is issued an alternative.
    After a registry loss the preference looks free to whichever entry
    refreshes first, so the recorded origin alone decides who gets it.
    """
    healthy = _allocator()
    first = [_reservation("identity-a", published=False)]
    second = [_reservation("identity-b", published=False)]
    await _cycle(healthy, first, entry_id="entry-a")
    await _cycle(healthy, second, entry_id="entry-b")
    assert first[0].issued_origin == "preferred"
    assert second[0].issued_origin == "collision_resolved"

    allocator = _allocator()
    allocator._registry_lost = True
    allocator._registry_missing = True
    late = [_reservation("identity-b", origin=second[0].issued_origin)]
    await _cycle(allocator, late, entry_id="entry-b")
    early = [_reservation("identity-a", origin=first[0].issued_origin)]
    await _cycle(allocator, early, entry_id="entry-a")

    assert late[0].slot_code is None
    assert late[0].code_source == "unallocated"
    assert early[0].slot_code == _CODE


@pytest.mark.parametrize("refused_first", [False, True])
async def test_flags_cleared_elsewhere_keep_a_resolved_code_withheld(
    refused_first: bool,
) -> None:
    """Another entry's clean cycle cannot hand over the preference (#855).

    Recovery flags clear on the first sound cycle of any entry, whether
    that comes before a collision-resolved booking refreshes or after
    it was refused. Either way the booking is still missing from the
    rebuilt registry and must stay withheld on every retry, while a
    booking recorded as preferred gets its own code back.
    """
    allocator = _allocator()
    allocator._registry_lost = True
    allocator._registry_missing = True
    resolved = [_reservation("identity-b", origin="collision_resolved")]
    if refused_first:
        await _cycle(allocator, resolved, entry_id="entry-b")
    await _cycle(allocator, [], entry_id="entry-free")
    assert allocator._registry_lost is False

    for _retry in range(2):
        resolved = [_reservation("identity-b", origin="collision_resolved")]
        await _cycle(allocator, resolved, entry_id="entry-b")
        assert resolved[0].slot_code is None
    assert allocator._registry.code_for_identity("identity-b") is None

    preferred = [_reservation("identity-a")]
    await _cycle(allocator, preferred, entry_id="entry-a")
    assert preferred[0].slot_code == _CODE


async def test_a_drifted_preference_is_not_recorded_as_preferred() -> None:
    """A kept code no longer matching the generator is not provable (#855).

    The registry keeps handing an identity the code it was first issued
    even after its generated preference changes, as when a feed stops
    carrying phone digits. Recovery would issue the new preference, so
    the kept code must not be recorded as preferred.
    """
    allocator = _allocator()
    first = [_reservation(published=False)]
    await _cycle(allocator, first, entry_id="entry-a")
    assert first[0].issued_origin == "preferred"

    drifted = [_reservation(code="9876", origin="preferred")]
    await _cycle(allocator, drifted, entry_id="entry-a")

    assert drifted[0].slot_code == _CODE
    assert drifted[0].issued_origin is None


async def test_recovery_refuses_an_unrecorded_origin() -> None:
    """A published code of unknown origin is withheld, not guessed."""
    allocator = _allocator()
    allocator._registry_lost = True
    allocator._registry_missing = True

    reservations = [_reservation(origin=None)]
    await _cycle(allocator, reservations)

    assert reservations[0].slot_code is None
    assert allocator._registry.code_for_identity("identity-a") is None


def test_issued_origin_survives_the_lockless_store() -> None:
    """The per-entry store records the origin, and keeps it when unissued."""
    store: dict[str, Any] = {}
    issued = _reservation(origin="collision_resolved")
    lockless_store.sync_lockless_slot_store(
        store, {"identity-a": issued}, entry_id="e", start_slot=1, max_events=3
    )
    mapping = store["mappings"]["identity-a"]
    assert lockless_store.issued_origin(mapping) == "collision_resolved"
    assert "code" not in mapping and _CODE not in str(mapping)

    withheld = _reservation(code=None, origin="collision_resolved")
    lockless_store.sync_lockless_slot_store(
        store, {"identity-a": withheld}, entry_id="e", start_slot=1, max_events=3
    )
    assert lockless_store.issued_origin(store["mappings"]["identity-a"]) == (
        "collision_resolved"
    )

    reloaded = _reservation(origin=None)
    CoordinatorRefreshMixin._hydrate_reservations_from_mappings(
        [reloaded], store["mappings"]
    )
    assert reloaded.issued_origin == "collision_resolved"


async def test_a_ghost_keeps_its_recorded_origin() -> None:
    """A feed miss neither drops nor overwrites the recorded origin.

    A ghost carries no preference, so the code the registry hands back
    cannot be compared with one; the hydrated origin must survive the
    cycle and the next store sync.
    """
    allocator = _allocator()
    await _cycle(allocator, [_reservation(published=False)], entry_id="entry-a")
    store: dict[str, Any] = {}
    lockless_store.sync_lockless_slot_store(
        store,
        {"identity-a": _reservation()},
        entry_id="entry-a",
        start_slot=1,
        max_events=3,
    )
    ghosts = reservations_module.build_ghost_reservations(
        set(),
        store["mappings"],
        "RC ",
        None,
        ReservationBuildContext(
            entry_id="entry-a",
            timezone=dt_util.UTC,
            event_prefix="RC",
            trim_names=False,
            max_name_length=0,
            code_buffer_before=0,
            code_buffer_after=0,
            should_update_code=False,
            code_generator="date",
            code_length=4,
            active_windows_for_name=lambda _name: set(),
        ),
    ).reservations
    assert [ghost.issued_origin for ghost in ghosts] == ["preferred"]

    await _cycle(allocator, ghosts, entry_id="entry-a")

    assert ghosts[0].slot_code == _CODE
    assert ghosts[0].issued_origin == "preferred"


async def _loaded(allocator: DoorCodeAllocator, *, lost: bool) -> DoorCodeAllocator:
    """Load a registry that was, or was not, lost."""
    result = RegistryLoadResult(
        AllocationRegistry(), registry_lost=lost, registry_missing=lost
    )

    async def _load() -> RegistryLoadResult:
        """Return the canned load result."""
        return result

    allocator._store = cast(
        Any, SimpleNamespace(async_load=_load, async_save=lambda _registry: None)
    )
    await allocator.async_load()
    return allocator


async def test_disabled_lock_entry_joins_the_gate_after_registry_loss() -> None:
    """A lost registry cannot vouch for a disabled lock's codes (#854).

    Even a reservation never published, which normal issuance would
    serve while the registry is missing, waits for that lock.
    """
    allocator = await _loaded(_allocator(disabled_lock=True), lost=True)
    assert allocator._pending_adoption == {"entry-disabled"}

    reservations = [_reservation(published=False)]
    await _cycle(allocator, reservations, entry_id="entry-free")

    assert reservations[0].slot_code is None
    assert allocator._registry_lost is True


async def test_disabled_lock_entry_stays_out_of_an_intact_registry_gate() -> None:
    """An intact registry still records a disabled lock's codes."""
    allocator = await _loaded(_allocator(disabled_lock=True), lost=False)

    assert allocator._pending_adoption == set()


async def test_disabling_an_unadopted_entry_keeps_the_gate_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unloading a disabled entry keeps it pending until it is deleted."""
    raised: list[str] = []
    monkeypatch.setattr(
        allocator_module,
        "async_create",
        lambda _hass, message, **_kwargs: raised.append(message),
    )
    allocator = await _loaded(_allocator(disabled_lock=True), lost=True)

    await allocator.async_unregister_entry("entry-disabled")
    assert allocator._pending_adoption == {"entry-disabled"}

    allocator._gate_deadline = 1.0
    allocator._warn_if_gate_expired()
    assert "clear its codes from the lock and delete it" in raised[0]

    await allocator.async_mark_entry_removed("entry-disabled")
    assert allocator._pending_adoption == set()
