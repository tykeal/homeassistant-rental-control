# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Diagnostic projections for the shared door-code allocator."""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from ..const import DOMAIN

if TYPE_CHECKING:
    from .allocator import DoorCodeAllocator


def allocator_diagnostics(allocator: DoorCodeAllocator) -> dict[str, Any]:
    """Return allocator diagnostics without exposing door codes."""
    conflicts = allocator._registry.conflicts()
    known_entry_ids = {
        entry.entry_id for entry in allocator.hass.config_entries.async_entries(DOMAIN)
    }
    return {
        "record_count": len(allocator._registry.records),
        "owner_count": sum(
            len(record.owners) for record in allocator._registry.records.values()
        ),
        "conflict_count": len(conflicts),
        "conflicts": [
            {
                "code_ref": record.code_ref,
                "owners": [_owner_diagnostics(owner) for owner in record.owners],
            }
            for record in conflicts
        ],
        "orphans": sorted(
            owner.entry_id
            for record in allocator._registry.records.values()
            for owner in record.owners
            if owner.entry_id not in known_entry_ids
        ),
        "records": [
            {
                "code_ref": record.code_ref,
                "code_length": record.code_length,
                "owner_count": len(record.owners),
                "owners": [_owner_diagnostics(owner) for owner in record.owners],
            }
            for record in allocator._registry.records.values()
        ],
        "pending_adoption": sorted(allocator._pending_adoption),
        "gate_deadline": allocator._gate_deadline,
        "registry_lost": allocator._registry_lost,
        "registry_missing": allocator._registry_missing,
    }


def _owner_diagnostics(owner: Any) -> dict[str, Any]:
    """Return diagnostics for one owner without door code material."""
    return {
        "entry_id": owner.entry_id,
        "identity_key": owner.identity_key,
        "origin": owner.origin.value,
        "lockname": owner.lockname,
        "slot": owner.slot,
        "lock_observed": owner.lock_observed,
    }
