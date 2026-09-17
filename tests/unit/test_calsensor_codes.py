# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Calendar sensor code-publication parity tests."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from unittest.mock import patch

from homeassistant.helpers.entity import EntityCategory

from custom_components.rental_control.const import DOMAIN
from custom_components.rental_control.const import NAME
from custom_components.rental_control.sensors.calsensor import RentalControlCalSensor

START = datetime(2026, 9, 17, 16, tzinfo=timezone.utc)
END = datetime(2026, 9, 20, 11, tzinfo=timezone.utc)


def test_sensor_path_never_invokes_code_generation(hass) -> None:
    """Calendar sensors read coordinator codes instead of generating codes."""
    event = SimpleNamespace(
        summary="Reserved - Jane Doe",
        description="Last 4 Digits: 9876",
        start=START,
        end=END,
        location=None,
        uid="sensor-never-generates",
    )
    coordinator = MagicMock()
    coordinator.name = "Test Rental"
    coordinator.unique_id = "test_unique_id"
    coordinator.entry_id = "test_entry_id"
    coordinator.last_update_success = True
    coordinator.data = [event]
    coordinator.event_prefix = ""
    coordinator.event_overrides = None
    coordinator.get_slot_assignment.return_value = None
    coordinator.get_slot_code.return_value = None
    coordinator.device_info = {
        "identifiers": {(DOMAIN, "test_unique_id")},
        "name": f"{NAME} Test Rental",
    }
    sensor = RentalControlCalSensor(hass, coordinator, f"{NAME} Test", 0)
    sensor.async_write_ha_state = MagicMock()

    with patch(
        "custom_components.rental_control.codegen.generate_slot_code",
        return_value="9876",
    ) as generate:
        sensor._handle_coordinator_update()

    assert sensor.entity_category == EntityCategory.DIAGNOSTIC
    assert sensor.extra_state_attributes["slot_code"] is None
    generate.assert_not_called()
