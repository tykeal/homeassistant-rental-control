# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Tests for the pure Store-synchronization coordinator helpers."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone

from custom_components.rental_control.const import STORE_SCHEMA_VERSION
from custom_components.rental_control.coordinator_helpers import store_sync
from custom_components.rental_control.coordinator_helpers.models import StoreSyncPlan
from custom_components.rental_control.reconciliation import DesiredPlan


def test_empty_slot_cache_shape() -> None:
    """An empty cache payload carries schema metadata and a note."""
    cache = store_sync.empty_slot_cache("entry-1", "lock", "reset")
    assert cache["entry_id"] == "entry-1"
    assert cache["lockname"] == "lock"
    assert cache["mappings"] == {}
    assert cache["migration_notes"] == ["reset"]


def test_migrate_to_v1_sets_schema_version() -> None:
    """Migration always yields schema version 1 with preserved mappings."""
    raw = {"mappings": {"k": {"slot": 5}}}
    result = store_sync.migrate_to_v1(
        raw, ("entry-1", "lock", 1, 4, "2026-01-01T00:00:00")
    )
    assert result["schema_version"] == 1
    assert result["mappings"] == {"k": {"slot": 5}}
    assert "legacy_authoritative_fields_ignored" in result["migration_notes"]


def test_normalize_loaded_store_rejects_non_dict() -> None:
    """Corrupt (non-dict) payloads normalize to None."""
    result = store_sync.normalize_loaded_store(
        "broken", ("entry-1", "lock", 1, 4, "2026-01-01T00:00:00")
    )
    assert result is None


def test_build_store_sync_plan_empty() -> None:
    """An empty plan produces an empty mutation plan."""
    plan = DesiredPlan(
        plan_id="p1", generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc)
    )
    result = store_sync.build_store_sync_plan(
        plan,
        {},
        [],
        {},
        {"mappings": {}},
        ("entry-1", "lock", 1, 4, "2026-01-01T00:00:00"),
    )
    assert isinstance(result, StoreSyncPlan)
    assert result.remove_identity_keys == []
    assert result.upsert_mappings == {}


def test_build_save_payload_preserves_blocked_slots() -> None:
    """Blocked slot metadata is included in the save payload."""
    payload = store_sync.build_save_payload(
        {"mappings": {}, "blocked_slots": {"slot-1": {"reason": "owner"}}},
        ("entry-1", "lock", 1, 4, "2026-01-01T00:00:00"),
    )

    assert payload["blocked_slots"] == {"slot-1": {"reason": "owner"}}


def test_loaded_store_save_round_trip_preserves_blocked_slots() -> None:
    """Loaded blocked slot metadata survives a normalize-to-save cycle."""
    raw = {
        "schema_version": STORE_SCHEMA_VERSION,
        "entry_id": "entry-1",
        "lockname": "lock",
        "mappings": {},
        "aliases": {},
        "migration_notes": [],
        "blocked_slots": {"slot-2": {"reason": "maintenance"}},
    }

    normalized = store_sync.normalize_loaded_store(
        raw, ("entry-1", "lock", 1, 4, "2026-01-01T00:00:00")
    )
    assert normalized is not None

    payload = store_sync.build_save_payload(
        normalized, ("entry-1", "lock", 1, 4, "2026-01-01T00:00:00")
    )

    assert payload["blocked_slots"] == {"slot-2": {"reason": "maintenance"}}
