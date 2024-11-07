# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2021 Andrew Grimberg <tykeal@bardicgrove.org>
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
from datetime import date
from datetime import datetime
from datetime import time
from datetime import timedelta
import functools
import logging
from typing import Any
from typing import Dict
from zoneinfo import ZoneInfo  # noreorder

import async_timeout
from homeassistant.components.calendar import CalendarEvent
from homeassistant.components.persistent_notification import async_create
from homeassistant.components.persistent_notification import async_dismiss
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.const import CONF_URL
from homeassistant.const import CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt
from icalendar import Calendar
import voluptuous as vol
import x_wr_timezone

from .config_flow import _lock_entry_convert as lock_entry_convert
from .const import ATTR_NAME
from .const import ATTR_NOTIFICATION_SOURCE
from .const import CONF_CHECKIN
from .const import CONF_CHECKOUT
from .const import CONF_CODE_GENERATION
from .const import CONF_SHOULD_UPDATE_CODE
from .const import CONF_CODE_LENGTH
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
from .const import COORDINATOR
from .const import DEFAULT_CODE_GENERATION
from .const import DEFAULT_SHOULD_UPDATE_CODE
from .const import DEFAULT_CODE_LENGTH
from .const import DEFAULT_GENERATE
from .const import DEFAULT_REFRESH_FREQUENCY
from .const import DOMAIN
from .const import EVENT_RENTAL_CONTROL_REFRESH
from .const import NAME
from .const import PLATFORMS
from .const import REQUEST_TIMEOUT
from .const import UNSUB_LISTENERS
from .const import VERSION
from .event_overrides import EventOverrides
from .sensors.calsensor import RentalControlCalSensor
from .util import async_reload_package_platforms
from .util import delete_rc_and_base_folder
from .util import gen_uuid
from .util import get_slot_name
from .util import handle_state_change

_LOGGER = logging.getLogger(__name__)


CONFIG_SCHEMA = vol.Schema({DOMAIN: vol.Schema({})}, extra=vol.ALLOW_EXTRA)

SERVICE_GENERATE_PACKAGE = "generate_package"
SERVICE_UPDATE_CODE_SLOT = "update_code_slot"


def setup(hass, config):  # pylint: disable=unused-argument
    """Set up this integration with config flow."""
    return True


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Set up Rental Control from a config entry."""
    config = config_entry.data
    _LOGGER.debug(
        "Running init async_setup_entry for calendar %s", config.get(CONF_NAME)
    )

    should_generate_package = config.get(CONF_GENERATE)

    updated_config = config.copy()
    updated_config.pop(CONF_GENERATE, None)
    if updated_config != config_entry.data:
        hass.config_entries.async_update_entry(config_entry, data=updated_config)

    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}

    coordinator = RentalControl(
        hass=hass,
        config_entry=config_entry,
    )

    hass.data[DOMAIN][config_entry.entry_id] = {
        COORDINATOR: coordinator,
        UNSUB_LISTENERS: [],
    }

    # Start listeners if needed
    await async_start_listener(hass, config_entry)

    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)

    config_entry.add_update_listener(update_listener)

    # remove files if needed
    if should_generate_package:
        delete_rc_and_base_folder(hass, config_entry)

    return True


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    """Handle removal of an entry."""
    config = config_entry.data
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
                hass.config_entries.async_forward_entry_unload(config_entry, component)
                for component in PLATFORMS
            ]
        )
    )

    if unload_ok:
        # Remove all package files and the base folder if needed
        await hass.async_add_executor_job(delete_rc_and_base_folder, hass, config_entry)

        await async_reload_package_platforms(hass)

        # Unsubscribe from any listeners
        for unsub_listener in hass.data[DOMAIN][config_entry.entry_id].get(
            UNSUB_LISTENERS, []
        ):
            unsub_listener()
        hass.data[DOMAIN][config_entry.entry_id].get(UNSUB_LISTENERS, []).clear()

        hass.data[DOMAIN].pop(config_entry.entry_id)

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
            version=2,
        )
        version = 2
        _LOGGER.debug("Migration to version %s complete", config_entry.version)

    # 2 -> 3: Migrate lock
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
                version=3,
            )

        version = 3
        _LOGGER.debug("Migration to version %s complete", config_entry.version)

    # 3 -> 4: Migrate code length
    if version == 3:
        _LOGGER.debug("Migrating from version %s", version)
        if CONF_CODE_LENGTH not in config_entry.data:
            data = config_entry.data.copy()
            data[CONF_CODE_LENGTH] = DEFAULT_CODE_LENGTH
            hass.config_entries.async_update_entry(
                entry=config_entry,
                unique_id=config_entry.unique_id,
                data=data,
                version=4,
            )

        version = 4
        _LOGGER.debug("Migration to version %s complete", config_entry.version)

    # 4 -> 5: Drop startup automation
    if version == 4:
        _LOGGER.debug(f"Migrating from version {version}")

        data = config_entry.data.copy()
        data[CONF_GENERATE] = DEFAULT_GENERATE
        hass.config_entries.async_update_entry(
            entry=config_entry,
            unique_id=config_entry.unique_id,
            data=data,
            version=5,
        )

        version = 5
        _LOGGER.debug(f"Migration to version {config_entry.version} complete")

    # 5 -> 6: Drop package_path from configuration
    if version == 5:
        _LOGGER.debug(f"Migrating from version {version}")

        data = config_entry.data.copy()
        data.pop(CONF_PATH, None)
        hass.config_entries.async_update_entry(
            entry=config_entry,
            unique_id=config_entry.unique_id,
            data=data,
            version=6,
        )

        version = 6
        _LOGGER.debug(f"Migration to version {config_entry.version} complete")

    return True


async def update_listener(hass: HomeAssistant, config_entry: ConfigEntry) -> None:
    """Update listener."""
    # No need to update if the options match the data
    if not config_entry.options:
        return

    new_data = config_entry.options.copy()
    new_data.pop(CONF_GENERATE, None)

    coordinator = hass.data[DOMAIN][config_entry.entry_id][COORDINATOR]

    # do not update the creation datetime if it already exists (which it should)
    new_data[CONF_CREATION_DATETIME] = coordinator.created

    hass.config_entries.async_update_entry(
        entry=config_entry,
        unique_id=config_entry.unique_id,
        data=new_data,
        title=new_data[CONF_NAME],
        options={},
    )

    # Update the calendar config
    coordinator.update_config(new_data)

    # Unsubscribe to any listeners so we can create new ones
    for unsub_listener in hass.data[DOMAIN][config_entry.entry_id].get(
        UNSUB_LISTENERS, []
    ):
        unsub_listener()
    hass.data[DOMAIN][config_entry.entry_id].get(UNSUB_LISTENERS, []).clear()

    if coordinator.lockname:
        await async_start_listener(hass, config_entry)
    else:
        _LOGGER.debug("Skipping re-adding listeners")

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


async def async_start_listener(hass: HomeAssistant, config_entry: ConfigEntry) -> None:
    """Start tracking updates to keymaster input entities."""
    entities: list[str] = []

    _LOGGER.debug(f"entry_id = '{config_entry.unique_id}'")

    coordinator = hass.data[DOMAIN][config_entry.entry_id][COORDINATOR]
    lockname = coordinator.lockname

    _LOGGER.debug(f"lockname = '{lockname}'")

    for i in range(
        coordinator.start_slot, coordinator.start_slot + coordinator.max_events
    ):
        entities.append(f"input_text.{lockname}_pin_{i}")
        entities.append(f"input_text.{lockname}_name_{i}")
        entities.append(f"input_datetime.start_date_{lockname}_{i}")
        entities.append(f"input_datetime.end_date_{lockname}_{i}")

    hass.data[DOMAIN][config_entry.entry_id][UNSUB_LISTENERS].append(
        async_track_state_change_event(
            hass,
            [entity for entity in entities],
            functools.partial(handle_state_change, hass, config_entry),
        )
    )


class RentalControl:
    """Get a list of events."""

    # pylint: disable=too-many-instance-attributes

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry):
        """Set up a calendar object."""
        config = config_entry.data
        self.hass: HomeAssistant = hass
        self.config_entry: ConfigEntry = config_entry
        self._name: str = str(config.get(CONF_NAME))
        self._unique_id: str = str(config_entry.unique_id)
        self._entry_id: str = config_entry.entry_id
        self.event_prefix: str | None = config.get(CONF_EVENT_PREFIX)
        self.url: str = str(config.get(CONF_URL))
        self.timezone: dt.dt.tzinfo = ZoneInfo(str(config.get(CONF_TIMEZONE)))
        self.refresh_frequency: int = config.get(
            CONF_REFRESH_FREQUENCY, DEFAULT_REFRESH_FREQUENCY
        )
        # after initial setup our first refresh should happen ASAP
        self.next_refresh: dt.dt.datetime = dt.now()
        # our config flow guarantees that checkin and checkout are valid times
        # just use cv.time to get the parsed time object
        self.checkin: time = cv.time(config.get(CONF_CHECKIN))
        self.checkout: time = cv.time(config.get(CONF_CHECKOUT))
        self.start_slot: int = int(str(config.get(CONF_START_SLOT)))
        self.lockname: str | None = config.get(CONF_LOCK_ENTRY)
        self.max_events: int = int(str(config.get(CONF_MAX_EVENTS)))
        self.days: int = int(str(config.get(CONF_DAYS)))
        self.ignore_non_reserved: bool = bool(config.get(CONF_IGNORE_NON_RESERVED))
        self.verify_ssl: bool = bool(config.get(CONF_VERIFY_SSL))
        self.calendar: list[CalendarEvent] = []
        self.calendar_ready: bool = False
        self.calendar_loaded: bool = False
        self.overrides_loaded: bool = False
        self.event_overrides: EventOverrides | None = (
            EventOverrides(self.start_slot, self.max_events) if self.lockname else None
        )
        self.event_sensors: list[RentalControlCalSensor] = []
        self._events_ready: bool = False
        self.code_generator: str = config.get(
            CONF_CODE_GENERATION, DEFAULT_CODE_GENERATION
        )
         self.should_update_code: bool = config.get(
            CONF_SHOULD_UPDATE_CODE, DEFAULT_SHOULD_UPDATE_CODE
        )
        self.code_length: int = config.get(CONF_CODE_LENGTH, DEFAULT_CODE_LENGTH)
        self.event: CalendarEvent | None = None
        self.created: str = config.get(CONF_CREATION_DATETIME, str(dt.now()))
        self._version: str = VERSION

        # setup device
        device_registry = dr.async_get(hass)
        device_registry.async_get_or_create(
            config_entry_id=self._entry_id,
            identifiers={(DOMAIN, self.unique_id)},
            name=self.name,
            sw_version=self.version,
        )

    @property
    def device_info(self) -> Dict[str, Any]:
        """Return the device info block."""
        return {
            "identifiers": {(DOMAIN, self.unique_id)},
            "name": self.name,
            "sw_version": self.version,
        }

    @property
    def name(self) -> str:
        """Return the name."""
        return self._name

    @property
    def unique_id(self) -> str:
        """Return the unique id."""
        return self._unique_id

    @property
    def version(self) -> str:
        """Return the version."""
        return self._version

    @property
    def events_ready(self) -> bool:
        """Return the status of all the event sensors"""

        # Once all events report ready we don't keep checking
        if self._events_ready:
            return self._events_ready

        # If all sensors have not yet been created we're still starting
        if len(self.event_sensors) != self.max_events:
            return self._events_ready

        sensors_status = [event.available for event in self.event_sensors]
        self._events_ready = all(sensors_status)

        return self._events_ready

    async def async_get_events(self, hass, start_date, end_date) -> list[CalendarEvent]:  # pylint: disable=unused-argument
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

    async def update(self) -> None:
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

        # Get slot overrides on startup
        if not self.calendar_ready and self.lockname:
            for i in range(self.start_slot, self.start_slot + self.max_events):
                slot_code = self.hass.states.get(f"input_text.{self.lockname}_pin_{i}")
                if slot_code is None:
                    continue

                slot_name = self.hass.states.get(f"input_text.{self.lockname}_name_{i}")
                if slot_name is None:
                    continue

                start_time_state = self.hass.states.get(
                    f"input_datetime.start_date_{self.lockname}_{i}"
                )
                if start_time_state is None:
                    continue
                start_time = dt.parse_datetime(start_time_state.state)
                if start_time is None:
                    continue

                end_time_state = self.hass.states.get(
                    f"input_datetime.end_date_{self.lockname}_{i}"
                )
                if end_time_state is None:
                    continue
                end_time = dt.parse_datetime(end_time_state.state)
                if end_time is None:
                    continue

                await self.update_event_overrides(
                    i,
                    slot_code.state,
                    slot_name.state,
                    start_time,
                    end_time,
                )

        # always refresh the overrides
        if self.event_overrides:
            await self.event_overrides.async_check_overrides(self)

    def update_config(self, config) -> None:
        """Update config entries."""
        self._name = config.get(CONF_NAME)
        self.url = config.get(CONF_URL)
        self.timezone = ZoneInfo(config.get(CONF_TIMEZONE))
        self.refresh_frequency = config.get(CONF_REFRESH_FREQUENCY)
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
        self.should_update_code = config.get(
            CONF_SHOULD_UPDATE_CODE, DEFAULT_SHOULD_UPDATE_CODE
        )
        self.code_length = config.get(CONF_CODE_LENGTH, DEFAULT_CODE_LENGTH)
        self.ignore_non_reserved = config.get(CONF_IGNORE_NON_RESERVED)
        self.verify_ssl = config.get(CONF_VERIFY_SSL)

        # updated the calendar in case the fetch days has changed
        self.calendar = self._refresh_event_dict()

    async def update_event_overrides(
        self,
        slot: int,
        slot_code: str,
        slot_name: str,
        start_time: datetime,
        end_time: datetime,
    ) -> None:
        """Update the event overrides with the ServiceCall data."""
        _LOGGER.debug("In update_event_overrides")

        # temporary call new_update_event_overrides
        if self.event_overrides:
            self.event_overrides.update(
                slot, slot_code, slot_name, start_time, end_time, self.event_prefix
            )

            if self.event_overrides.ready and self.calendar_loaded:
                self.calendar_ready = True
        else:
            if self.calendar_loaded:
                self.calendar_ready = True

        # Overrides have updated, trigger refresh of calendar
        self.next_refresh = dt.now()

    async def _ical_parser(
        self, calendar: Calendar, from_date: dt.dt.datetime, to_date: dt.dt.datetime
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
                    checkin: time = override["start_time"].time()
                    checkout: time = override["end_time"].time()
                else:
                    try:
                        # If the event has a time, use that, otherwise use the
                        # default checkin/checkout times
                        checkin = event["DTSTART"].dt.time()
                        checkout = event["DTEND"].dt.time()
                    except AttributeError:
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

                cal_event = await self._ical_event(start, end, from_date, event)
                if cal_event:
                    events.append(cal_event)

        events.sort(key=lambda k: k.start)
        return events

    async def _ical_event(
        self,
        start: dt.dt.datetime,
        end: dt.dt.datetime,
        from_date: dt.dt.datetime,
        event: Dict[Any, Any],
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
            return None
        _LOGGER.debug(
            "Start: %s Tzinfo: %s Default: %s StartAs %s",
            str(start),
            str(start.tzinfo),
            self.timezone,
            start.astimezone(self.timezone),
        )
        try:
            description = event.get("DESCRIPTION")
        except KeyError:
            # VRBO and Booking.com do not have a DESCRIPTION element
            description = None

        cal_event = CalendarEvent(
            description=description,
            end=end.astimezone(self.timezone),
            location=event.get("LOCATION"),
            summary=event.get("SUMMARY", "Unknown"),
            start=start.astimezone(self.timezone),
        )

        _LOGGER.debug("Event to add: %s", str(CalendarEvent))
        return cal_event

    def _refresh_event_dict(self) -> list[CalendarEvent]:
        """Ensure that all events in the calendar are start before max days."""

        def _get_date(day: date | datetime) -> date:
            """Return the date from a datetime or date object."""

            _LOGGER.debug("In _get_date: %s", day)
            if isinstance(day, date):
                _LOGGER.debug("Returning date: %s", day)
                return day
            _LOGGER.debug("Returning date: %s", day.date())
            return day.date()

        cal = self.calendar
        days = dt.start_of_local_day() + timedelta(days=self.days)

        return [x for x in cal if _get_date(x.start) <= days.date()]

    async def _refresh_calendar(self) -> None:
        """Update list of upcoming events."""
        _LOGGER.debug("Running RentalControl _refresh_calendar for %s", self.name)

        session = async_get_clientsession(self.hass, verify_ssl=self.verify_ssl)
        with async_timeout.timeout(REQUEST_TIMEOUT):
            response = await session.get(self.url)
        if response.status != 200:
            _LOGGER.error(
                "%s returned %s - %s", self.url, response.status, response.reason
            )

            # Calendar has failed to load for some reason, unflag the calendar
            # being loaded
            self.calendar_loaded = False
        else:
            text = await response.text()
            # Some calendars are for some reason filled with NULL-bytes.
            # They break the parsing, so we get rid of them
            event_list = Calendar.from_ical(text.replace("\x00", ""))

            # If the calendar is using a non-standard timezone definition,
            # convert it to a standard one
            if "X-WR-TIMEZONE" in event_list:
                event_list = await self.hass.async_add_executor_job(
                    x_wr_timezone.to_standard, event_list
                )

            start_of_events = dt.start_of_local_day()
            end_of_events = dt.start_of_local_day() + timedelta(days=self.days)

            self.calendar = await self._ical_parser(
                event_list, start_of_events, end_of_events
            )

            self.calendar_loaded = True

            if self.lockname is None:
                self.overrides_loaded = True

            if self.overrides_loaded:
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

        # signal an update to all the event sensors
        await asyncio.gather(*[event.async_update() for event in self.event_sensors])
