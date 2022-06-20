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
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from datetime import timedelta
from zoneinfo import ZoneInfo  # noreorder

import async_timeout
import homeassistant.helpers.config_validation as cv
import icalendar
import voluptuous as vol
from homeassistant.components.calendar import CalendarEvent
from homeassistant.components.persistent_notification import async_create
from homeassistant.components.persistent_notification import async_dismiss
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.const import CONF_URL
from homeassistant.const import CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.core import ServiceCall
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt

from .config_flow import _lock_entry_convert as lock_entry_convert
from .const import ATTR_NAME
from .const import ATTR_NOTIFICATION_SOURCE
from .const import CONF_CHECKIN
from .const import CONF_CHECKOUT
from .const import CONF_CODE_GENERATION
from .const import CONF_CREATION_DATETIME
from .const import CONF_DAYS
from .const import CONF_EVENT_PREFIX
from .const import CONF_GENERATE
from .const import CONF_IGNORE_NON_RESERVED
from .const import CONF_LOCK_ENTRY
from .const import CONF_MAX_EVENTS
from .const import CONF_PATH
from .const import CONF_REFRESH_FREQUENCY
from .const import CONF_START_SLOT
from .const import CONF_TIMEZONE
from .const import DEFAULT_CODE_GENERATION
from .const import DEFAULT_REFRESH_FREQUENCY
from .const import DOMAIN
from .const import EVENT_RENTAL_CONTROL_REFRESH
from .const import NAME
from .const import PLATFORMS
from .const import REQUEST_TIMEOUT
from .const import VERSION
from .services import generate_package_files
from .services import update_code_slot
from .util import async_reload_package_platforms
from .util import delete_rc_and_base_folder
from .util import fire_clear_code
from .util import gen_uuid
from .util import get_slot_name

_LOGGER = logging.getLogger(__name__)


CONFIG_SCHEMA = vol.Schema({DOMAIN: vol.Schema({})}, extra=vol.ALLOW_EXTRA)

SERVICE_GENERATE_PACKAGE = "generate_package"
SERVICE_UPDATE_CODE_SLOT = "update_code_slot"


def setup(hass, config):  # pylint: disable=unused-argument
    """Set up this integration with config flow."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Rental Control from a config entry."""
    config = entry.data
    _LOGGER.debug(
        "Running init async_setup_entry for calendar %s", config.get(CONF_NAME)
    )

    should_generate_package = config.get(CONF_GENERATE)

    updated_config = config.copy()
    updated_config.pop(CONF_GENERATE, None)
    if updated_config != entry.data:
        hass.config_entries.async_update_entry(entry, data=updated_config)

    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}
    hass.data[DOMAIN][entry.unique_id] = RentalControl(
        hass=hass, config=config, unique_id=entry.unique_id
    )

    for component in PLATFORMS:
        hass.async_create_task(
            hass.config_entries.async_forward_entry_setup(entry, component)
        )

    entry.add_update_listener(update_listener)

    # Generate package files
    async def _generate_package(service: ServiceCall) -> None:
        """Generate the package files."""
        _LOGGER.debug("In _generate_package: '%s'", service)
        await generate_package_files(hass, service.data["rental_control_name"])

    hass.services.async_register(
        DOMAIN,
        SERVICE_GENERATE_PACKAGE,
        _generate_package,
    )

    # Update Code Slot
    async def _update_code_slot(service: ServiceCall) -> None:
        """Update code slot with Keymaster information."""
        _LOGGER.debug("Update Code Slot service: %s", service)

        await update_code_slot(hass, service)

    hass.services.async_register(
        DOMAIN,
        SERVICE_UPDATE_CODE_SLOT,
        _update_code_slot,
    )

    # generate files if needed
    if should_generate_package:
        rc_name = config.get(CONF_NAME)
        servicedata = {"rental_control_name": rc_name}
        await hass.services.async_call(
            DOMAIN, SERVICE_GENERATE_PACKAGE, servicedata, blocking=True
        )

        _LOGGER.debug("Firing refresh event")
        # Fire an event for the startup automation to capture
        hass.bus.fire(
            EVENT_RENTAL_CONTROL_REFRESH,
            event_data={
                ATTR_NOTIFICATION_SOURCE: "event",
                ATTR_NAME: rc_name,
            },
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Handle removal of an entry."""
    config = entry.data
    rc_name = config.get(CONF_NAME)
    _LOGGER.debug("Running async_unload_entry for rental_control %s", rc_name)

    notification_id = f"{DOMAIN}_{rc_name}_unload"
    async_create(
        hass,
        (
            f"Removing `{rc_name}` and all of the files that were generated for "
            "it. This may take some time so don't panic. This message will "
            "automatically clear when removal is complete."
        ),
        title=f"{NAME} - Removing `{rc_name}`",
        notification_id=notification_id,
    )

    unload_ok = all(
        await asyncio.gather(
            *[
                hass.config_entries.async_forward_entry_unload(entry, component)
                for component in PLATFORMS
            ]
        )
    )

    if unload_ok:
        # Remove all package files and the base folder if needed
        await hass.async_add_executor_job(delete_rc_and_base_folder, hass, config)

        await async_reload_package_platforms(hass)

        hass.data[DOMAIN].pop(entry.unique_id)

    async_dismiss(hass, notification_id)

    return unload_ok


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate configuration."""

    version = config_entry.version

    # 1 -> 2: Migrate keys
    if version == 1:
        _LOGGER.debug("Migrating from version %s", version)
        data = config_entry.data.copy()

        data[CONF_CREATION_DATETIME] = str(dt.now())
        hass.config_entries.async_update_entry(
            entry=config_entry,
            unique_id=gen_uuid(data[CONF_CREATION_DATETIME]),
            data=data,
        )
        config_entry.version = 2
        _LOGGER.debug("Migration to version %s complete", config_entry.version)

    # 2 -> 3: Migrate l
    if version == 2:
        _LOGGER.debug("Migrating from version %s", version)
        if (
            CONF_LOCK_ENTRY in config_entry.data
            and config_entry.data[CONF_LOCK_ENTRY] is not None
        ):
            data = config_entry.data.copy()
            convert = lock_entry_convert(hass, config_entry.data[CONF_LOCK_ENTRY], True)
            data[CONF_LOCK_ENTRY] = convert
            hass.config_entries.async_update_entry(
                entry=config_entry,
                unique_id=config_entry.unique_id,
                data=data,
            )

        config_entry.version = 3
        _LOGGER.debug("Migration to version %s complete", config_entry.version)

    return True


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Update listener."""
    # No need to update if the options match the data
    if not entry.options:
        return

    new_data = entry.options.copy()
    new_data.pop(CONF_GENERATE, None)

    old_data = hass.data[DOMAIN][entry.unique_id]

    # do not update the creation datetime if it already exists (which it should)
    new_data[CONF_CREATION_DATETIME] = old_data.created

    hass.config_entries.async_update_entry(
        entry=entry,
        unique_id=entry.unique_id,
        data=new_data,
        title=new_data[CONF_NAME],
        options={},
    )

    # Update package files
    if new_data[CONF_LOCK_ENTRY]:
        rc_name = new_data[CONF_NAME]
        servicedata = {"rental_control_name": rc_name}
        await hass.services.async_call(
            DOMAIN, SERVICE_GENERATE_PACKAGE, servicedata, blocking=True
        )

        _LOGGER.debug("Firing refresh event")
        # Fire an event for the startup automation to capture
        hass.bus.fire(
            EVENT_RENTAL_CONTROL_REFRESH,
            event_data={
                ATTR_NOTIFICATION_SOURCE: "event",
                ATTR_NAME: rc_name,
            },
        )

    # Update the calendar config
    hass.data[DOMAIN][entry.unique_id].update_config(new_data)


class RentalControl:
    """Get a list of events."""

    # pylint: disable=too-many-instance-attributes

    def __init__(self, hass, config, unique_id):
        """Set up a calendar object."""
        self.hass = hass
        self._name = config.get(CONF_NAME)
        self._unique_id = unique_id
        self.event_prefix = config.get(CONF_EVENT_PREFIX)
        self.url = config.get(CONF_URL)
        # Early versions did not have these variables, as such it may not be
        # set, this should guard against issues until we're certain we can
        # remove this guard.
        try:
            self.timezone = ZoneInfo(config.get(CONF_TIMEZONE))
        except TypeError:
            self.timezone = dt.DEFAULT_TIME_ZONE
        self.refresh_frequency = config.get(CONF_REFRESH_FREQUENCY)
        if self.refresh_frequency is None:
            self.refresh_frequency = DEFAULT_REFRESH_FREQUENCY
        # after initial setup our first refresh should happen ASAP
        self.next_refresh = dt.now()
        # our config flow guarantees that checkin and checkout are valid times
        # just use cv.time to get the parsed time object
        self.checkin = cv.time(config.get(CONF_CHECKIN))
        self.checkout = cv.time(config.get(CONF_CHECKOUT))
        self.start_slot = config.get(CONF_START_SLOT)
        self.lockname = config.get(CONF_LOCK_ENTRY)
        self.max_events = config.get(CONF_MAX_EVENTS)
        self.days = config.get(CONF_DAYS)
        self.ignore_non_reserved = config.get(CONF_IGNORE_NON_RESERVED)
        self.verify_ssl = config.get(CONF_VERIFY_SSL)
        self.calendar = []
        self.calendar_ready = False
        self.event_overrides = {}
        self.code_generator = config.get(CONF_CODE_GENERATION, DEFAULT_CODE_GENERATION)
        self.event = None
        self.all_day = False
        self.created = config.get(CONF_CREATION_DATETIME, str(dt.now()))
        self._version = VERSION

        # Alert users if they have a lock defined but no packages path
        # this would happen if they've upgraded from an older version where
        # they already had a lock definition defined even though it didn't
        # do anything
        self.path = config.get(CONF_PATH, None)
        if self.path is None and self.lockname is not None:
            notification_id = f"{DOMAIN}_{self._name}_missing_path"
            async_create(
                hass,
                (f"Please update configuration for {NAME} {self._name}"),
                title=f"{NAME} - Missing configuration",
                notification_id=notification_id,
            )

        # setup device
        device_registry = dr.async_get(hass)
        device_registry.async_get_or_create(
            config_entry_id=self.unique_id,
            identifiers={(DOMAIN, self.unique_id)},
            name=self.name,
            sw_version=self.version,
        )

    @property
    def device_info(self):
        """Return the device info block."""
        return {
            "identifiers": {(DOMAIN, self.unique_id)},
            "name": self.name,
            "sw_version": self.version,
        }

    @property
    def name(self):
        """Return the name."""
        return self._name

    @property
    def unique_id(self):
        """Return the unique id."""
        return self._unique_id

    @property
    def version(self):
        """Return the version."""
        return self._version

    async def async_get_events(
        self, hass, start_date, end_date
    ) -> list[CalendarEvent]:  # pylint: disable=unused-argument
        """Get list of upcoming events."""
        _LOGGER.debug("Running RentalControl async_get_events")
        events = []
        if len(self.calendar) > 0:
            for event in self.calendar:
                _LOGGER.debug(
                    "Checking if event %s has start %s and end %s within in the limit: %s and %s",
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

    async def update(self):
        """Regularly update the calendar."""
        _LOGGER.debug("Running RentalControl update for calendar %s", self.name)

        now = dt.now()
        _LOGGER.debug("Refresh frequency is: %d", self.refresh_frequency)
        _LOGGER.debug("Current time is: %s", now)
        _LOGGER.debug("Next refresh is: %s", self.next_refresh)
        if now >= self.next_refresh:
            # Update the next refresh time before doing the calendar update
            # If refresh_frequency is 0, then set the refresh for a little in
            # the future to avoid having multiple calls to the calendar refresh
            # happen at the same time
            if self.refresh_frequency == 0:
                self.next_refresh = now + timedelta(seconds=10)
            else:
                self.next_refresh = now + timedelta(minutes=self.refresh_frequency)
            _LOGGER.debug("Updating next refresh to %s", self.next_refresh)
            await self._refresh_calendar()

    def update_config(self, config):
        """Update config entries."""
        self._name = config.get(CONF_NAME)
        self.url = config.get(CONF_URL)
        # Early versions did not have these variables, as such it may not be
        # set, this should guard against issues until we're certain
        # we can remove this guard.
        try:
            self.timezone = ZoneInfo(config.get(CONF_TIMEZONE))
        except TypeError:
            self.timezone = dt.DEFAULT_TIME_ZONE
        self.refresh_frequency = config.get(CONF_REFRESH_FREQUENCY)
        if self.refresh_frequency is None:
            self.refresh_frequency = DEFAULT_REFRESH_FREQUENCY
        # always do a refresh ASAP after a config change
        self.next_refresh = dt.now()
        self.event_prefix = config.get(CONF_EVENT_PREFIX)
        # our config flow guarantees that checkin and checkout are valid times
        # just use cv.time to get the parsed time object
        self.checkin = cv.time(config.get(CONF_CHECKIN))
        self.checkout = cv.time(config.get(CONF_CHECKOUT))
        self.lockname = config.get(CONF_LOCK_ENTRY)
        self.max_events = config.get(CONF_MAX_EVENTS)
        self.days = config.get(CONF_DAYS)
        self.code_generator = config.get(CONF_CODE_GENERATION, DEFAULT_CODE_GENERATION)
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

    async def update_event_overrides(
        self,
        slot: int,
        slot_code: str,
        slot_name: str,
        start_time: datetime.datetime,
        end_time: datetime.datetime,
    ):
        """Update the event overrides with the ServiceCall data."""
        _LOGGER.debug("In update_event_overrides")

        event_overrides = self.event_overrides.copy()

        if slot_name:
            _LOGGER.debug("Searching by slot_name: '%s'", slot_name)
            regex = r"^(" + self.event_prefix + " )?(.*)$"
            matches = re.findall(regex, slot_name)
            if matches[0][1] not in event_overrides:
                _LOGGER.debug("Event '%s' not in overrides", matches[0][1])
                for event in event_overrides.keys():
                    if slot == event_overrides[event]["slot"]:
                        _LOGGER.debug("Slot '%d' is in event '%s'", slot, event)
                        del event_overrides[event]
                        break

            event_overrides[matches[0][1]] = {
                "slot": slot,
                "slot_code": slot_code,
                "start_time": start_time,
                "end_time": end_time,
            }
        else:
            _LOGGER.debug("Searching by slot: '%s'", slot)
            for event in event_overrides.keys():
                if slot == event_overrides[event]["slot"]:
                    _LOGGER.debug("Slot '%d' is in event '%s'", slot, event)
                    del event_overrides[event]
                    break

            event_overrides["Slot " + str(slot)] = {
                "slot": slot,
            }

        self.event_overrides = event_overrides

        _LOGGER.debug("event_overrides: '%s'", self.event_overrides)
        if len(self.event_overrides) == self.max_events:
            _LOGGER.debug("max_events reached, flagging as ready")
            self.calendar_ready = True
        else:
            _LOGGER.debug(
                "max_events not reached yet, calendar_ready is '%s'",
                self.calendar_ready,
            )

        # Overrides have updated, trigger refresh of calendar
        self.next_refresh = dt.now()

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

                slot_name = get_slot_name(event["SUMMARY"], event["DESCRIPTION"], None)

                override = None
                if slot_name and slot_name in self.event_overrides:
                    override = self.event_overrides[slot_name]
                    _LOGGER.debug("override: '%s'", override)
                    # If start and stop are the same, then we ignore the override
                    # This shouldn't happen except when a slot has been cleared
                    # In that instance we shouldn't find an override
                    if override["start_time"] == override["end_time"]:
                        _LOGGER.debug("override is now none")
                        override = None

                if override:
                    checkin = override["start_time"].time()
                    checkout = override["end_time"].time()
                else:
                    checkin = self.checkin
                    checkout = self.checkout

                _LOGGER.debug("DTSTART in event: %s", event["DTSTART"].dt)
                dtstart = datetime.combine(event["DTSTART"].dt, checkin, self.timezone)

                start = dtstart

                if "DTEND" not in event:
                    dtend = dtstart
                else:
                    _LOGGER.debug("DTEND in event: %s", event["DTEND"].dt)
                    dtend = datetime.combine(event["DTEND"].dt, checkout, self.timezone)
                end = dtend

                # Modify the SUMMARY if we have an event_prefix
                if self.event_prefix:
                    event["SUMMARY"] = self.event_prefix + " " + event["SUMMARY"]

                cal_event = self._ical_event(start, end, from_date, event, override)
                if cal_event:
                    events.append(cal_event)

        sorted_events = sorted(events, key=lambda k: k.start)
        return sorted_events

    def _ical_event(
        self, start, end, from_date, event, override
    ) -> CalendarEvent | None:
        """Ensure that events are within the start and end."""
        # Ignore events that ended this midnight.
        if (end.date() < from_date.date()) or (
            end.date() == from_date.date()
            and end.hour == 0
            and end.minute == 0
            and end.second == 0
        ):
            _LOGGER.debug("This event has already ended")
            if override:
                _LOGGER.info("Override exists for event, clearing slot")
                fire_clear_code(self.hass, override["slot"], self._name)
            return None
        _LOGGER.debug(
            "Start: %s Tzinfo: %s Default: %s StartAs %s",
            str(start),
            str(start.tzinfo),
            self.timezone,
            start.astimezone(self.timezone),
        )
        cal_event = CalendarEvent(
            description=event.get("DESCRIPTION"),
            end=end.astimezone(self.timezone),
            location=event.get("LOCATION"),
            summary=event.get("SUMMARY", "Unknown"),
            start=start.astimezone(self.timezone),
        )
        _LOGGER.debug("Event to add: %s", str(CalendarEvent))
        return cal_event

    def _refresh_event_dict(self):
        """Ensure that all events in the calendar are start before max days."""

        cal = self.calendar
        days = dt.start_of_local_day() + timedelta(days=self.days)

        return [x for x in cal if x.start.date() <= days.date()]

    async def _refresh_calendar(self):
        """Update list of upcoming events."""
        _LOGGER.debug("Running RentalControl _refresh_calendar for %s", self.name)

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

            if self.lockname is None:
                self.calendar_ready = True

        if len(self.calendar) > 0:
            found_next_event = False
            for event in self.calendar:
                if event.end > dt.now() and not found_next_event:
                    _LOGGER.debug(
                        "Event %s is the first event with end in the future: %s",
                        event.summary,
                        event.end,
                    )
                    self.event = event
                    found_next_event = True
