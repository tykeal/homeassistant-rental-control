"""Support for iCal-URLs."""
from __future__ import annotations

import logging

from homeassistant.components.calendar import CalendarEntity
from homeassistant.components.calendar import CalendarEvent
from homeassistant.const import CONF_NAME
from homeassistant.helpers.entity import EntityCategory

from .const import DOMAIN
from .const import NAME
from .util import gen_uuid

_LOGGER = logging.getLogger(__name__)
OFFSET = "!!"


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the iCal Calendar platform."""
    config = config_entry.data
    _LOGGER.debug("Running setup_platform for calendar")
    _LOGGER.debug("Conf: %s", config)
    name = config.get(CONF_NAME)

    rental_control_events = hass.data[DOMAIN][config_entry.unique_id]

    calendar = RentalCalendar(hass, f"{NAME} {name}", rental_control_events)

    async_add_entities([calendar], True)


class RentalCalendar(CalendarEntity):
    """A device for getting the next Task from a WebDav Calendar."""

    def __init__(
        self, hass, name, rental_control_events
    ):  # pylint: disable=unused-argument
        """Create the iCal Calendar Event Device."""
        self._entity_category = EntityCategory.DIAGNOSTIC
        self._event = None
        self._name = name
        self.rental_control_events = rental_control_events
        self._unique_id = gen_uuid(f"{self.rental_control_events.unique_id} calendar")

    @property
    def device_info(self):
        """Return the device info block."""
        return self.rental_control_events.device_info

    @property
    def entity_category(self):
        """Return the category."""
        return self._entity_category

    @property
    def event(self) -> CalendarEvent | None:
        """Return the next upcoming event."""
        return self._event

    @property
    def name(self):
        """Return the name of the entity."""
        return self._name

    @property
    def unique_id(self):
        """Return the unique_id."""
        return self._unique_id

    async def async_get_events(self, hass, start_date, end_date) -> list[CalendarEvent]:
        """Get all events in a specific time frame."""
        _LOGGER.debug("Running RentalCalendar async get events")
        return await self.rental_control_events.async_get_events(
            hass, start_date, end_date
        )

    async def async_update(self):
        """Update event data."""
        _LOGGER.debug("Running RentalCalendar async update for %s", self.name)
        await self.rental_control_events.update()
        self._event = self.rental_control_events.event
