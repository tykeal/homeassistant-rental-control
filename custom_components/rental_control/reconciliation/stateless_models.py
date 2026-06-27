# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Stateless planner dataclasses."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from typing import Any

from .action_models import SlotAction
from .enums import ObservedSlotStatus
from .identity import _desired_name_forms
from .identity import normalize_slot_name_for_fingerprint


def _keymaster_text_cleared(value: str | None) -> bool:
    """Return whether a Keymaster text state represents a cleared value."""
    if value is None:
        return True
    text = value.strip().casefold()
    return text in {"", "unknown", "none"}


def _keymaster_text_unreadable(value: str | None) -> bool:
    """Return whether a Keymaster text state is unreadable."""
    return value is not None and value.strip().casefold() == "unavailable"


@dataclass(slots=True)
class ObservedSlot:
    """Physical Keymaster slot facts read during one stateless refresh."""

    slot: int
    managed: bool
    raw_name: str | None = None
    raw_pin: str | None = field(default=None, repr=False)
    has_pin: bool | None = None
    actual_start: datetime | None = None
    actual_end: datetime | None = None
    date_range_enabled: bool | None = None
    enabled: bool | None = None
    readable: bool = True
    empty_confirmed: bool = False
    classification: ObservedSlotStatus = ObservedSlotStatus.UNKNOWN
    normalized_name_forms: set[str] = field(default_factory=set)
    matched_desired_id: str | None = None

    def __post_init__(self) -> None:
        """Validate and derive normalized physical name forms."""
        name_present = not _keymaster_text_cleared(self.raw_name)
        if self.has_pin is None and self.raw_pin is not None:
            self.has_pin = not _keymaster_text_cleared(self.raw_pin)
        if self.raw_pin is None and self.has_pin is None:
            self.readable = False
            self.empty_confirmed = False
        if _keymaster_text_unreadable(self.raw_name) or _keymaster_text_unreadable(
            self.raw_pin
        ):
            self.readable = False
            self.empty_confirmed = False
        if name_present or self.has_pin:
            self.empty_confirmed = False
        if not self.managed:
            self.classification = ObservedSlotStatus.UNKNOWN
        elif not self.readable:
            self.classification = ObservedSlotStatus.UNKNOWN
            self.empty_confirmed = False
        elif self.empty_confirmed and not name_present and not self.has_pin:
            self.classification = ObservedSlotStatus.EMPTY
        elif name_present and self.has_pin:
            self.classification = ObservedSlotStatus.OCCUPIED
        elif name_present or self.has_pin:
            self.classification = ObservedSlotStatus.PHANTOM
        else:
            self.classification = ObservedSlotStatus.EMPTY
            self.empty_confirmed = True
        if name_present and self.raw_name:
            self.normalized_name_forms.add(
                normalize_slot_name_for_fingerprint(self.raw_name)
            )


@dataclass(slots=True)
class DesiredReservation:
    """Calendar reservation facts used by the stateless planner."""

    desired_id: str
    stable_slot_name: str
    display_slot_name: str
    start: datetime
    end: datetime
    buffered_start: datetime
    buffered_end: datetime
    slot_code: str = field(repr=False)
    code_source: str = "generated"
    event_uid: str | None = None
    booking_aliases: set[str] = field(default_factory=set)
    eligible: bool = True
    protected_active: bool = False
    checked_out: bool = False
    selected_rank: int | None = None
    matched_slot: int | None = None
    assigned_slot: int | None = None
    sensor_lookup_keys: set[str] = field(default_factory=set)
    physical_time_override: tuple[datetime, datetime] | None = None
    overflow_reason: str | None = None
    normalized_name_forms: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        """Validate desired reservation invariants and name forms."""
        if self.start >= self.end:
            raise ValueError(
                f"DesiredReservation start must be strictly before end: "
                f"{self.start!r} >= {self.end!r}"
            )
        self.normalized_name_forms.update(
            _desired_name_forms(self.stable_slot_name, self.display_slot_name)
        )


@dataclass(slots=True)
class StatelessPlan:
    """Refresh-local stateless physical slot reconciliation result."""

    plan_id: str
    generated_at: datetime
    observed_slots: dict[int, ObservedSlot] = field(default_factory=dict)
    desired_reservations: dict[str, DesiredReservation] = field(default_factory=dict)
    selected: dict[str, int] = field(default_factory=dict)
    overflow: dict[str, str] = field(default_factory=dict)
    actions: list[SlotAction] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)
