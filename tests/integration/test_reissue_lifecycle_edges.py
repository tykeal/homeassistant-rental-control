# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Integration coverage for forced re-issue lifecycle edges."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.rental_control.allocator.models import AllocationRequest
from custom_components.rental_control.allocator.models import CycleRequest
from custom_components.rental_control.coordinator_helpers import reissue

from tests.integration.test_reissue_deferred_release import _patch_notifications
from tests.integration.test_reissue_duplicate_healing import _OLD
from tests.integration.test_reissue_duplicate_healing import _allocation
from tests.integration.test_reissue_duplicate_healing import _allocator
from tests.integration.test_reissue_duplicate_healing import _directive
from tests.integration.test_reissue_duplicate_healing import _observation


async def test_lifecycle_edges_are_conservative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disappeared, changed, exhausted, and restarted targets fail safe."""
    _patch_notifications(monkeypatch)
    coordinator = SimpleNamespace(_entry_id="entry-a", lockname="front")
    pending = reissue.PendingReissue("identity-a", "identity-a", "front", 1)
    reissue.pending_reissues(coordinator)["identity-a"] = pending
    result = reissue.CodeResolutionResult(
        observation=None,
        reissues=(
            reissue.ReissueOutcome(
                "entry-a",
                "identity-a",
                "front",
                1,
                None,
                None,
                None,
                "failed",
                "no_existing_allocation",
            ),
        ),
        consumed_target_keys=("identity-a",),
    )
    reissue.consume_cycle_result(coordinator, result)
    assert reissue.pending_reissues(coordinator) == {}

    allocator = _allocator()
    changed = await allocator.async_resolve_cycle(
        CycleRequest(
            _observation("entry-a", {1: _OLD}),
            [],
            [],
            [_allocation("entry-a", "identity-new", "5678", 1)],
            {"identity-new"},
            forced_reissues=(_directive("entry-a", "identity-old", 1),),
        )
    )
    assert changed.reissues[0].disposition == "no_existing_allocation"

    exhausted = _allocator()
    await exhausted.async_allocate(
        AllocationRequest("entry-a", "identity-a", "0", 1, lockname="front", slot=1)
    )
    for digit in range(1, 10):
        await exhausted.async_allocate(
            AllocationRequest(f"entry-{digit}", f"id-{digit}", str(digit), 1)
        )
    failed = await exhausted.async_resolve_cycle(
        CycleRequest(
            _observation("entry-a", {1: "0"}),
            [],
            [],
            [
                AllocationRequest(
                    "entry-a", "identity-a", "1", 1, lockname="front", slot=1
                )
            ],
            {"identity-a"},
            forced_reissues=(_directive("entry-a", "identity-a", 1),),
        )
    )
    assert failed.reissues[0].retention_reason == "exhausted"
    assert exhausted._registry.code_for_identity("identity-a") == "0"

    restarted = SimpleNamespace()
    assert reissue.current_suppression(restarted) == reissue.ReissueSuppression()
    retry = await exhausted.async_resolve_cycle(
        CycleRequest(_observation("entry-a", {1: "0"}), [], [], [], set())
    )
    assert retry.reissues == ()
