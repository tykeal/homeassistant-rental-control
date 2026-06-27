# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Pure reservation and ghost-reservation builders for the coordinator.

These helpers perform no Home Assistant state reads, Store saves, refresh
requests, or Keymaster service calls.  They build reservations from parsed
calendar events and reconstruct ghosts from persisted mapping dicts.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import time
from datetime import timedelta
import logging
from typing import TYPE_CHECKING
from typing import Any

from ..const import OPERATION_KIND_CLEAR
from ..const import SLOT_STATUS_OCCUPIED
from ..const import SLOT_STATUS_PENDING_CLEAR
from ..const import SLOT_STATUS_PENDING_SET
from ..reconciliation import Reservation as _Reservation
from ..reconciliation import extract_booking_aliases
from ..reconciliation import make_reservation_fingerprint
from ..reconciliation import normalize_slot_name_for_fingerprint
from ..util import apply_buffer
from ..util import dt as _dt
from ..util import get_slot_name
from ..util import normalize_uid
from .codegen import generate_slot_code
from .models import GhostReservationResult
from .models import ObservedSlotQuery
from .models import _format_display_slot_name
from .slot_matching import find_observed_slot

if TYPE_CHECKING:
    from ..reconciliation import ManagedSlot
    from .models import ReservationBuildContext

_LOGGER = logging.getLogger(__name__)

_Window = tuple[datetime, datetime]


def _log_fence(key: str, reason: str, new_mc: int) -> None:
    """Log a ghost reservation being fenced as unmatched."""
    _LOGGER.debug("Ghost %s fenced (%s); missing_count=%d", key, reason, new_mc)


@dataclass
class _NameWindows:
    """Per-name reservation counts and reserved/ordered date windows."""

    counts: dict[str, int] = field(default_factory=dict)
    date_windows: dict[str, set[_Window]] = field(default_factory=dict)
    ordered_windows: dict[str, list[_Window]] = field(default_factory=dict)


def _coerce_event_datetime(value: Any, ctx: ReservationBuildContext) -> datetime:
    """Return a timezone-aware datetime for calendar date/datetime values."""
    if isinstance(value, datetime):
        return value
    return datetime.combine(value, time.min, ctx.timezone)


def _buffered_window(
    start: datetime, end: datetime, ctx: ReservationBuildContext
) -> _Window:
    """Return the buffered (start, end) window for a reservation."""
    buffered_start_raw, buffered_end_raw = apply_buffer(
        start, end, ctx.code_buffer_before, ctx.code_buffer_after, ctx
    )
    buffered_start = (
        buffered_start_raw if isinstance(buffered_start_raw, datetime) else start
    )
    buffered_end = buffered_end_raw if isinstance(buffered_end_raw, datetime) else end
    return buffered_start, buffered_end


def _collect_name_windows(
    ordered_calendar: list[Any], ctx: ReservationBuildContext
) -> _NameWindows:
    """Return per-name counts and reserved/ordered date windows."""
    windows = _NameWindows()
    for event in ordered_calendar:
        slot_name = get_slot_name(
            event.summary, event.description or "", ctx.event_prefix or ""
        )
        if not slot_name:
            continue
        key = normalize_slot_name_for_fingerprint(slot_name)
        windows.counts[key] = windows.counts.get(key, 0) + 1
        buffered = _buffered_window(
            _coerce_event_datetime(event.start, ctx),
            _coerce_event_datetime(event.end, ctx),
            ctx,
        )
        windows.date_windows.setdefault(key, set()).add(buffered)
        windows.ordered_windows.setdefault(key, []).append(buffered)
        active_windows = ctx.active_windows_for_name(slot_name)
        if active_windows:
            windows.date_windows.setdefault(key, set()).update(active_windows)
    return windows


def build_reservations(
    calendar: list[Any],
    managed_slots: list[ManagedSlot] | None,
    ctx: ReservationBuildContext,
) -> list[_Reservation]:
    """Convert parsed CalendarEvent objects to Reservation objects."""
    prefix = f"{ctx.event_prefix} " if ctx.event_prefix else ""
    reservations: list[_Reservation] = []
    observed_slots = managed_slots or []
    consumed: set[int] = set()
    ordered_calendar = sorted(
        calendar,
        key=lambda event: (
            _coerce_event_datetime(event.start, ctx),
            _coerce_event_datetime(event.end, ctx),
            event.summary or "",
        ),
    )
    windows = _collect_name_windows(ordered_calendar, ctx)
    for event in ordered_calendar:
        res = _build_one_reservation(
            event, ctx, prefix, observed_slots, consumed, windows
        )
        if res is not None:
            reservations.append(res)
    return reservations


def _build_one_reservation(
    event: Any,
    ctx: ReservationBuildContext,
    prefix: str,
    observed_slots: list[ManagedSlot],
    consumed: set[int],
    windows: _NameWindows,
) -> _Reservation | None:
    """Return one reservation for an event, or None when invalid."""
    slot_name = get_slot_name(
        event.summary, event.description or "", ctx.event_prefix or ""
    )
    if not slot_name:
        return None

    start = _coerce_event_datetime(event.start, ctx)
    end = _coerce_event_datetime(event.end, ctx)
    buffered_start, buffered_end = _buffered_window(start, end, ctx)
    identity_key = make_reservation_fingerprint(ctx.entry_id, slot_name, start, end)
    uid = normalize_uid(getattr(event, "uid", None))
    uid_aliases: set[str] = {uid} if uid else set()
    booking_aliases = extract_booking_aliases(event.summary, event.description or "")
    display_slot_name = _display_slot_name(slot_name, prefix, ctx)

    slot_code = generate_slot_code(
        ctx.code_generator, ctx.code_length, start, end, event.description, uid
    )
    code_source = "generated"
    key = normalize_slot_name_for_fingerprint(slot_name)
    active_windows = ctx.active_windows_for_name(slot_name)
    matched_physical = find_observed_slot(
        ObservedSlotQuery(
            managed_slots=observed_slots,
            slot_name=slot_name,
            display_slot_name=display_slot_name,
            consumed_slots=consumed,
            desired_start=buffered_start,
            desired_end=buffered_end,
            require_date_match=windows.counts.get(key, 0) > 1,
            reserved_date_windows=windows.date_windows.get(key),
            ordered_date_windows=windows.ordered_windows.get(key),
            block_unknown_date_fallback=_blocks_unknown_date(
                active_windows, buffered_start, buffered_end
            ),
            expected_name_count=windows.counts.get(key, 1),
            event_prefix=prefix,
        )
    )
    if matched_physical is not None and matched_physical.actual_code:
        slot_code, code_source = _resolve_observed_code(
            matched_physical, event, uid, ctx, slot_code, code_source
        )

    try:
        res = _Reservation(
            identity_key=identity_key,
            start=start,
            end=end,
            buffered_start=buffered_start,
            buffered_end=buffered_end,
            summary=event.summary,
            slot_name=slot_name,
            display_slot_name=display_slot_name,
            slot_code=slot_code,
            uid_aliases=uid_aliases,
            booking_aliases=booking_aliases,
            fingerprint_history=set(),
            missing_count=0,
            code_source=code_source,
        )
    except ValueError:
        _LOGGER.warning(
            "Skipping invalid reservation for %s: start=%s >= end=%s",
            event.summary,
            start,
            end,
        )
        return None
    res.sensor_lookup_keys.update({identity_key, *(uid_aliases or set())})
    return res


def _display_slot_name(
    slot_name: str, prefix: str, ctx: ReservationBuildContext
) -> str:
    """Return the Keymaster display name for a reservation."""
    return _format_display_slot_name(
        slot_name, prefix, ctx.trim_names, ctx.max_name_length
    )


def _blocks_unknown_date(
    active_windows: set[tuple[datetime, datetime]],
    buffered_start: datetime,
    buffered_end: datetime,
) -> bool:
    """Return whether an unsafe unknown-date fallback must be blocked."""
    return bool(active_windows and (buffered_start, buffered_end) not in active_windows)


def _resolve_observed_code(
    matched_physical: ManagedSlot,
    event: Any,
    uid: str | None,
    ctx: ReservationBuildContext,
    slot_code: str,
    code_source: str,
) -> tuple[str, str]:
    """Return the (code, source) preserving manual observed PINs."""
    observed_code = matched_physical.actual_code
    if observed_code is None:
        return slot_code, code_source
    observed_start = matched_physical.actual_start
    observed_end = matched_physical.actual_end
    if observed_start is not None and ctx.code_buffer_before:
        observed_start = observed_start + timedelta(minutes=ctx.code_buffer_before)
    if observed_end is not None and ctx.code_buffer_after:
        observed_end = observed_end - timedelta(minutes=ctx.code_buffer_after)
    old_generated = (
        generate_slot_code(
            ctx.code_generator,
            ctx.code_length,
            observed_start,
            observed_end,
            event.description,
            uid,
        )
        if observed_start is not None and observed_end is not None
        else None
    )
    if (
        old_generated is None
        or observed_code != old_generated
        or not ctx.should_update_code
    ):
        return observed_code, "manual_observed"
    return slot_code, code_source


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

    new_mc = mapping.get("missing_count", 0) + 1
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
    slot_name: str = identity.get("slot_name", "")
    last_actual = mapping.get("last_observed_actual", {})
    actual_name = last_actual.get("name_state")
    if _ghost_physical_name_mismatch(
        actual_name, slot_name, prefix, observed_mapping_keys, key, ctx
    ):
        _log_fence(key, "physical name mismatch", new_mc)
        return None
    start_raw = last_actual.get("start_state")
    end_raw = last_actual.get("end_state")
    if not slot_name or start_raw is None or end_raw is None:
        _log_fence(key, "missing slot_name or dates", new_mc)
        return None

    start_dt = (
        _dt.parse_datetime(start_raw) if isinstance(start_raw, str) else start_raw
    )
    end_dt = _dt.parse_datetime(end_raw) if isinstance(end_raw, str) else end_raw
    if start_dt is None or end_dt is None or start_dt >= end_dt:
        _log_fence(key, "invalid dates", new_mc)
        return None

    try:
        ghost = _Reservation(
            identity_key=key,
            start=start_dt,
            end=end_dt,
            buffered_start=start_dt,
            buffered_end=end_dt,
            summary=identity.get("summary", ""),
            slot_name=slot_name,
            display_slot_name=_format_display_slot_name(
                slot_name, prefix, ctx.trim_names, ctx.max_name_length
            ),
            slot_code="",
            uid_aliases=set(identity.get("uid_aliases", [])),
            booking_aliases=set(identity.get("booking_aliases", [])),
            fingerprint_history=set(mapping.get("fingerprint_history", [])),
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
