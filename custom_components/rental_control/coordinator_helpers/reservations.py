# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Pure reservation and ghost-reservation builders for the coordinator."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import time
from datetime import timedelta
import logging
from typing import TYPE_CHECKING
from typing import Any

from homeassistant.util import dt

from ..bookings_only import resolve_slot_name
from ..reconciliation import Reservation as _Reservation
from ..reconciliation import extract_booking_aliases
from ..reconciliation import make_reservation_fingerprint
from ..reconciliation import normalize_slot_name_for_fingerprint
from ..util import apply_buffer
from ..util import normalize_uid
from .codegen import extract_last_four
from .codegen import generate_slot_code
from .ghost_reservations import build_ghost_reservations as build_ghost_reservations
from .models import ObservedSlotQuery
from .models import _format_display_slot_name
from .slot_matching import find_observed_slot

if TYPE_CHECKING:
    from ..reconciliation import ManagedSlot
    from .models import ReservationBuildContext

_LOGGER = logging.getLogger(__name__)

_Window = tuple[datetime, datetime]


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


def _resolve_slot_name(event: Any, ctx: ReservationBuildContext) -> str | None:
    """Return an event's slot name, synthesizing one when permitted.

    Every call site must resolve the name the same way: the name is
    hashed into the reservation fingerprint, so a disagreement between
    the name-window pass and the reservation build would make identity
    unstable and churn issued codes.
    """
    return resolve_slot_name(
        event.summary,
        event.description,
        ctx.event_prefix or "",
        _coerce_event_datetime(event.start, ctx),
        _coerce_event_datetime(event.end, ctx),
        ctx.bookings_only_feed,
        getattr(event, "uid", None),
    )


def _collect_name_windows(
    ordered_calendar: list[Any], ctx: ReservationBuildContext
) -> _NameWindows:
    """Return per-name counts and reserved/ordered date windows."""
    windows = _NameWindows()
    for event in ordered_calendar:
        slot_name = _resolve_slot_name(event, ctx)
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
    slot_name = _resolve_slot_name(event, ctx)
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

    phone_last_four = extract_last_four(event.description)
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
        slot_code, code_source, rotation_pending = _resolve_observed_code(
            _ObservedCodeQuery(
                matched_physical=matched_physical,
                event=event,
                uid=uid,
                ctx=ctx,
                generated=(slot_code, code_source),
                buffered_start=buffered_start,
            )
        )
    else:
        rotation_pending = False
    rotation_origin = (
        (matched_physical.slot, matched_physical.actual_code)
        if rotation_pending and matched_physical and matched_physical.actual_code
        else None
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
            code_provenance=code_source,
            code_rotation_pending=rotation_pending,
            code_rotation_origin=rotation_origin,
            phone_last_four=phone_last_four,
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


@dataclass
class _ObservedCodeQuery:
    """Inputs needed to reconcile a generated code with the observed PIN."""

    matched_physical: ManagedSlot
    event: Any
    uid: str | None
    ctx: ReservationBuildContext
    generated: tuple[str, str]
    buffered_start: datetime


def _resolve_observed_code(query: _ObservedCodeQuery) -> tuple[str, str, bool]:
    """Return the (code, source, rotating) preserving manual observed PINs.

    The returned source is a diagnostics label only; it never changes
    which code is issued. ``manual_observed`` means the observed PIN does
    not match what the configured generator produces for the programmed
    window. That is the best signal available, but it cannot distinguish
    a human-entered PIN from a code produced by a previous generator
    configuration, so a generator change relabels pre-existing generated
    codes as ``manual_observed`` (see issue #736).
    """
    matched_physical = query.matched_physical
    event = query.event
    uid = query.uid
    ctx = query.ctx
    slot_code, code_source = query.generated
    observed_code = matched_physical.actual_code
    if observed_code is None:
        return slot_code, code_source, False
    slot_name = _resolve_slot_name(event, ctx)
    identity_key = (
        make_reservation_fingerprint(
            ctx.entry_id,
            slot_name,
            _coerce_event_datetime(event.start, ctx),
            _coerce_event_datetime(event.end, ctx),
        )
        if slot_name
        else None
    )
    if identity_key in ctx.reissue_suppression.identity_keys:
        return slot_code, code_source, False
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
    if old_generated is None or observed_code != old_generated:
        return observed_code, "manual_observed", False
    # The observed PIN is exactly what the generator produces for the
    # programmed window, so it is a generated code even when it is retained.
    if not ctx.should_update_code or not _rotation_window_open(
        matched_physical.actual_start, query.buffered_start
    ):
        return observed_code, "generated", False
    return slot_code, code_source, slot_code != observed_code


def _rotation_window_open(
    programmed_start: datetime | None, buffered_start: datetime
) -> bool:
    """Return whether a date shift may still rotate the generated code.

    ``should_update_code`` only promises to follow date changes on future
    events.  Once either the programmed window or the requested window has
    opened the guest may already be relying on the issued PIN, so the code
    is retained and only the stay dates are pushed to the lock.
    """
    now = dt.now()
    if buffered_start <= now:
        return False
    return programmed_start is not None and programmed_start > now
