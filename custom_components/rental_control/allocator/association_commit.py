# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Build durable association targets without dropping prior evidence."""

from __future__ import annotations

from typing import Any

from .association_transactions import AssociationTransaction
from .association_transactions import association_storage_record
from .association_transactions import valid_association_sections
from .association_transactions import valid_association_storage_record
from .associations import AssociationAttestation
from .associations import AssociationMode
from .associations import AssociationRebindHold
from .associations import AssociationTuple


def _read_association_records(
    payloads: object,
) -> tuple[dict[str, AssociationTuple], list[str]] | None:
    """Parse latest per-entry tuples and preserve their record order."""
    if not isinstance(payloads, list):
        return None
    by_entry: dict[str, AssociationTuple] = {}
    order: list[str] = []
    for payload in payloads:
        if not valid_association_storage_record(payload):
            return None
        assert isinstance(payload, dict)
        rc_entry_id = payload["rc_entry_id"]
        association = AssociationTuple.from_record(payload["tuple"])
        assert isinstance(rc_entry_id, str) and association is not None
        if rc_entry_id in by_entry:
            return None
        by_entry[rc_entry_id] = association
        order.append(rc_entry_id)
    return by_entry, order


def _append_attestation_records(
    payloads: object,
    transaction: AssociationTransaction,
) -> list[dict[str, object]] | None:
    """Append scoped attestations without changing prior evidence."""
    if not isinstance(payloads, list):
        return None
    records: list[dict[str, object]] = []
    identifiers: set[str] = set()
    for payload in payloads:
        attestation = AssociationAttestation.from_record(payload)
        if attestation is None or attestation.attestation_id in identifiers:
            return None
        identifiers.add(attestation.attestation_id)
        records.append(attestation.to_record())
    for attestation in transaction.attestations:
        if attestation.attestation_id in identifiers:
            return None
        identifiers.add(attestation.attestation_id)
        records.append(attestation.to_record())
    return records


def _append_pending_transactions(
    payloads: object,
    transaction: AssociationTransaction,
) -> list[dict[str, object]] | None:
    """Append one typed transaction without dropping a prior tuple chain."""
    if not isinstance(payloads, list):
        return None
    records: list[dict[str, object]] = []
    identifiers: set[str] = set()
    for payload in payloads:
        previous = AssociationTransaction.from_record(payload)
        if previous is None or previous.transaction_id in identifiers:
            return None
        identifiers.add(previous.transaction_id)
        records.append(previous.to_record())
    if transaction.transaction_id in identifiers:
        return None
    records.append(transaction.to_record())
    return records


def _append_retained_holds(
    payloads: object,
    transaction: AssociationTransaction,
) -> list[dict[str, object]] | None:
    """Append immutable known-entry holds without dropping prior evidence."""
    if not isinstance(payloads, list) or not all(
        isinstance(payload, dict) for payload in payloads
    ):
        return None
    records = [dict(payload) for payload in payloads]
    existing_ids: set[str] = set()
    existing_references: set[str] = set()
    for payload in records:
        if payload.get("kind") != "association_rebind":
            continue
        hold = AssociationRebindHold.from_record(payload)
        if (
            hold is None
            or hold.hold_id in existing_ids
            or existing_references.intersection(hold.claim_references)
        ):
            return None
        existing_ids.add(hold.hold_id)
        existing_references.update(hold.claim_references)
    for hold in transaction.retained_holds:
        if hold.hold_id in existing_ids or existing_references.intersection(
            hold.claim_references
        ):
            return None
        existing_ids.add(hold.hold_id)
        existing_references.update(hold.claim_references)
        records.append(hold.to_record())
    return records


def add_association_transaction_to_envelope(
    envelope: object,
    transaction: AssociationTransaction,
) -> dict[str, Any] | None:
    """Create a new immutable v2 target without dropping prior config history."""
    if not isinstance(envelope, dict) or not isinstance(envelope.get("data"), dict):
        return None
    data = envelope["data"]
    prior_generation = data.get("generation")
    prior_transaction_id = data.get("transaction_id")
    if (
        not isinstance(prior_generation, int)
        or isinstance(prior_generation, bool)
        or prior_generation + 1 != transaction.generation
        or transaction.predecessor_transaction_id != prior_transaction_id
    ):
        return None
    parsed_associations = _read_association_records(data.get("associations"))
    attestations = _append_attestation_records(data.get("attestations"), transaction)
    pending = _append_pending_transactions(data.get("pending_config"), transaction)
    holds = _append_retained_holds(data.get("holds"), transaction)
    if (
        parsed_associations is None
        or attestations is None
        or pending is None
        or holds is None
    ):
        return None
    associations_by_rc, association_order = parsed_associations
    for change in transaction.changes:
        prior = associations_by_rc.get(change.rc_entry_id)
        if prior is None:
            if change.old.mode is not AssociationMode.LEGACY_UNRESOLVED:
                return None
        elif prior != change.old:
            return None
        associations_by_rc[change.rc_entry_id] = change.new
        if change.rc_entry_id not in association_order:
            association_order.append(change.rc_entry_id)
    associations = [
        association_storage_record(rc_entry_id, associations_by_rc[rc_entry_id])
        for rc_entry_id in association_order
    ]
    if not valid_association_sections(associations, attestations, pending, holds):
        return None

    target_data = dict(data)
    target_data["transaction_id"] = transaction.transaction_id
    target_data["generation"] = transaction.generation
    target_data["associations"] = associations
    target_data["attestations"] = attestations
    target_data["holds"] = holds
    target_data["pending_config"] = pending
    target_envelope = dict(envelope)
    target_envelope["data"] = target_data
    return target_envelope
