# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2021 Andrew Grimberg <tykeal@bardicgrove.org>
##############################################################################
# COPYRIGHT 2025 Andrew Grimberg
#
# All rights reserved. This program and the accompanying materials
# are made available under the terms of the Apache 2.0 License
# which accompanies this distribution, and is available at
# https://www.apache.org/licenses/LICENSE-2.0
#
# Contributors:
#   Andrew Grimberg - Initial implementation
##############################################################################
"""Rental Control Coordinator."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Coroutine
from collections.abc import Mapping
from datetime import datetime
from datetime import time
from datetime import timedelta
import logging
from typing import Any
import uuid
from zoneinfo import ZoneInfo  # noreorder

import aiohttp
from homeassistant.components.button import DOMAIN as BUTTON
from homeassistant.components.calendar import CalendarEvent
from homeassistant.components.datetime import DOMAIN as DATETIME
from homeassistant.components.persistent_notification import async_create
from homeassistant.components.switch import DOMAIN as SWITCH
from homeassistant.components.text import DOMAIN as TEXT
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.const import CONF_URL
from homeassistant.const import CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt
from homeassistant.util import slugify
from icalendar import Calendar
import x_wr_timezone

from .const import CHECKIN_SENSOR
from .const import CHECKIN_STATE_CHECKED_IN
from .const import CHECKIN_STATE_CHECKED_OUT
from .const import CONF_CHECKIN
from .const import CONF_CHECKOUT
from .const import CONF_CODE_BUFFER_AFTER
from .const import CONF_CODE_BUFFER_BEFORE
from .const import CONF_CODE_GENERATION
from .const import CONF_CODE_LENGTH
from .const import CONF_CREATION_DATETIME
from .const import CONF_DAYS
from .const import CONF_EVENT_PREFIX
from .const import CONF_HONOR_EVENT_TIMES
from .const import CONF_IGNORE_NON_RESERVED
from .const import CONF_LOCK_ENTRY
from .const import CONF_MAX_EVENTS
from .const import CONF_MAX_NAME_LENGTH
from .const import CONF_REFRESH_FREQUENCY
from .const import CONF_SHOULD_UPDATE_CODE
from .const import CONF_START_SLOT
from .const import CONF_TIMEZONE
from .const import CONF_TRIM_NAMES
from .const import DEFAULT_CODE_BUFFER_AFTER
from .const import DEFAULT_CODE_BUFFER_BEFORE
from .const import DEFAULT_CODE_GENERATION
from .const import DEFAULT_CODE_LENGTH
from .const import DEFAULT_MAX_MISSES
from .const import DEFAULT_MAX_NAME_LENGTH
from .const import DEFAULT_REFRESH_FREQUENCY
from .const import DEFAULT_TRIM_NAMES
from .const import DOMAIN
from .const import EVENT_AGE_THRESHOLD_DAYS
from .const import LOCK_MANAGER
from .const import REQUEST_TIMEOUT
from .const import SLOT_STATUS_BLOCKED
from .const import SLOT_STATUS_OCCUPIED
from .const import SLOT_STATUS_PENDING_CLEAR
from .const import STORE_SCHEMA_VERSION
from .const import STORE_SLOT_MAPPINGS_KEY
from .const import VERSION
from .description_parser import extract_checkin_time
from .description_parser import extract_checkout_time
from .event_overrides import EventOverrides
from .reconciliation import DesiredPlan as _DesiredPlan
from .reconciliation import ManagedSlot as _ManagedSlot
from .reconciliation import Reservation as _Reservation
from .reconciliation import SlotStatus as _SlotStatus
from .reconciliation import compute_desired_plan
from .reconciliation import extract_booking_aliases
from .reconciliation import find_reservation_rematch
from .reconciliation import make_reservation_fingerprint
from .util import add_call
from .util import apply_buffer
from .util import async_fire_clear_code
from .util import check_gather_results
from .util import get_slot_name
from .util import normalize_uid
from .util import trim_name

# aislop-ignore-file ai-slop/hallucinated-import -- Provided by Home Assistant runtime.

_LOGGER = logging.getLogger(__name__)


class RentalControlCoordinator(DataUpdateCoordinator[list[CalendarEvent]]):
    """Coordinator for managing rental control calendar data."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry):
        """Set up a calendar coordinator."""
        config = config_entry.data
        self._name: str = str(config.get(CONF_NAME))
        self._unique_id: str = str(config_entry.unique_id)
        self._entry_id: str = config_entry.entry_id
        self.event_prefix: str | None = config.get(CONF_EVENT_PREFIX)
        self.url: str = str(config.get(CONF_URL))
        self.timezone: dt.dt.tzinfo = ZoneInfo(str(config.get(CONF_TIMEZONE)))
        self.refresh_frequency: int = config.get(
            CONF_REFRESH_FREQUENCY, DEFAULT_REFRESH_FREQUENCY
        )
        # our config flow guarantees that checkin and checkout are valid times
        # just use cv.time to get the parsed time object
        self.checkin: time = cv.time(config.get(CONF_CHECKIN))
        self.checkout: time = cv.time(config.get(CONF_CHECKOUT))
        self.start_slot: int = int(str(config.get(CONF_START_SLOT)))
        lockname_raw = config.get(CONF_LOCK_ENTRY)
        self.lockname: str | None = (
            slugify(lockname_raw) if lockname_raw and lockname_raw.strip() else None
        )
        self.max_events: int = int(str(config.get(CONF_MAX_EVENTS)))
        self.max_misses: int = DEFAULT_MAX_MISSES
        self.num_misses: int = 0
        self.days: int = int(str(config.get(CONF_DAYS)))
        self.ignore_non_reserved: bool = bool(config.get(CONF_IGNORE_NON_RESERVED))
        self.verify_ssl: bool = bool(config.get(CONF_VERIFY_SSL))
        self.event_overrides: EventOverrides | None = (
            EventOverrides(self.start_slot, self.max_events) if self.lockname else None
        )
        self.code_generator: str = config.get(
            CONF_CODE_GENERATION, DEFAULT_CODE_GENERATION
        )
        self.should_update_code: bool = bool(config.get(CONF_SHOULD_UPDATE_CODE))
        self.honor_event_times: bool = bool(config.get(CONF_HONOR_EVENT_TIMES))
        self.trim_names: bool = bool(config.get(CONF_TRIM_NAMES, DEFAULT_TRIM_NAMES))
        self.max_name_length: int = int(
            str(config.get(CONF_MAX_NAME_LENGTH, DEFAULT_MAX_NAME_LENGTH))
        )
        self.code_buffer_before: int = int(
            str(config.get(CONF_CODE_BUFFER_BEFORE, DEFAULT_CODE_BUFFER_BEFORE))
        )
        self.code_buffer_after: int = int(
            str(config.get(CONF_CODE_BUFFER_AFTER, DEFAULT_CODE_BUFFER_AFTER))
        )
        if self.event_overrides is not None:
            self.event_overrides.trim_names = self.trim_names
            self.event_overrides.max_name_length = self.max_name_length
            prefix = f"{self.event_prefix} " if self.event_prefix else ""
            self.event_overrides.prefix_length = len(prefix)
        self.code_length: int = config.get(CONF_CODE_LENGTH, DEFAULT_CODE_LENGTH)
        self.event: CalendarEvent | None = None
        self.created: str = config.get(CONF_CREATION_DATETIME, str(dt.now()))
        self._version: str = VERSION

        # Child lock discovery (spec 006)
        self._parent_entry_id: str | None = None
        self._child_locknames: set[str] = set()

        # Ring buffer of recent keymaster_lock_state_changed events seen
        # by the listener, populated only when the diagnostics option is
        # enabled. See spec for the entry shape and disposition values.
        self.keymaster_event_diagnostics: deque[dict[str, Any]] = deque(maxlen=10)

        # HA Store for persisted slot mappings (T017)
        self._store: Store | None = None
        self._slot_mappings: dict[str, Any] = {}

        # Reconciliation state (T022/T031/T033)
        self._latest_plan: _DesiredPlan | None = None
        self._latest_res_by_key: dict[str, _Reservation] = {}

        super().__init__(
            hass=hass,
            logger=_LOGGER,
            name=self._name,
            config_entry=config_entry,
            update_interval=timedelta(minutes=self.refresh_frequency),
        )

        # Discover parent entry after super().__init__ sets self.hass
        if self.lockname:
            self._parent_entry_id = self._find_parent_entry_id()
            if self._parent_entry_id is not None:
                self._child_locknames = self._discover_child_locks()

        device_registry = dr.async_get(hass)
        device_registry.async_get_or_create(
            config_entry_id=self._entry_id,
            identifiers={(DOMAIN, self.unique_id)},
            name=self.name,
            sw_version=self.version,
        )

        entity_registry = er.async_get(hass)
        if self.lockname:
            reset_entity = f"{BUTTON}.{self.lockname}_code_slot_{self.start_slot}_reset"
            has_reset = entity_registry.async_get(reset_entity)
            if has_reset is None:
                error_msg = """
The version of Keymaster is incompatible with this version of Rental Control.
Please update Keymaster to at least v0.1.0-b0
"""
                _LOGGER.error(error_msg)
                async_create(
                    hass,
                    error_msg,
                    title="Keymaster Incompatible Version",
                )

    def _find_parent_entry_id(self) -> str | None:
        """Find the keymaster config entry ID for the parent lock.

        Iterates over keymaster config entries and returns the
        ``entry_id`` whose slugified ``data["lockname"]`` matches
        ``self.lockname``.

        Returns:
            The parent keymaster entry_id, or None if not found.
        """
        for entry in self.hass.config_entries.async_entries(LOCK_MANAGER):
            raw = entry.data.get("lockname")
            if isinstance(raw, str) and raw.strip() and slugify(raw) == self.lockname:
                result: str = entry.entry_id
                return result
        return None

    def _discover_child_locks(self) -> set[str]:
        """Discover child lock locknames for the parent entry.

        Iterates keymaster config entries looking for entries whose
        ``data["parent_entry_id"]`` matches ``self._parent_entry_id``.
        Returns the set of child locknames (slugified).

        Returns:
            Set of child locknames (may be empty).
        """
        if self._parent_entry_id is None:
            return set()

        children: set[str] = set()
        for entry in self.hass.config_entries.async_entries(LOCK_MANAGER):
            if entry.data.get("parent_entry_id") == self._parent_entry_id:
                child_lockname = entry.data.get("lockname")
                if isinstance(child_lockname, str) and child_lockname.strip():
                    children.add(slugify(child_lockname))
        return children

    @property
    def monitored_locknames(self) -> frozenset[str]:
        """Return the set of all monitored locknames.

        Includes the parent lockname and all discovered child
        locknames.  Returns an empty frozenset when no lock is
        configured.

        Returns:
            Frozenset of monitored lockname strings.
        """
        if self.lockname is None:
            return frozenset()
        return frozenset({self.lockname} | self._child_locknames)

    @property
    def device_info(self) -> dr.DeviceInfo:
        """Return the device info block."""
        return {
            "identifiers": {(DOMAIN, self.unique_id)},
            "name": self.name,
            "sw_version": self.version,
        }

    @property
    def entry_id(self) -> str:
        """Return the config entry ID."""
        return self._entry_id

    @property
    def unique_id(self) -> str:
        """Return the unique id."""
        return self._unique_id

    @property
    def version(self) -> str:
        """Return the version."""
        return self._version

    @property
    def latest_plan(self) -> _DesiredPlan | None:
        """Return the most recently computed desired plan, or None."""
        return self._latest_plan

    @property
    def latest_overflow(self) -> dict[str, str]:
        """Return overflow dict from latest plan (identity_key → reason)."""
        if self._latest_plan is None:
            return {}
        return dict(self._latest_plan.overflow)

    @property
    def latest_reconciliation_diagnostics(self) -> dict[str, Any]:
        """Return a combined diagnostics snapshot from the latest plan.

        Merges the plan-level diagnostics (per-slot desired/actual/action/
        retry_count/last_error and per-reservation selected/overflow/aliases)
        with the :class:`~.event_overrides.EventOverrides` diagnostics snapshot
        (matched_slots, pending_corrections, pending_clear_slots,
        slot_retry_counts, last_slot_errors).

        Raw PIN / slot-code values are never included: ``slot_code``, ``pin``,
        and ``code`` keys are stripped before returning.

        Returns:
            Combined diagnostics dict; empty when no plan has been computed.
        """
        result: dict[str, Any] = {}
        if self._latest_plan is not None:
            result.update(self._latest_plan.diagnostics)
        if self.event_overrides is not None:
            result["event_overrides"] = self.event_overrides.diagnostics_snapshot
        # Safety: strip any raw code/PIN values that might have leaked
        result.pop("slot_code", None)
        result.pop("pin", None)
        result.pop("code", None)
        return result

    def get_slot_assignment(self, identity_key: str) -> int | None:
        """Return slot number assigned to identity_key in latest plan, or None."""
        if self._latest_plan is None:
            return None
        return self._latest_plan.selected.get(identity_key)

    def get_slot_code(self, identity_key: str) -> str | None:
        """Return slot_code for identity_key from latest reconciliation, or None.

        Looks up the reservation by *identity_key* in the most recent
        reconciliation result.  Returns ``None`` when reconciliation has
        not run yet, the reservation is not in the plan, or no code was
        generated for it.
        """
        res = self._latest_res_by_key.get(identity_key)
        return res.slot_code if res is not None else None

    def get_overflow_reason(self, identity_key: str) -> str | None:
        """Return overflow reason for identity_key in latest plan, or None."""
        if self._latest_plan is None:
            return None
        return self._latest_plan.overflow.get(identity_key)

    async def async_get_events(
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

    async def async_setup_keymaster_overrides(self) -> None:
        """Bootstrap Keymaster slot overrides on first load."""
        if not self.lockname:
            return

        for i in range(self.start_slot, self.start_slot + self.max_events):
            slot_code = self.hass.states.get(
                f"{TEXT}.{self.lockname}_code_slot_{i}_pin"
            )
            _LOGGER.debug("Slot code: '%s'", slot_code)
            if slot_code is None:
                continue
            slot_code_value = (
                "" if slot_code.state in ("unknown", "unavailable") else slot_code.state
            )

            slot_name = self.hass.states.get(
                f"{TEXT}.{self.lockname}_code_slot_{i}_name"
            )
            _LOGGER.debug("Slot name: '%s'", slot_name)
            if slot_name is None:
                continue
            slot_name_value = (
                "" if slot_name.state in ("unknown", "unavailable") else slot_name.state
            )

            use_date_range = self.hass.states.get(
                f"{SWITCH}.{self.lockname}_code_slot_{i}_use_date_range_limits"
            )

            if (
                slot_name_value
                and slot_code.state == ""
                and not slot_code_value
                and (use_date_range is None or use_date_range.state == "off")
            ):
                # Partially-reset slot: name persists but code was cleared
                # and date-range limits are off.  Only trigger when the raw
                # PIN state is explicitly empty, not when it is
                # unknown/unavailable (entity not yet loaded).
                _LOGGER.warning(
                    "Slot %d has name '%s' but no code; forcing "
                    "reset (Keymaster may not have fully cleared "
                    "the slot)",
                    i,
                    slot_name_value,
                )
                try:
                    await async_fire_clear_code(self, i)
                except Exception:
                    _LOGGER.exception(
                        "Failed to force-reset partially-cleared slot %d",
                        i,
                    )
                # Register the slot as empty so the overrides map
                # reaches max_slots and ready becomes True.
                slot_name_value = ""
                slot_code_value = ""

            if use_date_range and use_date_range.state == "on":
                start_time_state = self.hass.states.get(
                    f"{DATETIME}.{self.lockname}_code_slot_{i}_date_range_start"
                )
                _LOGGER.debug("Start time: '%s'", start_time_state)
                if start_time_state is None:
                    continue
                start_datetime = dt.parse_datetime(start_time_state.state)
                _LOGGER.debug("Start time: '%s'", start_datetime)
                if start_datetime is None:
                    continue
                start_time = start_datetime

                end_time_state = self.hass.states.get(
                    f"{DATETIME}.{self.lockname}_code_slot_{i}_date_range_end"
                )
                _LOGGER.debug("End time: '%s'", end_time_state)
                if end_time_state is None:
                    continue
                end_datetime = dt.parse_datetime(end_time_state.state)
                _LOGGER.debug("End time: '%s'", end_datetime)
                if end_datetime is None:
                    continue
                else:
                    end_time = end_datetime
            else:
                start_time = dt.start_of_local_day()
                end_time = dt.start_of_local_day() + timedelta(days=1)

            _LOGGER.debug(
                "Slot %d: %s, %s, %s, %s",
                i,
                slot_code_value,
                slot_name_value,
                start_time,
                end_time,
            )
            _LOGGER.debug("Updating event overrides")
            await self.update_event_overrides(
                i,
                slot_code_value,
                slot_name_value,
                start_time,
                end_time,
                request_refresh=False,
            )

    async def async_load_slot_store(self) -> None:
        """Load persisted slot mappings from the HA Store.

        Creates the Store on first call using the entry-scoped key
        ``STORE_SLOT_MAPPINGS_KEY.{entry_id}``.  Applies schema v1
        migration when the stored schema version is absent or below 1.
        Raw PINs are stripped after loading as a safety measure.
        """
        self._store = Store(
            self.hass,
            STORE_SCHEMA_VERSION,
            f"{STORE_SLOT_MAPPINGS_KEY}.{self._entry_id}",
        )
        raw: dict[str, Any] | None = await self._store.async_load()
        if raw is None:
            self._slot_mappings = {}
            return
        schema_version = raw.get("schema_version", 0)
        if schema_version < 1:
            raw = await self._migrate_slot_store_v1(raw)
        for mapping in raw.get("mappings", {}).values():
            last_obs = mapping.get("last_observed_actual")
            if last_obs is not None:
                last_obs.pop("pin", None)
                last_obs.pop("code", None)
                last_obs.pop("slot_code", None)
        self._slot_mappings = raw

    async def async_save_slot_store(self) -> None:
        """Save current slot mappings to the HA Store.

        A no-op when ``_store`` is ``None`` (store not yet
        initialised).  Raw PINs are stripped from
        ``last_observed_actual`` before persisting as a safety measure.
        """
        if self._store is None:
            return
        mappings: dict[str, Any] = {}
        for k, v in self._slot_mappings.get("mappings", {}).items():
            mapping = dict(v)
            last_obs = mapping.get("last_observed_actual")
            if last_obs is not None:
                last_obs = dict(last_obs)
                last_obs.pop("pin", None)
                last_obs.pop("code", None)
                last_obs.pop("slot_code", None)
                mapping["last_observed_actual"] = last_obs
            mappings[k] = mapping
        data: dict[str, Any] = {
            "schema_version": STORE_SCHEMA_VERSION,
            "entry_id": self._entry_id,
            "lockname": self.lockname,
            "start_slot": self.start_slot,
            "max_slots": self.max_events,
            "updated_at": dt.now().isoformat(),
            "mappings": mappings,
            "blocked_slots": self._slot_mappings.get("blocked_slots", {}),
        }
        await self._store.async_save(data)

    async def _migrate_slot_store_v1(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Migrate store data to schema version 1.

        Handles upgrading from schema_version 0 or a store that was
        written without a schema_version field.  Preserves any
        ``mappings`` and ``blocked_slots`` already present.

        Args:
            raw: The raw store dict to migrate.

        Returns:
            A dict conforming to schema version 1.
        """
        return {
            "schema_version": 1,
            "entry_id": raw.get("entry_id", self._entry_id),
            "lockname": raw.get("lockname", self.lockname),
            "start_slot": raw.get("start_slot", self.start_slot),
            "max_slots": raw.get("max_slots", self.max_events),
            "updated_at": raw.get("updated_at", dt.now().isoformat()),
            "mappings": raw.get("mappings", {}),
            "blocked_slots": raw.get("blocked_slots", {}),
        }

    async def async_adopt_keymaster_slots(self) -> None:
        """Adopt populated Keymaster slots on first upgrade.

        Called when the Store is empty (first upgrade from a version
        that did not persist slot mappings).  Iterates the managed slot
        range and records each populated slot without modifying any
        Keymaster state.

        Slots with both a name and a non-empty code are recorded as
        ``occupied``.  Slots with a name but no code (phantom slots)
        are recorded as ``pending_clear`` so they are fenced without
        being wiped immediately.  Empty slots (no name) are skipped.
        Raw PINs are never stored; only ``has_code: True/False`` is
        persisted.
        """
        if not self.lockname:
            return

        now_str = dt.now().isoformat()
        prefix = f"{self.event_prefix} " if self.event_prefix else ""
        mappings: dict[str, Any] = {}

        for i in range(self.start_slot, self.start_slot + self.max_events):
            name_state = self.hass.states.get(
                f"{TEXT}.{self.lockname}_code_slot_{i}_name"
            )
            if name_state is None:
                continue
            name_value = (
                ""
                if name_state.state in ("unknown", "unavailable")
                else name_state.state
            )
            if not name_value:
                continue

            code_state = self.hass.states.get(
                f"{TEXT}.{self.lockname}_code_slot_{i}_pin"
            )
            has_code = False
            if code_state is not None:
                code_value = (
                    ""
                    if code_state.state in ("unknown", "unavailable")
                    else code_state.state
                )
                has_code = bool(code_value)

            slot_name = name_value
            if prefix and slot_name.startswith(prefix):
                slot_name = slot_name[len(prefix) :]

            status = SLOT_STATUS_OCCUPIED if has_code else SLOT_STATUS_PENDING_CLEAR
            pending_clear_since: str | None = now_str if not has_code else None
            identity_key = f"adopted.{self._entry_id}.slot{i}"

            mappings[identity_key] = {
                "slot": i,
                "status": status,
                "operation_id": None,
                "operation_kind": None,
                "identity": {
                    "identity_key": identity_key,
                    "summary": slot_name,
                    "slot_name": slot_name,
                    "uid_aliases": [],
                    "booking_aliases": [],
                },
                "missing_count": 0,
                "pending_set_since": None,
                "pending_clear_since": pending_clear_since,
                "fingerprint_history": [],
                "updated_at": now_str,
                "last_observed_actual": {
                    "slot": i,
                    "classification": "adopted",
                    "name_state": name_value,
                    "has_code": has_code,
                    "start_state": None,
                    "end_state": None,
                    "use_date_range": None,
                    "enabled": None,
                },
            }

        if mappings:
            self._slot_mappings.setdefault("mappings", {}).update(mappings)
            if "schema_version" not in self._slot_mappings:
                self._slot_mappings.update(
                    {
                        "schema_version": STORE_SCHEMA_VERSION,
                        "entry_id": self._entry_id,
                        "lockname": self.lockname,
                        "start_slot": self.start_slot,
                        "max_slots": self.max_events,
                        "updated_at": now_str,
                        "blocked_slots": {},
                    }
                )
            await self.async_save_slot_store()

    def _generate_date_based_code(self, start: datetime, end: datetime) -> str:
        """Generate a date-based door code from reservation start/end times.

        Mirrors the ``date_based`` code generation in
        :class:`~.sensors.calsensor.RentalControlCalSensor` so that codes
        produced by the coordinator match what the sensor would generate.

        Args:
            start: Reservation start datetime.
            end: Reservation end datetime.

        Returns:
            A zero-padded date-derived code string of length
            :attr:`code_length`.
        """
        code_length = self.code_length
        start_day = start.strftime("%d")
        start_month = start.strftime("%m")
        start_year = start.strftime("%Y")
        end_day = end.strftime("%d")
        end_month = end.strftime("%m")
        end_year = end.strftime("%Y")
        code = f"{start_day}{end_day}{start_month}{end_month}{start_year}{end_year}"
        return (
            code[:code_length] if len(code) > code_length else code.zfill(code_length)
        )

    def _build_reservations(self, calendar: list[CalendarEvent]) -> list[_Reservation]:
        """Convert parsed CalendarEvent objects to Reservation objects.

        Produces one :class:`~.reconciliation.Reservation` per calendar
        event that has a usable slot name.  The coordinator's current
        persisted mappings are consulted to populate
        :attr:`~.reconciliation.Reservation.fingerprint_history` and
        :attr:`~.reconciliation.Reservation.missing_count`.

        Args:
            calendar: Parsed and sorted calendar events from the current
                refresh cycle.

        Returns:
            List of :class:`~.reconciliation.Reservation` objects ready
            for the planner.
        """
        if not calendar:
            return []

        persisted: dict[str, Any] = {}
        if self.event_overrides is not None:
            persisted = self.event_overrides.persisted_mappings

        prefix = f"{self.event_prefix} " if self.event_prefix else ""
        reservations: list[_Reservation] = []

        for event in calendar:
            slot_name = get_slot_name(
                event.summary,
                event.description or "",
                self.event_prefix or "",
            )
            if not slot_name:
                continue

            start: datetime = event.start
            end: datetime = event.end

            buffered_start_raw, buffered_end_raw = apply_buffer(
                start, end, self.code_buffer_before, self.code_buffer_after, self
            )
            buffered_start: datetime = (
                buffered_start_raw
                if isinstance(buffered_start_raw, datetime)
                else start
            )
            buffered_end: datetime = (
                buffered_end_raw if isinstance(buffered_end_raw, datetime) else end
            )

            identity_key = make_reservation_fingerprint(
                self._entry_id, slot_name, start, end
            )

            uid_raw = getattr(event, "uid", None)
            uid = normalize_uid(uid_raw)
            uid_aliases: set[str] = {uid} if uid else set()

            booking_aliases = extract_booking_aliases(
                event.summary, event.description or ""
            )

            actual_slot_names: dict[int, str] = {}
            for persisted_mapping in persisted.values():
                slot_num = persisted_mapping.get("slot")
                actual_name = (
                    persisted_mapping.get("last_observed_actual", {}) or {}
                ).get("name_state")
                if isinstance(slot_num, int) and isinstance(actual_name, str):
                    actual_slot_names[slot_num] = actual_name

            provisional = _Reservation(
                identity_key=identity_key,
                start=start,
                end=end,
                buffered_start=buffered_start,
                buffered_end=buffered_end,
                summary=event.summary,
                slot_name=slot_name,
                display_slot_name="",
                slot_code="",
                uid_aliases=uid_aliases,
                booking_aliases=booking_aliases,
            )
            rematch = find_reservation_rematch(
                provisional,
                persisted,
                current_reservations=reservations,
                actual_slot_names=actual_slot_names,
            )
            matched_key = rematch.matched_identity_key
            if matched_key is not None and matched_key != identity_key:
                mapping = persisted.pop(matched_key)
                history = set(mapping.get("fingerprint_history", []))
                history.add(matched_key)
                mapping["fingerprint_history"] = sorted(history)
                identity = mapping.setdefault("identity", {})
                if isinstance(identity, dict):
                    identity["identity_key"] = identity_key
                persisted[identity_key] = mapping
            else:
                mapping = persisted.get(identity_key, {})
                if rematch.kind.value == "ambiguous":
                    _LOGGER.warning(
                        "Reservation %s has ambiguous persisted rematch candidates: %s",
                        identity_key,
                        rematch.ambiguous_keys,
                    )

            fingerprint_history: set[str] = set(mapping.get("fingerprint_history", []))
            missing_count: int = mapping.get("missing_count", 0)

            slot_code = ""
            if self.event_overrides is not None:
                existing = self.event_overrides.get_slot_with_name(slot_name)
                if existing and existing.get("slot_code"):
                    slot_code = str(existing["slot_code"])
            if not slot_code:
                slot_code = self._generate_date_based_code(start, end)

            if self.trim_names and self.max_name_length > 0:
                guest_max = self.max_name_length - len(prefix)
                display_slot_name = f"{prefix}{trim_name(slot_name, guest_max)}"
            else:
                display_slot_name = f"{prefix}{slot_name}"

            try:
                res = _Reservation(
                    identity_key=identity_key,
                    start=start,
                    end=end,
                    buffered_start=buffered_start,
                    buffered_end=buffered_end,
                    summary=event.summary,
                    slot_name=slot_name,
                    display_slot_name=display_slot_name,
                    slot_code=slot_code,
                    uid_aliases=uid_aliases,
                    booking_aliases=booking_aliases,
                    fingerprint_history=fingerprint_history,
                    missing_count=missing_count,
                )
                reservations.append(res)
            except ValueError:
                _LOGGER.warning(
                    "Skipping invalid reservation for %s: start=%s >= end=%s",
                    event.summary,
                    start,
                    end,
                )

        return reservations

    def _observe_managed_slots(self) -> list[_ManagedSlot]:
        """Read Keymaster entity states and build ManagedSlot observations.

        Reads Keymaster text, switch, and datetime entities for every slot
        in the managed range to determine the current physical state.
        Persisted fence tokens from :attr:`event_overrides` override the
        entity-derived classification for ``PENDING_CLEAR`` slots.  The
        observed state is also written back to the
        :meth:`~.event_overrides.EventOverrides.update_actual_state`
        cache for diagnostics.

        Returns:
            List of :class:`~.reconciliation.ManagedSlot` instances, one
            per slot in ``start_slot .. start_slot + max_events - 1``.
        """
        if not self.lockname or not self.event_overrides:
            return []

        persisted = self.event_overrides.persisted_mappings
        pending_clear = self.event_overrides.pending_clear_slots

        slot_to_persisted_key: dict[int, str] = {}
        for key, mapping in persisted.items():
            slot_num = mapping.get("slot")
            if slot_num is None:
                continue
            current = slot_to_persisted_key.get(slot_num)
            if current is None:
                slot_to_persisted_key[slot_num] = key
            elif mapping.get("status") == SLOT_STATUS_OCCUPIED:
                slot_to_persisted_key[slot_num] = key

        slots: list[_ManagedSlot] = []

        for i in range(self.start_slot, self.start_slot + self.max_events):
            name_state = self.hass.states.get(
                f"{TEXT}.{self.lockname}_code_slot_{i}_name"
            )
            code_state = self.hass.states.get(
                f"{TEXT}.{self.lockname}_code_slot_{i}_pin"
            )

            if name_state is None or code_state is None:
                ms = _ManagedSlot(slot=i, managed=True, status=_SlotStatus.UNKNOWN)
                slots.append(ms)
                self.event_overrides.update_actual_state(
                    i,
                    {
                        "slot": i,
                        "classification": _SlotStatus.UNKNOWN.value,
                        "name_state": None,
                        "has_code": None,
                        "start_state": None,
                        "end_state": None,
                        "use_date_range": None,
                        "enabled": None,
                    },
                )
                continue

            name_value = (
                ""
                if name_state.state in ("unknown", "unavailable")
                else name_state.state
            )
            code_value = (
                ""
                if code_state.state in ("unknown", "unavailable")
                else code_state.state
            )
            has_code = bool(code_value)

            use_date_range_state = self.hass.states.get(
                f"{SWITCH}.{self.lockname}_code_slot_{i}_use_date_range_limits"
            )
            enabled_state = self.hass.states.get(
                f"{SWITCH}.{self.lockname}_code_slot_{i}_enabled"
            )
            start_dt_state = self.hass.states.get(
                f"{DATETIME}.{self.lockname}_code_slot_{i}_date_range_start"
            )
            end_dt_state = self.hass.states.get(
                f"{DATETIME}.{self.lockname}_code_slot_{i}_date_range_end"
            )

            date_range_on = (
                use_date_range_state is not None and use_date_range_state.state == "on"
            )
            enabled: bool | None = None
            if enabled_state is not None:
                enabled = enabled_state.state == "on"

            actual_start: datetime | None = None
            actual_end: datetime | None = None
            if date_range_on:
                if start_dt_state is not None:
                    actual_start = dt.parse_datetime(start_dt_state.state)
                if end_dt_state is not None:
                    actual_end = dt.parse_datetime(end_dt_state.state)

            persisted_key = slot_to_persisted_key.get(i)
            persisted_mapping = (
                persisted.get(persisted_key) if persisted_key is not None else None
            )
            persisted_status = (
                persisted_mapping.get("status")
                if persisted_mapping is not None
                else None
            )
            if i in pending_clear:
                status = _SlotStatus.PENDING_CLEAR
            elif persisted_status == SLOT_STATUS_BLOCKED:
                status = _SlotStatus.BLOCKED
            elif name_value and has_code:
                status = _SlotStatus.OCCUPIED
            elif name_value:
                status = _SlotStatus.PHANTOM
            else:
                status = _SlotStatus.FREE

            ms = _ManagedSlot(
                slot=i,
                managed=True,
                status=status,
                actual_name=name_value or None,
                actual_code_present=has_code,
                actual_start=actual_start,
                actual_end=actual_end,
                date_range_enabled=date_range_on,
                enabled=enabled,
                persisted_identity_key=persisted_key,
                last_error=self.event_overrides.get_last_slot_error(i),
            )
            slots.append(ms)

            self.event_overrides.update_actual_state(
                i,
                {
                    "slot": i,
                    "classification": status.value,
                    "name_state": name_value or None,
                    "has_code": has_code,
                    "start_state": actual_start,
                    "end_state": actual_end,
                    "use_date_range": date_range_on,
                    "enabled": enabled,
                },
            )

        return slots

    def _apply_checkin_protection(self, reservations: list[_Reservation]) -> None:
        """Mark active checked-in reservation as protected, if present.

        Reads :class:`~.sensors.checkinsensor.CheckinTrackingSensor` state
        from ``hass.data`` and sets :attr:`~.reconciliation.Reservation.protected_active`
        on the matching reservation so that reconciliation never evicts
        the active guest mid-stay (T043).

        When the sensor state is ``checked_out``, the matching reservation
        is flagged with :attr:`~.reconciliation.Reservation.checked_out`
        so the planner can handle graceful post-checkout slot release.

        Args:
            reservations: Mutable list of reservations for the current
                refresh cycle.  Modified in-place.
        """
        domain_data: dict[str, Any] | None = self.hass.data.get(DOMAIN)
        entry_data: dict[str, Any] = (
            domain_data.get(self._entry_id, {}) if domain_data is not None else {}
        )
        checkin_sensor = entry_data.get(CHECKIN_SENSOR)
        if checkin_sensor is None:
            return

        sensor_state: str = checkin_sensor.state
        if sensor_state not in (CHECKIN_STATE_CHECKED_IN, CHECKIN_STATE_CHECKED_OUT):
            return

        attrs: dict[str, Any] = checkin_sensor.extra_state_attributes
        guest_name: str | None = attrs.get("guest_name")
        if not guest_name:
            return

        for res in reservations:
            if res.slot_name == guest_name:
                if sensor_state == CHECKIN_STATE_CHECKED_IN:
                    res.protected_active = True
                elif sensor_state == CHECKIN_STATE_CHECKED_OUT:
                    res.checked_out = True
                break

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
        except aiohttp.ClientError as err:
            raise UpdateFailed(
                f"Calendar fetch failed for {self._name}: {err}"
            ) from err
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
            try:
                reservations = self._build_reservations(new_calendar)
                managed_slots = self._observe_managed_slots()
                self._apply_checkin_protection(reservations)

                plan_id = str(uuid.uuid4())
                plan = compute_desired_plan(
                    reservations=reservations,
                    managed_slots=managed_slots,
                    max_events=self.max_events,
                    plan_id=plan_id,
                    generated_at=dt.now(),
                    entry_id=self._entry_id,
                    lockname=self.lockname,
                    start_slot=self.start_slot,
                )

                violations = plan.validate()
                for v in violations:
                    _LOGGER.warning("Plan %s invariant violation: %s", plan_id, v)

                res_by_key: dict[str, _Reservation] = {
                    r.identity_key: r for r in reservations
                }
                await self.event_overrides.async_apply_plan(self, plan, res_by_key)

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
            except Exception:
                _LOGGER.exception(
                    "Reconciliation failed for %s; skipping cycle", self._name
                )

        await self.async_save_slot_store()

        # Refresh child lock discovery each cycle
        if self.lockname:
            self._parent_entry_id = self._find_parent_entry_id()
            previous_children = self._child_locknames
            self._child_locknames = self._discover_child_locks()
            if self._child_locknames != previous_children:
                _LOGGER.info(
                    "Child locknames updated for %s: %s",
                    self.lockname,
                    self._child_locknames or "(none)",
                )

        return new_calendar

    async def update_config(self, config: Mapping[str, Any]) -> None:
        """Update config entries."""
        self._name = config[CONF_NAME]
        self.name = self._name
        self.url = config[CONF_URL]
        self.timezone = ZoneInfo(config[CONF_TIMEZONE])
        self.refresh_frequency = config[CONF_REFRESH_FREQUENCY]
        self.update_interval = timedelta(minutes=self.refresh_frequency)
        self.event_prefix = config.get(CONF_EVENT_PREFIX)
        # our config flow guarantees that checkin and checkout are valid times
        # just use cv.time to get the parsed time object
        self.checkin = cv.time(config[CONF_CHECKIN])
        self.checkout = cv.time(config[CONF_CHECKOUT])
        lockname_raw = config.get(CONF_LOCK_ENTRY)
        previous_lockname = self.lockname
        previous_max_events = self.max_events
        previous_start_slot = self.start_slot
        self.lockname = (
            slugify(lockname_raw) if lockname_raw and lockname_raw.strip() else None
        )
        self.max_events = int(str(config.get(CONF_MAX_EVENTS)))
        self.start_slot = int(str(config.get(CONF_START_SLOT)))
        # Keep event_overrides in sync with config changes
        lockname_changed = self.lockname != previous_lockname
        if self.lockname:
            # Reset child lock discovery before any awaits to avoid
            # stale parent/children during concurrent refreshes.
            if lockname_changed:
                self._parent_entry_id = None
                self._child_locknames = set()
            overrides_stale = (
                self.event_overrides is None
                or lockname_changed
                or self.max_events != previous_max_events
                or self.start_slot != previous_start_slot
            )
            if overrides_stale:
                self.event_overrides = EventOverrides(self.start_slot, self.max_events)
                await self.async_setup_keymaster_overrides()
            # Re-discover parent entry and children on lockname change
            if lockname_changed:
                self._parent_entry_id = self._find_parent_entry_id()
                if self._parent_entry_id is not None:
                    self._child_locknames = self._discover_child_locks()
        else:
            self.event_overrides = None
            self._parent_entry_id = None
            self._child_locknames = set()
        self.days = config[CONF_DAYS]
        self.code_generator = config.get(CONF_CODE_GENERATION, DEFAULT_CODE_GENERATION)
        self.should_update_code = bool(config.get(CONF_SHOULD_UPDATE_CODE))
        self.honor_event_times = bool(config.get(CONF_HONOR_EVENT_TIMES))
        self.trim_names = bool(config.get(CONF_TRIM_NAMES, DEFAULT_TRIM_NAMES))
        self.max_name_length = int(
            str(config.get(CONF_MAX_NAME_LENGTH, DEFAULT_MAX_NAME_LENGTH))
        )
        previous_buffer_before = self.code_buffer_before
        previous_buffer_after = self.code_buffer_after
        self.code_buffer_before = int(
            str(config.get(CONF_CODE_BUFFER_BEFORE, DEFAULT_CODE_BUFFER_BEFORE))
        )
        self.code_buffer_after = int(
            str(config.get(CONF_CODE_BUFFER_AFTER, DEFAULT_CODE_BUFFER_AFTER))
        )
        if self.event_overrides is not None:
            self.event_overrides.trim_names = self.trim_names
            self.event_overrides.max_name_length = self.max_name_length
            prefix = f"{self.event_prefix} " if self.event_prefix else ""
            self.event_overrides.prefix_length = len(prefix)
        self.code_length = config.get(CONF_CODE_LENGTH, DEFAULT_CODE_LENGTH)
        self.ignore_non_reserved = bool(config.get(CONF_IGNORE_NON_RESERVED))
        self.verify_ssl = bool(config.get(CONF_VERIFY_SSL))

        await self.async_request_refresh()

        buffer_changed = (
            self.code_buffer_before != previous_buffer_before
            or self.code_buffer_after != previous_buffer_after
        )
        if buffer_changed:
            await self._async_update_buffer_times(
                previous_buffer_before, previous_buffer_after
            )

    async def _async_update_buffer_times(self, old_before: int, old_after: int) -> None:
        """Re-apply buffer to all assigned slots after config change.

        Override times are sourced from Keymaster entities which store
        already-buffered values.  To avoid double-buffering, reverse
        the previous buffer before applying the new one.
        """
        if not self.event_overrides or not self.lockname:
            return
        for slot in range(self.start_slot, self.start_slot + self.max_events):
            override = self.event_overrides.overrides.get(slot)
            if override is None:
                continue
            # Reverse the old buffer to recover unbuffered times
            start = override["start_time"]
            end = override["end_time"]
            if old_before:
                start = start + timedelta(minutes=old_before)
            if old_after:
                end = end - timedelta(minutes=old_after)
            # Apply new buffer
            buffered_start, buffered_end = apply_buffer(
                start,
                end,
                self.code_buffer_before,
                self.code_buffer_after,
                self,
            )
            coro: list[Coroutine] = []
            coro = add_call(
                self.hass,
                coro,
                DATETIME,
                "set_value",
                f"{DATETIME}.{self.lockname}_code_slot_{slot}_date_range_end",
                {"datetime": buffered_end},
            )
            coro = add_call(
                self.hass,
                coro,
                DATETIME,
                "set_value",
                f"{DATETIME}.{self.lockname}_code_slot_{slot}_date_range_start",
                {"datetime": buffered_start},
            )
            results = await asyncio.gather(*coro, return_exceptions=True)
            check_gather_results(
                results,
                f"Buffer time update slot {slot} ({self.lockname})",
                _LOGGER,
            )

    async def update_event_overrides(
        self,
        slot: int,
        slot_code: str,
        slot_name: str,
        start_time: datetime,
        end_time: datetime,
        *,
        request_refresh: bool = True,
    ) -> None:
        """Update the event overrides with the ServiceCall data."""
        _LOGGER.debug("In update_event_overrides")

        if self.event_overrides:
            await self.event_overrides.async_update(
                slot,
                slot_code,
                slot_name,
                start_time,
                end_time,
                self.event_prefix,
            )

        if request_refresh:
            await self.async_request_refresh()

    async def _ical_parser(
        self, calendar: Calendar, from_date: datetime, to_date: datetime
    ) -> list[CalendarEvent]:
        """Return a sorted list of events from a icalendar object."""

        events: list[CalendarEvent] = []

        _LOGGER.debug(
            "In _ical_parser:: from_date: %s; to_date: %s", from_date, to_date
        )

        for event in calendar.walk("VEVENT"):
            # RRULEs should not exist in AirBnB bookings, so log and error and
            # skip
            if "RRULE" in event:
                _LOGGER.error("RRULE in event: %s", str(event["SUMMARY"]))

            elif "Check-in" in event["SUMMARY"] or "Check-out" in event["SUMMARY"]:
                _LOGGER.debug("Smoobu extra event, ignoring")

            else:
                # Let's use the same magic as for rrules to get this (as) right
                # (as possible)
                try:
                    # Just ignore events that ended a long time ago
                    if "DTEND" in event and event[
                        "DTEND"
                    ].dt < from_date.date() - timedelta(days=EVENT_AGE_THRESHOLD_DAYS):
                        continue
                except (AttributeError, TypeError):  # fmt: skip
                    pass

                try:
                    # Ignore dates that are too far in the future
                    if "DTSTART" in event and event["DTSTART"].dt > to_date.date():
                        continue
                except (AttributeError, TypeError):  # fmt: skip
                    pass

                # Ignore Blocked or Not available by default, but if false,
                # keep the events.
                if self.ignore_non_reserved:
                    if any(x in event["SUMMARY"] for x in ["Blocked", "Not available"]):
                        # Skip Blocked or 'Not available' events
                        continue

                if "DESCRIPTION" in event:
                    slot_name = get_slot_name(
                        event["SUMMARY"], event["DESCRIPTION"], ""
                    )
                else:
                    # VRBO and Booking.com do not have a DESCRIPTION element
                    slot_name = get_slot_name(event["SUMMARY"], "", "")

                override = None
                if slot_name and self.event_overrides:
                    override = self.event_overrides.get_slot_with_name(slot_name)

                # Determine if event has explicit times (datetime vs date)
                has_explicit_times = isinstance(event["DTSTART"].dt, datetime) and (
                    "DTEND" in event and isinstance(event["DTEND"].dt, datetime)
                )

                if self.honor_event_times and has_explicit_times:
                    # FR-003: PMS times take priority for timed events
                    checkin: time = event["DTSTART"].dt.time()
                    checkout: time = event["DTEND"].dt.time()
                elif self.honor_event_times and not has_explicit_times:
                    # Priority 2: description-extracted times (NEW)
                    raw_desc = event.get("DESCRIPTION")
                    description = str(raw_desc) if raw_desc else ""
                    desc_checkin = extract_checkin_time(description)
                    desc_checkout = extract_checkout_time(description)

                    if override:
                        # Priority 3 fallback for missing description times
                        start_tz = override["start_time"].astimezone(self.timezone)
                        end_tz = override["end_time"].astimezone(self.timezone)
                        checkin = (
                            desc_checkin
                            if desc_checkin is not None
                            else start_tz.time()
                        )
                        checkout = (
                            desc_checkout
                            if desc_checkout is not None
                            else end_tz.time()
                        )
                    else:
                        # Priority 4 fallback for missing description times
                        checkin = (
                            desc_checkin if desc_checkin is not None else self.checkin
                        )
                        checkout = (
                            desc_checkout
                            if desc_checkout is not None
                            else self.checkout
                        )
                elif override:
                    # FR-005 (disabled) or FR-004 (all-day with override)
                    # Get start & end overrides in the correct timezone
                    # Overrides are stored in UTC since Keymaster's time
                    # start and end configuration values are in UTC
                    start_time: datetime = override["start_time"].astimezone(
                        self.timezone
                    )
                    end_time: datetime = override["end_time"].astimezone(self.timezone)
                    checkin = start_time.time()
                    checkout = end_time.time()
                else:
                    try:
                        # If the event has a time, use that, otherwise use the
                        # default checkin/checkout times
                        # No need to do tz conversion here, as the
                        # DTSTART and DTEND are already in the correct timezone
                        checkin = event["DTSTART"].dt.time()
                        checkout = event["DTEND"].dt.time()
                    except AttributeError:
                        checkin = self.checkin
                        checkout = self.checkout

                _LOGGER.debug("Checkin: %s, Checkout: %s", checkin, checkout)
                _LOGGER.debug("DTSTART in event: %s", event["DTSTART"].dt)
                dtstart: datetime = datetime.combine(
                    event["DTSTART"].dt, checkin, self.timezone
                )
                # convert dtstart to UTC
                dtstart = dt.as_utc(dtstart)

                start: datetime = dtstart

                if "DTEND" not in event:
                    dtend: datetime = dtstart
                else:
                    _LOGGER.debug("DTEND in event: %s", event["DTEND"].dt)
                    dtend = datetime.combine(event["DTEND"].dt, checkout, self.timezone)
                # convert dtend to UTC
                dtend = dt.as_utc(dtend)
                end = dtend

                # Modify the SUMMARY if we have an event_prefix
                if self.event_prefix:
                    event["SUMMARY"] = self.event_prefix + " " + event["SUMMARY"]

                cal_event: CalendarEvent | None = await self._ical_event(
                    start, end, from_date, event
                )
                if cal_event:
                    events.append(cal_event)

        events.sort(key=lambda k: k.start)
        return events

    async def _ical_event(
        self,
        start: dt.dt.datetime,
        end: dt.dt.datetime,
        from_date: dt.dt.datetime,
        event: dict[Any, Any],
    ) -> CalendarEvent | None:
        """Ensure that events are within the start and end."""
        _LOGGER.debug(
            "Running _ical_event for %s", str(event.get("SUMMARY", "Unknown"))
        )
        _LOGGER.debug("Start: %s, End: %s", start, end)
        _LOGGER.debug("From: %s", from_date)
        # Ignore events that ended this midnight.
        if (dt.as_utc(end) < dt.as_utc(from_date)) or (
            dt.as_utc(end).date() == dt.as_utc(from_date).date()
            and end.hour == 0
            and end.minute == 0
            and end.second == 0
        ):
            _LOGGER.debug("This event has already ended")
            return None
        _LOGGER.debug(
            "Start: %s Tzinfo: %s Default: %s StartAs %s",
            str(start),
            str(start.tzinfo),
            self.timezone,
            start.astimezone(self.timezone),
        )
        description = event.get("DESCRIPTION")

        raw_uid = event.get("UID")
        cal_event = CalendarEvent(
            description=description,
            end=end.astimezone(self.timezone),
            location=event.get("LOCATION"),
            summary=event.get("SUMMARY", "Unknown"),
            start=start.astimezone(self.timezone),
            uid=normalize_uid(str(raw_uid) if raw_uid is not None else None),
        )

        _LOGGER.debug("Event to add: %s", cal_event)
        return cal_event


# test
