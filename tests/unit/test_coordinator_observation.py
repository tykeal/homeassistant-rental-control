# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Tests for the pure Keymaster slot-observation coordinator helper."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from custom_components.rental_control import coordinator_helpers
from custom_components.rental_control.coordinator_helpers import keymaster_observation
from custom_components.rental_control.coordinator_helpers.coordinator_setup_shell import (
    CoordinatorSetupMixin,
)
from custom_components.rental_control.coordinator_helpers.models import (
    KeymasterSlotSnapshot,
)
from custom_components.rental_control.keymaster_identity import CapabilityResolution
from custom_components.rental_control.keymaster_identity import CapabilityStatus
from custom_components.rental_control.keymaster_identity import (
    build_capability_identity,
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


def test_coordinator_snapshot_reads_only_verified_addresses(monkeypatch) -> None:
    """Verified observations do not use lock-name-derived entity IDs."""
    properties = {
        "text.code_slots:21.name": "text.arbitrary_label",
        "text.code_slots:21.pin": "text.arbitrary_pin",
        "switch.code_slots:21.accesslimit_date_range_enabled": "switch.arbitrary_dates",
        "switch.code_slots:21.enabled": "switch.arbitrary_enabled",
        "datetime.code_slots:21.accesslimit_date_range_start": "datetime.arbitrary_start",
        "datetime.code_slots:21.accesslimit_date_range_end": "datetime.arbitrary_end",
    }
    resolutions = {}
    values = {
        "text.arbitrary_label": "Guest",
        "text.arbitrary_pin": "1234",
        "switch.arbitrary_dates": "on",
        "switch.arbitrary_enabled": "on",
        "datetime.arbitrary_start": "2026-06-01T10:00:00+00:00",
        "datetime.arbitrary_end": "2026-06-02T10:00:00+00:00",
    }
    for property_key, entity_id in properties.items():
        identity = build_capability_identity("km-entry", property_key, generation=9)
        assert identity is not None
        resolutions[property_key] = CapabilityResolution(
            identity, CapabilityStatus.READY, entity_id
        )
    lifecycle = SimpleNamespace(
        keymaster_entry_id="km-entry", generation=9, current=resolutions
    )
    monkeypatch.setattr(
        coordinator_helpers.coordinator_setup_shell,
        "get_keymaster_lifecycle",
        lambda *_args: lifecycle,
    )
    monkeypatch.setattr(
        coordinator_helpers.coordinator_setup_shell,
        "resolve_capability",
        lambda _hass, _entry_id, property_key, *, generation: resolutions[property_key],
    )
    hass = MagicMock()
    hass.states.get.side_effect = lambda entity_id: SimpleNamespace(
        state=values[entity_id]
    )
    reader: Any = CoordinatorSetupMixin()
    reader.hass = hass
    reader._entry_id = "rc-entry"
    reader.lockname = "old_slug"

    snapshot = reader._read_slot_snapshot(21)

    assert snapshot.name_state == "Guest"
    assert snapshot.pin_state == "1234"
    assert snapshot.enabled_state == "on"
    assert snapshot.start_state == values["datetime.arbitrary_start"]
    assert all(
        "old_slug" not in call.args[0] for call in hass.states.get.call_args_list
    )


def test_coordinator_verified_snapshot_fails_closed_on_missing_property(
    monkeypatch,
) -> None:
    """A missing exact capability yields an unknown observation, not fallback."""
    property_key = "text.code_slots:21.pin"
    identity = build_capability_identity("km-entry", property_key, generation=9)
    assert identity is not None
    lifecycle = SimpleNamespace(
        keymaster_entry_id="km-entry",
        generation=9,
        current={
            property_key: CapabilityResolution(
                identity, CapabilityStatus.MISSING, "text.guessed_pin"
            )
        },
    )
    monkeypatch.setattr(
        coordinator_helpers.coordinator_setup_shell,
        "get_keymaster_lifecycle",
        lambda *_args: lifecycle,
    )
    hass = MagicMock()
    reader: Any = CoordinatorSetupMixin()
    reader.hass = hass
    reader._entry_id = "rc-entry"
    reader.lockname = "old_slug"

    snapshot = reader._read_slot_snapshot(21)

    assert snapshot.pin_state is None
    hass.states.get.assert_not_called()
