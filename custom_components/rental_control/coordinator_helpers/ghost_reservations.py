# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Ghost-reservation reconstruction helpers for the coordinator."""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Any

from ..const import OPERATION_KIND_CLEAR
from ..const import SLOT_STATUS_OCCUPIED
from ..const import SLOT_STATUS_PENDING_CLEAR
from ..const import SLOT_STATUS_PENDING_SET
from ..reconciliation import Reservation as _Reservation
from ..reconciliation import normalize_slot_name_for_fingerprint
from ..util import dt as _dt
from .models import GhostReservationResult
from .models import ReservationBuildContext
from .models import _format_display_slot_name

_LOGGER = logging.getLogger(__name__)


def _coerce_persisted_datetime(value: Any) -> datetime | None:
    """Return a valid persisted datetime, or None for corrupt values."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        parsed = _dt.parse_datetime(value)
        return parsed if isinstance(parsed, datetime) else None
    return None


def _persisted_string_set(value: Any) -> set[str]:
    """Return persisted string aliases while ignoring corrupt values."""
    if not isinstance(value, (list, set, tuple)):
        return set()
    return {item for item in value if isinstance(item, str)}


def build_ghost_reservations(
    current_keys: set[str],
    persisted: dict[str, Any],
    prefix: str,
    observed_mapping_keys: set[str] | None,
    ctx: ReservationBuildContext,
) -> GhostReservationResult:
    """Build synthetic Reservations for assigned slots absent from the feed."""
    result = GhostReservationResult()
    for key, mapping in persisted.items():
        ghost = _build_one_ghost(
            key, mapping, current_keys, prefix, observed_mapping_keys, ctx
        )
        if ghost is not None:
            result.reservations.append(ghost)
    return result


def _build_one_ghost(
    key: str,
    mapping: dict[str, Any],
    current_keys: set[str],
    prefix: str,
    observed_mapping_keys: set[str] | None,
    ctx: ReservationBuildContext,
) -> _Reservation | None:
    """Return a single ghost reservation, applying missing-count mutations."""
    if key in current_keys:
        return None
    status = mapping.get("status")
    if status not in (SLOT_STATUS_OCCUPIED, SLOT_STATUS_PENDING_SET):
        return None

    missing_count = mapping.get("missing_count", 0)
    if not isinstance(missing_count, int):
        missing_count = 0
    new_mc = missing_count + 1
    mapping["missing_count"] = new_mc

    if status == SLOT_STATUS_PENDING_SET and new_mc >= 3:
        mapping["status"] = SLOT_STATUS_PENDING_CLEAR
        mapping["pending_set_since"] = None
        mapping["pending_clear_since"] = mapping.get(
            "pending_clear_since", _dt.now().isoformat()
        )
        mapping["operation_id"] = None
        mapping["operation_kind"] = OPERATION_KIND_CLEAR
        _LOGGER.debug(
            "Pending-set ghost %s missed %d cycles; marking pending-clear", key, new_mc
        )
        return None

    return _ghost_from_mapping(key, mapping, prefix, observed_mapping_keys, new_mc, ctx)


def _ghost_from_mapping(
    key: str,
    mapping: dict[str, Any],
    prefix: str,
    observed_mapping_keys: set[str] | None,
    new_mc: int,
    ctx: ReservationBuildContext,
) -> _Reservation | None:
    """Return a ghost reservation from a persisted mapping, or None when fenced."""
    identity = mapping.get("identity", {})
    if not isinstance(identity, dict):
        identity = {}
    slot_name_raw = identity.get("slot_name", "")
    slot_name = slot_name_raw if isinstance(slot_name_raw, str) else ""
    summary_raw = identity.get("summary", "")
    summary = summary_raw if isinstance(summary_raw, str) else ""
    last_actual = mapping.get("last_observed_actual", {})
    if not isinstance(last_actual, dict):
        last_actual = {}
    actual_name = last_actual.get("name_state")
    if _ghost_physical_name_mismatch(
        actual_name, slot_name, prefix, observed_mapping_keys, key, ctx
    ):
        _LOGGER.debug(
            "Ghost reservation %s: physical name %r differs from "
            "persisted identity %r; preserving slot as unmatched with "
            "missing_count=%d",
            key,
            actual_name,
            slot_name,
            new_mc,
        )
        return None
    start_raw = last_actual.get("start_state")
    end_raw = last_actual.get("end_state")
    if not slot_name or start_raw is None or end_raw is None:
        _LOGGER.debug(
            "Ghost reservation %s: missing slot_name or dates; slot remains "
            "fenced with missing_count=%d",
            key,
            new_mc,
        )
        return None

    start_dt = _coerce_persisted_datetime(start_raw)
    end_dt = _coerce_persisted_datetime(end_raw)
    if start_dt is not None and start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=ctx.timezone)
    if end_dt is not None and end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=ctx.timezone)
    if start_dt is None or end_dt is None or start_dt >= end_dt:
        _LOGGER.debug(
            "Ghost reservation %s: invalid dates (start=%s end=%s); "
            "slot remains fenced with missing_count=%d",
            key,
            start_raw,
            end_raw,
            new_mc,
        )
        return None

    try:
        ghost = _Reservation(
            identity_key=key,
            start=start_dt,
            end=end_dt,
            buffered_start=start_dt,
            buffered_end=end_dt,
            summary=summary,
            slot_name=slot_name,
            display_slot_name=_format_display_slot_name(
                slot_name, prefix, ctx.trim_names, ctx.max_name_length
            ),
            slot_code="",
            uid_aliases=_persisted_string_set(identity.get("uid_aliases", [])),
            booking_aliases=_persisted_string_set(identity.get("booking_aliases", [])),
            fingerprint_history=_persisted_string_set(
                mapping.get("fingerprint_history", [])
            ),
            missing_count=new_mc,
        )
    except ValueError:
        _LOGGER.debug("Ghost reservation %s: invalid Reservation fields; skipping", key)
        return None
    _LOGGER.debug("Ghost reservation %s created with missing_count=%d", key, new_mc)
    return ghost


def _ghost_physical_name_mismatch(
    actual_name: Any,
    slot_name: str,
    prefix: str,
    observed_mapping_keys: set[str] | None,
    key: str,
    ctx: ReservationBuildContext,
) -> bool:
    """Return whether a ghost's physical name fences it from rematching."""
    if not (
        observed_mapping_keys is not None
        and key in observed_mapping_keys
        and isinstance(actual_name, str)
        and slot_name
    ):
        return False
    name_forms = {
        normalize_slot_name_for_fingerprint(slot_name),
        normalize_slot_name_for_fingerprint(
            _format_display_slot_name(
                slot_name, prefix, ctx.trim_names, ctx.max_name_length
            )
        ),
    }
    return normalize_slot_name_for_fingerprint(actual_name) not in name_forms
