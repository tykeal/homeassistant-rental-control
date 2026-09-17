# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Integration coverage for lockless forced re-issue."""

from __future__ import annotations

import pytest

from custom_components.rental_control.allocator.models import AllocationRequest
from custom_components.rental_control.allocator.models import CycleObservation
from custom_components.rental_control.allocator.models import CycleRequest
from custom_components.rental_control.allocator.models import ForcedReissueDirective

from tests.integration.test_reissue_deferred_release import _patch_notifications
from tests.integration.test_reissue_duplicate_healing import _allocator


async def test_lockless_reissue_releases_hold_same_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lockless replacements publish immediately and release their hold."""
    _patch_notifications(monkeypatch)
    allocator = _allocator()
    await allocator.async_allocate(
        AllocationRequest("entry-a", "identity-a", "1234", 4)
    )

    result = await allocator.async_resolve_cycle(
        CycleRequest(
            CycleObservation("entry-a", None, frozenset(), {}, frozenset()),
            [],
            [],
            [AllocationRequest("entry-a", "identity-a", "5678", 4)],
            {"identity-a"},
            forced_reissues=(
                ForcedReissueDirective("entry-a", "identity-a", None, None),
            ),
        )
    )

    assert result.allocated["identity-a"].code == "5678"
    assert result.reissues[0].disposition == "released"
    assert allocator._registry.code_for_identity("identity-a") == "5678"
    assert "1234" not in allocator._registry.records
