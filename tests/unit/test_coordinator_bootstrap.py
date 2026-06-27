# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Tests for the pure Keymaster bootstrap/adoption coordinator helpers."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone

from custom_components.rental_control.coordinator_helpers import keymaster_bootstrap
from custom_components.rental_control.coordinator_helpers.models import (
    AdoptionMappingDecision,
)
from custom_components.rental_control.coordinator_helpers.models import (
    BootstrapDecision,
)
from custom_components.rental_control.coordinator_helpers.models import (
    KeymasterSlotSnapshot,
)


def test_plan_bootstrap_slot_skips_missing_pin() -> None:
    """A slot without a PIN state is skipped."""
    snapshot = KeymasterSlotSnapshot(slot=10, name_state="Guest")
    decision = keymaster_bootstrap.plan_bootstrap_slot(
        snapshot,
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    assert isinstance(decision, BootstrapDecision)
    assert decision.override_update is None
    assert decision.skip_reason == "missing_pin"


def test_plan_bootstrap_slot_builds_override() -> None:
    """A named, coded slot produces an override update."""
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 2, tzinfo=timezone.utc)
    snapshot = KeymasterSlotSnapshot(
        slot=10,
        name_state="Guest",
        pin_state="1234",
        use_date_range_state="off",
    )
    decision = keymaster_bootstrap.plan_bootstrap_slot(snapshot, start, end)
    assert decision.override_update is not None
    assert decision.override_update.slot == 10
    assert decision.override_update.slot_code == "1234"
    assert decision.override_update.slot_name == "Guest"


def test_plan_adoption_skips_existing_slot() -> None:
    """A slot already present in the Store is not adopted."""
    snapshot = KeymasterSlotSnapshot(slot=10, name_state="Guest", pin_state="1234")
    assert (
        keymaster_bootstrap.plan_adoption(snapshot, {10}, "entry", "RC ", "now") is None
    )


def test_plan_adoption_builds_mapping() -> None:
    """A new coded slot yields an adoption mapping decision."""
    snapshot = KeymasterSlotSnapshot(slot=11, name_state="Guest", pin_state="1234")
    decision = keymaster_bootstrap.plan_adoption(snapshot, set(), "entry", "", "now")
    assert isinstance(decision, AdoptionMappingDecision)
    assert decision.slot == 11
    assert decision.identity_key == "adopted.entry.slot11"
    assert isinstance(decision.mapping, dict)
