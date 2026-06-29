# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Focused tests for extracted Keymaster service helpers."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

from homeassistant.util import dt as dt_util
import pytest

from custom_components.rental_control import keymaster_services
from custom_components.rental_control import util
from custom_components.rental_control.helpers import OperationResult


async def test_keymaster_deps_read_util_attributes_at_call_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Util service wrappers pass patched util dependencies at call time."""
    captured = {}

    async def fake_set_code(coordinator, event, slot, deps):
        """Capture runtime dependencies supplied by the util wrapper."""
        captured["deps"] = deps
        return OperationResult(kind="set", slot=slot, confirmed=True)

    async def fake_sleep(delay: float) -> None:
        """Stand in for patched util asyncio.sleep."""

    create_notification = MagicMock()
    dismiss_notification = MagicMock()
    track_state_change = MagicMock()
    monkeypatch.setattr(keymaster_services, "async_fire_set_code", fake_set_code)
    monkeypatch.setattr(util.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(util, "async_track_state_change_event", track_state_change)
    monkeypatch.setattr(util, "_SET_CODE_CONFIRMATION_TIMEOUT", 1.25)
    monkeypatch.setattr(util, "pn_create", create_notification)
    monkeypatch.setattr(util, "pn_dismiss", dismiss_notification)

    result = await util.async_fire_set_code(MagicMock(), MagicMock(), 10)

    deps = captured["deps"]
    assert result == OperationResult(kind="set", slot=10, confirmed=True)
    assert deps.sleep is fake_sleep
    assert deps.track_state_change is track_state_change
    assert deps.confirmation_timeout == 1.25
    assert deps.create_notification is create_notification
    assert deps.dismiss_notification is dismiss_notification


async def test_update_times_writes_end_before_start() -> None:
    """Extracted update-times helper preserves Keymaster service-call order."""
    coordinator = MagicMock()
    coordinator.lockname = "front_door"
    coordinator.code_buffer_before = 0
    coordinator.code_buffer_after = 0
    coordinator.hass.services.async_call = AsyncMock()
    coordinator.event_overrides.verify_slot_ownership.return_value = True
    coordinator.hass.states.get.side_effect = lambda entity_id: MagicMock(
        state={
            "datetime.front_door_code_slot_10_date_range_start": "2025-01-15T16:00:00+00:00",
            "datetime.front_door_code_slot_10_date_range_end": "2025-01-17T11:00:00+00:00",
        }[entity_id]
    )
    event = MagicMock()
    event.extra_state_attributes = {
        "slot_name": "Guest",
        "start": datetime(2025, 1, 15, 16, tzinfo=dt_util.UTC),
        "end": datetime(2025, 1, 17, 11, tzinfo=dt_util.UTC),
    }

    result = await util.async_fire_update_times(coordinator, event, 10)

    calls = coordinator.hass.services.async_call.await_args_list
    assert result.confirmed is True
    assert calls[0].kwargs["target"]["entity_id"].endswith("date_range_end")
    assert calls[1].kwargs["target"]["entity_id"].endswith("date_range_start")


def test_operation_result_never_stores_raw_pin() -> None:
    """OperationResult fields do not include raw slot PIN storage."""
    result = OperationResult(kind="set", slot=10, confirmed=True)

    assert "pin" not in result.__dataclass_fields__
    assert "slot_code" not in result.__dataclass_fields__
