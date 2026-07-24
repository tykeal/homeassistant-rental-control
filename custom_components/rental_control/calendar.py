# SPDX-FileCopyrightText: 2021 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Support for iCal-URLs."""

from __future__ import annotations

from dataclasses import replace
import datetime
import logging
from typing import Any

from homeassistant.components.calendar import CalendarEntity
from homeassistant.components.calendar import CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import CONF_DATE_ONLY  # ADDED
from .const import COORDINATOR
from .const import DEFAULT_DATE_ONLY  # ADDED
from .const import DOMAIN
from .const import NAME
from .coordinator import RentalControlCoordinator
from .util import gen_uuid

_LOGGER = logging.getLogger(__name__)
OFFSET = "!!"


async def async_setup_entry(
    hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities
) -> bool:
    """Set up the iCal Calendar platform."""
    config = config_entry.data
    _LOGGER.debug("Running setup_platform for calendar")
    _LOGGER.debug("Conf: %s", config)

    coordinator: RentalControlCoordinator = hass.data[DOMAIN][config_entry.entry_id][
        COORDINATOR
    ]

    # CHANGED: pass config_entry so the calendar can read the date_only option
    calendar = RentalControlCalendar(coordinator, config_entry)

    async_add_entities([calendar])

    return True


def _datetime_to_date(value: datetime.datetime | datetime.date) -> datetime.date:
    """Collapse a datetime to a local calendar date, leaving dates untouched."""
    # ICS events carry timezone info, so convert to the user's local timezone
    # BEFORE dropping the time. Calling .date() on a raw UTC datetime would
    # yield the UTC calendar day, which can be off by one.
    if isinstance(value, datetime.datetime):
        return dt_util.as_local(value).date()
    # Already a date (all-day event); date has no .date() method, so return
    # it unchanged rather than raising AttributeError.
    return value


# ADDED: collapse a reservation to the nights the property is occupied.
def _collapse_span(
    start: datetime.datetime | datetime.date,
    end: datetime.datetime | datetime.date,
) -> tuple[datetime.date, datetime.date]:
    """Collapse an event span to all-day dates covering the nights occupied."""
    # All-day end dates are EXCLUSIVE, so a checkout of 11:00 Jan 8 collapsing
    # to the date Jan 8 renders the stay as Jan 5-7 -- the nights the guest is
    # actually in the property. Checkout day is intentionally not shown. No
    # adjustment to the end date is needed.
    new_start = _datetime_to_date(start)
    new_end = _datetime_to_date(end)
    # A booking with no overnight stay would collapse to a zero-length span,
    # but CalendarEvent requires end > start. Clamp to a single day so the
    # entity does not raise
    if new_end <= new_start:
        new_end = new_start + datetime.timedelta(days=1)
    return new_start, new_end


class RentalControlCalendar(
    CoordinatorEntity[RentalControlCoordinator], CalendarEntity
):
    """A device for getting the next Task from a WebDav Calendar."""

    def __init__(
        self,
        coordinator: RentalControlCoordinator,
        config_entry: ConfigEntry,  # ADDED
    ) -> None:
        """Create the iCal Calendar Event Device."""
        super().__init__(coordinator)
        self._config_entry = config_entry  # ADDED
        self._attr_has_entity_name = True
        self._attr_name = NAME
        self._entity_category: EntityCategory = EntityCategory.DIAGNOSTIC
        self._unique_id: str = gen_uuid(f"{coordinator.unique_id} calendar")

    @property
    def available(self) -> bool:
        """Return the calendar availability."""
        return bool(self.coordinator.last_update_success)

    # ADDED: read live from the entry so a settings change takes effect
    # without needing a reload.
    @property
    def _date_only(self) -> bool:
        """Return whether date-only display is enabled."""
        return bool(self._config_entry.data.get(CONF_DATE_ONLY, DEFAULT_DATE_ONLY))

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info block."""
        return self.coordinator.device_info

    @property
    def entity_category(self) -> EntityCategory:
        """Return the category."""
        return self._entity_category

    @property
    def event(self) -> CalendarEvent | None:
        """Return the next upcoming event."""
        upcoming = self.coordinator.event
        if upcoming is None:
            return None
        # ADDED: when disabled, hand back the coordinator's object untouched.
        if not self._date_only:
            return upcoming
        start, end = _collapse_span(upcoming.start, upcoming.end)  # CHANGED
        return replace(upcoming, start=start, end=end)

    @property
    def unique_id(self) -> str:
        """Return the unique_id."""
        return self._unique_id

    async def async_get_events(self, hass, start_date, end_date) -> Any:
        """Get all events in a specific time frame."""
        _LOGGER.debug("Running RentalControlCalendar async get events")
        events = await self.coordinator.async_get_events(hass, start_date, end_date)

        # ADDED: when disabled, return the coordinator's list unchanged.
        if not self._date_only:
            return events

        # CHANGED: collapse each event's span rather than each date separately.
        collapsed = []
        for e in events or []:
            start, end = _collapse_span(e.start, e.end)
            collapsed.append(replace(e, start=start, end=end))
        return collapsed