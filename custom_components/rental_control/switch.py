# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Switch platform for Rental Control.

Provides toggle entities for keymaster monitoring and early checkout
expiry. Switch entities are only created when keymaster is configured.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import COORDINATOR
from .const import DOMAIN
from .const import EARLY_CHECKOUT_EXPIRY_SWITCH
from .const import KEYMASTER_MONITORING_SWITCH
from .util import gen_uuid

if TYPE_CHECKING:
    from .coordinator import RentalControlCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Rental Control switch entities.

    Switch entities (keymaster monitoring, early checkout expiry) are
    created conditionally when keymaster is configured (FR-026).

    Args:
        hass: Home Assistant instance.
        config_entry: The integration config entry.
        async_add_entities: Callback to add entities.
    """
    coordinator: RentalControlCoordinator = hass.data[DOMAIN][config_entry.entry_id][
        COORDINATOR
    ]

    entities: list[SwitchEntity] = []

    if coordinator.lockname:
        entities.append(KeymasterMonitoringSwitch(coordinator, config_entry))
        entities.append(EarlyCheckoutExpirySwitch(coordinator, config_entry))

    async_add_entities(entities)


class KeymasterMonitoringSwitch(SwitchEntity, RestoreEntity):
    """Switch to enable/disable keymaster unlock monitoring.

    When ``on``, keymaster unlock events trigger check-in detection.
    When ``off`` (default), only time-based auto check-in is used.

    Inherits from ``RestoreEntity`` to persist on/off state across
    Home Assistant restarts.
    """

    def __init__(
        self,
        coordinator: RentalControlCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the keymaster monitoring switch.

        Args:
            coordinator: The rental control data coordinator.
            config_entry: The integration config entry.
        """
        self._coordinator = coordinator
        self._config_entry = config_entry
        self._attr_has_entity_name = True
        self._attr_translation_key = "keymaster_monitoring"
        self._attr_is_on: bool = False
        self._unique_id = gen_uuid(f"{coordinator.unique_id} keymaster_monitoring")

    @property
    def unique_id(self) -> str:
        """Return the unique ID for this switch."""
        return self._unique_id

    @property
    def is_on(self) -> bool:
        """Return whether keymaster monitoring is enabled."""
        return self._attr_is_on

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info linking to the existing integration device."""
        return self._coordinator.device_info

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable keymaster monitoring.

        Args:
            **kwargs: Additional keyword arguments (unused).
        """
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable keymaster monitoring.

        Args:
            **kwargs: Additional keyword arguments (unused).
        """
        self._attr_is_on = False
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Restore last known on/off state when added to hass."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None:
            self._attr_is_on = last_state.state == "on"

        # Store entity reference in hass.data for the event bus listener
        entry_data = self.hass.data.get(DOMAIN, {}).get(self._config_entry.entry_id, {})
        entry_data[KEYMASTER_MONITORING_SWITCH] = self

        _LOGGER.debug(
            "Keymaster monitoring switch restored: is_on=%s, entity_id=%s",
            self._attr_is_on,
            self.entity_id,
        )


class EarlyCheckoutExpirySwitch(SwitchEntity, RestoreEntity):
    """Switch to enable/disable early checkout lock code expiry.

    When ``on``, a keymaster unlock while the sensor is in
    ``checked_in`` state shortens the lock code expiry to
    ``now + grace_minutes`` instead of the original reservation end.
    When ``off`` (default), no expiry modification occurs.

    Inherits from ``RestoreEntity`` to persist on/off state across
    Home Assistant restarts.
    """

    def __init__(
        self,
        coordinator: RentalControlCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the early checkout expiry switch.

        Args:
            coordinator: The rental control data coordinator.
            config_entry: The integration config entry.
        """
        self._coordinator = coordinator
        self._config_entry = config_entry
        self._attr_has_entity_name = True
        self._attr_translation_key = "early_checkout_expiry"
        self._attr_is_on: bool = False
        self._unique_id = gen_uuid(f"{coordinator.unique_id} early_checkout_expiry")

    @property
    def unique_id(self) -> str:
        """Return the unique ID for this switch."""
        return self._unique_id

    @property
    def is_on(self) -> bool:
        """Return whether early checkout expiry is enabled."""
        return self._attr_is_on

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info linking to the existing integration device."""
        return self._coordinator.device_info

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable early checkout expiry.

        Args:
            **kwargs: Additional keyword arguments (unused).
        """
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable early checkout expiry.

        Args:
            **kwargs: Additional keyword arguments (unused).
        """
        self._attr_is_on = False
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Restore last known on/off state when added to hass."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None:
            self._attr_is_on = last_state.state == "on"

        # Store entity reference in hass.data for the sensor to look up
        self.hass.data.setdefault(DOMAIN, {}).setdefault(
            self._config_entry.entry_id, {}
        )[EARLY_CHECKOUT_EXPIRY_SWITCH] = self

        _LOGGER.debug(
            "Early checkout expiry switch restored: is_on=%s, entity_id=%s",
            self._attr_is_on,
            self.entity_id,
        )
