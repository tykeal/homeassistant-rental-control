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

from collections.abc import Mapping
from datetime import datetime
from datetime import timedelta
import logging
from typing import Any
from typing import cast

from homeassistant.components.calendar import CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.storage import Store as Store  # noqa: F401
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN
from .coordinator_helpers import diagnostics
from .coordinator_helpers import slot_matching
from .coordinator_helpers.coordinator_checkin_shell import CoordinatorCheckinMixin
from .coordinator_helpers.coordinator_config_shell import CoordinatorConfigMixin
from .coordinator_helpers.coordinator_refresh_shell import CoordinatorRefreshMixin
from .coordinator_helpers.coordinator_reservation_shell import (
    CoordinatorReservationMixin,
)
from .coordinator_helpers.coordinator_setup_shell import CoordinatorSetupMixin
from .coordinator_helpers.coordinator_store_shell import CoordinatorStoreMixin
from .coordinator_helpers.models import EventOverrideUpdate
from .coordinator_helpers.models import ObservedSlotQuery
from .coordinator_helpers.models import (
    _format_display_slot_name as _format_display_slot_name,  # noqa: F401
)
from .coordinator_helpers.models import normalize_event_override_update
from .reconciliation import DesiredPlan as _DesiredPlan
from .reconciliation import ManagedSlot as _ManagedSlot
from .reconciliation import compute_desired_plan as compute_desired_plan  # noqa: F401

# aislop-ignore-file ai-slop/hallucinated-import -- Provided by Home Assistant runtime.

_LOGGER = logging.getLogger(__name__)


def _util_module() -> Any:
    """Return the util module for runtime patch-sensitive delegation."""
    from . import util

    return util


def add_call(
    hass: Any,
    coro: list[Any],
    domain: str,
    service: str,
    target: str,
    data: dict[str, Any],
) -> Any:
    """Delegate service-call collection through util at runtime."""
    return _util_module().add_call(hass, coro, domain, service, target, data)


async def async_fire_clear_code(
    coordinator: Any, slot: int, expected_name: str | None = None
) -> Any:
    """Delegate clear-code calls through util at runtime."""
    return await _util_module().async_fire_clear_code(coordinator, slot, expected_name)


class RentalControlCoordinator(
    CoordinatorSetupMixin,
    CoordinatorStoreMixin,
    CoordinatorReservationMixin,
    CoordinatorCheckinMixin,
    CoordinatorRefreshMixin,
    CoordinatorConfigMixin,
    DataUpdateCoordinator[list[CalendarEvent]],
):
    """Coordinator for managing rental control calendar data."""

    _parent_entry_id: str | None
    _child_locknames: set[str]
    code_buffer_after: int
    code_buffer_before: int
    code_generator: str
    code_length: int
    days: int
    event: Any
    event_overrides: Any
    honor_event_times: bool
    ignore_non_reserved: bool
    lockname: str | None
    max_events: int
    max_name_length: int
    num_misses: int
    should_update_code: bool
    start_slot: int
    trim_names: bool
    verify_ssl: bool

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry):
        """Set up a calendar coordinator."""
        self._init_config_state(config_entry)
        super().__init__(
            hass=hass,
            logger=_LOGGER,
            name=self._name,
            config_entry=config_entry,
            update_interval=timedelta(minutes=self.refresh_frequency),
        )
        if self.lockname:
            self._parent_entry_id = self._find_parent_entry_id()
            if self._parent_entry_id is not None:
                self._child_locknames = self._discover_child_locks()
        self._register_keymaster_device(hass)

    @property
    def monitored_locknames(self) -> frozenset[str]:
        """Return the set of all monitored locknames."""
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
        """Return a combined diagnostics snapshot from the latest plan."""
        snapshot = (
            self.event_overrides.diagnostics_snapshot
            if self.event_overrides is not None
            else None
        )
        return diagnostics.build_reconciliation_diagnostics(self._latest_plan, snapshot)

    def get_slot_assignment(self, identity_key: str) -> int | None:
        """Return slot number assigned to identity_key in latest plan, or None."""
        if self._latest_plan is None:
            return None
        return self._latest_plan.selected.get(identity_key)

    def get_slot_code(self, identity_key: str) -> str | None:
        """Return slot_code for identity_key from latest reconciliation, or None."""
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
        return await self._async_get_events_impl(hass, start_date, end_date)

    async def async_setup_keymaster_overrides(self) -> None:
        """Bootstrap Keymaster slot overrides on first load."""
        await self._async_setup_keymaster_overrides_impl()

    async def async_load_slot_store(self) -> None:
        """Load cache-only slot metadata from the HA Store."""
        await self._async_load_slot_store_impl()

    def get_persisted_slot_mappings(self) -> dict[str, Any]:
        """Return entry-scoped persisted reservation-slot mappings."""
        return cast("dict[str, Any]", self._slot_mappings.get("mappings", {}))

    async def async_save_slot_store(self) -> None:
        """Best-effort save of cache-only slot metadata to the HA Store."""
        await self._async_save_slot_store_impl()

    async def async_adopt_keymaster_slots(self) -> None:
        """Adopt populated Keymaster slots on first upgrade."""
        await self._async_adopt_keymaster_slots_impl()

    def _find_observed_slot_by_name(
        self,
        query_or_slots: ObservedSlotQuery | list[_ManagedSlot],
        slot_name: str | None = None,
        display_slot_name: str | None = None,
        **criteria: Any,
    ) -> _ManagedSlot | None:
        """Return the current physical slot matching a stable/display name."""
        if isinstance(query_or_slots, ObservedSlotQuery):
            return slot_matching.find_observed_slot(query_or_slots)
        prefix = f"{self.event_prefix} " if self.event_prefix else ""
        query = ObservedSlotQuery(
            managed_slots=query_or_slots,
            slot_name=slot_name or "",
            display_slot_name=display_slot_name or "",
            event_prefix=prefix,
            **criteria,
        )
        return slot_matching.find_observed_slot(query)

    async def update_config(self, config: Mapping[str, Any]) -> None:
        """Update config entries."""
        await self._update_config_impl(config)

    async def update_event_overrides(
        self,
        update: EventOverrideUpdate | int | None = None,
        *values: Any,
        request_refresh: bool = True,
        **legacy: Any,
    ) -> None:
        """Update the event overrides with the ServiceCall data."""
        _LOGGER.debug("In update_event_overrides")
        payload = normalize_event_override_update(update, values, legacy)
        if self.event_overrides:
            await self.event_overrides.async_update(
                payload.slot,
                payload.slot_code,
                payload.slot_name,
                payload.start_time,
                payload.end_time,
                self.event_prefix,
            )
        if request_refresh:
            await self.async_request_refresh()
