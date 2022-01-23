# SPDX-License-Identifier: Apache-2.0
##############################################################################
# COPYRIGHT 2021 Andrew Grimberg
#
# All rights reserved. This program and the accompanying materials
# are made available under the terms of the Apache 2.0 License
# which accompanies this distribution, and is available at
# https://www.apache.org/licenses/LICENSE-2.0
#
# Contributors:
#   Andrew Grimberg - Initial implementation
##############################################################################
"""The Rental Control integration."""
import asyncio
import logging
from datetime import datetime
from datetime import timedelta
from zoneinfo import ZoneInfo  # noreorder

import async_timeout
import homeassistant.helpers.config_validation as cv
import icalendar
import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.const import CONF_URL
from homeassistant.const import CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt
from homeassistant.util import Throttle

from .const import CONF_CHECKIN
from .const import CONF_CHECKOUT
from .const import CONF_DAYS
from .const import CONF_EVENT_PREFIX
from .const import CONF_IGNORE_NON_RESERVED
from .const import CONF_MAX_EVENTS
from .const import CONF_TIMEZONE
from .const import DOMAIN
from .const import PLATFORMS
from .const import REQUEST_TIMEOUT

_LOGGER = logging.getLogger(__name__)


CONFIG_SCHEMA = vol.Schema({DOMAIN: vol.Schema({})}, extra=vol.ALLOW_EXTRA)

MIN_TIME_BETWEEN_UPDATES = timedelta(seconds=120)


def setup(hass, config):  # pylint: disable=unused-argument
    """Set up this integration with config flow."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Rental Control from a config entry."""
    config = entry.data
    _LOGGER.debug(
        "Running init async_setup_entry for calendar %s", config.get(CONF_NAME)
    )
    # TODO Store an API object for your platforms to access
    # hass.data[DOMAIN][entry.entry_id] = MyApi(...)
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}
    hass.data[DOMAIN][config.get(CONF_NAME)] = ICalEvents(hass=hass, config=config)

    for component in PLATFORMS:
        hass.async_create_task(
            hass.config_entries.async_forward_entry_setup(entry, component)
        )

    entry.add_update_listener(update_listener)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Unload a config entry."""
    config = entry.data
    _LOGGER.debug("Running async_unload_entry for calendar %s", config.get(CONF_NAME))
    unload_ok = all(
        await asyncio.gather(
            *[
                hass.config_entries.async_forward_entry_unload(entry, component)
                for component in PLATFORMS
            ]
        )
    )
    if unload_ok:
        hass.data[DOMAIN].pop(config.get(CONF_NAME))

    return unload_ok


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Update listener."""
    # No need to update if the options match the data
    if not entry.options:
        return

    new_data = entry.options.copy()

    hass.config_entries.async_update_entry(
        entry=entry,
        unique_id=entry.options[CONF_NAME],
        data=new_data,
        options={},
    )

    # Update the calendar config
    hass.data[DOMAIN][entry.data.get(CONF_NAME)].update_config(new_data)


class ICalEvents:
    """Get a list of events."""

    # pylint: disable=too-many-instance-attributes

    def __init__(self, hass, config):
        """Set up a calendar object."""
        self.hass = hass
        self.name = config.get(CONF_NAME)
        self.event_prefix = config.get(CONF_EVENT_PREFIX)
        self.url = config.get(CONF_URL)
        # Early versions did not have this variable, as such it may not be
        # set, this should guard against issues until we're certain we can
        # remove this guard.
        try:
            self.timezone = ZoneInfo(config.get(CONF_TIMEZONE))
        except TypeError:
            self.timezone = dt.DEFAULT_TIME_ZONE
        # our config flow guarantees that checkin and checkout are valid times
        # just use cv.time to get the parsed time object
        self.checkin = cv.time(config.get(CONF_CHECKIN))
        self.checkout = cv.time(config.get(CONF_CHECKOUT))
        self.max_events = config.get(CONF_MAX_EVENTS)
        self.days = config.get(CONF_DAYS)
        # Early versions did not have this variable, as such it may not be
        # set, this should guard against issues until we're certain
        # we can remove this guard.
        try:
            self.ignore_non_reserved = config.get(CONF_IGNORE_NON_RESERVED)
        except NameError:
            self.ignore_non_reserved = None
        self.verify_ssl = config.get(CONF_VERIFY_SSL)
        self.calendar = []
        self.event = None
        self.all_day = False

    async def async_get_events(
        self, hass, start_date, end_date
    ):  # pylint: disable=unused-argument
        """Get list of upcoming events."""
        _LOGGER.debug("Running ICalEvents async_get_events")
        events = []
        if len(self.calendar) > 0:
            for event in self.calendar:
                _LOGGER.debug(
                    "Checking if event %s has start %s and end %s within in the limit: %s and %s",
                    event["summary"],
                    event["start"],
                    event["end"],
                    start_date,
                    end_date,
                )

                if event["start"] < end_date and event["end"] > start_date:
                    _LOGGER.debug("... and it has")
                    events.append(event)
        return events

    @Throttle(MIN_TIME_BETWEEN_UPDATES)
    async def update(self):
        """Update list of upcoming events."""
        _LOGGER.debug("Running ICalEvents update for calendar %s", self.name)

        session = async_get_clientsession(self.hass, verify_ssl=self.verify_ssl)
        with async_timeout.timeout(REQUEST_TIMEOUT):
            response = await session.get(self.url)
        if response.status != 200:
            _LOGGER.error(
                "%s returned %s - %s", self.url, response.status, response.reason
            )
        else:
            text = await response.text()
            # Some calendars are for some reason filled with NULL-bytes.
            # They break the parsing, so we get rid of them
            event_list = icalendar.Calendar.from_ical(text.replace("\x00", ""))
            start_of_events = dt.start_of_local_day()
            end_of_events = dt.start_of_local_day() + timedelta(days=self.days)

            self.calendar = self._ical_parser(
                event_list, start_of_events, end_of_events
            )

        if len(self.calendar) > 0:
            found_next_event = False
            for event in self.calendar:
                if event["end"] > dt.now() and not found_next_event:
                    _LOGGER.debug(
                        "Event %s it the first event with end in the future: %s",
                        event["summary"],
                        event["end"],
                    )
                    self.event = event
                    found_next_event = True

    def update_config(self, config):
        """Update config entries."""
        self.name = config.get(CONF_NAME)
        self.url = config.get(CONF_URL)
        # Early versions did not have this variable, as such it may not be
        # set, this should guard against issues until we're certain
        # we can remove this guard.
        try:
            self.timezone = ZoneInfo(config.get(CONF_TIMEZONE))
        except TypeError:
            self.timezone = dt.DEFAULT_TIME_ZONE
        self.event_prefix = config.get(CONF_EVENT_PREFIX)
        # our config flow guarantees that checkin and checkout are valid times
        # just use cv.time to get the parsed time object
        self.checkin = cv.time(config.get(CONF_CHECKIN))
        self.checkout = cv.time(config.get(CONF_CHECKOUT))
        self.max_events = config.get(CONF_MAX_EVENTS)
        self.days = config.get(CONF_DAYS)
        # Early versions did not have this variable, as such it may not be
        # set, this should guard against issues until we're certain
        # we can remove this guard.
        try:
            self.ignore_non_reserved = config.get(CONF_IGNORE_NON_RESERVED)
        except NameError:
            self.ignore_non_reserved = None
        self.verify_ssl = config.get(CONF_VERIFY_SSL)

        # updated the calendar in case the fetch days has changed
        self.calendar = self._refresh_event_dict()

    def _ical_parser(self, calendar, from_date, to_date):
        """Return a sorted list of events from a icalendar object."""

        events = []

        _LOGGER.debug(
            "In _ical_parser:: from_date: %s; to_date: %s", from_date, to_date
        )

        for event in calendar.walk("VEVENT"):
            # RRULEs should not exist in AirBnB bookings, so log and error and
            # skip
            if "RRULE" in event:
                _LOGGER.error("RRULE in event: %s", str(event["SUMMARY"]))

            else:
                # Let's use the same magic as for rrules to get this (as) right
                # (as possible)
                try:
                    # Just ignore events that ended a long time ago
                    if "DTEND" in event and event[
                        "DTEND"
                    ].dt < from_date.date() - timedelta(days=30):
                        continue
                except Exception:  # pylint: disable=broad-except
                    pass

                try:
                    # Ignore dates that are too far in the future
                    if "DTSTART" in event and event["DTSTART"].dt > to_date.date():
                        continue
                except Exception:  # pylint: disable=broad-except
                    pass

                # Ignore Blocked or Not available by default, but if false,
                # keep the events.
                if (
                    isinstance(self.ignore_non_reserved, type(None))
                    or self.ignore_non_reserved
                ):
                    if any(x in event["SUMMARY"] for x in ["Blocked", "Not available"]):
                        # Skip Blocked or 'Not available' events
                        continue

                _LOGGER.debug("DTSTART in event: %s", event["DTSTART"].dt)
                dtstart = datetime.combine(
                    event["DTSTART"].dt, self.checkin, self.timezone
                )

                start = dtstart

                if "DTEND" not in event:
                    dtend = dtstart
                else:
                    _LOGGER.debug("DTEND in event: %s", event["DTEND"].dt)
                    dtend = datetime.combine(
                        event["DTEND"].dt, self.checkout, self.timezone
                    )
                end = dtend

                # Modify the SUMMARY if we have an event_prefix
                if self.event_prefix:
                    event["SUMMARY"] = self.event_prefix + " " + event["SUMMARY"]

                event_dict = self._ical_event_dict(start, end, from_date, event)
                if event_dict:
                    events.append(event_dict)

        sorted_events = sorted(events, key=lambda k: k["start"])
        return sorted_events

    def _ical_event_dict(self, start, end, from_date, event):
        """Ensure that events are within the start and end."""

        # Skip this event if it's in the past
        if end.date() < from_date.date():
            _LOGGER.debug("This event has already ended")
            return None
        # Ignore events that ended this midnight.
        if (
            end.date() == from_date.date()
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
        event_dict = {
            "summary": event.get("SUMMARY", "Unknown"),
            "start": start.astimezone(self.timezone),
            "end": end.astimezone(self.timezone),
            "location": event.get("LOCATION"),
            "description": event.get("DESCRIPTION"),
            "all_day": self.all_day,
        }
        _LOGGER.debug("Event to add: %s", str(event_dict))
        return event_dict

    def _refresh_event_dict(self):
        """Ensure that all events in the calendar are start before max days."""

        cal = self.calendar
        days = dt.start_of_local_day() + timedelta(days=self.days)

        return [x for x in cal if x["start"].date() <= days.date()]
