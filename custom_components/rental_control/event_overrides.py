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
from datetime import datetime
import logging
import sys
from typing import Any
from typing import NamedTuple
from typing import TypedDict
from typing import cast
import uuid  # noqa: F401 - accessed through self._module for patch compatibility

from homeassistant.util import dt

from .event_overrides_helpers import shell_apply as _shell_apply
from .event_overrides_helpers import shell_cleanup as _shell_cleanup
from .event_overrides_helpers import shell_compat as _shell_compat
from .event_overrides_helpers import shell_slots as _shell_slots
from .event_overrides_helpers.models import SlotReservationRequest
from .event_overrides_helpers.models import SlotUpdateRequest
from .event_overrides_helpers.trim import is_trimmed_match
from .event_overrides_helpers.trim import strip_prefix
from .util import OperationResult
from .util import async_fire_clear_code  # noqa: F401 - accessed via self._module
from .util import async_fire_set_code  # noqa: F401 - accessed via self._module
from .util import async_fire_update_times  # noqa: F401 - accessed via self._module
from .util import get_event_identities  # noqa: F401 - accessed via self._module

_LOGGER = logging.getLogger(__name__)
_PREFLIGHT_WARNINGS = {
    "read_failed": "Skipping clear for slot %d because a fresh Keymaster read failed",
    "non_string": "Skipping clear for slot %d because Keymaster text states are not concrete strings",
    "unreadable": "Skipping clear for slot %d because a fresh Keymaster read is unreadable",
    "name_changed": "Skipping clear for slot %d because physical name changed after planning",
    "name_appeared": "Skipping clear for slot %d because physical name appeared after planning",
    "pin_changed": "Skipping clear for slot %d because physical PIN presence changed after planning",
}


def _to_utc(value: datetime) -> datetime:
    """Normalize a datetime to UTC for timezone-safe comparison."""
    if value.tzinfo is None or value.utcoffset() is None:
        local: datetime = cast(datetime, dt.as_local(value))
        return cast(datetime, dt.as_utc(local))
    return cast(datetime, dt.as_utc(value))


def _states_match(expected: str, actual: str) -> bool:
    """Compare raw HA state strings, normalizing datetimes when possible."""
    if expected == actual:
        return True
    expected_dt = dt.parse_datetime(expected)
    actual_dt = dt.parse_datetime(actual)
    return (
        expected_dt is not None
        and actual_dt is not None
        and _to_utc(expected_dt) == _to_utc(actual_dt)
    )


def _strip_prefix(slot_name: str, prefix: str) -> str:
    """Remove a leading prefix and space from ``slot_name``."""
    return strip_prefix(slot_name, prefix)


def _is_trimmed_match(name_a: str, name_b: str, guest_max: int) -> bool:
    """Return whether one name is the trimmed form of the other."""
    return is_trimmed_match(name_a, name_b, guest_max)


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


class _SlotEvent:
    """Minimal event adapter for util slot-operation helpers."""

    def __init__(
        self, slot_name: str, slot_code: str, start: datetime, end: datetime
    ) -> None:
        """Populate the attributes expected by util service helpers."""
        self.extra_state_attributes = {
            "slot_name": slot_name,
            "slot_code": slot_code,
            "start": start,
            "end": end,
        }


class EventOverrides:
    """Event Overrides object and methods."""

    _logger, _reserve_result = _LOGGER, ReserveResult
    _operation_result_type, _preflight_warnings = OperationResult, _PREFLIGHT_WARNINGS
    _states_match = staticmethod(_states_match)
    _shell_to_utc = staticmethod(_to_utc)
    _today_date = staticmethod(lambda: dt.start_of_local_day().date())
    _module, _slot_event_cls = sys.modules[__name__], _SlotEvent
    _reservation_request_type = SlotReservationRequest
    _update_request_type = SlotUpdateRequest
    suppress_state_changes = _shell_compat.suppress_state_changes
    should_suppress_state_change = _shell_compat.should_suppress_state_change
    get_last_slot_error = _shell_compat.get_last_slot_error
    _record_slot_error = _shell_compat._record_slot_error
    _clear_slot_error = _shell_compat._clear_slot_error
    update_diagnostics_snapshot = _shell_compat.update_diagnostics_snapshot
    load_persisted_mappings = _shell_compat.load_persisted_mappings
    update_actual_state = _shell_compat.update_actual_state
    get_actual_state = _shell_compat.get_actual_state
    release_pending_clear_slot = _shell_compat.release_pending_clear_slot
    _assign_next_slot = _shell_compat._assign_next_slot
    __assign_next_slot = _shell_compat._assign_next_slot
    _get_slots_with_values = _shell_compat._get_slots_with_values
    __get_slots_with_values = _shell_compat._get_slots_with_values
    _get_slots_without_values = _shell_compat._get_slots_without_values
    __get_slots_without_values = _shell_compat._get_slots_without_values
    _match_catalog = _shell_slots._match_catalog
    _override_dict = _shell_compat._override_dict
    _restore_slot_name = _shell_compat._restore_slot_name
    _clear_assignment = _shell_compat._clear_assignment
    _find_overlapping_slot = _shell_compat._find_overlapping_slot
    async_reserve_or_get_slot = _shell_compat.async_reserve_or_get_slot
    async_update = _shell_compat.async_update
    verify_slot_ownership = _shell_compat.verify_slot_ownership
    record_retry_failure = _shell_compat.record_retry_failure
    record_retry_success = _shell_compat.record_retry_success
    _slot_confirmed_empty = _shell_compat._slot_confirmed_empty
    _fresh_slot_text_states = _shell_compat._fresh_slot_text_states
    _preflight_clear_result = _shell_compat._preflight_clear_result
    _slot_has_matching_event = _shell_slots._slot_has_matching_event
    _event_has_other_uid_owner = _shell_slots._event_has_other_uid_owner
    _slot_has_other_uid_owner = _shell_slots._slot_has_other_uid_owner
    _get_same_start_uid_bypass_slot = _shell_slots._get_same_start_uid_bypass_slot
    get_slot_name = _shell_slots.get_slot_name
    get_slot_with_name = _shell_slots.get_slot_with_name
    get_slot_key_by_name = _shell_slots.get_slot_key_by_name
    get_slot_start_date = _shell_slots.get_slot_start_date
    get_slot_start_time = _shell_slots.get_slot_start_time
    get_slot_end_date = _shell_slots.get_slot_end_date
    get_slot_end_time = _shell_slots.get_slot_end_time
    async_apply_plan = _shell_apply.async_apply_plan
    _apply_clear = _shell_apply._apply_clear
    _apply_set = _shell_apply._apply_set
    _apply_update_times = _shell_apply._apply_update_times
    _apply_overwrite_manual_change = _shell_apply._apply_overwrite_manual_change
    async_check_overrides = _shell_cleanup.async_check_overrides
    update = _shell_slots.update

    def __init__(self, start_slot: int, max_slots: int) -> None:
        """Setup the overrides object."""
        self._escalated: dict[int, bool] = {}
        self._lock = asyncio.Lock()
        self._max_slots = max_slots
        self._next_slot: int | None = None
        self._overrides: dict[int, EventOverride | None] = {}
        self._ready = False
        self._retry_counts: dict[int, int] = {}
        self._slot_miss_counts: dict[int, int] = {}
        self._slot_uids: dict[int, str | None] = {}
        self._start_slot = start_slot
        (
            self._trim_names,
            self._max_name_length,
            self._event_prefix,
            self._prefix_length,
        ) = False, 0, "", 0
        self._persisted_mappings: dict[str, dict[str, Any]] = {}
        self._pending_clear_slots: dict[int, str] = {}
        self._pending_fences: dict[int, str] = {}
        self._actual_state_cache: dict[int, dict[str, Any]] = {}
        self._reconciliation_active = False
        self._last_slot_errors: dict[int, str] = {}
        self._suppressed_state_changes: dict[int, dict[str, tuple[str, float]]] = {}
        self._diagnostics_snapshot: dict[str, Any] = {}

    @property
    def max_slots(self) -> int:
        """Return the configured slot count."""
        return self._max_slots

    @property
    def next_slot(self) -> int | None:
        """Return the next available greedy slot."""
        return self._next_slot

    @property
    def overrides(self) -> dict[int, EventOverride | None]:
        """Return the override mapping."""
        return self._overrides

    @property
    def ready(self) -> bool:
        """Return whether the override mapping is fully populated."""
        return self._ready

    @property
    def start_slot(self) -> int:
        """Return the first managed slot."""
        return self._start_slot

    @property
    def trim_names(self) -> bool:
        """Return whether trimmed-name matching is enabled."""
        return self._trim_names

    @trim_names.setter
    def trim_names(self, value: bool) -> None:
        """Set whether trimmed-name matching is enabled."""
        self._trim_names = value

    @property
    def max_name_length(self) -> int:
        """Return the configured maximum slot-name length."""
        return self._max_name_length

    @max_name_length.setter
    def max_name_length(self, value: int) -> None:
        """Set the configured maximum slot-name length."""
        self._max_name_length = value

    @property
    def event_prefix(self) -> str:
        """Return the configured event prefix."""
        return self._event_prefix

    @event_prefix.setter
    def event_prefix(self, value: str | None) -> None:
        """Set the configured event prefix."""
        self._event_prefix, self._prefix_length = (
            value or "",
            len(f"{value or ''} ") if value else 0,
        )

    @property
    def prefix_length(self) -> int:
        """Return the prefix length including its separator."""
        return self._prefix_length

    @prefix_length.setter
    def prefix_length(self, value: int) -> None:
        """Set the cached prefix length."""
        self._prefix_length = value

    persisted_mappings = property(lambda self: dict(self._persisted_mappings))
    pending_clear_slots = property(lambda self: dict(self._pending_clear_slots))
    pending_fences = property(lambda self: dict(self._pending_fences))
    reconciliation_active = property(lambda self: self._reconciliation_active)
    diagnostics_snapshot = property(lambda self: dict(self._diagnostics_snapshot))
