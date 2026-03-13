# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2021 Andrew Grimberg <tykeal@bardicgrove.org>
##############################################################################
# COPYRIGHT 2025 Andrew Grimberg
#
# All rights reserved. This program and the accompanying materials
# are made available under the terms of the Apache 2.0 License
# which accompanies this distribution, and is available at
# https://www.apache.org/licenses/LICENSE-2.0
#
# Contributors:
#   Andrew Grimberg - Initial implementation
##############################################################################
"""Rental Control Coordinator."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import datetime
from datetime import time
from datetime import timedelta
import logging
from typing import Any
from zoneinfo import ZoneInfo  # noreorder

import aiohttp
from homeassistant.components.button import DOMAIN as BUTTON
from homeassistant.components.calendar import CalendarEvent
from homeassistant.components.datetime import DOMAIN as DATETIME
from homeassistant.components.persistent_notification import async_create
from homeassistant.components.switch import DOMAIN as SWITCH
from homeassistant.components.text import DOMAIN as TEXT
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.const import CONF_URL
from homeassistant.const import CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt
from homeassistant.util import slugify
from icalendar import Calendar
import x_wr_timezone

from .const import CONF_CHECKIN
from .const import CONF_CHECKOUT
from .const import CONF_CODE_GENERATION
from .const import CONF_CODE_LENGTH
from .const import CONF_CREATION_DATETIME
from .const import CONF_DAYS
from .const import CONF_EVENT_PREFIX
from .const import CONF_IGNORE_NON_RESERVED
from .const import CONF_LOCK_ENTRY
from .const import CONF_MAX_EVENTS
from .const import CONF_REFRESH_FREQUENCY
from .const import CONF_SHOULD_UPDATE_CODE
from .const import CONF_START_SLOT
from .const import CONF_TIMEZONE
from .const import DEFAULT_CODE_GENERATION
from .const import DEFAULT_CODE_LENGTH
from .const import DEFAULT_MAX_MISSES
from .const import DEFAULT_REFRESH_FREQUENCY
from .const import DOMAIN
from .const import EVENT_AGE_THRESHOLD_DAYS
from .const import REQUEST_TIMEOUT
from .const import VERSION
from .event_overrides import EventOverrides
from .util import get_slot_name

_LOGGER = logging.getLogger(__name__)


class RentalControlCoordinator(DataUpdateCoordinator[list[CalendarEvent]]):
    """Coordinator for managing rental control calendar data."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry):
        """Set up a calendar coordinator."""
        config = config_entry.data
        self._name: str = str(config.get(CONF_NAME))
        self._unique_id: str = str(config_entry.unique_id)
        self._entry_id: str = config_entry.entry_id
        self.event_prefix: str | None = config.get(CONF_EVENT_PREFIX)
        self.url: str = str(config.get(CONF_URL))
        self.timezone: dt.dt.tzinfo = ZoneInfo(str(config.get(CONF_TIMEZONE)))
        self.refresh_frequency: int = config.get(
            CONF_REFRESH_FREQUENCY, DEFAULT_REFRESH_FREQUENCY
        )
        # our config flow guarantees that checkin and checkout are valid times
        # just use cv.time to get the parsed time object
        self.checkin: time = cv.time(config.get(CONF_CHECKIN))
        self.checkout: time = cv.time(config.get(CONF_CHECKOUT))
        self.start_slot: int = int(str(config.get(CONF_START_SLOT)))
        lockname_raw = config.get(CONF_LOCK_ENTRY)
        self.lockname: str | None = (
            slugify(lockname_raw) if lockname_raw and lockname_raw.strip() else None
        )
        self.max_events: int = int(str(config.get(CONF_MAX_EVENTS)))
        self.max_misses: int = DEFAULT_MAX_MISSES
        self.num_misses: int = 0
        self.days: int = int(str(config.get(CONF_DAYS)))
        self.ignore_non_reserved: bool = bool(config.get(CONF_IGNORE_NON_RESERVED))
        self.verify_ssl: bool = bool(config.get(CONF_VERIFY_SSL))
        self.event_overrides: EventOverrides | None = (
            EventOverrides(self.start_slot, self.max_events) if self.lockname else None
        )
        self.code_generator: str = config.get(
            CONF_CODE_GENERATION, DEFAULT_CODE_GENERATION
        )
        self.should_update_code: bool = bool(config.get(CONF_SHOULD_UPDATE_CODE))
        self.code_length: int = config.get(CONF_CODE_LENGTH, DEFAULT_CODE_LENGTH)
        self.event: CalendarEvent | None = None
        self.created: str = config.get(CONF_CREATION_DATETIME, str(dt.now()))
        self._version: str = VERSION

        super().__init__(
            hass=hass,
            logger=_LOGGER,
            name=self._name,
            config_entry=config_entry,
            update_interval=timedelta(minutes=self.refresh_frequency),
        )

        # setup device
        device_registry = dr.async_get(hass)
        device_registry.async_get_or_create(
            config_entry_id=self._entry_id,
            identifiers={(DOMAIN, self.unique_id)},
            name=self.name,
            sw_version=self.version,
        )

        entity_registry = er.async_get(hass)
        if self.lockname:
            reset_entity = f"{BUTTON}.{self.lockname}_code_slot_{self.start_slot}_reset"
            has_reset = entity_registry.async_get(reset_entity)
            if has_reset is None:
                error_msg = """
The version of Keymaster is incompatible with this version of Rental Control.
Please update Keymaster to at least v0.1.0-b0
"""
                _LOGGER.error(error_msg)
                async_create(
                    hass,
                    error_msg,
                    title="Keymaster Incompatible Version",
                )

    @property
    def device_info(self) -> dr.DeviceInfo:
        """Return the device info block."""
        return {
            "identifiers": {(DOMAIN, self.unique_id)},
            "name": self.name,
            "sw_version": self.version,
        }

    @property
    def unique_id(self) -> str:
        """Return the unique id."""
        return self._unique_id

    @property
    def version(self) -> str:
        """Return the version."""
        return self._version

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Get list of upcoming events."""
        _LOGGER.debug("Running RentalControl async_get_events")
        events = []
        cal_data: list[CalendarEvent] | None = self.data
        if cal_data and len(cal_data) > 0:
            for event in cal_data:
                _LOGGER.debug(
                    "Checking if event %s has start %s and end %s "
                    "within in the limit: %s and %s",
                    event.summary,
                    event.start,
                    event.end,
                    start_date,
                    end_date,
                )

                if event.start < end_date and event.end > start_date:
                    _LOGGER.debug("... and it has")
                    events.append(event)
        return events

    async def async_setup_keymaster_overrides(self) -> None:
        """Bootstrap Keymaster slot overrides on first load."""
        if not self.lockname:
            return

        for i in range(self.start_slot, self.start_slot + self.max_events):
            slot_code = self.hass.states.get(
                f"{TEXT}.{self.lockname}_code_slot_{i}_pin"
            )
            _LOGGER.debug("Slot code: '%s'", slot_code)
            if slot_code is None:
                continue
            slot_code_value = (
                "" if slot_code.state in ("unknown", "unavailable") else slot_code.state
            )

            slot_name = self.hass.states.get(
                f"{TEXT}.{self.lockname}_code_slot_{i}_name"
            )
            _LOGGER.debug("Slot name: '%s'", slot_name)
            if slot_name is None:
                continue
            slot_name_value = (
                "" if slot_name.state in ("unknown", "unavailable") else slot_name.state
            )

            use_date_range = self.hass.states.get(
                f"{SWITCH}.{self.lockname}_code_slot_{i}_use_date_range_limits"
            )
            if use_date_range and use_date_range.state == "on":
                start_time_state = self.hass.states.get(
                    f"{DATETIME}.{self.lockname}_code_slot_{i}_date_range_start"
                )
                _LOGGER.debug("Start time: '%s'", start_time_state)
                if start_time_state is None:
                    continue
                start_datetime = dt.parse_datetime(start_time_state.state)
                _LOGGER.debug("Start time: '%s'", start_datetime)
                if start_datetime is None:
                    continue
                start_time = start_datetime

                end_time_state = self.hass.states.get(
                    f"{DATETIME}.{self.lockname}_code_slot_{i}_date_range_end"
                )
                _LOGGER.debug("End time: '%s'", end_time_state)
                if end_time_state is None:
                    continue
                end_datetime = dt.parse_datetime(end_time_state.state)
                _LOGGER.debug("End time: '%s'", end_datetime)
                if end_datetime is None:
                    continue
                else:
                    end_time = end_datetime
            else:
                start_time = dt.start_of_local_day()
                end_time = dt.start_of_local_day() + timedelta(days=1)

            _LOGGER.debug(
                "Slot %d: %s, %s, %s, %s",
                i,
                slot_code_value,
                slot_name_value,
                start_time,
                end_time,
            )
            _LOGGER.debug("Updating event overrides")
            await self.update_event_overrides(
                i,
                slot_code_value,
                slot_name_value,
                start_time,
                end_time,
                request_refresh=False,
            )

    async def _async_update_data(self) -> list[CalendarEvent]:
        """Fetch and parse calendar data."""
        _LOGGER.debug(
            "Running RentalControl _async_update_data for %s",
            self._name,
        )

        try:
            session = async_get_clientsession(self.hass, verify_ssl=self.verify_ssl)
            async with asyncio.timeout(REQUEST_TIMEOUT):
                response = await session.get(self.url)
                try:
                    if response.status != 200:
                        raise UpdateFailed(
                            f"Calendar fetch failed for {self._name}: "
                            f"HTTP {response.status} - {response.reason}"
                        )
                    text = await response.text()
                finally:
                    response.release()
        except TimeoutError as err:
            raise UpdateFailed(f"Calendar fetch timed out for {self._name}") from err
        except aiohttp.ClientError as err:
            raise UpdateFailed(
                f"Calendar fetch failed for {self._name}: {err}"
            ) from err

        try:
            # Some calendars are filled with NULL-bytes that break
            # parsing
            event_list = Calendar.from_ical(text.replace("\x00", ""))

            # Convert non-standard timezone definitions
            if "X-WR-TIMEZONE" in event_list:
                event_list = await self.hass.async_add_executor_job(
                    x_wr_timezone.to_standard, event_list
                )

            start_of_events = dt.start_of_local_day()
            end_of_events = dt.start_of_local_day() + timedelta(days=self.days)

            new_calendar: list[CalendarEvent] = await self._ical_parser(
                event_list, start_of_events, end_of_events
            )
        except Exception as err:
            raise UpdateFailed(
                f"Failed to parse calendar for {self._name}: {err}"
            ) from err

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
                "No events found in calendar %s, but %d in previous. Miss %d of %d",
                self._name,
                len(previous),
                self.num_misses,
                self.max_misses,
            )
            return previous

        _LOGGER.debug(
            "Found %d events in calendar %s",
            len(new_calendar),
            self._name,
        )
        self.num_misses = 0

        # Find the next upcoming event (clear stale state first)
        self.event = None
        if len(new_calendar) > 0:
            found_next_event = False
            for event in new_calendar:
                if event.end > dt.now() and not found_next_event:
                    _LOGGER.debug(
                        "Event %s is the first event with end in the future: %s",
                        event.summary,
                        event.end,
                    )
                    self.event = event
                    found_next_event = True

        # Check overrides after successful parse
        if self.event_overrides:
            await self.event_overrides.async_check_overrides(
                self, calendar=new_calendar
            )

        return new_calendar

    async def update_config(self, config: Mapping[str, Any]) -> None:
        """Update config entries."""
        self._name = config[CONF_NAME]
        self.name = self._name
        self.url = config[CONF_URL]
        self.timezone = ZoneInfo(config[CONF_TIMEZONE])
        self.refresh_frequency = config[CONF_REFRESH_FREQUENCY]
        self.update_interval = timedelta(minutes=self.refresh_frequency)
        self.event_prefix = config.get(CONF_EVENT_PREFIX)
        # our config flow guarantees that checkin and checkout are valid times
        # just use cv.time to get the parsed time object
        self.checkin = cv.time(config[CONF_CHECKIN])
        self.checkout = cv.time(config[CONF_CHECKOUT])
        lockname_raw = config.get(CONF_LOCK_ENTRY)
        previous_lockname = self.lockname
        previous_max_events = self.max_events
        previous_start_slot = self.start_slot
        self.lockname = (
            slugify(lockname_raw) if lockname_raw and lockname_raw.strip() else None
        )
        self.max_events = int(str(config.get(CONF_MAX_EVENTS)))
        self.start_slot = int(str(config.get(CONF_START_SLOT)))
        # Keep event_overrides in sync with config changes
        if self.lockname:
            overrides_stale = (
                self.event_overrides is None
                or self.lockname != previous_lockname
                or self.max_events != previous_max_events
                or self.start_slot != previous_start_slot
            )
            if overrides_stale:
                self.event_overrides = EventOverrides(self.start_slot, self.max_events)
                await self.async_setup_keymaster_overrides()
        else:
            self.event_overrides = None
        self.days = config[CONF_DAYS]
        self.code_generator = config.get(CONF_CODE_GENERATION, DEFAULT_CODE_GENERATION)
        self.should_update_code = bool(config.get(CONF_SHOULD_UPDATE_CODE))
        self.code_length = config.get(CONF_CODE_LENGTH, DEFAULT_CODE_LENGTH)
        self.ignore_non_reserved = bool(config.get(CONF_IGNORE_NON_RESERVED))
        self.verify_ssl = bool(config.get(CONF_VERIFY_SSL))

        await self.async_request_refresh()

    async def update_event_overrides(
        self,
        slot: int,
        slot_code: str,
        slot_name: str,
        start_time: datetime,
        end_time: datetime,
        *,
        request_refresh: bool = True,
    ) -> None:
        """Update the event overrides with the ServiceCall data."""
        _LOGGER.debug("In update_event_overrides")

        if self.event_overrides:
            self.event_overrides.update(
                slot,
                slot_code,
                slot_name,
                start_time,
                end_time,
                self.event_prefix,
            )

        if request_refresh:
            await self.async_request_refresh()

    async def _ical_parser(
        self, calendar: Calendar, from_date: datetime, to_date: datetime
    ) -> list[CalendarEvent]:
        """Return a sorted list of events from a icalendar object."""

        events: list[CalendarEvent] = []

        _LOGGER.debug(
            "In _ical_parser:: from_date: %s; to_date: %s", from_date, to_date
        )

        for event in calendar.walk("VEVENT"):
            # RRULEs should not exist in AirBnB bookings, so log and error and
            # skip
            if "RRULE" in event:
                _LOGGER.error("RRULE in event: %s", str(event["SUMMARY"]))

            elif "Check-in" in event["SUMMARY"] or "Check-out" in event["SUMMARY"]:
                _LOGGER.debug("Smoobu extra event, ignoring")

            else:
                # Let's use the same magic as for rrules to get this (as) right
                # (as possible)
                try:
                    # Just ignore events that ended a long time ago
                    if "DTEND" in event and event[
                        "DTEND"
                    ].dt < from_date.date() - timedelta(days=EVENT_AGE_THRESHOLD_DAYS):
                        continue
                except (AttributeError, TypeError):
                    pass

                try:
                    # Ignore dates that are too far in the future
                    if "DTSTART" in event and event["DTSTART"].dt > to_date.date():
                        continue
                except (AttributeError, TypeError):
                    pass

                # Ignore Blocked or Not available by default, but if false,
                # keep the events.
                if self.ignore_non_reserved:
                    if any(x in event["SUMMARY"] for x in ["Blocked", "Not available"]):
                        # Skip Blocked or 'Not available' events
                        continue

                if "DESCRIPTION" in event:
                    slot_name = get_slot_name(
                        event["SUMMARY"], event["DESCRIPTION"], ""
                    )
                else:
                    # VRBO and Booking.com do not have a DESCRIPTION element
                    slot_name = get_slot_name(event["SUMMARY"], "", "")

                override = None
                if slot_name and self.event_overrides:
                    override = self.event_overrides.get_slot_with_name(slot_name)

                if override:
                    # Get start & end overrides in the correct timezone
                    # Overrides are stored in UTC since Keymaster's time
                    # start end end configurations values are in UTC
                    start_time: datetime = override["start_time"].astimezone(
                        self.timezone
                    )
                    end_time: datetime = override["end_time"].astimezone(self.timezone)
                    checkin: time = start_time.time()
                    checkout: time = end_time.time()
                    _LOGGER.debug("Checkin: %s, Checkout: %s", checkin, checkout)
                else:
                    try:
                        # If the event has a time, use that, otherwise use the
                        # default checkin/checkout times
                        # No need to do tz conversion here, as the
                        # DTSTART and DTEND are already in the correct timezone
                        checkin = event["DTSTART"].dt.time()
                        checkout = event["DTEND"].dt.time()
                    except AttributeError:
                        checkin = self.checkin
                        checkout = self.checkout

                _LOGGER.debug("Checkin: %s, Checkout: %s", checkin, checkout)
                _LOGGER.debug("DTSTART in event: %s", event["DTSTART"].dt)
                dtstart: datetime = datetime.combine(
                    event["DTSTART"].dt, checkin, self.timezone
                )
                # convert dtstart to UTC
                dtstart = dt.as_utc(dtstart)

                start: datetime = dtstart

                if "DTEND" not in event:
                    dtend: datetime = dtstart
                else:
                    _LOGGER.debug("DTEND in event: %s", event["DTEND"].dt)
                    dtend = datetime.combine(event["DTEND"].dt, checkout, self.timezone)
                # convert dtend to UTC
                dtend = dt.as_utc(dtend)
                end = dtend

                # Modify the SUMMARY if we have an event_prefix
                if self.event_prefix:
                    event["SUMMARY"] = self.event_prefix + " " + event["SUMMARY"]

                cal_event: CalendarEvent | None = await self._ical_event(
                    start, end, from_date, event
                )
                if cal_event:
                    events.append(cal_event)

        events.sort(key=lambda k: k.start)
        return events

    async def _ical_event(
        self,
        start: dt.dt.datetime,
        end: dt.dt.datetime,
        from_date: dt.dt.datetime,
        event: dict[Any, Any],
    ) -> CalendarEvent | None:
        """Ensure that events are within the start and end."""
        _LOGGER.debug(
            "Running _ical_event for %s", str(event.get("SUMMARY", "Unknown"))
        )
        _LOGGER.debug("Start: %s, End: %s", start, end)
        _LOGGER.debug("From: %s", from_date)
        # Ignore events that ended this midnight.
        if (dt.as_utc(end) < dt.as_utc(from_date)) or (
            dt.as_utc(end).date() == dt.as_utc(from_date).date()
            and end.hour == 0
            and end.minute == 0
            and end.second == 0
        ):
            _LOGGER.debug("This event has already ended")
            return None
        _LOGGER.debug(
            "Start: %s Tzinfo: %s Default: %s StartAs %s",
            str(start),
            str(start.tzinfo),
            self.timezone,
            start.astimezone(self.timezone),
        )
        description = event.get("DESCRIPTION")

        cal_event = CalendarEvent(
            description=description,
            end=end.astimezone(self.timezone),
            location=event.get("LOCATION"),
            summary=event.get("SUMMARY", "Unknown"),
            start=start.astimezone(self.timezone),
        )

        _LOGGER.debug("Event to add: %s", cal_event)
        return cal_event
