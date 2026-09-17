# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the pure allocation registry."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from typing import cast

import pytest

from custom_components.rental_control.allocator import allocator as allocator_module
from custom_components.rental_control.allocator import services as services_module
from custom_components.rental_control.allocator.allocator import DoorCodeAllocator
from custom_components.rental_control.allocator.models import AdoptionRequest
from custom_components.rental_control.allocator.models import AllocationOrigin
from custom_components.rental_control.allocator.models import AllocationOwner
from custom_components.rental_control.allocator.models import AllocationRecord
from custom_components.rental_control.allocator.models import AllocationRequest
from custom_components.rental_control.allocator.models import CycleObservation
from custom_components.rental_control.allocator.models import CycleRequest
from custom_components.rental_control.allocator.registry import AllocationRegistry

_NOW = "2026-09-16T12:00:00+00:00"


def _request(identity: str, preferred: str = "1234") -> AllocationRequest:
    """Build an allocation request for registry tests."""
    return AllocationRequest(
        entry_id="entry-a",
        identity_key=identity,
        preferred_code=preferred,
        code_length=len(preferred),
        lockname="front",
        slot=1,
    )


def test_allocate_lookup_and_release() -> None:
    """A registry can allocate, look up, and release an owner."""
    registry = AllocationRegistry(code_ref_salt="salt")
    record = registry.allocate(
        _request("identity-a"), "1234", AllocationOrigin.PREFERRED, "ref", _NOW
    )

    assert record.code == "1234"
    assert registry.code_for_identity("identity-a") == "1234"
    assert registry.release("identity-a") is True
    assert registry.code_for_identity("identity-a") is None
    assert registry.records == {}


def test_idempotent_re_request_returns_existing_record() -> None:
    """A known identity keeps its existing code without mutation."""
    registry = AllocationRegistry(code_ref_salt="salt")
    first = registry.allocate(
        _request("identity-a"), "1234", AllocationOrigin.PREFERRED, "ref", _NOW
    )
    second = registry.allocate(
        _request("identity-a", "5678"),
        "5678",
        AllocationOrigin.COLLISION_RESOLVED,
        "newref",
        _NOW,
    )

    assert second is first
    assert registry.code_for_identity("identity-a") == "1234"
    assert first.owners[0].origin is AllocationOrigin.PREFERRED


def test_conflict_recording_makes_code_unavailable() -> None:
    """A code with two owners is retained but cannot be reissued."""
    registry = AllocationRegistry(code_ref_salt="salt")
    registry.allocate(
        _request("identity-a"), "1234", AllocationOrigin.PREFERRED, "ref", _NOW
    )
    registry.add_owner(
        "1234",
        4,
        AllocationOwner(
            entry_id="entry-b",
            identity_key="identity-b",
            origin=AllocationOrigin.ADOPTED,
            lockname="front",
            slot=2,
            lock_observed=True,
            first_seen=_NOW,
            last_seen=_NOW,
        ),
        "ref",
        _NOW,
    )

    assert len(registry.conflicts()) == 1
    assert not registry.is_available("1234", 4, "identity-c")


def test_code_length_filtering_blocks_other_lengths() -> None:
    """Records are only available to requests of their stored length."""
    registry = AllocationRegistry(code_ref_salt="salt")
    registry.allocate(
        _request("identity-a"), "1234", AllocationOrigin.PREFERRED, "ref", _NOW
    )

    assert not registry.is_available("1234", 4, "identity-b")
    with pytest.raises(ValueError, match="code must be decimal"):
        registry.is_available("1234", 6, "identity-b")


def test_rejects_malformed_record_inputs() -> None:
    """Malformed codes and duplicate identities are rejected."""
    with pytest.raises(ValueError, match="code must be decimal"):
        AllocationRecord(
            code="12ab",
            code_ref="ref",
            encoding_salt_value="entry-a",
            code_length=4,
            owners=[
                AllocationOwner(
                    entry_id="entry-a",
                    identity_key="identity-a",
                    origin=AllocationOrigin.PREFERRED,
                )
            ],
        )
    with pytest.raises(ValueError, match="multiple records"):
        AllocationRegistry(
            records={
                "1234": AllocationRecord(
                    code="1234",
                    code_ref="ref-a",
                    encoding_salt_value="entry-a",
                    code_length=4,
                    owners=[
                        AllocationOwner(
                            entry_id="entry-a",
                            identity_key="identity-a",
                            origin=AllocationOrigin.PREFERRED,
                        )
                    ],
                ),
                "5678": AllocationRecord(
                    code="5678",
                    code_ref="ref-b",
                    encoding_salt_value="entry-b",
                    code_length=4,
                    owners=[
                        AllocationOwner(
                            entry_id="entry-b",
                            identity_key="identity-a",
                            origin=AllocationOrigin.ADOPTED,
                        )
                    ],
                ),
            }
        )


def test_rejects_duplicate_identity_in_same_record() -> None:
    """A single record cannot contain the same owner identity twice."""
    with pytest.raises(ValueError, match="multiple records"):
        AllocationRegistry(
            records={
                "1234": AllocationRecord(
                    code="1234",
                    code_ref="ref",
                    encoding_salt_value="entry-a",
                    code_length=4,
                    owners=[
                        AllocationOwner(
                            entry_id="entry-a",
                            identity_key="identity-a",
                            origin=AllocationOrigin.PREFERRED,
                        ),
                        AllocationOwner(
                            entry_id="entry-a",
                            identity_key="identity-a",
                            origin=AllocationOrigin.ADOPTED,
                        ),
                    ],
                )
            }
        )


@pytest.mark.parametrize(
    ("lockname", "slot"),
    [
        ("front", None),
        (None, 1),
        ("front", True),
    ],
)
def test_rejects_invalid_physical_owner_pair(
    lockname: str | None, slot: int | None
) -> None:
    """Physical owner metadata must be complete and non-boolean."""
    with pytest.raises(ValueError, match="slot|lockname"):
        AllocationOwner(
            entry_id="entry-a",
            identity_key="identity-a",
            origin=AllocationOrigin.ADOPTED,
            lockname=lockname,
            slot=slot,
        )


@pytest.mark.parametrize("code_length", [True, 1.0, 0])
def test_rejects_invalid_code_lengths(code_length: object) -> None:
    """Records and registry APIs accept only positive integer lengths."""
    with pytest.raises(ValueError, match="code_length"):
        AllocationRecord(
            code="1",
            code_ref="ref",
            encoding_salt_value="entry-a",
            code_length=code_length,  # type: ignore[arg-type]
            owners=[
                AllocationOwner(
                    entry_id="entry-a",
                    identity_key="identity-a",
                    origin=AllocationOrigin.PREFERRED,
                )
            ],
        )

    with pytest.raises(ValueError, match="code_length"):
        AllocationRegistry().is_available(
            "1",
            code_length,  # type: ignore[arg-type]
            "identity-a",
        )


async def test_sweep_retains_observed_programmed_code() -> None:
    """Sweep keeps inactive allocations still visible on the lock."""
    allocator = _allocator()
    await allocator.async_allocate(_request("identity-a"))

    report = await allocator.async_sweep(
        CycleObservation(
            entry_id="entry-a",
            lockname="front",
            managed_slots=frozenset({1}),
            observed_codes={"1234": 1},
            unreadable_slots=frozenset(),
        ),
        active_keys=set(),
    )

    assert report.released == []
    assert report.retained[0].reason == "code_still_programmed"
    assert allocator._registry.code_for_identity("identity-a") == "1234"


async def test_sweep_retains_unreadable_programmed_code() -> None:
    """Sweep treats unreadable owner slots as possibly still programmed."""
    allocator = _allocator()
    await allocator.async_allocate(_request("identity-a"))

    report = await allocator.async_sweep(
        CycleObservation(
            entry_id="entry-a",
            lockname="front",
            managed_slots=frozenset({1}),
            observed_codes={},
            unreadable_slots=frozenset({1}),
        ),
        active_keys=set(),
    )

    assert report.released == []
    assert report.retained[0].reason == "code_still_programmed"
    assert allocator._registry.code_for_identity("identity-a") == "1234"


async def test_sweep_releases_absent_and_reuses_preferred() -> None:
    """A released inactive allocation immediately frees its preferred code."""
    allocator = _allocator()
    await allocator.async_allocate(_request("identity-a"))

    report = await allocator.async_sweep(
        CycleObservation(
            entry_id="entry-a",
            lockname="front",
            managed_slots=frozenset({1}),
            observed_codes={},
            unreadable_slots=frozenset(),
        ),
        active_keys=set(),
    )
    result = await allocator.async_allocate(_request("identity-b"))

    assert [outcome.identity_key for outcome in report.released] == ["identity-a"]
    assert result.code == "1234"
    assert allocator._registry.code_for_identity("identity-a") is None


async def test_mark_entry_removed_releases_lockless_owner() -> None:
    """Removed lockless entries have no physical code and release immediately."""
    allocator = _allocator()
    await allocator.async_allocate(
        AllocationRequest(
            entry_id="entry-a",
            identity_key="identity-a",
            preferred_code="1234",
            code_length=4,
        )
    )

    report = await allocator.async_mark_entry_removed("entry-a")

    assert [outcome.identity_key for outcome in report.released] == ["identity-a"]
    assert report.retained == []
    assert allocator._registry.records == {}


async def test_release_paths_share_guard_behaviour(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sweep, removal, and orphan cleanup use the same guard decisions."""
    monkeypatch.setattr(allocator_module, "async_create", lambda *args, **kwargs: None)
    monkeypatch.setattr(services_module, "async_create", lambda *args, **kwargs: None)
    observation = CycleObservation(
        entry_id="entry-a",
        lockname="front",
        managed_slots=frozenset({1}),
        observed_codes={},
        unreadable_slots=frozenset({1}),
    )

    sweep_allocator = _allocator()
    await sweep_allocator.async_allocate(_request("identity-a"))
    sweep = await sweep_allocator.async_sweep(observation, active_keys=set())

    remove_allocator = _allocator()
    await remove_allocator.async_allocate(_request("identity-a"))
    remove = await remove_allocator.async_mark_entry_removed("entry-a")

    orphan_allocator = _allocator()
    await orphan_allocator.async_allocate(_request("identity-a"))
    orphan = await orphan_allocator.async_clear_orphans(set(), [observation])

    assert sweep.retained[0].reason == "code_still_programmed"
    assert remove.retained[0].reason == "unverifiable_lock"
    assert orphan.retained[0].reason == "code_still_programmed"


async def test_release_guard_requires_slot_coverage() -> None:
    """A lock observation that omits the owner's slot is not proof of removal."""
    allocator = _allocator()
    await allocator.async_allocate(_request("identity-a"))

    report = await allocator.async_sweep(
        CycleObservation(
            entry_id="entry-a",
            lockname="front",
            managed_slots=frozenset({2}),
            observed_codes={},
            unreadable_slots=frozenset(),
        ),
        active_keys=set(),
    )

    assert report.released == []
    assert report.retained[0].reason == "unverifiable_lock"
    assert allocator._registry.code_for_identity("identity-a") == "1234"


async def test_allocator_diagnostics_scrub_codes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Allocator diagnostics expose refs, conflicts, and orphans without codes."""
    monkeypatch.setattr(allocator_module, "async_create", lambda *args, **kwargs: None)
    allocator = _allocator()
    await allocator.async_allocate(_request("identity-alpha", "9876"))
    await allocator.async_adopt(
        AdoptionRequest(
            entry_id="entry-b",
            identity_key="identity-beta",
            code="9876",
            code_length=4,
            lockname="front",
            slot=2,
        )
    )

    snapshot = allocator.diagnostics
    serialized = json.dumps(snapshot)

    assert snapshot["record_count"] == 1
    assert snapshot["owner_count"] == 2
    assert snapshot["conflict_count"] == 1
    assert snapshot["orphans"] == ["entry-a", "entry-b"]
    assert snapshot["records"][0]["code_ref"] == allocator.code_ref("9876")
    assert snapshot["records"][0]["owner_count"] == 2
    assert snapshot["conflicts"][0]["code_ref"] == allocator.code_ref("9876")
    assert "9876" not in serialized


async def test_rekey_preserves_code_across_identity_change() -> None:
    """Historical fingerprint rekeying keeps the allocation deterministic."""
    allocator = _allocator()
    await allocator.async_allocate(_request("old-key"))

    result = await allocator.async_resolve_cycle(
        CycleRequest(
            observation=CycleObservation(
                entry_id="entry-a",
                lockname="front",
                managed_slots=frozenset({1}),
                observed_codes={},
                unreadable_slots=frozenset(),
            ),
            adoptions=[],
            rekeys=[("old-key", "new-key")],
            allocations=[_request("new-key", "5678")],
            active_keys={"new-key"},
        )
    )

    assert result.allocated["new-key"].code == "1234"
    assert allocator._registry.code_for_identity("old-key") is None
    assert allocator._registry.code_for_identity("new-key") == "1234"


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
