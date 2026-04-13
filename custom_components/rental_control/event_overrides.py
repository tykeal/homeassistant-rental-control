# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2021 Andrew Grimberg <tykeal@bardicgrove.org>
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
"""Rental Control EventOverrides."""

from __future__ import annotations

import asyncio
from datetime import date
from datetime import datetime
from datetime import time
import logging
from typing import TYPE_CHECKING
from typing import NamedTuple
from typing import TypedDict

from homeassistant.util import dt

from .const import DEFAULT_MAX_RETRY_CYCLES
from .util import EventIdentity
from .util import async_fire_clear_code
from .util import get_event_identities

if TYPE_CHECKING:
    from homeassistant.components.calendar import CalendarEvent

_LOGGER = logging.getLogger(__name__)


def _strip_prefix(slot_name: str, prefix: str) -> str:
    """Remove a leading prefix and space from slot_name.

    Uses ``str.removeprefix`` for deterministic matching that is safe
    regardless of regex metacharacters in *prefix*.
    """
    candidate = prefix + " "
    if slot_name.startswith(candidate):
        return slot_name[len(candidate) :]
    return slot_name


class ReserveResult(NamedTuple):
    """Result of a slot reservation attempt."""

    slot: int | None
    is_new: bool
    times_updated: bool


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

        self._escalated: dict[int, bool] = {}
        self._lock: asyncio.Lock = asyncio.Lock()
        self._max_slots: int = max_slots
        self._next_slot: int | None = None
        self._overrides: dict[int, EventOverride | None] = {}
        self._ready: bool = False
        self._retry_counts: dict[int, int] = {}
        self._slot_uids: dict[int, str | None] = {}
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
    def overrides(self) -> dict[int, EventOverride | None]:
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

        _LOGGER.debug("In EventOverrides.assign_next_slot")

        if len(self._overrides) != self.max_slots:
            _LOGGER.debug("System starting up")
            return

        slots_with_values = self.__get_slots_with_values()
        if len(slots_with_values) == self.max_slots:
            _LOGGER.debug("Overrides at max")
            self._next_slot = None
            return

        if len(slots_with_values):
            max_slot = slots_with_values[-1]
        else:
            max_slot = self.start_slot - 1

        # Get all the available slots greater than our current max
        avail_slots = self.__get_slots_without_values(max_slot)
        if len(avail_slots):
            _LOGGER.debug("Next slot is %s", avail_slots[0])
            self._next_slot = avail_slots[0]
            return

        # Slots greater than our current max don't work, so find the first free
        # slot
        avail_slots = self.__get_slots_without_values()

        if len(avail_slots):
            _LOGGER.debug("Next slot is %s", avail_slots[0])
            self._next_slot = avail_slots[0]
            return

        # We should never hit this directly, but if we do, set our next to None
        self._next_slot = None

    def __get_slots_with_values(self) -> list[int]:
        """Get a sorted list of the keys that have values."""
        return sorted(
            k for k in self._overrides.keys() if self._overrides[k] is not None
        )

    def __get_slots_without_values(self, max_slot: int = 0) -> list[int]:
        """
        Get the sorted list of the keys that have no value greater than
        max_slot.
        """
        return sorted(
            k
            for k in self._overrides.keys()
            if self._overrides[k] is None and k > max_slot
        )

    def _find_overlapping_slot(
        self,
        slot_name: str,
        start_time: datetime,
        end_time: datetime,
        uid: str | None = None,
        exclude_slot: int | None = None,
    ) -> int | None:
        """Find existing slot with same name and overlapping time range.

        Uses strict interval overlap: start_a < end_b AND start_b < end_a.
        If both incoming uid and stored uid are non-None and differ,
        the slot is skipped (distinct reservations with same name).

        When *exclude_slot* is set that slot number is skipped entirely,
        allowing ``async_update`` to avoid matching the slot it is about
        to write to.
        """
        for slot in self.__get_slots_with_values():
            if slot == exclude_slot:
                continue
            override = self._overrides[slot]
            if override is None:
                continue
            if override["slot_name"] != slot_name:
                continue
            if not (
                start_time < override["end_time"] and override["start_time"] < end_time
            ):
                continue
            stored_uid = self._slot_uids.get(slot)
            if uid is not None and stored_uid is not None and uid != stored_uid:
                continue
            return slot
        return None

    async def async_reserve_or_get_slot(
        self,
        slot_name: str,
        slot_code: str,
        start_time: datetime,
        end_time: datetime,
        uid: str | None = None,
        prefix: str | None = None,
    ) -> ReserveResult:
        """Atomically find existing slot or reserve next available.

        All work is performed under ``_lock`` so concurrent callers
        are serialised.
        """
        async with self._lock:
            if prefix is None:
                prefix = ""
            if slot_name and prefix:
                slot_name = _strip_prefix(slot_name, prefix)

            existing = self._find_overlapping_slot(slot_name, start_time, end_time, uid)
            if existing is not None:
                if uid is not None:
                    self._slot_uids[existing] = uid
                override = self._overrides[existing]
                if override is not None and (
                    override["start_time"] != start_time
                    or override["end_time"] != end_time
                ):
                    override["start_time"] = start_time
                    override["end_time"] = end_time
                    return ReserveResult(existing, False, True)
                return ReserveResult(existing, False, False)

            if self._next_slot is not None:
                new_slot = self._next_slot
                new_override: EventOverride = {
                    "slot_name": slot_name,
                    "slot_code": slot_code,
                    "start_time": start_time,
                    "end_time": end_time,
                }
                self._overrides[new_slot] = new_override
                if uid is not None:
                    self._slot_uids[new_slot] = uid
                self.__assign_next_slot()
                return ReserveResult(new_slot, True, False)

            _LOGGER.warning(
                "All %d override slots are occupied; "
                "reservation '%s' could not be assigned a slot",
                self._max_slots,
                slot_name,
            )
            return ReserveResult(None, False, False)

    async def async_update(
        self,
        slot: int,
        slot_code: str,
        slot_name: str,
        start_time: datetime,
        end_time: datetime,
        prefix: str | None = None,
    ) -> None:
        """Update slot with dedup enforcement (FR-004).

        All work is performed under ``_lock`` so concurrent callers
        are serialised.
        """
        async with self._lock:
            if prefix is None:
                prefix = ""
            if slot_name:
                if prefix:
                    slot_name = _strip_prefix(slot_name, prefix)

                dup = self._find_overlapping_slot(
                    slot_name,
                    start_time,
                    end_time,
                    exclude_slot=slot,
                )
                if dup is not None:
                    _LOGGER.warning(
                        "Duplicate slot_name '%s' detected in slot %d "
                        "while writing slot %d; redirecting write",
                        slot_name,
                        dup,
                        slot,
                    )
                    slot = dup

                override: EventOverride = {
                    "slot_name": slot_name,
                    "slot_code": slot_code,
                    "start_time": start_time,
                    "end_time": end_time,
                }
                self._overrides[slot] = override
            else:
                self._overrides[slot] = None
                self._slot_uids.pop(slot, None)

            self.__assign_next_slot()
            if len(self._overrides) == self.max_slots:
                self._ready = True

    def verify_slot_ownership(self, slot: int, expected_name: str) -> bool:
        """Check if slot is still assigned to expected_name.

        Read-only check — does not acquire the lock.
        """
        override = self._overrides.get(slot)
        return override is not None and override["slot_name"] == expected_name

    def record_retry_failure(self, slot: int) -> bool:
        """Record failed lock command.

        Returns True if escalation threshold reached.
        """
        count = self._retry_counts.get(slot, 0) + 1
        self._retry_counts[slot] = count
        if count >= DEFAULT_MAX_RETRY_CYCLES and not self._escalated.get(slot, False):
            self._escalated[slot] = True
            return True
        return False

    def record_retry_success(self, slot: int) -> None:
        """Reset failure tracking for slot."""
        self._retry_counts[slot] = 0
        self._escalated[slot] = False

    def _slot_has_matching_event(
        self,
        slot: int,
        events: list[EventIdentity],
    ) -> bool:
        """Check if an override slot matches any current calendar event.

        Uses name + strict interval overlap + UID tiebreaker, the same
        logic as ``_find_overlapping_slot`` but searching calendar
        events instead of other override slots.
        """
        override = self._overrides[slot]
        if override is None:
            return False

        slot_name = override["slot_name"]
        slot_start = override["start_time"]
        slot_end = override["end_time"]
        stored_uid = self._slot_uids.get(slot)

        for ev in events:
            if ev.name != slot_name:
                continue
            if not (slot_start < ev.end and ev.start < slot_end):
                continue
            if stored_uid is not None and ev.uid is not None and stored_uid != ev.uid:
                continue
            return True

        return False

    async def async_check_overrides(
        self,
        coordinator,
        calendar: list[CalendarEvent] | None = None,
    ) -> None:
        """Check if overrides need to have a clear_code event fired.

        When called from within _async_update_data, pass the fresh
        calendar list directly because coordinator.data has not been
        updated yet by the DUC framework.
        """
        _LOGGER.debug("In EventOverrides.async_check_overrides")

        cal = calendar if calendar is not None else coordinator.data
        if cal is None:
            _LOGGER.debug("Calendar data not available, not checking override validity")
            return

        _LOGGER.debug(self._overrides)
        # Only consider events within the sensor boundary so that
        # slots tied to events beyond max_events get cleared.
        sensor_cal = cal[: coordinator.max_events]
        event_ids = get_event_identities(coordinator, calendar=sensor_cal)
        _LOGGER.debug("event_identities = %s", event_ids)

        async with self._lock:
            assigned_slots = self.__get_slots_with_values()

            if not len(assigned_slots):
                _LOGGER.debug("No overrides to check")
                return

            cur_date_start = dt.start_of_local_day().date()

            for slot in assigned_slots:
                clear_code = False

                if not self._slot_has_matching_event(slot, event_ids):
                    _LOGGER.debug(
                        "%s not in current events, clearing",
                        self._overrides[slot],
                    )
                    clear_code = True

                start_date = self.get_slot_start_date(slot)
                end_date = self.get_slot_end_date(slot)

                if not len(cal):
                    _LOGGER.debug("No events in calendar, clearing %s", slot)
                    clear_code = True

                if not clear_code and start_date > end_date:
                    _LOGGER.debug(
                        "%s start and end times do not make sense, clearing",
                        slot,
                    )
                    clear_code = True

                if not clear_code and end_date < cur_date_start:
                    _LOGGER.debug("%s end is before today, clearing", slot)
                    clear_code = True

                if not clear_code:
                    if coordinator.max_events <= len(cal):
                        last_end = cal[coordinator.max_events - 1].end.date()
                    else:
                        last_end = cal[-1].end.date()

                    if start_date > last_end:
                        _LOGGER.debug(
                            "%s start is after last event ends, clearing",
                            slot,
                        )
                        clear_code = True

                if clear_code:
                    _LOGGER.debug("Firing clear code for slot %s", slot)
                    try:
                        await async_fire_clear_code(
                            coordinator, slot, expected_name=self.get_slot_name(slot)
                        )
                    except Exception:
                        _LOGGER.exception(
                            "Failed to fire clear code for slot %d; "
                            "keeping slot occupied",
                            slot,
                        )
                        continue

                    self._overrides[slot] = None
                    self._slot_uids.pop(slot, None)
                    self.__assign_next_slot()

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

    def get_slot_start_date(self, slot: int) -> date:
        """Return the start date of slot or today if no override."""

        override = self._overrides[slot]
        date_return: date = dt.start_of_local_day().date()

        if override:
            if "start_time" in override:
                date_return = override["start_time"].date()
        return date_return

    def get_slot_start_time(self, slot: int) -> time:
        """Return the start time of slot or the start of day if no override."""

        override = self._overrides[slot]
        time_return: time = time()

        if override:
            if "start_time" in override:
                time_return = override["start_time"].time()
        return time_return

    def get_slot_end_date(self, slot: int) -> date:
        """Return the end date of slot or today if no override."""

        override = self._overrides[slot]
        date_return: date = dt.start_of_local_day().date()

        if override:
            if "end_time" in override:
                date_return = override["end_time"].date()
        return date_return

    def get_slot_end_time(self, slot: int) -> time:
        """Return the end time of slot or the start of day if no override."""

        override = self._overrides[slot]
        time_return: time = time()

        if override:
            if "end_time" in override:
                time_return = override["end_time"].time()
        return time_return

    def update(
        self,
        slot: int,
        slot_code: str,
        slot_name: str,
        start_time: datetime,
        end_time: datetime,
        prefix: str | None = None,
    ) -> None:
        """Synchronously update overrides for a slot.

        This method mutates internal state without acquiring
        ``_lock``.  It is safe during bootstrap (before any async
        listeners are registered) but **must** be replaced by
        ``async_update()`` in post-bootstrap code paths once
        callers are migrated (see Phase 3+).
        """

        _LOGGER.debug("In EventOverrides.update")

        overrides = self._overrides.copy()

        if prefix is None:
            prefix = ""

        if slot_name:
            if prefix:
                slot_name = _strip_prefix(slot_name, prefix)
            override: EventOverride = {
                "slot_name": slot_name,
                "slot_code": slot_code,
                "start_time": start_time,
                "end_time": end_time,
            }
            overrides[slot] = override
        else:
            overrides[slot] = None

        self._overrides = overrides
        self.__assign_next_slot()
        if len(overrides) == self.max_slots:
            self._ready = True

        _LOGGER.debug("overrides = %s", self.overrides)
        _LOGGER.debug("ready = %s", self.ready)
        _LOGGER.debug("next_slot = %s", self.next_slot)
