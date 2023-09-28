# SPDX-License-Identifier: Apache-2.0
##############################################################################
# COPYRIGHT 2023 Andrew Grimberg
#
# All rights reserved. This program and the accompanying materials
# are made available under the terms of the Apache 2.0 License
# which accompanies this distribution, and is available at
# https://www.apache.org/licenses/LICENSE-2.0
#
# Contributors:
#   Andrew Grimberg - Initial implementation
##############################################################################
"""Rental Control EventOVerrides."""
import asyncio
import logging
import re
from datetime import datetime
from typing import Dict
from typing import List
from typing import TypedDict

from homeassistant.util import dt

from .util import async_fire_clear_code
from .util import get_event_names


_LOGGER = logging.getLogger(__name__)


class EventOverride(TypedDict):
    """Event override definition."""

    slot_name: str
    slot_code: str
    start_time: datetime
    end_time: datetime


class EventOverrides:
    """Event Overrides object and methods."""

    def __init__(self, start_slot: int, max_slots: int) -> None:
        """Setup the overrides object."""

        self._max_slots: int = max_slots
        self._next_slot: int | None = None
        self._overrides: Dict[int, EventOverride | None] = {}
        self._ready: bool = False
        self._start_slot: int = start_slot

    @property
    def max_slots(self) -> int:
        """Return the max_slots known."""
        return self._max_slots

    @property
    def next_slot(self) -> int | None:
        """Return the next_slot available."""
        return self._next_slot

    @property
    def overrides(self) -> Dict[int, EventOverride | None]:
        """Return the overrides."""
        return self._overrides

    @property
    def ready(self) -> bool:
        """Return if the overrides are ready."""
        return self._ready

    @property
    def start_slot(self) -> int:
        """Return the start_slot."""
        return self._start_slot

    def __assign_next_slot(self) -> None:
        """Assign the next slot."""

        _LOGGER.info("In EventOverrides.assign_next_slot")

        if len(self._overrides) != self.max_slots:
            _LOGGER.info("System starting up")
            return

        slots_with_values = self.__get_slots_with_values()
        if len(slots_with_values) == self.max_slots:
            _LOGGER.info("Overrides at max")
            self._next_slot = None
            return

        if len(slots_with_values):
            max_slot = slots_with_values[-1]
        else:
            max_slot = self.start_slot - 1

        # Get all the available slots greater than our current max
        avail_slots = self.__get_slots_without_values(max_slot)
        if len(avail_slots):
            _LOGGER.info(f"Next slot is {avail_slots[0]}")
            self._next_slot = avail_slots[0]
            return

        # Slots greater than our current max don't work, so find the first free
        # slot
        avail_slots = self.__get_slots_without_values()

        if len(avail_slots):
            _LOGGER.info(f"Next slot is {avail_slots[0]}")
            self._next_slot = avail_slots[0]
            return

        # We should never hit this directly, but if we do, set our next to None
        self._next_slot = None

    def __get_slots_with_values(self) -> List[int]:
        """Get a sorted list of the keys that have values."""
        return sorted(
            k for k in self._overrides.keys() if self._overrides[k] is not None
        )

    def __get_slots_without_values(self, max_slot: int = 0) -> List[int]:
        """
        Get the sorted list of the keys that have no value greater than
        max_slot.
        """
        return sorted(
            k
            for k in self._overrides.keys()
            if self._overrides[k] is None and k > max_slot
        )

    async def async_check_overrides(self, coordinator) -> None:
        """Check if overrides need to have a clear_code event fired."""

        _LOGGER.info("In EventOverrides.async_check_overrides")

        calendar = coordinator.calendar

        if not coordinator.calendar_loaded or not coordinator.events_ready:
            _LOGGER.info(
                "Calendar or events not loaded, not checking override validity"
            )
            return

        event_names = get_event_names(coordinator)
        _LOGGER.info(f"event_names = {event_names}")

        assigned_slots = self.__get_slots_with_values()

        if not len(assigned_slots):
            _LOGGER.info("No overrides to check")
            return

        cur_date_start = dt.start_of_local_day().date()

        for slot in assigned_slots:
            clear_code = False

            if self.get_slot_name(slot) not in event_names:
                _LOGGER.info(f"{self._overrides[slot]} not in current events, clearing")
                clear_code = True

            start_time = self.get_slot_start_time(slot).date()
            end_time = self.get_slot_end_time(slot).date()

            if not len(calendar):
                _LOGGER.info(f"No events in calendar, clearing {slot}")
                clear_code = True

            if not clear_code and start_time > end_time:
                _LOGGER.info(f"{slot} start and end times do not make sense, clearing")
                clear_code = True

            if not clear_code and end_time < cur_date_start:
                _LOGGER.info(f"{slot} end is before today, clearing")
                clear_code = True

            if not clear_code:
                if coordinator.max_events <= len(calendar):
                    last_end = calendar[coordinator.max_events - 1].end.date()
                else:
                    last_end = calendar[-1].end.date()

                if start_time > last_end:
                    _LOGGER.info(f"{slot} start is after last event ends, clearing")
                    clear_code = True

            if clear_code:
                _LOGGER.info(f"Firing clear code for slot {slot}")
                await async_fire_clear_code(coordinator, slot)

                # signal an update to all the event sensors
                await asyncio.gather(
                    *[event.async_update() for event in coordinator.event_sensors]
                )

    def get_slot_name(self, slot: int) -> str:
        """Return the slot name."""
        override = self._overrides[slot]

        if override and "slot_name" in override:
            return override["slot_name"]
        else:
            return ""

    def get_slot_with_name(self, slot_name: str) -> EventOverride | None:
        """
        Find the override that has slot_name and return the data if
        available.
        """

        slots_with_values = self.__get_slots_with_values()
        for slot in slots_with_values:
            override = self.overrides[slot]
            if override and override["slot_name"] == slot_name:
                return override

        return None

    def get_slot_key_by_name(self, slot_name: str) -> int:
        """
        Find the override that has slot_name and return the data if
        available.

        Returns 0 if no slot with name is found
        """

        slots_with_values = self.__get_slots_with_values()
        for slot in slots_with_values:
            override = self.overrides[slot]
            if override and override["slot_name"] == slot_name:
                return slot

        return 0

    def get_slot_start_time(self, slot: int) -> datetime:
        """Return the start datetime of slot or the start of day if no override."""

        override = self._overrides[slot]

        if override:
            if "start_time" in override:
                return override["start_time"]
        else:
            # because HA doesn't ship type hints we have to ignore this
            # particular type validation
            return dt.start_of_local_day()  # type: ignore

    def get_slot_end_time(self, slot: int) -> datetime:
        """Return the end datetime of slot or the start of day if no override."""

        override = self._overrides[slot]

        if override:
            if "end_time" in override:
                return override["end_time"]
        else:
            # because HA doesn't ship type hints we have to ignore this
            # particular type validation
            return dt.start_of_local_day()  # type: ignore

    def update(
        self,
        slot: int,
        slot_code: str,
        slot_name: str,
        start_time: datetime,
        end_time: datetime,
        prefix: str,
    ) -> None:
        """Update overrides."""

        _LOGGER.info("In EventOverrides.update")

        overrides = self._overrides.copy()

        if slot_name:
            regex = r"^(" + prefix + " )?(.*)$"
            matches = re.findall(regex, slot_name)
            overrides[slot] = {
                "slot_name": matches[0][1],
                "slot_code": slot_code,
                "start_time": start_time,
                "end_time": end_time,
            }
        else:
            overrides[slot] = None

        self._overrides = overrides
        self.__assign_next_slot()
        if len(overrides) == self.max_slots:
            self._ready = True

        _LOGGER.info(f"overrides = {self.overrides}")
        _LOGGER.info(f"ready = {self.ready}")
        _LOGGER.info(f"next_slot = {self.next_slot}")
