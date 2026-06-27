# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Coordinator shell mixins for behavior-preserving delegation."""

# mypy: disable-error-code="attr-defined, has-type, var-annotated, misc, no-redef"

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from collections.abc import Mapping
from datetime import datetime
from datetime import timedelta
import importlib
import logging
from typing import Any
from zoneinfo import ZoneInfo  # noreorder

from homeassistant.components.datetime import DOMAIN as DATETIME
from homeassistant.const import CONF_NAME
from homeassistant.const import CONF_URL
from homeassistant.const import CONF_VERIFY_SSL
import homeassistant.helpers.config_validation as cv
from homeassistant.util import slugify

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
from ..util import apply_buffer
from ..util import check_gather_results
from . import config_update

_LOGGER = logging.getLogger(__name__)


def _coordinator_module() -> Any:
    """Return the public coordinator module for patched compatibility."""
    return importlib.import_module("custom_components.rental_control.coordinator")


class CoordinatorConfigMixin:
    """Provide extracted coordinator shell behavior."""

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
            slugify(lockname_raw) if lockname_raw and lockname_raw.strip() else None
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
                    _LOGGER.exception(
                        "Failed to load persisted slot mappings for %s",
                        self._entry_id,
                    )
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
        if not self.event_overrides or not self.lockname:
            return
        for slot in range(self.start_slot, self.start_slot + self.max_events):
            override = self.event_overrides.overrides.get(slot)
            if override is None:
                continue
            # Reverse the old buffer to recover unbuffered times
            start, end = config_update.unbuffer_window(
                override["start_time"], override["end_time"], old_before, old_after
            )
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
            coro = _coordinator_module().add_call(
                self.hass,
                coro,
                DATETIME,
                "set_value",
                end_entity,
                {"datetime": buffered_end},
            )
            coro = _coordinator_module().add_call(
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
