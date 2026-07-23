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

from .const import COORDINATOR
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

    calendar = RentalControlCalendar(coordinator)

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


class RentalControlCalendar(
    CoordinatorEntity[RentalControlCoordinator], CalendarEntity
):
    """A device for getting the next Task from a WebDav Calendar."""

    def __init__(self, coordinator: RentalControlCoordinator) -> None:
        """Create the iCal Calendar Event Device."""
        super().__init__(coordinator)
        self._attr_has_entity_name = True
        self._attr_name = NAME
        self._entity_category: EntityCategory = EntityCategory.DIAGNOSTIC
        self._unique_id: str = gen_uuid(f"{coordinator.unique_id} calendar")

    @property
    def available(self) -> bool:
        """Return the calendar availability."""
        return bool(self.coordinator.last_update_success)

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
        return replace(
            upcoming,
            start=_datetime_to_date(upcoming.start),
            end=_datetime_to_date(upcoming.end),
        )

    @property
    def unique_id(self) -> str:
        """Return the unique_id."""
        return self._unique_id

    async def async_get_events(self, hass, start_date, end_date) -> Any:
        """Get all events in a specific time frame."""
        _LOGGER.debug("Running RentalControlCalendar async get events")
        events = await self.coordinator.async_get_events(hass, start_date, end_date)

        return [
            replace(
                e,
                start=_datetime_to_date(e.start),
                end=_datetime_to_date(e.end),
            )
            for e in events or []
        ]
