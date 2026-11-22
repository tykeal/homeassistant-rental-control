# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Coordinator shell mixins for behavior-preserving delegation."""

# mypy: disable-error-code="attr-defined, has-type, var-annotated, misc, no-redef"

from __future__ import annotations

import asyncio
from datetime import datetime
from datetime import timedelta
import logging

from homeassistant.components.calendar import CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt
from icalendar import Calendar
import x_wr_timezone

from ..const import REQUEST_TIMEOUT
from . import calendar_parsing
from .coordinator_allocation_shell import CoordinatorAllocationMixin

_LOGGER = logging.getLogger(__name__)


class CoordinatorRefreshMixin(CoordinatorAllocationMixin):
    """Provide extracted coordinator shell behavior."""

    async def _async_get_events_impl(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Get list of upcoming events."""
        _LOGGER.debug("Running RentalControl async_get_events")
        events = []
        cal_data: list[CalendarEvent] | None = self.data
        if cal_data and len(cal_data) > 0:
            for event in cal_data:
                if event.start < end_date and event.end > start_date:
                    events.append(event)
        return events

    async def _async_fetch_calendar(self) -> list[CalendarEvent]:
        """Fetch iCalendar data from URL and parse into events."""
        try:
            session = async_get_clientsession(self.hass, verify_ssl=self.verify_ssl)
            async with asyncio.timeout(REQUEST_TIMEOUT):
                response = await session.get(self.url)
                try:
                    if response.status != 200:
                        raise UpdateFailed(
                            f"Calendar fetch failed (HTTP {response.status})"
                        )
                    text = await response.text()
                finally:
                    response.release()
        except TimeoutError:
            raise UpdateFailed("Calendar fetch timed out") from None
        except UpdateFailed:
            raise
        except Exception:
            raise UpdateFailed("Calendar fetch failed") from None

        try:
            # Some calendars are filled with NULL-bytes that break
            # parsing.  from_ical triggers blocking timezone file I/O
            # so run it in the executor.
            cleaned = text.replace("\x00", "")
            event_list = await self.hass.async_add_executor_job(
                Calendar.from_ical, cleaned
            )

            # Convert non-standard timezone definitions
            if "X-WR-TIMEZONE" in event_list:
                event_list = await self.hass.async_add_executor_job(
                    x_wr_timezone.to_standard, event_list
                )

            start_of_events = dt.start_of_local_day()
            end_of_events = dt.start_of_local_day() + timedelta(days=self.days)

            return await self._ical_parser(event_list, start_of_events, end_of_events)
        except Exception:
            raise UpdateFailed("Calendar parse failed") from None

    async def _async_update_data(self) -> list[CalendarEvent]:
        """Fetch and parse calendar data."""
        _LOGGER.debug("Running Rental Control calendar update")

        is_fresh_data = True

        try:
            new_calendar = await self._async_fetch_calendar()
        except UpdateFailed:
            if self.data is not None:
                _LOGGER.warning(
                    "Calendar fetch or parse failed; using cached data (%d events)",
                    len(self.data),
                )
                new_calendar = list(self.data)
                is_fresh_data = False
            else:
                raise

        reconciliation_calendar = new_calendar
        if is_fresh_data:
            # Miss tracking: preserve stale data when within tolerance
            previous: list[CalendarEvent] | None = self.data
            if (
                previous
                and len(previous) > 0
                and len(new_calendar) == 0
                and self.num_misses < self.max_misses
            ):
                self.num_misses += 1
                _LOGGER.warning(
                    "No events found, but %d were previously available. Miss %d of %d",
                    len(previous),
                    self.num_misses,
                    self.max_misses,
                )
                new_calendar = list(previous)
            else:
                _LOGGER.debug(
                    "Found %d calendar events",
                    len(new_calendar),
                )
                self.num_misses = 0

        # Find the next upcoming event (clear stale state first)
        self.event = None
        if len(new_calendar) > 0:
            for event in new_calendar:
                if event.end > dt.now():
                    _LOGGER.debug("Found the next upcoming calendar event")
                    self.event = event
                    break

        self._advance_calendar_misses = is_fresh_data
        try:
            if self.event_overrides:
                await self._run_reconciliation(reconciliation_calendar)
            else:
                await self._run_lockless_allocation(reconciliation_calendar)
        finally:
            self._advance_calendar_misses = False

        await self.async_save_slot_store()

        self._refresh_child_locks()

        return new_calendar

    def _refresh_child_locks(self) -> None:
        """Refresh child lock discovery for the current cycle."""
        if not self.lockname:
            return
        self._parent_entry_id = self._find_parent_entry_id()
        previous_children = self._child_locknames
        self._child_locknames = self._discover_child_locks()
        if self._child_locknames != previous_children:
            _LOGGER.info(
                "Child lock discovery updated (%d related entries)",
                len(self._child_locknames),
            )

    async def _ical_parser(
        self, calendar: Calendar, from_date: datetime, to_date: datetime
    ) -> list[CalendarEvent]:
        """Return a sorted list of events from a icalendar object."""
        return calendar_parsing.parse_calendar(
            calendar, from_date, to_date, self._calendar_parse_context()
        )
