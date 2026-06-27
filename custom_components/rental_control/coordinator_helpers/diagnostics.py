# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Pure diagnostics projection helpers.

The coordinator shell owns the latest desired plan and the event-override
diagnostics snapshot. This module combines and scrubs those projections
without touching Home Assistant. Raw PIN / slot-code values are always
removed before diagnostics leave the coordinator.
"""

from __future__ import annotations

from typing import Any
from typing import cast

from ..reconciliation import DesiredPlan

_CODE_KEYS = frozenset({"slot_code", "pin", "code"})


def scrub_codes(value: Any) -> Any:
    """Return a copy of *value* with raw code-bearing keys removed."""
    if isinstance(value, dict):
        return {
            key: scrub_codes(item)
            for key, item in value.items()
            if key not in _CODE_KEYS
        }
    if isinstance(value, list):
        return [scrub_codes(item) for item in value]
    return value


def build_reconciliation_diagnostics(
    plan: DesiredPlan | None,
    event_overrides_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return the combined, scrubbed reconciliation diagnostics snapshot.

    Args:
        plan: The most recently computed desired plan, or ``None``.
        event_overrides_snapshot: The :class:`EventOverrides` diagnostics
            snapshot, or ``None`` when overrides are not configured.

    Returns:
        Combined diagnostics dict with raw codes scrubbed; empty when no
        plan has been computed.
    """
    result: dict[str, Any] = {}
    if plan is not None:
        result.update(plan.diagnostics)
    if event_overrides_snapshot is not None:
        result["event_overrides"] = event_overrides_snapshot
    return cast("dict[str, Any]", scrub_codes(result))
