# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Pure data models for the shared door-code allocator."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from enum import StrEnum


class AllocationOrigin(StrEnum):
    """Describe how an allocation owner obtained a code."""

    PREFERRED = "preferred"
    COLLISION_RESOLVED = "collision_resolved"
    ADOPTED = "adopted"


@dataclass(slots=True)
class AllocationOwner:
    """One reservation identity that owns an allocation record."""

    entry_id: str
    identity_key: str
    origin: AllocationOrigin
    lockname: str | None = None
    slot: int | None = None
    lock_observed: bool = False
    first_seen: str = ""
    last_seen: str = ""

    def __post_init__(self) -> None:
        """Validate owner identity and physical slot metadata."""
        if not self.entry_id:
            raise ValueError("entry_id must be non-empty")
        if not self.identity_key:
            raise ValueError("identity_key must be non-empty")
        if not isinstance(self.origin, AllocationOrigin):
            self.origin = AllocationOrigin(self.origin)
        if self.slot is not None and (
            not isinstance(self.slot, int) or isinstance(self.slot, bool)
        ):
            raise ValueError("slot must be a positive integer or None")
        if self.slot is not None and self.slot < 1:
            raise ValueError("slot must be a positive integer or None")
        if (self.lockname is None) != (self.slot is None):
            raise ValueError("lockname and slot must both be set or both be None")


@dataclass(slots=True)
class AllocationRecord:
    """One plain in-memory code and the reservations that own it."""

    code: str = field(repr=False)
    code_ref: str
    encoding_salt_value: str
    encoding_salt_source: str = "entry_id"
    code_length: int = 4
    owners: list[AllocationOwner] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        """Validate code shape and required ownership metadata."""
        if not isinstance(self.code_length, int) or isinstance(self.code_length, bool):
            raise ValueError("code_length must be a positive integer")
        if self.code_length < 1:
            raise ValueError("code_length must be positive")
        if not self.code.isdecimal() or len(self.code) != self.code_length:
            raise ValueError("code must be decimal digits matching code_length")
        if not self.code_ref:
            raise ValueError("code_ref must be non-empty")
        if self.encoding_salt_source != "entry_id":
            raise ValueError("encoding_salt_source must be 'entry_id'")
        if not self.encoding_salt_value:
            raise ValueError("encoding_salt_value must be non-empty")
        if not self.owners:
            raise ValueError("allocation records must have at least one owner")


@dataclass(frozen=True, slots=True)
class AllocationRequest:
    """Request a code for one reservation identity."""

    entry_id: str
    identity_key: str
    preferred_code: str
    code_length: int
    fingerprint_history: frozenset[str] = frozenset()
    previously_published: bool = False
    lockname: str | None = None
    slot: int | None = None
    issuance_allowed: bool = True


@dataclass(frozen=True, slots=True)
class AllocationResult:
    """Result of allocation or adoption."""

    code: str | None = field(default=None, repr=False)
    origin: AllocationOrigin | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class AdoptionRequest:
    """Record an observed lock code as owned by a reservation."""

    entry_id: str
    identity_key: str
    code: str
    code_length: int
    lockname: str
    slot: int
    fingerprint_history: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class CycleObservation:
    """Physical slot state observed for one entry refresh cycle.

    ``unreadable_slots`` is all managed slots whose observed status is
    ``SlotStatus.UNKNOWN``.  ``blocked_reason`` is diagnostic only and
    ``SlotStatus.FREE`` is known-empty, not unreadable.
    """

    entry_id: str
    lockname: str | None
    managed_slots: frozenset[int]
    observed_codes: dict[str, int]
    unreadable_slots: frozenset[int]


@dataclass(frozen=True, slots=True)
class CycleRequest:
    """Batch allocator request for one entry refresh cycle."""

    observation: CycleObservation
    adoptions: list[AdoptionRequest]
    rekeys: list[tuple[str, str]]
    allocations: list[AllocationRequest]
    active_keys: set[str]


@dataclass(frozen=True, slots=True)
class OrphanOutcome:
    """Report one released or retained owner without exposing a code."""

    code_ref: str
    entry_id: str
    identity_key: str
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ReleaseReport:
    """Report owners released or retained by a sweep-like operation."""

    released: list[OrphanOutcome] = field(default_factory=list)
    retained: list[OrphanOutcome] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class CycleResult:
    """Batch allocator result for one entry refresh cycle."""

    adopted: dict[str, AllocationResult]
    allocated: dict[str, AllocationResult]
    released: ReleaseReport
    unaccounted_slots: frozenset[int]


@dataclass(frozen=True, slots=True)
class OrphanCleanupReport:
    """Report operator cleanup of orphaned allocation owners."""

    dry_run: bool
    cleared: list[OrphanOutcome] = field(default_factory=list)
    retained: list[OrphanOutcome] = field(default_factory=list)
