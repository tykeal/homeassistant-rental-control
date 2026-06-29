# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Focused tests for extracted Keymaster state handlers."""

from __future__ import annotations

from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest

from custom_components.rental_control import state_handlers
from custom_components.rental_control import util
from custom_components.rental_control.const import COORDINATOR
from custom_components.rental_control.const import DOMAIN


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
