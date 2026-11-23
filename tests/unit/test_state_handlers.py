# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Focused tests for extracted Keymaster state handlers."""

from __future__ import annotations

import asyncio
from datetime import UTC
from datetime import datetime
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest

from custom_components.rental_control import event_overrides as event_overrides_module
from custom_components.rental_control import state_handlers
from custom_components.rental_control import util
from custom_components.rental_control.const import COORDINATOR
from custom_components.rental_control.const import DOMAIN
from custom_components.rental_control.coordinator import RentalControlCoordinator
from custom_components.rental_control.event_overrides import EventOverrides
from custom_components.rental_control.keymaster_identity import CapabilityResolution
from custom_components.rental_control.keymaster_identity import CapabilityStatus
from custom_components.rental_control.keymaster_identity import (
    build_capability_identity,
)
from custom_components.rental_control.state_handlers import StateHandlerDeps


def _verified_resolutions(*, name_ready: bool = True):
    """Build one exact arbitrary-address slot with optional missing name."""
    properties = {
        "button.code_slots:20.reset": "button.unrelated_reset",
        "switch.code_slots:20.enabled": "switch.area_enabled",
        "switch.code_slots:20.accesslimit_date_range_enabled": "switch.area_dates",
        "text.code_slots:20.name": "text.area_label",
        "text.code_slots:20.pin": "text.area_secret",
    }
    results = {}
    for property_key, entity_id in properties.items():
        identity = build_capability_identity(
            "keymaster-entry", property_key, generation=7
        )
        assert identity is not None
        ready = name_ready or property_key != "text.code_slots:20.name"
        results[property_key] = CapabilityResolution(
            identity,
            CapabilityStatus.READY if ready else CapabilityStatus.UNAVAILABLE,
            entity_id,
        )
    return results


async def test_state_handler_deps_read_util_sleep_at_call_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The util state wrapper supplies patched sleep at call time."""
    captured = {}

    async def fake_handle_state_change(hass, config_entry, event, deps):
        """Capture runtime dependencies supplied by the util wrapper."""
        captured["deps"] = deps

    async def fake_sleep(delay: float) -> None:
        """Stand in for patched util asyncio.sleep."""

    monkeypatch.setattr(state_handlers, "handle_state_change", fake_handle_state_change)
    monkeypatch.setattr(util.asyncio, "sleep", fake_sleep)

    await util.handle_state_change(MagicMock(), MagicMock(), MagicMock())

    assert captured["deps"].sleep is fake_sleep


async def test_reset_entity_preserves_empty_override_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reset entities still clear override code and name to empty strings."""
    lockname = "front_door"
    slot = 10
    coordinator = MagicMock()
    coordinator.lockname = lockname
    coordinator.keymaster_runtime_enabled = True
    coordinator.event_overrides.async_update = AsyncMock()
    hass = MagicMock()
    hass.data = {DOMAIN: {"entry-id": {COORDINATOR: coordinator}}}
    config_entry = MagicMock(entry_id="entry-id")
    event = MagicMock()
    event.data = {"entity_id": f"button.{lockname}_code_slot_{slot}_reset"}

    async def fake_sleep(delay: float) -> None:
        """Skip the state-change settle delay."""

    monkeypatch.setattr(util.asyncio, "sleep", fake_sleep)

    await util.handle_state_change(hass, config_entry, event)

    coordinator.event_overrides.async_update.assert_awaited_once()
    call_args = coordinator.event_overrides.async_update.call_args[0]
    assert call_args[0:3] == (slot, "", "")


def test_verified_state_context_uses_exact_property_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verified feedback accepts a property template, not an ID substring."""
    lifecycle = SimpleNamespace(generation=7, current=_verified_resolutions())
    monkeypatch.setattr(
        state_handlers, "get_keymaster_lifecycle", lambda *_args: lifecycle
    )
    coordinator = MagicMock()
    coordinator.config_entry.entry_id = "rc-entry"
    coordinator.event_overrides = None
    coordinator.keymaster_runtime_enabled = True
    event = MagicMock()
    event.data = {
        "entity_id": "text.area_secret",
        "new_state": SimpleNamespace(state="1234"),
    }
    deps = StateHandlerDeps(
        AsyncMock(),
        logging.getLogger(__name__),
        str,
        lambda value, _limit: value,
    )

    context = state_handlers._resolve_context(MagicMock(), event, coordinator, "", deps)

    assert context is not None
    assert context.slot_num == 20
    assert context.property_key == "text.code_slots:20.pin"
    assert context.capability_entity_ids == {
        key: resolution.entity_id for key, resolution in lifecycle.current.items()
    }


async def test_verified_reset_requires_ready_same_slot_name_and_pin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unavailable same-slot name control prevents reset feedback."""
    lifecycle = SimpleNamespace(
        generation=7, current=_verified_resolutions(name_ready=False)
    )
    monkeypatch.setattr(
        state_handlers, "get_keymaster_lifecycle", lambda *_args: lifecycle
    )
    coordinator = MagicMock()
    coordinator.keymaster_runtime_enabled = True
    coordinator.config_entry.entry_id = "rc-entry"
    coordinator.event_overrides = MagicMock()
    coordinator.event_overrides.async_update = AsyncMock()
    event = MagicMock()
    event.data = {
        "entity_id": "button.unrelated_reset",
        "new_state": SimpleNamespace(state="pressed"),
    }
    deps = StateHandlerDeps(
        AsyncMock(),
        logging.getLogger(__name__),
        str,
        lambda value, _limit: value,
    )

    context = state_handlers._resolve_context(MagicMock(), event, coordinator, "", deps)
    assert context is not None

    handled = await state_handlers._handle_reset_entity(context, deps)

    assert handled
    coordinator.event_overrides.async_update.assert_not_awaited()


async def test_feedback_identity_is_captured_before_sleep_and_rechecked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same entity ID cannot be reinterpreted after a lifecycle rebind."""
    resolutions = _verified_resolutions()
    lifecycle = SimpleNamespace(generation=7, current=resolutions)
    monkeypatch.setattr(
        state_handlers, "get_keymaster_lifecycle", lambda *_args: lifecycle
    )
    coordinator = MagicMock()
    coordinator.keymaster_runtime_enabled = True
    coordinator.lockname = None
    coordinator.config_entry.entry_id = "rc-entry"
    coordinator.event_overrides = MagicMock()
    coordinator.event_overrides.overrides = {}
    coordinator.event_overrides.async_update = AsyncMock()
    coordinator.update_event_overrides = AsyncMock()
    hass = MagicMock()
    hass.data = {DOMAIN: {"rc-entry": {COORDINATOR: coordinator}}}
    config_entry = MagicMock(entry_id="rc-entry")
    event = MagicMock()
    event.data = {
        "entity_id": "text.area_secret",
        "new_state": SimpleNamespace(state="9876"),
    }

    async def rebind_during_settle(_delay: float) -> None:
        """Reuse the same entity address for a different selected identity."""
        lifecycle.generation = 8
        old = lifecycle.current["text.code_slots:20.pin"]
        identity = build_capability_identity(
            "different-keymaster-entry",
            "text.code_slots:20.pin",
            generation=8,
        )
        assert identity is not None
        lifecycle.current = {
            **lifecycle.current,
            "text.code_slots:20.pin": CapabilityResolution(
                identity,
                CapabilityStatus.READY,
                old.entity_id,
            ),
        }

    monkeypatch.setattr(util.asyncio, "sleep", rebind_during_settle)

    await util.handle_state_change(hass, config_entry, event)

    coordinator.update_event_overrides.assert_not_awaited()
    coordinator.event_overrides.async_update.assert_not_awaited()


async def test_concurrent_verified_feedback_preserves_each_changed_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Queued callbacks cannot overwrite a concurrent PIN, name, or expiry."""
    resolutions = _verified_resolutions()
    for property_key, entity_id in {
        "datetime.code_slots:20.accesslimit_date_range_start": "datetime.area_start",
        "datetime.code_slots:20.accesslimit_date_range_end": "datetime.area_end",
    }.items():
        identity = build_capability_identity(
            "keymaster-entry", property_key, generation=7
        )
        assert identity is not None
        resolutions[property_key] = CapabilityResolution(
            identity, CapabilityStatus.READY, entity_id
        )
    lifecycle = SimpleNamespace(generation=7, current=resolutions)
    monkeypatch.setattr(
        state_handlers, "get_keymaster_lifecycle", lambda *_args: lifecycle
    )
    start_time = datetime(2026, 11, 1, tzinfo=UTC)
    old_end_time = datetime(2026, 11, 10, tzinfo=UTC)
    new_end_time = datetime(2026, 11, 20, tzinfo=UTC)
    event_overrides = EventOverrides(20, 10)
    event_overrides.overrides[20] = event_overrides._override_dict(
        "old-pin", "Old guest", start_time, old_end_time
    )
    coordinator = MagicMock()
    coordinator.keymaster_runtime_enabled = True
    coordinator.lockname = None
    coordinator.trim_names = False
    coordinator.event_prefix = ""
    coordinator.config_entry.entry_id = "rc-entry"
    coordinator.event_overrides = event_overrides
    coordinator.async_request_refresh = AsyncMock()
    coordinator.update_event_overrides = (
        RentalControlCoordinator.update_event_overrides.__get__(
            coordinator, RentalControlCoordinator
        )
    )
    current_states = {
        "switch.area_enabled": SimpleNamespace(state="on"),
        "switch.area_dates": SimpleNamespace(state="on"),
        "text.area_secret": SimpleNamespace(state="new-pin"),
        "text.area_label": SimpleNamespace(state="New guest"),
        "datetime.area_start": SimpleNamespace(state=start_time.isoformat()),
        "datetime.area_end": SimpleNamespace(state=new_end_time.isoformat()),
    }
    hass = MagicMock()
    hass.data = {DOMAIN: {"rc-entry": {COORDINATOR: coordinator}}}
    hass.states.get = current_states.get
    config_entry = MagicMock(entry_id="rc-entry")

    waiting = 0
    all_waiting = asyncio.Event()

    async def hold_until_both_callbacks_are_queued(_delay: float) -> None:
        """Make all three callbacks capture the original override before dispatch."""
        nonlocal waiting
        waiting += 1
        if waiting == 3:
            all_waiting.set()
        await all_waiting.wait()

    monkeypatch.setattr(util.asyncio, "sleep", hold_until_both_callbacks_are_queued)

    pin_event = MagicMock()
    pin_event.data = {
        "entity_id": "text.area_secret",
        "new_state": SimpleNamespace(state="new-pin"),
    }
    name_event = MagicMock()
    name_event.data = {
        "entity_id": "text.area_label",
        "new_state": SimpleNamespace(state="New guest"),
    }
    end_event = MagicMock()
    end_event.data = {
        "entity_id": "datetime.area_end",
        "new_state": SimpleNamespace(state=new_end_time.isoformat()),
    }

    await asyncio.gather(
        util.handle_state_change(hass, config_entry, pin_event),
        util.handle_state_change(hass, config_entry, name_event),
        util.handle_state_change(hass, config_entry, end_event),
    )

    final_override = event_overrides.overrides[20]
    assert final_override is not None
    assert final_override["slot_code"] == "new-pin"
    assert final_override["slot_name"] == "New guest"
    assert final_override["end_time"] == new_end_time


async def test_queued_feedback_cannot_resurrect_override_after_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A queued old event is fenced by reset, while fresh feedback may reinitialize."""
    lifecycle = SimpleNamespace(generation=7, current=_verified_resolutions())
    monkeypatch.setattr(
        state_handlers, "get_keymaster_lifecycle", lambda *_args: lifecycle
    )
    start_time = datetime(2026, 11, 1, tzinfo=UTC)
    end_time = datetime(2026, 11, 10, tzinfo=UTC)
    event_overrides = EventOverrides(20, 10)
    event_overrides.overrides[20] = event_overrides._override_dict(
        "old-pin", "Old guest", start_time, end_time
    )
    coordinator = MagicMock()
    coordinator.keymaster_runtime_enabled = True
    coordinator.lockname = None
    coordinator.trim_names = False
    coordinator.event_prefix = ""
    coordinator.config_entry.entry_id = "rc-entry"
    coordinator.event_overrides = event_overrides
    coordinator.async_request_refresh = AsyncMock()
    coordinator.update_event_overrides = (
        RentalControlCoordinator.update_event_overrides.__get__(
            coordinator, RentalControlCoordinator
        )
    )
    current_states = {
        "button.unrelated_reset": SimpleNamespace(state="pressed"),
        "switch.area_enabled": SimpleNamespace(state="on"),
        "switch.area_dates": SimpleNamespace(state="off"),
        "text.area_secret": SimpleNamespace(state="old-pin"),
        "text.area_label": SimpleNamespace(state="Old guest"),
    }
    hass = MagicMock()
    hass.data = {DOMAIN: {"rc-entry": {COORDINATOR: coordinator}}}
    hass.states.get = current_states.get
    config_entry = MagicMock(entry_id="rc-entry")

    queued = asyncio.Event()
    release = asyncio.Event()
    sleep_calls = 0

    async def hold_first_callback(_delay: float) -> None:
        """Queue old text feedback, but allow the later reset callback through."""
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls == 1:
            queued.set()
            await release.wait()

    monkeypatch.setattr(util.asyncio, "sleep", hold_first_callback)
    stale_pin_event = MagicMock()
    stale_pin_event.data = {
        "entity_id": "text.area_secret",
        "new_state": SimpleNamespace(state="old-pin"),
    }
    reset_event = MagicMock()
    reset_event.data = {
        "entity_id": "button.unrelated_reset",
        "new_state": SimpleNamespace(state="pressed"),
    }

    stale_task = asyncio.create_task(
        util.handle_state_change(hass, config_entry, stale_pin_event)
    )
    await queued.wait()
    await util.handle_state_change(hass, config_entry, reset_event)
    assert event_overrides.overrides.get(20) is None
    release.set()
    await stale_task
    assert event_overrides.overrides.get(20) is None

    current_states["text.area_secret"] = SimpleNamespace(state="fresh-pin")
    fresh_pin_event = MagicMock()
    fresh_pin_event.data = {
        "entity_id": "text.area_secret",
        "new_state": SimpleNamespace(state="fresh-pin"),
    }
    await util.handle_state_change(hass, config_entry, fresh_pin_event)

    fresh_override = event_overrides.overrides[20]
    assert fresh_override is not None
    assert fresh_override["slot_code"] == "fresh-pin"
    assert fresh_override["slot_name"] == "Old guest"


async def test_out_of_order_same_field_feedback_keeps_current_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An older queued event cannot overwrite the live value of the same field."""
    lifecycle = SimpleNamespace(generation=7, current=_verified_resolutions())
    monkeypatch.setattr(
        state_handlers, "get_keymaster_lifecycle", lambda *_args: lifecycle
    )
    start_time = datetime(2026, 11, 1, tzinfo=UTC)
    end_time = datetime(2026, 11, 10, tzinfo=UTC)
    event_overrides = EventOverrides(20, 10)
    event_overrides.overrides[20] = event_overrides._override_dict(
        "old-pin", "Old guest", start_time, end_time
    )
    coordinator = MagicMock()
    coordinator.keymaster_runtime_enabled = True
    coordinator.lockname = None
    coordinator.trim_names = False
    coordinator.event_prefix = ""
    coordinator.config_entry.entry_id = "rc-entry"
    coordinator.event_overrides = event_overrides
    coordinator.async_request_refresh = AsyncMock()
    coordinator.update_event_overrides = (
        RentalControlCoordinator.update_event_overrides.__get__(
            coordinator, RentalControlCoordinator
        )
    )
    current_states = {
        "switch.area_enabled": SimpleNamespace(state="on"),
        "switch.area_dates": SimpleNamespace(state="off"),
        "text.area_secret": SimpleNamespace(state="new-pin"),
        "text.area_label": SimpleNamespace(state="Old guest"),
    }
    hass = MagicMock()
    hass.data = {DOMAIN: {"rc-entry": {COORDINATOR: coordinator}}}
    hass.states.get = current_states.get
    config_entry = MagicMock(entry_id="rc-entry")

    waiting = 0
    both_waiting = asyncio.Event()

    async def queue_both_events(_delay: float) -> None:
        """Ensure both callbacks capture the same initial override."""
        nonlocal waiting
        waiting += 1
        if waiting == 2:
            both_waiting.set()
        await both_waiting.wait()

    monkeypatch.setattr(util.asyncio, "sleep", queue_both_events)
    old_event = MagicMock()
    old_event.data = {
        "entity_id": "text.area_secret",
        "new_state": SimpleNamespace(state="older-pin"),
    }
    new_event = MagicMock()
    new_event.data = {
        "entity_id": "text.area_secret",
        "new_state": SimpleNamespace(state="new-pin"),
    }

    await asyncio.gather(
        util.handle_state_change(hass, config_entry, old_event),
        util.handle_state_change(hass, config_entry, new_event),
    )

    final_override = event_overrides.overrides[20]
    assert final_override is not None
    assert final_override["slot_code"] == "new-pin"


async def test_stale_enabled_event_does_not_initialize_missing_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An outdated enabled event cannot initialize from a missing-current slot."""
    lifecycle = SimpleNamespace(generation=7, current=_verified_resolutions())
    monkeypatch.setattr(
        state_handlers, "get_keymaster_lifecycle", lambda *_args: lifecycle
    )
    event_overrides = EventOverrides(20, 10)
    coordinator = MagicMock()
    coordinator.keymaster_runtime_enabled = True
    coordinator.lockname = None
    coordinator.trim_names = False
    coordinator.event_prefix = ""
    coordinator.config_entry.entry_id = "rc-entry"
    coordinator.event_overrides = event_overrides
    coordinator.async_request_refresh = AsyncMock()
    coordinator.update_event_overrides = (
        RentalControlCoordinator.update_event_overrides.__get__(
            coordinator, RentalControlCoordinator
        )
    )
    current_states = {
        "switch.area_enabled": SimpleNamespace(state="off"),
        "switch.area_dates": SimpleNamespace(state="off"),
        "text.area_secret": SimpleNamespace(state="fresh-pin"),
        "text.area_label": SimpleNamespace(state="Fresh guest"),
    }
    hass = MagicMock()
    hass.data = {DOMAIN: {"rc-entry": {COORDINATOR: coordinator}}}
    hass.states.get = current_states.get
    config_entry = MagicMock(entry_id="rc-entry")
    event = MagicMock()
    event.data = {
        "entity_id": "switch.area_enabled",
        "new_state": SimpleNamespace(state="on"),
    }

    async def skip_settle(_delay: float) -> None:
        """Run the callback immediately."""

    monkeypatch.setattr(util.asyncio, "sleep", skip_settle)
    await util.handle_state_change(hass, config_entry, event)

    assert event_overrides.overrides.get(20) is None


async def test_reconciliation_clear_fences_feedback_before_hardware_state_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A confirmed reconciliation clear fences callbacks while live state is stale."""
    monkeypatch.setattr(state_handlers, "get_keymaster_lifecycle", lambda *_args: None)
    start_time = datetime(2026, 11, 1, tzinfo=UTC)
    end_time = datetime(2026, 11, 10, tzinfo=UTC)
    event_overrides = EventOverrides(20, 10)
    event_overrides.overrides[20] = event_overrides._override_dict(
        "old-pin", "Old guest", start_time, end_time
    )
    coordinator = MagicMock()
    coordinator.keymaster_runtime_enabled = True
    coordinator.lockname = "front_door"
    coordinator.trim_names = False
    coordinator.config_entry.entry_id = "rc-entry"
    coordinator.event_overrides = event_overrides
    coordinator.async_request_refresh = AsyncMock()
    coordinator.update_event_overrides = (
        RentalControlCoordinator.update_event_overrides.__get__(
            coordinator, RentalControlCoordinator
        )
    )
    current_states = {
        "switch.front_door_code_slot_20_enabled": SimpleNamespace(state="on"),
        "switch.front_door_code_slot_20_use_date_range_limits": SimpleNamespace(
            state="on"
        ),
        "text.front_door_code_slot_20_pin": SimpleNamespace(state="old-pin"),
        "text.front_door_code_slot_20_name": SimpleNamespace(state="Old guest"),
        "datetime.front_door_code_slot_20_date_range_start": SimpleNamespace(
            state=start_time.isoformat()
        ),
        "datetime.front_door_code_slot_20_date_range_end": SimpleNamespace(
            state=end_time.isoformat()
        ),
    }
    hass = MagicMock()
    hass.data = {DOMAIN: {"rc-entry": {COORDINATOR: coordinator}}}
    hass.states.get = current_states.get
    coordinator.hass = hass
    config_entry = MagicMock(entry_id="rc-entry")

    queued = asyncio.Event()
    release = asyncio.Event()

    async def hold_feedback(_delay: float) -> None:
        """Hold feedback captured before reconciliation commits its clear."""
        queued.set()
        await release.wait()

    monkeypatch.setattr(util.asyncio, "sleep", hold_feedback)
    stale_event = MagicMock()
    stale_event.data = {"entity_id": "text.front_door_code_slot_20_pin"}
    stale_task = asyncio.create_task(
        util.handle_state_change(hass, config_entry, stale_event)
    )
    await queued.wait()

    confirmed_clear = util.OperationResult(kind="clear", slot=20, confirmed=True)
    monkeypatch.setattr(
        event_overrides_module,
        "async_fire_clear_code",
        AsyncMock(return_value=confirmed_clear),
    )
    try:
        await event_overrides._apply_clear(coordinator, 20)
        assert event_overrides.overrides.get(20) is None
        assert current_states["text.front_door_code_slot_20_pin"].state == "old-pin"
        assert current_states["text.front_door_code_slot_20_name"].state == "Old guest"
    finally:
        release.set()
    await stale_task

    assert event_overrides.overrides.get(20) is None


async def test_reservation_supersession_fences_queued_feedback_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Queued full-state feedback cannot revert a newer reserved date window."""
    monkeypatch.setattr(state_handlers, "get_keymaster_lifecycle", lambda *_args: None)
    old_start = datetime(2026, 11, 1, tzinfo=UTC)
    old_end = datetime(2026, 11, 10, tzinfo=UTC)
    new_start = datetime(2026, 11, 2, tzinfo=UTC)
    new_end = datetime(2026, 11, 12, tzinfo=UTC)
    event_overrides = EventOverrides(20, 10)
    event_overrides.overrides[20] = event_overrides._override_dict(
        "old-pin", "Old guest", old_start, old_end
    )
    event_overrides._slot_uids[20] = "reservation-uid"
    coordinator = MagicMock()
    coordinator.keymaster_runtime_enabled = True
    coordinator.lockname = "front_door"
    coordinator.trim_names = False
    coordinator.config_entry.entry_id = "rc-entry"
    coordinator.event_overrides = event_overrides
    coordinator.async_request_refresh = AsyncMock()
    coordinator.update_event_overrides = (
        RentalControlCoordinator.update_event_overrides.__get__(
            coordinator, RentalControlCoordinator
        )
    )
    current_states = {
        "switch.front_door_code_slot_20_enabled": SimpleNamespace(state="on"),
        "switch.front_door_code_slot_20_use_date_range_limits": SimpleNamespace(
            state="on"
        ),
        "text.front_door_code_slot_20_pin": SimpleNamespace(state="old-pin"),
        "text.front_door_code_slot_20_name": SimpleNamespace(state="Old guest"),
        "datetime.front_door_code_slot_20_date_range_start": SimpleNamespace(
            state=old_start.isoformat()
        ),
        "datetime.front_door_code_slot_20_date_range_end": SimpleNamespace(
            state=old_end.isoformat()
        ),
    }
    hass = MagicMock()
    hass.data = {DOMAIN: {"rc-entry": {COORDINATOR: coordinator}}}
    hass.states.get = current_states.get
    config_entry = MagicMock(entry_id="rc-entry")

    queued = asyncio.Event()
    release = asyncio.Event()

    async def hold_feedback(_delay: float) -> None:
        """Queue a no-new-state callback with a pre-reservation snapshot."""
        queued.set()
        await release.wait()

    monkeypatch.setattr(util.asyncio, "sleep", hold_feedback)
    stale_event = MagicMock()
    stale_event.data = {"entity_id": "text.front_door_code_slot_20_pin"}
    stale_task = asyncio.create_task(
        util.handle_state_change(hass, config_entry, stale_event)
    )
    await queued.wait()

    try:
        reserved = await event_overrides.async_reserve_or_get_slot(
            "Old guest",
            "old-pin",
            new_start,
            new_end,
            uid="reservation-uid",
        )
        assert reserved.slot == 20
        assert reserved.times_updated
    finally:
        release.set()
    await stale_task

    final_override = event_overrides.overrides[20]
    assert final_override is not None
    assert final_override["start_time"] == new_start
    assert final_override["end_time"] == new_end
