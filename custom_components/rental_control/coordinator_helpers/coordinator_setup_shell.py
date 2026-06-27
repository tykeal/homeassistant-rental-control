# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Coordinator shell mixins for behavior-preserving delegation."""

# mypy: disable-error-code="attr-defined, has-type, var-annotated, misc, no-redef"

from __future__ import annotations

from collections import deque
from datetime import time
from datetime import timedelta
import importlib
import logging
from typing import Any
from zoneinfo import ZoneInfo  # noreorder

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
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.storage import Store
from homeassistant.util import dt
from homeassistant.util import slugify

from ..const import CONF_CHECKIN
from ..const import CONF_CHECKOUT
from ..const import CONF_CODE_BUFFER_AFTER
from ..const import CONF_CODE_BUFFER_BEFORE
from ..const import CONF_CODE_GENERATION
from ..const import CONF_CODE_LENGTH
from ..const import CONF_CREATION_DATETIME
from ..const import CONF_DAYS
from ..const import CONF_EVENT_PREFIX
from ..const import CONF_HONOR_EVENT_TIMES
from ..const import CONF_IGNORE_NON_RESERVED
from ..const import CONF_LOCK_ENTRY
from ..const import CONF_MAX_EVENTS
from ..const import CONF_MAX_NAME_LENGTH
from ..const import CONF_REFRESH_FREQUENCY
from ..const import CONF_SHOULD_UPDATE_CODE
from ..const import CONF_START_SLOT
from ..const import CONF_TIMEZONE
from ..const import CONF_TRIM_NAMES
from ..const import DEFAULT_CODE_BUFFER_AFTER
from ..const import DEFAULT_CODE_BUFFER_BEFORE
from ..const import DEFAULT_CODE_GENERATION
from ..const import DEFAULT_CODE_LENGTH
from ..const import DEFAULT_MAX_MISSES
from ..const import DEFAULT_MAX_NAME_LENGTH
from ..const import DEFAULT_REFRESH_FREQUENCY
from ..const import DEFAULT_TRIM_NAMES
from ..const import DOMAIN
from ..const import LOCK_MANAGER
from ..const import VERSION
from ..event_overrides import EventOverrides
from ..reconciliation import DesiredPlan as _DesiredPlan
from ..reconciliation import Reservation as _Reservation
from . import keymaster_bootstrap
from .models import CalendarParseContext
from .models import KeymasterSlotSnapshot
from .models import ReservationBuildContext

_LOGGER = logging.getLogger(__name__)


def _coordinator_module() -> Any:
    """Return the public coordinator module for patched compatibility."""
    return importlib.import_module("custom_components.rental_control.coordinator")


class CoordinatorSetupMixin:
    """Provide extracted coordinator shell behavior."""

    async def _async_setup_keymaster_overrides_impl(self) -> None:
        """Bootstrap Keymaster slot overrides on first load."""
        if not self.lockname:
            return

        default_start = dt.start_of_local_day()
        default_end = dt.start_of_local_day() + timedelta(days=1)
        for i in range(self.start_slot, self.start_slot + self.max_events):
            snapshot = self._read_slot_snapshot(i)
            decision = keymaster_bootstrap.plan_bootstrap_slot(
                snapshot, default_start, default_end
            )
            if decision.force_clear:
                try:
                    await _coordinator_module().async_fire_clear_code(self, i)
                except Exception:
                    _LOGGER.exception(
                        "Failed to force-reset partially-cleared slot %d", i
                    )
            update = decision.override_update
            if update is None:
                continue
            await self.update_event_overrides(
                update.slot,
                update.slot_code,
                update.slot_name,
                update.start_time,
                update.end_time,
                request_refresh=False,
            )

    def _init_config_state(self, config_entry: ConfigEntry) -> None:
        """Initialize instance attributes from the config entry."""
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
            self.event_overrides.event_prefix = self.event_prefix
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

    def _register_keymaster_device(self, hass: HomeAssistant) -> None:
        """Register the device and warn on incompatible Keymaster versions."""
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

    def _state_value(self, entity_id: str) -> str | None:
        """Return the HA state string for an entity, or None when absent."""
        state = self.hass.states.get(entity_id)
        return state.state if state is not None else None

    def _read_slot_snapshot(self, slot: int) -> KeymasterSlotSnapshot:
        """Read all Keymaster entity states for a slot into a snapshot."""
        base = f"{self.lockname}_code_slot_{slot}"
        return KeymasterSlotSnapshot(
            slot=slot,
            name_state=self._state_value(f"{TEXT}.{base}_name"),
            pin_state=self._state_value(f"{TEXT}.{base}_pin"),
            use_date_range_state=self._state_value(
                f"{SWITCH}.{base}_use_date_range_limits"
            ),
            enabled_state=self._state_value(f"{SWITCH}.{base}_enabled"),
            start_state=self._state_value(f"{DATETIME}.{base}_date_range_start"),
            end_state=self._state_value(f"{DATETIME}.{base}_date_range_end"),
        )

    def _reservation_build_context(self) -> ReservationBuildContext:
        """Return the pure context for reservation building."""
        return ReservationBuildContext(
            entry_id=self._entry_id,
            timezone=self.timezone,
            event_prefix=self.event_prefix,
            trim_names=self.trim_names,
            max_name_length=self.max_name_length,
            code_buffer_before=self.code_buffer_before,
            code_buffer_after=self.code_buffer_after,
            should_update_code=self.should_update_code,
            code_generator=self.code_generator,
            code_length=self.code_length,
            active_windows_for_name=self._active_checkin_windows_for_name,
        )

    def _calendar_parse_context(self) -> CalendarParseContext:
        """Return the pure context for iCal parsing."""
        lookup = (
            self.event_overrides.get_slot_with_name
            if self.event_overrides is not None
            else None
        )
        return CalendarParseContext(
            timezone=self.timezone,
            checkin=self.checkin,
            checkout=self.checkout,
            event_prefix=self.event_prefix,
            ignore_non_reserved=self.ignore_non_reserved,
            honor_event_times=self.honor_event_times,
            code_buffer_before=self.code_buffer_before,
            code_buffer_after=self.code_buffer_after,
            override_lookup=lookup,
        )
