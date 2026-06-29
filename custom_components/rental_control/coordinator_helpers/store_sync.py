# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Pure Store synchronization planning.

The coordinator shell owns the live ``_slot_mappings`` dict and all Store
saves. This module computes a :class:`~.models.StoreSyncPlan` describing
which mappings to remove, which cache-only mappings to upsert, and the
metadata to refresh, given a desired plan and operation results.
"""

from __future__ import annotations

from typing import Any
from typing import cast

from ..const import SLOT_STATUS_OCCUPIED
from ..const import STORE_SCHEMA_VERSION
from ..reconciliation import DesiredPlan
from ..reconciliation import ManagedSlot
from ..reconciliation import Reservation
from ..util import OperationResult
from .models import StoreSyncPlan
from .models import _store_datetime


def observed_actual_snapshot(ms: ManagedSlot) -> dict[str, Any]:
    """Return the ``last_observed_actual`` snapshot for a managed slot."""
    return {
        "slot": ms.slot,
        "classification": ms.status.value,
        "name_state": ms.actual_name,
        "has_code": ms.actual_code_present,
        "start_state": _store_datetime(ms.actual_start),
        "end_state": _store_datetime(ms.actual_end),
        "use_date_range": ms.date_range_enabled,
        "enabled": ms.enabled,
    }


def build_adopted_mapping(
    ms: ManagedSlot, identity_key: str, slot_name: str, now_str: str
) -> dict[str, Any]:
    """Build the persisted mapping for an adopted readable coded slot."""
    return {
        "slot": ms.slot,
        "status": SLOT_STATUS_OCCUPIED,
        "operation_id": None,
        "operation_kind": None,
        "identity": {
            "identity_key": identity_key,
            "summary": slot_name,
            "slot_name": slot_name,
            "start": _store_datetime(ms.actual_start),
            "end": _store_datetime(ms.actual_end),
            "uid_aliases": [],
            "booking_aliases": [],
        },
        "missing_count": 0,
        "pending_set_since": None,
        "pending_clear_since": None,
        "fingerprint_history": [],
        "updated_at": now_str,
        "last_observed_actual": {
            "slot": ms.slot,
            "classification": ms.status.value,
            "name_state": ms.actual_name,
            "has_code": True,
            "start_state": _store_datetime(ms.actual_start),
            "end_state": _store_datetime(ms.actual_end),
            "use_date_range": ms.date_range_enabled,
            "enabled": ms.enabled,
        },
    }


def build_cache_mapping(
    slot: int,
    res: Reservation,
    actual: dict[str, Any],
    now_str: str,
) -> dict[str, Any]:
    """Build the cache-only Store mapping payload for a selected reservation."""
    return {
        "slot": slot,
        "status": "cache",
        "operation_id": None,
        "operation_kind": None,
        "identity": {
            "identity_key": res.identity_key,
            "summary": res.summary,
            "slot_name": res.slot_name,
            "start": res.start.isoformat(),
            "end": res.end.isoformat(),
            "uid_aliases": sorted(res.uid_aliases),
            "booking_aliases": sorted(res.booking_aliases),
        },
        "missing_count": 0,
        "pending_set_since": None,
        "pending_clear_since": None,
        "fingerprint_history": sorted(res.fingerprint_history),
        "updated_at": now_str,
        "last_observed_actual": {
            "slot": slot,
            "classification": actual.get("classification", "occupied"),
            "name_state": actual.get("name_state") or res.display_slot_name,
            "has_code": actual.get("has_code", bool(res.slot_code)),
            "start_state": (
                _store_datetime(actual.get("start_state"))
                or res.buffered_start.isoformat()
            ),
            "end_state": _store_datetime(actual.get("end_state"))
            or res.buffered_end.isoformat(),
            "use_date_range": actual.get("use_date_range"),
            "enabled": actual.get("enabled"),
        },
    }


def _store_metadata(
    plan: DesiredPlan,
    current_store: dict[str, Any],
    meta: tuple[str, str | None, int, int, str],
) -> dict[str, Any]:
    """Build the Store metadata refresh dict (excluding ``mappings``)."""
    entry_id, lockname, start_slot, max_slots, now_str = meta
    return {
        "schema_version": STORE_SCHEMA_VERSION,
        "entry_id": entry_id,
        "lockname": lockname,
        "start_slot": start_slot,
        "max_slots": max_slots,
        "updated_at": now_str,
        "aliases": current_store.get("aliases", {}),
        "last_plan": plan.diagnostics,
        "migration_notes": current_store.get("migration_notes", []),
    }


def empty_slot_cache(entry_id: str, lockname: str | None, note: str) -> dict[str, Any]:
    """Return an empty cache-only Store payload with a migration note."""
    return {
        "schema_version": STORE_SCHEMA_VERSION,
        "entry_id": entry_id,
        "lockname": lockname,
        "mappings": {},
        "aliases": {},
        "migration_notes": [note],
    }


def migrate_to_v1(
    raw: dict[str, Any], defaults: tuple[str, str | None, int, int, str]
) -> dict[str, Any]:
    """Migrate raw Store data to schema version 1.

    Args:
        raw: The raw store dict to migrate.
        defaults: ``(entry_id, lockname, start_slot, max_slots, now_str)``
            fallbacks for missing top-level fields.

    Returns:
        A dict conforming to schema version 1.
    """
    entry_id, lockname, start_slot, max_slots, now_str = defaults
    migration_notes = raw.get("migration_notes", [])
    if not isinstance(migration_notes, list):
        migration_notes = []
    return {
        "schema_version": 1,
        "entry_id": raw.get("entry_id", entry_id),
        "lockname": raw.get("lockname", lockname),
        "start_slot": raw.get("start_slot", start_slot),
        "max_slots": raw.get("max_slots", max_slots),
        "updated_at": raw.get("updated_at", now_str),
        "mappings": raw.get("mappings", {}),
        "aliases": raw.get("aliases", {}),
        "migration_notes": [*migration_notes, "legacy_authoritative_fields_ignored"],
    }


def normalize_loaded_store(
    raw: Any, defaults: tuple[str, str | None, int, int, str]
) -> dict[str, Any] | None:
    """Validate and scrub a loaded Store payload.

    Args:
        raw: The raw value returned by the Store loader.
        defaults: ``(entry_id, lockname, start_slot, max_slots, now_str)``
            fallbacks used when migrating legacy payloads.

    Returns:
        The normalized Store dict, or ``None`` when the payload is corrupt.
    """
    if not isinstance(raw, dict):
        return None
    schema_version = raw.get("schema_version", 0)
    if not isinstance(schema_version, int):
        return None
    if schema_version > STORE_SCHEMA_VERSION:
        return None
    if schema_version < 1:
        raw = migrate_to_v1(raw, defaults)
    mappings = raw.get("mappings", {})
    if not isinstance(mappings, dict):
        return None
    aliases = raw.get("aliases", {})
    if aliases is None:
        raw["aliases"] = {}
    elif not isinstance(aliases, dict):
        return None
    migration_notes = raw.get("migration_notes", [])
    if migration_notes is None:
        raw["migration_notes"] = []
    elif not isinstance(migration_notes, list):
        return None
    for mapping in mappings.values():
        if not isinstance(mapping, dict):
            return None
        last_obs = mapping.get("last_observed_actual")
        if last_obs is not None:
            if not isinstance(last_obs, dict):
                return None
            last_obs.pop("pin", None)
            last_obs.pop("code", None)
            last_obs.pop("slot_code", None)
    raw.setdefault("mappings", {})
    raw.setdefault("aliases", {})
    raw.setdefault("migration_notes", [])
    return cast("dict[str, Any]", raw)


def build_save_payload(
    current_store: dict[str, Any],
    meta: tuple[str, str | None, int, int, str],
) -> dict[str, Any]:
    """Build the scrubbed cache-only Store payload to persist.

    Args:
        current_store: The live ``_slot_mappings`` dict.
        meta: ``(entry_id, lockname, start_slot, max_slots, now_str)``.

    Returns:
        A Store payload with raw code-bearing keys removed from each
        mapping's ``last_observed_actual`` snapshot.
    """
    entry_id, lockname, start_slot, max_slots, now_str = meta
    mappings: dict[str, Any] = {}
    for key, value in current_store.get("mappings", {}).items():
        mapping = dict(value)
        last_obs = mapping.get("last_observed_actual")
        if last_obs is not None:
            last_obs = dict(last_obs)
            last_obs.pop("pin", None)
            last_obs.pop("code", None)
            last_obs.pop("slot_code", None)
            mapping["last_observed_actual"] = last_obs
        mappings[key] = mapping
    return {
        "schema_version": STORE_SCHEMA_VERSION,
        "entry_id": entry_id,
        "lockname": lockname,
        "start_slot": start_slot,
        "max_slots": max_slots,
        "updated_at": now_str,
        "mappings": mappings,
        "aliases": current_store.get("aliases", {}),
        "blocked_slots": current_store.get("blocked_slots", {}),
        "last_plan": current_store.get("last_plan", {}),
        "migration_notes": current_store.get("migration_notes", []),
    }


def build_store_sync_plan(
    plan: DesiredPlan,
    res_by_key: dict[str, Reservation],
    operation_results: list[OperationResult],
    actual_by_slot: dict[int, dict[str, Any]],
    current_store: dict[str, Any],
    meta: tuple[str, str | None, int, int, str],
) -> StoreSyncPlan:
    """Compute the Store synchronization plan for a refresh cycle.

    Args:
        plan: The desired reconciliation plan.
        res_by_key: Reservations indexed by identity key.
        operation_results: Results of the apply operations.
        actual_by_slot: Observed actual-state dicts keyed by slot number.
        current_store: The current ``_slot_mappings`` dict (read-only here).
        meta: ``(entry_id, lockname, start_slot, max_slots, now_str)``.

    Returns:
        A :class:`StoreSyncPlan` describing removals, upserts, and metadata.
    """
    now_str = meta[4]
    mappings: dict[str, Any] = current_store.get("mappings", {})
    confirmed_clear_slots = {
        result.slot
        for result in operation_results
        if result.kind == "clear" and result.confirmed
    }
    failed_set_slots = {
        result.slot
        for result in operation_results
        if result.kind == "set" and result.failed
    }

    remove_keys: list[str] = [
        key
        for key, mapping in mappings.items()
        if mapping.get("slot") in confirmed_clear_slots
    ]
    upserts: dict[str, dict[str, Any]] = {}
    for identity_key, slot in plan.selected.items():
        if slot in confirmed_clear_slots or slot in failed_set_slots:
            continue
        res = res_by_key.get(identity_key)
        if res is None:
            continue
        remove_keys.extend(
            key
            for key, mapping in mappings.items()
            if key != identity_key and mapping.get("slot") == slot
        )
        actual = actual_by_slot.get(slot) or {}
        upserts[identity_key] = build_cache_mapping(slot, res, actual, now_str)

    return StoreSyncPlan(
        remove_identity_keys=remove_keys,
        upsert_mappings=upserts,
        metadata=_store_metadata(plan, current_store, meta),
    )
