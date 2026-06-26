# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Check-in/check-out tracking sensor for Rental Control."""

from __future__ import annotations

from datetime import datetime
import logging
from typing import TYPE_CHECKING
from typing import Any

from homeassistant.components.calendar import CalendarEvent
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE
from homeassistant.core import HomeAssistant
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .. import const as rc_const
from ..util import add_call as add_call  # re-exported for tests/runtime patching
from ..util import gen_uuid
from ..util import get_entry_data
from .checkin import runtime
from .checkin import timer_runtime
from .checkin.applicator import apply_restore_decision
from .checkin.applicator import apply_transition_decision
from .checkin.event_selection import event_key
from .checkin.event_selection import extract_slot_name
from .checkin.event_selection import find_followon_event
from .checkin.event_selection import find_tracked_event
from .checkin.event_selection import get_relevant_event
from .checkin.models import CheckinStateSnapshot
from .checkin.models import CoordinatorUpdateContext
from .checkin.persistence import CheckinExtraStoredData
from .checkin.restore_decisions import decide_restore_state
from .checkin.timers import CheckinTimerManager
from .checkin.transition_decisions import decide_coordinator_update

if TYPE_CHECKING:
    from ..coordinator import RentalControlCoordinator

_LOGGER = logging.getLogger(__name__)
_CHECKIN_STATE_ICONS = {
    rc_const.CHECKIN_STATE_AWAITING: "mdi:door-open",
    rc_const.CHECKIN_STATE_CHECKED_IN: "mdi:account-check",
    rc_const.CHECKIN_STATE_CHECKED_OUT: "mdi:airplane",
    rc_const.CHECKIN_STATE_NO_RESERVATION: "mdi:bed-empty",
}


class CheckinTrackingSensor(
    CoordinatorEntity["RentalControlCoordinator"], SensorEntity, RestoreEntity
):
    """Sensor that tracks guest check-in/check-out state."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: RentalControlCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the check-in tracking sensor."""
        super().__init__(coordinator)
        self._hass = hass
        self._config_entry = config_entry
        self._attr_has_entity_name = True
        self._attr_translation_key = "checkin"
        self._attr_device_class = SensorDeviceClass.ENUM
        self._attr_options = [
            rc_const.CHECKIN_STATE_NO_RESERVATION,
            rc_const.CHECKIN_STATE_AWAITING,
            rc_const.CHECKIN_STATE_CHECKED_IN,
            rc_const.CHECKIN_STATE_CHECKED_OUT,
        ]
        self._state = rc_const.CHECKIN_STATE_NO_RESERVATION
        self._tracked_event_summary: str | None = None
        self._tracked_event_start: datetime | None = None
        self._tracked_event_end: datetime | None = None
        self._tracked_event_slot_name: str | None = None
        self._checkin_source: str | None = None
        self._checkout_source: str | None = None
        self._checkout_time: datetime | None = None
        self._transition_target_time: datetime | None = None
        self._checked_out_event_key: str | None = None
        self._next_event_start_day: datetime | None = None
        self._checkin_lock_name: str | None = None
        self._linger_followon_key: str | None = None
        self._linger_baseline: datetime | None = None
        self._event_missing_warned = False
        self._timer_manager = CheckinTimerManager(hass, async_track_point_in_time)
        self._unique_id = gen_uuid(f"{self.coordinator.unique_id} checkin_tracking")

    @property
    def _unsub_timer(self) -> CALLBACK_TYPE | None:
        """Return the compatibility timer unsubscribe handle."""
        return self._timer_manager.handle

    @_unsub_timer.setter
    def _unsub_timer(self, value: CALLBACK_TYPE | None) -> None:
        """Set the compatibility timer unsubscribe handle."""
        self._timer_manager.handle = value

    @property
    def unique_id(self) -> str:
        """Return the unique ID for this sensor."""
        return self._unique_id

    @property
    def state(self) -> str:
        """Return the current check-in tracking state."""
        return self._state

    @property
    def icon(self) -> str:
        """Return an icon reflecting the current check-in state."""
        return _CHECKIN_STATE_ICONS.get(self._state, "mdi:bed-empty")

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info linking to the existing integration device."""
        return self.coordinator.device_info

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes for the sensor."""
        attrs: dict[str, Any] = {
            "checkin_state": self._state,
            "summary": self._tracked_event_summary,
            "start": self._tracked_event_start,
            "end": self._tracked_event_end,
            "guest_name": self._tracked_event_slot_name,
            "checkin_source": self._checkin_source,
            "checkout_source": self._checkout_source,
            "checkout_time": self._checkout_time,
            "next_transition": self._transition_target_time,
            "lock_name": self._checkin_lock_name,
        }
        if self._config_entry.data.get(
            rc_const.CONF_ENABLE_KEYMASTER_EVENT_DIAGNOSTICS,
            rc_const.DEFAULT_ENABLE_KEYMASTER_EVENT_DIAGNOSTICS,
        ):
            attrs["keymaster_event_diagnostics"] = list(
                self.coordinator.keymaster_event_diagnostics
            )
        return attrs

    @property
    def extra_restore_state_data(self) -> CheckinExtraStoredData:
        """Return entity-specific state data for RestoreEntity persistence."""
        return CheckinExtraStoredData.from_snapshot(self._snapshot())

    @staticmethod
    def _event_key(summary: str, start: datetime) -> str:
        """Generate the behavior-compatible event identity key."""
        return event_key(summary, start)

    def _snapshot(self) -> CheckinStateSnapshot:
        """Return a typed snapshot of current state."""
        return CheckinStateSnapshot(
            self._state,
            self._tracked_event_summary,
            self._tracked_event_start,
            self._tracked_event_end,
            self._tracked_event_slot_name,
            self._checkin_source,
            self._checkout_source,
            self._checkout_time,
            self._transition_target_time,
            self._checked_out_event_key,
            self._next_event_start_day,
            self._checkin_lock_name,
            self._linger_followon_key,
            self._linger_baseline,
            self._event_missing_warned,
        )

    def _apply_snapshot(self, snapshot: CheckinStateSnapshot) -> None:
        """Replace mutable fields from a snapshot."""
        for field in snapshot.__dataclass_fields__:
            if field == "state":
                self._state = snapshot.state
            else:
                setattr(self, f"_{field}", getattr(snapshot, field))

    def _decision_context(self) -> CoordinatorUpdateContext:
        """Build a pure decision context from entity state."""
        return CoordinatorUpdateContext(
            self._snapshot(),
            self.coordinator.data or [],
            dt_util.now,
            self.coordinator.last_update_success,
            self._is_keymaster_monitoring_enabled(),
            self._get_cleaning_window(),
            self.coordinator.event_prefix or "",
            self._unsub_timer is not None,
            self.coordinator.name,
        )

    def _get_cleaning_window(self) -> float:
        """Return the configured cleaning window in hours."""
        return float(
            self._config_entry.data.get(
                rc_const.CONF_CLEANING_WINDOW, rc_const.DEFAULT_CLEANING_WINDOW
            )
        )

    def _cancel_timer(self) -> None:
        """Cancel any pending timer callback."""
        self._timer_manager.cancel()

    def _get_relevant_event(self) -> CalendarEvent | None:
        """Get the most relevant event from coordinator data."""
        return get_relevant_event(self.coordinator.data or [], dt_util.now())

    def _find_followon_event(self, checkout_time: datetime) -> CalendarEvent | None:
        """Find the first follow-on event after checkout."""
        return find_followon_event(
            self.coordinator.data or [], checkout_time, self._checked_out_event_key
        )

    def _find_tracked_event(self) -> CalendarEvent | None:
        """Find the currently tracked event in coordinator data."""
        return find_tracked_event(
            self.coordinator.data or [],
            self._tracked_event_summary,
            self._tracked_event_start,
        )

    def _extract_slot_name(self, event: CalendarEvent) -> str | None:
        """Extract guest/slot name from a calendar event."""
        return extract_slot_name(event, self.coordinator.event_prefix or "")

    def _is_keymaster_monitoring_enabled(self) -> bool:
        """Return True when keymaster monitoring is switched on."""
        entry_data = get_entry_data(self._hass, self._config_entry.entry_id)
        if entry_data is None:
            return self.coordinator.lockname is not None
        monitoring_switch = entry_data.get(rc_const.KEYMASTER_MONITORING_SWITCH)
        if monitoring_switch is not None:
            return bool(monitoring_switch.is_on)
        return self.coordinator.lockname is not None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        _LOGGER.debug(
            "Running CheckinTrackingSensor coordinator update for %s",
            self.coordinator.name,
        )
        apply_transition_decision(
            self, decide_coordinator_update(self._decision_context())
        )

    def _transition_to_awaiting(self, event: CalendarEvent) -> None:
        """Transition to awaiting_checkin state."""
        runtime.transition_to_awaiting(self, event)

    def _update_tracked_event(self, event: CalendarEvent) -> None:
        """Update mutable tracked fields from an event."""
        self._tracked_event_end = event.end
        self._tracked_event_slot_name = self._extract_slot_name(event)

    def _update_tracked_slot_name(self, event: CalendarEvent) -> None:
        """Update tracked slot name from an event."""
        self._tracked_event_slot_name = self._extract_slot_name(event)

    def _transition_to_checked_in(self, source: str, lock_name: str = "") -> None:
        """Transition to checked_in state and fire the check-in event."""
        runtime.transition_to_checked_in(self, source, lock_name)

    def _checkin_payload(self, source: str) -> dict[str, Any]:
        """Return a check-in event payload."""
        return runtime.checkin_payload(self, source)

    def _schedule_auto_checkin(self, start_time: datetime) -> None:
        """Schedule automatic check-in for a start time."""
        timer_runtime.schedule_auto_checkin(self, start_time)

    def _schedule_auto_checkout(self, end_time: datetime) -> None:
        """Schedule automatic checkout at the given end time."""
        timer_runtime.schedule_auto_checkout(self, end_time)

    def _transition_to_checked_out(
        self, source: str, linger_baseline: datetime | None = None
    ) -> None:
        """Transition to checked_out state."""
        runtime.transition_to_checked_out(self, source, linger_baseline)

    def _compute_linger_timing(self, baseline: datetime | None = None) -> None:
        """Compute and schedule post-checkout linger timing."""
        timer_runtime.compute_linger_timing(self, baseline)

    def _schedule_linger_to_awaiting(self, target: datetime) -> None:
        """Schedule same-day linger transition."""
        timer_runtime.schedule_linger_to_awaiting(self, target)

    def _schedule_linger_to_no_reservation(
        self, target: datetime, followup: datetime | None
    ) -> None:
        """Schedule linger expiry transition."""
        timer_runtime.schedule_linger_to_no_reservation(self, target, followup)

    def _schedule_no_reservation_to_awaiting(self, target: datetime) -> None:
        """Schedule FR-006c follow-up awaiting transition."""
        timer_runtime.schedule_no_reservation_to_awaiting(self, target)

    def _transition_to_no_reservation(self) -> None:
        """Transition to no_reservation state and clear tracked data."""
        runtime.transition_to_no_reservation(self)

    @callback
    def _async_auto_checkin_callback(self, _now: datetime) -> None:
        """Timer callback for automatic check-in at event start time."""
        timer_runtime.auto_checkin_callback(self, _now)

    @callback
    def _async_auto_checkout_callback(self, _now: datetime) -> None:
        """Timer callback for automatic check-out at event end time."""
        timer_runtime.auto_checkout_callback(self, _now)

    @callback
    def _async_linger_to_awaiting_callback(self, _now: datetime) -> None:
        """Timer callback for same-day turnover."""
        timer_runtime.linger_to_awaiting_callback(self, _now)

    @callback
    def _async_linger_to_no_reservation_callback(self, _now: datetime) -> None:
        """Timer callback for post-checkout linger expiry."""
        timer_runtime.linger_to_no_reservation_callback(self, _now)

    @callback
    def _async_no_reservation_to_awaiting_callback(self, _now: datetime) -> None:
        """Timer callback for FR-006c follow-up awaiting transition."""
        timer_runtime.no_reservation_to_awaiting_callback(self, _now)

    async def async_checkout(self, force: bool = False) -> None:
        """Handle manual checkout service call."""
        await runtime.async_checkout(self, force)

    async def async_set_state(self, state: str) -> None:
        """Force-set the sensor to an arbitrary valid state."""
        await runtime.async_set_state(self, state)

    async def async_added_to_hass(self) -> None:
        """Restore persisted state and validate against current time/data."""
        await super().async_added_to_hass()
        last_extra = await self.async_get_last_extra_data()
        if last_extra is not None:
            restored = CheckinExtraStoredData.from_dict(last_extra.as_dict())
            self._apply_snapshot(restored.snapshot)
            _LOGGER.debug(
                "Restored state '%s' for %s", self._state, self.coordinator.name
            )
            self._validate_restored_state()
        else:
            _LOGGER.debug(
                "No prior state for %s, starting as %s",
                self.coordinator.name,
                rc_const.CHECKIN_STATE_NO_RESERVATION,
            )
        if self.coordinator.last_update_success and self.coordinator.data is not None:
            self._handle_coordinator_update()

    def _validate_restored_state(self) -> None:
        """Validate restored state against current time and coordinator data."""
        apply_restore_decision(self, decide_restore_state(self._decision_context()))

    def _apply_silent_checked_in(self, source: str) -> None:
        """Apply a restore-silent checked-in transition."""
        runtime.apply_silent_checked_in(self, source)

    def _apply_silent_checked_out(
        self, source: str, checkout_time: datetime | None
    ) -> None:
        """Apply a restore-silent checked-out transition."""
        runtime.apply_silent_checked_out(self, source, checkout_time)

    async def async_will_remove_from_hass(self) -> None:
        """Clean up when entity is being removed."""
        self._cancel_timer()
        await super().async_will_remove_from_hass()

    @callback
    def async_handle_keymaster_unlock(
        self, code_slot_num: int, lock_name: str = ""
    ) -> None:
        """Handle a keymaster unlock event."""
        runtime.handle_keymaster_unlock(self, code_slot_num, lock_name)

    async def _async_update_lock_code_expiry(self, new_end: datetime) -> None:
        """Update keymaster lock code expiry after early checkout."""
        await runtime.async_update_lock_code_expiry(self, new_end)
