# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Action classification helpers for reconciliation planners."""

from __future__ import annotations

from .enums import ActionKind
from .plan_models import ManagedSlot
from .plan_models import Reservation


def _compute_drift_fields(ms: ManagedSlot, res: Reservation) -> list[str]:
    """Return the fields that differ between actual and desired state."""
    fields: list[str] = []
    if ms.actual_name is not None and ms.actual_name != res.display_slot_name:
        fields.append("name")
    if ms.actual_code_present is False:
        fields.append("code")
    if ms.actual_start is not None and ms.actual_start != res.buffered_start:
        fields.append("start")
    if ms.actual_end is not None and ms.actual_end != res.buffered_end:
        fields.append("end")
    if ms.date_range_enabled is False:
        fields.append("date_range_enabled")
    return fields


def classify_matched_desired_slot(
    ms: ManagedSlot, desired_res: Reservation | None
) -> tuple[ActionKind, str | None]:
    """Classify an occupied matched slot with desired reservation context."""
    if desired_res is None:
        return ActionKind.NOOP, None
    drift_fields = _compute_drift_fields(ms, desired_res)
    code_drift = ms.actual_code is not None and ms.actual_code != desired_res.slot_code
    name_drift = (
        ms.actual_name is not None and ms.actual_name != desired_res.display_slot_name
    )
    non_date_drift = [field for field in drift_fields if field not in {"start", "end"}]
    if code_drift or name_drift or non_date_drift:
        fields = set(drift_fields)
        if code_drift:
            fields.add("code")
        if name_drift:
            fields.add("name")
        return (
            ActionKind.OVERWRITE_MANUAL_CHANGE,
            "drifted fields: " + ", ".join(sorted(fields)),
        )
    if (ms.actual_start is not None or ms.actual_end is not None) and (
        ms.actual_start != desired_res.buffered_start
        or ms.actual_end != desired_res.buffered_end
    ):
        return ActionKind.UPDATE_TIMES, None
    return ActionKind.NOOP, None
