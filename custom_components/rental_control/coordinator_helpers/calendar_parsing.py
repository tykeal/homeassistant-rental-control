# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Pure iCalendar interpretation helpers for the coordinator.

These helpers perform no network I/O, Home Assistant state reads, Store
writes, refresh requests, or service calls.  The coordinator shell owns
fetching and executor handling and passes already-parsed
``icalendar.Calendar`` objects plus a :class:`~.models.CalendarParseContext`.
"""

from __future__ import annotations

from datetime import datetime
from datetime import time
from datetime import timedelta
from datetime import tzinfo
from typing import TYPE_CHECKING
from typing import Any
from typing import cast

from homeassistant.components.calendar import CalendarEvent

from ..const import EVENT_AGE_THRESHOLD_DAYS
from ..description_parser import extract_checkin_time
from ..description_parser import extract_checkout_time
from ..util import apply_buffer
from ..util import dt as _dt
from ..util import get_slot_name
from ..util import normalize_uid

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .models import CalendarParseContext

# aislop-ignore-file ai-slop/hallucinated-import -- Provided by Home Assistant runtime.

import logging

_LOGGER = logging.getLogger(__name__)


def combine_event_time(value: Any, selected_time: time, timezone: tzinfo) -> datetime:
    """Return an event-date datetime using the selected local time."""
    event_date = value.date() if isinstance(value, datetime) else value
    return cast(
        "datetime",
        _dt.as_utc(datetime.combine(event_date, selected_time, timezone)),
    )


def datetimes_match(left: datetime, right: datetime, timezone: tzinfo) -> bool:
    """Return whether two datetimes represent the same instant."""
    left_local = left.replace(tzinfo=timezone) if left.tzinfo is None else left
    right_local = right.replace(tzinfo=timezone) if right.tzinfo is None else right
    left_utc = cast("datetime", _dt.as_utc(left_local))
    right_utc = cast("datetime", _dt.as_utc(right_local))
    return left_utc == right_utc


def physical_override_time(
    value: datetime,
    timezone: tzinfo,
    buffer_before: int,
    buffer_after: int,
    *,
    start: bool,
) -> time:
    """Return the unbuffered local time represented by a physical slot time."""
    local_value = (
        value.replace(tzinfo=timezone)
        if value.tzinfo is None
        else value.astimezone(timezone)
    )
    if start and buffer_before:
        local_value += timedelta(minutes=buffer_before)
    if not start and buffer_after:
        local_value -= timedelta(minutes=buffer_after)
    return local_value.time()


def buffer_aware_override_times(
    event_start: Any,
    event_end: Any,
    expected_checkin: time,
    expected_checkout: time,
    override: Mapping[str, Any],
    ctx: CalendarParseContext,
) -> tuple[time, time]:
    """Return manual override times only when physical times truly differ.

    Keymaster stores already-buffered datetimes.  A physical time that
    matches the expected calendar/default window after applying buffers is
    system-managed and must not freeze future Honor Event Times changes.
    """
    expected_start = combine_event_time(event_start, expected_checkin, ctx.timezone)
    expected_end = combine_event_time(event_end, expected_checkout, ctx.timezone)
    buffered_start_raw, buffered_end_raw = apply_buffer(
        expected_start,
        expected_end,
        ctx.code_buffer_before,
        ctx.code_buffer_after,
        ctx,
    )
    buffered_start = (
        buffered_start_raw
        if isinstance(buffered_start_raw, datetime)
        else expected_start
    )
    buffered_end = (
        buffered_end_raw if isinstance(buffered_end_raw, datetime) else expected_end
    )

    checkin = expected_checkin
    checkout = expected_checkout
    override_start = override.get("start_time")
    override_end = override.get("end_time")
    if isinstance(override_start, datetime) and not datetimes_match(
        override_start, buffered_start, ctx.timezone
    ):
        checkin = physical_override_time(
            override_start,
            ctx.timezone,
            ctx.code_buffer_before,
            ctx.code_buffer_after,
            start=True,
        )
    if isinstance(override_end, datetime) and not datetimes_match(
        override_end, buffered_end, ctx.timezone
    ):
        checkout = physical_override_time(
            override_end,
            ctx.timezone,
            ctx.code_buffer_before,
            ctx.code_buffer_after,
            start=False,
        )
    return checkin, checkout


def parse_calendar(
    calendar: Any,
    from_date: datetime,
    to_date: datetime,
    ctx: CalendarParseContext,
) -> list[CalendarEvent]:
    """Return a sorted list of events from an icalendar object."""
    events: list[CalendarEvent] = []
    _LOGGER.debug("In parse_calendar:: from_date: %s; to_date: %s", from_date, to_date)
    for event in calendar.walk("VEVENT"):
        cal_event = _parse_vevent(event, from_date, to_date, ctx)
        if cal_event is not None:
            events.append(cal_event)
    events.sort(key=lambda k: k.start)
    return events


def _vevent_filtered(
    event: Any, from_date: datetime, to_date: datetime, ctx: Any
) -> bool:
    """Return whether an event should be skipped before time selection."""
    if "RRULE" in event:
        _LOGGER.error("RRULE in event: %s", str(event["SUMMARY"]))
        return True
    if "Check-in" in event["SUMMARY"] or "Check-out" in event["SUMMARY"]:
        _LOGGER.debug("Smoobu extra event, ignoring")
        return True
    try:
        if "DTEND" in event and event["DTEND"].dt < from_date.date() - timedelta(
            days=EVENT_AGE_THRESHOLD_DAYS
        ):
            return True
    except (AttributeError, TypeError):  # fmt: skip
        pass
    try:
        if "DTSTART" in event and event["DTSTART"].dt > to_date.date():
            return True
    except (AttributeError, TypeError):  # fmt: skip
        pass
    if ctx.ignore_non_reserved and any(
        x in event["SUMMARY"] for x in ["Blocked", "Not available"]
    ):
        return True
    return False


def _parse_vevent(
    event: Any, from_date: datetime, to_date: datetime, ctx: CalendarParseContext
) -> CalendarEvent | None:
    """Convert a single VEVENT into a CalendarEvent, or None when skipped."""
    if _vevent_filtered(event, from_date, to_date, ctx):
        return None

    if "DESCRIPTION" in event:
        slot_name = get_slot_name(event["SUMMARY"], event["DESCRIPTION"], "")
    else:
        slot_name = get_slot_name(event["SUMMARY"], "", "")

    override = None
    if slot_name and ctx.override_lookup is not None:
        override = ctx.override_lookup(slot_name)

    checkin, checkout = _select_event_times(event, override, ctx)

    _LOGGER.debug("Checkin: %s, Checkout: %s", checkin, checkout)
    dtstart: datetime = datetime.combine(event["DTSTART"].dt, checkin, ctx.timezone)
    dtstart = _dt.as_utc(dtstart)
    start: datetime = dtstart
    if "DTEND" not in event:
        dtend: datetime = dtstart
    else:
        dtend = datetime.combine(event["DTEND"].dt, checkout, ctx.timezone)
    dtend = _dt.as_utc(dtend)
    end = dtend

    if ctx.event_prefix:
        event["SUMMARY"] = ctx.event_prefix + " " + event["SUMMARY"]

    return _build_calendar_event(start, end, from_date, event, ctx)


def _select_event_times(
    event: Any, override: Any, ctx: CalendarParseContext
) -> tuple[time, time]:
    """Return the (checkin, checkout) times for a single calendar event."""
    has_explicit_times = isinstance(event["DTSTART"].dt, datetime) and (
        "DTEND" in event and isinstance(event["DTEND"].dt, datetime)
    )

    if ctx.honor_event_times and has_explicit_times:
        return event["DTSTART"].dt.time(), event["DTEND"].dt.time()
    if ctx.honor_event_times and not has_explicit_times:
        return _select_honor_times_no_explicit(event, override, ctx)
    if override:
        checkin = physical_override_time(
            override["start_time"],
            ctx.timezone,
            ctx.code_buffer_before,
            ctx.code_buffer_after,
            start=True,
        )
        checkout = physical_override_time(
            override["end_time"],
            ctx.timezone,
            ctx.code_buffer_before,
            ctx.code_buffer_after,
            start=False,
        )
        return checkin, checkout
    try:
        return event["DTSTART"].dt.time(), event["DTEND"].dt.time()
    except AttributeError:
        return ctx.checkin, ctx.checkout


def _select_honor_times_no_explicit(
    event: Any, override: Any, ctx: CalendarParseContext
) -> tuple[time, time]:
    """Return times for honor-event-times all-day events via description/override."""
    raw_desc = event.get("DESCRIPTION")
    description = str(raw_desc) if raw_desc else ""
    desc_checkin = extract_checkin_time(description)
    desc_checkout = extract_checkout_time(description)
    expected_checkin = desc_checkin if desc_checkin is not None else ctx.checkin
    expected_checkout = desc_checkout if desc_checkout is not None else ctx.checkout

    if override:
        event_end_dt = event["DTEND"].dt if "DTEND" in event else event["DTSTART"].dt
        checkin, checkout = buffer_aware_override_times(
            event["DTSTART"].dt,
            event_end_dt,
            expected_checkin,
            expected_checkout,
            override,
            ctx,
        )
        if desc_checkin is not None:
            checkin = desc_checkin
        if desc_checkout is not None:
            checkout = desc_checkout
        return checkin, checkout
    return expected_checkin, expected_checkout


def _build_calendar_event(
    start: datetime,
    end: datetime,
    from_date: datetime,
    event: Any,
    ctx: CalendarParseContext,
) -> CalendarEvent | None:
    """Ensure that events are within the start and end."""
    if (_dt.as_utc(end) < _dt.as_utc(from_date)) or (
        _dt.as_utc(end).date() == _dt.as_utc(from_date).date()
        and end.hour == 0
        and end.minute == 0
        and end.second == 0
    ):
        _LOGGER.debug("This event has already ended")
        return None
    description = event.get("DESCRIPTION")
    raw_uid = event.get("UID")
    cal_event = CalendarEvent(
        description=description,
        end=end.astimezone(ctx.timezone),
        location=event.get("LOCATION"),
        summary=event.get("SUMMARY", "Unknown"),
        start=start.astimezone(ctx.timezone),
        uid=normalize_uid(str(raw_uid) if raw_uid is not None else None),
    )
    _LOGGER.debug("Event to add: %s", cal_event)
    return cal_event
