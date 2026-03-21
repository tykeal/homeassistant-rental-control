# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Switch platform for Rental Control.

Provides toggle entities for keymaster monitoring and early checkout
expiry. Switch entities are only created when keymaster is configured.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Rental Control switch entities.

    Switch entities (keymaster monitoring, early checkout expiry) will
    be created conditionally when keymaster is configured. Currently
    a placeholder for Phase 5 implementation.

    Args:
        hass: Home Assistant instance.
        config_entry: The integration config entry.
        async_add_entities: Callback to add entities.
    """
    # Phase 5 will add KeymasterMonitoringSwitch and
    # EarlyCheckoutExpirySwitch here
