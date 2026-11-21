# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Protected association transaction records and exact replay helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .associations import AssociationAttestation
from .associations import AssociationChange
from .associations import AssociationMode
from .associations import AssociationRebindHold
from .associations import AssociationReplayState
from .associations import AssociationTuple
from .associations import _opaque_id


@dataclass(frozen=True, slots=True)
class AssociationTransaction:
    """Atomic set of tuple changes sharing one protected transaction."""

    transaction_id: str
    generation: int
    predecessor_transaction_id: str | None
    changes: tuple[AssociationChange, ...]
    attestations: tuple[AssociationAttestation, ...] = ()
    retained_holds: tuple[AssociationRebindHold, ...] = ()

    def __post_init__(self) -> None:
        """Validate transaction linkage and its scoped attestations."""
        self._validate_identity()
        self._validate_attestations()
        self._validate_holds()
        self._validate_legacy_references()

    def _validate_identity(self) -> None:
        """Validate transaction-level identifiers and changed-entry uniqueness."""
        if (
            not _opaque_id(self.transaction_id)
            or not _opaque_id(self.predecessor_transaction_id, optional=True)
            or not isinstance(self.generation, int)
            or isinstance(self.generation, bool)
            or self.generation < 1
            or not self.changes
            or not all(isinstance(change, AssociationChange) for change in self.changes)
            or not all(
                isinstance(item, AssociationAttestation) for item in self.attestations
            )
            or not all(
                isinstance(item, AssociationRebindHold) for item in self.retained_holds
            )
            or len({change.rc_entry_id for change in self.changes}) != len(self.changes)
        ):
            raise ValueError("invalid association transaction")
        if any(
            change.new.association_transaction_id != self.transaction_id
            for change in self.changes
        ):
            raise ValueError("association tuple transaction mismatch")

    def _validate_attestations(self) -> None:
        """Require attestations to authorize only their exact legacy changes."""
        changed_rc_entries = {change.rc_entry_id for change in self.changes}
        for attestation in self.attestations:
            if (
                attestation.transaction_id != self.transaction_id
                or attestation.rc_entry_id not in changed_rc_entries
            ):
                raise ValueError("attestation transaction scope mismatch")
            change = next(
                change
                for change in self.changes
                if change.rc_entry_id == attestation.rc_entry_id
            )
            if (
                change.old.mode is not AssociationMode.LEGACY_UNRESOLVED
                or change.new.mode is not AssociationMode.VERIFIED
                or change.new.verified_keymaster_entry_id
                != attestation.selected_keymaster_entry_id
                or not set(attestation.legacy_reference_ids).issubset(
                    change.affected_references
                )
            ):
                raise ValueError("attestation does not cover legacy transition")
        if not _attestations_are_consistent(self.attestations):
            raise ValueError("contradictory shared-owner attestations")

    def _validate_holds(self) -> None:
        """Validate immutable original-entry holds and their transaction scope."""
        hold_ids: set[str] = set()
        held_references: set[str] = set()
        for hold in self.retained_holds:
            if (
                hold.hold_id in hold_ids
                or hold.transaction_id != self.transaction_id
                or hold.generation != self.generation
            ):
                raise ValueError("association rebind hold transaction mismatch")
            hold_ids.add(hold.hold_id)
            if held_references.intersection(hold.claim_references):
                raise ValueError("duplicate retained association reference")
            held_references.update(hold.claim_references)
            hold_change = next(
                (item for item in self.changes if item.rc_entry_id == hold.rc_entry_id),
                None,
            )
            if (
                hold_change is None
                or hold_change.old.mode is not AssociationMode.VERIFIED
                or hold_change.old.verified_keymaster_entry_id
                != hold.original_km_entry_id
                or hold_change.new.verified_keymaster_entry_id
                != hold.selected_keymaster_entry_id
                or hold_change.new.verified_keymaster_entry_id
                == hold_change.old.verified_keymaster_entry_id
                or not set(hold.claim_references).issubset(
                    hold_change.affected_references
                )
            ):
                raise ValueError("association rebind hold scope mismatch")
        self._validate_hold_coverage()

    def _validate_hold_coverage(self) -> None:
        """Require affected verified work to keep an exact original-entry hold."""
        for change in self.changes:
            is_rebind = (
                change.old.mode is AssociationMode.VERIFIED
                and change.new.verified_keymaster_entry_id
                != change.old.verified_keymaster_entry_id
            )
            change_holds = [
                hold
                for hold in self.retained_holds
                if hold.rc_entry_id == change.rc_entry_id
            ]
            if is_rebind and change.affected_references:
                if len(change_holds) != 1 or set(
                    change_holds[0].claim_references
                ) != set(change.affected_references):
                    raise ValueError("verified work requires an original-entry hold")
            elif change_holds:
                raise ValueError("unexpected association rebind hold")

    def _validate_legacy_references(self) -> None:
        """Require legacy-owned references to be explicitly attested."""
        for change in self.changes:
            if (
                change.old.mode is AssociationMode.LEGACY_UNRESOLVED
                and change.new.mode is AssociationMode.VERIFIED
                and change.affected_references
            ):
                covered_references = {
                    reference
                    for attestation in self.attestations
                    if attestation.rc_entry_id == change.rc_entry_id
                    for reference in attestation.legacy_reference_ids
                }
                if not set(change.affected_references).issubset(covered_references):
                    raise ValueError("legacy references lack explicit attestation")

    def to_record(self) -> dict[str, object]:
        """Serialize a pending-config chain record."""
        return {
            "kind": "association",
            "transaction_id": self.transaction_id,
            "generation": self.generation,
            "predecessor_transaction_id": self.predecessor_transaction_id,
            "changes": [change.to_record() for change in self.changes],
            "attestations": [item.to_record() for item in self.attestations],
            "retained_holds": [item.to_record() for item in self.retained_holds],
        }

    @classmethod
    def from_record(cls, payload: object) -> AssociationTransaction | None:
        """Parse and cross-validate one pending association transaction."""
        legacy_keys = {
            "kind",
            "transaction_id",
            "generation",
            "predecessor_transaction_id",
            "changes",
            "attestations",
        }
        if not isinstance(payload, dict) or frozenset(payload) not in {
            frozenset(legacy_keys),
            frozenset(legacy_keys | {"retained_holds"}),
        }:
            return None
        if payload["kind"] != "association":
            return None
        raw_changes = payload["changes"]
        raw_attestations = payload["attestations"]
        raw_holds = payload.get("retained_holds", [])
        if (
            not isinstance(raw_changes, list)
            or not isinstance(raw_attestations, list)
            or not isinstance(raw_holds, list)
        ):
            return None
        changes: list[AssociationChange] = []
        for item in raw_changes:
            change = AssociationChange.from_record(item)
            if change is None:
                return None
            changes.append(change)
        attestations: list[AssociationAttestation] = []
        for item in raw_attestations:
            attestation = AssociationAttestation.from_record(item)
            if attestation is None:
                return None
            attestations.append(attestation)
        retained_holds: list[AssociationRebindHold] = []
        for item in raw_holds:
            hold = AssociationRebindHold.from_record(item)
            if hold is None:
                return None
            retained_holds.append(hold)
        try:
            return cls(
                payload["transaction_id"],
                payload["generation"],
                payload["predecessor_transaction_id"],
                tuple(changes),
                tuple(attestations),
                tuple(retained_holds),
            )
        except TypeError, ValueError:
            return None


def association_storage_record(
    rc_entry_id: str, association: AssociationTuple
) -> dict[str, object]:
    """Build the unique latest-association envelope record."""
    return {"rc_entry_id": rc_entry_id, "tuple": association.to_record()}


def valid_association_storage_record(payload: object) -> bool:
    """Validate the exact per-entry association storage object."""
    if not isinstance(payload, dict) or set(payload) != {"rc_entry_id", "tuple"}:
        return False
    return bool(
        isinstance(payload["rc_entry_id"], str)
        and payload["rc_entry_id"]
        and AssociationTuple.from_record(payload["tuple"]) is not None
    )


def _attestations_are_consistent(
    attestations: tuple[AssociationAttestation, ...] | list[AssociationAttestation],
) -> bool:
    """Prevent one legacy owner reference from being attested to two locks."""
    target_by_reference: dict[str, str] = {}
    for attestation in attestations:
        for reference in attestation.legacy_reference_ids:
            target = target_by_reference.setdefault(
                reference, attestation.selected_keymaster_entry_id
            )
            if target != attestation.selected_keymaster_entry_id:
                return False
    return True


def valid_association_sections(
    associations: object,
    attestations: object,
    pending_config: object,
    holds: object | None = None,
) -> bool:
    """Validate cross-record identity, attestation, and replay references."""
    if (
        not isinstance(associations, list)
        or not isinstance(attestations, list)
        or not isinstance(pending_config, list)
        or (holds is not None and not isinstance(holds, list))
    ):
        return False
    hold_records = [] if holds is None else holds
    assert isinstance(hold_records, list)
    persisted_holds = _parse_persisted_holds(hold_records)
    association_by_rc = _parse_associations(associations)
    parsed_attestations = _parse_attestations(attestations)
    if (
        persisted_holds is None
        or association_by_rc is None
        or parsed_attestations is None
    ):
        return False
    return _validate_pending_associations(
        pending_config, persisted_holds, association_by_rc
    )


def _parse_persisted_holds(
    hold_records: list[Any],
) -> dict[str, AssociationRebindHold] | None:
    """Validate unique persisted association rebind holds."""
    persisted_holds: dict[str, AssociationRebindHold] = {}
    held_references: set[str] = set()
    for item in hold_records:
        if not isinstance(item, dict):
            return None
        if item.get("kind") != "association_rebind":
            continue
        hold = AssociationRebindHold.from_record(item)
        if (
            hold is None
            or hold.hold_id in persisted_holds
            or held_references.intersection(hold.claim_references)
        ):
            return None
        persisted_holds[hold.hold_id] = hold
        held_references.update(hold.claim_references)
    return persisted_holds


def _parse_associations(
    associations: list[Any],
) -> dict[str, AssociationTuple] | None:
    """Validate unique latest tuples indexed by their RC entry."""
    association_by_rc: dict[str, AssociationTuple] = {}
    for item in associations:
        if not valid_association_storage_record(item):
            return None
        assert isinstance(item, dict)
        rc_entry_id = item["rc_entry_id"]
        association = AssociationTuple.from_record(item["tuple"])
        assert isinstance(rc_entry_id, str)
        assert association is not None
        if rc_entry_id in association_by_rc:
            return None
        association_by_rc[rc_entry_id] = association
    return association_by_rc


def _parse_attestations(
    attestations: list[Any],
) -> list[AssociationAttestation] | None:
    """Validate globally unique and noncontradictory scoped attestations."""
    attestation_ids: set[str] = set()
    parsed_attestations: list[AssociationAttestation] = []
    for item in attestations:
        attestation = AssociationAttestation.from_record(item)
        if attestation is None or attestation.attestation_id in attestation_ids:
            return None
        attestation_ids.add(attestation.attestation_id)
        parsed_attestations.append(attestation)
    if not _attestations_are_consistent(parsed_attestations):
        return None
    return parsed_attestations


def _validate_pending_associations(
    pending_config: list[Any],
    persisted_holds: dict[str, AssociationRebindHold],
    association_by_rc: dict[str, AssociationTuple],
) -> bool:
    """Cross-check transaction history against latest tuples and retained holds."""
    transaction_ids: set[str] = set()
    transaction_holds: dict[str, AssociationRebindHold] = {}
    latest_pending: dict[str, AssociationTuple] = {}
    for item in pending_config:
        transaction = AssociationTransaction.from_record(item)
        if (
            transaction is None
            or transaction.transaction_id in transaction_ids
            or transaction.generation < 1
        ):
            return False
        transaction_ids.add(transaction.transaction_id)
        for hold in transaction.retained_holds:
            if hold.hold_id in transaction_holds:
                return False
            transaction_holds[hold.hold_id] = hold
        for change in transaction.changes:
            latest_pending[change.rc_entry_id] = change.new
    if transaction_holds != persisted_holds:
        return False
    return all(
        association_by_rc.get(rc_entry_id) == association
        for rc_entry_id, association in latest_pending.items()
    )


def replay_association_chain(
    current: AssociationTuple | None,
    chain: tuple[AssociationChange, ...],
) -> tuple[AssociationReplayState, AssociationTuple | None]:
    """Match exact prior tuples for replay; reject missing/ahead/unknown state."""
    if current is None or not chain:
        return AssociationReplayState.UNKNOWN, None
    if any(
        chain[index].rc_entry_id != chain[0].rc_entry_id
        or chain[index].old != chain[index - 1].new
        for index in range(1, len(chain))
    ):
        return AssociationReplayState.UNKNOWN, None
    if current == chain[-1].new:
        return AssociationReplayState.MATCHES_LATEST, chain[-1].new
    if current in {change.old for change in chain} | {
        change.new for change in chain[:-1]
    }:
        return AssociationReplayState.REPLAY_PRIOR, chain[-1].new
    return AssociationReplayState.UNKNOWN, None
