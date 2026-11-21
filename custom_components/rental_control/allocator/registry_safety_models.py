# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Typed, PIN-free records for original-slot protection and release history."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timedelta
from typing import Any

_FENCE_FIELDS = frozenset(
    {
        "fence_id",
        "original_km_entry_id",
        "slot",
        "rc_entry_id",
        "owner_ref",
        "allocation_ref",
        "created_transaction_id",
        "created_generation",
        "fence_type",
    }
)
_TOMBSTONE_FIELDS = frozenset(
    {
        "tombstone_id",
        "fence_id",
        "original_km_entry_id",
        "slot",
        "rc_entry_id",
        "owner_ref",
        "allocation_ref",
        "fence_type",
        "created_transaction_id",
        "created_generation",
        "resolved_transaction_id",
        "resolved_generation",
        "resolution_kind",
        "created_at",
    }
)
_FENCE_TYPES = frozenset({"allocation", "pending_clear"})
_RESOLUTION_KINDS = frozenset({"clear_confirmed", "owner_released"})


def _is_opaque_id(value: object) -> bool:
    """Validate a fixed-length lowercase transaction or record identifier."""
    return (
        isinstance(value, str)
        and len(value) == 32
        and all(character in "0123456789abcdef" for character in value)
    )


def _is_reference(value: object) -> bool:
    """Validate a bounded nonempty reference without interpreting its identity."""
    return (
        isinstance(value, str)
        and bool(value)
        and len(value) <= 256
        and value == value.strip()
        and all(character.isprintable() for character in value)
    )


def _is_positive_integer(value: object) -> bool:
    """Reject booleans and nonpositive values from integer safety fields."""
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def validate_pending_fence(record: object) -> bool:
    """Validate one original-entry/slot/owner fence and its creating version."""
    if not isinstance(record, dict) or set(record) != _FENCE_FIELDS:
        return False
    return bool(
        _is_opaque_id(record["fence_id"])
        and _is_reference(record["original_km_entry_id"])
        and _is_positive_integer(record["slot"])
        and _is_reference(record["rc_entry_id"])
        and _is_reference(record["owner_ref"])
        and _is_reference(record["allocation_ref"])
        and _is_opaque_id(record["created_transaction_id"])
        and _is_positive_integer(record["created_generation"])
        and isinstance(record["fence_type"], str)
        and record["fence_type"] in _FENCE_TYPES
    )


def _is_aware_timestamp(value: object) -> bool:
    """Accept only parseable timestamps with an explicit UTC offset."""
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def validate_resolution_tombstone(record: object) -> bool:
    """Validate retained original identity plus the resolving transaction."""
    if not isinstance(record, dict) or set(record) != _TOMBSTONE_FIELDS:
        return False
    created_generation = record["created_generation"]
    resolved_generation = record["resolved_generation"]
    return bool(
        _is_opaque_id(record["tombstone_id"])
        and _is_opaque_id(record["fence_id"])
        and _is_reference(record["original_km_entry_id"])
        and _is_positive_integer(record["slot"])
        and _is_reference(record["rc_entry_id"])
        and _is_reference(record["owner_ref"])
        and _is_reference(record["allocation_ref"])
        and isinstance(record["fence_type"], str)
        and record["fence_type"] in _FENCE_TYPES
        and _is_opaque_id(record["created_transaction_id"])
        and _is_positive_integer(created_generation)
        and _is_opaque_id(record["resolved_transaction_id"])
        and _is_positive_integer(resolved_generation)
        and resolved_generation > created_generation
        and record["resolved_transaction_id"] != record["created_transaction_id"]
        and isinstance(record["resolution_kind"], str)
        and record["resolution_kind"] in _RESOLUTION_KINDS
        and _is_aware_timestamp(record["created_at"])
    )


def valid_fence_section(records: object) -> bool:
    """Require a list of typed, uniquely identified pending slot fences."""
    if not isinstance(records, list) or not all(
        validate_pending_fence(record) for record in records
    ):
        return False
    identifiers = [record["fence_id"] for record in records]
    return len(identifiers) == len(set(identifiers))


def valid_tombstone_section(records: object) -> bool:
    """Require typed, uniquely identified retained resolution tombstones."""
    if not isinstance(records, list) or not all(
        validate_resolution_tombstone(record) for record in records
    ):
        return False
    identifiers = [record["tombstone_id"] for record in records]
    fence_ids = [record["fence_id"] for record in records]
    return (
        len(identifiers) == len(set(identifiers))
        and len(fence_ids) == len(set(fence_ids))
        and not set(identifiers) & set(fence_ids)
    )


def references_match_snapshot(
    fences: list[dict[str, Any]],
    tombstones: list[dict[str, Any]],
    *,
    transaction_id: str,
    generation: int,
) -> bool:
    """Reject safety records whose creating/resolving commit is from the future."""
    for fence in fences:
        created_generation = fence["created_generation"]
        if created_generation > generation or (
            created_generation == generation
            and fence["created_transaction_id"] != transaction_id
        ):
            return False
    for tombstone in tombstones:
        created_generation = tombstone["created_generation"]
        resolved_generation = tombstone["resolved_generation"]
        if (
            created_generation > generation
            or resolved_generation > generation
            or (
                resolved_generation == generation
                and tombstone["resolved_transaction_id"] != transaction_id
            )
        ):
            return False
    return True


def validate_snapshot_structure(data: dict[str, Any]) -> bool:
    """Reject duplicate active allocations and active/tombstone ID collisions."""
    target_fences = data["pending_fences"]
    target_tombstones = data["tombstones"]
    active_fence_ids = {record["fence_id"] for record in target_fences}
    all_tombstone_ids = {
        identifier
        for record in target_tombstones
        for identifier in (record["tombstone_id"], record["fence_id"])
    }
    if active_fence_ids & all_tombstone_ids:
        return False

    active_allocations = [
        (
            record["original_km_entry_id"],
            record["slot"],
            record["allocation_ref"],
        )
        for record in target_fences
    ]
    if len(active_allocations) != len(set(active_allocations)):
        return False
    return True


@dataclass(frozen=True, slots=True)
class PendingSlotFence:
    """Identify an original physical slot and protected owner allocation."""

    fence_id: str
    original_km_entry_id: str = field(repr=False)
    slot: int
    rc_entry_id: str = field(repr=False)
    owner_ref: str = field(repr=False)
    allocation_ref: str = field(repr=False)
    created_transaction_id: str
    created_generation: int
    fence_type: str

    def to_record(self) -> dict[str, Any]:
        """Return the validated JSON-compatible persisted form."""
        record = {
            "fence_id": self.fence_id,
            "original_km_entry_id": self.original_km_entry_id,
            "slot": self.slot,
            "rc_entry_id": self.rc_entry_id,
            "owner_ref": self.owner_ref,
            "allocation_ref": self.allocation_ref,
            "created_transaction_id": self.created_transaction_id,
            "created_generation": self.created_generation,
            "fence_type": self.fence_type,
        }
        if not validate_pending_fence(record):
            raise ValueError("invalid pending slot fence")
        return record


@dataclass(frozen=True, slots=True)
class ResolutionTombstone:
    """Retain the original fence references through a resolving transaction."""

    tombstone_id: str
    fence_id: str
    original_km_entry_id: str = field(repr=False)
    slot: int
    rc_entry_id: str = field(repr=False)
    owner_ref: str = field(repr=False)
    allocation_ref: str = field(repr=False)
    fence_type: str
    created_transaction_id: str
    created_generation: int
    resolved_transaction_id: str
    resolved_generation: int
    resolution_kind: str
    created_at: str

    def to_record(self) -> dict[str, Any]:
        """Return the validated JSON-compatible persisted form."""
        record = {
            "tombstone_id": self.tombstone_id,
            "fence_id": self.fence_id,
            "original_km_entry_id": self.original_km_entry_id,
            "slot": self.slot,
            "rc_entry_id": self.rc_entry_id,
            "owner_ref": self.owner_ref,
            "allocation_ref": self.allocation_ref,
            "fence_type": self.fence_type,
            "created_transaction_id": self.created_transaction_id,
            "created_generation": self.created_generation,
            "resolved_transaction_id": self.resolved_transaction_id,
            "resolved_generation": self.resolved_generation,
            "resolution_kind": self.resolution_kind,
            "created_at": self.created_at,
        }
        if not validate_resolution_tombstone(record):
            raise ValueError("invalid resolution tombstone")
        return record


def resolve_fence_to_tombstone(
    fence: dict[str, Any],
    *,
    tombstone_id: str,
    resolved_transaction_id: str,
    resolved_generation: int,
    resolution_kind: str,
    retained_at: str,
) -> dict[str, Any]:
    """Retain the complete original fence identity in one resolution record.

    This constructs bookkeeping only. It does not validate live slot evidence,
    release guards, or authorize a hardware or ownership side effect.
    """
    if not validate_pending_fence(fence):
        raise ValueError("invalid pending slot fence")
    tombstone = ResolutionTombstone(
        tombstone_id=tombstone_id,
        fence_id=fence["fence_id"],
        original_km_entry_id=fence["original_km_entry_id"],
        slot=fence["slot"],
        rc_entry_id=fence["rc_entry_id"],
        owner_ref=fence["owner_ref"],
        allocation_ref=fence["allocation_ref"],
        fence_type=fence["fence_type"],
        created_transaction_id=fence["created_transaction_id"],
        created_generation=fence["created_generation"],
        resolved_transaction_id=resolved_transaction_id,
        resolved_generation=resolved_generation,
        resolution_kind=resolution_kind,
        created_at=retained_at,
    )
    return tombstone.to_record()


@dataclass(frozen=True, slots=True)
class TombstonePressure:
    """PIN-free pressure summary for retained resolution tombstones."""

    count: int
    oldest_age_seconds: int | None


def tombstone_pressure(
    records: list[dict[str, Any]],
    *,
    now: datetime,
) -> TombstonePressure:
    """Report retained count and oldest age without exposing row identifiers."""
    if now.tzinfo is None or now.utcoffset() is None:
        return TombstonePressure(len(records), None)
    if not records:
        return TombstonePressure(0, None)
    timestamps: list[datetime] = []
    for record in records:
        if not validate_resolution_tombstone(record):
            return TombstonePressure(len(records), None)
        timestamps.append(datetime.fromisoformat(record["created_at"]))
    oldest = min(timestamps)
    age = max(timedelta(0), now - oldest)
    return TombstonePressure(len(records), int(age.total_seconds()))
