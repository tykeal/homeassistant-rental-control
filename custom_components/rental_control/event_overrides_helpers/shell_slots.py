# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Slot date/time accessor methods for EventOverrides."""

from __future__ import annotations

from datetime import time as dt_time
from typing import Any

from .matcher import _get_same_start_uid_bypass_slot as _matcher_same_start_slot
from .matcher import _has_other_uid_owner
from .matcher import build_match_catalog
from .mirror import match_target_slot
from .models import MatchRequest
from .slot_bookkeeping import normalize_update_request
from .trim import make_trim_config
from .trim import strip_prefix


def get_slot_start_date(self, slot: int):
    """Return the start date of ``slot`` or today when empty."""
    override = self._overrides.get(slot)
    return (
        override["start_time"].date()
        if override and "start_time" in override
        else self._today_date()
    )


def get_slot_start_time(self, slot: int):
    """Return the start time of ``slot`` or midnight when empty."""
    override = self._overrides.get(slot)
    return (
        override["start_time"].time()
        if override and "start_time" in override
        else dt_time()
    )


def get_slot_end_date(self, slot: int):
    """Return the end date of ``slot`` or today when empty."""
    override = self._overrides.get(slot)
    return (
        override["end_time"].date()
        if override and "end_time" in override
        else self._today_date()
    )


def get_slot_end_time(self, slot: int):
    """Return the end time of ``slot`` or midnight when empty."""
    override = self._overrides.get(slot)
    return (
        override["end_time"].time()
        if override and "end_time" in override
        else dt_time()
    )


def _match_catalog(self, exclude_slot: int | None = None):
    """Return a pure catalog snapshot of the current override state."""
    return build_match_catalog(
        {
            slot: (
                None
                if override is None
                else {
                    **override,
                    "start_time": self._shell_to_utc(override["start_time"]),
                    "end_time": self._shell_to_utc(override["end_time"]),
                }
            )
            for slot, override in self._overrides.items()
        },
        self._slot_uids,
        make_trim_config(
            self._trim_names,
            self._max_name_length,
            self._event_prefix,
            self._prefix_length,
        ),
        exclude_slot,
        {
            slot: (override["start_time"].date(), override["end_time"].date())
            for slot, override in self._overrides.items()
            if override is not None
        },
    )


def _slot_has_matching_event(self, slot: int, events) -> bool:
    """Return whether ``slot`` matches any event in mirror orientation."""
    if self._overrides.get(slot) is None:
        return False
    catalog = self._match_catalog()
    for event in events:
        result = match_target_slot(
            catalog,
            slot,
            MatchRequest(
                event.name,
                self._shell_to_utc(event.start),
                self._shell_to_utc(event.end),
                event.uid,
                target_slot=slot,
            ),
        )
        if result.slot == slot:
            self._restore_slot_name(result)
            return True
    return False


def _event_has_other_uid_owner(self, event, exclude_slot: int) -> bool:
    """Return whether another slot claims ``event`` via an exact UID match."""
    return bool(
        self._slot_has_other_uid_owner(event.name, event.uid, exclude_slot=exclude_slot)
    )


def _slot_has_other_uid_owner(
    self,
    slot_name: str,
    uid: str | None,
    exclude_slot: int | None = None,
) -> bool:
    """Return whether another slot already owns ``uid`` for ``slot_name``."""
    return _has_other_uid_owner(
        self._match_catalog(exclude_slot), slot_name, uid, exclude_slot
    )


def _get_same_start_uid_bypass_slot(
    self, event, exclude_slot: int | None = None
) -> int | None:
    """Return the preferred same-start fallback slot for ``event``."""
    return _matcher_same_start_slot(
        self._match_catalog(exclude_slot),
        event.name,
        self._shell_to_utc(event.start),
        self._shell_to_utc(event.end),
        event.uid,
        exclude_slot,
    )


def get_slot_name(self, slot: int) -> str:
    """Return the slot name for ``slot`` or an empty string."""
    override = self._overrides.get(slot)
    return override["slot_name"] if override and "slot_name" in override else ""


def get_slot_with_name(self, slot_name: str):
    """Return the first override whose stored name matches ``slot_name``."""
    return next(
        (
            override
            for slot in self._get_slots_with_values()
            if (override := self.overrides[slot]) and override["slot_name"] == slot_name
        ),
        None,
    )


def get_slot_key_by_name(self, slot_name: str) -> int:
    """Return the slot number for ``slot_name`` or ``0`` when absent."""
    return next(
        (
            slot
            for slot in self._get_slots_with_values()
            if (override := self.overrides[slot]) and override["slot_name"] == slot_name
        ),
        0,
    )


def update(self, update=None, *values: Any, **legacy: Any) -> None:
    """Synchronously update overrides for a slot."""
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
    overrides = self._overrides.copy()
    overrides[payload.slot] = (
        self._override_dict(
            payload.slot_code, slot_name, payload.start_time, payload.end_time
        )
        if slot_name
        else None
    )
    self._slot_miss_counts.pop(payload.slot, None)
    self._overrides = overrides
    self._assign_next_slot()
    if len(overrides) == self.max_slots:
        self._ready = True
