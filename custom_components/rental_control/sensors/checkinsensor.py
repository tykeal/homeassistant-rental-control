# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Check-in/check-out tracking sensor for Rental Control.

Phase 2 skeleton — provides entity wiring, unique_id, device_info,
extra_state_attributes, and _event_key.  State-machine transitions
will be added in later phases.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ..const import CHECKIN_STATE_NO_RESERVATION
from ..util import gen_uuid

if TYPE_CHECKING:
    from ..coordinator import RentalControlCoordinator


class CheckinTrackingSensor(
    CoordinatorEntity["RentalControlCoordinator"],
    RestoreEntity,
):
    """Sensor that tracks guest check-in/check-out state.

    Phase 2 skeleton — exposes entity metadata and attributes.
    State-machine transitions (no_reservation → awaiting_checkin →
    checked_in → checked_out) will be added in later phases.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: RentalControlCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the check-in tracking sensor.

        Args:
            hass: Home Assistant instance.
            coordinator: The rental control data coordinator.
            config_entry: The integration config entry.
        """
        super().__init__(coordinator)
        self._hass = hass
        self._config_entry = config_entry
        self._state: str = CHECKIN_STATE_NO_RESERVATION

        # Tracked event fields (from data-model.md)
        self._tracked_event_summary: str | None = None
        self._tracked_event_start: datetime | None = None
        self._tracked_event_end: datetime | None = None
        self._tracked_event_slot_name: str | None = None
        self._checkin_source: str | None = None
        self._checkout_source: str | None = None
        self._checkout_time: datetime | None = None
        self._transition_target_time: datetime | None = None
        self._checked_out_event_key: str | None = None

        # Internal timer unsubscribe handle
        self._unsub_timer: CALLBACK_TYPE | None = None

        # Unique ID
        self._unique_id = gen_uuid(f"{self.coordinator.unique_id} checkin_tracking")

    @property
    def unique_id(self) -> str:
        """Return the unique ID for this sensor."""
        return self._unique_id

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return f"{self.coordinator.name} Check-in"

    @property
    def state(self) -> str:
        """Return the current check-in tracking state."""
        return self._state

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info linking to the existing integration device."""
        return self.coordinator.device_info

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes for the sensor.

        Returns all tracked event attributes per data-model.md.
        """
        return {
            "state": self._state,
            "summary": self._tracked_event_summary,
            "start": (
                self._tracked_event_start.isoformat()
                if self._tracked_event_start
                else None
            ),
            "end": (
                self._tracked_event_end.isoformat() if self._tracked_event_end else None
            ),
            "guest_name": self._tracked_event_slot_name,
            "checkin_source": self._checkin_source,
            "checkout_source": self._checkout_source,
            "checkout_time": (
                self._checkout_time.isoformat() if self._checkout_time else None
            ),
            "next_transition": (
                self._transition_target_time.isoformat()
                if self._transition_target_time
                else None
            ),
        }

    @staticmethod
    def _event_key(summary: str, start: datetime) -> str:
        """Generate a unique identity key for an event.

        Only summary and start are used for identity. The end time is
        deliberately excluded so that post-checkout event extensions
        (same summary + start but later end) are detected as the SAME
        event rather than a new one, satisfying FR-007.

        Args:
            summary: The event summary text.
            start: The event start datetime.

        Returns:
            A composite identity key string.
        """
        return f"{summary}|{start.isoformat()}"

    async def async_will_remove_from_hass(self) -> None:
        """Clean up when entity is being removed."""
        if self._unsub_timer is not None:
            self._unsub_timer()
            self._unsub_timer = None
        await super().async_will_remove_from_hass()
