# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for forced re-issue cycle mechanics."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from typing import cast

import pytest

from custom_components.rental_control.allocator.allocator import DoorCodeAllocator
from custom_components.rental_control.allocator.issuance import observed_alias_key
from custom_components.rental_control.allocator.models import AdoptionRequest
from custom_components.rental_control.allocator.models import AllocationOrigin
from custom_components.rental_control.allocator.models import AllocationOwner
from custom_components.rental_control.allocator.models import AllocationRecord
from custom_components.rental_control.allocator.models import AllocationRequest
from custom_components.rental_control.allocator.models import CycleObservation
from custom_components.rental_control.allocator.models import CycleRequest
from custom_components.rental_control.allocator.models import ForcedReissueDirective
from custom_components.rental_control.allocator.reissue import apply_forced_reissues
from custom_components.rental_control.allocator.reissue import forced_release_hold_key
from custom_components.rental_control.allocator.reissue import is_forced_release_hold
from custom_components.rental_control.allocator.reissue import release_forced_holds

_NOW = "2026-09-17T12:00:00+00:00"
_D = ForcedReissueDirective


def test_apply_forced_reissue_rehomes_identity_and_preserves_metadata() -> None:
    """A directive frees the identity and preserves physical owner metadata."""
    allocator = _allocator()
    owner = _owner(
        "entry-a",
        "identity-a",
        origin=AllocationOrigin.PREFERRED,
        lock_observed=True,
        first_seen="first",
        last_seen="last",
    )
    allocator._registry.records["1234"] = _record("1234", owner)
    allocator._registry.rebuild_index()
    outcomes, staged = apply_forced_reissues(
        allocator,
        _cycle(forced_reissues=(_D("entry-a", "identity-a", "front", 1),)),
    )
    hold_key = forced_release_hold_key("identity-a", "entry-a", "front", 1)
    assert allocator._registry.code_for_identity("identity-a") is None
    assert allocator._registry.code_for_identity(hold_key) == "1234"
    assert staged[0].hold_key == hold_key
    assert outcomes[0].disposition == "held_pending_release"
    assert outcomes[0].replaced_code_ref == "ref-1234"
    held_owner = allocator._registry.records["1234"].owners[0]
    assert held_owner.entry_id == "entry-a"
    assert held_owner.origin is AllocationOrigin.PREFERRED
    assert held_owner.lockname == "front"
    assert held_owner.slot == 1
    assert held_owner.lock_observed is True
    assert held_owner.first_seen == "first"
    assert held_owner.last_seen == "last"


def test_apply_forced_reissue_collapses_observed_alias_record() -> None:
    """An observed alias on the physical code becomes the retained hold."""
    allocator = _allocator()
    request = AdoptionRequest(
        entry_id="entry-a",
        identity_key="identity-a",
        code="1234",
        code_length=4,
        lockname="front",
        slot=1,
    )
    alias_key = observed_alias_key(request)
    allocator._registry.records["9999"] = _record(
        "9999", _owner("entry-a", "identity-a", lock_observed=False)
    )
    allocator._registry.records["1234"] = _record(
        "1234", _owner("entry-a", alias_key, lock_observed=True)
    )
    allocator._registry.rebuild_index()
    outcomes, _staged = apply_forced_reissues(
        allocator,
        _cycle(forced_reissues=(_D("entry-a", "identity-a", "front", 1),)),
    )
    hold_key = forced_release_hold_key("identity-a", "entry-a", "front", 1)
    assert allocator._registry.code_for_identity("identity-a") is None
    assert allocator._registry.code_for_identity(alias_key) is None
    assert allocator._registry.code_for_identity(hold_key) == "1234"
    assert allocator._registry.records["9999"].owners == []
    assert outcomes[0].replaced_code_ref == "ref-1234"


def test_slot_only_alias_uses_valid_hold_namespace() -> None:
    """A slot-only alias target still creates a structurally valid hold."""
    allocator = _allocator()
    alias_key = observed_alias_key(
        AdoptionRequest("entry-a", "identity-a", "1234", 4, "front", 1)
    )
    allocator._registry.records["1234"] = _record(
        "1234", _owner("entry-a", alias_key, lock_observed=True)
    )
    allocator._registry.rebuild_index()
    _outcomes, staged = apply_forced_reissues(
        allocator, _cycle(forced_reissues=(_D("entry-a", None, "front", 1),))
    )
    assert is_forced_release_hold(staged[0].hold_key)
    assert staged[0].hold_key == forced_release_hold_key(
        "identity-a", "entry-a", "front", 1
    )


async def test_identity_without_allocation_reports_and_still_allocates() -> None:
    """A missing identity does not block the normal allocation request."""
    allocator = _allocator()
    result = await allocator.async_resolve_cycle(
        _cycle(
            forced_reissues=(_D("entry-a", "identity-a", "front", 1),),
            allocations=[_request("identity-a", "1234")],
            active_keys={"identity-a"},
        )
    )
    assert result.reissues[0].disposition == "no_existing_allocation"
    assert result.allocated["identity-a"].code == "1234"
    assert allocator._registry.code_for_identity("identity-a") == "1234"


async def test_forced_target_adoption_does_not_reclaim_hold() -> None:
    """An observed old code is not adopted back onto the freed identity."""
    allocator = _allocator()
    allocator._registry.records["1234"] = _record(
        "1234", _owner("entry-a", "identity-a", lock_observed=True)
    )
    allocator._registry.rebuild_index()
    result = await allocator.async_resolve_cycle(
        _cycle(
            observation=CycleObservation(
                "entry-a", "front", frozenset({1}), {"1234": 1}, frozenset()
            ),
            forced_reissues=(_D("entry-a", "identity-a", "front", 1),),
            adoptions=[AdoptionRequest("entry-a", "identity-a", "1234", 4, "front", 1)],
            allocations=[_request("identity-a", "5678")],
            active_keys={"identity-a"},
        )
    )
    assert "identity-a" not in result.adopted
    assert result.allocated["identity-a"].code == "5678"
    assert allocator._registry.code_for_identity("identity-a") == "5678"


async def test_forced_reissue_uses_pending_rekey_source() -> None:
    """A historical owner is staged before rekey can reclaim the old code."""
    allocator = _allocator()
    allocator._registry.records["1234"] = _record(
        "1234", _owner("entry-a", "old-identity", lock_observed=True)
    )
    allocator._registry.rebuild_index()
    result = await allocator.async_resolve_cycle(
        CycleRequest(
            observation=CycleObservation(
                "entry-a", "front", frozenset({1}), {"1234": 1}, frozenset()
            ),
            adoptions=[],
            rekeys=[("old-identity", "identity-a")],
            allocations=[_request("identity-a", "5678")],
            active_keys={"identity-a"},
            forced_reissues=(_D("entry-a", "identity-a", "front", 1),),
        )
    )
    hold_key = forced_release_hold_key("identity-a", "entry-a", "front", 1)
    assert result.allocated["identity-a"].code == "5678"
    assert allocator._registry.code_for_identity("identity-a") == "5678"
    assert allocator._registry.code_for_identity(hold_key) == "1234"


async def test_bare_slot_without_owner_is_terminal() -> None:
    """A bare slot target with no owner records a terminal no-op."""
    allocator = _allocator()
    result = await allocator.async_resolve_cycle(
        _cycle(
            forced_reissues=(_D("entry-a", None, "front", 1),),
            allocations=[],
            active_keys=set(),
        )
    )
    assert result.allocated == {}
    assert result.reissues[0].identity_key is None
    assert result.reissues[0].disposition == "no_existing_allocation"
    assert allocator._registry.records == {}


def test_existing_hold_is_not_rehomed_again() -> None:
    """A repeated directive reports the existing hold without duplicating it."""
    allocator = _allocator()
    hold_key = forced_release_hold_key("identity-a", "entry-a", "front", 1)
    allocator._registry.records["1234"] = _record(
        "1234", _owner("entry-a", hold_key, lock_observed=True)
    )
    allocator._registry.records["5678"] = _record(
        "5678", _owner("entry-a", "identity-a", lock_observed=False)
    )
    allocator._registry.rebuild_index()
    outcomes, staged = apply_forced_reissues(
        allocator, _cycle(forced_reissues=(_D("entry-a", "identity-a", "front", 1),))
    )
    assert staged == []
    assert outcomes[0].replaced_code_ref == "ref-1234"
    assert allocator._registry.code_for_identity(hold_key) == "1234"
    assert allocator._registry.code_for_identity("identity-a") == "5678"


@pytest.mark.parametrize(
    ("guard_reason", "disposition"),
    [(None, "released"), ("code_still_programmed", "held_pending_release")],
)
def test_release_forced_holds_uses_guard_verdict(
    monkeypatch: pytest.MonkeyPatch, guard_reason: str | None, disposition: str
) -> None:
    """Forced holds release only on a None guard verdict."""
    allocator = _allocator()
    hold_key = forced_release_hold_key("identity-a", "entry-a", "front", 1)
    allocator._registry.records["1234"] = _record(
        "1234", _owner("entry-a", hold_key, lock_observed=True)
    )
    allocator._registry.rebuild_index()
    calls: list[str | None] = []

    def guard(*_args: Any, forced_release: object = None, **_kwargs: Any) -> str | None:
        """Record the exemption and return the parametrized verdict."""
        calls.append(getattr(forced_release, "identity_key", None))
        return guard_reason

    monkeypatch.setattr(allocator, "_release_guard_reason", guard)
    outcomes = release_forced_holds(allocator, _cycle())
    assert calls == [hold_key]
    assert outcomes[0].disposition == disposition
    assert outcomes[0].retention_reason == guard_reason
    assert outcomes[0].retention_reason != "adoption_conflict"
    assert (allocator._registry.code_for_identity(hold_key) is None) is (
        guard_reason is None
    )


async def test_resolve_cycle_orders_reissue_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cycle order is re-home, adopt, allocate, sweep, release, save."""
    allocator = _allocator()
    calls: list[str] = []

    def apply(*_args: Any, **_kwargs: Any) -> tuple[list[Any], list[Any]]:
        """Record the re-home step."""
        calls.append("rehome")
        return [], []

    def adopt(request: AdoptionRequest) -> object:
        """Record the adoption step."""
        calls.append("adopt")
        return SimpleNamespace(code=request.code, origin=AllocationOrigin.ADOPTED)

    def sweep(_observation: CycleObservation, _active_keys: set[str]) -> object:
        """Record the sweep step."""
        calls.append("sweep")
        return SimpleNamespace(released=[], retained=[])

    def release(*_args: Any, **_kwargs: Any) -> list[Any]:
        """Record the release step."""
        calls.append("release")
        return []

    monkeypatch.setattr(
        "custom_components.rental_control.allocator.reissue.apply_forced_reissues",
        apply,
    )

    def allocate(_allocator: DoorCodeAllocator, request: AllocationRequest) -> object:
        """Record the allocation step."""
        calls.append("allocate")
        return SimpleNamespace(
            code=request.preferred_code,
            origin=AllocationOrigin.PREFERRED,
            reason=None,
        )

    monkeypatch.setattr(
        "custom_components.rental_control.allocator.reissue.release_forced_holds",
        release,
    )
    monkeypatch.setattr(
        "custom_components.rental_control.allocator.issuance.allocate_request",
        allocate,
    )
    monkeypatch.setattr(allocator, "_adopt_unlocked", adopt)
    monkeypatch.setattr(allocator, "_sweep_unlocked", sweep)
    allocator._store = cast(
        Any, SimpleNamespace(async_save=lambda _registry: calls.append("save"))
    )

    await allocator.async_resolve_cycle(
        _cycle(
            forced_reissues=(_D("entry-a", "identity-a", "front", 1),),
            adoptions=[AdoptionRequest("entry-a", "identity-b", "2222", 4, "front", 2)],
            allocations=[_request("identity-a", "1234")],
            active_keys={"identity-a"},
        )
    )

    assert calls == ["rehome", "adopt", "allocate", "sweep", "release", "save"]


async def test_same_cycle_hold_release_is_persisted() -> None:
    """The single save observes both the re-home and same-cycle release."""
    saved_identities: list[set[str]] = []
    allocator = _allocator()
    allocator._store = cast(
        Any,
        SimpleNamespace(
            async_save=lambda registry: saved_identities.append(
                set(registry.by_identity)
            )
        ),
    )
    allocator._registry.records["1234"] = _record(
        "1234", _owner("entry-a", "identity-a", lockname=None, slot=None)
    )
    allocator._registry.rebuild_index()

    result = await allocator.async_resolve_cycle(
        _cycle(
            observation=CycleObservation("entry-a", None, frozenset(), {}, frozenset()),
            forced_reissues=(_D("entry-a", "identity-a", None, None),),
            allocations=[_request("identity-a", "5678", lockname=None, slot=None)],
            active_keys={"identity-a"},
        )
    )

    assert len(saved_identities) == 1
    assert saved_identities[0] == {"identity-a"}
    assert any(outcome.disposition == "released" for outcome in result.reissues)
    assert allocator._registry.code_for_identity("identity-a") == "5678"


async def test_alias_blocked_rollback_restores_removed_primary() -> None:
    """Alias-backed rollback restores both the alias and stale primary."""
    allocator = _allocator()
    alias_key = observed_alias_key(
        AdoptionRequest("entry-a", "identity-a", "1", 1, "front", 1)
    )
    allocator._registry.records["0"] = _record(
        "0", _owner("entry-a", "identity-a"), code_length=1
    )
    allocator._registry.records["1"] = _record(
        "1", _owner("entry-a", alias_key), code_length=1
    )
    allocator._registry.rebuild_index()
    result = await allocator.async_resolve_cycle(
        _cycle(
            observation=CycleObservation(
                "entry-a", "front", frozenset({1}), {"1": 1}, frozenset()
            ),
            forced_reissues=(_D("entry-a", "identity-a", "front", 1),),
            allocations=[],
            active_keys={"identity-a"},
        )
    )
    assert result.reissues[0].retention_reason == "no_allocation_request"
    assert allocator._registry.code_for_identity("identity-a") == "0"
    assert allocator._registry.code_for_identity(alias_key) == "1"


async def test_registry_lost_fails_closed_without_durable_hold_mutation() -> None:
    """Registry-lost cycles do not create or release forced holds."""
    allocator = _allocator()
    allocator._registry_lost = True
    allocator._registry.records["1234"] = _record(
        "1234", _owner("entry-a", "identity-a", lock_observed=True)
    )
    allocator._registry.rebuild_index()
    before_identities = dict(allocator._registry.by_identity)

    result = await allocator.async_resolve_cycle(
        _cycle(
            forced_reissues=(_D("entry-a", "identity-a", "front", 1),),
            allocations=[_request("identity-a", "5678")],
            active_keys={"identity-a"},
        )
    )

    assert result.reissues[0].disposition == "failed"
    assert result.reissues[0].retention_reason == "recovery_fail_closed"
    assert allocator._registry.by_identity == before_identities
    assert not any(
        is_forced_release_hold(key) for key in allocator._registry.by_identity
    )


async def test_exhaustion_rolls_back_staged_hold() -> None:
    """A failed replacement restores the original owner and removes the hold."""
    allocator = _allocator()
    allocator._registry.records["0"] = _record(
        "0", _owner("entry-a", "identity-a"), code_length=1
    )
    for digit in range(1, 10):
        allocator._registry.records[str(digit)] = _record(
            str(digit),
            _owner(f"entry-{digit}", f"taken-{digit}"),
            code_length=1,
        )
    allocator._registry.rebuild_index()

    result = await allocator.async_resolve_cycle(
        _cycle(
            observation=CycleObservation(
                "entry-a", "front", frozenset({1}), {}, frozenset()
            ),
            forced_reissues=(_D("entry-a", "identity-a", "front", 1),),
            allocations=[_request("identity-a", "1", code_length=1)],
            active_keys={"identity-a"},
        )
    )

    assert result.allocated["identity-a"].reason == "exhausted"
    assert result.reissues[0].disposition == "failed"
    assert result.reissues[0].retention_reason == "exhausted"
    assert allocator._registry.code_for_identity("identity-a") == "0"
    hold_key = forced_release_hold_key("identity-a", "entry-a", "front", 1)
    assert allocator._registry.code_for_identity(hold_key) is None


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


def _owner(
    entry_id: str,
    identity_key: str,
    *,
    origin: AllocationOrigin = AllocationOrigin.ADOPTED,
    lockname: str | None = "front",
    slot: int | None = 1,
    lock_observed: bool = True,
    first_seen: str = _NOW,
    last_seen: str = _NOW,
) -> AllocationOwner:
    """Build one registry owner."""
    return AllocationOwner(
        entry_id=entry_id,
        identity_key=identity_key,
        origin=origin,
        lockname=lockname,
        slot=slot,
        lock_observed=lock_observed,
        first_seen=first_seen,
        last_seen=last_seen,
    )


def _record(
    code: str, owner: AllocationOwner, *, code_length: int | None = None
) -> AllocationRecord:
    """Build one registry record."""
    length = len(code) if code_length is None else code_length
    return AllocationRecord(
        code=code,
        code_ref=f"ref-{code}",
        encoding_salt_value=owner.entry_id,
        code_length=length,
        owners=[owner],
        created_at=_NOW,
        updated_at=_NOW,
    )


def _request(
    identity_key: str,
    preferred_code: str,
    *,
    code_length: int | None = None,
    lockname: str | None = "front",
    slot: int | None = 1,
) -> AllocationRequest:
    """Build one allocation request."""
    return AllocationRequest(
        entry_id="entry-a",
        identity_key=identity_key,
        preferred_code=preferred_code,
        code_length=len(preferred_code) if code_length is None else code_length,
        lockname=lockname,
        slot=slot,
    )


def _cycle(
    *,
    observation: CycleObservation | None = None,
    adoptions: list[AdoptionRequest] | None = None,
    allocations: list[AllocationRequest] | None = None,
    active_keys: set[str] | None = None,
    forced_reissues: tuple[_D, ...] = (),
) -> CycleRequest:
    """Build one cycle request."""
    return CycleRequest(
        observation=observation
        or CycleObservation("entry-a", "front", frozenset({1}), {}, frozenset()),
        adoptions=[] if adoptions is None else adoptions,
        rekeys=[],
        allocations=[] if allocations is None else allocations,
        active_keys=set() if active_keys is None else active_keys,
        forced_reissues=forced_reissues,
    )
