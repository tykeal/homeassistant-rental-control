# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Typed retained-work holds and explicit historical attestations."""

from __future__ import annotations

from dataclasses import dataclass

from .associations import _opaque_id


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
