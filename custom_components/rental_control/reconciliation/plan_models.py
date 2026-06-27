# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Legacy desired-plan dataclasses."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from typing import Any

from .action_models import SlotAction
from .enums import ActionKind
from .enums import SlotStatus


@dataclass(slots=True)
class Reservation:
    """Normalized calendar stay eligible for slot planning.

    A ``Reservation`` is constructed from a parsed iCal event and
    carries all fields required by the planner to decide whether and
    where to assign a Keymaster slot.

    The ``identity_key`` is a stable fingerprint derived from the
    normalized slot name, start, end, and entry scope.  It is the
    primary stable identifier used by the Store and the planner.
    ``uid_aliases`` and ``booking_aliases`` are *volatile* secondary
    identifiers that may change between feed refreshes.

    ``slot_code`` is never written to the HA Store; it is an in-memory
    field used only during the current reconciliation cycle.

    Attributes:
        identity_key: Versioned stable fingerprint of normalized slot
            name, start, end, and entry scope.
        start: Reservation check-in/access start before lock-code
            buffer.
        end: Reservation checkout/access end before lock-code buffer.
        buffered_start: Keymaster date-range start after existing
            before-buffer.
        buffered_end: Keymaster date-range end after existing
            after-buffer.
        summary: Calendar summary for sensor display.
        slot_name: Unprefixed, untrimmed guest-facing slot name from
            existing extraction logic.
        display_slot_name: Prefixed/trimmed Keymaster name computed at
            write time.
        slot_code: Generated or retained code for current planning; not
            persisted raw in Store.
        uid_aliases: Volatile iCal UIDs seen for this reservation.
            Aliases only, not primary identity.
        booking_aliases: Optional extracted booking or confirmation
            identifiers when available.
        fingerprint_history: Prior stable fingerprints for conservative
            rematch after date or UID changes.
        eligible: True when current parser/config rules include the
            reservation.
        protected_active: True for a currently checked-in guest inside
            the active stay window.
        checked_out: True when check-in tracking says the stay has
            checked out.
        missing_count: Consecutive refreshes missing from feed while
            persisted and assigned.  0, 1, or 2 keeps the slot; 3
            makes the reservation clearable unless protected.
        desired_slot: Slot selected by the current desired plan.
        overflow_reason: Why the reservation is not assigned, e.g.
            ``"capacity"`` or ``"blocked_clear"``.
    """

    identity_key: str
    start: datetime
    end: datetime
    buffered_start: datetime
    buffered_end: datetime
    summary: str
    slot_name: str
    display_slot_name: str
    slot_code: str = field(repr=False)
    uid_aliases: set[str] = field(default_factory=set)
    booking_aliases: set[str] = field(default_factory=set)
    fingerprint_history: set[str] = field(default_factory=set)
    eligible: bool = True
    protected_active: bool = False
    checked_out: bool = False
    missing_count: int = 0
    desired_slot: int | None = None
    overflow_reason: str | None = None
    sensor_lookup_keys: set[str] = field(default_factory=set)
    code_source: str = "generated"

    def __post_init__(self) -> None:
        """Validate Reservation field invariants.

        Raises:
            ValueError: If ``start`` is not strictly before ``end``,
                or if ``missing_count`` is negative.
        """
        if self.start >= self.end:
            raise ValueError(
                f"Reservation start must be strictly before end: "
                f"{self.start!r} >= {self.end!r}"
            )
        if self.missing_count < 0:
            raise ValueError(
                f"missing_count must be non-negative, got {self.missing_count}"
            )


@dataclass(slots=True)
class ManagedSlot:
    """A numbered Keymaster slot inside the RC-managed range.

    ``ManagedSlot`` holds both the *desired* state from the current
    :class:`DesiredPlan` and the *observed* state read from Keymaster
    entities during the most recent refresh.  The planner compares the
    two to determine which :class:`ActionKind` applies.

    Only slots where :attr:`managed` is ``True`` are ever modified by
    the reconciliation system.  Slots with
    ``status in {PENDING_CLEAR, BLOCKED, UNKNOWN}`` cannot receive a
    different reservation until their status resolves.

    Attributes:
        slot: Physical Keymaster slot number.
        managed: True only inside the configured
            ``start_slot .. start_slot + max_events - 1`` range.
        status: Current physical/logical state of the slot.
        actual_name: Observed Keymaster slot name, normalized for
            unknown/unavailable values.
        actual_code_present: Whether the PIN text entity contains a
            usable code.  ``None`` means the state was unreadable.
        actual_start: Observed Keymaster date-range start.
        actual_end: Observed Keymaster date-range end.
        date_range_enabled: Observed Keymaster use-date-range switch
            state.
        enabled: Observed Keymaster slot enabled switch state.
        desired_identity_key: Desired reservation for this slot in the
            current plan.
        persisted_identity_key: Previously stored reservation for this
            slot.
        blocked_reason: Reason the slot cannot be reused.
        preserve_unmatched: True when a populated physical slot with a
            persisted owner should be fenced if it cannot be matched to a
            current reservation yet.  This extends the adopted-slot
            preservation invariant to freshly observed stale Store mappings
            while their missing-count tolerance has not expired.
        retry_count: Consecutive failed physical operations for this
            slot.
        last_operation_id: Reconcile operation token used to classify
            callback echoes.
        dirty_during_operation: True when a callback observed state
            while an operation token was pending.
        last_error: Description of the most recent failed operation for
            this slot, if any.  Populated by the apply-plan phase and
            carried forward to the next refresh cycle for diagnostics.
            Not persisted in the HA Store.
    """

    slot: int
    managed: bool
    status: SlotStatus = SlotStatus.UNKNOWN
    actual_name: str | None = None
    actual_code: str | None = field(default=None, repr=False)
    actual_code_present: bool | None = None
    actual_start: datetime | None = None
    actual_end: datetime | None = None
    date_range_enabled: bool | None = None
    enabled: bool | None = None
    desired_identity_key: str | None = None
    persisted_identity_key: str | None = None
    blocked_reason: str | None = None
    preserve_unmatched: bool = False
    retry_count: int = 0
    last_operation_id: str | None = None
    dirty_during_operation: bool = False
    last_error: str | None = None


@dataclass(slots=True)
class PlannedSlot:
    """Desired-vs-actual comparison record for one managed slot.

    One ``PlannedSlot`` is produced per managed slot during each
    refresh and stored in :attr:`DesiredPlan.slots`.  It captures the
    planner's side-by-side view of what is desired versus what was
    actually observed so that diagnostics can explain every decision.

    Attributes:
        slot: Physical Keymaster slot number.
        desired_identity_key: Identity key the planner wants in this
            slot, or ``None`` if the slot should be empty.
        actual_classification: Observed slot classification string from
            the Keymaster entity read (e.g. ``"free"``,
            ``"occupied"``, ``"phantom"``).
        action: Reconciliation action the planner determined for this
            slot.
        pending_reason: Human-readable explanation when the action is
            ``ActionKind.BLOCKED`` or ``ActionKind.RETRY_CLEAR``.
        retry_count: Copy of :attr:`ManagedSlot.retry_count` at plan
            time, included for diagnostics.
        last_error: Description of the most recent failed operation for
            this slot, if any.
    """

    slot: int
    desired_identity_key: str | None
    actual_classification: str
    action: ActionKind
    pending_reason: str | None = None
    retry_count: int = 0
    last_error: str | None = None


@dataclass(slots=True)
class DesiredPlan:
    """Deterministic refresh result containing the full slot diff.

    Produced once per coordinator refresh cycle, ``DesiredPlan``
    records every decision made by the planner for the current set of
    eligible reservations and managed slots.  It is the single source
    of truth consumed by the apply-plan phase.

    Invariants:
        - ``len(selected) <= max_events`` unless protected active
          reservations exceed capacity; capacity violations are
          diagnostic-only.
        - Each selected identity appears exactly once.
        - Each selected slot appears exactly once.
        - No selected reservation is assigned behind a farther
          unprotected reservation once physical operations are
          confirmed.

    Attributes:
        plan_id: Refresh-scoped identifier for logging and operation
            tokens.
        generated_at: Time the plan was computed.
        selected: Mapping from reservation identity key to desired
            slot number.
        protected: Set of selected identity keys protected by
            checked-in state.
        overflow: Mapping from unassigned eligible identity keys to
            human-readable overflow reasons.
        slots: Per-slot desired-vs-actual comparison keyed by slot
            number.
        actions: Ordered list of actions to apply during apply-plan.
        diagnostics: Freeform capture of desired-vs-actual state for
            support diagnostics.
    """

    plan_id: str
    generated_at: datetime
    selected: dict[str, int] = field(default_factory=dict)
    protected: set[str] = field(default_factory=set)
    overflow: dict[str, str] = field(default_factory=dict)
    slots: dict[int, PlannedSlot] = field(default_factory=dict)
    actions: list[SlotAction] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> list[str]:
        """Return a list of invariant violations found in this plan.

        Checks that each selected identity key appears exactly once and
        that each selected slot number appears exactly once.  Returns
        an empty list when all invariants hold.  Callers may raise on a
        non-empty result or record the violations as diagnostics.

        Returns:
            List of human-readable violation messages; empty when the
            plan is internally consistent.
        """
        violations: list[str] = []
        seen_identities: set[str] = set()
        seen_slots: set[int] = set()
        for identity_key, slot in self.selected.items():
            if identity_key in seen_identities:
                violations.append(
                    f"Identity key {identity_key!r} appears more than once in selected."
                )
            seen_identities.add(identity_key)
            if slot in seen_slots:
                violations.append(
                    f"Slot {slot} is claimed by more than one reservation in selected."
                )
            seen_slots.add(slot)
        return violations
