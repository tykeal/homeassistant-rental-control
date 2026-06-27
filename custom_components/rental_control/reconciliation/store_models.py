# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Cache-only Store dataclasses."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class CacheOnlyStoreRecord:
    """Cache-only Store payload metadata; never authoritative for planning."""

    schema_version: int
    entry_id: str
    lockname: str | None = None
    updated_at: str | None = None
    aliases: dict[str, Any] = field(default_factory=dict)
    last_plan: dict[str, Any] = field(default_factory=dict)
    migration_notes: list[str] = field(default_factory=list)


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
