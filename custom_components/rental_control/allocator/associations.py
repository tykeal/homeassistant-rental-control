# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Typed, fail-closed association tuples and transaction replay."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from ..const import CONF_ASSOCIATION_GENERATION
from ..const import CONF_ASSOCIATION_MODE
from ..const import CONF_ASSOCIATION_TRANSACTION_ID
from ..const import CONF_LOCK_ENTRY
from ..const import CONF_VERIFIED_KEYMASTER_ENTRY_ID

_TUPLE_FIELDS = frozenset(
    {
        "legacy_lock_selection_present",
        "legacy_lock_selection_raw",
        "verified_keymaster_entry_id",
        "association_generation",
        "association_transaction_id",
        "mode",
    }
)
_OPAQUE_ID_LENGTH = 32


class AssociationMode(StrEnum):
    """Distinguish intentional locklessness from unresolved legacy history."""

    EXPLICIT_LOCKLESS = "explicit_lockless"
    LEGACY_UNRESOLVED = "legacy_unresolved"
    VERIFIED = "verified"
    DISABLED = "disabled"
    MISSING = "missing"
    AMBIGUOUS = "ambiguous"


class AssociationReplayState(StrEnum):
    """Sanitized restart reconciliation outcome for one tuple chain."""

    MATCHES_LATEST = "matches_latest"
    REPLAY_PRIOR = "replay_prior"
    UNKNOWN = "unknown"


def _opaque_id(value: object, *, optional: bool = False) -> bool:
    """Validate a lowercase transaction identifier."""
    if optional and value is None:
        return True
    return (
        isinstance(value, str)
        and len(value) == _OPAQUE_ID_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


@dataclass(frozen=True, slots=True)
class AssociationTuple:
    """Exact persisted association fields, preserving absent versus ``None``."""

    legacy_lock_selection_present: bool
    legacy_lock_selection_raw: str | None
    verified_keymaster_entry_id: str | None
    association_generation: int
    association_transaction_id: str | None
    mode: AssociationMode

    def __post_init__(self) -> None:
        """Reject contradictory or malformed tuple variants."""
        if not isinstance(self.legacy_lock_selection_present, bool):
            raise ValueError("invalid legacy selection presence")
        if self.legacy_lock_selection_raw is not None and not isinstance(
            self.legacy_lock_selection_raw, str
        ):
            raise ValueError("invalid legacy selection")
        if not isinstance(self.association_generation, int) or isinstance(
            self.association_generation, bool
        ):
            raise ValueError("invalid association generation")
        if not isinstance(self.mode, AssociationMode):
            raise ValueError("invalid association mode")
        if not self.legacy_lock_selection_present and (
            self.legacy_lock_selection_raw is not None
        ):
            raise ValueError("absent legacy selection has a value")
        if self.mode is AssociationMode.LEGACY_UNRESOLVED:
            if self.verified_keymaster_entry_id is not None:
                raise ValueError("invalid unresolved legacy tuple")
            if self.association_generation == 0:
                if self.association_transaction_id is not None:
                    raise ValueError("invalid unresolved legacy tuple")
            elif self.association_generation < 1 or not _opaque_id(
                self.association_transaction_id
            ):
                raise ValueError("invalid committed unresolved tuple")
            return
        if self.association_generation < 1 or not _opaque_id(
            self.association_transaction_id
        ):
            raise ValueError("invalid committed association tuple")
        if self.mode is AssociationMode.EXPLICIT_LOCKLESS:
            if self.verified_keymaster_entry_id is not None:
                raise ValueError("lockless tuple has a Keymaster entry")
            return
        if (
            not isinstance(self.verified_keymaster_entry_id, str)
            or not self.verified_keymaster_entry_id
        ):
            raise ValueError("verified association has no Keymaster entry")

    @classmethod
    def from_config(cls, data: Mapping[str, Any]) -> AssociationTuple | None:
        """Read a tuple from current config data without collapsing absence."""
        present = CONF_LOCK_ENTRY in data
        raw = data.get(CONF_LOCK_ENTRY)
        if raw is not None and not isinstance(raw, str):
            return None
        assoc_fields = (
            CONF_VERIFIED_KEYMASTER_ENTRY_ID,
            CONF_ASSOCIATION_GENERATION,
            CONF_ASSOCIATION_TRANSACTION_ID,
            CONF_ASSOCIATION_MODE,
        )
        if not any(field in data for field in assoc_fields):
            return cls(
                present,
                raw,
                None,
                0,
                None,
                AssociationMode.LEGACY_UNRESOLVED,
            )
        if not all(field in data for field in assoc_fields):
            return None
        try:
            mode = AssociationMode(data[CONF_ASSOCIATION_MODE])
            return cls(
                present,
                raw,
                data[CONF_VERIFIED_KEYMASTER_ENTRY_ID],
                data[CONF_ASSOCIATION_GENERATION],
                data[CONF_ASSOCIATION_TRANSACTION_ID],
                mode,
            )
        except TypeError, ValueError:
            return None

    @classmethod
    def from_record(cls, payload: object) -> AssociationTuple | None:
        """Parse one exact JSON tuple record."""
        if not isinstance(payload, dict) or set(payload) != _TUPLE_FIELDS:
            return None
        try:
            return cls(
                payload["legacy_lock_selection_present"],
                payload["legacy_lock_selection_raw"],
                payload["verified_keymaster_entry_id"],
                payload["association_generation"],
                payload["association_transaction_id"],
                AssociationMode(payload["mode"]),
            )
        except TypeError, ValueError:
            return None

    def to_record(self) -> dict[str, object]:
        """Serialize the exact tuple fields for protected storage."""
        return {
            "legacy_lock_selection_present": self.legacy_lock_selection_present,
            "legacy_lock_selection_raw": self.legacy_lock_selection_raw,
            "verified_keymaster_entry_id": self.verified_keymaster_entry_id,
            "association_generation": self.association_generation,
            "association_transaction_id": self.association_transaction_id,
            "mode": self.mode.value,
        }

    def merge_into_config(self, current: Mapping[str, Any]) -> dict[str, Any]:
        """Replace only association fields while retaining unrelated config."""
        merged = dict(current)
        if self.legacy_lock_selection_present:
            merged[CONF_LOCK_ENTRY] = self.legacy_lock_selection_raw
        else:
            merged.pop(CONF_LOCK_ENTRY, None)
        merged[CONF_VERIFIED_KEYMASTER_ENTRY_ID] = self.verified_keymaster_entry_id
        merged[CONF_ASSOCIATION_GENERATION] = self.association_generation
        merged[CONF_ASSOCIATION_TRANSACTION_ID] = self.association_transaction_id
        merged[CONF_ASSOCIATION_MODE] = self.mode.value
        return merged


@dataclass(frozen=True, slots=True)
class AssociationChange:
    """One exact RC-entry tuple transition and its opaque legacy references."""

    rc_entry_id: str
    old: AssociationTuple
    new: AssociationTuple
    affected_references: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Require one-step generation and unique, nonempty reference IDs."""
        if not isinstance(self.rc_entry_id, str) or not self.rc_entry_id:
            raise ValueError("invalid RC entry ID")
        if not isinstance(self.old, AssociationTuple) or not isinstance(
            self.new, AssociationTuple
        ):
            raise ValueError("invalid association tuple")
        if self.new.association_generation != self.old.association_generation + 1:
            raise ValueError("association generation is not consecutive")
        if self.new.association_transaction_id is None:
            raise ValueError("new association has no transaction ID")
        if any(
            not isinstance(reference, str) or not reference
            for reference in self.affected_references
        ) or len(set(self.affected_references)) != len(self.affected_references):
            raise ValueError("invalid affected reference")

    def to_record(self) -> dict[str, object]:
        """Serialize the exact old/new tuple transition."""
        return {
            "rc_entry_id": self.rc_entry_id,
            "old": self.old.to_record(),
            "new": self.new.to_record(),
            "affected_references": list(self.affected_references),
        }

    @classmethod
    def from_record(cls, payload: object) -> AssociationChange | None:
        """Parse one exact old/new transition record."""
        if not isinstance(payload, dict) or set(payload) != {
            "rc_entry_id",
            "old",
            "new",
            "affected_references",
        }:
            return None
        old = AssociationTuple.from_record(payload["old"])
        new = AssociationTuple.from_record(payload["new"])
        references = payload["affected_references"]
        if (
            old is None
            or new is None
            or not isinstance(references, list)
            or not all(isinstance(reference, str) for reference in references)
        ):
            return None
        try:
            return cls(payload["rc_entry_id"], old, new, tuple(references))
        except TypeError, ValueError:
            return None


@dataclass(frozen=True, slots=True)
class AssociationRebindHold:
    """Keep prior verified work attached to its original Keymaster entry."""

    hold_id: str
    rc_entry_id: str
    original_km_entry_id: str
    selected_keymaster_entry_id: str | None
    claim_references: tuple[str, ...]
    transaction_id: str
    generation: int

    def __post_init__(self) -> None:
        """Validate an immutable, transaction-scoped retained-work record."""
        if (
            not _opaque_id(self.hold_id)
            or not _opaque_id(self.transaction_id)
            or not isinstance(self.rc_entry_id, str)
            or not self.rc_entry_id
            or not isinstance(self.original_km_entry_id, str)
            or not self.original_km_entry_id
            or (
                self.selected_keymaster_entry_id is not None
                and (
                    not isinstance(self.selected_keymaster_entry_id, str)
                    or not self.selected_keymaster_entry_id
                )
            )
            or not isinstance(self.generation, int)
            or isinstance(self.generation, bool)
            or self.generation < 1
            or not self.claim_references
            or any(
                not isinstance(reference, str) or not reference
                for reference in self.claim_references
            )
            or len(set(self.claim_references)) != len(self.claim_references)
        ):
            raise ValueError("invalid association rebind hold")

    def to_record(self) -> dict[str, object]:
        """Serialize the exact protected hold fields."""
        return {
            "kind": "association_rebind",
            "hold_id": self.hold_id,
            "rc_entry_id": self.rc_entry_id,
            "original_km_entry_id": self.original_km_entry_id,
            "selected_keymaster_entry_id": self.selected_keymaster_entry_id,
            "claim_references": list(self.claim_references),
            "transaction_id": self.transaction_id,
            "generation": self.generation,
        }

    @classmethod
    def from_record(cls, payload: object) -> AssociationRebindHold | None:
        """Parse one exact association rebind hold."""
        if not isinstance(payload, dict) or set(payload) != {
            "kind",
            "hold_id",
            "rc_entry_id",
            "original_km_entry_id",
            "selected_keymaster_entry_id",
            "claim_references",
            "transaction_id",
            "generation",
        }:
            return None
        references = payload["claim_references"]
        if (
            payload["kind"] != "association_rebind"
            or not isinstance(references, list)
            or not all(isinstance(reference, str) for reference in references)
        ):
            return None
        try:
            return cls(
                payload["hold_id"],
                payload["rc_entry_id"],
                payload["original_km_entry_id"],
                payload["selected_keymaster_entry_id"],
                tuple(references),
                payload["transaction_id"],
                payload["generation"],
            )
        except TypeError, ValueError:
            return None


@dataclass(frozen=True, slots=True)
class AssociationAttestation:
    """Auditable same-original-lock assertion scoped to unknown legacy work."""

    attestation_id: str
    rc_entry_id: str
    selected_keymaster_entry_id: str
    legacy_reference_ids: tuple[str, ...]
    transaction_id: str
    assertion: str = "same_original_physical_lock"

    def __post_init__(self) -> None:
        """Keep attestations explicit, scoped, and free of stored credentials."""
        if (
            not _opaque_id(self.attestation_id)
            or not _opaque_id(self.transaction_id)
            or not isinstance(self.rc_entry_id, str)
            or not self.rc_entry_id
            or not isinstance(self.selected_keymaster_entry_id, str)
            or not self.selected_keymaster_entry_id
            or self.assertion != "same_original_physical_lock"
            or not self.legacy_reference_ids
            or any(
                not isinstance(reference, str) or not reference
                for reference in self.legacy_reference_ids
            )
            or len(set(self.legacy_reference_ids)) != len(self.legacy_reference_ids)
        ):
            raise ValueError("invalid scoped attestation")

    def to_record(self) -> dict[str, object]:
        """Serialize the operator assertion without free-form sensitive text."""
        return {
            "attestation_id": self.attestation_id,
            "rc_entry_id": self.rc_entry_id,
            "selected_keymaster_entry_id": self.selected_keymaster_entry_id,
            "legacy_reference_ids": list(self.legacy_reference_ids),
            "transaction_id": self.transaction_id,
            "assertion": self.assertion,
        }

    @classmethod
    def from_record(cls, payload: object) -> AssociationAttestation | None:
        """Parse one exact scoped attestation record."""
        if not isinstance(payload, dict) or set(payload) != {
            "attestation_id",
            "rc_entry_id",
            "selected_keymaster_entry_id",
            "legacy_reference_ids",
            "transaction_id",
            "assertion",
        }:
            return None
        references = payload["legacy_reference_ids"]
        if not isinstance(references, list) or not all(
            isinstance(reference, str) for reference in references
        ):
            return None
        try:
            return cls(
                payload["attestation_id"],
                payload["rc_entry_id"],
                payload["selected_keymaster_entry_id"],
                tuple(references),
                payload["transaction_id"],
                payload["assertion"],
            )
        except TypeError, ValueError:
            return None
