# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Slot reconciliation data models for Rental Control.

Provides typed dataclasses, enumerations, and identity-matching helpers
used by the slot reconciliation system: the deterministic planner that
assigns RC-managed Keymaster slots to calendar reservations.

Exported names
--------------
FINGERPRINT_VERSION
    Version tag for the v1 primary reservation fingerprint scheme.
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
RematchKind
    Classification of a reservation identity rematch result.
RematchResult
    Result of a reservation identity rematch lookup.
normalize_slot_name_for_fingerprint
    Return the stable normalized form of a slot name for fingerprinting.
make_reservation_fingerprint
    Compute the stable versioned identity fingerprint for a reservation.
extract_booking_aliases
    Extract booking/confirmation aliases from event text.
find_reservation_rematch
    Find the best identity rematch for a reservation in persisted mappings.
compute_desired_plan
    Compute the deterministic desired slot plan for a set of reservations.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timezone
from enum import Enum
import hashlib
import logging
import re
from typing import Any

FINGERPRINT_VERSION = "v1"
"""Version tag for the primary reservation fingerprint scheme.

If the fingerprint algorithm changes in a future release this tag
must be bumped (e.g. to ``"v2"``) so that v1 and v2 fingerprints
occupy separate namespaces and old persisted keys can be preserved
in :attr:`Reservation.fingerprint_history` for conservative rematch.
"""

# Airbnb confirmation codes: one uppercase letter followed by nine
# uppercase alphanumeric characters (e.g. "HMXXXXXXXX").
_LOGGER = logging.getLogger(__name__)

_AIRBNB_CONF_RE = re.compile(r"(?<![A-Z0-9])([A-Z][A-Z0-9]{9})(?![A-Z0-9])")


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
        last_error: Description of the most recent failed operation for
            this slot, if any.  Populated by the apply-plan phase and
            carried forward to the next refresh cycle for diagnostics.
            Not persisted in the HA Store.
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
        start: Optional ISO-8601 reservation start used for NAME_TIME rematch.
        end: Optional ISO-8601 reservation end used for NAME_TIME rematch.
        uid_aliases: Volatile iCal UIDs seen for this reservation.
        booking_aliases: Optional extracted booking/confirmation IDs.
    """

    identity_key: str
    summary: str
    slot_name: str
    start: str | None = None
    end: str | None = None
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


def normalize_slot_name_for_fingerprint(slot_name: str) -> str:
    """Return the stable normalized form of a slot name for fingerprinting.

    Strips leading/trailing whitespace and case-folds (Unicode NFKD
    casefold) so that names that differ only in case or surrounding
    whitespace produce identical fingerprints.

    Args:
        slot_name: Unprefixed, untrimmed guest-facing slot name.

    Returns:
        Casefold-normalized, stripped slot name.
    """
    return slot_name.strip().casefold()


def _dt_to_utc_iso(dt: datetime) -> str:
    """Convert *dt* to a UTC ISO-8601 string for fingerprint computation.

    Naive datetimes are treated as UTC.  The output format is always
    ``YYYY-MM-DDTHH:MM:SS+00:00`` to ensure a single canonical
    representation regardless of the original timezone offset.

    Args:
        dt: Input datetime, timezone-aware or naive.

    Returns:
        Fixed-format UTC ISO-8601 string.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")


def make_reservation_fingerprint(
    entry_id: str,
    slot_name: str,
    start: datetime,
    end: datetime,
) -> str:
    """Compute the stable versioned identity fingerprint for a reservation.

    The fingerprint is a 64-character lowercase SHA-256 hexdigest of a
    canonical string built from:

    - :data:`FINGERPRINT_VERSION` prefix (``"v1"``),
    - *entry_id* (config-entry scope),
    - normalized, casefold-stripped *slot_name*,
    - UTC ISO-8601 *start*, and
    - UTC ISO-8601 *end*.

    The fingerprint deliberately excludes volatile calendar UIDs so that
    platform UID churn does not change the primary identity key.

    Args:
        entry_id: Config entry ID that scopes this fingerprint to one
            integration instance.
        slot_name: Unprefixed, untrimmed guest-facing slot name.  Will
            be normalized via :func:`normalize_slot_name_for_fingerprint`.
        start: Reservation start datetime (any timezone; converted to
            UTC before hashing).
        end: Reservation end datetime (any timezone; converted to UTC
            before hashing).

    Returns:
        64-character lowercase SHA-256 hexdigest string.
    """
    normalized_name = normalize_slot_name_for_fingerprint(slot_name)
    start_utc = _dt_to_utc_iso(start)
    end_utc = _dt_to_utc_iso(end)
    canonical = (
        f"{FINGERPRINT_VERSION}:{entry_id}:{normalized_name}:{start_utc}:{end_utc}"
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def extract_booking_aliases(summary: str, description: str) -> set[str]:
    """Extract booking/confirmation aliases from an event's text fields.

    Searches the combined *summary* and *description* text for known
    booking-platform confirmation codes.  Currently extracts:

    - **Airbnb** confirmation codes: one uppercase letter followed by
      nine uppercase alphanumeric characters (e.g. ``HMXXXXXXXX``).

    The extracted aliases are stored as :attr:`Reservation.booking_aliases`
    and used as secondary signals in :func:`find_reservation_rematch`.

    Args:
        summary: Raw iCal SUMMARY field value.
        description: Raw iCal DESCRIPTION field value; may be empty or
            ``None``-like (empty string is safe).

    Returns:
        Set of extracted booking identifier strings; empty when none
        are detected.
    """
    aliases: set[str] = set()
    text = f"{summary} {description or ''}"
    for m in _AIRBNB_CONF_RE.finditer(text):
        aliases.add(m.group(1))
    return aliases


class RematchKind(str, Enum):
    """Classification of a reservation identity rematch result.

    Values follow the six-rule matching hierarchy from R-002 in
    ``specs/012-slot-reconciliation/research.md``.  Lower-numbered rules
    take precedence: :func:`find_reservation_rematch` returns the *first*
    matching rule for each candidate reservation.
    """

    EXACT = "exact"
    """Rule 1: primary fingerprint found unchanged in persisted mappings."""

    UID_ALIAS = "uid_alias"
    """Rule 2: a volatile UID alias overlaps; name also matches.

    :attr:`RematchResult.date_shifted` is always ``True`` when this
    kind is returned, because if the dates had not changed the primary
    fingerprint would have matched under rule 1 instead.
    """

    BOOKING_ALIAS = "booking_alias"
    """Rule 3: a booking/confirmation alias overlaps; name also matches."""

    NAME_TIME = "name_time"
    """Rule 4: normalized name plus exact UTC start/end match.

    Triggered when no alias evidence is available but the persisted
    identity dict's ``slot_name``, ``start``, and ``end`` match the
    incoming reservation exactly.  Acts as a migration safety net.
    """

    CONTINUITY = "continuity"
    """Rule 5: conservative continuity rematch.

    Exactly one persisted mapping is compatible based on fingerprint
    history, booking aliases, or normalized name with actual-slot
    evidence, and no other current reservation competes for it.
    """

    AMBIGUOUS = "ambiguous"
    """Two or more candidates are equally compatible; no rematch is made.

    Can arise from rule 2 (multiple UID alias matches), rule 3 (multiple
    booking alias matches), or rule 5 (multiple continuity-compatible
    mappings).  :attr:`RematchResult.ambiguous_keys` lists all
    compatible candidates for diagnostic capture.
    """

    NO_MATCH = "no_match"
    """No compatible persisted mapping was found under any rule."""


@dataclass(slots=True)
class RematchResult:
    """Result of a reservation identity rematch lookup.

    Produced by :func:`find_reservation_rematch` and consumed by the
    coordinator's identity-resolution step to decide whether and how
    to update the persisted slot mapping.

    Attributes:
        kind: Classification of the match found.
        matched_identity_key: Primary identity key of the persisted
            mapping that matched.  ``None`` for ``AMBIGUOUS`` and
            ``NO_MATCH`` results.
        date_shifted: ``True`` when the match was established via a UID
            alias but the incoming reservation's dates differ from the
            persisted fingerprint.  Always ``False`` for non-UID-alias
            matches.  When ``True`` and ``should_update_code`` is also
            ``True`` in the coordinator config, the coordinator should
            regenerate the access code alongside the date update.
        ambiguous_keys: Identity keys of all compatible candidates when
            *kind* is ``AMBIGUOUS``; empty otherwise.
    """

    kind: RematchKind
    matched_identity_key: str | None
    date_shifted: bool = False
    ambiguous_keys: list[str] = field(default_factory=list)


def _get_nested(d: dict[str, Any], *keys: str) -> Any:
    """Safely navigate nested dict keys; return ``None`` on any miss."""
    current: Any = d
    for k in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(k)
    return current


def _is_continuity_compatible(
    reservation: Reservation,
    mapping_key: str,
    mapping: dict[str, Any],
    res_norm_name: str,
    actual_slot_names: dict[int, str] | None,
) -> bool:
    """Return ``True`` if *mapping* is continuity-compatible with *reservation*.

    A mapping is continuity-compatible when:

    1. Normalized names match (required baseline).
    2. At least one of the following weak signals is present:

       a. The reservation's current identity key appears in the mapping's
          ``fingerprint_history`` (a prior fingerprint for this mapping).
       b. The mapping's primary ``identity_key`` appears in the
          reservation's :attr:`Reservation.fingerprint_history`.
       c. A booking alias overlaps between the two records.
       d. The actual Keymaster slot name for the mapping's slot matches
          the reservation's normalized name (actual-slot continuity).

    Args:
        reservation: The incoming reservation candidate.
        mapping_key: Identity key of the persisted mapping being tested.
        mapping: Raw Store mapping dict.
        res_norm_name: Pre-computed normalized name for *reservation*.
        actual_slot_names: Optional mapping of slot number → current
            Keymaster slot name for actual-slot continuity checks.

    Returns:
        ``True`` if the mapping is continuity-compatible with the
        reservation; ``False`` otherwise.
    """
    persisted_name = normalize_slot_name_for_fingerprint(
        _get_nested(mapping, "identity", "slot_name") or ""
    )
    if persisted_name != res_norm_name:
        return False

    # Signal (a): reservation's current fingerprint in mapping's history
    fp_history: list[str] = mapping.get("fingerprint_history") or []
    if reservation.identity_key in fp_history:
        return True

    # Signal (b): persisted key in reservation's fingerprint history
    if mapping_key in reservation.fingerprint_history:
        return True

    # Signal (c): booking alias overlap
    persisted_booking: set[str] = set(
        _get_nested(mapping, "identity", "booking_aliases") or []
    )
    if persisted_booking & reservation.booking_aliases:
        return True

    # Signal (d): actual Keymaster slot name matches
    if actual_slot_names is not None:
        slot_num: int | None = mapping.get("slot")
        if slot_num is not None:
            actual_name = actual_slot_names.get(slot_num)
            if actual_name is not None:
                if normalize_slot_name_for_fingerprint(actual_name) == res_norm_name:
                    return True

    return False


def _has_competing_reservation(
    candidate_key: str,
    persisted_mappings: dict[str, dict[str, Any]],
    current_reservations: list[Reservation] | None,
    this_reservation: Reservation,
) -> bool:
    """Return ``True`` if another current reservation also matches *candidate_key*.

    Used by :func:`find_reservation_rematch` to verify that a single
    continuity candidate is not also claimed by another current
    reservation (which would make the rematch ambiguous even though
    only one persisted mapping is compatible).

    Args:
        candidate_key: Identity key of the single compatible mapping.
        persisted_mappings: Full raw Store mapping dict.
        current_reservations: All current reservations being reconciled.
        this_reservation: The reservation being matched (excluded from
            the competition check).

    Returns:
        ``True`` if at least one other current reservation competes for
        the candidate mapping by normalized name; ``False`` otherwise.
    """
    if current_reservations is None:
        return False
    candidate_mapping = persisted_mappings.get(candidate_key, {})
    candidate_norm_name = normalize_slot_name_for_fingerprint(
        _get_nested(candidate_mapping, "identity", "slot_name") or ""
    )
    for other in current_reservations:
        if other.identity_key == this_reservation.identity_key:
            continue  # skip self
        if normalize_slot_name_for_fingerprint(other.slot_name) == candidate_norm_name:
            return True
    return False


def find_reservation_rematch(
    reservation: Reservation,
    persisted_mappings: dict[str, dict[str, Any]],
    current_reservations: list[Reservation] | None = None,
    actual_slot_names: dict[int, str] | None = None,
) -> RematchResult:
    """Find the best identity rematch for *reservation* in *persisted_mappings*.

    Implements the six-rule matching hierarchy described in R-002 of
    ``specs/012-slot-reconciliation/research.md``:

    1. **Exact fingerprint**: if the reservation's ``identity_key`` is
       already a key in *persisted_mappings*, return
       :attr:`RematchKind.EXACT`.
    2. **UID alias + name**: if any volatile UID alias in the incoming
       reservation overlaps with a persisted mapping's ``uid_aliases``
       *and* the normalized names match, return
       :attr:`RematchKind.UID_ALIAS` with ``date_shifted=True`` (because
       the fingerprint would have matched under rule 1 if dates were
       unchanged).  If multiple mappings match, return AMBIGUOUS.
    3. **Booking alias + name**: if any booking/confirmation alias
       overlaps *and* names match, return
       :attr:`RematchKind.BOOKING_ALIAS`.  If multiple mappings match
       the same alias, return AMBIGUOUS.
    4. **Name + exact start/end**: compare the normalized name and exact
       UTC start/end stored in the persisted mapping's ``identity`` dict.
       Returns :attr:`RematchKind.NAME_TIME` when both match.  Serves
       as a migration safety net.
    5. **Conservative continuity**: collect all continuity-compatible
       candidates (see :func:`_is_continuity_compatible`).  If exactly
       one is found and no other current reservation competes for it,
       return :attr:`RematchKind.CONTINUITY`.  If multiple candidates
       are compatible, return :attr:`RematchKind.AMBIGUOUS` with all
       keys for diagnostic capture.
    6. **No match**: return :attr:`RematchKind.NO_MATCH`.

    Args:
        reservation: The incoming reservation to resolve against stored
            mappings.
        persisted_mappings: Raw Store mapping dicts keyed by identity
            key (as loaded by
            :meth:`~rental_control.event_overrides.EventOverrides.load_persisted_mappings`).
        current_reservations: All current reservations being reconciled
            in this refresh cycle.  Used for competition checking in
            the conservative continuity rematch.  Optional.
        actual_slot_names: Mapping of Keymaster slot number to the
            currently observed slot name entity state.  Provides the
            actual-slot continuity signal in rule 5.  Optional.

    Returns:
        A :class:`RematchResult` describing the best match found.
    """
    # Rule 1: exact primary fingerprint match
    if reservation.identity_key in persisted_mappings:
        return RematchResult(
            kind=RematchKind.EXACT,
            matched_identity_key=reservation.identity_key,
        )

    res_norm_name = normalize_slot_name_for_fingerprint(reservation.slot_name)

    # Rule 2: UID alias + normalized name match
    # If a UID alias matches but rule 1 did not fire, the fingerprint must
    # differ.  Given that the fingerprint encodes entry_id + name + start +
    # end, and entry_id is constant per instance, different fingerprint with
    # matching name means the dates shifted → date_shifted=True always.
    uid_matches: list[str] = []
    for mapping_key, mapping in persisted_mappings.items():
        persisted_uids: set[str] = set(
            _get_nested(mapping, "identity", "uid_aliases") or []
        )
        if persisted_uids & reservation.uid_aliases:
            persisted_name = normalize_slot_name_for_fingerprint(
                _get_nested(mapping, "identity", "slot_name") or ""
            )
            if persisted_name == res_norm_name:
                uid_matches.append(mapping_key)

    if len(uid_matches) == 1:
        return RematchResult(
            kind=RematchKind.UID_ALIAS,
            matched_identity_key=uid_matches[0],
            date_shifted=True,
        )
    if len(uid_matches) > 1:
        return RematchResult(
            kind=RematchKind.AMBIGUOUS,
            matched_identity_key=None,
            ambiguous_keys=uid_matches,
        )

    # Rule 3: booking alias + normalized name match
    # Collect all matches to detect ambiguous scenarios (e.g. duplicate
    # booking codes across two persisted mappings).
    booking_matches: list[str] = []
    for mapping_key, mapping in persisted_mappings.items():
        persisted_booking: set[str] = set(
            _get_nested(mapping, "identity", "booking_aliases") or []
        )
        if persisted_booking & reservation.booking_aliases:
            persisted_name = normalize_slot_name_for_fingerprint(
                _get_nested(mapping, "identity", "slot_name") or ""
            )
            if persisted_name == res_norm_name:
                booking_matches.append(mapping_key)

    if len(booking_matches) == 1:
        return RematchResult(
            kind=RematchKind.BOOKING_ALIAS,
            matched_identity_key=booking_matches[0],
        )
    if len(booking_matches) > 1:
        return RematchResult(
            kind=RematchKind.AMBIGUOUS,
            matched_identity_key=None,
            ambiguous_keys=booking_matches,
        )

    # Rule 4: name + exact start/end stored in identity dict
    for mapping_key, mapping in persisted_mappings.items():
        persisted_name = normalize_slot_name_for_fingerprint(
            _get_nested(mapping, "identity", "slot_name") or ""
        )
        if persisted_name != res_norm_name:
            continue
        start_iso: str | None = _get_nested(mapping, "identity", "start")
        end_iso: str | None = _get_nested(mapping, "identity", "end")
        if start_iso is None or end_iso is None:
            continue
        try:
            p_start = datetime.fromisoformat(start_iso)
            p_end = datetime.fromisoformat(end_iso)
        except (ValueError, TypeError):  # fmt: skip
            continue
        # Normalize both sides to UTC for comparison
        res_s = (
            reservation.start.astimezone(timezone.utc)
            if reservation.start.tzinfo
            else reservation.start.replace(tzinfo=timezone.utc)
        )
        res_e = (
            reservation.end.astimezone(timezone.utc)
            if reservation.end.tzinfo
            else reservation.end.replace(tzinfo=timezone.utc)
        )
        p_s = (
            p_start.astimezone(timezone.utc)
            if p_start.tzinfo
            else p_start.replace(tzinfo=timezone.utc)
        )
        p_e = (
            p_end.astimezone(timezone.utc)
            if p_end.tzinfo
            else p_end.replace(tzinfo=timezone.utc)
        )
        if res_s == p_s and res_e == p_e:
            return RematchResult(
                kind=RematchKind.NAME_TIME,
                matched_identity_key=mapping_key,
            )

    # Rule 5: conservative continuity rematch
    candidates: list[str] = [
        mapping_key
        for mapping_key, mapping in persisted_mappings.items()
        if _is_continuity_compatible(
            reservation,
            mapping_key,
            mapping,
            res_norm_name,
            actual_slot_names,
        )
    ]

    if len(candidates) == 1:
        if not _has_competing_reservation(
            candidates[0],
            persisted_mappings,
            current_reservations,
            reservation,
        ):
            return RematchResult(
                kind=RematchKind.CONTINUITY,
                matched_identity_key=candidates[0],
            )
        # Competition found → treat as ambiguous even with one candidate
        return RematchResult(
            kind=RematchKind.AMBIGUOUS,
            matched_identity_key=None,
            ambiguous_keys=list(candidates),
        )

    if len(candidates) > 1:
        return RematchResult(
            kind=RematchKind.AMBIGUOUS,
            matched_identity_key=None,
            ambiguous_keys=list(candidates),
        )

    return RematchResult(kind=RematchKind.NO_MATCH, matched_identity_key=None)


def _find_persisted_slot_for_reservation(
    managed_slots: list[ManagedSlot], identity_key: str
) -> ManagedSlot | None:
    """Return the managed slot that has *identity_key* as its persisted assignment.

    Scans only slots where :attr:`ManagedSlot.managed` is ``True``.
    Returns ``None`` if no such slot is found.

    Args:
        managed_slots: Full list of managed and unmanaged slots.
        identity_key: Reservation identity key to look up.

    Returns:
        The first managed slot whose :attr:`ManagedSlot.persisted_identity_key`
        matches *identity_key*, or ``None``.
    """
    for ms in managed_slots:
        if ms.managed and ms.persisted_identity_key == identity_key:
            return ms
    return None


def _is_slot_assignable(ms: ManagedSlot) -> bool:
    """Return ``True`` when *ms* can receive a new desired assignment.

    Slots with :attr:`SlotStatus.PENDING_CLEAR`, :attr:`SlotStatus.BLOCKED`,
    or :attr:`SlotStatus.UNKNOWN` status cannot receive a different reservation
    until their status resolves.  All other statuses (FREE, OCCUPIED, PHANTOM)
    are considered assignable by the planner.

    Args:
        ms: Managed slot to evaluate.

    Returns:
        ``True`` if the slot may be included in a desired assignment;
        ``False`` if the slot must be skipped.
    """
    return ms.status not in {
        SlotStatus.PENDING_CLEAR,
        SlotStatus.BLOCKED,
        SlotStatus.UNKNOWN,
    }


def _compute_drift_fields(ms: ManagedSlot, res: Reservation) -> list[str]:
    """Return the list of field names that differ between actual and desired state.

    Compares the observed :class:`ManagedSlot` fields against the desired
    :class:`Reservation` to detect manual or external changes to a managed
    Keymaster slot.  Only fields where an observed value is available are
    checked; ``None`` observations are skipped rather than treated as drift.

    Raw PIN values are never compared or returned; only code *presence* is
    checked via :attr:`ManagedSlot.actual_code_present`.

    Fields checked:

    - **name**: :attr:`~ManagedSlot.actual_name` vs
      :attr:`~Reservation.display_slot_name`.
    - **code**: :attr:`~ManagedSlot.actual_code_present` is ``False`` when
      a code should be present (i.e., the slot is occupied).
    - **start**: :attr:`~ManagedSlot.actual_start` vs
      :attr:`~Reservation.buffered_start`.
    - **end**: :attr:`~ManagedSlot.actual_end` vs
      :attr:`~Reservation.buffered_end`.
    - **date_range_enabled**: :attr:`~ManagedSlot.date_range_enabled` is
      ``False`` when dates are configured, indicating the switch was turned
      off manually.

    Args:
        ms: The managed slot with observed Keymaster state.
        res: The desired reservation whose fields represent the expected state.

    Returns:
        Sorted list of field name strings that differ; empty when no drift is
        detected.
    """
    fields: list[str] = []

    # Name drift: actual Keymaster display name differs from desired
    if ms.actual_name is not None and ms.actual_name != res.display_slot_name:
        fields.append("name")

    # Code presence drift: slot should have a code but one was removed
    if ms.actual_code_present is False:
        fields.append("code")

    # Start date drift
    if ms.actual_start is not None and ms.actual_start != res.buffered_start:
        fields.append("start")

    # End date drift
    if ms.actual_end is not None and ms.actual_end != res.buffered_end:
        fields.append("end")

    # Date-range switch drift: switch should be enabled for date-ranged slots
    if ms.date_range_enabled is False:
        fields.append("date_range_enabled")

    return fields


def _filter_eligible(reservations: list[Reservation]) -> list[Reservation]:
    """Return the subset of *reservations* eligible for slot planning.

    Excludes ineligible, checked-out, and feed-missed reservations.
    Protected active reservations bypass the missing-count filter because
    an active guest must stay assigned regardless of feed gaps.

    Args:
        reservations: Full list of current reservations.

    Returns:
        Filtered list containing only candidates that may be selected.
    """
    result: list[Reservation] = []
    for res in reservations:
        if not res.eligible or res.checked_out:
            continue
        if res.missing_count >= 3 and not res.protected_active:
            continue
        result.append(res)
    return result


def _select_candidates(
    eligible: list[Reservation],
    max_events: int,
) -> tuple[list[Reservation], list[Reservation], list[Reservation], int]:
    """Partition *eligible* into protected, selected-non-protected, and overflow.

    Protected active reservations are always selected first and count against
    ``max_events``.  The remaining capacity is filled by the soonest
    non-protected reservations sorted by ``(start, identity_key)``.

    Args:
        eligible: Pre-filtered list of eligible reservations.
        max_events: Maximum total assignments allowed.

    Returns:
        A four-tuple of
        ``(protected, selected_np, overflow_list, remaining_capacity)``
        where *remaining_capacity* is ``max(0, max_events - len(protected))``.
    """
    protected = [r for r in eligible if r.protected_active]
    non_protected = [r for r in eligible if not r.protected_active]
    non_protected.sort(key=lambda r: (r.start, r.identity_key))
    remaining_capacity = max(0, max_events - len(protected))
    return (
        protected,
        non_protected[:remaining_capacity],
        non_protected[remaining_capacity:],
        remaining_capacity,
    )


def _build_slot_action(
    ms: ManagedSlot,
    desired_key: str | None,
    res_by_key: dict[str, "Reservation"],
) -> tuple[ActionKind, str | None]:
    """Determine the reconciliation action for one managed slot.

    Compares the desired assignment against the slot's current observed
    state to produce the appropriate :class:`ActionKind` and an optional
    human-readable reason string.

    Args:
        ms: The managed slot being evaluated.
        desired_key: Identity key of the reservation the planner wants in
            this slot, or ``None`` if the slot should be empty.
        res_by_key: Mapping of identity key → :class:`Reservation` for
            date comparison.

    Returns:
        A two-tuple ``(action, pending_reason)``.
    """
    if ms.status is SlotStatus.PENDING_CLEAR:
        return ActionKind.RETRY_CLEAR, ms.blocked_reason or "pending_clear"

    if ms.status in {SlotStatus.BLOCKED, SlotStatus.UNKNOWN}:
        return ActionKind.BLOCKED, ms.blocked_reason or ms.status.value

    if desired_key is None:
        if ms.status is SlotStatus.FREE:
            return ActionKind.NOOP, None
        return ActionKind.CLEAR, None

    desired_res = res_by_key.get(desired_key)
    if ms.status is SlotStatus.FREE:
        return ActionKind.SET, None

    if ms.status is SlotStatus.OCCUPIED:
        if ms.persisted_identity_key != desired_key:
            return ActionKind.CLEAR, None

        # Detect non-date drift (manual/external name, code, or switch change).
        # Date-only drift is handled below as UPDATE_TIMES to preserve the
        # churn-minimisation invariant.  Unmanaged slots never reach this path
        # because compute_desired_plan iterates only managed == True slots.
        if desired_res is not None:
            drift_fields = _compute_drift_fields(ms, desired_res)
            non_date_drift = [f for f in drift_fields if f not in ("start", "end")]
            if non_date_drift:
                # Include all drifted fields (including dates if also wrong) in
                # the reason so diagnostics capture the full picture.
                reason = "drifted fields: " + ", ".join(drift_fields)
                return ActionKind.OVERWRITE_MANUAL_CHANGE, reason

        if desired_res is not None and (
            ms.actual_start is not None or ms.actual_end is not None
        ):
            dates_match = (
                ms.actual_start == desired_res.buffered_start
                and ms.actual_end == desired_res.buffered_end
            )
            return (ActionKind.NOOP if dates_match else ActionKind.UPDATE_TIMES), None
        return ActionKind.NOOP, None

    return ActionKind.CLEAR, None


def _build_plan_diagnostics_snapshot(
    plan: DesiredPlan,
    reservations: list[Reservation],
    max_events: int,
    *,
    entry_id: str | None = None,
    lockname: str | None = None,
    start_slot: int | None = None,
) -> dict[str, Any]:
    """Build a comprehensive diagnostics snapshot for *plan*.

    Produces a dict capturing plan metadata, per-slot desired/actual/action/
    blocked_reason/retry_count/last_error, and per-reservation
    selected/protected/overflow/missing_count/assigned_slot/uid_aliases/
    booking_aliases.  Raw slot codes are deliberately excluded.

    Called once per refresh at the end of :func:`compute_desired_plan`
    after ``plan.slots`` and ``plan.actions`` are fully populated.

    Args:
        plan: The partially-built :class:`DesiredPlan` whose
            :attr:`~DesiredPlan.slots`, :attr:`~DesiredPlan.selected`,
            :attr:`~DesiredPlan.protected`, and :attr:`~DesiredPlan.overflow`
            are already set.
        reservations: All current reservations (eligible and ineligible).
        max_events: Maximum number of reservations that can be assigned.
        entry_id: Optional config-entry scope for diagnostics context.
        lockname: Optional Keymaster lock name for diagnostics context.
        start_slot: Optional managed-range start for diagnostics context.

    Returns:
        Populated diagnostics dict suitable for
        :attr:`DesiredPlan.diagnostics`.
    """
    existing_diag: dict[str, Any] = dict(plan.diagnostics)

    diag: dict[str, Any] = {
        "plan_id": plan.plan_id,
        "generated_at": plan.generated_at.isoformat(),
        "max_slots": max_events,
    }
    if entry_id is not None:
        diag["entry_id"] = entry_id
    if lockname is not None:
        diag["lockname"] = lockname
    if start_slot is not None:
        diag["start_slot"] = start_slot

    # Per-slot diagnostics (no raw codes)
    slots_diag: dict[int, dict[str, Any]] = {}
    for slot_num, ps in plan.slots.items():
        slot_entry: dict[str, Any] = {
            "desired_identity_key": ps.desired_identity_key,
            "actual_classification": ps.actual_classification,
            "action": ps.action.value,
            "blocked_reason": ps.pending_reason,
            "retry_count": ps.retry_count,
            "last_error": ps.last_error,
        }
        # Include parsed drift fields for OVERWRITE_MANUAL_CHANGE actions so
        # diagnostics consumers can enumerate exactly which fields were wrong.
        if ps.action is ActionKind.OVERWRITE_MANUAL_CHANGE and ps.pending_reason:
            prefix = "drifted fields: "
            if ps.pending_reason.startswith(prefix):
                slot_entry["drift_fields"] = [
                    f.strip()
                    for f in ps.pending_reason[len(prefix) :].split(",")
                    if f.strip()
                ]
        slots_diag[slot_num] = slot_entry
    diag["slots"] = slots_diag

    # Per-reservation diagnostics (slot_code intentionally excluded)
    res_diag: dict[str, dict[str, Any]] = {}
    for res in reservations:
        ikey = res.identity_key
        res_diag[ikey] = {
            "selected": ikey in plan.selected,
            "protected": ikey in plan.protected,
            "overflow_reason": plan.overflow.get(ikey),
            "missing_count": res.missing_count,
            "assigned_slot": plan.selected.get(ikey),
            "uid_aliases": sorted(res.uid_aliases),
            "booking_aliases": sorted(res.booking_aliases),
            "slot_name": res.slot_name,
            "summary": res.summary,
            "eligible": res.eligible,
            "protected_active": res.protected_active,
            "checked_out": res.checked_out,
        }
    diag["reservations"] = res_diag

    # Carry over pre-existing diagnostics keys not overwritten above.
    for k, v in existing_diag.items():
        diag.setdefault(k, v)

    return diag


def compute_desired_plan(
    reservations: list[Reservation],
    managed_slots: list[ManagedSlot],
    max_events: int,
    plan_id: str,
    generated_at: datetime,
    *,
    entry_id: str | None = None,
    lockname: str | None = None,
    start_slot: int | None = None,
) -> DesiredPlan:
    """Compute the deterministic desired slot plan for the current set of reservations.

    **Selection**: eligible candidates are filtered, protected active reservations
    are always selected first (counting against ``max_events``), and remaining
    capacity is filled by soonest non-protected sorted by ``(start, identity_key)``.
    Overflow reservations receive reason ``"capacity"`` with per-entry rank.

    **Slot assignment**: protected and selected reservations retain their persisted
    slot when it is assignable; otherwise the lowest free managed slot is used.
    ``PENDING_CLEAR``, ``BLOCKED``, and ``UNKNOWN`` slots are never assigned.

    **Action generation**: ``SET`` for a free slot, ``UPDATE_TIMES`` when dates
    differ, ``NOOP`` when already correct, ``CLEAR`` for stale/phantom/wrong
    occupants, ``RETRY_CLEAR`` for pending clears, and ``BLOCKED`` for locked
    slots.  ``NOOP`` actions are excluded from :attr:`DesiredPlan.actions`.

    **Diagnostics**: a comprehensive snapshot is stored in
    :attr:`DesiredPlan.diagnostics` capturing ``plan_id``, ``generated_at``,
    per-slot desired/actual/action/blocked_reason/retry_count/last_error, and
    per-reservation selected/protected/overflow/missing_count/assigned_slot/
    uid_aliases/booking_aliases.  Raw slot codes are never included.

    Args:
        reservations: All current reservations (eligible and ineligible).
        managed_slots: All managed slots with their current observed and
            persisted state.
        max_events: Maximum number of reservations that can be assigned.
        plan_id: Refresh-scoped identifier for logging and operation tokens.
        generated_at: Time at which the plan was computed.
        entry_id: Optional config-entry scope for diagnostics context.
        lockname: Optional Keymaster lock name for diagnostics context.
        start_slot: Optional managed-range start for diagnostics context.

    Returns:
        A fully populated :class:`DesiredPlan` with :attr:`~DesiredPlan.selected`,
        :attr:`~DesiredPlan.protected`, :attr:`~DesiredPlan.overflow`,
        :attr:`~DesiredPlan.slots`, :attr:`~DesiredPlan.actions`, and
        :attr:`~DesiredPlan.diagnostics` populated.
    """
    plan = DesiredPlan(plan_id=plan_id, generated_at=generated_at)

    eligible = _filter_eligible(reservations)
    protected, selected_np, overflow_list, remaining_capacity = _select_candidates(
        eligible, max_events
    )
    plan.protected = {r.identity_key for r in protected}

    assigned: dict[int, str] = {}
    free_slot_numbers: list[int] = sorted(
        ms.slot for ms in managed_slots if ms.managed and ms.status is SlotStatus.FREE
    )

    def _try_assign(res: Reservation) -> bool:
        """Try to assign *res* to its persisted slot or the lowest free slot."""
        persisted = _find_persisted_slot_for_reservation(
            managed_slots, res.identity_key
        )
        if (
            persisted is not None
            and _is_slot_assignable(persisted)
            and persisted.slot not in assigned
        ):
            assigned[persisted.slot] = res.identity_key
            if persisted.slot in free_slot_numbers:
                free_slot_numbers.remove(persisted.slot)
            return True
        if free_slot_numbers:
            assigned[free_slot_numbers.pop(0)] = res.identity_key
            return True
        return False

    for res in protected:
        if not _try_assign(res):
            plan.diagnostics.setdefault("protected_capacity_violations", []).append(
                res.identity_key
            )

    for res in selected_np:
        if not _try_assign(res):
            plan.overflow[res.identity_key] = "no_free_slot"
            _LOGGER.warning(
                "Overflow: reservation %s selected but no free managed slot available",
                res.identity_key,
            )

    for slot_num, ikey in assigned.items():
        plan.selected[ikey] = slot_num

    for rank_offset, res in enumerate(overflow_list):
        plan.overflow[res.identity_key] = "capacity"
        plan.diagnostics.setdefault("overflow_details", {})[res.identity_key] = {
            "rank": remaining_capacity + rank_offset + 1,
            "reason": "capacity",
            "start": res.start.isoformat(),
            "identity_key": res.identity_key,
        }

    slot_to_identity: dict[int, str] = {v: k for k, v in plan.selected.items()}
    res_by_key: dict[str, Reservation] = {r.identity_key: r for r in reservations}

    # Detect duplicate persisted assignments: same identity key in multiple OCCUPIED
    # managed slots.  The canonical slot is the lowest-numbered one.  All others are
    # non-canonical and will be cleared.
    _persisted_to_slots: dict[str, list[int]] = {}
    for _ms in managed_slots:
        if (
            _ms.managed
            and _ms.persisted_identity_key is not None
            and _ms.status is SlotStatus.OCCUPIED
        ):
            _persisted_to_slots.setdefault(_ms.persisted_identity_key, []).append(
                _ms.slot
            )
    _non_canonical_slots: set[int] = set()
    for _dup_key, _dup_slots in _persisted_to_slots.items():
        if len(_dup_slots) > 1:
            _canonical = min(_dup_slots)
            _non_canonical = [s for s in _dup_slots if s != _canonical]
            _non_canonical_slots.update(_non_canonical)
            _LOGGER.warning(
                "Duplicate assignment: identity %s found in slots %s; "
                "slot %d is canonical, slots %s will be cleared (duplicate collapse)",
                _dup_key,
                sorted(_dup_slots),
                _canonical,
                sorted(_non_canonical),
            )

    for ms in sorted(managed_slots, key=lambda m: m.slot):
        if not ms.managed:
            continue
        desired_key = slot_to_identity.get(ms.slot)
        action, pending_reason = _build_slot_action(ms, desired_key, res_by_key)
        if (
            action is ActionKind.RETRY_CLEAR
            and ms.persisted_identity_key in plan.protected
        ):
            action = ActionKind.BLOCKED
            pending_reason = "protected_active_pending_clear"
        ms.desired_identity_key = desired_key
        plan.slots[ms.slot] = PlannedSlot(
            slot=ms.slot,
            desired_identity_key=desired_key,
            actual_classification=ms.status.value,
            action=action,
            pending_reason=pending_reason,
            retry_count=ms.retry_count,
            last_error=ms.last_error,
        )
        if action is not ActionKind.NOOP:
            clear_reason: str | None = pending_reason
            if action is ActionKind.CLEAR:
                if ms.slot in _non_canonical_slots:
                    clear_reason = "duplicate_non_canonical"
                elif ms.status is SlotStatus.PHANTOM:
                    clear_reason = "phantom"
                elif ms.status is SlotStatus.OCCUPIED and desired_key is None:
                    clear_reason = "stale"
                elif ms.status is SlotStatus.OCCUPIED and desired_key is not None:
                    clear_reason = "mis_assigned"
            plan.actions.append(
                SlotAction(
                    kind=action,
                    slot=ms.slot,
                    identity_key=desired_key,
                    reason=clear_reason,
                )
            )

    plan.diagnostics = _build_plan_diagnostics_snapshot(
        plan,
        reservations,
        max_events,
        entry_id=entry_id,
        lockname=lockname,
        start_slot=start_slot,
    )

    return plan
