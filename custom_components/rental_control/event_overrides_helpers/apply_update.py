# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Pure update-times and overwrite helpers for EventOverrides."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def build_update_time_suppression(lockname: str, slot: int, res: Any) -> dict[str, str]:
    """Return the state-suppression payload for update-times."""
    return {
        f"datetime.{lockname}_code_slot_{slot}_date_range_start": res.buffered_start.isoformat(),
        f"datetime.{lockname}_code_slot_{slot}_date_range_end": res.buffered_end.isoformat(),
    }


def parse_drift_fields(reason: str | None) -> list[str]:
    """Return parsed drift field names from an overwrite reason string."""
    if not reason or not reason.startswith("drifted fields: "):
        return []
    return [
        field.strip()
        for field in reason[len("drifted fields: ") :].split(",")
        if field.strip()
    ]


def build_replacement_plan_id(slot: int, uuid4: Callable[[], object]) -> str:
    """Return the synthetic plan id used for clear-before-replace set calls."""
    return f"replace-{slot}-{uuid4()}"
