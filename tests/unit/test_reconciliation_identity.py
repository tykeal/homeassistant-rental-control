# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Focused tests for reconciliation identity helpers."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone

from custom_components.rental_control.reconciliation import extract_booking_aliases
from custom_components.rental_control.reconciliation import make_reservation_fingerprint
from custom_components.rental_control.reconciliation import (
    normalize_slot_name_for_fingerprint,
)
from custom_components.rental_control.reconciliation.identity import _names_match


def test_fingerprint_normalizes_names_and_utc_values() -> None:
    """Equivalent name and timezone forms produce the same fingerprint."""
    start = datetime(2026, 7, 1, 12, tzinfo=timezone.utc)
    end = datetime(2026, 7, 2, 12, tzinfo=timezone.utc)
    assert normalize_slot_name_for_fingerprint(" Guest ") == "guest"
    assert make_reservation_fingerprint(
        "entry", " Guest ", start, end
    ) == make_reservation_fingerprint("entry", "guest", start, end)


def test_booking_aliases_and_prefix_matching_stay_conservative() -> None:
    """Airbnb aliases extract and generic unsafe prefixes do not match."""
    assert extract_booking_aliases("Stay HM12345678", "") == {"HM12345678"}
    assert _names_match("RC Ann", "Ann", "RC Ann", prefix="RC ")
    assert not _names_match("Anna", "Ann", None, prefix="")
