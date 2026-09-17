# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Regression tests for forced-release guard scoping."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from typing import cast

import pytest

from custom_components.rental_control.allocator import services as services_module
from custom_components.rental_control.allocator.allocator import DoorCodeAllocator
from custom_components.rental_control.allocator.models import AllocationOrigin
from custom_components.rental_control.allocator.models import AllocationOwner
from custom_components.rental_control.allocator.models import AllocationRecord
from custom_components.rental_control.allocator.models import AllocationRequest
from custom_components.rental_control.allocator.models import CycleObservation
from custom_components.rental_control.allocator.models import ForcedReleaseExemption
from custom_components.rental_control.allocator.reissue import forced_release_hold_key

_NOW = "2026-09-17T12:00:00+00:00"
_CODE = "1234"


@pytest.fixture(autouse=True)
def _patch_notifications(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent orphan reporting from touching Home Assistant internals."""
    monkeypatch.setattr(services_module, "async_create", lambda *args, **kwargs: None)
    monkeypatch.setattr(services_module, "async_dismiss", lambda *args, **kwargs: None)


def test_default_guard_retains_two_owner_record() -> None:
    """Without an exemption, conflict records remain retained."""
    allocator = _allocator()
    record = _record(owner_keys=("identity-a", "identity-b"))

    reason = allocator._release_guard_reason(record, [record.owners[0]], [])

    assert reason == "adoption_conflict"


@pytest.mark.parametrize(
    "exemption",
    [
        ForcedReleaseExemption(_CODE, "entry-a", "wrong-key"),
        ForcedReleaseExemption(_CODE, "wrong-entry", "identity-a"),
        ForcedReleaseExemption("5678", "entry-a", "identity-a"),
    ],
)
def test_mismatched_exemptions_are_inert(
    exemption: ForcedReleaseExemption,
) -> None:
    """Exemptions that fail any identifying field do not weaken the guard."""
    allocator = _allocator()
    record = _record(owner_keys=("identity-a", "identity-b"))

    reason = allocator._release_guard_reason(
        record, [record.owners[0]], [], forced_release=exemption
    )

    assert reason == "adoption_conflict"


def test_non_hold_identity_cannot_be_exempted() -> None:
    """A live reservation identity is not exempted even with matching fields."""
    allocator = _allocator()
    record = _record(owner_keys=("identity-a", "identity-b"))

    reason = allocator._release_guard_reason(
        record,
        [record.owners[0]],
        [],
        forced_release=ForcedReleaseExemption(_CODE, "entry-a", "identity-a"),
    )

    assert reason == "adoption_conflict"


def test_exemption_requires_one_owner_under_evaluation() -> None:
    """An exemption never applies to a multi-owner evaluation list."""
    allocator = _allocator()
    hold_key = forced_release_hold_key("identity-a", "entry-a", "front", 1)
    record = _record(owner_keys=(hold_key, "identity-b"))

    reason = allocator._release_guard_reason(
        record,
        list(record.owners),
        [_covered_without_code()],
        forced_release=ForcedReleaseExemption(_CODE, "entry-a", hold_key),
    )

    assert reason == "adoption_conflict"


def test_unverifiable_lock_survives_matching_exemption() -> None:
    """A matching exemption does not bypass missing physical coverage."""
    allocator = _allocator()
    hold_key = forced_release_hold_key("identity-a", "entry-a", "front", 1)
    record = _record(owner_keys=(hold_key, "identity-b"))

    reason = allocator._release_guard_reason(
        record,
        [record.owners[0]],
        [],
        forced_release=ForcedReleaseExemption(_CODE, "entry-a", hold_key),
    )

    assert reason == "unverifiable_lock"


@pytest.mark.parametrize(
    "observation",
    [
        CycleObservation(
            entry_id="entry-a",
            lockname="front",
            managed_slots=frozenset({1}),
            observed_codes={_CODE: 1},
            unreadable_slots=frozenset(),
        ),
        CycleObservation(
            entry_id="entry-a",
            lockname="front",
            managed_slots=frozenset({1}),
            observed_codes={},
            unreadable_slots=frozenset({1}),
        ),
    ],
)
def test_physical_programmed_state_survives_matching_exemption(
    observation: CycleObservation,
) -> None:
    """Observed and unreadable slots still retain exempted hold owners."""
    allocator = _allocator()
    hold_key = forced_release_hold_key("identity-a", "entry-a", "front", 1)
    record = _record(owner_keys=(hold_key, "identity-b"))

    reason = allocator._release_guard_reason(
        record,
        [record.owners[0]],
        [observation],
        forced_release=ForcedReleaseExemption(_CODE, "entry-a", hold_key),
    )

    assert reason == "code_still_programmed"


def test_known_free_slot_does_not_retain_matching_exemption() -> None:
    """A covered readable slot without the code permits the hold release."""
    allocator = _allocator()
    hold_key = forced_release_hold_key("identity-a", "entry-a", "front", 1)
    record = _record(owner_keys=(hold_key, "identity-b"))

    reason = allocator._release_guard_reason(
        record,
        [record.owners[0]],
        [_covered_without_code()],
        forced_release=ForcedReleaseExemption(_CODE, "entry-a", hold_key),
    )

    assert reason is None


async def test_only_named_hold_owner_is_releasable_from_conflict() -> None:
    """A forced exemption releases only the targeted hold owner."""
    allocator = _allocator()
    hold_key = forced_release_hold_key("identity-a", "entry-a", "front", 1)
    allocator._registry.records[_CODE] = _record(owner_keys=(hold_key, "identity-b"))
    allocator._registry.rebuild_index()
    record = allocator._registry.records[_CODE]

    reason = allocator._release_guard_reason(
        record,
        [record.owners[0]],
        [_covered_without_code()],
        forced_release=ForcedReleaseExemption(_CODE, "entry-a", hold_key),
    )
    if reason is None:
        allocator._registry.release(hold_key)

    assert allocator._registry.code_for_identity(hold_key) is None
    assert allocator._registry.code_for_identity("identity-b") == _CODE
    assert not allocator._registry.is_available(_CODE, 4, "identity-c")


@pytest.mark.parametrize(
    ("observation", "expected_released", "expected_retained", "reason"),
    [
        (
            CycleObservation(
                entry_id="entry-a",
                lockname="front",
                managed_slots=frozenset({1}),
                observed_codes={},
                unreadable_slots=frozenset(),
            ),
            ["identity-a"],
            [],
            None,
        ),
        (
            CycleObservation(
                entry_id="entry-a",
                lockname="front",
                managed_slots=frozenset({1}),
                observed_codes={_CODE: 1},
                unreadable_slots=frozenset(),
            ),
            [],
            ["identity-a"],
            "code_still_programmed",
        ),
        (
            CycleObservation(
                entry_id="entry-a",
                lockname="front",
                managed_slots=frozenset({1}),
                observed_codes={},
                unreadable_slots=frozenset({1}),
            ),
            [],
            ["identity-a"],
            "code_still_programmed",
        ),
        (
            CycleObservation(
                entry_id="entry-a",
                lockname="front",
                managed_slots=frozenset({2}),
                observed_codes={},
                unreadable_slots=frozenset(),
            ),
            [],
            ["identity-a"],
            "unverifiable_lock",
        ),
    ],
)
async def test_sweep_release_path_keeps_existing_guard_behaviour(
    observation: CycleObservation,
    expected_released: list[str],
    expected_retained: list[str],
    reason: str | None,
) -> None:
    """The ordinary sweep path keeps the pre-existing guard semantics."""
    allocator = _allocator()
    await allocator.async_allocate(_request("identity-a"))

    report = await allocator.async_sweep(observation, active_keys=set())

    assert [item.identity_key for item in report.released] == expected_released
    assert [item.identity_key for item in report.retained] == expected_retained
    assert [item.reason for item in report.retained] == (
        [reason] if reason is not None else []
    )


async def test_mark_entry_removed_keeps_existing_guard_behaviour() -> None:
    """Entry removal still uses the full guard and passes no exemption."""
    allocator = _allocator()
    await allocator.async_allocate(_request("identity-a"))

    report = await allocator.async_mark_entry_removed("entry-a")

    assert report.released == []
    assert [item.reason for item in report.retained] == ["unverifiable_lock"]
    assert allocator._registry.code_for_identity("identity-a") == _CODE


async def test_orphan_cleanup_keeps_existing_guard_behaviour() -> None:
    """Orphan cleanup still uses the full guard and passes no exemption."""
    allocator = _allocator()
    await allocator.async_allocate(_request("identity-a"))

    report = await allocator.async_clear_orphans(set(), [_covered_without_code()])

    assert [item.identity_key for item in report.cleared] == ["identity-a"]
    assert report.retained == []
    assert allocator._registry.code_for_identity("identity-a") is None


async def test_sweep_skips_hold_namespace_owner() -> None:
    """The ordinary inactive-owner sweep never evaluates hold identities."""
    allocator = _allocator()
    hold_key = forced_release_hold_key("identity-a", "entry-a", "front", 1)
    allocator._registry.records[_CODE] = _record(owner_keys=(hold_key,))
    allocator._registry.rebuild_index()

    report = await allocator.async_sweep(_covered_without_code(), active_keys=set())

    assert report.released == []
    assert report.retained == []
    assert allocator._registry.code_for_identity(hold_key) == _CODE


async def test_two_owner_record_still_retained_by_ordinary_paths() -> None:
    """Ordinary release paths still retain conflict records."""
    sweep_allocator = _allocator_with_conflict()
    sweep = await sweep_allocator.async_sweep(_covered_without_code(), set())
    remove_allocator = _allocator_with_conflict()
    removed = await remove_allocator.async_mark_entry_removed("entry-a")
    orphan_allocator = _allocator_with_conflict()
    orphan = await orphan_allocator.async_clear_orphans(
        set(), [_covered_without_code()]
    )

    assert [item.reason for item in sweep.retained] == [
        "adoption_conflict",
        "adoption_conflict",
    ]
    assert [item.reason for item in removed.retained] == [
        "adoption_conflict",
        "adoption_conflict",
    ]
    assert [item.reason for item in orphan.retained] == [
        "adoption_conflict",
        "adoption_conflict",
    ]


def test_no_phase_two_production_exemption_activation() -> None:
    """Phase two adds the guard shape without enabling an ordinary call path."""
    production = Path("custom_components/rental_control")
    sources = {
        path: path.read_text(encoding="utf-8")
        for path in production.rglob("*.py")
        if "__pycache__" not in path.parts
    }

    constructor_sites = [
        path for path, text in sources.items() if "ForcedReleaseExemption(" in text
    ]
    call_sites = [
        path
        for path, text in sources.items()
        if "forced_release=" in text
        and path != Path("custom_components/rental_control/allocator/allocator.py")
    ]

    assert constructor_sites == []
    assert call_sites == []


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


def _allocator_with_conflict() -> DoorCodeAllocator:
    """Build an allocator containing one ordinary two-owner record."""
    allocator = _allocator()
    allocator._registry.records[_CODE] = _record(
        owner_keys=("identity-a", "identity-b")
    )
    allocator._registry.rebuild_index()
    return allocator


def _request(identity_key: str) -> AllocationRequest:
    """Build a simple allocation request."""
    return AllocationRequest(
        entry_id="entry-a",
        identity_key=identity_key,
        preferred_code=_CODE,
        code_length=4,
        lockname="front",
        slot=1,
    )


def _record(owner_keys: tuple[str, ...]) -> AllocationRecord:
    """Build one allocation record with the requested owner identities."""
    return AllocationRecord(
        code=_CODE,
        code_ref="ref-1234",
        encoding_salt_value="entry-a",
        owners=[
            AllocationOwner(
                entry_id="entry-a",
                identity_key=key,
                origin=AllocationOrigin.ADOPTED,
                lockname="front",
                slot=index,
                lock_observed=True,
                first_seen=_NOW,
                last_seen=_NOW,
            )
            for index, key in enumerate(owner_keys, start=1)
        ],
    )


def _covered_without_code() -> CycleObservation:
    """Build a readable observation proving the code is absent."""
    return CycleObservation(
        entry_id="entry-a",
        lockname="front",
        managed_slots=frozenset({1}),
        observed_codes={},
        unreadable_slots=frozenset(),
    )
