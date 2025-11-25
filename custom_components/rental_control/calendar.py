# SPDX-FileCopyrightText: 2021 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Support for iCal-URLs."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.calendar import CalendarEntity
from homeassistant.components.calendar import CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory

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

    async_add_entities([calendar], True)

    return True


class RentalControlCalendar(CalendarEntity):
    """A device for getting the next Task from a WebDav Calendar."""

    def __init__(self, coordinator: RentalControlCoordinator) -> None:
        """Create the iCal Calendar Event Device."""
        self._available: bool = False
        self._entity_category: EntityCategory = EntityCategory.DIAGNOSTIC
        self._event: CalendarEvent | None = None
        self._name: str = f"{NAME} {coordinator.name}"
        self.coordinator: RentalControlCoordinator = coordinator
        self._unique_id: str = gen_uuid(f"{self.coordinator.unique_id} calendar")

    @property
    def available(self) -> bool:
        """Return the calendar availablity."""
        return self._available

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
        return self._event

    @property
    def name(self) -> str:
        """Return the name of the entity."""
        return self._name

    @property
    def unique_id(self) -> str:
        """Return the unique_id."""
        return self._unique_id

    async def async_get_events(self, hass, start_date, end_date) -> Any:
        """Get all events in a specific time frame."""
        _LOGGER.debug("Running RentalControlCalendar async get events")
        return await self.coordinator.async_get_events(hass, start_date, end_date)

    async def async_update(self) -> None:
        """Update event data."""
        _LOGGER.debug("Running RentalControlCalendar async update for %s", self.name)
        await self.coordinator.update()
        self._event = self.coordinator.event

        if self.coordinator.calendar_ready:
            self._available = True
