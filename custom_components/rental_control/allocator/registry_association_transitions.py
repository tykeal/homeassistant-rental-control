# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Validate protected association, alias, and retained-hold transitions."""

from __future__ import annotations

from typing import Any

from .association_evidence import AssociationRebindHold
from .association_references import association_references_for_snapshot
from .association_references import ledger_owner_reference
from .association_references import original_keymaster_entry_for_references
from .association_transactions import AssociationTransaction
from .associations import AssociationChange
from .associations import AssociationMode
from .associations import AssociationTuple
from .registry import is_observed_alias


def validate_association_history_evidence(data: dict[str, Any]) -> bool:
    """Revalidate persisted independent proofs against retained safety evidence."""
    records = data.get("pending_config")
    if not isinstance(records, list):
        return False
    for payload in records:
        transaction = AssociationTransaction.from_record(payload)
        if transaction is None:
            return False
        for change in transaction.changes:
            original_entry = change.independent_original_keymaster_entry_id
            if original_entry is None:
                continue
            if (
                original_keymaster_entry_for_references(
                    data,
                    change.rc_entry_id,
                    change.independently_proven_references,
                    require_complete_snapshot=False,
                )
                != original_entry
            ):
                return False
    return True


def validate_association_history(
    prior_data: dict[str, Any], target_data: dict[str, Any]
) -> bool:
    """Require tuple/attestation/hold chains to be append-only and linked."""
    for section in ("attestations", "pending_config", "holds"):
        prior_records = prior_data[section]
        target_records = target_data[section]
        if target_records[: len(prior_records)] != prior_records:
            return False

    prior_associations = {
        item["rc_entry_id"]: AssociationTuple.from_record(item["tuple"])
        for item in prior_data["associations"]
    }
    target_associations = {
        item["rc_entry_id"]: AssociationTuple.from_record(item["tuple"])
        for item in target_data["associations"]
    }
    new_transactions = [
        AssociationTransaction.from_record(item)
        for item in target_data["pending_config"][len(prior_data["pending_config"]) :]
    ]
    if any(transaction is None for transaction in new_transactions):
        return False
    changes = [
        change
        for transaction in new_transactions
        if transaction is not None
        for change in transaction.changes
    ]
    for transaction in new_transactions:
        if transaction is None or not _legacy_reference_evidence_is_valid(
            prior_data, transaction
        ):
            return False
    if not _association_tuples_follow_transactions(
        prior_associations,
        target_associations,
        changes,
    ):
        return False
    return _association_holds_are_valid(
        prior_data,
        target_data,
        prior_associations,
        target_associations,
    )


def same_slot_alias_transfer_is_safe(
    prior_data: dict[str, Any],
    target_data: dict[str, Any],
    prior_record: dict[str, Any],
    prior_owner: dict[str, Any],
) -> bool | None:
    """Validate an alias replacement on the same verified physical slot.

    ``None`` means there is no same-code/same-slot replacement and normal
    owner-release authorization applies. ``False`` means such a replacement
    exists but lacks an exact same-Keymaster association or target fence.
    """
    target_ledger = target_data.get("ledger")
    if not isinstance(target_ledger, dict) or not isinstance(
        target_ledger.get("records"), list
    ):
        return False
    candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for record in target_ledger["records"]:
        if not isinstance(record, dict) or any(
            record.get(field) != prior_record.get(field)
            for field in ("encoded_code", "encoding_salt_value", "code_length")
        ):
            continue
        owners = record.get("owners")
        if not isinstance(owners, list):
            return False
        for owner in owners:
            if (
                isinstance(owner, dict)
                and owner.get("entry_id") != prior_owner["entry_id"]
                and not is_observed_alias(owner.get("identity_key", ""))
                and owner.get("slot") == prior_owner.get("slot")
                and owner.get("lock_observed") is True
            ):
                candidates.append((record, owner))
    if not candidates:
        return None
    if len(candidates) != 1:
        return False
    target_record, target_owner = candidates[0]
    source_keymaster = _verified_keymaster_entry(prior_data, prior_owner["entry_id"])
    target_keymaster = _verified_keymaster_entry(target_data, target_owner["entry_id"])
    target_reference = ledger_owner_reference(target_data, target_record, target_owner)
    if (
        source_keymaster is None
        or target_keymaster != source_keymaster
        or target_reference is None
    ):
        return False
    return any(
        isinstance(fence, dict)
        and fence.get("rc_entry_id") == target_owner["entry_id"]
        and fence.get("owner_ref") == target_reference
        and fence.get("original_km_entry_id") == source_keymaster
        and fence.get("slot") == target_owner["slot"]
        for fence in target_data.get("pending_fences", [])
    )


def _verified_keymaster_entry(data: dict[str, Any], rc_entry_id: str) -> str | None:
    """Return one exact verified Keymaster entry from the tuple snapshot."""
    records = data.get("associations")
    if not isinstance(records, list):
        return None
    matches = [
        AssociationTuple.from_record(item.get("tuple"))
        for item in records
        if isinstance(item, dict) and item.get("rc_entry_id") == rc_entry_id
    ]
    if len(matches) != 1 or matches[0] is None:
        return None
    association = matches[0]
    if (
        association.mode is not AssociationMode.VERIFIED
        or association.verified_keymaster_entry_id is None
    ):
        return None
    return association.verified_keymaster_entry_id


def _association_tuples_follow_transactions(
    prior: dict[str, AssociationTuple | None],
    target: dict[str, AssociationTuple | None],
    changes: list[AssociationChange],
) -> bool:
    """Require every latest tuple change to match one newly appended record."""
    for rc_entry_id in set(prior) | set(target):
        old = prior.get(rc_entry_id)
        new = target.get(rc_entry_id)
        if old == new:
            continue
        if old is None:
            if new is None or not any(
                change.rc_entry_id == rc_entry_id
                and change.old.mode is AssociationMode.LEGACY_UNRESOLVED
                and change.new == new
                for change in changes
            ):
                return False
        elif new is None or not any(
            change.rc_entry_id == rc_entry_id
            and change.old == old
            and change.new == new
            for change in changes
        ):
            return False
    return True


def _association_holds_are_valid(
    prior_data: dict[str, Any],
    target_data: dict[str, Any],
    prior_associations: dict[str, AssociationTuple | None],
    target_associations: dict[str, AssociationTuple | None],
) -> bool:
    """Retain every known-entry hold with its exact original claim references."""
    prior_holds = {
        hold.hold_id: hold
        for item in prior_data["holds"]
        if item.get("kind") == "association_rebind"
        if (hold := AssociationRebindHold.from_record(item)) is not None
    }
    target_holds = {
        hold.hold_id: hold
        for item in target_data["holds"]
        if item.get("kind") == "association_rebind"
        if (hold := AssociationRebindHold.from_record(item)) is not None
    }
    for hold_id, hold in target_holds.items():
        if hold_id in prior_holds:
            if prior_holds[hold_id] != hold:
                return False
            continue
        prior_association = prior_associations.get(hold.rc_entry_id)
        target_association = target_associations.get(hold.rc_entry_id)
        expected_references = association_references_for_snapshot(
            prior_data, hold.rc_entry_id
        )
        if (
            prior_association is None
            or prior_association.mode is not AssociationMode.VERIFIED
            or prior_association.verified_keymaster_entry_id
            != hold.original_km_entry_id
            or target_association is None
            or target_association.verified_keymaster_entry_id
            != hold.selected_keymaster_entry_id
            or expected_references is None
            or set(expected_references) != set(hold.claim_references)
        ):
            return False
    return True


def _legacy_reference_evidence_is_valid(
    prior_data: dict[str, Any],
    transaction: AssociationTransaction,
) -> bool:
    """Require full explicit attestation or exact independent original proof."""
    for change in transaction.changes:
        if (
            change.old.mode is not AssociationMode.LEGACY_UNRESOLVED
            or change.new.mode is not AssociationMode.VERIFIED
            or not change.affected_references
        ):
            continue
        expected_references = association_references_for_snapshot(
            prior_data, change.rc_entry_id
        )
        if expected_references is None or set(expected_references) != set(
            change.affected_references
        ):
            return False
        attested = {
            reference
            for item in transaction.attestations
            if item.rc_entry_id == change.rc_entry_id
            for reference in item.legacy_reference_ids
        }
        affected = set(change.affected_references)
        if attested == affected:
            if (
                change.independent_original_keymaster_entry_id is not None
                or change.independently_proven_references
            ):
                return False
            continue
        if (
            attested
            or change.independent_original_keymaster_entry_id
            != change.new.verified_keymaster_entry_id
            or set(change.independently_proven_references) != affected
        ):
            return False
        original_entry = original_keymaster_entry_for_references(
            prior_data,
            change.rc_entry_id,
            change.independently_proven_references,
        )
        if original_entry != change.independent_original_keymaster_entry_id:
            return False
    return True
