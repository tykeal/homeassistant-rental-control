# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Verify date-only updates on exact verified Keymaster entities."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.rental_control import util
from custom_components.rental_control.coordinator_helpers.coordinator_config_shell import (
    CoordinatorConfigMixin,
)

from tests.integration.test_verified_consumer_dispatch import _CODE
from tests.integration.test_verified_consumer_dispatch import _OWNER_IDENTITY
from tests.integration.test_verified_consumer_dispatch import _SLOT
from tests.integration.test_verified_consumer_dispatch import (
    _register_recording_services,
)
from tests.integration.test_verified_consumer_dispatch import _setup_verified_consumer


async def test_verified_override_and_buffer_updates_preserve_code_and_suppress_feedback(
    hass,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct and buffer-driven amendments confirm exact addresses only."""
    coordinator, store, entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage"
    )
    events: list[dict[str, Any]] = []
    await _register_recording_services(hass, store, entity_ids, events)

    original_deps = util._keymaster_deps

    async def immediate_sleep(_delay: float) -> None:
        """Skip retry delays while checking verified date amendments."""
        return None

    monkeypatch.setattr(
        util,
        "_keymaster_deps",
        lambda: replace(original_deps(), sleep=immediate_sleep),
    )
    reservation = SimpleNamespace(
        identity_key=_OWNER_IDENTITY,
        slot_name="Guest",
        slot_code=_CODE,
        start=datetime.now(UTC) + timedelta(hours=1),
        end=datetime.now(UTC) + timedelta(hours=2),
        buffered_start=datetime.now(UTC) + timedelta(hours=1),
        buffered_end=datetime.now(UTC) + timedelta(hours=2),
        display_slot_name="Guest",
    )
    set_result = await coordinator.event_overrides._apply_set(
        coordinator, _SLOT, reservation, "date-test-set"
    )
    assert set_result.confirmed
    before = store.loaded_result
    assert before is not None and before.data is not None
    before_ledger = deepcopy(before.data["ledger"])
    before_transaction_id = before.transaction_id
    name_id = entity_ids[f"text.code_slots:{_SLOT}.name"]
    pin_id = entity_ids[f"text.code_slots:{_SLOT}.pin"]

    start = datetime.now(UTC) + timedelta(hours=3)
    end = datetime.now(UTC) + timedelta(hours=4)
    first_date_event = len(events)
    update_result = await coordinator.event_overrides._apply_update_times(
        coordinator,
        _SLOT,
        SimpleNamespace(
            identity_key=_OWNER_IDENTITY,
            slot_name="Guest",
            slot_code=_CODE,
            start=start,
            end=end,
            buffered_start=start,
            buffered_end=end,
        ),
    )
    assert update_result.confirmed
    direct_events = events[first_date_event:]
    start_id = entity_ids[f"datetime.code_slots:{_SLOT}.accesslimit_date_range_start"]
    end_id = entity_ids[f"datetime.code_slots:{_SLOT}.accesslimit_date_range_end"]
    assert {event["entity_id"] for event in direct_events} == {start_id, end_id}
    assert all(event["domain"] == "datetime" for event in direct_events)
    assert all(event["fences"] == ("allocation",) for event in direct_events)
    assert hass.states.get(name_id).state == "Guest"
    assert hass.states.get(pin_id).state == _CODE
    assert coordinator.event_overrides.should_suppress_state_change(
        _SLOT, start_id, hass.states.get(start_id).state
    )
    assert not coordinator.event_overrides.should_suppress_state_change(
        _SLOT,
        "datetime.consumer_code_slot_21_date_range_start",
        hass.states.get(start_id).state,
    )

    coordinator.code_buffer_before = 15
    coordinator.code_buffer_after = 30
    coordinator._slot_mappings = {"mappings": {_OWNER_IDENTITY: {"slot": _SLOT}}}
    buffer_event_start = len(events)
    await CoordinatorConfigMixin._async_update_buffer_times(
        coordinator, old_before=0, old_after=0
    )
    buffer_events = events[buffer_event_start:]
    assert {event["entity_id"] for event in buffer_events} == {start_id, end_id}
    assert all(event["domain"] == "datetime" for event in buffer_events)
    assert all(event["fences"] == ("allocation",) for event in buffer_events)
    assert (
        hass.states.get(start_id).state == (start - timedelta(minutes=15)).isoformat()
    )
    assert hass.states.get(end_id).state == (end + timedelta(minutes=30)).isoformat()
    assert hass.states.get(name_id).state == "Guest"
    assert hass.states.get(pin_id).state == _CODE

    after = store.loaded_result
    assert after is not None and after.data is not None
    assert after.transaction_id == before_transaction_id
    assert after.data["ledger"] == before_ledger
