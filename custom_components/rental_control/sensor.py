"""Creating sensors for upcoming events."""

from __future__ import annotations

import logging

from homeassistant.const import CONF_NAME

from .const import CONF_MAX_EVENTS
from .const import COORDINATOR
from .const import DOMAIN
from .const import NAME
from .sensors.calsensor import RentalControlCalSensor

_LOGGER = logging.getLogger(__name__)


async def async_setup_platform(hass, config, add_entities, discovery_info=None):  # pylint: disable=unused-argument
    """Set up this integration with config flow."""
    return True


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the iCal Sensor."""
    config = config_entry.data
    name = config.get(CONF_NAME)
    max_events = config.get(CONF_MAX_EVENTS)

    coordinator = hass.data[DOMAIN][config_entry.entry_id][COORDINATOR]
    await coordinator.update()
    if coordinator.calendar is None:
        _LOGGER.error("Unable to fetch iCal")
        return False

    sensors = []
    for eventnumber in range(max_events):
        sensors.append(
            RentalControlCalSensor(
                hass,
                coordinator,
                f"{NAME} {name}",
                eventnumber,
            )
        )

    async_add_entities(sensors)
