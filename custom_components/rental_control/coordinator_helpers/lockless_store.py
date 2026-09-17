# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Helpers for cache-only lockless slot metadata."""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from homeassistant.util import dt

from ..const import SLOT_STATUS_OCCUPIED
from ..const import STORE_SCHEMA_VERSION

if TYPE_CHECKING:
    from ..reconciliation import Reservation


def sync_lockless_slot_store(
    slot_mappings: dict[str, Any],
    res_by_key: dict[str, Reservation],
    *,
    entry_id: str,
    start_slot: int,
    max_events: int,
) -> None:
    """Persist lockless reservation metadata and published-code state."""
    mappings: dict[str, Any] = slot_mappings.setdefault("mappings", {})
    current_keys = set(res_by_key)
    for stale_key in list(mappings):
        if stale_key not in current_keys:
            mappings.pop(stale_key, None)
    now_str = dt.now().isoformat()
    for reservation in res_by_key.values():
        mappings[reservation.identity_key] = {
            "slot": None,
            "status": SLOT_STATUS_OCCUPIED,
            "operation_id": None,
            "operation_kind": None,
            "identity": {
                "identity_key": reservation.identity_key,
                "summary": reservation.summary,
                "slot_name": reservation.slot_name,
                "start": reservation.start.isoformat(),
                "end": reservation.end.isoformat(),
                "uid_aliases": sorted(reservation.uid_aliases),
                "booking_aliases": sorted(reservation.booking_aliases),
            },
            "missing_count": reservation.missing_count,
            "published_once": reservation.published_once
            or reservation.slot_code is not None,
            "pending_set_since": None,
            "pending_clear_since": None,
            "fingerprint_history": sorted(reservation.fingerprint_history),
            "updated_at": now_str,
            "last_observed_actual": {
                "slot": None,
                "classification": SLOT_STATUS_OCCUPIED,
                "name_state": reservation.display_slot_name,
                "has_code": reservation.slot_code is not None,
                "start_state": reservation.buffered_start.isoformat(),
                "end_state": reservation.buffered_end.isoformat(),
                "use_date_range": None,
                "enabled": None,
            },
        }
    slot_mappings.update(
        {
            "schema_version": STORE_SCHEMA_VERSION,
            "entry_id": entry_id,
            "lockname": None,
            "start_slot": start_slot,
            "max_slots": max_events,
            "updated_at": now_str,
        }
    )
