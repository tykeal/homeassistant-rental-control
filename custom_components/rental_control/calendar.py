"""Support for iCal-URLs."""
import copy
import logging

from homeassistant.components.calendar import calculate_offset
from homeassistant.components.calendar import CalendarEventDevice
from homeassistant.components.calendar import is_offset_reached
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

    calendar = ICalCalendarEventDevice(hass, f"{NAME} {name}", rental_control_events)

    async_add_entities([calendar], True)


class ICalCalendarEventDevice(CalendarEventDevice):
    """A device for getting the next Task from a WebDav Calendar."""

    def __init__(
        self, hass, name, rental_control_events
    ):  # pylint: disable=unused-argument
        """Create the iCal Calendar Event Device."""
        self._entity_category = EntityCategory.DIAGNOSTIC
        self._event = None
        self._name = name
        self._offset_reached = False
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
    def extra_state_attributes(self):
        """Return the custom state attributes."""
        return {"offset_reached": self._offset_reached}

    @property
    def event(self):
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

    async def async_get_events(self, hass, start_date, end_date):
        """Get all events in a specific time frame."""
        _LOGGER.debug("Running ICalCalendarEventDevice async get events")
        return await self.rental_control_events.async_get_events(
            hass, start_date, end_date
        )

    async def async_update(self):
        """Update event data."""
        _LOGGER.debug("Running ICalCalendarEventDevice async update for %s", self.name)
        await self.rental_control_events.update()
        event = copy.deepcopy(self.rental_control_events.event)
        if event is None:
            self._event = event
            return
        event = calculate_offset(event, OFFSET)
        self._event = copy.deepcopy(event)
        self._event["start"] = {}
        self._event["end"] = {}
        self._event["start"]["dateTime"] = event["start"].isoformat()
        self._event["end"]["dateTime"] = event["end"].isoformat()
        self._offset_reached = is_offset_reached(self.event)
        self._event["all_day"] = event["all_day"]
