# SPDX-FileCopyrightText: 2021 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Creating sensors for upcoming events."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.typing import DiscoveryInfoType

from .const import CONF_MAX_EVENTS
from .const import COORDINATOR
from .const import DOMAIN
from .const import NAME
from .sensors.calsensor import RentalControlCalSensor

_LOGGER = logging.getLogger(__name__)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> bool:
    """Set up this integration with config flow."""
    return True


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> bool:
    """Set up the iCal Sensor."""
    config = config_entry.data
    name = config.get(CONF_NAME)
    max_events = config.get(CONF_MAX_EVENTS)

    coordinator = hass.data[DOMAIN][config_entry.entry_id][COORDINATOR]
    # Data is guaranteed available via async_config_entry_first_refresh()
    if coordinator.data is None:
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

    return True
