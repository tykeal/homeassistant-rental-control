"""Mapping sensor for slot to event."""
from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity import EntityCategory

from ..const import MAP_ICON
from ..util import async_check_overrides
from ..util import fire_set_code
from ..util import gen_uuid
from ..util import get_event_names

_LOGGER = logging.getLogger(__name__)


class RentalControlMappingSensor(Entity):
    """
    A sensor that defines the mapping of door code slots to events
    """

    def __init__(self, hass: HomeAssistant, rental_control, sensor_name):
        """
        Initialize the sensor.

        sensor_name is typically the name of the calendar
        """
        self.rental_control = rental_control

        self._entity_category = EntityCategory.DIAGNOSTIC
        self._is_available = False
        self._mapping_attributes = {
            "prefix": self.rental_control.event_prefix,
            "mapping": {},
        }
        for i in range(
            self.rental_control.start_slot,
            self.rental_control.start_slot + self.rental_control.max_events,
        ):
            self._mapping_attributes["mapping"][i] = None

        self._name = f"{sensor_name} Mapping"
        self._state = "Ready"
        self._startup_count = 0
        self._unique_id = gen_uuid(f"{self.rental_control.unique_id} mapping sensor")

    @property
    def available(self) -> bool:
        """Return True if sensor is ready."""
        return self._is_available

    @property
    def device_info(self) -> dict:
        """Return the device info block."""
        return self.rental_control.device_info

    @property
    def entity_category(self) -> EntityCategory:
        """Return the entity category."""
        return self._entity_category

    @property
    def extra_state_attributes(self) -> dict:
        """Return the mapping attributes."""
        attrib = {
            **self._mapping_attributes,
        }

        return attrib

    @property
    def icon(self) -> str:
        """Return the icon for the frontend."""
        return MAP_ICON

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return self._name

    @property
    def state(self) -> str:
        """Return the mapping state."""
        return self._state

    @property
    def unique_id(self) -> str:
        """Return the unique id."""
        return self._unique_id

    async def async_update(self):
        """Update the sensor."""
        _LOGGER.debug(
            "Running RentalControlMappingSensor async_udpate for %s", self.name
        )

        # Do nothing if the rc calendar is not ready
        if not self.rental_control.calendar_ready:
            _LOGGER.debug("calendar not ready, skipping mapping update")
            return

        # Do not execute until everything has had a chance to fully stabilize
        # This can take a couple of minutes
        if self._startup_count < 2:
            _LOGGER.debug("Rental Control still starting, skipping mapping update")
            self._startup_count += 1
            return

        # Make sure overrides are accurate
        await async_check_overrides(self.rental_control)

        overrides = self.rental_control.event_overrides.copy()

        for override in overrides:
            if override not in self._mapping_attributes["mapping"].values():
                _LOGGER.debug("%s is not in current mapping", override)
                if "Slot " not in override:
                    self._mapping_attributes["mapping"][
                        overrides[override]["slot"]
                    ] = override
            else:
                _LOGGER.debug("%s is in current mapping", override)

        slots = filter(lambda k: ("Slot " in k), overrides)
        for event in get_event_names(self.rental_control):
            if event not in overrides:
                try:
                    slot = next(slots)
                    _LOGGER.debug(
                        "%s is not in overrides, setting to slot %s", event, slot
                    )
                    fire_set_code(
                        self.rental_control.hass,
                        self.rental_control.name,
                        overrides[slot]["slot"],
                        event,
                    )
                except StopIteration:
                    pass
        self._is_available = self.rental_control.calendar_ready
