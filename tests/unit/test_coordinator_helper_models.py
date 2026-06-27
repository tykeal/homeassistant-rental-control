# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Tests for the pure coordinator helper data models."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone

import pytest

from custom_components.rental_control.coordinator_helpers import models


def test_store_datetime_serializes_datetime() -> None:
    """Datetimes are converted to ISO strings; other values pass through."""
    value = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
    assert models._store_datetime(value) == value.isoformat()
    assert models._store_datetime("plain") == "plain"
    assert models._store_datetime(None) is None


def test_adopted_slot_placeholder() -> None:
    """Placeholder names embed the slot number."""
    assert models._adopted_slot_placeholder(7) == "Adopted Slot 7"


def test_format_display_slot_name_without_trim() -> None:
    """Without trimming the prefix is simply prepended."""
    assert models._format_display_slot_name("Guest", "RC ", False, 0) == "RC Guest"


def test_format_display_slot_name_with_trim() -> None:
    """With trimming the combined name is bounded by max length."""
    result = models._format_display_slot_name("LongGuestName", "RC ", True, 8)
    assert result.startswith("RC ")
    assert len(result) <= 8


def test_normalize_event_override_update_positional() -> None:
    """Five positional values become an EventOverrideUpdate."""
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 2, tzinfo=timezone.utc)
    result = models.normalize_event_override_update(
        10, (("1234", "Guest", start, end)), {}
    )
    assert result.slot == 10
    assert result.slot_code == "1234"
    assert result.slot_name == "Guest"
    assert result.start_time == start
    assert result.end_time == end


def test_normalize_event_override_update_keyword() -> None:
    """Keyword arguments are accepted."""
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 2, tzinfo=timezone.utc)
    result = models.normalize_event_override_update(
        None,
        (),
        {
            "slot": 5,
            "slot_code": "9999",
            "slot_name": "Bob",
            "start_time": start,
            "end_time": end,
        },
    )
    assert result.slot == 5
    assert result.slot_name == "Bob"


def test_normalize_event_override_update_passthrough_dataclass() -> None:
    """A pre-built dataclass is returned unchanged."""
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    update = models.EventOverrideUpdate(1, "1111", "X", start, start)
    assert models.normalize_event_override_update(update, (), {}) is update


def test_normalize_event_override_update_rejects_unknown() -> None:
    """Unknown keyword arguments raise TypeError."""
    with pytest.raises(TypeError):
        models.normalize_event_override_update(None, (), {"bogus": 1})


def test_normalize_event_override_update_rejects_missing() -> None:
    """Missing required arguments raise TypeError."""
    with pytest.raises(TypeError):
        models.normalize_event_override_update(10, (), {})


def test_dataclasses_construct() -> None:
    """All exported helper dataclasses construct with minimal args."""
    snapshot = models.KeymasterSlotSnapshot(slot=3)
    assert snapshot.slot == 3
    plan = models.StoreSyncPlan()
    assert plan.remove_identity_keys == []
    assert plan.upsert_mappings == {}
    ghost = models.GhostReservationResult()
    assert ghost.reservations == []
    decision = models.BootstrapDecision(slot=2)
    assert decision.override_update is None
