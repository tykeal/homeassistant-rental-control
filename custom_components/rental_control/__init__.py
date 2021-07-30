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
from .const import CONF_MAX_EVENTS
from .const import DOMAIN
from .const import PLATFORMS

_LOGGER = logging.getLogger(__name__)


CONFIG_SCHEMA = vol.Schema({DOMAIN: vol.Schema({})}, extra=vol.ALLOW_EXTRA)

MIN_TIME_BETWEEN_UPDATES = timedelta(seconds=120)


def setup(hass, config):  # pylint: disable=unused-argument
    """Set up this integration with config flow."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
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


class ICalEvents:
    """Get a list of events."""

    # pylint: disable=too-many-instance-attributes

    def __init__(self, hass, config):
        """Set up a calendar object."""
        self.hass = hass
        self.name = config.get(CONF_NAME)
        self.url = config.get(CONF_URL)
        # our config flow guarantees that checkin and checkout are valid times
        # just use cv.time to get the parsed time object
        self.checkin = cv.time(config.get(CONF_CHECKIN))
        self.checkout = cv.time(config.get(CONF_CHECKOUT))
        self.max_events = config.get(CONF_MAX_EVENTS)
        self.days = config.get(CONF_DAYS)
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
        async with session.get(self.url) as response:
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

    def _ical_parser(self, calendar, from_date, to_date):
        """Return a sorted list of events from a icalendar object."""

        events = []

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
                    ].dt.date() < from_date.date() - timedelta(days=30):
                        continue
                except Exception:  # pylint: disable=broad-except
                    pass
                try:
                    # Ignore dates that are too far in the future
                    if "DSTART" in event and event["DTSTART"].dt <= to_date.date():
                        continue
                except Exception:  # pylint: disable=broad-except
                    pass

                _LOGGER.debug("DTSTART in event: %s", event["DTSTART"].dt)
                dtstart = datetime.combine(
                    event["DTSTART"].dt, self.checkin, dt.DEFAULT_TIME_ZONE
                )

                start = dtstart

                if "DTEND" not in event:
                    dtend = dtstart
                else:
                    _LOGGER.debug("DTEND in event")
                    dtend = datetime.combine(
                        event["DTEND"].dt, self.checkout, dt.DEFAULT_TIME_ZONE
                    )
                end = dtend

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
            dt.DEFAULT_TIME_ZONE,
            start.astimezone(dt.DEFAULT_TIME_ZONE),
        )
        event_dict = {
            "summary": event.get("SUMMARY", "Unknown"),
            "start": start.astimezone(dt.DEFAULT_TIME_ZONE),
            "end": end.astimezone(dt.DEFAULT_TIME_ZONE),
            "location": event.get("LOCATION"),
            "description": event.get("DESCRIPTION"),
            "all_day": self.all_day,
        }
        _LOGGER.debug("Event to add: %s", str(event_dict))
        return event_dict
