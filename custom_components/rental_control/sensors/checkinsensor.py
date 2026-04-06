# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Check-in/check-out tracking sensor for Rental Control.

Implements a four-state state machine that tracks guest occupancy:
``no_reservation`` → ``awaiting_checkin`` → ``checked_in`` → ``checked_out``.
Transitions are driven by coordinator data updates and timer-scheduled
callbacks.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from datetime import timedelta
import logging
from typing import TYPE_CHECKING
from typing import Any

from homeassistant.components.calendar import CalendarEvent
from homeassistant.components.datetime import DOMAIN as DATETIME
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE
from homeassistant.core import HomeAssistant
from homeassistant.core import callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.helpers.restore_state import ExtraStoredData
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from ..const import CHECKIN_STATE_AWAITING
from ..const import CHECKIN_STATE_CHECKED_IN
from ..const import CHECKIN_STATE_CHECKED_OUT
from ..const import CHECKIN_STATE_NO_RESERVATION
from ..const import CONF_CLEANING_WINDOW
from ..const import DEFAULT_CLEANING_WINDOW
from ..const import DOMAIN
from ..const import EARLY_CHECKOUT_EXPIRY_SWITCH
from ..const import EVENT_RENTAL_CONTROL_CHECKIN
from ..const import EVENT_RENTAL_CONTROL_CHECKOUT
from ..const import KEYMASTER_MONITORING_SWITCH
from ..util import add_call
from ..util import check_gather_results
from ..util import compute_early_expiry_time
from ..util import gen_uuid
from ..util import get_slot_name

if TYPE_CHECKING:
    from ..coordinator import RentalControlCoordinator

_LOGGER = logging.getLogger(__name__)


class CheckinExtraStoredData(ExtraStoredData):
    """Extra stored data for persisting CheckinTrackingSensor state.

    Serialises all sensor instance variables to a dict of JSON-safe
    values.  Datetime fields are stored as ISO 8601 strings (or
    ``None``).  The companion ``from_dict`` class-method rebuilds an
    instance from the dict produced by ``as_dict``.
    """

    def __init__(
        self,
        state: str,
        tracked_event_summary: str | None,
        tracked_event_start: datetime | None,
        tracked_event_end: datetime | None,
        tracked_event_slot_name: str | None,
        checkin_source: str | None,
        checkout_source: str | None,
        checkout_time: datetime | None,
        transition_target_time: datetime | None,
        checked_out_event_key: str | None,
        next_event_start_day: datetime | None = None,
    ) -> None:
        """Initialise from typed field values."""
        self.state = state
        self.tracked_event_summary = tracked_event_summary
        self.tracked_event_start = tracked_event_start
        self.tracked_event_end = tracked_event_end
        self.tracked_event_slot_name = tracked_event_slot_name
        self.checkin_source = checkin_source
        self.checkout_source = checkout_source
        self.checkout_time = checkout_time
        self.transition_target_time = transition_target_time
        self.checked_out_event_key = checked_out_event_key
        self.next_event_start_day = next_event_start_day

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict representation."""
        return {
            "state": self.state,
            "tracked_event_summary": self.tracked_event_summary,
            "tracked_event_start": (
                self.tracked_event_start.isoformat()
                if self.tracked_event_start
                else None
            ),
            "tracked_event_end": (
                self.tracked_event_end.isoformat() if self.tracked_event_end else None
            ),
            "tracked_event_slot_name": self.tracked_event_slot_name,
            "checkin_source": self.checkin_source,
            "checkout_source": self.checkout_source,
            "checkout_time": (
                self.checkout_time.isoformat() if self.checkout_time else None
            ),
            "transition_target_time": (
                self.transition_target_time.isoformat()
                if self.transition_target_time
                else None
            ),
            "checked_out_event_key": self.checked_out_event_key,
            "next_event_start_day": (
                self.next_event_start_day.isoformat()
                if self.next_event_start_day
                else None
            ),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CheckinExtraStoredData:
        """Reconstruct an instance from a dict (as produced by ``as_dict``).

        ISO 8601 date-time strings are parsed back to ``datetime``
        objects; ``None`` values pass through unchanged.
        """

        def _parse_dt(value: str | None) -> datetime | None:
            """Parse an ISO 8601 string to datetime or return None."""
            if value is None:
                return None
            try:
                result: datetime | None = dt_util.parse_datetime(value)
            except (TypeError, ValueError):
                _LOGGER.warning("Failed to parse stored datetime: %s", value)
                return None
            if result is None:
                _LOGGER.warning("Failed to parse stored datetime: %s", value)
            return result

        return cls(
            state=data.get("state", CHECKIN_STATE_NO_RESERVATION),
            tracked_event_summary=data.get("tracked_event_summary"),
            tracked_event_start=_parse_dt(data.get("tracked_event_start")),
            tracked_event_end=_parse_dt(data.get("tracked_event_end")),
            tracked_event_slot_name=data.get("tracked_event_slot_name"),
            checkin_source=data.get("checkin_source"),
            checkout_source=data.get("checkout_source"),
            checkout_time=_parse_dt(data.get("checkout_time")),
            transition_target_time=_parse_dt(data.get("transition_target_time")),
            checked_out_event_key=data.get("checked_out_event_key"),
            next_event_start_day=_parse_dt(data.get("next_event_start_day")),
        )


class CheckinTrackingSensor(
    CoordinatorEntity["RentalControlCoordinator"],
    SensorEntity,
    RestoreEntity,
):
    """Sensor that tracks guest check-in/check-out state.

    Maintains a four-state state machine driven by coordinator data
    updates and timer-scheduled callbacks:

    - ``no_reservation``: No relevant calendar event
    - ``awaiting_checkin``: Event identified, waiting for guest arrival
    - ``checked_in``: Guest has arrived
    - ``checked_out``: Guest has departed, post-checkout linger active
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
        self._attr_has_entity_name = True
        self._attr_translation_key = "checkin"
        self._attr_device_class = SensorDeviceClass.ENUM
        self._attr_options = [
            CHECKIN_STATE_NO_RESERVATION,
            CHECKIN_STATE_AWAITING,
            CHECKIN_STATE_CHECKED_IN,
            CHECKIN_STATE_CHECKED_OUT,
        ]
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

        # FR-006c: Start day of the follow-on event for midnight-to-awaiting
        self._next_event_start_day: datetime | None = None

        # Follow-on event key for linger guard (not persisted)
        self._linger_followon_key: str | None = None

        # Effective baseline for follow-on event lookups (not persisted).
        # Set by _transition_to_checked_out to preserve the correct
        # reference point across subsequent coordinator updates.
        self._linger_baseline: datetime | None = None

        # Internal timer unsubscribe handle
        self._unsub_timer: CALLBACK_TYPE | None = None

        # Guard against repeated warnings when tracked event is missing
        self._event_missing_warned: bool = False

        # Unique ID
        self._unique_id = gen_uuid(f"{self.coordinator.unique_id} checkin_tracking")

    @property
    def unique_id(self) -> str:
        """Return the unique ID for this sensor."""
        return self._unique_id

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
            "checkin_state": self._state,
            "summary": self._tracked_event_summary,
            "start": self._tracked_event_start,
            "end": self._tracked_event_end,
            "guest_name": self._tracked_event_slot_name,
            "checkin_source": self._checkin_source,
            "checkout_source": self._checkout_source,
            "checkout_time": self._checkout_time,
            "next_transition": self._transition_target_time,
        }

    @property
    def extra_restore_state_data(self) -> CheckinExtraStoredData:
        """Return entity-specific state data for RestoreEntity persistence.

        Returns a ``CheckinExtraStoredData`` instance containing all
        sensor fields that must survive an HA restart.
        """
        return CheckinExtraStoredData(
            state=self._state,
            tracked_event_summary=self._tracked_event_summary,
            tracked_event_start=self._tracked_event_start,
            tracked_event_end=self._tracked_event_end,
            tracked_event_slot_name=self._tracked_event_slot_name,
            checkin_source=self._checkin_source,
            checkout_source=self._checkout_source,
            checkout_time=self._checkout_time,
            transition_target_time=self._transition_target_time,
            checked_out_event_key=self._checked_out_event_key,
            next_event_start_day=self._next_event_start_day,
        )

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

    def _get_cleaning_window(self) -> float:
        """Return the configured cleaning window in hours.

        Returns:
            The cleaning window duration from config data,
            or the default value.
        """
        return float(
            self._config_entry.data.get(CONF_CLEANING_WINDOW, DEFAULT_CLEANING_WINDOW)
        )

    def _cancel_timer(self) -> None:
        """Cancel any pending timer callback."""
        if self._unsub_timer is not None:
            self._unsub_timer()
            self._unsub_timer = None

    def _get_relevant_event(self) -> CalendarEvent | None:
        """Get the most relevant event from coordinator data.

        Returns:
            The first event from coordinator data (event 0), or None
            if no events are available.
        """
        if self.coordinator.data and len(self.coordinator.data) > 0:
            return self.coordinator.data[0]
        return None

    def _get_next_event(self) -> CalendarEvent | None:
        """Get the next event after the current one.

        Returns:
            The second event from coordinator data (event 1), or None
            if not available.
        """
        if self.coordinator.data and len(self.coordinator.data) > 1:
            return self.coordinator.data[1]
        return None

    def _find_followon_event(self, checkout_time: datetime) -> CalendarEvent | None:
        """Find the first follow-on event after checkout.

        Scans coordinator data for the first event that is not the
        checked-out event and starts at or after checkout time. This
        avoids hardcoding an index, which would break if the
        checked-out event has already been filtered from the data.

        Args:
            checkout_time: The checkout time to compare against.

        Returns:
            The follow-on CalendarEvent, or None if none found.
        """
        events = self.coordinator.data or []
        for event in events:
            if self._checked_out_event_key is not None:
                event_key = self._event_key(event.summary, event.start)
                if event_key == self._checked_out_event_key:
                    continue
            if event.start >= checkout_time:
                return event
        return None

    def _find_tracked_event(self) -> CalendarEvent | None:
        """Find the currently tracked event in coordinator data by identity key.

        Searches all events, not just position 0, to handle event
        list reordering during coordinator refreshes.

        Returns:
            The tracked CalendarEvent, or None if not found.
        """
        if (
            not self.coordinator.data
            or self._tracked_event_summary is None
            or self._tracked_event_start is None
        ):
            return None
        tracked_key = self._event_key(
            self._tracked_event_summary, self._tracked_event_start
        )
        for event in self.coordinator.data:
            if self._event_key(event.summary, event.start) == tracked_key:
                return event
        return None

    def _extract_slot_name(self, event: CalendarEvent) -> str | None:
        """Extract guest/slot name from a calendar event.

        Uses the same extraction logic as the existing cal sensor.

        Args:
            event: The calendar event to extract from.

        Returns:
            The extracted slot name, or None.
        """
        return get_slot_name(
            event.summary,
            event.description or "",
            self.coordinator.event_prefix or "",
        )

    def _is_keymaster_monitoring_enabled(self) -> bool:
        """Return True when keymaster monitoring is switched on.

        Looks up the :class:`KeymasterMonitoringSwitch` stored in
        ``hass.data`` for this config entry.  Returns ``False`` when
        the switch entity is missing or turned off.
        """
        entry_data = self._hass.data.get(DOMAIN, {}).get(
            self._config_entry.entry_id, {}
        )
        monitoring_switch = entry_data.get(KEYMASTER_MONITORING_SWITCH)
        return monitoring_switch is not None and monitoring_switch.is_on

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator.

        Evaluates the current event data and state to determine if a
        state transition is needed.
        """
        _LOGGER.debug(
            "Running CheckinTrackingSensor coordinator update for %s",
            self.coordinator.name,
        )

        if not self.coordinator.last_update_success:
            self.async_write_ha_state()
            return

        current_state = self._state

        if current_state == CHECKIN_STATE_NO_RESERVATION:
            event = self._get_relevant_event()
            if event is not None:
                self._transition_to_awaiting(event)
            else:
                self.async_write_ha_state()

        elif current_state == CHECKIN_STATE_AWAITING:
            tracked = self._find_tracked_event()
            if tracked is not None:
                # Same event still in coordinator — update mutable fields
                self._tracked_event_end = tracked.end
                self._tracked_event_slot_name = self._extract_slot_name(tracked)
                self.async_write_ha_state()
            else:
                # Tracked event gone — pick up next available or clear
                event = self._get_relevant_event()
                if event is not None:
                    self._transition_to_awaiting(event)
                else:
                    self._transition_to_no_reservation()

        elif current_state == CHECKIN_STATE_CHECKED_IN:
            tracked = self._find_tracked_event()
            if tracked is not None:
                self._event_missing_warned = False
                now = dt_util.now()
                if tracked.end <= now:
                    # Event has ended — force checkout as safety net
                    # in case the auto-checkout timer failed to fire.
                    _LOGGER.debug(
                        "Event end time %s has passed, forcing "
                        "automatic checkout for %s",
                        tracked.end,
                        self.coordinator.name,
                    )
                    self._tracked_event_end = tracked.end
                    self._tracked_event_slot_name = self._extract_slot_name(tracked)
                    self._cancel_timer()
                    self._transition_to_checked_out(
                        source="automatic",
                        linger_baseline=tracked.end,
                    )
                elif tracked.end != self._tracked_event_end:
                    # FR-030: Check if event end time changed
                    _LOGGER.debug(
                        "Event end time changed from %s to %s, "
                        "rescheduling auto check-out",
                        self._tracked_event_end,
                        tracked.end,
                    )
                    self._tracked_event_end = tracked.end
                    self._cancel_timer()
                    self._schedule_auto_checkout(tracked.end)
                    self._tracked_event_slot_name = self._extract_slot_name(tracked)
                    self.async_write_ha_state()
                else:
                    # Update slot name in case it changed
                    self._tracked_event_slot_name = self._extract_slot_name(tracked)
                    self.async_write_ha_state()
            else:
                # Tracked event not found in coordinator data.
                # If the stored end time has already passed, force
                # checkout as a safety net (mirrors the tracked-event
                # end-time check above).  Otherwise, preserve
                # checked_in for transient data mismatches.
                fallback_end = self._tracked_event_end
                if fallback_end is not None and fallback_end <= dt_util.now():
                    _LOGGER.warning(
                        "Tracked event not found in coordinator data "
                        "while checked_in for %s and stored end "
                        "time %s has passed; forcing checkout",
                        self.coordinator.name,
                        fallback_end,
                    )
                    self._cancel_timer()
                    self._transition_to_checked_out(
                        source="automatic",
                        linger_baseline=fallback_end,
                    )
                else:
                    if not self._event_missing_warned:
                        _LOGGER.warning(
                            "Tracked event not found in coordinator "
                            "data while checked_in for %s; "
                            "preserving state",
                            self.coordinator.name,
                        )
                        self._event_missing_warned = True
                    else:
                        _LOGGER.debug(
                            "Tracked event still missing for %s; "
                            "preserving checked_in state",
                            self.coordinator.name,
                        )
                    self.async_write_ha_state()

        elif current_state == CHECKIN_STATE_CHECKED_OUT:
            checkout_time = (
                self._linger_baseline or self._checkout_time or dt_util.now()
            )
            followon = self._find_followon_event(checkout_time)
            if followon is not None:
                followon_key = self._event_key(followon.summary, followon.start)
                if (
                    self._unsub_timer is not None
                    and self._linger_followon_key == followon_key
                ):
                    # Same follow-on, timer already scheduled
                    self.async_write_ha_state()
                else:
                    # New or changed follow-on event
                    self._cancel_timer()
                    self._compute_linger_timing()
                    self.async_write_ha_state()
            else:
                # No follow-on event
                if self._linger_followon_key is not None:
                    # Follow-on was removed — recompute for FR-006b
                    self._cancel_timer()
                    self._linger_followon_key = None
                    self._compute_linger_timing()
                elif self._unsub_timer is None:
                    self._compute_linger_timing()
                self.async_write_ha_state()

    def _transition_to_awaiting(self, event: CalendarEvent) -> None:
        """Transition to awaiting_checkin state.

        Sets state and attributes from the provided event and schedules
        auto check-in at event start time.

        Args:
            event: The calendar event to track.
        """
        _LOGGER.debug(
            "Transitioning to awaiting_checkin for event: %s",
            event.summary,
        )
        self._state = CHECKIN_STATE_AWAITING
        self._tracked_event_summary = event.summary
        self._tracked_event_start = event.start
        self._tracked_event_end = event.end
        self._tracked_event_slot_name = self._extract_slot_name(event)
        self._checkin_source = None
        self._checkout_source = None
        self._checkout_time = None
        self._checked_out_event_key = None
        self._next_event_start_day = None
        self._linger_followon_key = None
        self._linger_baseline = None
        self._event_missing_warned = False

        # Schedule auto check-in at event start time
        self._cancel_timer()
        now = dt_util.now()
        if event.start > now:
            self._transition_target_time = event.start
            self._unsub_timer = async_track_point_in_time(
                self._hass,
                self._async_auto_checkin_callback,
                event.start,
            )
        else:
            # Event start already passed — auto check-in only if
            # keymaster monitoring is disabled; otherwise stay in
            # awaiting_checkin until the guest uses their code.
            self._transition_target_time = None
            if not self._is_keymaster_monitoring_enabled():
                self._transition_to_checked_in(source="automatic")
                return

        self.async_write_ha_state()

    def _transition_to_checked_in(self, source: str) -> None:
        """Transition to checked_in state.

        Fires a check-in event on the HA event bus and schedules auto
        check-out at the event end time.

        Args:
            source: How check-in occurred (``keymaster`` or ``automatic``).
        """
        _LOGGER.debug(
            "Transitioning to checked_in (source=%s) for event: %s",
            source,
            self._tracked_event_summary,
        )
        self._state = CHECKIN_STATE_CHECKED_IN
        self._checkin_source = source
        self._transition_target_time = None
        self._event_missing_warned = False

        # Fire check-in event (per contracts/events.md)
        self._hass.bus.async_fire(
            EVENT_RENTAL_CONTROL_CHECKIN,
            {
                "entity_id": self.entity_id,
                "summary": self._tracked_event_summary or "",
                "start": (
                    self._tracked_event_start.isoformat()
                    if self._tracked_event_start
                    else ""
                ),
                "end": (
                    self._tracked_event_end.isoformat()
                    if self._tracked_event_end
                    else ""
                ),
                "guest_name": self._tracked_event_slot_name or "",
                "source": source,
            },
        )

        # Schedule auto check-out at event end time
        self._cancel_timer()
        if self._tracked_event_end is not None:
            self._schedule_auto_checkout(self._tracked_event_end)

        self.async_write_ha_state()

    def _schedule_auto_checkout(self, end_time: datetime) -> None:
        """Schedule automatic checkout at the given end time.

        Args:
            end_time: When to trigger automatic checkout.
        """
        now = dt_util.now()
        if end_time > now:
            self._transition_target_time = end_time
            self._unsub_timer = async_track_point_in_time(
                self._hass,
                self._async_auto_checkout_callback,
                end_time,
            )
        else:
            # End time already passed - checkout immediately
            self._transition_target_time = None
            self._transition_to_checked_out(
                source="automatic",
                linger_baseline=end_time,
            )

    def _transition_to_checked_out(
        self,
        source: str,
        linger_baseline: datetime | None = None,
    ) -> None:
        """Transition to checked_out state.

        Records checkout details, fires checkout event, stores event
        identity key for FR-007, and computes post-checkout linger timing.

        Args:
            source: How check-out occurred (``manual`` or ``automatic``).
            linger_baseline: Reference time for follow-on event lookup
                and linger scheduling.  Defaults to ``dt_util.now()``.
                Pass the event end time when the reservation has already
                ended so that follow-on events starting between ``end``
                and ``now`` are not skipped.
        """
        _LOGGER.debug(
            "Transitioning to checked_out (source=%s) for event: %s",
            source,
            self._tracked_event_summary,
        )
        self._state = CHECKIN_STATE_CHECKED_OUT
        self._checkout_source = source
        self._checkout_time = dt_util.now()
        self._event_missing_warned = False

        # Store event identity key for FR-007
        if self._tracked_event_summary and self._tracked_event_start:
            self._checked_out_event_key = self._event_key(
                self._tracked_event_summary, self._tracked_event_start
            )

        # Fire check-out event (per contracts/events.md)
        self._hass.bus.async_fire(
            EVENT_RENTAL_CONTROL_CHECKOUT,
            {
                "entity_id": self.entity_id,
                "summary": self._tracked_event_summary or "",
                "start": (
                    self._tracked_event_start.isoformat()
                    if self._tracked_event_start
                    else ""
                ),
                "end": (
                    self._tracked_event_end.isoformat()
                    if self._tracked_event_end
                    else ""
                ),
                "guest_name": self._tracked_event_slot_name or "",
                "source": source,
            },
        )

        # Compute post-checkout linger timing
        self._cancel_timer()
        effective_baseline = linger_baseline or self._checkout_time
        self._linger_baseline = effective_baseline
        self._compute_linger_timing(baseline=effective_baseline)

        self.async_write_ha_state()

    def _compute_linger_timing(self, baseline: datetime | None = None) -> None:
        """Compute and schedule post-checkout linger timing.

        Determines which FR-006 scenario applies based on the next
        event's timing and schedules the appropriate transition.

        Date comparisons use the HA-configured local timezone so that
        "same calendar day" is evaluated from the user's perspective,
        not raw UTC dates.

        Args:
            baseline: Reference time for follow-on event lookup and
                gap calculations.  Falls back to ``_linger_baseline``,
                then ``_checkout_time``, then ``dt_util.now()``.
        """
        checkout_time = (
            baseline or self._linger_baseline or self._checkout_time or dt_util.now()
        )
        next_event = self._find_followon_event(checkout_time)

        if next_event is not None:
            self._linger_followon_key = self._event_key(
                next_event.summary, next_event.start
            )
            # Compare dates in the HA-configured local timezone (T038)
            local_checkout = dt_util.as_local(checkout_time)
            local_next_start = dt_util.as_local(next_event.start)

            if local_next_start.date() == local_checkout.date():
                # FR-006a: Same-day turnover
                # Transition at half the gap between checkout and next start
                gap = next_event.start - checkout_time
                half_gap = checkout_time + (gap / 2)
                self._transition_target_time = half_gap
                self._next_event_start_day = None
                self._unsub_timer = async_track_point_in_time(
                    self._hass,
                    self._async_linger_to_awaiting_callback,
                    half_gap,
                )
                _LOGGER.debug(
                    "FR-006a: Same-day turnover, transition to awaiting at %s",
                    half_gap.isoformat(),
                )
            else:
                # FR-006c: Different-day follow-on
                # Transition at midnight boundary; store next event's start
                # day for the follow-up timer (T039)
                midnight = dt_util.start_of_local_day(checkout_time + timedelta(days=1))
                self._transition_target_time = midnight
                self._next_event_start_day = dt_util.start_of_local_day(
                    next_event.start
                )
                self._unsub_timer = async_track_point_in_time(
                    self._hass,
                    self._async_linger_to_no_reservation_callback,
                    midnight,
                )
                _LOGGER.debug(
                    "FR-006c: Different-day follow-on, transition to "
                    "no_reservation at %s, follow-up awaiting at %s",
                    midnight.isoformat(),
                    self._next_event_start_day.isoformat(),
                )
        else:
            self._linger_followon_key = None
            # FR-006b: No follow-on reservation
            # Transition after cleaning window
            cleaning_hours = self._get_cleaning_window()
            linger_end = checkout_time + timedelta(hours=cleaning_hours)
            self._transition_target_time = linger_end
            self._next_event_start_day = None
            self._unsub_timer = async_track_point_in_time(
                self._hass,
                self._async_linger_to_no_reservation_callback,
                linger_end,
            )
            _LOGGER.debug(
                "FR-006b: No follow-on, transition to no_reservation at %s",
                linger_end.isoformat(),
            )

    def _transition_to_no_reservation(self) -> None:
        """Transition to no_reservation state.

        Clears all tracked event data and cancels any pending timers.
        Note: Does NOT clear ``_next_event_start_day`` — the FR-006c
        follow-up logic in ``_async_linger_to_no_reservation_callback``
        reads it after this method returns.
        """
        _LOGGER.debug("Transitioning to no_reservation")
        self._state = CHECKIN_STATE_NO_RESERVATION
        self._tracked_event_summary = None
        self._tracked_event_start = None
        self._tracked_event_end = None
        self._tracked_event_slot_name = None
        self._checkin_source = None
        self._checkout_source = None
        self._checkout_time = None
        self._transition_target_time = None
        self._checked_out_event_key = None
        self._linger_followon_key = None
        self._linger_baseline = None
        self._event_missing_warned = False

        self._cancel_timer()
        self.async_write_ha_state()

    @callback
    def _async_auto_checkin_callback(self, _now: datetime) -> None:
        """Timer callback for automatic check-in at event start time.

        When keymaster monitoring is enabled the sensor must stay in
        ``awaiting_checkin`` until the guest actually uses their door
        code, so the automatic transition is skipped.

        Args:
            _now: The current time when the callback fires.
        """
        _LOGGER.debug("Auto check-in timer fired for %s", self.coordinator.name)
        self._unsub_timer = None
        if self._state != CHECKIN_STATE_AWAITING:
            return
        if self._is_keymaster_monitoring_enabled():
            _LOGGER.debug(
                "Keymaster monitoring is on; staying in awaiting_checkin "
                "until door code is used for %s",
                self.coordinator.name,
            )
            self._transition_target_time = None
            self.async_write_ha_state()
            return
        self._transition_to_checked_in(source="automatic")

    @callback
    def _async_auto_checkout_callback(self, _now: datetime) -> None:
        """Timer callback for automatic check-out at event end time.

        Args:
            _now: The current time when the callback fires.
        """
        _LOGGER.debug("Auto check-out timer fired for %s", self.coordinator.name)
        self._unsub_timer = None
        if self._state == CHECKIN_STATE_CHECKED_IN:
            self._transition_to_checked_out(source="automatic")

    @callback
    def _async_linger_to_awaiting_callback(self, _now: datetime) -> None:
        """Timer callback for same-day turnover (FR-006a).

        Transitions from checked_out to awaiting_checkin for the next event.

        Args:
            _now: The current time when the callback fires.
        """
        _LOGGER.debug("Linger-to-awaiting timer fired for %s", self.coordinator.name)
        self._unsub_timer = None
        if self._state == CHECKIN_STATE_CHECKED_OUT:
            checkout_time = self._linger_baseline or self._checkout_time or _now
            event = self._find_followon_event(checkout_time)
            if event is not None:
                self._transition_to_awaiting(event)
            else:
                self._transition_to_no_reservation()

    @callback
    def _async_linger_to_no_reservation_callback(self, _now: datetime) -> None:
        """Timer callback for post-checkout linger expiry (FR-006b/c).

        Transitions from checked_out to no_reservation. For FR-006c,
        schedules a follow-up timer at 00:00 on the next event's start
        day to transition from no_reservation to awaiting_checkin (T039).

        Args:
            _now: The current time when the callback fires.
        """
        _LOGGER.debug(
            "Linger-to-no-reservation timer fired for %s", self.coordinator.name
        )
        self._unsub_timer = None
        if self._state == CHECKIN_STATE_CHECKED_OUT:
            # Capture follow-on event start day before clearing state
            followon_start_day = self._next_event_start_day
            self._transition_to_no_reservation()

            # T039: FR-006c follow-up timer for midnight-to-awaiting
            if followon_start_day is not None:
                if followon_start_day > _now:
                    self._transition_target_time = followon_start_day
                    self._next_event_start_day = followon_start_day
                    self._unsub_timer = async_track_point_in_time(
                        self._hass,
                        self._async_no_reservation_to_awaiting_callback,
                        followon_start_day,
                    )
                    _LOGGER.debug(
                        "FR-006c: Scheduled follow-up awaiting transition at %s for %s",
                        followon_start_day.isoformat(),
                        self.coordinator.name,
                    )
                    self.async_write_ha_state()
                else:
                    # Follow-on start day already passed — trigger
                    # coordinator-driven evaluation on next update
                    _LOGGER.debug(
                        "FR-006c: Follow-on start day %s already passed, "
                        "relying on coordinator update",
                        followon_start_day.isoformat(),
                    )

    @callback
    def _async_no_reservation_to_awaiting_callback(self, _now: datetime) -> None:
        """Timer callback for FR-006c follow-up awaiting transition.

        Fires at 00:00 on the next event's start day. Finds the
        follow-on event in coordinator data and transitions to
        awaiting_checkin. Falls back to no-op if the event has
        disappeared.

        Args:
            _now: The current time when the callback fires.
        """
        _LOGGER.debug(
            "FR-006c no-reservation-to-awaiting timer fired for %s",
            self.coordinator.name,
        )
        self._unsub_timer = None
        self._next_event_start_day = None
        self._transition_target_time = None

        if self._state != CHECKIN_STATE_NO_RESERVATION:
            return

        # Find the next event from coordinator data
        event = self._get_relevant_event()
        if event is not None:
            self._transition_to_awaiting(event)
        else:
            _LOGGER.debug(
                "FR-006c: No event found in coordinator data at "
                "follow-up time, staying in no_reservation",
            )

    async def async_checkout(self) -> None:
        """Handle manual checkout service call.

        Validates guard conditions:
        1. Sensor must be in ``checked_in`` state
        2. Reservation boundaries must be known
        3. Current date must be on or after the last day of the
           reservation (the calendar date of the event end time)

        On success, calls ``_transition_to_checked_out(source="manual")``.

        Raises:
            ServiceValidationError: If guard conditions are not met.
        """
        # Guard 1: State must be checked_in
        if self._state != CHECKIN_STATE_CHECKED_IN:
            raise ServiceValidationError(
                f"Checkout is only available when the guest is "
                f"checked in (current state: {self._state})"
            )

        # Guard 2: Reservation boundaries must be known
        now = dt_util.now()
        end = self._tracked_event_end

        if self._tracked_event_start is None or end is None:
            raise ServiceValidationError(
                "Checkout requires known reservation boundaries"
            )

        # Guard 3: Must be on or after the last day of the reservation
        local_now = dt_util.as_local(now)
        local_end = dt_util.as_local(end)
        if local_now.date() < local_end.date():
            raise ServiceValidationError(
                f"Checkout is only available on the last day of "
                f"the reservation or later "
                f"(current: {local_now.date().isoformat()}, "
                f"checkout day: {local_end.date().isoformat()})"
            )

        # Early expiry: shorten lock code if switch is on (FR-022)
        entry_data = self._hass.data.get(DOMAIN, {}).get(
            self._config_entry.entry_id, {}
        )
        early_expiry_switch = entry_data.get(EARLY_CHECKOUT_EXPIRY_SWITCH)

        if early_expiry_switch is not None and early_expiry_switch.is_on:
            if self._tracked_event_end is not None:
                new_end = compute_early_expiry_time(now, self._tracked_event_end)
                if new_end < self._tracked_event_end:
                    _LOGGER.info(
                        "Early checkout expiry: shortening end time from %s to %s for %s",
                        self._tracked_event_end,
                        new_end,
                        self._tracked_event_summary,
                    )
                    self._tracked_event_end = new_end
                    await self._async_update_lock_code_expiry(new_end)

        # Use event end as linger baseline when past end so that
        # follow-on event detection uses the correct reference point.
        effective_baseline = min(now, end)
        self._transition_to_checked_out(
            source="manual", linger_baseline=effective_baseline
        )

    async def async_added_to_hass(self) -> None:
        """Restore persisted state and validate against current time/data.

        Retrieves the last stored extra data (if any) via
        ``async_get_last_extra_data``, repopulates all instance fields,
        then runs stale-state validation to auto-correct outdated state
        and reschedule timers.

        If no prior state exists the sensor starts in
        ``no_reservation`` and falls through to normal coordinator
        processing.
        """
        await super().async_added_to_hass()

        # --- T018: Restore persisted state ---
        last_extra = await self.async_get_last_extra_data()
        if last_extra is not None:
            data = last_extra.as_dict()
            restored = CheckinExtraStoredData.from_dict(data)

            self._state = restored.state
            self._tracked_event_summary = restored.tracked_event_summary
            self._tracked_event_start = restored.tracked_event_start
            self._tracked_event_end = restored.tracked_event_end
            self._tracked_event_slot_name = restored.tracked_event_slot_name
            self._checkin_source = restored.checkin_source
            self._checkout_source = restored.checkout_source
            self._checkout_time = restored.checkout_time
            self._transition_target_time = restored.transition_target_time
            self._checked_out_event_key = restored.checked_out_event_key
            self._next_event_start_day = restored.next_event_start_day

            _LOGGER.debug(
                "Restored state '%s' for %s", self._state, self.coordinator.name
            )

            # --- T020: Stale-state validation ---
            self._validate_restored_state()

            # After restoration and stale-state correction, reconcile with
            # current coordinator data so restored state is immediately
            # consistent with the latest calendar events.
            if (
                self.coordinator.last_update_success
                and self.coordinator.data is not None
            ):
                self._handle_coordinator_update()
        else:
            _LOGGER.debug(
                "No prior state for %s, starting as %s",
                self.coordinator.name,
                CHECKIN_STATE_NO_RESERVATION,
            )
            # Fall through: process any current coordinator data
            if (
                self.coordinator.last_update_success
                and self.coordinator.data is not None
            ):
                self._handle_coordinator_update()

    def _validate_restored_state(self) -> None:
        """Validate restored state against current time and coordinator data.

        Auto-corrects stale state and reschedules timers:

        * ``checked_in`` with ended event → ``checked_out``
        * ``awaiting_checkin`` with passed start (time-based) → ``checked_in``
        * ``checked_out`` with expired linger / new event → reprocess
        * Valid state with pending transition → reschedule timer
        """
        now = dt_util.now()
        current_state = self._state

        if current_state == CHECKIN_STATE_CHECKED_IN:
            if self._tracked_event_end is not None and self._tracked_event_end <= now:
                # Event has ended while we were down → silent checkout
                # (no HA bus event to avoid triggering automations on
                # restart catch-up).
                _LOGGER.debug(
                    "Stale restore: checked_in but event ended, "
                    "transitioning to checked_out (silent)"
                )
                self._state = CHECKIN_STATE_CHECKED_OUT
                self._checkout_source = "automatic"
                self._checkout_time = self._tracked_event_end
                if (
                    self._tracked_event_summary is not None
                    and self._tracked_event_start is not None
                ):
                    self._checked_out_event_key = self._event_key(
                        self._tracked_event_summary,
                        self._tracked_event_start,
                    )
                self._compute_linger_timing()
                self.async_write_ha_state()
            else:
                # Still valid checked_in — reschedule auto-checkout timer
                if self._tracked_event_end is not None:
                    self._cancel_timer()
                    self._schedule_auto_checkout(self._tracked_event_end)
                else:
                    self._cancel_timer()
                    self._transition_target_time = None
                self.async_write_ha_state()

        elif current_state == CHECKIN_STATE_AWAITING:
            if (
                self._tracked_event_start is not None
                and self._tracked_event_start <= now
                and not self._is_keymaster_monitoring_enabled()
            ):
                # Event start passed while we were down → silent checkin
                # (no HA bus event to avoid triggering automations on
                # restart catch-up).  Skipped when keymaster monitoring
                # is enabled — the guest must use their door code.
                _LOGGER.debug(
                    "Stale restore: awaiting_checkin but start passed, "
                    "transitioning to checked_in (silent)"
                )
                self._state = CHECKIN_STATE_CHECKED_IN
                self._checkin_source = "automatic"
                self._cancel_timer()
                self._transition_target_time = None
                if (
                    self._tracked_event_end is not None
                    and self._tracked_event_end <= now
                ):
                    # Event also ended — silent checkout too
                    self._state = CHECKIN_STATE_CHECKED_OUT
                    self._checkout_source = "automatic"
                    self._checkout_time = self._tracked_event_end
                    if (
                        self._tracked_event_summary is not None
                        and self._tracked_event_start is not None
                    ):
                        self._checked_out_event_key = self._event_key(
                            self._tracked_event_summary,
                            self._tracked_event_start,
                        )
                    self._compute_linger_timing()
                elif self._tracked_event_end is not None:
                    self._schedule_auto_checkout(self._tracked_event_end)
                self.async_write_ha_state()
            else:
                # Still valid awaiting — reschedule auto-checkin timer
                if self._tracked_event_start is not None:
                    self._cancel_timer()
                    self._transition_target_time = self._tracked_event_start
                    self._unsub_timer = async_track_point_in_time(
                        self._hass,
                        self._async_auto_checkin_callback,
                        self._tracked_event_start,
                    )
                else:
                    self._cancel_timer()
                    self._transition_target_time = None
                self.async_write_ha_state()

        elif current_state == CHECKIN_STATE_CHECKED_OUT:
            # Per spec acceptance scenario US2-4: if a genuinely new
            # event is now relevant (different from the checked-out
            # event), skip linger and transition to awaiting for it.
            event = self._get_relevant_event()
            if event is not None:
                event_key = self._event_key(event.summary, event.start)
                if event_key != self._checked_out_event_key:
                    _LOGGER.debug(
                        "Stale restore: checked_out but new event "
                        "available, transitioning to awaiting_checkin"
                    )
                    self._transition_to_awaiting(event)
                    return

            # No new event (or same checked-out event). Check if the
            # linger period has expired while HA was down.
            linger_expired = False
            if (
                self._transition_target_time is not None
                and self._transition_target_time <= now
            ):
                linger_expired = True
            elif self._checkout_time is not None:
                # No stored transition target — conservatively use the
                # cleaning window to determine expiry.
                cleaning_hours = self._get_cleaning_window()
                linger_expired = (
                    self._checkout_time + timedelta(hours=cleaning_hours)
                ) <= now

            if linger_expired:
                _LOGGER.debug(
                    "Stale restore: checked_out and linger expired, "
                    "transitioning to no_reservation"
                )
                self._transition_to_no_reservation()
            elif (
                self.coordinator.last_update_success
                and self.coordinator.data is not None
            ):
                # Reschedule linger timer based on restored checkout time
                # and current coordinator data.
                self._compute_linger_timing()
                self.async_write_ha_state()
            else:
                # Linger has not expired — reschedule the linger timer
                # so we will transition out of checked_out once the
                # window ends.
                self._compute_linger_timing()
                self.async_write_ha_state()

        elif current_state == CHECKIN_STATE_NO_RESERVATION:
            # FR-006c: Reschedule follow-up timer if pending
            if (
                self._next_event_start_day is not None
                and self._next_event_start_day > now
            ):
                self._cancel_timer()
                self._transition_target_time = self._next_event_start_day
                self._unsub_timer = async_track_point_in_time(
                    self._hass,
                    self._async_no_reservation_to_awaiting_callback,
                    self._next_event_start_day,
                )
                _LOGGER.debug(
                    "Restored FR-006c follow-up timer at %s for %s",
                    self._next_event_start_day.isoformat(),
                    self.coordinator.name,
                )
            elif self._next_event_start_day is not None:
                # Follow-up time already passed — clear stale data
                self._next_event_start_day = None
                self._transition_target_time = None
            self.async_write_ha_state()

        else:
            # Unknown or corrupted state — reset to no_reservation
            _LOGGER.warning(
                "Unknown restored state '%s', resetting to no_reservation",
                current_state,
            )
            self._transition_to_no_reservation()

    async def async_will_remove_from_hass(self) -> None:
        """Clean up when entity is being removed."""
        self._cancel_timer()
        await super().async_will_remove_from_hass()

    @callback
    def async_handle_keymaster_unlock(
        self,
        code_slot_num: int,
    ) -> None:
        """Handle a keymaster unlock event.

        Called by the event bus listener after validating lockname,
        state, slot range, and monitoring switch is on.  This method
        performs remaining sensor-level checks:

        - ``code_slot_num != 0`` (FR-017: ignore manual/RF unlocks)
        - ``code_slot_num`` is within the managed slot range
        - Sensor is in ``awaiting_checkin`` state → transition to checked_in
        - Sensor is in ``checked_in`` state → ignored (early checkout
          expiry is handled by ``async_checkout`` per FR-022/FR-023)

        Args:
            code_slot_num: The keymaster code slot number that was used.
        """
        # FR-017: Ignore manual/RF unlocks (code_slot_num == 0)
        if code_slot_num == 0:
            _LOGGER.debug("Ignoring keymaster unlock: code_slot_num == 0 (manual/RF)")
            return

        # Validate code slot is in managed range
        start_slot = self.coordinator.start_slot
        max_events = self.coordinator.max_events
        if not (start_slot <= code_slot_num < start_slot + max_events):
            _LOGGER.debug(
                "Ignoring keymaster unlock: code_slot_num %d outside "
                "managed range [%d, %d)",
                code_slot_num,
                start_slot,
                start_slot + max_events,
            )
            return

        # Unlock while checked_in is ignored — early expiry is handled
        # by async_checkout per FR-022/FR-023
        if self._state == CHECKIN_STATE_CHECKED_IN:
            # Validate the unlock slot matches the tracked event
            tracked_slot = 0
            if self._tracked_event_slot_name and self.coordinator.event_overrides:
                tracked_slot = self.coordinator.event_overrides.get_slot_key_by_name(
                    self._tracked_event_slot_name
                )
            if tracked_slot != code_slot_num:
                _LOGGER.debug(
                    "Ignoring keymaster unlock: code_slot_num %d "
                    "does not match tracked event slot %d",
                    code_slot_num,
                    tracked_slot,
                )
                return
            return

        # Only process check-in when in awaiting_checkin state
        if self._state != CHECKIN_STATE_AWAITING:
            _LOGGER.debug(
                "Ignoring keymaster unlock: sensor state is %s, not awaiting_checkin",
                self._state,
            )
            return

        # All conditions met — transition to checked_in
        _LOGGER.info(
            "Keymaster unlock detected for slot %d, transitioning to checked_in for %s",
            code_slot_num,
            self._tracked_event_summary,
        )
        self._transition_to_checked_in(source="keymaster")

    async def _async_update_lock_code_expiry(self, new_end: datetime) -> None:
        """Update keymaster lock code expiry after early checkout.

        Calls the ``datetime.set_value`` service to shorten the
        lock-code date-range end so the physical code expires at
        *new_end* instead of the original reservation end.
        """
        if self._tracked_event_slot_name is None:
            return

        if not self.coordinator.event_overrides:
            return

        slot = self.coordinator.event_overrides.get_slot_key_by_name(
            self._tracked_event_slot_name
        )
        lockname = self.coordinator.lockname
        if not slot or not lockname:
            return

        coro = add_call(
            self._hass,
            [],
            DATETIME,
            "set_value",
            f"datetime.{lockname}_code_slot_{slot}_date_range_end",
            {"datetime": new_end.isoformat()},
        )
        results = await asyncio.gather(*coro, return_exceptions=True)
        check_gather_results(
            results,
            "Early checkout lock code expiry update",
            _LOGGER,
        )
