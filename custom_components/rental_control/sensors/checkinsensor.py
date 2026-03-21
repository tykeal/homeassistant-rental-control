# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Check-in/check-out tracking sensor for Rental Control.

Implements a four-state state machine that tracks guest occupancy:
``no_reservation`` → ``awaiting_checkin`` → ``checked_in`` → ``checked_out``.
Transitions are driven by coordinator data updates and timer-scheduled
callbacks.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta
import logging
from typing import TYPE_CHECKING
from typing import Any

from homeassistant.components.calendar import CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE
from homeassistant.core import HomeAssistant
from homeassistant.core import callback
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
from ..const import EVENT_RENTAL_CONTROL_CHECKIN
from ..const import EVENT_RENTAL_CONTROL_CHECKOUT
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
            result: datetime | None = dt_util.parse_datetime(value)
            if result is None:
                _LOGGER.warning("Failed to parse stored datetime: %s", value)
                return None
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
        )


class CheckinTrackingSensor(
    CoordinatorEntity["RentalControlCoordinator"],
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
            The cleaning window duration from config options,
            or the default value.
        """
        return float(
            self._config_entry.options.get(
                CONF_CLEANING_WINDOW, DEFAULT_CLEANING_WINDOW
            )
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

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator.

        Evaluates the current event data and state to determine if a
        state transition is needed.
        """
        _LOGGER.debug(
            "Running CheckinTrackingSensor coordinator update for %s",
            self.name,
        )

        if not self.coordinator.last_update_success:
            self.async_write_ha_state()
            return

        event = self._get_relevant_event()
        current_state = self._state

        if current_state == CHECKIN_STATE_NO_RESERVATION:
            if event is not None:
                self._transition_to_awaiting(event)
            else:
                self.async_write_ha_state()

        elif current_state == CHECKIN_STATE_AWAITING:
            if event is None:
                # Event disappeared (cancelled)
                self._transition_to_no_reservation()
            else:
                # Check if event identity changed (e.g., cancellation
                # shifted a different event to position 0)
                event_key = self._event_key(event.summary, event.start)
                tracked_key = None
                if (
                    self._tracked_event_summary is not None
                    and self._tracked_event_start is not None
                ):
                    tracked_key = self._event_key(
                        self._tracked_event_summary,
                        self._tracked_event_start,
                    )

                if event_key != tracked_key:
                    # Different event — full re-transition to reschedule
                    self._transition_to_awaiting(event)
                else:
                    # Same event — update mutable fields only
                    self._tracked_event_end = event.end
                    self._tracked_event_slot_name = self._extract_slot_name(event)
                    self.async_write_ha_state()

        elif current_state == CHECKIN_STATE_CHECKED_IN:
            if event is None:
                # Event disappeared while checked in
                self._transition_to_no_reservation()
            else:
                # FR-030: Check if event end time changed for the same event
                event_key = self._event_key(event.summary, event.start)
                tracked_key = None
                if (
                    self._tracked_event_summary is not None
                    and self._tracked_event_start is not None
                ):
                    tracked_key = self._event_key(
                        self._tracked_event_summary, self._tracked_event_start
                    )

                if event_key == tracked_key:
                    # Same event - check if end time changed
                    if event.end != self._tracked_event_end:
                        _LOGGER.debug(
                            "Event end time changed from %s to %s, "
                            "rescheduling auto check-out",
                            self._tracked_event_end,
                            event.end,
                        )
                        self._tracked_event_end = event.end
                        self._cancel_timer()
                        self._schedule_auto_checkout(event.end)
                    # Update slot name in case it changed
                    self._tracked_event_slot_name = self._extract_slot_name(event)
                    self.async_write_ha_state()
                else:
                    # Different event while checked in - transition out
                    self._transition_to_no_reservation()

        elif current_state == CHECKIN_STATE_CHECKED_OUT:
            if event is not None:
                event_key = self._event_key(event.summary, event.start)
                if event_key == self._checked_out_event_key:
                    # FR-007: Same event we checked out from, do NOT
                    # re-transition even if end time changed
                    self.async_write_ha_state()
                else:
                    # Genuinely new follow-on event while checked out:
                    # cancel any existing linger timer and recompute
                    # linger timing based on the updated coordinator data.
                    self._cancel_timer()
                    self._compute_linger_timing()
                    self.async_write_ha_state()
            else:
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
            # Event start already passed - auto check-in immediately
            self._transition_target_time = None
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
            self._transition_to_checked_out(source="automatic")

    def _transition_to_checked_out(self, source: str) -> None:
        """Transition to checked_out state.

        Records checkout details, fires checkout event, stores event
        identity key for FR-007, and computes post-checkout linger timing.

        Args:
            source: How check-out occurred (``manual`` or ``automatic``).
        """
        _LOGGER.debug(
            "Transitioning to checked_out (source=%s) for event: %s",
            source,
            self._tracked_event_summary,
        )
        self._state = CHECKIN_STATE_CHECKED_OUT
        self._checkout_source = source
        self._checkout_time = dt_util.now()

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
        self._compute_linger_timing()

        self.async_write_ha_state()

    def _compute_linger_timing(self) -> None:
        """Compute and schedule post-checkout linger timing.

        Determines which FR-006 scenario applies based on the next
        event's timing and schedules the appropriate transition.
        """
        checkout_time = self._checkout_time or dt_util.now()
        next_event = self._find_followon_event(checkout_time)

        if next_event is not None:
            # Check if next event starts on same calendar day
            if next_event.start.date() == checkout_time.date():
                # FR-006a: Same-day turnover
                # Transition at half the gap between checkout and next start
                gap = next_event.start - checkout_time
                half_gap = checkout_time + (gap / 2)
                self._transition_target_time = half_gap
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
                # Transition at midnight boundary
                midnight = dt_util.start_of_local_day(checkout_time + timedelta(days=1))
                self._transition_target_time = midnight
                self._unsub_timer = async_track_point_in_time(
                    self._hass,
                    self._async_linger_to_no_reservation_callback,
                    midnight,
                )
                _LOGGER.debug(
                    "FR-006c: Different-day follow-on, transition to "
                    "no_reservation at %s",
                    midnight.isoformat(),
                )
        else:
            # FR-006b: No follow-on reservation
            # Transition after cleaning window
            cleaning_hours = self._get_cleaning_window()
            linger_end = checkout_time + timedelta(hours=cleaning_hours)
            self._transition_target_time = linger_end
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

        self._cancel_timer()
        self.async_write_ha_state()

    @callback
    def _async_auto_checkin_callback(self, _now: datetime) -> None:
        """Timer callback for automatic check-in at event start time.

        Args:
            _now: The current time when the callback fires.
        """
        _LOGGER.debug("Auto check-in timer fired for %s", self.name)
        self._unsub_timer = None
        if self._state == CHECKIN_STATE_AWAITING:
            self._transition_to_checked_in(source="automatic")

    @callback
    def _async_auto_checkout_callback(self, _now: datetime) -> None:
        """Timer callback for automatic check-out at event end time.

        Args:
            _now: The current time when the callback fires.
        """
        _LOGGER.debug("Auto check-out timer fired for %s", self.name)
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
        _LOGGER.debug("Linger-to-awaiting timer fired for %s", self.name)
        self._unsub_timer = None
        if self._state == CHECKIN_STATE_CHECKED_OUT:
            # Pick the next event, skipping the one we checked out from
            event = self._get_relevant_event()
            if (
                event is not None
                and self._checked_out_event_key is not None
                and self._event_key(event.summary, event.start)
                == self._checked_out_event_key
            ):
                event = self._get_next_event()
            if event is not None:
                self._transition_to_awaiting(event)
            else:
                self._transition_to_no_reservation()

    @callback
    def _async_linger_to_no_reservation_callback(self, _now: datetime) -> None:
        """Timer callback for post-checkout linger expiry (FR-006b/c).

        Transitions from checked_out to no_reservation.

        Args:
            _now: The current time when the callback fires.
        """
        _LOGGER.debug("Linger-to-no-reservation timer fired for %s", self.name)
        self._unsub_timer = None
        if self._state == CHECKIN_STATE_CHECKED_OUT:
            self._transition_to_no_reservation()

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

            _LOGGER.debug("Restored state '%s' for %s", self._state, self.name)

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
                self.name,
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
                # Event has ended while we were down → auto checkout
                _LOGGER.debug(
                    "Stale restore: checked_in but event ended, "
                    "transitioning to checked_out"
                )
                self._transition_to_checked_out(source="automatic")
                # Use the event's end time as the effective checkout time
                # so the post-checkout linger window is anchored correctly
                # rather than to the restore time.
                self._checkout_time = self._tracked_event_end
                # Recompute linger timing anchored to the corrected
                # checkout time (replaces the stale timers that the
                # transition scheduled based on restore time).
                self._cancel_timer()
                self._compute_linger_timing()
            else:
                # Still valid checked_in — reschedule auto-checkout timer
                if self._tracked_event_end is not None:
                    self._cancel_timer()
                    self._schedule_auto_checkout(self._tracked_event_end)
                self.async_write_ha_state()

        elif current_state == CHECKIN_STATE_AWAITING:
            if (
                self._tracked_event_start is not None
                and self._tracked_event_start <= now
            ):
                # Event start passed while we were down → auto checkin
                _LOGGER.debug(
                    "Stale restore: awaiting_checkin but start passed, "
                    "transitioning to checked_in"
                )
                self._transition_to_checked_in(source="automatic")
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
                # and current coordinator data. _handle_coordinator_update
                # does not schedule linger transitions for checked_out.
                self._compute_linger_timing()
                self.async_write_ha_state()
            else:
                # Linger has not expired — reschedule the linger timer
                # so we will transition out of checked_out once the
                # window ends.
                self._compute_linger_timing()
                self.async_write_ha_state()

        elif current_state == CHECKIN_STATE_NO_RESERVATION:
            # Process current coordinator data
            if (
                self.coordinator.last_update_success
                and self.coordinator.data is not None
            ):
                self._handle_coordinator_update()
            else:
                self.async_write_ha_state()

        else:
            # Unknown or corrupted state — reset to no_reservation
            _LOGGER.warning(
                "Unknown restored state '%s', resetting to no_reservation",
                current_state,
            )
            self._state = CHECKIN_STATE_NO_RESERVATION
            if (
                self.coordinator.last_update_success
                and self.coordinator.data is not None
            ):
                self._handle_coordinator_update()
            else:
                self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Clean up when entity is being removed."""
        self._cancel_timer()
        await super().async_will_remove_from_hass()
