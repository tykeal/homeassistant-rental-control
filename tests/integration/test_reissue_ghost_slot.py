# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Integration coverage for forced re-issue of a ghost slot."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.rental_control.allocator.models import AllocationRequest
from custom_components.rental_control.allocator.models import CycleObservation
from custom_components.rental_control.allocator.models import CycleRequest
from custom_components.rental_control.coordinator_helpers import reissue
from custom_components.rental_control.reconciliation import ActionKind
from custom_components.rental_control.reconciliation import compute_desired_plan

from tests.integration.test_reissue_deferred_release import _patch_notifications
from tests.integration.test_reissue_duplicate_healing import _OLD
from tests.integration.test_reissue_duplicate_healing import _START
from tests.integration.test_reissue_duplicate_healing import _allocator
from tests.integration.test_reissue_duplicate_healing import _observation
from tests.integration.test_reissue_duplicate_healing import _slot


async def test_ghost_slot_target_clears_without_ghost_resurrection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bare slot target can clear a bad code with the guarded hold lifecycle."""
    _patch_notifications(monkeypatch)
    allocator = _allocator()
    await allocator.async_allocate(
        AllocationRequest("entry-a", "ghost-id", _OLD, 4, lockname="front", slot=1)
    )
    coordinator = SimpleNamespace(lockname="front")
    persisted = {"ghost-id": {"slot": 1}}
    reissue.pending_reissues(coordinator)["slot:front:1"] = reissue.PendingReissue(
        "slot:front:1", None, "front", 1
    )

    reissue.remove_pending_ghost_targets(coordinator, persisted, set())
    first = await allocator.async_resolve_cycle(
        CycleRequest(
            _observation("entry-a", {1: _OLD}),
            [],
            [],
            [],
            set(),
            forced_reissues=(
                reissue.ForcedReissueDirective("entry-a", None, "front", 1),
            ),
        )
    )
    plan = compute_desired_plan(
        [],
        [_slot(1, _OLD, "Ghost", "ghost-id")],
        1,
        "clear",
        _START,
        entry_id="entry-a",
        lockname="front",
        start_slot=1,
    )

    assert persisted == {}
    assert any(
        outcome.retention_reason == "code_still_programmed"
        for outcome in first.reissues
    )
    assert [action.kind for action in plan.actions] == [ActionKind.CLEAR]

    final = await allocator.async_resolve_cycle(
        CycleRequest(
            CycleObservation("entry-a", "front", frozenset({1}), {}, frozenset()),
            [],
            [],
            [],
            set(),
        )
    )

    assert final.reissues[0].disposition == "released"
    assert allocator._registry.records == {}
