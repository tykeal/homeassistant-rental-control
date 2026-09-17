# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Coordinator shell mixins for behavior-preserving delegation."""

# mypy: disable-error-code="attr-defined, has-type, var-annotated, misc, no-redef"

from __future__ import annotations

import asyncio
from datetime import datetime
from datetime import timedelta
import importlib
import logging
from typing import Any
from typing import cast
import uuid

from homeassistant.components.calendar import CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt
from icalendar import Calendar
import x_wr_timezone

from ..const import REQUEST_TIMEOUT
from ..const import SLOT_STATUS_OCCUPIED
from ..const import STORE_SCHEMA_VERSION
from ..reconciliation import Reservation as _Reservation
from ..reconciliation.desired import select_eligible_reservations
from ..util import OperationResult
from . import calendar_parsing
from . import code_allocation
from . import slot_matching

_LOGGER = logging.getLogger(__name__)


def _coordinator_module() -> Any:
    """Return the public coordinator module for patched compatibility."""
    return importlib.import_module("custom_components.rental_control.coordinator")


class CoordinatorRefreshMixin:
    """Provide extracted coordinator shell behavior."""

    async def _async_get_events_impl(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Get list of upcoming events."""
        _LOGGER.debug("Running RentalControl async_get_events")
        events = []
        cal_data: list[CalendarEvent] | None = self.data
        if cal_data and len(cal_data) > 0:
            for event in cal_data:
                _LOGGER.debug(
                    "Checking if event %s has start %s and end %s "
                    "within in the limit: %s and %s",
                    event.summary,
                    event.start,
                    event.end,
                    start_date,
                    end_date,
                )

                if event.start < end_date and event.end > start_date:
                    _LOGGER.debug("... and it has")
                    events.append(event)
        return events

    async def _async_fetch_calendar(self) -> list[CalendarEvent]:
        """Fetch iCalendar data from URL and parse into events."""
        try:
            session = async_get_clientsession(self.hass, verify_ssl=self.verify_ssl)
            async with asyncio.timeout(REQUEST_TIMEOUT):
                response = await session.get(self.url)
                try:
                    if response.status != 200:
                        raise UpdateFailed(
                            f"Calendar fetch failed for {self._name}: "
                            f"HTTP {response.status} - {response.reason}"
                        )
                    text = await response.text()
                finally:
                    response.release()
        except TimeoutError as err:
            raise UpdateFailed(f"Calendar fetch timed out for {self._name}") from err
        except UpdateFailed:
            raise
        except Exception as err:
            raise UpdateFailed(
                f"Calendar fetch failed for {self._name}: {err}"
            ) from err

        try:
            # Some calendars are filled with NULL-bytes that break
            # parsing.  from_ical triggers blocking timezone file I/O
            # so run it in the executor.
            cleaned = text.replace("\x00", "")
            event_list = await self.hass.async_add_executor_job(
                Calendar.from_ical, cleaned
            )

            # Convert non-standard timezone definitions
            if "X-WR-TIMEZONE" in event_list:
                event_list = await self.hass.async_add_executor_job(
                    x_wr_timezone.to_standard, event_list
                )

            start_of_events = dt.start_of_local_day()
            end_of_events = dt.start_of_local_day() + timedelta(days=self.days)

            return await self._ical_parser(event_list, start_of_events, end_of_events)
        except Exception as err:
            raise UpdateFailed(
                f"Failed to parse calendar for {self._name}: {err}"
            ) from err

    async def _async_update_data(self) -> list[CalendarEvent]:
        """Fetch and parse calendar data."""
        _LOGGER.debug(
            "Running RentalControl _async_update_data for %s",
            self._name,
        )

        is_fresh_data = True

        try:
            new_calendar = await self._async_fetch_calendar()
        except UpdateFailed as err:
            if self.data is not None:
                _LOGGER.warning(
                    "Calendar fetch/parse failed for %s: %s; "
                    "using cached data (%d events)",
                    self._name,
                    err,
                    len(self.data),
                )
                new_calendar = list(self.data)
                is_fresh_data = False
            else:
                raise

        if is_fresh_data:
            # Miss tracking: preserve stale data when within tolerance
            previous: list[CalendarEvent] | None = self.data
            if (
                previous
                and len(previous) > 0
                and len(new_calendar) == 0
                and self.num_misses < self.max_misses
            ):
                self.num_misses += 1
                _LOGGER.warning(
                    "No events found in calendar %s, but %d in previous. Miss %d of %d",
                    self._name,
                    len(previous),
                    self.num_misses,
                    self.max_misses,
                )
                new_calendar = list(previous)
            else:
                _LOGGER.debug(
                    "Found %d events in calendar %s",
                    len(new_calendar),
                    self._name,
                )
                self.num_misses = 0

        # Find the next upcoming event (clear stale state first)
        self.event = None
        if len(new_calendar) > 0:
            for event in new_calendar:
                if event.end > dt.now():
                    _LOGGER.debug(
                        "Event %s is the first event with end in the future: %s",
                        event.summary,
                        event.end,
                    )
                    self.event = event
                    break

        if self.event_overrides:
            await self._run_reconciliation(new_calendar)
        else:
            await self._run_lockless_allocation(new_calendar)

        await self.async_save_slot_store()

        self._refresh_child_locks()

        return new_calendar

    def _refresh_child_locks(self) -> None:
        """Refresh child lock discovery for the current cycle."""
        if not self.lockname:
            return
        self._parent_entry_id = self._find_parent_entry_id()
        previous_children = self._child_locknames
        self._child_locknames = self._discover_child_locks()
        if self._child_locknames != previous_children:
            _LOGGER.info(
                "Child locknames updated for %s: %s",
                self.lockname,
                self._child_locknames or "(none)",
            )

    async def _run_reconciliation(self, new_calendar: list[CalendarEvent]) -> None:
        """Build a desired plan from the calendar and apply it to the lock."""
        event_overrides = self.event_overrides
        if event_overrides is None:
            return
        try:
            observed_slots = self._observe_managed_slots()
            reservations = self._prepare_reservations_for_adoption(
                new_calendar, observed_slots
            )
            self._apply_checkin_protection(reservations, observed_slots)
            await code_allocation.async_resolve_codes(
                self.hass,
                self._entry_id,
                self.lockname,
                self.code_length,
                observed_slots,
                reservations,
            )

            plan_id = str(uuid.uuid4())
            plan = _coordinator_module().compute_desired_plan(
                reservations=reservations,
                managed_slots=observed_slots,
                max_events=self.max_events,
                plan_id=plan_id,
                generated_at=dt.now(),
                entry_id=self._entry_id,
                lockname=self.lockname,
                start_slot=self.start_slot,
            )

            res_by_key: dict[str, _Reservation] = {
                r.identity_key: r for r in reservations
            }
            violations = plan.validate(res_by_key)
            for v in violations:
                _LOGGER.warning("Plan %s invariant violation: %s", plan_id, v)

            self._record_observed_slot_codes(plan, res_by_key, observed_slots)

            if self._must_defer_for_checkin_restore(reservations, observed_slots):
                _LOGGER.info(
                    "Deferring reconciliation for %s until check-in state is "
                    "available; same-name physical slot has missing date state",
                    self._name,
                )
                operation_results: list[OperationResult] = []
            else:
                operation_results = await event_overrides.async_apply_plan(
                    self, plan, res_by_key
                )
            self._sync_slot_store_from_plan(plan, res_by_key, operation_results)

            self._latest_plan = plan
            self._latest_res_by_key = res_by_key

            _LOGGER.debug(
                "Reconciliation for %s: plan=%s selected=%d overflow=%d actions=%d",
                self._name,
                plan_id,
                len(plan.selected),
                len(plan.overflow),
                len(plan.actions),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception(
                "Reconciliation failed for %s; skipping cycle", self._name
            )

    async def _run_lockless_allocation(self, new_calendar: list[CalendarEvent]) -> None:
        """Allocate codes for entries that do not manage Keymaster slots."""
        try:
            reservations = self._prepare_reservations_for_adoption(new_calendar, [])
            await code_allocation.async_resolve_codes(
                self.hass,
                self._entry_id,
                None,
                self.code_length,
                [],
                reservations,
            )
            res_by_key: dict[str, _Reservation] = {
                reservation.identity_key: reservation
                for reservation in select_eligible_reservations(reservations)
            }
            self._sync_lockless_slot_store(res_by_key)
            self._latest_res_by_key = res_by_key
            self._latest_plan = None
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception(
                "Lockless allocation failed for %s; skipping cycle", self._name
            )

    def _sync_lockless_slot_store(self, res_by_key: dict[str, _Reservation]) -> None:
        """Persist lockless reservation metadata and published-code state."""
        mappings: dict[str, Any] = self._slot_mappings.setdefault("mappings", {})
        current_keys = set(res_by_key)
        for stale_key in list(mappings):
            if stale_key not in current_keys:
                mappings.pop(stale_key, None)
        now_str = dt.now().isoformat()
        for reservation in res_by_key.values():
            mappings[reservation.identity_key] = {
                "slot": None,
                "status": SLOT_STATUS_OCCUPIED,
                "operation_id": None,
                "operation_kind": None,
                "identity": {
                    "identity_key": reservation.identity_key,
                    "summary": reservation.summary,
                    "slot_name": reservation.slot_name,
                    "start": reservation.start.isoformat(),
                    "end": reservation.end.isoformat(),
                    "uid_aliases": sorted(reservation.uid_aliases),
                    "booking_aliases": sorted(reservation.booking_aliases),
                },
                "missing_count": reservation.missing_count,
                "published_once": reservation.published_once
                or reservation.slot_code is not None,
                "pending_set_since": None,
                "pending_clear_since": None,
                "fingerprint_history": sorted(reservation.fingerprint_history),
                "updated_at": now_str,
                "last_observed_actual": {
                    "slot": None,
                    "classification": SLOT_STATUS_OCCUPIED,
                    "name_state": reservation.display_slot_name,
                    "has_code": reservation.slot_code is not None,
                    "start_state": reservation.buffered_start.isoformat(),
                    "end_state": reservation.buffered_end.isoformat(),
                    "use_date_range": None,
                    "enabled": None,
                },
            }
        self._slot_mappings.update(
            {
                "schema_version": STORE_SCHEMA_VERSION,
                "entry_id": self._entry_id,
                "lockname": None,
                "start_slot": self.start_slot,
                "max_slots": self.max_events,
                "updated_at": now_str,
            }
        )

    def _prepare_reservations_for_adoption(
        self,
        new_calendar: list[CalendarEvent],
        observed_slots: list[Any],
    ) -> list[_Reservation]:
        """Hydrate live and ghost reservations before allocator adoption."""
        self._merge_observed_slots_into_mappings(observed_slots)
        reservations = cast(
            "list[_Reservation]",
            self._build_reservations(new_calendar, observed_slots),
        )
        persisted: dict[str, Any] = self._slot_mappings.setdefault("mappings", {})
        observed_mapping_keys = {
            slot.persisted_identity_key
            for slot in observed_slots
            if slot.persisted_identity_key is not None
        }
        actual_slot_names = {
            slot.slot: slot.actual_name
            for slot in observed_slots
            if isinstance(slot.actual_name, str)
        }
        observed_mapping_keys = (
            slot_matching.remap_observed_mappings_to_physical_reservations(
                persisted,
                reservations,
                actual_slot_names,
                observed_mapping_keys,
            )
        )
        self._hydrate_reservations_from_mappings(reservations, persisted)
        prefix = f"{self.event_prefix} " if self.event_prefix else ""
        reservations.extend(
            self._build_ghost_reservations(
                {reservation.identity_key for reservation in reservations},
                persisted,
                prefix,
                observed_mapping_keys,
            )
        )
        return reservations

    @staticmethod
    def _hydrate_reservations_from_mappings(
        reservations: list[_Reservation], persisted: dict[str, Any]
    ) -> None:
        """Copy cache-only mapping continuity fields onto live reservations."""
        for reservation in reservations:
            mapping = persisted.get(reservation.identity_key)
            if not isinstance(mapping, dict):
                continue
            history = mapping.get("fingerprint_history", [])
            if isinstance(history, (list, set, tuple)):
                reservation.fingerprint_history.update(
                    item for item in history if isinstance(item, str)
                )
            reservation.missing_count = 0
            reservation.published_once = mapping.get("published_once") is True

    async def _ical_parser(
        self, calendar: Calendar, from_date: datetime, to_date: datetime
    ) -> list[CalendarEvent]:
        """Return a sorted list of events from a icalendar object."""
        return calendar_parsing.parse_calendar(
            calendar, from_date, to_date, self._calendar_parse_context()
        )
