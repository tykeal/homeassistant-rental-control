# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Compatibility-boundary methods for the EventOverrides shell."""

from __future__ import annotations

from time import monotonic
from typing import TYPE_CHECKING
from typing import Any
from typing import cast

from ..const import DEFAULT_MAX_RETRY_CYCLES
from ..util import is_cleared_keymaster_text_state
from ..util import normalize_uid
from .apply_clear import decide_clear_preflight
from .diagnostics import build_diagnostics_snapshot
from .matcher import match_slot
from .models import MatchRequest
from .slot_bookkeeping import compute_next_slot
from .slot_bookkeeping import get_slots_with_values
from .slot_bookkeeping import get_slots_without_values
from .slot_bookkeeping import normalize_reservation_request
from .slot_bookkeeping import normalize_update_request
from .trim import make_trim_config
from .trim import names_match
from .trim import strip_prefix

if TYPE_CHECKING:
    from ..event_overrides import ReserveResult

SUPPRESSED_STATE_CHANGE_TTL = 5.0


def suppress_state_changes(self, slot: int, changes: dict[str, Any]) -> None:
    """Mark coordinator-originated state changes to ignore in callbacks."""
    now = monotonic()
    pending = self._suppressed_state_changes.setdefault(slot, {})
    for entity_id, value in changes.items():
        pending[entity_id] = (str(value), now)


def should_suppress_state_change(self, slot: int, entity_id: str, state: str) -> bool:
    """Return whether a callback is feedback from our own service call."""
    pending = self._suppressed_state_changes.get(slot)
    if not pending:
        return False
    now = monotonic()
    for pending_entity_id, (_, created) in list(pending.items()):
        if now - created > SUPPRESSED_STATE_CHANGE_TTL:
            pending.pop(pending_entity_id, None)
    expected = pending.get(entity_id)
    if expected is None:
        if not pending:
            self._suppressed_state_changes.pop(slot, None)
        return False
    if self._states_match(expected[0], state):
        pending.pop(entity_id, None)
        if not pending:
            self._suppressed_state_changes.pop(slot, None)
        return True
    return False


def get_last_slot_error(self, slot: int) -> str | None:
    """Return the last recorded slot error."""
    return cast("str | None", self._last_slot_errors.get(slot))


def _record_slot_error(self, slot: int, error: str) -> None:
    """Record a failed operation error."""
    self._last_slot_errors[slot] = error


def _clear_slot_error(self, slot: int) -> None:
    """Clear any recorded slot error."""
    self._last_slot_errors.pop(slot, None)


def update_diagnostics_snapshot(self, plan) -> None:
    """Build and store a diagnostics snapshot from the completed plan."""
    self._diagnostics_snapshot = build_diagnostics_snapshot(
        plan,
        self._pending_clear_slots,
        self._retry_counts,
        self._last_slot_errors,
        self._start_slot,
        self._max_slots,
    )


def load_persisted_mappings(self, mappings: dict[str, dict[str, Any]]) -> None:
    """Load cache-only mappings without creating assignment fences."""
    self._persisted_mappings = {key: dict(value) for key, value in mappings.items()}


def update_actual_state(self, slot: int, state: dict[str, Any]) -> None:
    """Store the observed Keymaster state snapshot for a slot."""
    self._actual_state_cache[slot] = state


def get_actual_state(self, slot: int) -> dict[str, Any] | None:
    """Return the cached actual state for ``slot``."""
    return cast("dict[str, Any] | None", self._actual_state_cache.get(slot))


def release_pending_clear_slot(self, slot: int) -> None:
    """Release a pending-clear fence after observing an empty slot."""
    self._pending_clear_slots.pop(slot, None)
    self._pending_fences.pop(slot, None)
    self._clear_assignment(slot)
    self._clear_slot_error(slot)


def _slot_confirmed_empty(self, coordinator: Any, slot: int) -> bool:
    """Return whether a fresh Keymaster read shows blank name and PIN."""
    lockname = getattr(coordinator, "lockname", None)
    hass = getattr(coordinator, "hass", None)
    if not lockname or hass is None:
        return False
    name_state = hass.states.get(f"text.{lockname}_code_slot_{slot}_name")
    pin_state = hass.states.get(f"text.{lockname}_code_slot_{slot}_pin")
    return (
        bool(name_state and pin_state)
        and is_cleared_keymaster_text_state(name_state.state)
        and is_cleared_keymaster_text_state(pin_state.state)
    )


def _fresh_slot_text_states(
    self, coordinator: Any, slot: int
) -> tuple[Any, Any] | None:
    """Return a fresh physical Keymaster name/PIN read for ``slot``."""
    lockname = getattr(coordinator, "lockname", None)
    hass = getattr(coordinator, "hass", None)
    if not lockname or hass is None:
        return None
    name_state = hass.states.get(f"text.{lockname}_code_slot_{slot}_name")
    pin_state = hass.states.get(f"text.{lockname}_code_slot_{slot}_pin")
    return (
        None
        if name_state is None or pin_state is None
        else (name_state.state, pin_state.state)
    )


def _preflight_clear_result(
    self, coordinator: Any, slot: int, expected_name: str | None
):
    """Abort stale clear actions when the physical slot changed after plan."""
    fresh = self._fresh_slot_text_states(coordinator, slot)
    actual = self._actual_state_cache.get(slot) or {}
    if (
        fresh is not None
        and "name_state" in actual
        and actual["name_state"] is not None
    ):
        fresh_name = "" if fresh[0] is None else str(fresh[0])
        if self._states_match(str(actual["name_state"]), fresh_name):
            actual = {**actual, "name_state": fresh_name}
    outcome = decide_clear_preflight(fresh, expected_name, actual)
    if outcome["status"] == "proceed":
        return None
    if outcome["status"] == "confirmed":
        self.release_pending_clear_slot(slot)
        return self._operation_result_type(kind="clear", slot=slot, confirmed=True)
    log_method = (
        self._logger.debug
        if outcome["reason"] == "non_string"
        else self._logger.warning
    )
    log_method(self._preflight_warnings[outcome["reason"]], slot)
    return self._operation_result_type(kind="clear", slot=slot, unconfirmed=True)


def _assign_next_slot(self) -> None:
    """Recompute ``_next_slot`` for the deprecated greedy path."""
    self._next_slot = compute_next_slot(
        self._overrides, self.start_slot, self.max_slots
    )


def _get_slots_with_values(self) -> list[int]:
    """Return sorted occupied slot numbers."""
    return get_slots_with_values(self._overrides)


def _get_slots_without_values(self, max_slot: int = 0) -> list[int]:
    """Return sorted free slot numbers greater than ``max_slot``."""
    return get_slots_without_values(self._overrides, max_slot)


def _override_dict(self, slot_code: str, slot_name: str, start_time, end_time):
    """Return a new override payload dict."""
    return {
        "slot_name": slot_name,
        "slot_code": slot_code,
        "start_time": start_time,
        "end_time": end_time,
    }


def _restore_slot_name(self, result: Any) -> None:
    """Apply a trim-aware full-name restoration from a matcher result."""
    if (
        result.slot is not None
        and result.restored_slot_name
        and self._overrides.get(result.slot) is not None
    ):
        self._overrides[result.slot]["slot_name"] = result.restored_slot_name


def _clear_assignment(self, slot: int) -> None:
    """Clear in-memory override, UID, and miss-count state for ``slot``."""
    self._overrides[slot] = None
    self._slot_uids.pop(slot, None)
    self._slot_miss_counts.pop(slot, None)


def _find_overlapping_slot(
    self,
    slot_name: str,
    start_time,
    end_time,
    uid: str | None = None,
    exclude_slot: int | None = None,
) -> int | None:
    """Find an existing slot matching a reservation identity."""
    result = match_slot(
        self._match_catalog(exclude_slot),
        MatchRequest(
            slot_name,
            self._shell_to_utc(start_time),
            self._shell_to_utc(end_time),
            uid,
            exclude_slot=exclude_slot,
        ),
    )
    self._restore_slot_name(result)
    return result.slot


async def async_reserve_or_get_slot(
    self, request=None, *values: Any, **legacy: Any
) -> "ReserveResult":
    """Atomically find an existing slot or reserve the next available one."""
    if isinstance(request, self._reservation_request_type):
        if values or legacy:
            msg = "SlotReservationRequest cannot be combined with extra values"
            raise TypeError(msg)
        payload = request
    else:
        payload = normalize_reservation_request(
            *(() if request is None else (request,)), *values, **legacy
        )
    slot_name = (
        strip_prefix(payload.slot_name, payload.prefix or "")
        if payload.slot_name and payload.prefix
        else payload.slot_name
    )
    async with self._lock:
        existing = self._find_overlapping_slot(
            slot_name, payload.start_time, payload.end_time, payload.uid
        )
        if existing is not None:
            self._slot_miss_counts.pop(existing, None)
            if payload.uid is not None:
                self._slot_uids[existing] = normalize_uid(payload.uid)
            override = self._overrides[existing]
            changed = override is not None and (
                self._shell_to_utc(override["start_time"])
                != self._shell_to_utc(payload.start_time)
                or self._shell_to_utc(override["end_time"])
                != self._shell_to_utc(payload.end_time)
            )
            if changed and override is not None:
                override["start_time"], override["end_time"] = (
                    payload.start_time,
                    payload.end_time,
                )
            return cast("ReserveResult", self._reserve_result(existing, False, changed))
        if self._next_slot is None:
            self._logger.warning(
                "All %d override slots are occupied; reservation '%s' could not be assigned a slot",
                self._max_slots,
                slot_name,
            )
            return cast("ReserveResult", self._reserve_result(None, False, False))
        slot = self._next_slot
        self._overrides[slot] = self._override_dict(
            payload.slot_code, slot_name, payload.start_time, payload.end_time
        )
        self._slot_miss_counts.pop(slot, None)
        if payload.uid is not None:
            self._slot_uids[slot] = normalize_uid(payload.uid)
        self._assign_next_slot()
        return cast("ReserveResult", self._reserve_result(slot, True, False))


async def async_update(self, update=None, *values: Any, **legacy: Any) -> None:
    """Update a slot with duplicate-detection enforcement."""
    if isinstance(update, self._update_request_type):
        if values or legacy:
            msg = "SlotUpdateRequest cannot be combined with extra values"
            raise TypeError(msg)
        payload = update
    else:
        payload = normalize_update_request(
            *(() if update is None else (update,)), *values, **legacy
        )
    slot_name = (
        strip_prefix(payload.slot_name, payload.prefix or "")
        if payload.slot_name and payload.prefix
        else payload.slot_name
    )
    async with self._lock:
        slot = payload.slot
        if slot_name:
            duplicate = self._find_overlapping_slot(
                slot_name, payload.start_time, payload.end_time, exclude_slot=slot
            )
            if duplicate is not None:
                self._logger.warning(
                    "Duplicate slot_name '%s' detected in slot %d while writing slot %d; redirecting write",
                    slot_name,
                    duplicate,
                    slot,
                )
                slot = duplicate
            self._overrides[slot] = self._override_dict(
                payload.slot_code, slot_name, payload.start_time, payload.end_time
            )
            self._slot_miss_counts.pop(slot, None)
        else:
            self._clear_assignment(slot)
        self._assign_next_slot()
        if len(self._overrides) == self.max_slots:
            self._ready = True


def verify_slot_ownership(self, slot: int, expected_name: str) -> bool:
    """Return whether ``slot`` is still assigned to ``expected_name``."""
    override = self._overrides.get(slot)
    return override is not None and names_match(
        override["slot_name"],
        expected_name,
        make_trim_config(
            self._trim_names,
            self._max_name_length,
            self._event_prefix,
            self._prefix_length,
        ),
    )


def record_retry_failure(self, slot: int) -> bool:
    """Record a failed lock command and return whether escalation is due."""
    count = self._retry_counts.get(slot, 0) + 1
    self._retry_counts[slot] = count
    if count >= DEFAULT_MAX_RETRY_CYCLES and not self._escalated.get(slot, False):
        self._escalated[slot] = True
        return True
    return False


def record_retry_success(self, slot: int) -> None:
    """Reset failure tracking for ``slot``."""
    self._retry_counts[slot], self._escalated[slot] = 0, False
