# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Synchronous pre-dispatch checks against the planned in-memory reservation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from dataclasses import replace
from typing import Any

from ..keymaster_topology import verified_slot_source

_CODE_FIELD = "slot_code"


@dataclass(frozen=True, slots=True)
class ReservationGuard:
    """In-memory evidence one slot operation was planned against.

    ``echoes`` holds the values the operation itself writes; a field that
    now equals its echo is accepted so our own feedback cannot abort us.
    """

    overrides: Any = field(repr=False, compare=False)
    coordinator: Any = field(repr=False, compare=False)
    slot: int
    identity_key: str | None
    assignment: Any
    source_key: tuple[Any, ...] | None
    generation: int
    fields: dict[str, Any] | None
    include_code: bool
    echoes: Mapping[str, Any] = field(default_factory=dict)

    def rebased(self) -> ReservationGuard:
        """Adopt the current override and generation as the planned state."""
        return replace(
            self,
            generation=self.overrides.get_feedback_generation(self.slot),
            fields=_reservation_fields(
                self.overrides.overrides.get(self.slot), self.include_code
            ),
        )

    def is_current(self) -> bool:
        """Return whether the planned reservation still owns the slot."""
        return (
            self.coordinator.event_overrides is self.overrides
            and _assignment(self.coordinator, self.identity_key) == self.assignment
            and self._source_is_current()
            and self.overrides.get_feedback_generation(self.slot) == self.generation
            and self._fields_are_current()
        )

    def allows_clear(self) -> bool:
        """Accept the planned reservation or our reset echo, not a replacement.

        A reset legitimately empties the slot (override ``None``, generation
        advanced, name source possibly unresolved); any other reservation
        now on the slot must keep it.
        """
        if (
            self.coordinator.event_overrides is not self.overrides
            or _assignment(self.coordinator, self.identity_key) != self.assignment
        ):
            return False
        if self.overrides.overrides.get(self.slot) is None:
            return True
        current_source = _source_key(self.coordinator, self.slot)
        return (
            current_source is None
            or self.source_key is None
            or current_source == self.source_key
        ) and (
            self.overrides.get_feedback_generation(self.slot) == self.generation
            and self._fields_are_current()
        )

    def _source_is_current(self) -> bool:
        """Require an unchanged name source once one was resolved."""
        if self.source_key is None:
            # Unresolved (e.g. native-empty name before a SET); the verified
            # intent still revalidates lifecycle and selected entry.
            return True
        return _source_key(self.coordinator, self.slot) == self.source_key

    def _fields_are_current(self) -> bool:
        """Compare reservation fields, admitting our own echoed writes."""
        current = _reservation_fields(
            self.overrides.overrides.get(self.slot), self.include_code
        )
        if current is None or self.fields is None:
            return current == self.fields
        expected = dict(self.fields)
        for key, value in self.echoes.items():
            if key in expected and current.get(key) == value:
                expected[key] = value
        return current == expected


def capture_reservation_guard(
    overrides: Any,
    coordinator: Any,
    slot: int,
    identity_key: str | None,
    *,
    include_code: bool,
    echoes: Mapping[str, Any] | None = None,
) -> ReservationGuard:
    """Snapshot the in-memory reservation evidence for one slot."""
    return ReservationGuard(
        overrides=overrides,
        coordinator=coordinator,
        slot=slot,
        identity_key=identity_key,
        assignment=_assignment(coordinator, identity_key),
        source_key=_source_key(coordinator, slot),
        generation=overrides.get_feedback_generation(slot),
        fields=_reservation_fields(overrides.overrides.get(slot), include_code),
        include_code=include_code,
        echoes=dict(echoes or {}),
    )


def guarded_intent(intent: Any, guard: ReservationGuard | None) -> Any:
    """Bind the guard to every dispatch made through ``intent``."""
    if intent is None or guard is None:
        return intent
    return replace(intent, dispatch_guard=guard.is_current)


def clear_confirmation_intent(intent: Any, guard: ReservationGuard | None) -> Any:
    """Bind clear-echo-aware reservation evidence to fence resolution."""
    if intent is None or guard is None:
        return intent
    return replace(intent, dispatch_guard=guard.allows_clear)


def _assignment(coordinator: Any, identity_key: str | None) -> Any:
    """Return the coordinator's current slot assignment for the identity."""
    reader = getattr(coordinator, "get_slot_assignment", None)
    if identity_key is None or not callable(reader):
        return None
    return reader(identity_key)


def _source_key(coordinator: Any, slot: int) -> tuple[Any, ...] | None:
    """Return the stable identity of the slot's verified name source."""
    config_entry = getattr(coordinator, "config_entry", None)
    if config_entry is None:
        return None
    source = verified_slot_source(coordinator.hass, config_entry, slot)
    if source is None:
        return None
    return (
        source.entry.entry_id,
        source.lock_entity_id,
        source.lockname,
        source.lifecycle_generation,
    )


def _reservation_fields(override: Any, include_code: bool) -> dict[str, Any] | None:
    """Return the identifying override fields, with the PIN only if written."""
    if not isinstance(override, dict):
        return None
    return {
        key: value
        for key, value in override.items()
        if include_code or key != _CODE_FIELD
    }
