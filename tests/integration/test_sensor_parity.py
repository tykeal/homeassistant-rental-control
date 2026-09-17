# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Integration coverage for allocator-backed sensor publication parity."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from typing import cast
from unittest.mock import MagicMock

from custom_components.rental_control.allocator.allocator import DoorCodeAllocator
from custom_components.rental_control.const import COORDINATOR
from custom_components.rental_control.const import DOMAIN
from custom_components.rental_control.coordinator import RentalControlCoordinator
from custom_components.rental_control.coordinator_helpers import code_allocation
from custom_components.rental_control.reconciliation import DesiredPlan
from custom_components.rental_control.reconciliation import ManagedSlot
from custom_components.rental_control.reconciliation import SlotStatus
from custom_components.rental_control.sensors.calsensor import RentalControlCalSensor

from tests.integration.test_allocator_cross_entry import _event
from tests.integration.test_allocator_cross_entry import _fake_hass
from tests.integration.test_allocator_cross_entry import _free_slot
from tests.integration.test_allocator_cross_entry import _reservation


async def test_sensor_publishes_collision_resolved_physical_code(
    hass: Any,
    monkeypatch,
) -> None:
    """Sensor exposes the collision-resolved code observed on the lock slot."""
    allocator = _allocator()
    monkeypatch.setattr(code_allocation, "get_allocator", lambda _hass: allocator)
    first_event = _event("Reserved: Alpha", "Reserved same dates", "uid-a")
    second_event = _event("Reserved: Bravo", "Reserved same dates", "uid-b")
    first_reservations = [_reservation("entry-a", first_event, "date_based")]
    second_reservations = [_reservation("entry-b", second_event, "date_based")]

    await code_allocation.async_resolve_codes(
        _fake_hass(), "entry-a", "front", 4, [_free_slot(1)], first_reservations
    )
    await code_allocation.async_resolve_codes(
        _fake_hass(), "entry-b", "front", 4, [_free_slot(2)], second_reservations
    )

    preferred_code = first_reservations[0].slot_code
    resolved = second_reservations[0]
    resolved_code = resolved.slot_code
    assert resolved_code is not None
    assert preferred_code is not None
    assert resolved_code != preferred_code

    real_coordinator = object.__new__(RentalControlCoordinator)
    real_coordinator.event_overrides = object()
    real_coordinator._latest_res_by_key = {resolved.identity_key: resolved}
    real_coordinator._observed_slot_codes = {}
    plan = DesiredPlan(plan_id="sensor-parity", generated_at=resolved.start)
    plan.selected[resolved.identity_key] = 2
    real_coordinator._latest_plan = plan
    real_coordinator._record_observed_slot_codes(
        plan,
        {resolved.identity_key: resolved},
        [
            ManagedSlot(
                slot=2,
                managed=True,
                status=SlotStatus.OCCUPIED,
                actual_code=resolved_code,
            )
        ],
    )

    coordinator = _sensor_coordinator(real_coordinator, second_event, 2)
    hass.data.setdefault(DOMAIN, {})["entry-b"] = {COORDINATOR: coordinator}
    sensor = RentalControlCalSensor(hass, coordinator, "Rental Control Test", 0)
    sensor.async_write_ha_state = MagicMock()

    sensor._handle_coordinator_update()

    assert sensor.extra_state_attributes["slot_code"] == resolved_code
    assert sensor.extra_state_attributes["slot_code"] != preferred_code
    assert sensor.extra_state_attributes["slot_number"] == 2


def _sensor_coordinator(
    source: RentalControlCoordinator,
    event: Any,
    slot: int,
) -> MagicMock:
    """Build a sensor-facing coordinator backed by real parity methods."""
    coordinator = MagicMock()
    coordinator.name = "Sensor Parity Rental"
    coordinator.unique_id = "sensor-parity-entry"
    coordinator.entry_id = "entry-b"
    coordinator.last_update_success = True
    coordinator.data = [event]
    coordinator.event_prefix = ""
    coordinator.event_overrides = object()
    coordinator.get_slot_assignment.return_value = slot
    coordinator.get_slot_code.side_effect = source.get_slot_code
    coordinator.device_info = {
        "identifiers": {(DOMAIN, "sensor-parity-entry")},
        "name": "Sensor Parity Rental",
    }
    return coordinator


def _allocator() -> DoorCodeAllocator:
    """Build an allocator with persistence disabled for sensor integration."""
    allocator = DoorCodeAllocator(_fake_hass())
    allocator._store = cast(Any, SimpleNamespace(async_save=lambda _registry: None))
    return allocator
