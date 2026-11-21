# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Validate protected-fence transitions against the actual prior snapshot."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .association_references import ledger_owner_reference
from .registry import is_observed_alias
from .registry_association_transitions import same_slot_alias_transfer_is_safe
from .registry_association_transitions import validate_association_history
from .registry_safety_models import validate_snapshot_structure


def _fence_matches_tombstone(fence: dict[str, Any], tombstone: dict[str, Any]) -> bool:
    """Compare every immutable original-fence field with its tombstone."""
    return all(
        fence[fence_field] == tombstone[tombstone_field]
        for fence_field, tombstone_field in (
            ("fence_id", "fence_id"),
            ("original_km_entry_id", "original_km_entry_id"),
            ("slot", "slot"),
            ("rc_entry_id", "rc_entry_id"),
            ("owner_ref", "owner_ref"),
            ("allocation_ref", "allocation_ref"),
            ("created_transaction_id", "created_transaction_id"),
            ("created_generation", "created_generation"),
            ("fence_type", "fence_type"),
        )
    )


def validate_snapshot_transition(
    prior_data: dict[str, Any] | None,
    target_data: dict[str, Any],
) -> bool:
    """Require the target to preserve or exactly resolve prior safety records.

    Both snapshots must already pass their complete v2 shape validation. This
    cross-record check is called after reading the actual predecessor while
    both allocator and process locks are held.
    """
    if not validate_snapshot_structure(target_data) or (
        prior_data is not None and not validate_snapshot_structure(prior_data)
    ):
        return False
    if not _new_fences_are_current(prior_data, target_data):
        return False
    if prior_data is None:
        return True
    if "ledger" not in prior_data or "ledger" not in target_data:
        return _validate_fence_transition(prior_data, target_data)
    if not _validate_ledger_transition(prior_data, target_data):
        return False
    if not validate_association_history(prior_data, target_data):
        return False
    return _validate_fence_transition(prior_data, target_data)


def _new_fences_are_current(
    prior_data: dict[str, Any] | None, target_data: dict[str, Any]
) -> bool:
    """Require each newly introduced fence to name this exact commit."""
    prior_fence_ids = (
        {record["fence_id"] for record in prior_data["pending_fences"]}
        if prior_data is not None
        else set()
    )
    return all(
        fence["fence_id"] in prior_fence_ids
        or (
            fence["created_transaction_id"] == target_data["transaction_id"]
            and fence["created_generation"] == target_data["generation"]
        )
        for fence in target_data["pending_fences"]
    )


def _validate_fence_transition(
    prior_data: dict[str, Any], target_data: dict[str, Any]
) -> bool:
    """Preserve old fences or resolve them to exact immutable tombstones."""
    prior_fences = prior_data["pending_fences"]
    prior_tombstones = prior_data["tombstones"]
    target_fences = target_data["pending_fences"]
    target_tombstones = target_data["tombstones"]
    target_transaction_id = target_data["transaction_id"]
    target_generation = target_data["generation"]
    prior_fences_by_id = {record["fence_id"]: record for record in prior_fences}
    target_fences_by_id = {record["fence_id"]: record for record in target_fences}
    target_tombstones_by_id = {
        record["tombstone_id"]: record for record in target_tombstones
    }
    target_tombstones_by_fence = {
        record["fence_id"]: record for record in target_tombstones
    }
    prior_tombstone_ids = {record["tombstone_id"] for record in prior_tombstones}

    for prior_tombstone in prior_tombstones:
        if target_tombstones_by_id.get(prior_tombstone["tombstone_id"]) != (
            prior_tombstone
        ):
            return False

    for tombstone in target_tombstones:
        if tombstone["tombstone_id"] in prior_tombstone_ids:
            continue
        original_fence = prior_fences_by_id.get(tombstone["fence_id"])
        if (
            original_fence is None
            or tombstone["fence_id"] in target_fences_by_id
            or not _fence_matches_tombstone(original_fence, tombstone)
            or tombstone["resolved_transaction_id"] != target_transaction_id
            or tombstone["resolved_generation"] != target_generation
        ):
            return False

    for prior_fence in prior_fences:
        fence_id = prior_fence["fence_id"]
        target_fence = target_fences_by_id.get(fence_id)
        if target_fence is not None:
            if target_fence != prior_fence or fence_id in target_tombstones_by_fence:
                return False
            continue
        tombstone = target_tombstones_by_fence.get(fence_id)
        if (
            tombstone is None
            or not _fence_matches_tombstone(prior_fence, tombstone)
            or tombstone["resolved_transaction_id"] != target_transaction_id
            or tombstone["resolved_generation"] != target_generation
        ):
            return False
    return True


def _serialized_owners(
    data: dict[str, Any],
) -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
    """Index serialized owners by their salted allocation/location reference."""
    ledger = data.get("ledger")
    if not isinstance(ledger, dict) or not isinstance(ledger.get("records"), list):
        return {}
    owners: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for record in ledger["records"]:
        if not isinstance(record, dict) or not isinstance(record.get("owners"), list):
            return {}
        for owner in record["owners"]:
            if not isinstance(owner, dict):
                return {}
            reference = ledger_owner_reference(data, record, owner)
            if reference is None or reference in owners:
                return {}
            owners[reference] = (record, owner)
    return owners


def _owners_by_identity(data: dict[str, Any]) -> dict[tuple[str, str], str]:
    """Map each exact config-entry owner identity to its opaque reference."""
    indexed: dict[tuple[str, str], str] = {}
    for reference, (_record, owner) in _serialized_owners(data).items():
        key = (owner["entry_id"], owner["identity_key"])
        if key in indexed:
            return {}
        indexed[key] = reference
    return indexed


def _owner_release_is_proven(
    prior_data: dict[str, Any],
    target_data: dict[str, Any],
    owner_reference: str,
    owner: dict[str, Any],
) -> bool:
    """Require an exact current tombstone for a prior owner's allocation fence."""
    prior_fences = {
        fence["fence_id"]: fence
        for fence in prior_data["pending_fences"]
        if fence["fence_type"] == "allocation"
        and fence["owner_ref"] == owner_reference
        and fence["rc_entry_id"] == owner["entry_id"]
        and fence["slot"] == owner["slot"]
    }
    target_tombstones = {
        tombstone["fence_id"]: tombstone
        for tombstone in target_data["tombstones"]
        if tombstone["resolution_kind"] == "owner_released"
    }
    transaction_id = target_data["transaction_id"]
    generation = target_data["generation"]
    return any(
        fence_id in target_tombstones
        and target_tombstones[fence_id]["resolved_transaction_id"] == transaction_id
        and target_tombstones[fence_id]["resolved_generation"] == generation
        for fence_id in prior_fences
    )


def snapshot_requires_owner_authorization(
    prior_data: dict[str, Any] | None,
    target_data: dict[str, Any],
) -> bool:
    """Detect any prior owner reference absent from the requested snapshot."""
    if prior_data is None:
        return False
    prior_owners = _serialized_owners(prior_data)
    target_owners = _serialized_owners(target_data)
    prior_count = sum(
        len(record["owners"]) for record in prior_data["ledger"]["records"]
    )
    target_count = sum(
        len(record["owners"]) for record in target_data["ledger"]["records"]
    )
    if len(prior_owners) != prior_count or len(target_owners) != target_count:
        return True
    return not prior_owners.keys() <= target_owners.keys()


def authorize_snapshot_owner_transition(
    prior_data: dict[str, Any] | None,
    target_data: dict[str, Any],
    authorizer: Callable[[], bool] | None,
) -> bool:
    """Run a supplied release guard only when prior owners would change."""
    try:
        if not snapshot_requires_owner_authorization(prior_data, target_data):
            return True
        return callable(authorizer) and authorizer() is True
    except Exception:
        return False


def _validate_ledger_transition(
    prior_data: dict[str, Any], target_data: dict[str, Any]
) -> bool:
    """Guard owner loss with exact release proof, while allowing live updates.

    Ordinary observation fields, exclusions, additions, and guarded location
    changes may evolve. A prior owner may not disappear or move unless its
    allocation fence is resolved by an exact ``owner_released`` tombstone in
    this target transaction. Association rebind history separately retains
    known-original-entry claims in immutable holds.
    """
    prior_ledger = prior_data["ledger"]
    target_ledger = target_data["ledger"]
    if prior_ledger["code_ref_salt"] != target_ledger["code_ref_salt"]:
        return False
    prior_owners = _serialized_owners(prior_data)
    target_owners = _serialized_owners(target_data)
    target_by_identity = _owners_by_identity(target_data)
    prior_owner_count = sum(len(record["owners"]) for record in prior_ledger["records"])
    target_owner_count = sum(
        len(record["owners"]) for record in target_ledger["records"]
    )
    if (
        len(prior_owners) != prior_owner_count
        or len(target_owners) != target_owner_count
    ):
        return False

    # Existing owner references retain their immutable provenance. Runtime
    # observation timestamps and observed-state flags may be refreshed.
    for reference, (prior_record, prior_owner) in prior_owners.items():
        target = target_owners.get(reference)
        if target is not None:
            target_record, target_owner = target
            if (
                target_record["encoding_salt_source"]
                != prior_record["encoding_salt_source"]
                or target_record["code_length"] != prior_record["code_length"]
                or target_record["created_at"] != prior_record["created_at"]
                or any(
                    target_owner[key] != prior_owner[key]
                    for key in ("entry_id", "identity_key", "origin", "first_seen")
                )
            ):
                return False
            continue
        if not _owner_release_is_proven(
            prior_data,
            target_data,
            reference,
            prior_owner,
        ):
            return False
        if is_observed_alias(prior_owner["identity_key"]):
            alias_transfer = same_slot_alias_transfer_is_safe(
                prior_data,
                target_data,
                prior_record,
                prior_owner,
            )
            if alias_transfer is False:
                return False
        replacement_reference = target_by_identity.get(
            (prior_owner["entry_id"], prior_owner["identity_key"])
        )
        if replacement_reference is not None:
            target_record, target_owner = target_owners[replacement_reference]
            if any(
                target_owner[key] != prior_owner[key]
                for key in ("entry_id", "identity_key", "origin", "first_seen")
            ):
                return False
            if not any(
                fence["fence_type"] == "allocation"
                and fence["owner_ref"] == replacement_reference
                and fence["created_transaction_id"] == target_data["transaction_id"]
                and fence["created_generation"] == target_data["generation"]
                for fence in target_data["pending_fences"]
            ):
                return False
            if (
                target_record["encoding_salt_source"]
                != prior_record["encoding_salt_source"]
            ):
                return False
    return True
