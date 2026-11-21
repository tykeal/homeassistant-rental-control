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
    independent_original_keymaster_entry_id: str | None = None
    independently_proven_references: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Validate tuple transition and independent original-entry evidence."""
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
        if self.independent_original_keymaster_entry_id is None:
            if self.independently_proven_references:
                raise ValueError("independent references lack an original entry")
        elif (
            not isinstance(self.independent_original_keymaster_entry_id, str)
            or not self.independent_original_keymaster_entry_id
            or not self.independently_proven_references
            or any(
                not isinstance(reference, str) or not reference
                for reference in self.independently_proven_references
            )
            or len(set(self.independently_proven_references))
            != len(self.independently_proven_references)
            or not set(self.independently_proven_references).issubset(
                self.affected_references
            )
            or self.old.mode is not AssociationMode.LEGACY_UNRESOLVED
            or self.new.mode is not AssociationMode.VERIFIED
            or self.new.verified_keymaster_entry_id
            != self.independent_original_keymaster_entry_id
        ):
            raise ValueError("invalid independent original-entry proof")

    def to_record(self) -> dict[str, object]:
        """Serialize the exact old/new tuple transition."""
        return {
            "rc_entry_id": self.rc_entry_id,
            "old": self.old.to_record(),
            "new": self.new.to_record(),
            "affected_references": list(self.affected_references),
            "independent_original_keymaster_entry_id": (
                self.independent_original_keymaster_entry_id
            ),
            "independently_proven_references": list(
                self.independently_proven_references
            ),
        }

    @classmethod
    def from_record(cls, payload: object) -> AssociationChange | None:
        """Parse one exact old/new transition record."""
        legacy_fields = {
            "rc_entry_id",
            "old",
            "new",
            "affected_references",
        }
        proof_fields = {
            "independent_original_keymaster_entry_id",
            "independently_proven_references",
        }
        if not isinstance(payload, dict) or frozenset(payload) not in {
            frozenset(legacy_fields),
            frozenset(legacy_fields | proof_fields),
        }:
            return None
        old = AssociationTuple.from_record(payload["old"])
        new = AssociationTuple.from_record(payload["new"])
        references = payload["affected_references"]
        proof_references = payload.get("independently_proven_references", [])
        if (
            old is None
            or new is None
            or not isinstance(references, list)
            or not all(isinstance(reference, str) for reference in references)
            or not isinstance(proof_references, list)
            or not all(isinstance(reference, str) for reference in proof_references)
        ):
            return None
        try:
            return cls(
                payload["rc_entry_id"],
                old,
                new,
                tuple(references),
                payload.get("independent_original_keymaster_entry_id"),
                tuple(proof_references),
            )
        except TypeError, ValueError:
            return None
