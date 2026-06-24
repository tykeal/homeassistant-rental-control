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
from datetime import date
from datetime import datetime
from datetime import time
from datetime import timedelta
from datetime import timezone
from functools import lru_cache
import logging
import random
import re
from typing import Any
from typing import cast
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
from .const import OPERATION_KIND_CLEAR
from .const import REQUEST_TIMEOUT
from .const import SLOT_STATUS_OCCUPIED
from .const import SLOT_STATUS_PENDING_CLEAR
from .const import SLOT_STATUS_PENDING_SET
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
from .reconciliation import make_reservation_fingerprint
from .reconciliation import normalize_slot_name_for_fingerprint
from .util import OperationResult
from .util import add_call
from .util import apply_buffer
from .util import async_fire_clear_code
from .util import check_gather_results
from .util import get_slot_name
from .util import is_cleared_keymaster_text_state as _is_blank_keymaster_text
from .util import is_unreadable_keymaster_text_state
from .util import normalize_uid
from .util import trim_name

# aislop-ignore-file ai-slop/hallucinated-import -- Provided by Home Assistant runtime.
# aislop-ignore-file complexity/file-too-large complexity/function-too-long -- Existing module size is outside this emergency fix scope.

_LOGGER = logging.getLogger(__name__)


def _store_datetime(value: Any) -> Any:
    """Return a JSON-serializable datetime value for Store payloads."""
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _adopted_slot_placeholder(slot: int) -> str:
    """Return a safe placeholder name for a code-bearing unnamed slot."""
    return f"Adopted Slot {slot}"


def _format_display_slot_name(
    slot_name: str,
    prefix: str,
    trim_names: bool,
    max_name_length: int,
) -> str:
    """Return the Keymaster display name for a reservation slot."""
    display_name = f"{prefix}{slot_name}"
    if not trim_names or max_name_length <= 0:
        return display_name
    if len(prefix) >= max_name_length:
        return trim_name(display_name, max_name_length)
    return f"{prefix}{trim_name(slot_name, max_name_length - len(prefix))}"


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
        self._checkin_restore_pending = False

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

        def scrub_codes(value: Any) -> Any:
            """Return diagnostics with raw code-bearing keys removed."""
            if isinstance(value, dict):
                return {
                    key: scrub_codes(item)
                    for key, item in value.items()
                    if key not in {"slot_code", "pin", "code"}
                }
            if isinstance(value, list):
                return [scrub_codes(item) for item in value]
            return value

        result: dict[str, Any] = {}
        if self._latest_plan is not None:
            result.update(self._latest_plan.diagnostics)
        if self.event_overrides is not None:
            result["event_overrides"] = self.event_overrides.diagnostics_snapshot
        return cast("dict[str, Any]", scrub_codes(result))

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
            if is_unreadable_keymaster_text_state(slot_code.state):
                continue
            slot_code_value = (
                "" if _is_blank_keymaster_text(slot_code.state) else slot_code.state
            )

            slot_name = self.hass.states.get(
                f"{TEXT}.{self.lockname}_code_slot_{i}_name"
            )
            _LOGGER.debug("Slot name: '%s'", slot_name)
            if slot_name is None:
                continue
            if is_unreadable_keymaster_text_state(slot_name.state):
                continue
            slot_name_value = (
                "" if _is_blank_keymaster_text(slot_name.state) else slot_name.state
            )

            use_date_range = self.hass.states.get(
                f"{SWITCH}.{self.lockname}_code_slot_{i}_use_date_range_limits"
            )

            if (
                slot_name_value
                and _is_blank_keymaster_text(slot_code.state)
                and not slot_code_value
                and (use_date_range is None or use_date_range.state == "off")
            ):
                # Partially-reset slot: name persists but code was cleared
                # and date-range limits are off.  Keymaster reset exposes
                # Null as HA "unknown"; "unavailable" was skipped above
                # because that state is not evidence that the code cleared.
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
            elif slot_code_value and not slot_name_value:
                _LOGGER.warning(
                    "Slot %d has a code but no readable name; marking it "
                    "occupied with a placeholder to avoid reuse",
                    i,
                )
                slot_name_value = _adopted_slot_placeholder(i)

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
        """Load cache-only slot metadata from the HA Store."""
        self._store = Store(
            self.hass,
            STORE_SCHEMA_VERSION,
            f"{STORE_SLOT_MAPPINGS_KEY}.{self._entry_id}",
        )
        try:
            raw: dict[str, Any] | None = await self._store.async_load()
        except Exception as err:
            _LOGGER.warning(
                "Ignoring unreadable Rental Control slot cache for %s: %s",
                self._entry_id,
                err,
            )
            self._slot_mappings = self._empty_slot_cache("cache_load_failed")
            return
        if raw is None:
            self._slot_mappings = self._empty_slot_cache("cache_missing")
            return
        if not isinstance(raw, dict):
            self._slot_mappings = self._empty_slot_cache("cache_corrupt")
            return
        schema_version = raw.get("schema_version", 0)
        if not isinstance(schema_version, int):
            self._slot_mappings = self._empty_slot_cache("cache_corrupt")
            return
        if schema_version < 1:
            try:
                raw = await self._migrate_slot_store_v1(raw)
            except Exception:
                self._slot_mappings = self._empty_slot_cache("cache_corrupt")
                return
        mappings = raw.get("mappings", {})
        if not isinstance(mappings, dict):
            self._slot_mappings = self._empty_slot_cache("cache_corrupt")
            return
        aliases = raw.get("aliases", {})
        if aliases is None:
            raw["aliases"] = {}
        elif not isinstance(aliases, dict):
            self._slot_mappings = self._empty_slot_cache("cache_corrupt")
            return
        migration_notes = raw.get("migration_notes", [])
        if migration_notes is None:
            raw["migration_notes"] = []
        elif not isinstance(migration_notes, list):
            self._slot_mappings = self._empty_slot_cache("cache_corrupt")
            return
        for mapping in mappings.values():
            if not isinstance(mapping, dict):
                self._slot_mappings = self._empty_slot_cache("cache_corrupt")
                return
            last_obs = mapping.get("last_observed_actual")
            if last_obs is not None:
                if not isinstance(last_obs, dict):
                    self._slot_mappings = self._empty_slot_cache("cache_corrupt")
                    return
                last_obs.pop("pin", None)
                last_obs.pop("code", None)
                last_obs.pop("slot_code", None)
        raw.setdefault("mappings", {})
        raw.setdefault("aliases", {})
        raw.setdefault("migration_notes", [])
        self._slot_mappings = raw

    def _empty_slot_cache(self, note: str) -> dict[str, Any]:
        """Return an empty cache-only Store payload with a migration note."""
        return {
            "schema_version": STORE_SCHEMA_VERSION,
            "entry_id": self._entry_id,
            "lockname": self.lockname,
            "mappings": {},
            "aliases": {},
            "migration_notes": [note],
        }

    def get_persisted_slot_mappings(self) -> dict[str, Any]:
        """Return entry-scoped persisted reservation-slot mappings."""
        return cast("dict[str, Any]", self._slot_mappings.get("mappings", {}))

    async def async_save_slot_store(self) -> None:
        """Best-effort save of cache-only slot metadata to the HA Store."""
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
            "aliases": self._slot_mappings.get("aliases", {}),
            "last_plan": self._slot_mappings.get("last_plan", {}),
            "migration_notes": self._slot_mappings.get("migration_notes", []),
        }
        try:
            await self._store.async_save(data)
        except Exception as err:
            _LOGGER.warning(
                "Failed to save Rental Control slot cache for %s: %s",
                self._entry_id,
                err,
            )

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
        migration_notes = raw.get("migration_notes", [])
        if not isinstance(migration_notes, list):
            migration_notes = []
        return {
            "schema_version": 1,
            "entry_id": raw.get("entry_id", self._entry_id),
            "lockname": raw.get("lockname", self.lockname),
            "start_slot": raw.get("start_slot", self.start_slot),
            "max_slots": raw.get("max_slots", self.max_events),
            "updated_at": raw.get("updated_at", dt.now().isoformat()),
            "mappings": raw.get("mappings", {}),
            "aliases": raw.get("aliases", {}),
            "migration_notes": [
                *migration_notes,
                "legacy_authoritative_fields_ignored",
            ],
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
        existing_slots = {
            mapping.get("slot")
            for mapping in self._slot_mappings.get("mappings", {}).values()
            if isinstance(mapping, dict)
        }

        for i in range(self.start_slot, self.start_slot + self.max_events):
            if i in existing_slots:
                continue
            name_state = self.hass.states.get(
                f"{TEXT}.{self.lockname}_code_slot_{i}_name"
            )
            if name_state is None:
                continue
            if is_unreadable_keymaster_text_state(name_state.state):
                continue
            name_value = (
                "" if _is_blank_keymaster_text(name_state.state) else name_state.state
            )

            code_state = self.hass.states.get(
                f"{TEXT}.{self.lockname}_code_slot_{i}_pin"
            )
            has_code = False
            if code_state is not None:
                if is_unreadable_keymaster_text_state(code_state.state):
                    continue
                code_value = (
                    ""
                    if _is_blank_keymaster_text(code_state.state)
                    else code_state.state
                )
                has_code = bool(code_value)
            if not name_value and not has_code:
                continue

            use_date_range_state = self.hass.states.get(
                f"{SWITCH}.{self.lockname}_code_slot_{i}_use_date_range_limits"
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
            actual_start: datetime | None = None
            actual_end: datetime | None = None
            if date_range_on:
                if start_dt_state is not None:
                    actual_start = dt.parse_datetime(start_dt_state.state)
                if end_dt_state is not None:
                    actual_end = dt.parse_datetime(end_dt_state.state)

            slot_name = name_value or _adopted_slot_placeholder(i)
            if prefix and slot_name.startswith(prefix):
                slot_name = slot_name[len(prefix) :]

            status = SLOT_STATUS_OCCUPIED if has_code else SLOT_STATUS_PENDING_CLEAR
            pending_clear_since: str | None = now_str if not has_code else None
            identity_key = f"adopted.{self._entry_id}.slot{i}"
            if identity_key in self._slot_mappings.get("mappings", {}):
                continue

            mappings[identity_key] = {
                "slot": i,
                "status": status,
                "operation_id": None,
                "operation_kind": None,
                "identity": {
                    "identity_key": identity_key,
                    "summary": slot_name,
                    "slot_name": slot_name,
                    "start": _store_datetime(actual_start),
                    "end": _store_datetime(actual_end),
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
                    "start_state": _store_datetime(actual_start),
                    "end_state": _store_datetime(actual_end),
                    "use_date_range": date_range_on,
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
            if self.event_overrides is not None:
                self.event_overrides.load_persisted_mappings(
                    self._slot_mappings.get("mappings", {})
                )

    def _adopt_observed_coded_slots(self, managed_slots: list[_ManagedSlot]) -> None:
        """Adopt readable coded slots that have no persisted mapping yet."""
        persisted: dict[str, Any] = self._slot_mappings.setdefault("mappings", {})
        existing_slots = {
            mapping.get("slot")
            for mapping in persisted.values()
            if isinstance(mapping, dict)
        }
        now_str = dt.now().isoformat()
        prefix = f"{self.event_prefix} " if self.event_prefix else ""
        adopted = False

        for ms in managed_slots:
            if (
                not ms.managed
                or ms.persisted_identity_key is not None
                or ms.slot in existing_slots
                or ms.status is not _SlotStatus.OCCUPIED
                or ms.actual_code_present is not True
            ):
                continue

            slot_name = ms.actual_name or _adopted_slot_placeholder(ms.slot)
            if prefix and slot_name.startswith(prefix):
                slot_name = slot_name[len(prefix) :]

            identity_key = f"adopted.{self._entry_id}.slot{ms.slot}"
            if identity_key in persisted:
                continue

            persisted[identity_key] = {
                "slot": ms.slot,
                "status": SLOT_STATUS_OCCUPIED,
                "operation_id": None,
                "operation_kind": None,
                "identity": {
                    "identity_key": identity_key,
                    "summary": slot_name,
                    "slot_name": slot_name,
                    "start": _store_datetime(ms.actual_start),
                    "end": _store_datetime(ms.actual_end),
                    "uid_aliases": [],
                    "booking_aliases": [],
                },
                "missing_count": 0,
                "pending_set_since": None,
                "pending_clear_since": None,
                "fingerprint_history": [],
                "updated_at": now_str,
                "last_observed_actual": {
                    "slot": ms.slot,
                    "classification": ms.status.value,
                    "name_state": ms.actual_name,
                    "has_code": True,
                    "start_state": _store_datetime(ms.actual_start),
                    "end_state": _store_datetime(ms.actual_end),
                    "use_date_range": ms.date_range_enabled,
                    "enabled": ms.enabled,
                },
            }
            ms.persisted_identity_key = identity_key
            existing_slots.add(ms.slot)
            adopted = True

        if adopted:
            self._slot_mappings.update(
                {
                    "schema_version": STORE_SCHEMA_VERSION,
                    "entry_id": self._entry_id,
                    "lockname": self.lockname,
                    "start_slot": self.start_slot,
                    "max_slots": self.max_events,
                    "updated_at": now_str,
                    "blocked_slots": self._slot_mappings.get("blocked_slots", {}),
                }
            )
            if self.event_overrides is not None:
                self.event_overrides.load_persisted_mappings(persisted)

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

    @staticmethod
    def _extract_last_four(description: str | None) -> str | None:
        """Extract last-four phone digits from reservation text."""
        if description is None:
            return None

        explicit = re.findall(r"""\(?Last 4 Digits\)?:\s+(\d{4})(?!\d)""", description)
        if explicit:
            return str(explicit[0])

        phone_last_four = re.findall(
            r"""Phone\s*\(last\s*4\):\s*(\d{4})(?!\d)""",
            description,
            re.I,
        )
        if phone_last_four:
            return str(phone_last_four[0])

        if "Phone" in description:
            phone_matches = re.findall(
                r"""Phone(?: Number)?:\s+(\+?[\d\. \-\(\)]{9,})""",
                description,
            )
            if phone_matches:
                digits = str(phone_matches[0]).replace(" ", "")
                if len(digits) >= 4:
                    return digits[-4:]

        return None

    def _generate_slot_code(
        self,
        start: datetime,
        end: datetime,
        description: str | None,
        uid: str | None,
    ) -> str:
        """Generate a slot code using the configured legacy generator."""
        generator = self.code_generator

        if description is None and (generator != "static_random" or uid is None):
            generator = "date_based"

        code: str | None = None
        if generator == "last_four" and self.code_length == 4:
            code = self._extract_last_four(description)
        elif generator == "static_random":
            seed = uid if uid else description
            if seed:
                rng = random.Random(seed)
                max_range = int("9999".rjust(self.code_length, "9"))
                code = str(rng.randrange(1, max_range, self.code_length)).zfill(
                    self.code_length
                )

        return code if code is not None else self._generate_date_based_code(start, end)

    def _merge_observed_slots_into_mappings(
        self, managed_slots: list[_ManagedSlot]
    ) -> None:
        """Refresh persisted actual snapshots from current physical slots.

        Store mappings loaded on restart may contain stale
        ``last_observed_actual`` snapshots.  Before rematching current
        calendar reservations, physical Keymaster state must be allowed to
        win over those stale snapshots so populated slots can be reclaimed
        rather than stale-cleared.  Readable coded slots with no mapping
        are adopted here so deleted or missing stores recover on any
        refresh once Keymaster entities settle.
        """
        self._adopt_observed_coded_slots(managed_slots)
        persisted = self._slot_mappings.get("mappings", {})
        for ms in managed_slots:
            if ms.persisted_identity_key is None:
                continue
            mapping = persisted.get(ms.persisted_identity_key)
            if mapping is None:
                continue
            mapping["last_observed_actual"] = {
                "slot": ms.slot,
                "classification": ms.status.value,
                "name_state": ms.actual_name,
                "has_code": ms.actual_code_present,
                "start_state": _store_datetime(ms.actual_start),
                "end_state": _store_datetime(ms.actual_end),
                "use_date_range": ms.date_range_enabled,
                "enabled": ms.enabled,
            }

    @staticmethod
    def _observed_value_as_datetime(value: Any) -> datetime | None:
        """Return a datetime for an observed Store value, if parseable."""
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str):
            parsed = dt.parse_datetime(value)
            if not isinstance(parsed, datetime):
                return None
        else:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _physical_mapping_matches_reservation(
        mapping: dict[str, Any],
        reservation: _Reservation,
        actual_slot_names: dict[int, str],
    ) -> bool:
        """Return whether fresh physical state identifies a reservation."""
        if not RentalControlCoordinator._physical_mapping_name_matches_reservation(
            mapping, reservation, actual_slot_names
        ):
            return False

        actual = mapping.get("last_observed_actual", {})
        if not isinstance(actual, dict):
            return True
        actual_start = RentalControlCoordinator._observed_value_as_datetime(
            actual.get("start_state")
        )
        actual_end = RentalControlCoordinator._observed_value_as_datetime(
            actual.get("end_state")
        )
        if actual_start is None or actual_end is None:
            return True
        return (
            actual_start == reservation.buffered_start
            and actual_end == reservation.buffered_end
        )

    @staticmethod
    def _physical_mapping_name_matches_reservation(
        mapping: dict[str, Any],
        reservation: _Reservation,
        actual_slot_names: dict[int, str],
    ) -> bool:
        """Return whether a fresh physical slot name matches a reservation."""
        slot_num = mapping.get("slot")
        if not isinstance(slot_num, int):
            return False
        actual_name = actual_slot_names.get(slot_num)
        if not actual_name:
            return False
        actual_name_form = normalize_slot_name_for_fingerprint(actual_name)
        reservation_name_forms = {
            normalize_slot_name_for_fingerprint(reservation.slot_name),
            normalize_slot_name_for_fingerprint(reservation.display_slot_name),
        }
        if actual_name_form not in reservation_name_forms:
            return False
        return True

    def _find_observed_slot_by_name(
        self,
        managed_slots: list[_ManagedSlot],
        slot_name: str,
        display_slot_name: str,
        consumed_slots: set[int] | None = None,
        desired_start: datetime | None = None,
        desired_end: datetime | None = None,
        require_date_match: bool = False,
        reserved_date_windows: set[tuple[datetime, datetime]] | None = None,
        ordered_date_windows: list[tuple[datetime, datetime]] | None = None,
        block_unknown_date_fallback: bool = False,
        expected_name_count: int = 1,
    ) -> _ManagedSlot | None:
        """Return the current physical slot matching a stable/display name."""
        prefix = f"{self.event_prefix} " if self.event_prefix else ""
        desired_forms = {
            normalize_slot_name_for_fingerprint(slot_name),
            normalize_slot_name_for_fingerprint(display_slot_name),
        }
        consumed = consumed_slots if consumed_slots is not None else set()
        all_candidates: list[_ManagedSlot] = []
        candidates: list[_ManagedSlot] = []
        matching_candidate_count = 0
        for slot in sorted(
            managed_slots,
            key=lambda observed: (
                observed.actual_start or datetime.max.replace(tzinfo=timezone.utc),
                observed.actual_end or datetime.max.replace(tzinfo=timezone.utc),
                observed.slot,
            ),
        ):
            if not slot.managed or not slot.actual_name:
                continue
            actual = slot.actual_name
            actual_forms = {normalize_slot_name_for_fingerprint(actual)}
            if prefix and actual.startswith(prefix):
                actual_forms.add(
                    normalize_slot_name_for_fingerprint(actual[len(prefix) :])
                )
            if actual_forms & desired_forms:
                matching_candidate_count += 1
                all_candidates.append(slot)
                if slot.slot in consumed:
                    continue
                candidates.append(slot)
        if require_date_match:
            if matching_candidate_count < expected_name_count:
                if desired_start is not None and desired_end is not None:
                    for slot in candidates:
                        if (
                            slot.actual_start == desired_start
                            and slot.actual_end == desired_end
                        ):
                            consumed.add(slot.slot)
                            return slot
                if ordered_date_windows:
                    pairings = self._select_partial_ordered_pairings(
                        all_candidates, ordered_date_windows
                    )
                    desired_window = (
                        (desired_start, desired_end)
                        if desired_start is not None and desired_end is not None
                        else None
                    )
                    matched_slot = (
                        pairings.get(desired_window)
                        if desired_window is not None
                        else None
                    )
                    if matched_slot is not None and matched_slot.slot not in consumed:
                        consumed.add(matched_slot.slot)
                        return matched_slot
                    return None
                shifted_candidates = [
                    slot
                    for slot in candidates
                    if slot.actual_start is not None
                    and slot.actual_end is not None
                    and (
                        not reserved_date_windows
                        or (slot.actual_start, slot.actual_end)
                        not in reserved_date_windows
                    )
                ]
                if len(shifted_candidates) == 1:
                    consumed.add(shifted_candidates[0].slot)
                    return shifted_candidates[0]
                return None
            if any(
                slot.actual_start is None or slot.actual_end is None
                for slot in all_candidates
            ):
                return None
            if ordered_date_windows and matching_candidate_count > expected_name_count:
                canonical = self._select_ordered_physical_subset(
                    all_candidates, ordered_date_windows
                )
                desired_window = (
                    (desired_start, desired_end)
                    if desired_start is not None and desired_end is not None
                    else None
                )
                for slot, window in zip(canonical, ordered_date_windows, strict=False):
                    if slot.slot not in consumed and (
                        desired_window is None or window == desired_window
                    ):
                        consumed.add(slot.slot)
                        return slot
                return None
            if candidates:
                consumed.add(candidates[0].slot)
                return candidates[0]
            return None
        if desired_start is not None and desired_end is not None:
            for slot in candidates:
                if (
                    slot.actual_start == desired_start
                    and slot.actual_end == desired_end
                ):
                    consumed.add(slot.slot)
                    return slot
        fallback_candidates = candidates
        if reserved_date_windows and block_unknown_date_fallback:
            fallback_candidates = [
                slot
                for slot in candidates
                if slot.actual_start is not None
                and slot.actual_end is not None
                and (slot.actual_start, slot.actual_end) not in reserved_date_windows
            ]
        if fallback_candidates:
            consumed.add(fallback_candidates[0].slot)
            return fallback_candidates[0]
        return None

    @staticmethod
    def _select_ordered_physical_subset(
        slots: list[_ManagedSlot], desired_windows: list[tuple[datetime, datetime]]
    ) -> list[_ManagedSlot]:
        """Return minimum-distance ordered physical subset for desired windows."""

        def _distance(slot: _ManagedSlot, window: tuple[datetime, datetime]) -> float:
            """Return absolute date distance for one physical/desired pair."""
            assert slot.actual_start is not None
            assert slot.actual_end is not None
            return abs((slot.actual_start - window[0]).total_seconds()) + abs(
                (slot.actual_end - window[1]).total_seconds()
            )

        @lru_cache
        def _best(slot_index: int, desired_index: int) -> tuple[float, tuple[int, ...]]:
            """Return best ordered subset cost and indices from this position."""
            if desired_index == len(desired_windows):
                return 0.0, ()
            if slot_index == len(slots):
                return float("inf"), ()
            skip_cost, skip_indices = _best(slot_index + 1, desired_index)
            take_rest_cost, take_indices = _best(slot_index + 1, desired_index + 1)
            take_cost = (
                _distance(slots[slot_index], desired_windows[desired_index])
                + take_rest_cost
            )
            if take_cost < skip_cost:
                return take_cost, (slot_index, *take_indices)
            return skip_cost, skip_indices

        _, indices = _best(0, 0)
        return [slots[index] for index in indices]

    @staticmethod
    def _select_partial_ordered_pairings(
        slots: list[_ManagedSlot], desired_windows: list[tuple[datetime, datetime]]
    ) -> dict[tuple[datetime, datetime], _ManagedSlot]:
        """Return ordered pairings when physical duplicates are missing."""
        dated_slots = [
            slot
            for slot in slots
            if slot.actual_start is not None and slot.actual_end is not None
        ]
        if not dated_slots:
            return {}

        def _distance(slot: _ManagedSlot, window: tuple[datetime, datetime]) -> float:
            """Return absolute date distance for one physical/desired pair."""
            assert slot.actual_start is not None
            assert slot.actual_end is not None
            return abs((slot.actual_start - window[0]).total_seconds()) + abs(
                (slot.actual_end - window[1]).total_seconds()
            )

        @lru_cache
        def _best(slot_index: int, desired_index: int) -> tuple[float, tuple[int, ...]]:
            """Return best desired-window indices for remaining physical slots."""
            if slot_index == len(dated_slots):
                return 0.0, ()
            if desired_index == len(desired_windows):
                return float("inf"), ()
            skip_cost, skip_indices = _best(slot_index, desired_index + 1)
            take_rest_cost, take_indices = _best(slot_index + 1, desired_index + 1)
            take_cost = (
                _distance(dated_slots[slot_index], desired_windows[desired_index])
                + take_rest_cost
            )
            if take_cost < skip_cost:
                return take_cost, (desired_index, *take_indices)
            return skip_cost, skip_indices

        _, indices = _best(0, 0)
        return {
            desired_windows[desired_index]: dated_slots[slot_index]
            for slot_index, desired_index in enumerate(indices)
        }

    @staticmethod
    def _remap_observed_mappings_to_physical_reservations(
        persisted: dict[str, Any],
        current_reservations: list[_Reservation],
        actual_slot_names: dict[int, str],
        observed_mapping_keys: set[str],
    ) -> set[str]:
        """Atomically re-key stale Store mappings to current physical occupants."""
        remap: dict[str, str] = {}
        target_counts: dict[str, int] = {}
        for mapping_key in observed_mapping_keys:
            mapping = persisted.get(mapping_key)
            if not isinstance(mapping, dict):
                continue
            matches = [
                res.identity_key
                for res in current_reservations
                if RentalControlCoordinator._physical_mapping_matches_reservation(
                    mapping, res, actual_slot_names
                )
            ]
            if len(matches) != 1:
                continue
            target_key = matches[0]
            remap[mapping_key] = target_key
            target_counts[target_key] = target_counts.get(target_key, 0) + 1

        remap = {
            source: target
            for source, target in remap.items()
            if target_counts.get(target) == 1
        }
        if not remap:
            return observed_mapping_keys

        sources = set(remap)
        safe_remap = {
            source: target
            for source, target in remap.items()
            if target not in persisted
            or target in sources
            or target == source
            or target not in observed_mapping_keys
        }
        if not safe_remap:
            return observed_mapping_keys

        original_items = list(persisted.items())
        rebuilt: dict[str, Any] = {}
        replaced_stale_targets = {
            target
            for source, target in safe_remap.items()
            if target != source and target in persisted and target not in sources
        }
        for source_key, mapping in original_items:
            if source_key in replaced_stale_targets:
                continue
            target_key = safe_remap.get(source_key, source_key)
            if target_key in rebuilt:
                return observed_mapping_keys
            if target_key != source_key:
                history = set(mapping.get("fingerprint_history", []))
                history.add(source_key)
                mapping["fingerprint_history"] = sorted(history)
                identity = mapping.setdefault("identity", {})
                if isinstance(identity, dict):
                    identity["identity_key"] = target_key
            rebuilt[target_key] = mapping

        persisted.clear()
        persisted.update(rebuilt)
        return {safe_remap.get(key, key) for key in observed_mapping_keys}

    def _build_reservations(
        self,
        calendar: list[CalendarEvent],
        managed_slots: list[_ManagedSlot] | None = None,
    ) -> list[_Reservation]:
        """Convert parsed CalendarEvent objects to Reservation objects.

        Produces one :class:`~.reconciliation.Reservation` per calendar
        event that has a usable slot name.  The coordinator's current
        persisted mappings (``_slot_mappings["mappings"]``) are consulted
        to populate :attr:`~.reconciliation.Reservation.fingerprint_history`
        and :attr:`~.reconciliation.Reservation.missing_count`.

        Physical Keymaster observations are used only as current-cycle facts
        for stable-name matching and manual PIN preservation; missing feed
        entries are handled by the stateless planner from physical state.

        Args:
            calendar: Parsed and sorted calendar events from the current
                refresh cycle.
            managed_slots: Optional current physical slot observations.
                When provided, observed names are treated as fresh
                physical facts for rematching stale persisted mappings.

        Returns:
            List of :class:`~.reconciliation.Reservation` objects ready
            for the planner; includes ghost reservations for absent slots.
        """
        prefix = f"{self.event_prefix} " if self.event_prefix else ""
        reservations: list[_Reservation] = []
        observed_slots = managed_slots or []
        consumed_observed_slots: set[int] = set()
        slot_name_counts: dict[str, int] = {}
        slot_name_date_windows: dict[str, set[tuple[datetime, datetime]]] = {}
        slot_name_ordered_windows: dict[str, list[tuple[datetime, datetime]]] = {}
        ordered_calendar = sorted(
            calendar,
            key=lambda event: (
                self._coerce_event_datetime(event.start),
                self._coerce_event_datetime(event.end),
                event.summary or "",
            ),
        )
        for event in ordered_calendar:
            slot_name = get_slot_name(
                event.summary,
                event.description or "",
                self.event_prefix or "",
            )
            if slot_name:
                key = normalize_slot_name_for_fingerprint(slot_name)
                slot_name_counts[key] = slot_name_counts.get(key, 0) + 1
                event_start = self._coerce_event_datetime(event.start)
                event_end = self._coerce_event_datetime(event.end)
                buffered_start_raw, buffered_end_raw = apply_buffer(
                    event_start,
                    event_end,
                    self.code_buffer_before,
                    self.code_buffer_after,
                    self,
                )
                buffered_start_dt = (
                    buffered_start_raw
                    if isinstance(buffered_start_raw, datetime)
                    else event_start
                )
                buffered_end_dt = (
                    buffered_end_raw
                    if isinstance(buffered_end_raw, datetime)
                    else event_end
                )
                slot_name_date_windows.setdefault(key, set()).add(
                    (buffered_start_dt, buffered_end_dt)
                )
                slot_name_ordered_windows.setdefault(key, []).append(
                    (buffered_start_dt, buffered_end_dt)
                )
                active_windows = self._active_checkin_windows_for_name(slot_name)
                if active_windows:
                    slot_name_date_windows.setdefault(key, set()).update(active_windows)

        for event in ordered_calendar:
            slot_name = get_slot_name(
                event.summary,
                event.description or "",
                self.event_prefix or "",
            )
            if not slot_name:
                continue

            start = self._coerce_event_datetime(event.start)
            end = self._coerce_event_datetime(event.end)

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
            display_slot_name = _format_display_slot_name(
                slot_name, prefix, self.trim_names, self.max_name_length
            )

            slot_code = self._generate_slot_code(start, end, event.description, uid)
            code_source = "generated"
            active_windows = self._active_checkin_windows_for_name(slot_name)
            matched_physical = self._find_observed_slot_by_name(
                managed_slots=observed_slots,
                slot_name=slot_name,
                display_slot_name=display_slot_name,
                consumed_slots=consumed_observed_slots,
                desired_start=buffered_start,
                desired_end=buffered_end,
                require_date_match=slot_name_counts.get(
                    normalize_slot_name_for_fingerprint(slot_name), 0
                )
                > 1,
                reserved_date_windows=slot_name_date_windows.get(
                    normalize_slot_name_for_fingerprint(slot_name)
                ),
                ordered_date_windows=slot_name_ordered_windows.get(
                    normalize_slot_name_for_fingerprint(slot_name)
                ),
                block_unknown_date_fallback=bool(
                    active_windows
                    and (buffered_start, buffered_end) not in active_windows
                ),
                expected_name_count=slot_name_counts.get(
                    normalize_slot_name_for_fingerprint(slot_name), 1
                ),
            )
            if matched_physical is not None and matched_physical.actual_code:
                observed_code = matched_physical.actual_code
                observed_start = matched_physical.actual_start
                observed_end = matched_physical.actual_end
                if observed_start is not None and self.code_buffer_before:
                    observed_start = observed_start + timedelta(
                        minutes=self.code_buffer_before
                    )
                if observed_end is not None and self.code_buffer_after:
                    observed_end = observed_end - timedelta(
                        minutes=self.code_buffer_after
                    )
                old_generated = (
                    self._generate_slot_code(
                        observed_start,
                        observed_end,
                        event.description,
                        uid,
                    )
                    if observed_start is not None and observed_end is not None
                    else None
                )
                if old_generated is None:
                    slot_code = observed_code
                    code_source = "manual_observed"
                elif observed_code != old_generated:
                    slot_code = observed_code
                    code_source = "manual_observed"
                elif not self.should_update_code:
                    slot_code = observed_code
                    code_source = "manual_observed"

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
                    fingerprint_history=set(),
                    missing_count=0,
                    code_source=code_source,
                )
                res.sensor_lookup_keys.update(
                    {
                        identity_key,
                        *(uid_aliases or set()),
                    }
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

    def _build_ghost_reservations(
        self,
        current_keys: set[str],
        persisted: dict[str, Any],
        prefix: str,
        observed_mapping_keys: set[str] | None = None,
    ) -> list[_Reservation]:
        """Build synthetic Reservations for assigned slots absent from the feed.

        When a previously-assigned reservation disappears from the calendar
        feed, this method reconstructs a ghost :class:`~.reconciliation.Reservation`
        with an incremented ``missing_count``.  The planner includes it so
        that the slot is retained for up to two consecutive misses (T089);
        on the third miss the ghost is filtered out by
        :func:`~.reconciliation._filter_eligible` and the slot becomes
        clearable.

        Raw PIN values are never stored in the persisted mapping, so the
        ghost ``slot_code`` is always an empty string.

        Args:
            current_keys: Identity keys already built from the current
                calendar feed.  Absent keys are those in *persisted* but
                not in this set.
            persisted: Snapshot of ``_slot_mappings["mappings"]`` passed
                by the caller so that updates made to ``missing_count``
                here are reflected directly in the coordinator's live
                store dict.
            prefix: Computed event-prefix string (e.g. ``"RC "``).
            observed_mapping_keys: Mapping keys whose observed actual
                state came from the current physical Keymaster read.

        Returns:
            List of ghost :class:`~.reconciliation.Reservation` objects
            for assigned slots missing from the current feed.
        """
        ghost_reservations: list[_Reservation] = []

        for key, mapping in persisted.items():
            if key in current_keys:
                continue
            status = mapping.get("status")
            if status not in (SLOT_STATUS_OCCUPIED, SLOT_STATUS_PENDING_SET):
                continue

            new_mc = mapping.get("missing_count", 0) + 1
            mapping["missing_count"] = new_mc

            if status == SLOT_STATUS_PENDING_SET and new_mc >= 3:
                mapping["status"] = SLOT_STATUS_PENDING_CLEAR
                mapping["pending_set_since"] = None
                mapping["pending_clear_since"] = mapping.get(
                    "pending_clear_since", dt.now().isoformat()
                )
                mapping["operation_id"] = None
                mapping["operation_kind"] = OPERATION_KIND_CLEAR
                _LOGGER.debug(
                    "Pending-set ghost %s missed %d cycles; marking pending-clear",
                    key,
                    new_mc,
                )
                continue

            identity = mapping.get("identity", {})
            slot_name: str = identity.get("slot_name", "")
            summary: str = identity.get("summary", "")
            uid_aliases: set[str] = set(identity.get("uid_aliases", []))
            booking_aliases: set[str] = set(identity.get("booking_aliases", []))
            fingerprint_history: set[str] = set(mapping.get("fingerprint_history", []))

            # Recover dates from the last observed Keymaster state.
            last_actual = mapping.get("last_observed_actual", {})
            actual_name = last_actual.get("name_state")
            if (
                observed_mapping_keys is not None
                and key in observed_mapping_keys
                and isinstance(actual_name, str)
                and slot_name
                and normalize_slot_name_for_fingerprint(actual_name)
                not in {
                    normalize_slot_name_for_fingerprint(slot_name),
                    normalize_slot_name_for_fingerprint(
                        _format_display_slot_name(
                            slot_name,
                            prefix,
                            self.trim_names,
                            self.max_name_length,
                        )
                    ),
                }
            ):
                _LOGGER.debug(
                    "Ghost reservation %s: physical name %r differs from "
                    "persisted identity %r; preserving slot as unmatched with "
                    "missing_count=%d",
                    key,
                    actual_name,
                    slot_name,
                    new_mc,
                )
                continue
            start_raw = last_actual.get("start_state")
            end_raw = last_actual.get("end_state")

            if not slot_name or start_raw is None or end_raw is None:
                _LOGGER.debug(
                    "Ghost reservation %s: missing slot_name or dates; slot remains "
                    "fenced with missing_count=%d",
                    key,
                    new_mc,
                )
                continue

            start_dt: datetime | None = (
                dt.parse_datetime(start_raw)
                if isinstance(start_raw, str)
                else start_raw
            )
            end_dt: datetime | None = (
                dt.parse_datetime(end_raw) if isinstance(end_raw, str) else end_raw
            )

            if start_dt is None or end_dt is None or start_dt >= end_dt:
                _LOGGER.debug(
                    "Ghost reservation %s: invalid dates (start=%s end=%s); "
                    "slot remains fenced with missing_count=%d",
                    key,
                    start_raw,
                    end_raw,
                    new_mc,
                )
                continue

            display_slot_name = _format_display_slot_name(
                slot_name, prefix, self.trim_names, self.max_name_length
            )

            try:
                ghost = _Reservation(
                    identity_key=key,
                    start=start_dt,
                    end=end_dt,
                    buffered_start=start_dt,
                    buffered_end=end_dt,
                    summary=summary,
                    slot_name=slot_name,
                    display_slot_name=display_slot_name,
                    slot_code="",  # raw PIN is never stored; no code available
                    uid_aliases=uid_aliases,
                    booking_aliases=booking_aliases,
                    fingerprint_history=fingerprint_history,
                    missing_count=new_mc,
                )
                ghost_reservations.append(ghost)
                _LOGGER.debug(
                    "Ghost reservation %s created with missing_count=%d", key, new_mc
                )
            except ValueError:
                _LOGGER.debug(
                    "Ghost reservation %s: invalid Reservation fields; skipping", key
                )

        return ghost_reservations

    def _sync_slot_store_from_plan(
        self,
        plan: _DesiredPlan,
        res_by_key: dict[str, _Reservation],
        operation_results: list[OperationResult],
    ) -> None:
        """Synchronize cache-only alias and diagnostic metadata from a plan."""
        mappings: dict[str, Any] = self._slot_mappings.setdefault("mappings", {})
        now_str = dt.now().isoformat()
        confirmed_clear_slots = {
            result.slot
            for result in operation_results
            if result.kind == "clear" and result.confirmed
        }
        failed_set_slots = {
            result.slot
            for result in operation_results
            if result.kind == "set" and result.failed
        }
        for identity_key, mapping in list(mappings.items()):
            if mapping.get("slot") in confirmed_clear_slots:
                mappings.pop(identity_key, None)

        for identity_key, slot in plan.selected.items():
            if slot in confirmed_clear_slots:
                continue
            if slot in failed_set_slots:
                continue
            res = res_by_key.get(identity_key)
            if res is None:
                continue
            actual = (
                self.event_overrides.get_actual_state(slot)
                if self.event_overrides is not None
                else None
            ) or {}
            for stale_key in [
                key
                for key, mapping in mappings.items()
                if key != identity_key and mapping.get("slot") == slot
            ]:
                mappings.pop(stale_key, None)
            mappings[identity_key] = {
                "slot": slot,
                "status": "cache",
                "operation_id": None,
                "operation_kind": None,
                "identity": {
                    "identity_key": identity_key,
                    "summary": res.summary,
                    "slot_name": res.slot_name,
                    "start": res.start.isoformat(),
                    "end": res.end.isoformat(),
                    "uid_aliases": sorted(res.uid_aliases),
                    "booking_aliases": sorted(res.booking_aliases),
                },
                "missing_count": 0,
                "pending_set_since": None,
                "pending_clear_since": None,
                "fingerprint_history": sorted(res.fingerprint_history),
                "updated_at": now_str,
                "last_observed_actual": {
                    "slot": slot,
                    "classification": actual.get("classification", "occupied"),
                    "name_state": actual.get("name_state") or res.display_slot_name,
                    "has_code": actual.get("has_code", bool(res.slot_code)),
                    "start_state": (
                        _store_datetime(actual.get("start_state"))
                        or res.buffered_start.isoformat()
                    ),
                    "end_state": _store_datetime(actual.get("end_state"))
                    or res.buffered_end.isoformat(),
                    "use_date_range": actual.get("use_date_range"),
                    "enabled": actual.get("enabled"),
                },
            }

        self._slot_mappings.update(
            {
                "schema_version": STORE_SCHEMA_VERSION,
                "entry_id": self._entry_id,
                "lockname": self.lockname,
                "start_slot": self.start_slot,
                "max_slots": self.max_events,
                "updated_at": now_str,
                "aliases": self._slot_mappings.get("aliases", {}),
                "last_plan": plan.diagnostics,
                "migration_notes": self._slot_mappings.get("migration_notes", []),
            }
        )

        if self.event_overrides is not None:
            self.event_overrides.load_persisted_mappings(mappings)

    def _observe_managed_slots(self) -> list[_ManagedSlot]:
        """Read Keymaster entity states and build ManagedSlot observations.

        Reads Keymaster text, switch, and datetime entities for every slot
        in the managed range to determine the current physical state. Store
        cache contents are deliberately ignored for classification. The
        observed state is also written back to the
        :meth:`~.event_overrides.EventOverrides.update_actual_state`
        cache for diagnostics.

        Returns:
            List of :class:`~.reconciliation.ManagedSlot` instances, one
            per slot in ``start_slot .. start_slot + max_events - 1``.
        """
        if not self.lockname or not self.event_overrides:
            return []

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

            name_empty = _is_blank_keymaster_text(name_state.state)
            code_empty = _is_blank_keymaster_text(code_state.state)
            unreadable = is_unreadable_keymaster_text_state(
                name_state.state
            ) or is_unreadable_keymaster_text_state(code_state.state)

            if unreadable:
                status = _SlotStatus.UNKNOWN
                ms = _ManagedSlot(
                    slot=i,
                    managed=True,
                    status=status,
                    blocked_reason="unreadable",
                )
                slots.append(ms)
                self.event_overrides.update_actual_state(
                    i,
                    {
                        "slot": i,
                        "classification": status.value,
                        "name_state": None,
                        "has_code": None,
                        "start_state": None,
                        "end_state": None,
                        "use_date_range": None,
                        "enabled": None,
                    },
                )
                continue

            name_value = "" if name_empty else name_state.state
            code_value = "" if code_empty else code_state.state
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
            date_range_enabled: bool | None = None
            if use_date_range_state is not None and use_date_range_state.state in (
                "on",
                "off",
            ):
                date_range_enabled = date_range_on
            enabled: bool | None = None
            if enabled_state is not None and enabled_state.state in ("on", "off"):
                enabled = enabled_state.state == "on"

            actual_start: datetime | None = None
            actual_end: datetime | None = None
            if date_range_on:
                if start_dt_state is not None:
                    actual_start = dt.parse_datetime(start_dt_state.state)
                if end_dt_state is not None:
                    actual_end = dt.parse_datetime(end_dt_state.state)

            if has_code:
                status = _SlotStatus.OCCUPIED
                blocked_reason = None
            elif name_value:
                status = _SlotStatus.PHANTOM
                blocked_reason = None
            else:
                status = _SlotStatus.FREE
                blocked_reason = None

            ms = _ManagedSlot(
                slot=i,
                managed=True,
                status=status,
                actual_name=name_value or None,
                actual_code=code_value or None,
                actual_code_present=has_code,
                actual_start=actual_start,
                actual_end=actual_end,
                date_range_enabled=date_range_enabled,
                enabled=enabled,
                persisted_identity_key=None,
                blocked_reason=blocked_reason,
                preserve_unmatched=False,
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
                    "use_date_range": date_range_enabled,
                    "enabled": enabled,
                },
            )

        return slots

    def _apply_checkin_protection(
        self,
        reservations: list[_Reservation],
        managed_slots: list[_ManagedSlot] | None = None,
    ) -> None:
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
        tracked_start = RentalControlCoordinator._coerce_checkin_datetime(
            attrs.get("start")
        )
        tracked_end = RentalControlCoordinator._coerce_checkin_datetime(
            attrs.get("end")
        )

        name_matches = [res for res in reservations if res.slot_name == guest_name]
        exact_matches = [
            res
            for res in name_matches
            if tracked_start is not None
            and tracked_end is not None
            and res.start == tracked_start
            and res.end == tracked_end
        ]
        matches = exact_matches
        if tracked_start is None or tracked_end is None:
            matches = name_matches if len(name_matches) == 1 else []
        for res in matches[:1]:
            if sensor_state == CHECKIN_STATE_CHECKED_IN:
                res.protected_active = True
            elif sensor_state == CHECKIN_STATE_CHECKED_OUT:
                res.checked_out = True
            return

        if (
            sensor_state == CHECKIN_STATE_CHECKED_IN
            and tracked_start is not None
            and tracked_end is not None
            and managed_slots
        ):
            prefix = f"{self.event_prefix} " if self.event_prefix else ""
            display_slot_name = _format_display_slot_name(
                guest_name, prefix, self.trim_names, self.max_name_length
            )
            buffered_start_raw, buffered_end_raw = apply_buffer(
                tracked_start,
                tracked_end,
                self.code_buffer_before,
                self.code_buffer_after,
                self,
            )
            buffered_start = (
                buffered_start_raw
                if isinstance(buffered_start_raw, datetime)
                else tracked_start
            )
            buffered_end = (
                buffered_end_raw
                if isinstance(buffered_end_raw, datetime)
                else tracked_end
            )
            matched_physical = self._find_observed_slot_by_name(
                managed_slots,
                guest_name,
                display_slot_name,
                desired_start=buffered_start,
                desired_end=buffered_end,
            )
            same_name_slots = [
                slot
                for slot in managed_slots
                if slot.managed
                and slot.status is _SlotStatus.OCCUPIED
                and self._physical_slot_name_matches_name(
                    slot.actual_name, guest_name, display_slot_name
                )
            ]
            if matched_physical is None:
                return
            if (
                matched_physical.actual_start is not None
                and matched_physical.actual_end is not None
                and (
                    matched_physical.actual_start != buffered_start
                    or matched_physical.actual_end != buffered_end
                )
                and len(same_name_slots) != 1
            ):
                return
            identity_key = make_reservation_fingerprint(
                self._entry_id, guest_name, tracked_start, tracked_end
            )
            slot_code = matched_physical.actual_code or self._generate_slot_code(
                tracked_start, tracked_end, None, None
            )
            protected = _Reservation(
                identity_key=identity_key,
                start=tracked_start,
                end=tracked_end,
                buffered_start=buffered_start,
                buffered_end=buffered_end,
                summary=str(attrs.get("summary") or guest_name),
                slot_name=guest_name,
                display_slot_name=display_slot_name,
                slot_code=slot_code,
                protected_active=True,
                code_source=(
                    "manual_observed"
                    if matched_physical.actual_code is not None
                    else "generated"
                ),
            )
            protected.sensor_lookup_keys.add(identity_key)
            reservations.append(protected)

    @staticmethod
    def _coerce_checkin_datetime(value: Any) -> datetime | None:
        """Return a datetime from check-in sensor attributes."""
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            parsed = dt.parse_datetime(value)
            return parsed if isinstance(parsed, datetime) else None
        return None

    def _coerce_event_datetime(self, value: date | datetime) -> datetime:
        """Return a timezone-aware datetime for calendar date/datetime values."""
        if isinstance(value, datetime):
            return value
        return datetime.combine(value, time.min, self.timezone)

    def _combine_event_time(
        self, value: date | datetime, selected_time: time
    ) -> datetime:
        """Return an event-date datetime using the selected local time."""
        event_date = value.date() if isinstance(value, datetime) else value
        return cast(
            "datetime",
            dt.as_utc(datetime.combine(event_date, selected_time, self.timezone)),
        )

    def _datetimes_match(self, left: datetime, right: datetime) -> bool:
        """Return whether two datetimes represent the same instant."""
        left_local = left.replace(tzinfo=self.timezone) if left.tzinfo is None else left
        right_local = (
            right.replace(tzinfo=self.timezone) if right.tzinfo is None else right
        )
        left_utc = cast("datetime", dt.as_utc(left_local))
        right_utc = cast("datetime", dt.as_utc(right_local))
        return left_utc == right_utc

    def _physical_override_time(self, value: datetime, *, start: bool) -> time:
        """Return the unbuffered local time represented by a physical slot time."""
        local_value = (
            value.replace(tzinfo=self.timezone)
            if value.tzinfo is None
            else value.astimezone(self.timezone)
        )
        if start and self.code_buffer_before:
            local_value += timedelta(minutes=self.code_buffer_before)
        if not start and self.code_buffer_after:
            local_value -= timedelta(minutes=self.code_buffer_after)
        return local_value.time()

    def _buffer_aware_override_times(
        self,
        event_start: date | datetime,
        event_end: date | datetime,
        expected_checkin: time,
        expected_checkout: time,
        override: Mapping[str, Any],
    ) -> tuple[time, time]:
        """Return manual override times only when physical times truly differ.

        Keymaster stores already-buffered datetimes.  A physical time that
        matches the expected calendar/default window after applying buffers is
        system-managed and must not freeze future Honor Event Times changes.
        """
        expected_start = self._combine_event_time(event_start, expected_checkin)
        expected_end = self._combine_event_time(event_end, expected_checkout)
        buffered_start_raw, buffered_end_raw = apply_buffer(
            expected_start,
            expected_end,
            self.code_buffer_before,
            self.code_buffer_after,
            self,
        )
        buffered_start = (
            buffered_start_raw
            if isinstance(buffered_start_raw, datetime)
            else expected_start
        )
        buffered_end = (
            buffered_end_raw if isinstance(buffered_end_raw, datetime) else expected_end
        )

        checkin = expected_checkin
        checkout = expected_checkout
        override_start = override.get("start_time")
        override_end = override.get("end_time")
        if isinstance(override_start, datetime) and not self._datetimes_match(
            override_start, buffered_start
        ):
            checkin = self._physical_override_time(override_start, start=True)
        if isinstance(override_end, datetime) and not self._datetimes_match(
            override_end, buffered_end
        ):
            checkout = self._physical_override_time(override_end, start=False)
        return checkin, checkout

    def _must_defer_for_checkin_restore(
        self, reservations: list[_Reservation], managed_slots: list[_ManagedSlot]
    ) -> bool:
        """Return whether apply should wait for check-in sensor restore."""
        domain_data: dict[str, Any] | None = self.hass.data.get(DOMAIN)
        if domain_data is None or self._entry_id not in domain_data:
            return False
        if not self._checkin_restore_pending:
            return False
        entry_data: dict[str, Any] = domain_data.get(self._entry_id, {})
        if entry_data.get(CHECKIN_SENSOR) is not None:
            return False
        return any(
            slot.managed
            and slot.status is _SlotStatus.OCCUPIED
            and (
                not any(
                    self._physical_slot_name_matches_reservation(slot.actual_name, res)
                    for res in reservations
                )
                or (
                    (
                        slot.actual_start is None
                        or slot.actual_end is None
                        or not any(
                            slot.actual_start == res.buffered_start
                            and slot.actual_end == res.buffered_end
                            for res in reservations
                            if self._physical_slot_name_matches_reservation(
                                slot.actual_name, res
                            )
                        )
                    )
                    and any(
                        self._physical_slot_name_matches_reservation(
                            slot.actual_name, res
                        )
                        for res in reservations
                    )
                )
            )
            for slot in managed_slots
        )

    def _physical_slot_name_matches_name(
        self, actual_name: str | None, slot_name: str, display_slot_name: str
    ) -> bool:
        """Return whether a physical display name matches a logical name."""
        if not actual_name:
            return False
        prefix = f"{self.event_prefix} " if self.event_prefix else ""
        actual_forms = {normalize_slot_name_for_fingerprint(actual_name)}
        if prefix and actual_name.startswith(prefix):
            actual_forms.add(
                normalize_slot_name_for_fingerprint(actual_name[len(prefix) :])
            )
        desired_forms = {
            normalize_slot_name_for_fingerprint(slot_name),
            normalize_slot_name_for_fingerprint(display_slot_name),
        }
        return bool(actual_forms & desired_forms)

    def _physical_slot_name_matches_reservation(
        self, actual_name: str | None, reservation: _Reservation
    ) -> bool:
        """Return whether a physical display name matches a reservation name."""
        return self._physical_slot_name_matches_name(
            actual_name, reservation.slot_name, reservation.display_slot_name
        )

    def _active_checkin_windows_for_name(
        self, slot_name: str
    ) -> set[tuple[datetime, datetime]]:
        """Return active check-in windows that physical slots must reserve."""
        domain_data: dict[str, Any] | None = self.hass.data.get(DOMAIN)
        entry_data: dict[str, Any] = (
            domain_data.get(self._entry_id, {}) if domain_data is not None else {}
        )
        checkin_sensor = entry_data.get(CHECKIN_SENSOR)
        if checkin_sensor is None or checkin_sensor.state != CHECKIN_STATE_CHECKED_IN:
            return set()
        attrs: dict[str, Any] = checkin_sensor.extra_state_attributes
        if attrs.get("guest_name") != slot_name:
            return set()
        tracked_start = RentalControlCoordinator._coerce_checkin_datetime(
            attrs.get("start")
        )
        tracked_end = RentalControlCoordinator._coerce_checkin_datetime(
            attrs.get("end")
        )
        if tracked_start is None or tracked_end is None:
            return set()
        buffered_start_raw, buffered_end_raw = apply_buffer(
            tracked_start,
            tracked_end,
            self.code_buffer_before,
            self.code_buffer_after,
            self,
        )
        buffered_start = (
            buffered_start_raw
            if isinstance(buffered_start_raw, datetime)
            else tracked_start
        )
        buffered_end = (
            buffered_end_raw if isinstance(buffered_end_raw, datetime) else tracked_end
        )
        return {(tracked_start, tracked_end), (buffered_start, buffered_end)}

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
                observed_slots = self._observe_managed_slots()
                reservations = self._build_reservations(new_calendar, observed_slots)
                self._apply_checkin_protection(reservations, observed_slots)

                plan_id = str(uuid.uuid4())
                plan = compute_desired_plan(
                    reservations=reservations,
                    managed_slots=observed_slots,
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
                if self._must_defer_for_checkin_restore(reservations, observed_slots):
                    _LOGGER.warning(
                        "Deferring reconciliation for %s until check-in state is "
                        "available; same-name physical slot has missing date state",
                        self._name,
                    )
                    operation_results = []
                else:
                    operation_results = await self.event_overrides.async_apply_plan(
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
                persisted = self._slot_mappings.get("mappings", {})
                if persisted:
                    try:
                        self.event_overrides.load_persisted_mappings(persisted)
                    except ValueError:
                        _LOGGER.exception(
                            "Failed to load persisted slot mappings for %s",
                            self._entry_id,
                        )
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

        buffer_changed = (
            self.code_buffer_before != previous_buffer_before
            or self.code_buffer_after != previous_buffer_after
        )
        if buffer_changed:
            await self._async_update_buffer_times(
                previous_buffer_before, previous_buffer_after
            )
        await self.async_request_refresh()

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
            start_entity = (
                f"{DATETIME}.{self.lockname}_code_slot_{slot}_date_range_start"
            )
            end_entity = f"{DATETIME}.{self.lockname}_code_slot_{slot}_date_range_end"
            self.event_overrides.suppress_state_changes(
                slot,
                {
                    start_entity: (
                        buffered_start.isoformat()
                        if isinstance(buffered_start, datetime)
                        else buffered_start
                    ),
                    end_entity: (
                        buffered_end.isoformat()
                        if isinstance(buffered_end, datetime)
                        else buffered_end
                    ),
                },
            )
            coro: list[Coroutine] = []
            coro = add_call(
                self.hass,
                coro,
                DATETIME,
                "set_value",
                end_entity,
                {"datetime": buffered_end},
            )
            coro = add_call(
                self.hass,
                coro,
                DATETIME,
                "set_value",
                start_entity,
                {"datetime": buffered_start},
            )
            results = await asyncio.gather(*coro, return_exceptions=True)
            check_gather_results(
                results,
                f"Buffer time update slot {slot} ({self.lockname})",
                _LOGGER,
            )
            override["start_time"] = (
                buffered_start if isinstance(buffered_start, datetime) else start
            )
            override["end_time"] = (
                buffered_end if isinstance(buffered_end, datetime) else end
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
                    expected_checkin = (
                        desc_checkin if desc_checkin is not None else self.checkin
                    )
                    expected_checkout = (
                        desc_checkout if desc_checkout is not None else self.checkout
                    )

                    if override:
                        # Priority 3 fallback for genuine manual overrides.
                        # Override timestamps are physical Keymaster dates,
                        # which already include lock-code buffers.
                        event_end_dt = (
                            event["DTEND"].dt
                            if "DTEND" in event
                            else event["DTSTART"].dt
                        )
                        checkin, checkout = self._buffer_aware_override_times(
                            event["DTSTART"].dt,
                            event_end_dt,
                            expected_checkin,
                            expected_checkout,
                            override,
                        )
                        if desc_checkin is not None:
                            checkin = desc_checkin
                        if desc_checkout is not None:
                            checkout = desc_checkout
                    else:
                        # Priority 4 fallback for missing description times
                        checkin = expected_checkin
                        checkout = expected_checkout
                elif override:
                    # FR-005 (disabled) or FR-004 (all-day with override)
                    # Override timestamps are sourced from Keymaster's
                    # physical, already-buffered date range.
                    checkin = self._physical_override_time(
                        override["start_time"], start=True
                    )
                    checkout = self._physical_override_time(
                        override["end_time"], start=False
                    )
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
