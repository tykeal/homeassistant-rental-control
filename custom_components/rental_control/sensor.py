# SPDX-FileCopyrightText: 2021 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Creating sensors for upcoming events."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.typing import DiscoveryInfoType
import voluptuous as vol

from .const import CHECKIN_SENSOR
from .const import CONF_MAX_EVENTS
from .const import COORDINATOR
from .const import DOMAIN
from .const import NAME
from .sensors.calsensor import RentalControlCalSensor
from .sensors.checkinsensor import CheckinTrackingSensor

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

    # Add check-in tracking sensor (FR-028: created for every instance)
    checkin_sensor = CheckinTrackingSensor(
        hass,
        coordinator,
        config_entry,
    )
    sensors.append(checkin_sensor)

    # Store sensor reference for keymaster event bus listener (T026)
    hass.data[DOMAIN][config_entry.entry_id][CHECKIN_SENSOR] = checkin_sensor

    async_add_entities(sensors)

    # Register checkout entity service (see specs/004-checkin-tracking/contracts/checkout-service.md)
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        "checkout",
        {vol.Optional("force", default=False): cv.boolean},
        "async_checkout",
    )

    # Register debug set_state entity service
    platform.async_register_entity_service(
        "set_state",
        {vol.Required("state"): cv.string},
        "async_set_state",
    )

    return True
