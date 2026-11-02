# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Shared codes are re-judged every cycle, not only as owners join (#870).

A conflict silenced at formation under a wrong rule, or one whose
explanation later decayed, used to stay silent forever because nothing
judged an already-shared record again. These tests pin the per-cycle
re-judgement and its dedupe: a stable verdict reports once.
"""

from __future__ import annotations

from types import MappingProxyType
from types import SimpleNamespace
from typing import Any
from typing import cast

import pytest

from custom_components.rental_control.allocator import allocator as allocator_module
from custom_components.rental_control.allocator import code_origin
from custom_components.rental_control.allocator.allocator import DoorCodeAllocator
from custom_components.rental_control.allocator.models import AdoptionRequest
from custom_components.rental_control.allocator.models import AllocationOrigin
from custom_components.rental_control.allocator.models import AllocationOwner
from custom_components.rental_control.allocator.models import CycleObservation
from custom_components.rental_control.allocator.models import CycleRequest
from custom_components.rental_control.allocator.models import ForcedReissueDirective

from tests.fixtures.fake_hass import fake_config

_CODE = "4321"


def _hass(**entries: str | None) -> Any:
    """Build a hass with ``last_four`` entries named by keyword.

    A value of ``"disabled"`` registers the entry as disabled by the
    user; ``None`` registers no entry at all.
    """
    configured = {
        entry_id: SimpleNamespace(
            entry_id=entry_id,
            title=f"Unit {entry_id[-1].upper()}",
            disabled_by="user" if state == "disabled" else None,
            data=MappingProxyType({"code_generation": "last_four", "code_length": 4}),
            options=MappingProxyType({}),
        )
        for entry_id, state in entries.items()
        if state is not None
    }
    hass = SimpleNamespace()
    hass.data = {}
    hass.config = fake_config()
    hass.config_entries = SimpleNamespace(
        async_entries=lambda _domain=None: list(configured.values()),
        async_get_entry=configured.get,
    )
    return hass


@pytest.fixture(autouse=True)
def _clean_cycle_evidence() -> Any:
    """Keep one test's in-flight evidence out of the next one."""
    code_origin.reset_cycle_evidence()
    yield
    code_origin.reset_cycle_evidence()


@pytest.fixture(name="notifications")
def _notifications(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Capture allocator notifications instead of raising them."""
    raised: list[str] = []
    monkeypatch.setattr(
        allocator_module,
        "async_create",
        lambda _hass, message, **_k: raised.append(message),
    )
    return raised


def _shared(hass: Any, *owners: tuple[str, str]) -> DoorCodeAllocator:
    """Return an allocator whose registry already shares ``_CODE``.

    The record is planted directly, as a registry persisted by an
    earlier release would load, so no owner joins and no formation
    report runs.
    """
    allocator = DoorCodeAllocator(hass)
    allocator._store = cast(Any, SimpleNamespace(async_save=lambda _registry: None))
    allocator._pending_adoption = set()
    for slot, (entry_id, identity_key) in enumerate(owners, start=1):
        allocator._registry.add_owner(
            _CODE,
            4,
            AllocationOwner(
                entry_id,
                identity_key,
                AllocationOrigin.ADOPTED,
                "front",
                slot,
                lock_observed=True,
            ),
            allocator.code_ref(_CODE),
            "now",
        )
    return allocator


async def _cycle(
    allocator: DoorCodeAllocator,
    entry_id: str,
    phones: dict[str, str | None],
    forced_reissues: tuple[ForcedReissueDirective, ...] = (),
) -> None:
    """Run one cycle for an entry whose reservations carry ``phones``."""
    code_origin.record_cycle_evidence(
        entry_id,
        [
            SimpleNamespace(identity_key=key, phone_last_four=phone)
            for key, phone in phones.items()
        ],
    )
    await allocator.async_resolve_cycle(
        CycleRequest(
            observation=CycleObservation(
                entry_id=entry_id,
                lockname=None,
                managed_slots=frozenset(),
                observed_codes={},
                unreadable_slots=frozenset(),
            ),
            adoptions=[],
            rekeys=[],
            allocations=[],
            active_keys=set(phones),
            forced_reissues=forced_reissues,
        )
    )


async def test_wrongly_silenced_conflict_reports_once_after_upgrade(
    notifications: list[str],
) -> None:
    """A persisted unexplained conflict is reported on the first cycle.

    Under #857's configuration-only rule two phoneless ``last_four``
    owners were silenced as they formed. Upgrading must report them,
    and must not report them again on every refresh after that.
    """
    allocator = _shared(_hass(a="on", b="on"), ("a", "guest-a"), ("b", "guest-b"))
    await _cycle(allocator, "b", {"guest-b": None})

    for _refresh in range(4):
        await _cycle(allocator, "a", {"guest-a": None})
        await _cycle(allocator, "b", {"guest-b": None})

    assert len(notifications) == 1
    assert allocator.diagnostics["reported_conflicts"] == [allocator.code_ref(_CODE)]


async def test_decayed_explanation_is_reported_once(
    notifications: list[str],
) -> None:
    """Phone evidence that disappears turns silence into one report."""
    allocator = _shared(_hass(a="on", b="on"), ("a", "guest-a"), ("b", "guest-b"))
    for _refresh in range(3):
        await _cycle(allocator, "a", {"guest-a": _CODE})
        await _cycle(allocator, "b", {"guest-b": _CODE})
    assert notifications == []

    for _refresh in range(3):
        await _cycle(allocator, "a", {"guest-a": None})
        await _cycle(allocator, "b", {"guest-b": _CODE})
    assert len(notifications) == 1

    await _cycle(allocator, "a", {"guest-a": _CODE})
    await _cycle(allocator, "a", {"guest-a": None})
    assert len(notifications) == 2


async def test_owner_without_a_cycle_yet_defers_judgement(
    notifications: list[str],
) -> None:
    """An enabled entry still starting up is not judged as unexplained."""
    allocator = _shared(_hass(a="on", b="on"), ("a", "guest-a"), ("b", "guest-b"))
    await _cycle(allocator, "a", {"guest-a": _CODE})
    assert notifications == []

    await _cycle(allocator, "b", {"guest-b": _CODE})
    assert notifications == []


@pytest.mark.parametrize("state", ["disabled", None])
async def test_owner_that_never_cycles_is_judged_as_it_stands(
    notifications: list[str], state: str | None
) -> None:
    """A disabled or deleted owner cannot vouch, so the code is reported."""
    allocator = _shared(_hass(a="on", b=state), ("a", "guest-a"), ("b", "guest-b"))
    await _cycle(allocator, "a", {"guest-a": _CODE})
    await _cycle(allocator, "a", {"guest-a": _CODE})

    assert len(notifications) == 1


async def test_alias_sharing_is_never_rejudged_as_a_conflict(
    notifications: list[str],
) -> None:
    """An observed alias beside one booking is not a shared-code conflict."""
    allocator = _shared(
        _hass(a="on", b="on"),
        ("a", "guest-a"),
        ("b", "guest-b:observed:b:front:2"),
    )
    await _cycle(allocator, "a", {"guest-a": None})
    await _cycle(allocator, "b", {"guest-b": None})

    assert notifications == []
    assert allocator.diagnostics["reported_conflicts"] == []


async def test_hold_made_from_shared_alias_is_not_a_conflict(
    notifications: list[str],
) -> None:
    """Re-issuing an aliased slot leaves a hold, not a second booking.

    The hold only keeps the old code reserved until the slot is
    cleared; its booking already has a replacement, so the booking
    still sharing the code is neither reported nor held back.
    """
    allocator = _shared(
        _hass(a="on", b="on"),
        ("b", "guest-b"),
        ("a", "guest-x:observed:a:front:2"),
    )
    await _cycle(
        allocator,
        "a",
        {},
        forced_reissues=(ForcedReissueDirective("a", None, "front", 2),),
    )
    await _cycle(allocator, "b", {"guest-b": None})

    owners = [owner.identity_key for owner in allocator._registry.records[_CODE].owners]
    assert owners == ["guest-b", "guest-x:reissued:a:front:2"]
    assert allocator._registry.conflicts() == []
    assert notifications == []
    record = allocator._registry.records[_CODE]
    reason = allocator._release_guard_reason(record, record.owners[:1], [])
    assert reason != "adoption_conflict"


async def test_alias_does_not_unexplain_bookings_as_they_join(
    notifications: list[str],
) -> None:
    """A phoneless alias beside two phone-explained bookings stays silent."""
    allocator = _shared(
        _hass(a="on", b="on"),
        ("a", "guest-a"),
        ("b", "guest-x:observed:b:front:2"),
    )
    code_origin.record_cycle_evidence(
        "a", [SimpleNamespace(identity_key="guest-a", phone_last_four=_CODE)]
    )
    code_origin.record_cycle_evidence(
        "b", [SimpleNamespace(identity_key="guest-b", phone_last_four=_CODE)]
    )
    await allocator.async_adopt(AdoptionRequest("b", "guest-b", _CODE, 4, "front", 3))
    await _cycle(allocator, "b", {"guest-b": _CODE})

    assert len(allocator._registry.records[_CODE].owners) == 3
    assert notifications == []
