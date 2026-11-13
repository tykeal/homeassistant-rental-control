# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Shared-code conflicts are judged by physical slot and reported once (#942).

A booking whose fingerprint changes adopts the code already on its slot
and leaves its old identity on the same record. Those owners used to
read as a conflict between bookings, raise a notification on every
restart, and block their own cleanup. These tests replay the three
shapes seen in a real diagnostics dump and pin the notification
lifecycle: one per code, with full details, withdrawn on resolution and
never raised again for an unchanged conflict.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
import logging
from types import MappingProxyType
from types import SimpleNamespace
from typing import Any
from typing import cast

import pytest

from custom_components.rental_control.allocator import allocator as allocator_module
from custom_components.rental_control.allocator import code_origin
from custom_components.rental_control.allocator import conflict_notices
from custom_components.rental_control.allocator import services as services_module
from custom_components.rental_control.allocator.allocator import DoorCodeAllocator
from custom_components.rental_control.allocator.models import AdoptionRequest
from custom_components.rental_control.allocator.models import AllocationOrigin
from custom_components.rental_control.allocator.models import AllocationOwner
from custom_components.rental_control.allocator.models import CycleObservation
from custom_components.rental_control.allocator.models import CycleRequest
from custom_components.rental_control.allocator.store import RegistryStore
from custom_components.rental_control.const import COORDINATOR
from custom_components.rental_control.const import DOMAIN

from tests.fixtures.fake_hass import as_hass
from tests.fixtures.fake_hass import fake_config

_CODE = "4321"
_LOCK = "frontdoor"
#: Entries sharing the lock manage disjoint slot ranges, as in the dump.
_MANAGED = {
    "r14": frozenset({12, 14}),
    "r23": frozenset({20}),
    "u13": frozenset({13}),
    "u16": frozenset({16, 17}),
}


@dataclass
class _Notices:
    """Notifications raised and dismissed by the allocator."""

    created: list[dict[str, Any]] = field(default_factory=list)
    dismissed: list[str] = field(default_factory=list)


@pytest.fixture(autouse=True)
def _clean_cycle_evidence() -> Any:
    """Keep one test's in-flight evidence out of the next one."""
    code_origin.reset_cycle_evidence()
    yield
    code_origin.reset_cycle_evidence()


@pytest.fixture(name="notices")
def _notices(monkeypatch: pytest.MonkeyPatch) -> _Notices:
    """Capture conflict notifications instead of raising them."""
    notices = _Notices()
    monkeypatch.setattr(
        allocator_module,
        "async_create",
        lambda _hass, message, **kwargs: notices.created.append(
            {"message": message, **kwargs}
        ),
    )
    monkeypatch.setattr(
        conflict_notices,
        "async_dismiss",
        lambda _hass, notification_id: notices.dismissed.append(notification_id),
    )
    return notices


def _reservation(name: str, start_day: int) -> SimpleNamespace:
    """Return a live reservation with a slot name and a three-night stay."""
    return SimpleNamespace(
        display_slot_name=name,
        slot_name=name,
        start=datetime(2026, 11, start_day, 16, tzinfo=UTC),
        end=datetime(2026, 11, start_day + 3, 11, tzinfo=UTC),
        phone_last_four=None,
    )


def _hass(reservations: dict[str, dict[str, SimpleNamespace]]) -> Any:
    """Build a hass of date-based entries holding the given reservations."""
    entries = {
        entry_id: SimpleNamespace(
            entry_id=entry_id,
            title=entry_id.upper(),
            disabled_by=None,
            data=MappingProxyType({"code_generation": "date_based", "code_length": 4}),
            options=MappingProxyType({}),
        )
        for entry_id in reservations
    }
    hass = SimpleNamespace()
    hass.data = {
        DOMAIN: {
            entry_id: {COORDINATOR: SimpleNamespace(_latest_res_by_key=by_key)}
            for entry_id, by_key in reservations.items()
        }
    }
    hass.config = fake_config()
    hass.config_entries = SimpleNamespace(
        async_entries=lambda _domain=None: list(entries.values()),
        async_get_entry=entries.get,
    )
    return hass


def _allocator(hass: Any, *owners: tuple[str, str, int]) -> DoorCodeAllocator:
    """Return an allocator whose loaded registry already holds ``owners``.

    Each owner is ``(entry_id, identity_key, slot)`` on the shared lock,
    planted as a registry persisted by an earlier release would load.
    """
    allocator = DoorCodeAllocator(hass)
    allocator._store = cast(Any, SimpleNamespace(async_save=lambda _registry: None))
    allocator._pending_adoption = set()
    allocator._registry.code_ref_salt = "persisted-salt"
    for entry_id, identity_key, slot in owners:
        allocator._registry.add_owner(
            _CODE,
            4,
            AllocationOwner(
                entry_id,
                identity_key,
                AllocationOrigin.ADOPTED,
                _LOCK,
                slot,
                lock_observed=True,
                first_seen="2026-11-01T00:00:00+00:00",
                last_seen="2026-11-01T00:00:00+00:00",
            ),
            allocator.code_ref(_CODE),
            "now",
        )
    return allocator


async def _cycle(
    allocator: DoorCodeAllocator,
    entry_id: str,
    active: set[str],
    programmed_slots: set[int],
) -> None:
    """Run one cycle where ``_CODE`` is programmed in ``programmed_slots``."""
    code_origin.record_cycle_evidence(
        entry_id,
        [SimpleNamespace(identity_key=key, phone_last_four=None) for key in active],
    )
    managed = _MANAGED[entry_id]
    visible = programmed_slots & managed
    observed = {_CODE: min(visible)} if visible else {}
    await allocator.async_resolve_cycle(
        CycleRequest(
            observation=CycleObservation(
                entry_id=entry_id,
                lockname=_LOCK,
                managed_slots=managed,
                observed_codes=observed,
                unreadable_slots=frozenset(),
            ),
            adoptions=[],
            rekeys=[],
            allocations=[],
            active_keys=active,
        )
    )


def _owners(allocator: DoorCodeAllocator) -> list[str]:
    """Return the identities still owning ``_CODE``."""
    record = allocator._registry.records.get(_CODE)
    return [] if record is None else [owner.identity_key for owner in record.owners]


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Return the WARNING records logged so far."""
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


async def test_same_slot_stale_owners_recover_silently(
    notices: _Notices, caplog: pytest.LogCaptureFixture
) -> None:
    """An existing registry stuck on one slot heals on the normal sweep.

    Three adopted identities of one booking on one slot used to read as
    a conflict, which in turn held every stale identity back for good.
    """
    hass = _hass({"r23": {"guest-new": _reservation("Guest", 20)}})
    allocator = _allocator(
        hass,
        ("r23", "guest-old-1", 20),
        ("r23", "guest-old-2", 20),
        ("r23", "guest-new", 20),
    )
    assert allocator._registry.conflicts() == []

    await _cycle(allocator, "r23", {"guest-new"}, {20})

    assert _owners(allocator) == ["guest-new"]
    assert notices.created == []
    assert _warnings(caplog) == []
    assert allocator.diagnostics["conflict_count"] == 0


async def test_adopting_onto_own_slot_is_not_a_conflict(
    notices: _Notices, caplog: pytest.LogCaptureFixture
) -> None:
    """A changed fingerprint adopting its own slot's code stays silent."""
    hass = _hass({"r23": {"guest-new": _reservation("Guest", 20)}})
    allocator = _allocator(hass, ("r23", "guest-old", 20))

    await allocator.async_adopt(
        AdoptionRequest("r23", "guest-new", _CODE, 4, _LOCK, 20)
    )
    await _cycle(allocator, "r23", {"guest-new"}, {20})

    assert _owners(allocator) == ["guest-new"]
    assert notices.created == []
    assert _warnings(caplog) == []


async def test_last_stale_owner_on_a_slot_is_kept_while_programmed(
    notices: _Notices,
) -> None:
    """With no booking left on the slot, one owner still guards the code."""
    hass = _hass({"r23": {}})
    allocator = _allocator(hass, ("r23", "old-1", 20), ("r23", "old-2", 20))

    await _cycle(allocator, "r23", set(), {20})

    assert len(_owners(allocator)) == 1
    assert notices.created == []


async def test_same_entry_on_two_slots_notifies_once_with_details(
    notices: _Notices,
) -> None:
    """One booking per slot of one entry is one detailed notification."""
    hass = _hass(
        {
            "r14": {
                "guest-12": _reservation("Alice", 2),
                "guest-14": _reservation("Bob", 10),
            }
        }
    )
    allocator = _allocator(hass, ("r14", "guest-14", 14), ("r14", "guest-12", 12))

    for _refresh in range(3):
        await _cycle(allocator, "r14", {"guest-12", "guest-14"}, {12, 14})

    assert len(notices.created) == 1
    message = notices.created[0]["message"]
    assert "- R14 on frontdoor slot 12 (Alice, 2026-11-02 to 2026-11-05)" in message
    assert "- R14 on frontdoor slot 14 (Bob, 2026-11-10 to 2026-11-13)" in message
    assert notices.created[0]["notification_id"] == (
        conflict_notices.conflict_notification_id(allocator.code_ref(_CODE))
    )


async def test_cross_entry_conflict_lists_each_slot_once(
    notices: _Notices,
) -> None:
    """Stale owners on one side collapse to a single line, then go."""
    hass = _hass(
        {
            "u13": {"guest-13": _reservation("Carol", 4)},
            "u16": {"guest-16": _reservation("Dave", 6)},
        }
    )
    allocator = _allocator(
        hass,
        ("u13", "stale-1", 13),
        ("u13", "stale-2", 13),
        ("u13", "guest-13", 13),
        ("u16", "guest-16", 16),
    )

    for _refresh in range(2):
        await _cycle(allocator, "u13", {"guest-13"}, {13, 16})
        await _cycle(allocator, "u16", {"guest-16"}, {13, 16})

    assert _owners(allocator) == ["guest-13", "guest-16"]
    assert len(notices.created) == 1
    lines = [
        line
        for line in notices.created[0]["message"].splitlines()
        if line.startswith("- ")
    ]
    assert lines == [
        "- U13 on frontdoor slot 13 (Carol, 2026-11-04 to 2026-11-07)",
        "- U16 on frontdoor slot 16 (Dave, 2026-11-06 to 2026-11-09)",
    ]


async def test_resolved_conflict_is_dismissed(notices: _Notices) -> None:
    """The code's notification is withdrawn once the conflict resolves.

    However one side gives the code up - a forced re-issue, say - the
    next cycle finds no conflict left and withdraws its notification.
    """
    hass = _hass({"u13": {"guest-13": _reservation("Carol", 4)}, "u16": {}})
    allocator = _allocator(hass, ("u13", "guest-13", 13), ("u16", "guest-16", 16))
    await _cycle(allocator, "u13", {"guest-13"}, {13, 16})
    await _cycle(allocator, "u16", {"guest-16"}, {13, 16})
    assert len(notices.created) == 1

    allocator._registry.release("guest-16")
    await _cycle(allocator, "u13", {"guest-13"}, {13})

    assert _owners(allocator) == ["guest-13"]
    code_ref = allocator.code_ref(_CODE)
    assert notices.dismissed == [conflict_notices.conflict_notification_id(code_ref)]
    assert allocator.diagnostics["reported_conflicts"] == []


def _restart(hass: Any, allocator: DoorCodeAllocator) -> DoorCodeAllocator:
    """Return a fresh allocator loaded from the registry as persisted."""
    store = RegistryStore(as_hass(hass))
    payload = store._payload_from_registry(allocator._registry)
    restarted = DoorCodeAllocator(hass)
    restarted._store = allocator._store
    restarted._pending_adoption = set()
    restarted._registry = store._registry_from_payload(payload)
    return restarted


async def test_announced_conflict_stays_quiet_after_restart(
    notices: _Notices, caplog: pytest.LogCaptureFixture
) -> None:
    """A dismissed notification is not brought back by a restart."""
    hass = _hass({"u13": {"guest-13": _reservation("Carol", 4)}, "u16": {}})
    allocator = _allocator(hass, ("u13", "guest-13", 13), ("u16", "guest-16", 16))
    await _cycle(allocator, "u13", {"guest-13"}, {13, 16})
    await _cycle(allocator, "u16", {"guest-16"}, {13, 16})
    assert len(notices.created) == 1
    assert len(_warnings(caplog)) == 1

    restarted = _restart(hass, allocator)
    for _refresh in range(3):
        await _cycle(restarted, "u13", {"guest-13"}, {13, 16})

    assert len(notices.created) == 1
    assert len(_warnings(caplog)) == 1
    assert restarted.diagnostics["reported_conflicts"] == [restarted.code_ref(_CODE)]


async def test_changed_slot_set_is_announced_again(
    notices: _Notices, caplog: pytest.LogCaptureFixture
) -> None:
    """A conflict reaching another slot is a new conflict worth raising."""
    hass = _hass({"u13": {"guest-13": _reservation("Carol", 4)}, "u16": {}})
    allocator = _allocator(hass, ("u13", "guest-13", 13), ("u16", "guest-16", 16))
    await _cycle(allocator, "u13", {"guest-13"}, {13, 16})
    await _cycle(allocator, "u16", {"guest-16"}, {13, 16})
    restarted = _restart(hass, allocator)

    await restarted.async_adopt(AdoptionRequest("u16", "guest-17", _CODE, 4, _LOCK, 17))
    await _cycle(restarted, "u13", {"guest-13"}, {13, 16, 17})

    assert len(notices.created) == 2
    assert "slot 17" in notices.created[1]["message"]
    assert len(_warnings(caplog)) == 2


@pytest.mark.parametrize("dry_run", [True, False])
async def test_orphan_cleanup_keeps_one_owner_per_programmed_slot(
    monkeypatch: pytest.MonkeyPatch, dry_run: bool
) -> None:
    """A dry run promises exactly what a real cleanup would clear."""
    monkeypatch.setattr(services_module, "async_create", lambda *_a, **_k: None)
    monkeypatch.setattr(services_module, "async_dismiss", lambda *_a, **_k: None)
    allocator = _allocator(_hass({}), ("gone", "old-1", 20), ("gone", "old-2", 20))
    observation = CycleObservation(
        entry_id="other",
        lockname=_LOCK,
        managed_slots=frozenset({20}),
        observed_codes={_CODE: 20},
        unreadable_slots=frozenset(),
    )

    report = await allocator.async_clear_orphans(set(), [observation], dry_run=dry_run)

    assert [item.identity_key for item in report.cleared] == ["old-1"]
    assert [item.identity_key for item in report.retained] == ["old-2"]
