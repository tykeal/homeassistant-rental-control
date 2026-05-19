# SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Tests for buffer config change triggering slot time updates."""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rental_control.const import DOMAIN
from custom_components.rental_control.coordinator import RentalControlCoordinator
from custom_components.rental_control.event_overrides import EventOverrides

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


def _make_config_entry(
    buffer_before: int = 0, buffer_after: int = 0
) -> MockConfigEntry:
    """Return a mock config entry with specified buffer values."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Test Rental",
        version=10,
        unique_id="buffer-test-id",
        data={
            "name": "Test Rental",
            "url": "https://example.com/calendar.ics",
            "timezone": "America/New_York",
            "checkin": "16:00",
            "checkout": "11:00",
            "start_slot": 10,
            "keymaster_entry_id": "front_door",
            "max_events": 3,
            "days": 90,
            "verify_ssl": True,
            "ignore_non_reserved": False,
            "honor_event_times": False,
            "code_buffer_before": buffer_before,
            "code_buffer_after": buffer_after,
        },
        options={
            "refresh_frequency": 5,
        },
        entry_id="buffer_test_entry_id",
    )


def _make_update_config(buffer_before: int = 0, buffer_after: int = 0) -> dict:
    """Return an update config dict with specified buffer values."""
    return {
        "name": "Test Rental",
        "url": "https://example.com/calendar.ics",
        "timezone": "America/New_York",
        "checkin": "16:00",
        "checkout": "11:00",
        "start_slot": 10,
        "keymaster_entry_id": "front_door",
        "max_events": 3,
        "days": 90,
        "verify_ssl": True,
        "ignore_non_reserved": False,
        "honor_event_times": False,
        "refresh_frequency": 5,
        "code_buffer_before": buffer_before,
        "code_buffer_after": buffer_after,
    }


@pytest.fixture
def mock_entry() -> MockConfigEntry:
    """Return a mock config entry with lock and zero buffers."""
    return _make_config_entry(buffer_before=0, buffer_after=0)


async def test_buffer_change_calls_update_buffer_times(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test that update_config calls _async_update_buffer_times on change.

    When code_buffer_before or code_buffer_after change in the options
    flow, _async_update_buffer_times should be invoked.
    """
    mock_entry.add_to_hass(hass)
    coordinator = RentalControlCoordinator(hass, mock_entry)
    coordinator.async_request_refresh = AsyncMock()

    new_config = _make_update_config(buffer_before=30, buffer_after=60)
    with patch.object(
        coordinator, "_async_update_buffer_times", new_callable=AsyncMock
    ) as mock_buf:
        await coordinator.update_config(new_config)

    mock_buf.assert_awaited_once()


async def test_buffer_no_change_skips_update_buffer_times(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test update_config skips _async_update_buffer_times when unchanged.

    When buffer values remain the same, _async_update_buffer_times
    should not be called.
    """
    mock_entry.add_to_hass(hass)
    coordinator = RentalControlCoordinator(hass, mock_entry)
    coordinator.async_request_refresh = AsyncMock()

    new_config = _make_update_config(buffer_before=0, buffer_after=0)
    with patch.object(
        coordinator, "_async_update_buffer_times", new_callable=AsyncMock
    ) as mock_buf:
        await coordinator.update_config(new_config)

    mock_buf.assert_not_awaited()


async def test_update_buffer_times_updates_assigned_slots(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test _async_update_buffer_times updates Keymaster entities.

    When called, all currently assigned slots should have their
    date range entities updated to reflect the new buffered times.
    """
    mock_entry.add_to_hass(hass)
    coordinator = RentalControlCoordinator(hass, mock_entry)
    coordinator.code_buffer_before = 30
    coordinator.code_buffer_after = 60

    # Set up event overrides with an assigned slot
    tz = ZoneInfo("America/New_York")
    start_time = datetime(2025, 1, 15, 16, 0, tzinfo=tz)
    end_time = datetime(2025, 1, 20, 11, 0, tzinfo=tz)

    overrides = EventOverrides(10, 3)
    overrides._overrides = {
        10: {
            "slot_name": "Guest A",
            "slot_code": "1234",
            "start_time": start_time,
            "end_time": end_time,
        },
        11: None,
        12: None,
    }
    overrides._ready = True
    coordinator.event_overrides = overrides

    captured_calls: list[dict] = []

    def fake_add_call(hass, coro, domain, service, target, data):
        """Capture service call params."""
        captured_calls.append(
            {
                "domain": domain,
                "service": service,
                "target": target,
                "data": data,
            }
        )
        coro.append(AsyncMock(return_value=None)())
        return coro

    with patch(
        "custom_components.rental_control.coordinator.add_call",
        side_effect=fake_add_call,
    ):
        await coordinator._async_update_buffer_times(0, 0)
    assert len(captured_calls) == 2

    # Check correct entities were targeted
    entity_ids = {c["target"] for c in captured_calls}

    assert "datetime.front_door_code_slot_10_date_range_start" in entity_ids
    assert "datetime.front_door_code_slot_10_date_range_end" in entity_ids

    # Verify buffered times: start - 30min, end + 60min
    for call in captured_calls:
        if "start" in call["target"]:
            expected = start_time - timedelta(minutes=30)
            assert call["data"]["datetime"] == expected
        elif "end" in call["target"]:
            expected = end_time + timedelta(minutes=60)
            assert call["data"]["datetime"] == expected


async def test_update_buffer_times_no_assigned_slots(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test _async_update_buffer_times with no assigned slots.

    When all override slots are None, no service calls should be made.
    """
    mock_entry.add_to_hass(hass)
    coordinator = RentalControlCoordinator(hass, mock_entry)
    coordinator.code_buffer_before = 15
    coordinator.code_buffer_after = 30

    overrides = EventOverrides(10, 3)
    overrides._overrides = {10: None, 11: None, 12: None}
    overrides._ready = True
    coordinator.event_overrides = overrides

    with patch(
        "custom_components.rental_control.coordinator.add_call",
    ) as mock_add_call:
        await coordinator._async_update_buffer_times(0, 0)

    mock_add_call.assert_not_called()


async def test_update_buffer_times_no_lockname(
    hass: HomeAssistant,
) -> None:
    """Test _async_update_buffer_times returns early with no lockname.

    When no lock is configured, no service calls should be made.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Rental",
        version=10,
        unique_id="buffer-no-lock-id",
        data={
            "name": "Test Rental",
            "url": "https://example.com/calendar.ics",
            "timezone": "America/New_York",
            "checkin": "16:00",
            "checkout": "11:00",
            "start_slot": 10,
            "max_events": 3,
            "days": 90,
            "verify_ssl": True,
            "ignore_non_reserved": False,
            "honor_event_times": False,
            "code_buffer_before": 0,
            "code_buffer_after": 0,
        },
        options={"refresh_frequency": 5},
        entry_id="buffer_no_lock_entry",
    )
    entry.add_to_hass(hass)
    coordinator = RentalControlCoordinator(hass, entry)
    coordinator.code_buffer_before = 30
    coordinator.code_buffer_after = 60

    assert coordinator.lockname is None

    with patch(
        "custom_components.rental_control.coordinator.add_call",
    ) as mock_add_call:
        await coordinator._async_update_buffer_times(0, 0)

    mock_add_call.assert_not_called()


async def test_update_buffer_times_no_overrides(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test _async_update_buffer_times with event_overrides None.

    When event_overrides is None, no service calls should be made.
    """
    mock_entry.add_to_hass(hass)
    coordinator = RentalControlCoordinator(hass, mock_entry)
    coordinator.code_buffer_before = 30
    coordinator.code_buffer_after = 60
    coordinator.event_overrides = None

    with patch(
        "custom_components.rental_control.coordinator.add_call",
    ) as mock_add_call:
        await coordinator._async_update_buffer_times(0, 0)

    mock_add_call.assert_not_called()


async def test_update_buffer_times_multiple_slots(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test _async_update_buffer_times updates all assigned slots.

    When multiple slots have active overrides, all should get updated.
    """
    mock_entry.add_to_hass(hass)
    coordinator = RentalControlCoordinator(hass, mock_entry)
    coordinator.code_buffer_before = 15
    coordinator.code_buffer_after = 45

    tz = ZoneInfo("America/New_York")
    overrides = EventOverrides(10, 3)
    overrides._overrides = {
        10: {
            "slot_name": "Guest A",
            "slot_code": "1234",
            "start_time": datetime(2025, 1, 15, 16, 0, tzinfo=tz),
            "end_time": datetime(2025, 1, 20, 11, 0, tzinfo=tz),
        },
        11: {
            "slot_name": "Guest B",
            "slot_code": "5678",
            "start_time": datetime(2025, 2, 1, 16, 0, tzinfo=tz),
            "end_time": datetime(2025, 2, 5, 11, 0, tzinfo=tz),
        },
        12: None,
    }
    overrides._ready = True
    coordinator.event_overrides = overrides

    captured_calls: list[dict] = []

    def fake_add_call(hass, coro, domain, service, target, data):
        """Capture service call params."""
        captured_calls.append(
            {
                "domain": domain,
                "service": service,
                "target": target,
                "data": data,
            }
        )
        coro.append(AsyncMock(return_value=None)())
        return coro

    with patch(
        "custom_components.rental_control.coordinator.add_call",
        side_effect=fake_add_call,
    ):
        await coordinator._async_update_buffer_times(0, 0)

    # 2 assigned slots x 2 calls each (start + end) = 4 calls
    assert len(captured_calls) == 4


async def test_update_buffer_times_reverses_old_buffer(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test _async_update_buffer_times avoids double-buffering.

    Override times stored in memory come from Keymaster entities
    which already have the previous buffer applied.  The method
    must reverse the old buffer before applying the new one.
    """
    mock_entry.add_to_hass(hass)
    coordinator = RentalControlCoordinator(hass, mock_entry)
    coordinator.code_buffer_before = 60
    coordinator.code_buffer_after = 45

    tz = ZoneInfo("America/New_York")
    # Unbuffered event times: start=16:00, end=11:00
    raw_start = datetime(2025, 3, 10, 16, 0, tzinfo=tz)
    raw_end = datetime(2025, 3, 15, 11, 0, tzinfo=tz)

    # Overrides contain already-buffered times (old buffer: 30min/30min)
    old_before = 30
    old_after = 30
    buffered_start = raw_start - timedelta(minutes=old_before)
    buffered_end = raw_end + timedelta(minutes=old_after)

    overrides = EventOverrides(10, 3)
    overrides._overrides = {
        10: {
            "slot_name": "Guest X",
            "slot_code": "9999",
            "start_time": buffered_start,
            "end_time": buffered_end,
        },
        11: None,
        12: None,
    }
    overrides._ready = True
    coordinator.event_overrides = overrides

    captured_calls: list[dict] = []

    def fake_add_call(hass, coro, domain, service, target, data):
        """Capture service call params."""
        captured_calls.append(
            {
                "domain": domain,
                "service": service,
                "target": target,
                "data": data,
            }
        )
        coro.append(AsyncMock(return_value=None)())
        return coro

    with patch(
        "custom_components.rental_control.coordinator.add_call",
        side_effect=fake_add_call,
    ):
        await coordinator._async_update_buffer_times(old_before, old_after)

    assert len(captured_calls) == 2

    # New buffer: 60 before, 45 after applied to raw (unbuffered) times
    for call in captured_calls:
        if "start" in call["target"]:
            expected = raw_start - timedelta(minutes=60)
            assert call["data"]["datetime"] == expected
        elif "end" in call["target"]:
            expected = raw_end + timedelta(minutes=45)
            assert call["data"]["datetime"] == expected
