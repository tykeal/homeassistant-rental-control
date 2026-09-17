# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Integration coverage for deferred forced-release holds."""

from __future__ import annotations

import pytest

from custom_components.rental_control.allocator.models import CycleRequest
from custom_components.rental_control.reconciliation import ActionKind
from custom_components.rental_control.reconciliation import compute_desired_plan

from tests.integration.test_reissue_duplicate_healing import _OLD
from tests.integration.test_reissue_duplicate_healing import _START
from tests.integration.test_reissue_duplicate_healing import _adopt_duplicate
from tests.integration.test_reissue_duplicate_healing import _allocation
from tests.integration.test_reissue_duplicate_healing import _allocator
from tests.integration.test_reissue_duplicate_healing import _directive
from tests.integration.test_reissue_duplicate_healing import _observation
from tests.integration.test_reissue_duplicate_healing import _reservation
from tests.integration.test_reissue_duplicate_healing import _slot


async def test_deferred_release_retries_without_reissuing_old_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stuck old physical code is held, retried, and then released."""
    _patch_notifications(monkeypatch)
    allocator = _allocator()
    await _adopt_duplicate(allocator)
    first = await allocator.async_resolve_cycle(
        CycleRequest(
            _observation("entry-a", {1: _OLD}),
            [],
            [],
            [_allocation("entry-a", "identity-a", _OLD, 1)],
            {"identity-a"},
            forced_reissues=(_directive("entry-a", "identity-a", 1),),
        )
    )
    replacement = first.allocated["identity-a"].code
    assert replacement is not None

    second = await allocator.async_resolve_cycle(
        CycleRequest(
            _observation("entry-a", {1: _OLD}),
            [],
            [],
            [_allocation("entry-a", "identity-a", replacement, 1)],
            {"identity-a"},
        )
    )
    reservation = _reservation("identity-a", "Target", replacement)
    plan = compute_desired_plan(
        [reservation],
        [_slot(1, _OLD, "Target", "identity-a")],
        1,
        "retry",
        _START,
        entry_id="entry-a",
        lockname="front",
        start_slot=1,
    )
    third = await allocator.async_allocate(
        _allocation("entry-c", "identity-c", _OLD, 20)
    )

    assert second.reissues[0].retention_reason == "code_still_programmed"
    assert reservation.slot_code == replacement
    assert [item.kind for item in plan.actions] == [ActionKind.OVERWRITE_MANUAL_CHANGE]
    assert third.code != _OLD

    final = await allocator.async_resolve_cycle(
        CycleRequest(
            _observation("entry-a", {1: replacement}),
            [],
            [],
            [_allocation("entry-a", "identity-a", replacement, 1)],
            {"identity-a"},
        )
    )

    assert final.reissues[0].disposition == "released"
    assert len(allocator._registry.records[_OLD].owners) == 1


def _patch_notifications(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable Home Assistant notification side effects for pure fixtures."""
    monkeypatch.setattr(
        "custom_components.rental_control.allocator.allocator.async_create",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "custom_components.rental_control.allocator.services.async_create",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "custom_components.rental_control.allocator.services.async_dismiss",
        lambda *args, **kwargs: None,
    )
