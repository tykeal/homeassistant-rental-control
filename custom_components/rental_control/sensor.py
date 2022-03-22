"""Creating sensors for upcoming events."""
import logging
import random
import re
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

    rental_control_events = hass.data[DOMAIN][config_entry.unique_id]
    await rental_control_events.update()
    if rental_control_events.calendar is None:
        _LOGGER.error("Unable to fetch iCal")
        return False

    sensors = []
    for eventnumber in range(max_events):
        sensors.append(
            ICalSensor(
                hass,
                rental_control_events,
                DOMAIN + " " + name,
                eventnumber,
            )
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
        if rental_control_events.event_prefix:
            summary = f"{rental_control_events.event_prefix} No reservation"
        else:
            summary = "No reservation"
        self._event_attributes = {
            "summary": summary,
            "description": None,
            "location": None,
            "start": None,
            "end": None,
            "eta": None,
            "slot_name": None,
            "slot_code": None,
        }
        self._state = summary
        self._is_available = None
        self._code_generator = rental_control_events.code_generator

    def _generate_door_code(self) -> str:
        """Generate a door code based upon the selected type."""

        generator = self._code_generator

        # If there is no event description force date_based generation
        # This is because VRBO does not appear to provide any descriptions in
        # their calendar entries!
        # This also gets around Unavailable and Blocked entries that do not
        # have a description either
        if self._event_attributes["description"] is None:
            generator = "date_based"

        # AirBnB provides the last 4 digits of the guest's registered phone
        #
        # VRBO does not appear to provide any phone numbers
        #
        # Guesty provides last 4 + either a full number or all but last digit
        # for VRBO listings and doesn't appear to provide anything for AirBnB
        # listings, or if it does provide them, my example Guesty calendar doesn't
        # have any new enough to have the data
        #
        # TripAdvisor does not appear to provide any phone number data

        ret = None

        if generator == "last_four":
            p = re.compile("\\(Last 4 Digits\\):\\s+(\\d{4})")
            last_four = p.findall(self._event_attributes["description"])[0]
            ret = last_four
        elif generator == "static_random":
            # If the description changes this will most likely change the code
            random.seed(self._event_attributes["description"])
            ret = str(random.randrange(1, 9999, 4)).zfill(4)

        if ret is None:
            # Generate code based on checkin/out days
            #
            # This generator will have a side effect of changing the code
            # if the start or end dates shift!
            #
            # This is the default and fall back generator if no other
            # generator produced a code
            start_day = self._event_attributes["start"].strftime("%d")
            end_day = self._event_attributes["end"].strftime("%d")
            return f"{start_day}{end_day}"
        else:
            return ret

    def _get_slot_name(self) -> str:
        """Determine the name for a door slot."""

        # strip off any prefix if it's being used
        if self.rental_control_events.event_prefix:
            p = re.compile(f"{self.rental_control_events.event_prefix} (.*)")
            summary = p.findall(self._event_attributes["summary"])[0]
        else:
            summary = self._event_attributes["summary"]

        # Blocked and Unavailable should not have a slot
        p = re.compile("Not available|Blocked")
        if p.search(summary):
            return None

        # AirBnB & VRBO
        if re.search("Reserved", summary):
            # AirBnB
            if summary == "Reserved":
                p = re.compile("([A-Z][A-Z0-9]{9})")
                return p.search(self._event_attributes["description"])[0]
            else:
                p = re.compile(" - (.*)$")
                return p.findall(summary)[0]

        # Tripadvisor
        if re.search("Tripadvisor", summary):
            p = re.compile("Tripadvisor.*: (.*)")
            return p.findall(summary)[0]

        # Guesty
        p = re.compile("-(.*)-.*-")
        return p.findall(summary)[0]

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

        self._code_generator = self.rental_control_events.code_generator
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
            self._event_attributes["slot_name"] = self._get_slot_name()
            self._event_attributes["slot_code"] = self._generate_door_code()
        else:
            # No reservations
            _LOGGER.debug(
                "No events available for sensor %s, removing from calendar %s",
                str(self._event_number),
                self.name,
            )
            if self.rental_control_events.event_prefix:
                summary = f"{self.rental_control_events.event_prefix} No reservation"
            else:
                summary = "No reservation"
            self._event_attributes = {
                "summary": summary,
                "description": None,
                "location": None,
                "start": None,
                "end": None,
                "eta": None,
                "slot_name": None,
                "slot_code": None,
            }
            self._state = summary
