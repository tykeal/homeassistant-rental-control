"""Creating sensors for upcoming events."""
import logging
from datetime import datetime
from datetime import timedelta

from homeassistant.const import CONF_NAME
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity import generate_entity_id

from .const import CONF_MAX_EVENTS
from .const import DOMAIN
from .const import ICON

_LOGGER = logging.getLogger(__name__)


async def async_setup_platform(
    hass, config, add_entities, discovery_info=None
):  # pylint: disable=unused-argument
    """Set up this integration with config flow."""
    return True


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the iCal Sensor."""
    config = config_entry.data
    name = config.get(CONF_NAME)
    max_events = config.get(CONF_MAX_EVENTS)

    rental_control_events = hass.data[DOMAIN][name]
    await rental_control_events.update()
    if rental_control_events.calendar is None:
        _LOGGER.error("Unable to fetch iCal")
        return False

    sensors = []
    for eventnumber in range(max_events):
        sensors.append(
            ICalSensor(hass, rental_control_events, DOMAIN + " " + name, eventnumber)
        )

    async_add_entities(sensors)


class ICalSensor(Entity):
    """
    Implementation of a iCal sensor.

    Represents the Nth upcoming event.
    May have a name like 'sensor.mycalander_event_0' for the first
    upcoming event.
    """

    def __init__(self, hass, rental_control_events, sensor_name, event_number):
        """
        Initialize the sensor.

        sensor_name is typically the name of the calendar.
        eventnumber indicates which upcoming event this is, starting at zero
        """
        self._event_number = event_number
        self._hass = hass
        self.rental_control_events = rental_control_events
        self._entity_id = generate_entity_id(
            "sensor.{}",
            f"{sensor_name} event {self._event_number}",
            hass=self._hass,
        )
        self._event_attributes = {
            "summary": "No reservation",
            "description": None,
            "location": None,
            "start": None,
            "end": None,
            "eta": None,
        }
        self._state = None
        self._is_available = None

    @property
    def entity_id(self):
        """Return the entity_id of the sensor."""
        return self._entity_id

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._event_attributes["summary"]

    @property
    def icon(self):
        """Return the icon for the frontend."""
        return ICON

    @property
    def state(self):
        """Return the date of the next event."""
        return self._state

    @property
    def extra_state_attributes(self):
        """Return the attributes of the event."""
        return self._event_attributes

    @property
    def available(self):
        """Return True if ZoneMinder is available."""
        return self._event_attributes["start"] is not None

    async def async_update(self):
        """Update the sensor."""
        _LOGGER.debug("Running ICalSensor async update for %s", self.name)

        await self.rental_control_events.update()

        event_list = self.rental_control_events.calendar
        if event_list and (self._event_number < len(event_list)):
            val = event_list[self._event_number]
            name = val.get("summary", "Unknown")
            start = val.get("start")

            _LOGGER.debug(
                "Adding event %s - Start %s - End %s - as event %s to calendar %s",
                val.get("summary", "unknown"),
                val.get("start"),
                val.get("end"),
                str(self._event_number),
                self.name,
            )

            self._event_attributes["summary"] = val.get("summary", "unknown")
            self._event_attributes["start"] = val.get("start")
            self._event_attributes["end"] = val.get("end")
            self._event_attributes["location"] = val.get("location", "")
            self._event_attributes["description"] = val.get("description", "")
            self._event_attributes["eta"] = (
                start - datetime.now(start.tzinfo) + timedelta(days=1)
            ).days
            self._event_attributes["all_day"] = val.get("all_day")
            self._state = f"{name} - {start.strftime('%-d %B %Y')}"
            if not val.get("all_day"):
                self._state += f" {start.strftime('%H:%M')}"
        else:
            # No reservations
            _LOGGER.debug(
                "No events available for sensor %s, removing from calendar %s",
                str(self._event_number),
                self.name,
            )
            self._event_attributes = {
                "summary": "No reservation",
                "description": None,
                "location": None,
                "start": None,
                "end": None,
                "eta": None,
            }
            self._state = "No reservation"
