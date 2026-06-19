# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Slot reconciliation data models for Rental Control.

Provides typed dataclasses and enumerations representing the core data
structures used by the slot reconciliation system: the deterministic
planner that assigns RC-managed Keymaster slots to calendar
reservations.

Exported names
--------------
SlotStatus
    Physical/logical state of a managed Keymaster slot.
ActionKind
    Enumeration of reconciliation action types.
SlotAction
    A single planned reconciliation action targeting one slot.
Reservation
    Normalized calendar stay eligible for slot planning.
ManagedSlot
    A numbered Keymaster slot inside the RC-managed range.
PlannedSlot
    Desired-vs-actual comparison record for one managed slot.
DesiredPlan
    Deterministic refresh result containing the full slot diff.
StoredIdentity
    Stable identity fields persisted in the HA Store.
StoredActual
    Redacted last-observed Keymaster state persisted in the HA Store.
SlotMapping
    Top-level HA Store record for one persisted slot assignment.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from enum import Enum
from typing import Any


class SlotStatus(str, Enum):
    """Physical/logical state of a managed Keymaster slot.

    Values map 1-to-1 to the string literals stored in the HA Store so
    that serialisation is a no-op cast.

    Lifecycle transitions::

        FREE  ──set──►  OCCUPIED  ──desired gone──►  PENDING_CLEAR
                                                        │         │
                                             confirmed  │  failed │
                                                        ▼         ▼
                                                      FREE     BLOCKED
                                                        ▲         │
                                                        └─confirmed┘
        *  ──►  PHANTOM : name present, PIN absent/slot disabled
        *  ──►  UNKNOWN : actual entity state unreadable
    """

    FREE = "free"
    OCCUPIED = "occupied"
    PENDING_CLEAR = "pending_clear"
    BLOCKED = "blocked"
    PHANTOM = "phantom"
    UNKNOWN = "unknown"


class ActionKind(str, Enum):
    """Reconciliation action type for one managed Keymaster slot.

    Used by :class:`SlotAction` and :class:`PlannedSlot` to label the
    physical operation (or absence of operation) that the planner
    determined for a slot during a single refresh cycle.
    """

    NOOP = "noop"
    """Actual already matches desired; no Keymaster service call needed."""

    SET = "set"
    """Slot is confirmed free; write name, code, and date range."""

    UPDATE_TIMES = "update_times"
    """Same reservation and code; only buffered date-range changed."""

    CLEAR = "clear"
    """Slot contains stale, duplicate, phantom, or expired state."""

    RETRY_CLEAR = "retry_clear"
    """Prior clear attempt unconfirmed; increment retry count and re-clear."""

    OVERWRITE_MANUAL_CHANGE = "overwrite_manual_change"
    """Actual state drifted from desired; restore desired and log drift."""

    BLOCKED = "blocked"
    """Actual state unknown or clear unconfirmed; no assignment allowed."""


@dataclass(slots=True)
class SlotAction:
    """A single planned reconciliation action for one managed Keymaster slot.

    Instances are collected in :attr:`DesiredPlan.actions` and applied
    in order by the apply-plan phase.  Each instance couples an
    :class:`ActionKind` with the target slot and optional identity
    context so that the apply loop does not need to re-derive
    per-slot context.

    Attributes:
        kind: Type of action to perform.
        slot: Physical Keymaster slot number to act on.
        identity_key: Identity of the reservation being set or cleared;
            ``None`` for clears where no reservation is being assigned.
        reason: Optional human-readable explanation, e.g. overflow
            cause or drift description.
    """

    kind: ActionKind
    slot: int
    identity_key: str | None = None
    reason: str | None = None


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
        retry_count: Consecutive failed physical operations for this
            slot.
        last_operation_id: Reconcile operation token used to classify
            callback echoes.
        dirty_during_operation: True when a callback observed state
            while an operation token was pending.
    """

    slot: int
    managed: bool
    status: SlotStatus = SlotStatus.UNKNOWN
    actual_name: str | None = None
    actual_code_present: bool | None = None
    actual_start: datetime | None = None
    actual_end: datetime | None = None
    date_range_enabled: bool | None = None
    enabled: bool | None = None
    desired_identity_key: str | None = None
    persisted_identity_key: str | None = None
    blocked_reason: str | None = None
    retry_count: int = 0
    last_operation_id: str | None = None
    dirty_during_operation: bool = False


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


@dataclass(slots=True)
class StoredIdentity:
    """Stable identity fields persisted in the HA Store.

    ``StoredIdentity`` captures the subset of :class:`Reservation`
    fields written to persistent storage.  These fields are sufficient
    to re-identify a reservation after a Home Assistant restart without
    re-fetching the calendar feed.

    Note that ``uid_aliases`` and ``booking_aliases`` use ``list``
    (JSON-serializable) rather than the ``set`` used in
    :class:`Reservation`; the ordering is not significant.

    Attributes:
        identity_key: Primary stable fingerprint.
        summary: Calendar summary for sensor display.
        slot_name: Unprefixed, untrimmed guest-facing slot name.
        uid_aliases: Volatile iCal UIDs seen for this reservation.
        booking_aliases: Optional extracted booking/confirmation IDs.
    """

    identity_key: str
    summary: str
    slot_name: str
    uid_aliases: list[str] = field(default_factory=list)
    booking_aliases: list[str] = field(default_factory=list)


@dataclass(slots=True)
class StoredActual:
    """Redacted last-observed Keymaster state persisted in the HA Store.

    Raw PIN values are **never** stored.  This record holds only
    redacted or non-sensitive fields sufficient for drift detection
    and migration diagnostics.

    Attributes:
        slot: Slot number.
        classification: Last observed slot classification, e.g.
            ``"free"``, ``"occupied"``, or ``"phantom"``.
        name_state: Last observed name entity state after
            unknown/unavailable normalization.
        has_code: Whether the PIN entity contained a usable code at
            last observation.  The raw PIN value is never stored.
        start_state: Last observed Keymaster date-range start.
        end_state: Last observed Keymaster date-range end.
        use_date_range: Last observed use-date-range switch state.
        enabled: Last observed slot-enabled switch state.
    """

    slot: int
    classification: str
    name_state: str | None = None
    has_code: bool | None = None
    start_state: datetime | None = None
    end_state: datetime | None = None
    use_date_range: bool | None = None
    enabled: bool | None = None


@dataclass(slots=True)
class SlotMapping:
    """Top-level HA Store record for one persisted slot assignment.

    One ``SlotMapping`` is written per managed slot assignment and
    survives Home Assistant restarts.  The Store layer enforces the
    invariant that no two mappings may claim the same slot with
    ``status == "occupied"`` simultaneously.

    Store invariants (enforced by the persistence layer):
        - No two mappings may claim the same slot unless at most one
          is ``"occupied"`` and the other is historical/overflow
          diagnostic state.
        - ``"pending_set"`` and ``"pending_clear"`` slots are fenced
          after restart until a refresh verifies the physical state.
        - Raw PIN values are not stored; only
          :attr:`StoredActual.has_code` for drift detection.

    Attributes:
        schema_version: Store schema version.  Starts at ``1``.
        entry_id: Config entry scope.
        identity_key: Primary Reservation identity.
        slot: Persisted desired/last confirmed slot.
        status: Mapping status string: ``"occupied"``,
            ``"pending_set"``, ``"pending_clear"``, ``"blocked"``,
            or ``"overflow"``.
        identity: Stable fields and aliases for the reservation.
        last_observed_actual: Redacted last actual state for
            diagnostics and migration.
        updated_at: Last Store update time.
        fingerprint_history: Prior fingerprints for conservative
            rematch after date shifts.
        missing_count: Consecutive feed misses while persisted.
        lockname: Keymaster lock scope for migration checks.
        start_slot: Managed range start at time of record creation.
        max_slots: Managed range length at time of record creation.
        operation_id: Persisted fence token for an in-flight set or
            clear.
        operation_kind: ``"set"`` or ``"clear"`` while an operation
            is pending.
        pending_set_since: When a set became in-flight and not yet
            verified.
        pending_clear_since: When clear became unconfirmed.
    """

    schema_version: int
    entry_id: str
    identity_key: str
    slot: int
    status: str
    identity: StoredIdentity
    last_observed_actual: StoredActual
    updated_at: datetime
    fingerprint_history: list[str] = field(default_factory=list)
    missing_count: int = 0
    lockname: str | None = None
    start_slot: int = 0
    max_slots: int = 0
    operation_id: str | None = None
    operation_kind: str | None = None
    pending_set_since: datetime | None = None
    pending_clear_since: datetime | None = None

    def __post_init__(self) -> None:
        """Validate SlotMapping field invariants.

        Raises:
            ValueError: If ``missing_count`` is negative, or if
                ``schema_version`` is less than 1.
        """
        if self.schema_version < 1:
            raise ValueError(f"schema_version must be >= 1, got {self.schema_version}")
        if self.missing_count < 0:
            raise ValueError(
                f"missing_count must be non-negative, got {self.missing_count}"
            )
