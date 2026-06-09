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
from .util import normalize_uid
from .util import trim_name

if TYPE_CHECKING:
    from homeassistant.components.calendar import CalendarEvent

_LOGGER = logging.getLogger(__name__)

SLOT_MISS_THRESHOLD = 2


def _to_utc(value: datetime) -> datetime:
    """Normalize a datetime to UTC for timezone-safe comparison.

    Aware datetimes are converted via ``dt.as_utc``.  Naive datetimes
    (missing ``tzinfo`` or returning ``None`` from ``utcoffset()``)
    are assumed to represent Home Assistant's configured local timezone
    before conversion.

    ``dt.as_utc`` is intentionally **not** used for naive values because
    it treats them as already-UTC, whereas values arriving here without
    timezone info (e.g. from ``dt.parse_datetime`` on a tz-less string)
    are more likely to be in the user's configured local timezone.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        local: datetime = dt.as_local(value)
        result: datetime = dt.as_utc(local)
        return result
    utc: datetime = dt.as_utc(value)
    return utc


def _strip_prefix(slot_name: str, prefix: str) -> str:
    """Remove a leading prefix and space from slot_name.

    Uses ``str.removeprefix`` for deterministic matching that is safe
    regardless of regex metacharacters in *prefix*.
    """
    candidate = prefix + " "
    if slot_name.startswith(candidate):
        return slot_name[len(candidate) :]
    return slot_name


def _is_trimmed_match(name_a: str, name_b: str, guest_max: int) -> bool:
    """Check if *name_a* and *name_b* are related by trim_name.

    Returns True when trimming the longer name to *guest_max*
    produces the shorter name, covering both word-boundary and
    hard-truncated single-word cases.
    """
    if name_a == name_b or guest_max <= 0:
        return False
    shorter, longer = sorted((name_a, name_b), key=len)
    return trim_name(longer, guest_max) == shorter


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
        self._slot_miss_counts: dict[int, int] = {}
        self._slot_uids: dict[int, str | None] = {}
        self._start_slot: int = start_slot
        self._trim_names: bool = False
        self._max_name_length: int = 0
        self._prefix_length: int = 0

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

    @property
    def trim_names(self) -> bool:
        """Return whether name trimming is enabled."""
        return self._trim_names

    @trim_names.setter
    def trim_names(self, value: bool) -> None:
        """Set whether name trimming is enabled."""
        self._trim_names = value

    @property
    def max_name_length(self) -> int:
        """Return the configured max name length."""
        return self._max_name_length

    @max_name_length.setter
    def max_name_length(self, value: int) -> None:
        """Set the configured max name length."""
        self._max_name_length = value

    @property
    def prefix_length(self) -> int:
        """Return the event prefix length including separator."""
        return self._prefix_length

    @prefix_length.setter
    def prefix_length(self, value: int) -> None:
        """Set the event prefix length including separator."""
        self._prefix_length = value

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
        """Find existing slot matching by UID or overlapping time range.

        Uses a three-phase search:

        Phase 1 — UID positive match.  When both the incoming event and
        a stored slot carry a non-None UID that is equal, the slot is
        returned immediately (provided the name also matches).  This
        ensures that a reservation whose dates shifted beyond the
        original overlap window is still recognised as the same booking.

        Phase 2 — Strict interval overlap with a UID-aware same-start
        bypass. ``start_a < end_b AND start_b < end_a``. If both UIDs
        are non-None and differ, different start times are rejected but
        same-start candidates are reconsidered as possible date-change
        updates. When multiple same-start candidates exist, the best
        matching slot is chosen and any exact UID owner still wins.

        Phase 3 — Trim-aware fallback for trimmed names.  After a
        Home Assistant restart the override may contain a trimmed
        display name read back from Keymaster.  If the incoming
        *slot_name* is the trimmed form of the stored name (or
        vice-versa) the slot is returned.  Uses the actual
        ``trim_name`` function so both word-boundary and
        hard-truncated single-word cases are handled correctly.
        Phase 3a checks UID-positive matches without requiring
        time overlap (mirroring Phase 1).  Phase 3b checks
        overlap-based matches (mirroring Phase 2).

        When *exclude_slot* is set that slot number is skipped entirely,
        allowing ``async_update`` to avoid matching the slot it is about
        to write to.
        """
        uid = normalize_uid(uid)

        # Phase 1: UID positive match
        if uid is not None:
            for slot in self.__get_slots_with_values():
                if slot == exclude_slot:
                    continue
                stored_uid = normalize_uid(self._slot_uids.get(slot))
                if stored_uid is not None and stored_uid == uid:
                    override = self._overrides[slot]
                    if override is not None and override["slot_name"] == slot_name:
                        return slot

        # Phase 2: name + strict interval overlap with UID-aware bypass
        start_utc = _to_utc(start_time)
        end_utc = _to_utc(end_time)
        preferred_same_start_slot: int | None = None
        if uid is not None:
            preferred_same_start_slot = self._get_same_start_uid_bypass_slot(
                EventIdentity(slot_name, start_time, end_time, uid),
                exclude_slot=exclude_slot,
            )
        for slot in self.__get_slots_with_values():
            if slot == exclude_slot:
                continue
            override = self._overrides[slot]
            if override is None:
                continue
            if override["slot_name"] != slot_name:
                continue
            if not (
                start_utc < _to_utc(override["end_time"])
                and _to_utc(override["start_time"]) < end_utc
            ):
                continue
            stored_uid = normalize_uid(self._slot_uids.get(slot))
            if uid is not None:
                if stored_uid is None:
                    if self._slot_has_other_uid_owner(
                        slot_name, uid, exclude_slot=slot
                    ):
                        continue
                    if (
                        preferred_same_start_slot is not None
                        and preferred_same_start_slot != slot
                    ):
                        continue
                elif uid != stored_uid:
                    # Same-start bypass: if start times match, this is likely
                    # the same reservation with a regenerated UID (date
                    # extension/shortening) rather than a different booking.
                    if _to_utc(start_time) != _to_utc(override["start_time"]):
                        continue
                    if self._slot_has_other_uid_owner(
                        slot_name, uid, exclude_slot=slot
                    ):
                        continue
                    if preferred_same_start_slot != slot:
                        continue
            return slot

        # Phase 3: trim-aware fallback for trimmed names.
        # Only active when name trimming is enabled.  Uses the actual
        # trim_name function so both word-boundary and hard-truncated
        # single-word cases are handled correctly.
        if self._trim_names:
            guest_max = self._max_name_length - self._prefix_length

            # Phase 3a: UID-positive trim match (no overlap required),
            # mirrors Phase 1 but tolerates trimmed names.
            if uid is not None:
                for slot in self.__get_slots_with_values():
                    if slot == exclude_slot:
                        continue
                    stored_uid = normalize_uid(self._slot_uids.get(slot))
                    if stored_uid is None or stored_uid != uid:
                        continue
                    override = self._overrides[slot]
                    if override is None:
                        continue
                    stored = override["slot_name"]
                    if stored == slot_name:
                        continue  # already matched in Phase 1
                    if not _is_trimmed_match(stored, slot_name, guest_max):
                        continue
                    if len(slot_name) > len(stored):
                        override["slot_name"] = slot_name
                    return slot

            # Phase 3b: trim match with overlap (no UID required).
            for slot in self.__get_slots_with_values():
                if slot == exclude_slot:
                    continue
                override = self._overrides[slot]
                if override is None:
                    continue
                stored = override["slot_name"]
                if stored == slot_name:
                    continue  # already checked in Phase 2
                if not _is_trimmed_match(stored, slot_name, guest_max):
                    continue
                if not (
                    start_utc < _to_utc(override["end_time"])
                    and _to_utc(override["start_time"]) < end_utc
                ):
                    continue
                stored_uid = normalize_uid(self._slot_uids.get(slot))
                if uid is not None:
                    if stored_uid is None:
                        if self._slot_has_other_uid_owner(
                            slot_name, uid, exclude_slot=slot
                        ):
                            continue
                        if (
                            preferred_same_start_slot is not None
                            and preferred_same_start_slot != slot
                        ):
                            continue
                    elif uid != stored_uid:
                        if _to_utc(start_time) != _to_utc(override["start_time"]):
                            continue
                        if self._slot_has_other_uid_owner(
                            slot_name, uid, exclude_slot=slot
                        ):
                            continue
                        if preferred_same_start_slot != slot:
                            continue
                # Update stored name to the full untrimmed version
                if len(slot_name) > len(stored):
                    override["slot_name"] = slot_name
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
                self._slot_miss_counts.pop(existing, None)
                if uid is not None:
                    self._slot_uids[existing] = normalize_uid(uid)
                override = self._overrides[existing]
                start_utc = _to_utc(start_time)
                end_utc = _to_utc(end_time)
                if override is not None and (
                    _to_utc(override["start_time"]) != start_utc
                    or _to_utc(override["end_time"]) != end_utc
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
                self._slot_miss_counts.pop(new_slot, None)
                if uid is not None:
                    self._slot_uids[new_slot] = normalize_uid(uid)
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
                self._slot_miss_counts.pop(slot, None)
            else:
                self._overrides[slot] = None
                self._slot_uids.pop(slot, None)
                self._slot_miss_counts.pop(slot, None)

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

        Uses a three-phase search mirroring ``_find_overlapping_slot``:

        Phase 1 — UID positive match.  If the stored slot UID equals an
        event UID and the names match, the slot is considered matched
        regardless of time overlap.

        Phase 2 — name + strict interval overlap with the same-start
        UID bypass and preferred-slot tie-breaking.

        Phase 3 — trim-aware fallback for trimmed names with time
        overlap, restoring the full name on match. The same-start UID
        bypass and preferred-slot tie-breaking apply here too.
        """
        override = self._overrides[slot]
        if override is None:
            return False

        slot_name = override["slot_name"]
        slot_start = override["start_time"]
        slot_end = override["end_time"]
        stored_uid = normalize_uid(self._slot_uids.get(slot))

        # Phase 1: UID positive match
        if stored_uid is not None:
            for ev in events:
                ev_uid = normalize_uid(ev.uid)
                if ev_uid is not None and ev_uid == stored_uid and ev.name == slot_name:
                    return True

        # Phase 2: name + strict interval overlap with UID-aware bypass
        slot_start_utc = _to_utc(slot_start)
        slot_end_utc = _to_utc(slot_end)
        for ev in events:
            if ev.name != slot_name:
                continue
            if not (
                slot_start_utc < _to_utc(ev.end) and _to_utc(ev.start) < slot_end_utc
            ):
                continue
            ev_uid = normalize_uid(ev.uid)
            if ev_uid is not None:
                if stored_uid is None:
                    if self._event_has_other_uid_owner(ev, exclude_slot=slot):
                        continue
                    preferred_slot = self._get_same_start_uid_bypass_slot(ev)
                    if preferred_slot is not None and preferred_slot != slot:
                        continue
                elif stored_uid != ev_uid:
                    # Same-start bypass: UID regenerated on date change.
                    if _to_utc(ev.start) != _to_utc(slot_start):
                        continue
                    if self._event_has_other_uid_owner(ev, exclude_slot=slot):
                        continue
                    preferred_slot = self._get_same_start_uid_bypass_slot(ev)
                    if preferred_slot != slot:
                        continue
            return True

        # Phase 3: trim-aware fallback for trimmed names.
        # Only active when name trimming is enabled.
        if self._trim_names:
            guest_max = self._max_name_length - self._prefix_length

            # Phase 3a: UID-positive trim match (no overlap required),
            # mirrors Phase 1 but tolerates trimmed names.
            if stored_uid is not None:
                for ev in events:
                    ev_uid = normalize_uid(ev.uid)
                    if ev_uid is None or ev_uid != stored_uid:
                        continue
                    if ev.name == slot_name:
                        continue  # already matched in Phase 1
                    if not _is_trimmed_match(slot_name, ev.name, guest_max):
                        continue
                    if len(ev.name) > len(slot_name):
                        override["slot_name"] = ev.name
                    return True

            # Phase 3b: trim match with overlap (no UID required).
            for ev in events:
                if ev.name == slot_name:
                    continue
                if not _is_trimmed_match(slot_name, ev.name, guest_max):
                    continue
                if not (
                    slot_start_utc < _to_utc(ev.end)
                    and _to_utc(ev.start) < slot_end_utc
                ):
                    continue
                ev_uid = normalize_uid(ev.uid)
                if ev_uid is not None:
                    if stored_uid is None:
                        if self._event_has_other_uid_owner(ev, exclude_slot=slot):
                            continue
                        preferred_slot = self._get_same_start_uid_bypass_slot(ev)
                        if preferred_slot is not None and preferred_slot != slot:
                            continue
                    elif stored_uid != ev_uid:
                        if _to_utc(ev.start) != _to_utc(slot_start):
                            continue
                        if self._event_has_other_uid_owner(ev, exclude_slot=slot):
                            continue
                        preferred_slot = self._get_same_start_uid_bypass_slot(ev)
                        if preferred_slot != slot:
                            continue
                if len(ev.name) > len(slot_name):
                    override["slot_name"] = ev.name
                return True

        return False

    def _event_has_other_uid_owner(
        self,
        event: EventIdentity,
        exclude_slot: int,
    ) -> bool:
        """Return whether another slot claims *event* via an exact UID match."""
        return self._slot_has_other_uid_owner(
            event.name,
            event.uid,
            exclude_slot=exclude_slot,
        )

    def _slot_has_other_uid_owner(
        self,
        slot_name: str,
        uid: str | None,
        exclude_slot: int | None = None,
    ) -> bool:
        """Return whether another slot already owns *uid* for *slot_name*."""
        uid = normalize_uid(uid)
        if uid is None:
            return False

        guest_max = self._max_name_length - self._prefix_length
        for candidate in self.__get_slots_with_values():
            if candidate == exclude_slot:
                continue
            override = self._overrides[candidate]
            if override is None:
                continue

            stored_uid = normalize_uid(self._slot_uids.get(candidate))
            if stored_uid != uid:
                continue

            stored_name = override["slot_name"]
            if stored_name == slot_name:
                return True
            if self._trim_names and _is_trimmed_match(
                stored_name, slot_name, guest_max
            ):
                return True

        return False

    def _get_same_start_uid_bypass_slot(
        self,
        event: EventIdentity,
        exclude_slot: int | None = None,
    ) -> int | None:
        """Return the preferred same-start fallback slot for *event*."""
        event_start_utc = _to_utc(event.start)
        event_end_utc = _to_utc(event.end)
        guest_max = self._max_name_length - self._prefix_length

        best_slot: int | None = None
        best_distance: float | None = None
        best_exact = False

        for candidate in self.__get_slots_with_values():
            if candidate == exclude_slot:
                continue
            override = self._overrides[candidate]
            if override is None:
                continue

            stored_name = override["slot_name"]
            exact_name = stored_name == event.name
            if not exact_name:
                if not self._trim_names or not _is_trimmed_match(
                    stored_name, event.name, guest_max
                ):
                    continue

            if not (
                _to_utc(override["start_time"]) < event_end_utc
                and event_start_utc < _to_utc(override["end_time"])
            ):
                continue

            if _to_utc(override["start_time"]) != event_start_utc:
                continue

            distance = abs(
                (_to_utc(override["end_time"]) - event_end_utc).total_seconds()
            )
            if (
                best_slot is None
                or best_distance is None
                or distance < best_distance
                or (
                    distance == best_distance
                    and (
                        (exact_name and not best_exact)
                        or (exact_name == best_exact and candidate < best_slot)
                    )
                )
            ):
                best_slot = candidate
                best_distance = distance
                best_exact = exact_name

        return best_slot

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
                start_date = self.get_slot_start_date(slot)

                if not self._slot_has_matching_event(slot, event_ids):
                    if start_date >= cur_date_start:
                        count = self._slot_miss_counts.get(slot, 0) + 1
                        self._slot_miss_counts[slot] = count
                        _LOGGER.debug(
                            "Slot %d miss count: %d/%d for %s",
                            slot,
                            count,
                            SLOT_MISS_THRESHOLD,
                            self.get_slot_name(slot),
                        )
                        if count >= SLOT_MISS_THRESHOLD:
                            _LOGGER.debug(
                                "%s not in current events after %d consecutive misses, "
                                "clearing",
                                self._overrides[slot],
                                count,
                            )
                            clear_code = True
                    else:
                        _LOGGER.debug(
                            "%s not in current events, clearing",
                            self._overrides[slot],
                        )
                        clear_code = True
                else:
                    self._slot_miss_counts.pop(slot, None)

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
                            "freeing slot so new events can be assigned. "
                            "The stale code may be overwritten when a "
                            "future event is programmed into this slot.",
                            slot,
                        )

                    self._overrides[slot] = None
                    self._slot_uids.pop(slot, None)
                    self._slot_miss_counts.pop(slot, None)
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

        self._slot_miss_counts.pop(slot, None)
        self._overrides = overrides
        self.__assign_next_slot()
        if len(overrides) == self.max_slots:
            self._ready = True

        _LOGGER.debug("overrides = %s", self.overrides)
        _LOGGER.debug("ready = %s", self.ready)
        _LOGGER.debug("next_slot = %s", self.next_slot)
