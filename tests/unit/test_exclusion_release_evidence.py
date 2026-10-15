# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Proof that slot exclusions never weaken code-release evidence.

An exclusion suppresses issuance-gate decisions only. If it were applied
to the shared cycle observation instead, an excluded slot would stop
contributing programmed-code evidence and the allocator could release a
code that is still physically on the lock.
"""

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
from custom_components.rental_control.allocator.models import CycleObservation

_NOW = "2026-10-15T12:00:00+00:00"
_CODE = "1234"
_EXCLUDED_SLOT = 3


@pytest.fixture(autouse=True)
def _patch_notifications(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep orphan reporting away from Home Assistant internals."""
    monkeypatch.setattr(services_module, "async_create", lambda *args, **kwargs: None)
    monkeypatch.setattr(services_module, "async_dismiss", lambda *args, **kwargs: None)


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


def _record() -> AllocationRecord:
    """Build one record owned by an entry against the excluded slot."""
    return AllocationRecord(
        code=_CODE,
        code_ref="ref-1234",
        encoding_salt_value="entry-a",
        owners=[
            AllocationOwner(
                entry_id="entry-a",
                identity_key="identity-a",
                origin=AllocationOrigin.ADOPTED,
                lockname="front",
                slot=_EXCLUDED_SLOT,
                lock_observed=True,
                first_seen=_NOW,
                last_seen=_NOW,
            )
        ],
        code_length=4,
    )


def _observation() -> CycleObservation:
    """Build the shared observation the release path consumes."""
    return CycleObservation(
        entry_id="entry-a",
        lockname="front",
        managed_slots=frozenset({1, 2, _EXCLUDED_SLOT}),
        observed_codes={},
        unreadable_slots=frozenset({_EXCLUDED_SLOT}),
    )


def test_excluded_slot_still_proves_a_code_is_programmed() -> None:
    """An excluded unreadable slot retains its owner's code."""
    allocator = _allocator()
    record = _record()

    reason = allocator._release_guard_reason(record, record.owners, [_observation()])

    assert reason == "code_still_programmed"


async def test_orphan_cleanup_retains_an_excluded_slots_code() -> None:
    """Orphan cleanup refuses to clear a code on an excluded dead slot."""
    allocator = _allocator()
    allocator._registry.records[_CODE] = _record()
    allocator._registry.rebuild_index()

    report = await allocator.async_clear_orphans(set(), [_observation()])

    assert report.cleared == []
    assert _CODE in allocator._registry.records


def test_observation_builder_knows_nothing_of_exclusions() -> None:
    """The shared observation must never be filtered by exclusions."""
    source = Path(
        "custom_components/rental_control/coordinator_helpers/code_allocation.py"
    ).read_text(encoding="utf-8")
    builder = source.split("def build_cycle_observation(", 1)[1].split("\ndef ", 1)[0]

    assert "exclu" not in builder.lower()
