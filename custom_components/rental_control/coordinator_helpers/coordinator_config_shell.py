# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Coordinator shell mixins for behavior-preserving delegation."""

# mypy: disable-error-code="attr-defined, has-type, var-annotated, misc, no-redef"

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from collections.abc import Mapping
from datetime import date
from datetime import datetime
from datetime import time
from datetime import timedelta
from datetime import tzinfo
import importlib
import logging
from typing import Any
from typing import cast
from zoneinfo import ZoneInfo  # noreorder

from homeassistant.components.datetime import DOMAIN as DATETIME
from homeassistant.const import CONF_NAME
from homeassistant.const import CONF_URL
from homeassistant.const import CONF_VERIFY_SSL
import homeassistant.helpers.config_validation as cv

from .. import util as _util
from ..bookings_only import feed_is_bookings_only
from ..const import CONF_CHECKIN
from ..const import CONF_CHECKOUT
from ..const import CONF_CODE_BUFFER_AFTER
from ..const import CONF_CODE_BUFFER_BEFORE
from ..const import CONF_CODE_GENERATION
from ..const import CONF_CODE_LENGTH
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
from ..const import DEFAULT_MAX_NAME_LENGTH
from ..const import DEFAULT_TRIM_NAMES
from ..event_overrides import EventOverrides
from ..helpers import configured_lock_slug
from ..keymaster_operations import persisted_owner_identity_for_slot
from ..keymaster_operations import prepare_verified_intent
from ..keymaster_operations import protected_registry_active
from ..keymaster_operations import verified_lifecycle_active
from ..keymaster_runtime import coordinator_keymaster_runtime_enabled
from ..keymaster_runtime import explicitly_lockless
from ..keymaster_slot_operations import async_slot_operation
from ..util import apply_buffer
from ..util import check_gather_results
from . import config_update

_LOGGER = logging.getLogger(__name__)


def _coordinator_module() -> Any:
    """Return the public coordinator module for patched compatibility."""
    return importlib.import_module("custom_components.rental_control.coordinator")


class CoordinatorConfigMixin:
    """Provide extracted coordinator shell behavior."""

    # Declared to match the other bases: CoordinatorSetupMixin owns the
    # tzinfo-typed timezone and DataUpdateCoordinator the interval.
    timezone: tzinfo
    update_interval: timedelta | None

    async def _update_config_impl(self, config: Mapping[str, Any]) -> None:
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
            None if explicitly_lockless(config) else configured_lock_slug(lockname_raw)
        )
        self.max_events = int(str(config.get(CONF_MAX_EVENTS)))
        self.start_slot = int(str(config.get(CONF_START_SLOT)))
        # Keep event_overrides in sync with config changes
        await self._sync_lock_config(
            previous_lockname, previous_max_events, previous_start_slot
        )
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
            self.event_overrides.event_prefix = self.event_prefix
        self.code_length = config.get(CONF_CODE_LENGTH, DEFAULT_CODE_LENGTH)
        self.ignore_non_reserved = bool(config.get(CONF_IGNORE_NON_RESERVED))
        self.bookings_only_feed = feed_is_bookings_only(config)
        self.verify_ssl = bool(config.get(CONF_VERIFY_SSL))

        if config_update.buffer_changed(
            self.code_buffer_before,
            self.code_buffer_after,
            previous_buffer_before,
            previous_buffer_after,
        ):
            await self._async_update_buffer_times(
                previous_buffer_before, previous_buffer_after
            )
        await self.async_request_refresh()

    async def _sync_lock_config(
        self,
        previous_lockname: str | None,
        previous_max_events: int,
        previous_start_slot: int,
    ) -> None:
        """Re-sync event overrides and lock discovery after a config change."""
        if explicitly_lockless(self.config_entry.data):
            self.event_overrides = None
            self._parent_entry_id = None
            self._child_locknames = set()
            return
        if not coordinator_keymaster_runtime_enabled(self):
            return
        lockname_changed = self.lockname != previous_lockname
        if not self.lockname:
            self.event_overrides = None
            self._parent_entry_id: str | None = None
            self._child_locknames: set[str] = set()
            return
        # Reset child lock discovery before any awaits to avoid
        # stale parent/children during concurrent refreshes.
        if lockname_changed:
            self._parent_entry_id: str | None = None
            self._child_locknames: set[str] = set()
        overrides_stale = config_update.overrides_are_stale(
            self.event_overrides is None,
            lockname_changed,
            self.max_events,
            previous_max_events,
            self.start_slot,
            previous_start_slot,
        )
        if overrides_stale:
            self.event_overrides = EventOverrides(self.start_slot, self.max_events)
            await self.async_setup_keymaster_overrides()
            persisted = self._slot_mappings.get("mappings", {})
            if persisted:
                try:
                    self.event_overrides.load_persisted_mappings(persisted)
                except ValueError:
                    _LOGGER.warning("Failed to load persisted slot mappings")
        # Re-discover parent entry and children on lockname change
        if lockname_changed:
            self._parent_entry_id = self._find_parent_entry_id()
            if self._parent_entry_id is not None:
                self._child_locknames = self._discover_child_locks()

    async def _async_update_buffer_times(self, old_before: int, old_after: int) -> None:
        """Re-apply buffer to all assigned slots after config change.

        Override times are sourced from Keymaster entities which store
        already-buffered values.  To avoid double-buffering, reverse
        the previous buffer before applying the new one.
        """
        if not coordinator_keymaster_runtime_enabled(self):
            return
        verified = verified_lifecycle_active(self)
        if not self.event_overrides or (not verified and not self.lockname):
            return
        for slot in range(self.start_slot, self.start_slot + self.max_events):
            override = self.event_overrides.overrides.get(slot)
            if override is None:
                continue
            if verified:
                await _async_update_verified_buffer_slot(
                    self, slot, old_before, old_after
                )
                continue
            if protected_registry_active(self.hass):
                _LOGGER.warning(
                    "Date update skipped for slot %s because verified "
                    "Keymaster identity is unavailable",
                    slot,
                )
                continue
            await _async_update_legacy_buffer_slot(self, slot, old_before, old_after)


def _buffered_override_window(
    coordinator: Any,
    override: Mapping[str, Any],
    old_before: int,
    old_after: int,
) -> tuple[date | datetime, date | datetime, date | datetime, date | datetime]:
    """Return original and newly buffered datetimes for one slot."""
    start, end = config_update.unbuffer_window(
        override["start_time"], override["end_time"], old_before, old_after
    )
    buffered_start, buffered_end = apply_buffer(
        start,
        end,
        coordinator.code_buffer_before,
        coordinator.code_buffer_after,
        coordinator,
    )
    return start, end, buffered_start, buffered_end


async def _async_update_verified_buffer_slot(
    coordinator: Any,
    slot: int,
    old_before: int,
    old_after: int,
) -> None:
    """Serialize date-fence preparation through final confirmation."""
    async with async_slot_operation(coordinator, slot):
        await _async_update_verified_buffer_slot_locked(
            coordinator, slot, old_before, old_after
        )


async def _async_update_verified_buffer_slot_locked(
    coordinator: Any,
    slot: int,
    old_before: int,
    old_after: int,
) -> None:
    """Commit, dispatch, and confirm a protected exact-identity date update."""
    current_override = coordinator.event_overrides.overrides.get(slot)
    if current_override is None:
        return
    expected_generation = coordinator.event_overrides.get_feedback_generation(slot)
    override_snapshot = dict(current_override)
    owner_identity_key = persisted_owner_identity_for_slot(coordinator, slot)
    if owner_identity_key is None:
        return
    intent = await prepare_verified_intent(
        coordinator,
        slot,
        owner_identity_key,
        (
            f"text.code_slots:{slot}.name",
            f"switch.code_slots:{slot}.accesslimit_date_range_enabled",
            f"datetime.code_slots:{slot}.accesslimit_date_range_start",
            f"datetime.code_slots:{slot}.accesslimit_date_range_end",
        ),
        "allocation",
    )
    if intent is None:
        return
    _start, _end, buffered_start_value, buffered_end_value = _buffered_override_window(
        coordinator, override_snapshot, old_before, old_after
    )
    buffered_start = _as_coordinator_datetime(buffered_start_value, coordinator)
    buffered_end = _as_coordinator_datetime(buffered_end_value, coordinator)
    start_entity = intent.entity_id(
        f"datetime.code_slots:{slot}.accesslimit_date_range_start"
    )
    end_entity = intent.entity_id(
        f"datetime.code_slots:{slot}.accesslimit_date_range_end"
    )
    coordinator.event_overrides.suppress_state_changes(
        slot,
        {
            start_entity: buffered_start.isoformat(),
            end_entity: buffered_end.isoformat(),
        },
    )
    try:
        await intent.async_call(
            f"datetime.code_slots:{slot}.accesslimit_date_range_end",
            "set_value",
            {"datetime": buffered_end},
        )
        await intent.async_call(
            f"datetime.code_slots:{slot}.accesslimit_date_range_start",
            "set_value",
            {"datetime": buffered_start},
        )
    except Exception:
        _LOGGER.warning("Verified date update did not complete for slot %s", slot)
        return
    if not await _confirm_verified_buffer_window(
        coordinator, intent, start_entity, end_entity, buffered_start, buffered_end
    ):
        return
    if await intent.async_is_current():
        await coordinator.event_overrides.async_update_times(
            slot,
            buffered_start,
            buffered_end,
            expected_generation=expected_generation,
            expected_times=(
                override_snapshot["start_time"],
                override_snapshot["end_time"],
            ),
            guard=intent.is_current,
        )


def _as_coordinator_datetime(value: date | datetime, coordinator: Any) -> datetime:
    """Convert date-only buffers to local midnight for HA datetime services."""
    if isinstance(value, datetime):
        return value
    return datetime.combine(value, time.min, tzinfo=coordinator.timezone)


async def _confirm_verified_buffer_window(
    coordinator: Any,
    intent: Any,
    start_entity: str,
    end_entity: str,
    buffered_start: datetime,
    buffered_end: datetime,
) -> bool:
    """Confirm both current date values against the same still-current intent."""
    end_matches = await _util._async_wait_for_expected_datetime(
        coordinator.hass,
        end_entity,
        buffered_end,
        _util._SET_CODE_CONFIRMATION_TIMEOUT,
        intent=intent,
    )
    if not end_matches:
        return False
    return await _util._async_wait_for_expected_datetime(
        coordinator.hass,
        start_entity,
        buffered_start,
        _util._SET_CODE_CONFIRMATION_TIMEOUT,
        intent=intent,
    )


async def _async_update_legacy_buffer_slot(
    coordinator: Any,
    slot: int,
    old_before: int,
    old_after: int,
) -> None:
    """Preserve existing slug-based date update behavior for legacy entries."""
    current_override = coordinator.event_overrides.overrides.get(slot)
    if current_override is None:
        return
    expected_generation = coordinator.event_overrides.get_feedback_generation(slot)
    override_snapshot = dict(current_override)
    start, end, buffered_start, buffered_end = _buffered_override_window(
        coordinator, override_snapshot, old_before, old_after
    )
    start_entity = (
        f"{DATETIME}.{coordinator.lockname}_code_slot_{slot}_date_range_start"
    )
    end_entity = f"{DATETIME}.{coordinator.lockname}_code_slot_{slot}_date_range_end"
    coordinator.event_overrides.suppress_state_changes(
        slot,
        {
            start_entity: buffered_start.isoformat(),
            end_entity: buffered_end.isoformat(),
        },
    )
    calls: list[Coroutine] = []
    calls = _coordinator_module().add_call(
        coordinator.hass,
        calls,
        DATETIME,
        "set_value",
        end_entity,
        {"datetime": buffered_end},
    )
    calls = _coordinator_module().add_call(
        coordinator.hass,
        calls,
        DATETIME,
        "set_value",
        start_entity,
        {"datetime": buffered_start},
    )
    results = await asyncio.gather(*calls, return_exceptions=True)
    check_gather_results(
        results,
        f"Buffer time update slot {slot} ({coordinator.lockname})",
        _LOGGER,
    )
    updated_start = (
        buffered_start
        if isinstance(buffered_start, datetime)
        else cast(datetime, start)
    )
    updated_end = (
        buffered_end if isinstance(buffered_end, datetime) else cast(datetime, end)
    )
    await coordinator.event_overrides.async_update_times(
        slot,
        updated_start,
        updated_end,
        expected_generation=expected_generation,
        expected_times=(
            override_snapshot["start_time"],
            override_snapshot["end_time"],
        ),
    )
