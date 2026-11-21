# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Validate protected-fence transitions against the actual prior snapshot."""

from __future__ import annotations

from typing import Any

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
    if not validate_snapshot_structure(target_data):
        return False
    target_fences = target_data["pending_fences"]
    target_tombstones = target_data["tombstones"]
    target_transaction_id = target_data["transaction_id"]
    target_generation = target_data["generation"]
    prior_fences_by_id = (
        {record["fence_id"]: record for record in prior_data["pending_fences"]}
        if prior_data is not None
        else {}
    )
    for fence in target_fences:
        if fence["fence_id"] in prior_fences_by_id:
            continue
        if (
            fence["created_transaction_id"] != target_transaction_id
            or fence["created_generation"] != target_generation
        ):
            return False

    if prior_data is None:
        return True
    if not validate_snapshot_structure(prior_data):
        return False

    prior_fences = prior_data["pending_fences"]
    prior_tombstones = prior_data["tombstones"]
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
