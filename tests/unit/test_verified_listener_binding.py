# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for lifecycle-driven Keymaster feedback listener binding."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest

import custom_components.rental_control as integration
from custom_components.rental_control import keymaster_feedback_listener
from custom_components.rental_control.const import COORDINATOR
from custom_components.rental_control.const import DOMAIN
from custom_components.rental_control.const import UNSUB_LISTENERS
from custom_components.rental_control.keymaster_identity import CapabilityResolution
from custom_components.rental_control.keymaster_identity import CapabilityStatus
from custom_components.rental_control.keymaster_identity import (
    build_capability_identity,
)


@pytest.mark.asyncio
async def test_listener_rebinds_exact_current_ids_and_unsubscribes_old(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lifecycle refresh replaces one tracker without adding slug IDs."""
    hass = MagicMock()
    entry = MagicMock(entry_id="rc-entry", unique_id="public-id")
    coordinator = SimpleNamespace(
        lockname=None,
        start_slot=20,
        max_events=4,
    )
    entry_data = {COORDINATOR: coordinator, UNSUB_LISTENERS: []}
    listeners = cast(list[object], entry_data[UNSUB_LISTENERS])
    hass.data = {DOMAIN: {entry.entry_id: entry_data}}
    lifecycle = SimpleNamespace(generation=7)
    lifecycle.current = {
        "text.code_slots:21.name": CapabilityResolution(
            build_capability_identity(
                "km-entry", "text.code_slots:21.name", generation=7
            ),
            CapabilityStatus.READY,
            "text.area_label",
        ),
        "text.code_slots:23.pin": CapabilityResolution(
            build_capability_identity(
                "km-entry", "text.code_slots:23.pin", generation=7
            ),
            CapabilityStatus.READY,
            "text.custom_pin",
        ),
    }
    monkeypatch.setattr(
        keymaster_feedback_listener,
        "get_keymaster_lifecycle",
        lambda *_args: lifecycle,
    )
    monkeypatch.setattr(
        keymaster_feedback_listener.CapabilityRegistryIndex,
        "build",
        lambda _hass: object(),
    )
    monkeypatch.setattr(
        keymaster_feedback_listener,
        "resolve_capability_identity",
        lambda _hass, identity, registry_index: next(
            resolution
            for resolution in lifecycle.current.values()
            if resolution.identity is not None
            and resolution.identity.property_key == identity.property_key
        ),
    )
    tracked: list[tuple[list[str], object]] = []
    unsubscribed: list[int] = []

    def track(_hass, entity_ids, _callback):
        """Capture the exact tracker input and return an observable unsubscribe."""
        index = len(tracked)
        tracked.append((list(entity_ids), _callback))
        return lambda: unsubscribed.append(index)

    monkeypatch.setattr(
        keymaster_feedback_listener, "async_track_state_change_event", track
    )

    await integration.async_start_listener(hass, entry)

    assert tracked[0][0] == ["text.area_label", "text.custom_pin"]
    assert len(listeners) == 1

    lifecycle.current = {
        "text.code_slots:21.name": CapabilityResolution(
            build_capability_identity(
                "km-entry", "text.code_slots:21.name", generation=7
            ),
            CapabilityStatus.READY,
            "text.renamed_label",
        )
    }
    await integration.async_start_listener(hass, entry)

    assert unsubscribed == [0]
    assert tracked[1][0] == ["text.renamed_label"]
    assert len(listeners) == 1

    lifecycle.current = {}
    await integration.async_start_listener(hass, entry)

    assert unsubscribed == [0, 1]
    assert len(tracked) == 2
    assert listeners == []


async def test_lifecycle_callback_rebinds_only_its_current_generation(
    hass, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Late rebind tasks cannot act after lifecycle generation advances."""
    entry = MagicMock(entry_id="rc-entry", unique_id="public-id")
    entry.async_create_task = lambda _hass, coro, *, name: hass.async_create_task(
        coro, name=name
    )
    coordinator = MagicMock()
    lifecycle = SimpleNamespace(generation=8)
    entry_data = {COORDINATOR: coordinator}
    hass.data[DOMAIN] = {entry.entry_id: entry_data}
    start_listener = AsyncMock()
    listener_started = asyncio.Event()
    continue_listener = asyncio.Event()

    async def blocked_start_listener(*_args, **_kwargs):
        """Hold the rebind so coordinator replacement races its completion."""
        listener_started.set()
        await continue_listener.wait()

    start_listener.side_effect = blocked_start_listener
    arm_readability = MagicMock()
    monkeypatch.setattr(
        keymaster_feedback_listener,
        "get_keymaster_lifecycle",
        lambda *_args: lifecycle,
    )
    monkeypatch.setattr(
        keymaster_feedback_listener, "async_start_listener", start_listener
    )
    monkeypatch.setattr(
        keymaster_feedback_listener,
        "async_arm_startup_readability_refresh",
        arm_readability,
    )
    on_resolved = integration._lifecycle_listener_callback(hass, entry, coordinator)

    on_resolved(7, {})
    await hass.async_block_till_done()
    start_listener.assert_not_awaited()

    on_resolved(8, {})
    await listener_started.wait()
    entry_data[COORDINATOR] = MagicMock()
    continue_listener.set()
    await hass.async_block_till_done()
    start_listener.assert_awaited_once()
    arm_readability.assert_not_called()
