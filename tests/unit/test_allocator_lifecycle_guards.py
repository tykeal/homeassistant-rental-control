# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Recheck snapshot readiness after a protected operation waits for a commit."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.rental_control.allocator import protection_gate
from custom_components.rental_control.allocator.allocator import DoorCodeAllocator
from custom_components.rental_control.allocator.models import AllocationOrigin
from custom_components.rental_control.allocator.models import AllocationOwner
from custom_components.rental_control.allocator.models import AllocationRecord
from custom_components.rental_control.allocator.models import CycleObservation
from custom_components.rental_control.allocator.models import OrphanCleanupReport
from custom_components.rental_control.allocator.models import ReleaseReport
from custom_components.rental_control.allocator.registry import AllocationRegistry
from custom_components.rental_control.const import DOMAIN
from custom_components.rental_control.const import PROTECTED_REGISTRY_STORE

_NOW = "2026-11-21T00:00:00+00:00"


@pytest.mark.parametrize("operation", ["sweep", "remove", "cleanup"])
async def test_lifecycle_rechecks_readiness_before_mutating(
    hass,
    operation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A snapshot that turns unready after the initial wait retains owners."""
    allocator = DoorCodeAllocator(hass)
    allocator._protection_required = True
    owner = AllocationOwner(
        entry_id="entry-a",
        identity_key="owner-a",
        origin=AllocationOrigin.PREFERRED,
        lock_observed=False,
        first_seen=_NOW,
        last_seen=_NOW,
    )
    allocator._registry = AllocationRegistry(
        code_ref_salt="lifecycle-gate-salt",
        records={
            "1234": AllocationRecord(
                code="1234",
                code_ref="masked-ref",
                encoding_salt_value="entry-a",
                owners=[owner],
                created_at=_NOW,
                updated_at=_NOW,
            )
        },
    )
    before = deepcopy(allocator._registry)
    store = SimpleNamespace(
        async_wait_for_commit_completion=AsyncMock(return_value=True)
    )
    hass.data[DOMAIN] = {PROTECTED_REGISTRY_STORE: store}
    checks = iter((True, False))
    monkeypatch.setattr(
        protection_gate,
        "snapshot_ready",
        lambda _allocator: next(checks),
    )

    report: ReleaseReport | OrphanCleanupReport
    if operation == "sweep":
        report = await allocator.async_sweep(
            CycleObservation("entry-a", None, frozenset(), {}, frozenset()),
            active_keys=set(),
        )
    elif operation == "remove":
        report = await allocator.async_mark_entry_removed("entry-a")
    else:
        report = await allocator.async_clear_orphans(set(), [], dry_run=False)

    assert allocator._registry == before
    assert {outcome.identity_key for outcome in report.retained} == {"owner-a"}
    assert not getattr(report, "released", [])
    assert not getattr(report, "cleared", [])
