"""Creating sensors for upcoming events."""
from __future__ import annotations

import logging

from homeassistant.const import CONF_NAME

from .const import CONF_MAX_EVENTS
from .const import DOMAIN
from .const import NAME
from .sensors.calsensor import RentalControlCalSensor

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
