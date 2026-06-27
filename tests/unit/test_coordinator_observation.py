# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Tests for the pure Keymaster slot-observation coordinator helper."""

from __future__ import annotations

from custom_components.rental_control.coordinator_helpers import keymaster_observation
from custom_components.rental_control.coordinator_helpers.models import (
    KeymasterSlotSnapshot,
)
from custom_components.rental_control.reconciliation import ManagedSlot
from custom_components.rental_control.reconciliation import SlotStatus


def test_classify_slot_unknown_when_states_missing() -> None:
    """Missing name/pin states classify the slot as UNKNOWN."""
    snapshot = KeymasterSlotSnapshot(slot=4)
    slot, actual = keymaster_observation.classify_slot(snapshot, None)
    assert isinstance(slot, ManagedSlot)
    assert slot.slot == 4
    assert slot.status is SlotStatus.UNKNOWN
    assert isinstance(actual, dict)


def test_classify_slot_occupied_when_named_and_coded() -> None:
    """A named, coded slot is classified as managed and occupied."""
    snapshot = KeymasterSlotSnapshot(
        slot=6,
        name_state="Guest",
        pin_state="1234",
        use_date_range_state="off",
        enabled_state="on",
    )
    slot, actual = keymaster_observation.classify_slot(snapshot, None)
    assert slot.slot == 6
    assert slot.managed is True
    assert slot.actual_name == "Guest"
    assert slot.status is SlotStatus.OCCUPIED
