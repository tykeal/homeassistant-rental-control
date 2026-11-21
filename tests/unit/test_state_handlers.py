# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Focused tests for extracted Keymaster state handlers."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest

from custom_components.rental_control import state_handlers
from custom_components.rental_control import util
from custom_components.rental_control.const import COORDINATOR
from custom_components.rental_control.const import DOMAIN
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
