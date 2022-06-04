"""Creating sensors for upcoming events."""
from __future__ import annotations

import logging
import random
import re
from datetime import datetime
from datetime import timedelta

from homeassistant.const import CONF_NAME
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity import EntityCategory

from .const import CONF_MAX_EVENTS
from .const import DOMAIN
from .const import ICON
from .const import NAME
from .util import gen_uuid
from .util import get_slot_name

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
            RentalControlCalSensor(
                hass,
                rental_control_events,
                f"{NAME} {name}",
                eventnumber,
            )
        )

    async_add_entities(sensors)


class RentalControlCalSensor(Entity):
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
        self.rental_control_events = rental_control_events
        if rental_control_events.event_prefix:
            summary = f"{rental_control_events.event_prefix} No reservation"
        else:
            summary = "No reservation"
        self._code_generator = rental_control_events.code_generator
        self._entity_category = EntityCategory.DIAGNOSTIC
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
        self._parsed_attributes = {}
        self._event_number = event_number
        self._hass = hass
        self._is_available = None
        self._name = f"{sensor_name} Event {self._event_number}"
        self._state = summary
        self._unique_id = gen_uuid(
            f"{self.rental_control_events.unique_id} sensor {self._event_number}"
        )

    def _extract_email(self) -> str | None:
        """Extract guest email from a description"""
        if self._event_attributes["description"] is None:
            return None
        p = re.compile(r"""Email:\s+(\S+@\S+)""")
        ret = p.findall(self._event_attributes["description"])
        if ret:
            return ret[0]
        else:
            return None

    def _extract_last_four(self) -> str | None:
        """Extract the last 4 digits from a description."""
        if self._event_attributes["description"] is None:
            return None
        p = re.compile(r"""\(Last 4 Digits\):\s+(\d{4})""")
        ret = p.findall(self._event_attributes["description"])
        if ret:
            return ret[0]
        else:
            return None

    def _extract_num_guests(self) -> str | None:
        """Extract the number of guests from a description."""
        if self._event_attributes["description"] is None:
            return None
        p = re.compile(r"""Guests:\s+(\d+)$""", re.M)
        ret = p.findall(self._event_attributes["description"])
        if ret:
            return ret[0]
        else:
            return None

    def _extract_phone_number(self) -> str | None:
        """Extract guest phone number from a description"""
        if self._event_attributes["description"] is None:
            return None
        p = re.compile(r"""Phone Number:\s+(\+?[\d\. \-\(\)]{9,})""")
        ret = p.findall(self._event_attributes["description"])
        if ret:
            return ret[0].strip()
        else:
            return None

    def _extract_url(self) -> str | None:
        """Extract reservation URL."""
        if self._event_attributes["description"] is None:
            return None
        p = re.compile(r"""(https?://.*$)""", re.M)
        ret = p.findall(self._event_attributes["description"])
        if ret:
            return ret[0]
        else:
            return None

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
            ret = self._extract_last_four()
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

    @property
    def available(self):
        """Return True if ZoneMinder is available."""
        return self._event_attributes["start"] is not None

    @property
    def device_info(self):
        """Return the device info block."""
        return self.rental_control_events.device_info

    @property
    def entity_category(self):
        """Return the entity category."""
        return self._entity_category

    @property
    def extra_state_attributes(self) -> dict:
        """Return the attributes of the event."""
        attrib = {**self._event_attributes, **self._parsed_attributes}
        return attrib

    @property
    def icon(self):
        """Return the icon for the frontend."""
        return ICON

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def state(self):
        """Return the date of the next event."""
        return self._state

    @property
    def unique_id(self):
        """Return the unique_id."""
        return self._unique_id

    async def async_update(self):
        """Update the sensor."""
        _LOGGER.debug("Running RentalControlCalSensor async update for %s", self.name)

        await self.rental_control_events.update()

        self._code_generator = self.rental_control_events.code_generator
        event_list = self.rental_control_events.calendar
        if event_list and (self._event_number < len(event_list)):
            event = event_list[self._event_number]
            name = event.summary
            start = event.start

            _LOGGER.debug(
                "Adding event %s - Start %s - End %s - as event %s to calendar %s",
                event.summary,
                event.start,
                event.end,
                str(self._event_number),
                self.name,
            )

            self._event_attributes["summary"] = event.summary
            self._event_attributes["start"] = event.start
            self._event_attributes["end"] = event.end
            self._event_attributes["location"] = event.location
            self._event_attributes["description"] = event.description
            self._event_attributes["eta"] = (
                start - datetime.now(start.tzinfo) + timedelta(days=1)
            ).days
            self._state = f"{name} - {start.strftime('%-d %B %Y')}"
            self._state += f" {start.strftime('%H:%M')}"
            self._event_attributes["slot_name"] = get_slot_name(
                self._event_attributes["summary"],
                self._event_attributes["description"],
                self.rental_control_events.event_prefix,
            )
            self._event_attributes["slot_code"] = self._generate_door_code()

            # attributes parsed from description
            parsed_attributes = {}

            last_four = self._extract_last_four()
            if last_four is not None:
                parsed_attributes["last_four"] = last_four

            num_guests = self._extract_num_guests()
            if num_guests is not None:
                parsed_attributes["number_of_guests"] = num_guests

            guest_email = self._extract_email()
            if guest_email is not None:
                parsed_attributes["guest_email"] = guest_email

            phone_number = self._extract_phone_number()
            if phone_number is not None:
                parsed_attributes["phone_number"] = phone_number

            reservation_url = self._extract_url()
            if reservation_url is not None:
                parsed_attributes["reservation_url"] = reservation_url

            self._parsed_attributes = parsed_attributes
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
            self._parsed_attributes = {}
            self._state = summary
