# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Verify date-only updates on exact verified Keymaster entities."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest

from custom_components.rental_control import util
from custom_components.rental_control.coordinator import RentalControlCoordinator
from custom_components.rental_control.coordinator_helpers.coordinator_config_shell import (
    CoordinatorConfigMixin,
)
from custom_components.rental_control.keymaster_operations import (
    VerifiedOperationRejected,
)
from custom_components.rental_control.sensors.checkin.verified_date import (
    async_update_verified_lock_code_expiry,
)

from tests.integration.test_verified_consumer_dispatch import _CODE
from tests.integration.test_verified_consumer_dispatch import _OWNER_IDENTITY
from tests.integration.test_verified_consumer_dispatch import _SLOT
from tests.integration.test_verified_consumer_dispatch import (
    _register_recording_services,
)
from tests.integration.test_verified_consumer_dispatch import _setup_verified_consumer

_RELEASES = ("v0.6.1", "v0.6.5")


@pytest.mark.parametrize("release", _RELEASES)
async def test_verified_override_and_buffer_updates_preserve_code_and_suppress_feedback(
    hass,
    tmp_path: Path,
    release: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct and buffer-driven amendments confirm exact addresses only."""
    coordinator, store, entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage", release
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


@pytest.mark.parametrize("release", _RELEASES)
async def test_buffer_update_fences_queued_full_state_feedback(
    hass,
    tmp_path: Path,
    release: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A queued full snapshot cannot undo a confirmed buffer-time update."""
    coordinator, store, entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage", release
    )
    events: list[dict[str, Any]] = []
    await _register_recording_services(hass, store, entity_ids, events)
    original_deps = util._state_deps()

    async def immediate_sleep(_delay: float) -> None:
        """Skip feedback settling delay while orchestrating the race."""

    monkeypatch.setattr(
        util,
        "_state_deps",
        lambda: replace(original_deps, sleep=immediate_sleep),
    )
    start = datetime.now(UTC) + timedelta(hours=1)
    end = datetime.now(UTC) + timedelta(hours=2)
    reservation = SimpleNamespace(
        identity_key=_OWNER_IDENTITY,
        slot_name="Guest",
        slot_code=_CODE,
        start=start,
        end=end,
        buffered_start=start,
        buffered_end=end,
        display_slot_name="Guest",
    )
    result = await coordinator.event_overrides._apply_set(
        coordinator, _SLOT, reservation, "buffer-feedback-race"
    )
    assert result.confirmed
    coordinator.async_request_refresh = AsyncMock()
    coordinator.update_event_overrides = (
        RentalControlCoordinator.update_event_overrides.__get__(
            coordinator, RentalControlCoordinator
        )
    )
    coordinator.code_buffer_before = 15
    coordinator.code_buffer_after = 30
    coordinator._slot_mappings = {"mappings": {_OWNER_IDENTITY: {"slot": _SLOT}}}

    entered_update = asyncio.Event()
    release_update = asyncio.Event()
    original_update = coordinator.update_event_overrides

    async def pause_before_update(*args: Any, **kwargs: Any) -> None:
        """Pause after the stale full snapshot was read but before its commit."""
        entered_update.set()
        await release_update.wait()
        await original_update(*args, **kwargs)

    monkeypatch.setattr(coordinator, "update_event_overrides", pause_before_update)
    event = MagicMock()
    event.data = {"entity_id": entity_ids[f"text.code_slots:{_SLOT}.name"]}
    feedback = asyncio.create_task(
        util.handle_state_change(hass, coordinator.config_entry, event)
    )
    await entered_update.wait()

    await CoordinatorConfigMixin._async_update_buffer_times(
        coordinator, old_before=0, old_after=0
    )
    expected_start = start - timedelta(minutes=15)
    expected_end = end + timedelta(minutes=30)
    override = coordinator.event_overrides.overrides[_SLOT]
    assert override is not None
    assert override["start_time"] == expected_start
    assert override["end_time"] == expected_end

    release_update.set()
    await feedback
    override = coordinator.event_overrides.overrides[_SLOT]
    assert override is not None
    assert override["start_time"] == expected_start
    assert override["end_time"] == expected_end


@pytest.mark.parametrize(
    "failure",
    (
        "overrides_missing",
        "slot_absent",
        "slot_ambiguous",
        "owner_mismatch",
        "source_unverified",
    ),
)
async def test_date_update_requires_unique_verified_slot_and_owner(
    hass,
    failure: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Date writes stop before intent creation when routing is not exact."""
    from custom_components.rental_control.sensors.checkin import verified_date

    start = datetime.now(UTC)
    coordinator = SimpleNamespace(
        event_overrides=None,
        start_slot=_SLOT,
        max_events=1,
        get_slot_assignment=lambda _identity: _SLOT,
    )
    if failure != "overrides_missing":
        names = {
            _SLOT: None if failure == "slot_absent" else "Guest",
            _SLOT + 1: "Guest" if failure == "slot_ambiguous" else None,
        }
        coordinator.max_events = 2 if failure == "slot_ambiguous" else 1
        coordinator.event_overrides = SimpleNamespace(
            get_slot_name=lambda slot: names.get(slot)
        )
    if failure == "owner_mismatch":
        coordinator.get_slot_assignment = lambda _identity: _SLOT + 1
    config_entry = SimpleNamespace(entry_id="rc-entry")
    entity = SimpleNamespace(
        coordinator=coordinator,
        _config_entry=config_entry,
        _tracked_event_slot_name="Guest",
        _tracked_event_start=start,
        _tracked_event_end=start + timedelta(hours=1),
        _hass=hass,
    )
    calls: list[str] = []

    def forbidden(*_args: Any, **_kwargs: Any) -> None:
        """Record any intent preparation past a failed precondition."""
        calls.append("intent")

    monkeypatch.setattr(verified_date, "prepare_verified_intent", forbidden)
    if failure == "source_unverified":
        monkeypatch.setattr(verified_date, "verified_slot_source", lambda *_a: None)

    result = await async_update_verified_lock_code_expiry(
        entity,
        start + timedelta(hours=2),
        identity_end=None,
    )

    assert not result
    assert calls == []


@pytest.mark.parametrize("error_kind", ["rejected", "unexpected"])
async def test_date_update_fails_closed_after_verified_dispatch_error(
    hass,
    error_kind: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dispatch failures do not report a date write as confirmed."""
    from custom_components.rental_control.sensors.checkin import verified_date

    start = datetime.now(UTC)
    coordinator = SimpleNamespace(
        event_overrides=SimpleNamespace(get_slot_name=lambda _slot: "Guest"),
        start_slot=_SLOT,
        max_events=1,
        get_slot_assignment=lambda _identity: _SLOT,
    )
    config_entry = SimpleNamespace(entry_id="rc-entry")
    entity = SimpleNamespace(
        coordinator=coordinator,
        _config_entry=config_entry,
        _tracked_event_slot_name="Guest",
        _tracked_event_start=start,
        _tracked_event_end=start + timedelta(hours=1),
        _hass=hass,
    )

    @asynccontextmanager
    async def slot_operation(_coordinator: Any, _slot: int):
        """Supply the per-slot serialization context without provider I/O."""
        yield

    class FailedIntent:
        """Represent a verified provider dispatch that did not complete."""

        async def async_call(
            self, _property_key: str, _service: str, _data: dict[str, str]
        ) -> None:
            """Raise the injected provider failure without exposing details."""
            if error_kind == "rejected":
                raise VerifiedOperationRejected("private provider details")
            raise RuntimeError("private provider details")

    monkeypatch.setattr(verified_date, "async_slot_operation", slot_operation)
    monkeypatch.setattr(
        verified_date,
        "verified_slot_source",
        lambda *_args: SimpleNamespace(),
    )
    monkeypatch.setattr(
        verified_date,
        "prepare_verified_intent",
        lambda *_args: _resolved(FailedIntent()),
    )

    result = await async_update_verified_lock_code_expiry(
        entity,
        start + timedelta(hours=2),
        identity_end=None,
    )

    assert not result


async def test_date_update_requires_prepared_intent_before_dispatch(
    hass,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing durable owner fence cannot trigger a date service call."""
    from custom_components.rental_control.sensors.checkin import verified_date

    start = datetime.now(UTC)
    coordinator = SimpleNamespace(
        event_overrides=SimpleNamespace(get_slot_name=lambda _slot: "Guest"),
        start_slot=_SLOT,
        max_events=1,
        get_slot_assignment=lambda _identity: _SLOT,
    )
    entity = SimpleNamespace(
        coordinator=coordinator,
        _config_entry=SimpleNamespace(entry_id="rc-entry"),
        _tracked_event_slot_name="Guest",
        _tracked_event_start=start,
        _tracked_event_end=start + timedelta(hours=1),
        _hass=hass,
    )

    @asynccontextmanager
    async def slot_operation(_coordinator: Any, _slot: int):
        """Supply the per-slot serialization context without provider I/O."""
        yield

    monkeypatch.setattr(verified_date, "async_slot_operation", slot_operation)
    monkeypatch.setattr(
        verified_date,
        "verified_slot_source",
        lambda *_args: SimpleNamespace(),
    )
    monkeypatch.setattr(
        verified_date,
        "prepare_verified_intent",
        lambda *_args: _resolved(None),
    )

    result = await async_update_verified_lock_code_expiry(
        entity,
        start + timedelta(hours=2),
        identity_end=None,
    )

    assert not result


async def _resolved(value: Any) -> Any:
    """Return one immediate intent result for the narrow failure-boundary test."""
    return value
