# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Focused tests for reconciliation rematch dispatch."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone

from custom_components.rental_control.reconciliation import RematchKind
from custom_components.rental_control.reconciliation import Reservation
from custom_components.rental_control.reconciliation import find_reservation_rematch


def _reservation() -> Reservation:
    """Build a reservation for rematch tests."""
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    end = datetime(2026, 7, 2, tzinfo=timezone.utc)
    return Reservation(
        "new",
        start,
        end,
        start,
        end,
        "Guest",
        "Guest",
        "RC Guest",
        "1234",
        uid_aliases={"uid-1"},
    )


def test_uid_alias_rematch_keeps_date_shifted_semantics() -> None:
    """UID alias rematches remain date-shifted after exact match misses."""
    mapping = {
        "old": {"slot": 1, "identity": {"slot_name": "Guest", "uid_aliases": ["uid-1"]}}
    }
    result = find_reservation_rematch(_reservation(), mapping)
    assert result.kind is RematchKind.UID_ALIAS
    assert result.matched_identity_key == "old"
    assert result.date_shifted
