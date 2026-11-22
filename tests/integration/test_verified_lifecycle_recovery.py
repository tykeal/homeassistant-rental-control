# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Verify feedback recovery when a queued entity address is reused."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

from homeassistant.helpers import entity_registry as er
import pytest

from custom_components.rental_control import util
from custom_components.rental_control.const import DOMAIN
from custom_components.rental_control.const import UNSUB_LISTENERS
from custom_components.rental_control.keymaster_feedback_listener import (
    async_start_listener,
)
from custom_components.rental_control.keymaster_lifecycle import get_keymaster_lifecycle
from custom_components.rental_control.keymaster_lifecycle import (
    stop_keymaster_lifecycle,
)

from tests.integration.test_verified_consumer_dispatch import _SLOT
from tests.integration.test_verified_consumer_dispatch import _setup_verified_consumer


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_queued_feedback_is_discarded_when_entity_id_is_reused(
    hass,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A different registry identity at the old address cannot receive feedback."""
    coordinator, _store, entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage"
    )
    entry_id = coordinator.config_entry.entry_id
    entry_data = hass.data[DOMAIN][entry_id]
    entry_data[UNSUB_LISTENERS] = []
    coordinator.update_event_overrides = AsyncMock()
    name_id = entity_ids[f"text.code_slots:{_SLOT}.name"]

    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocked_sleep(_delay: float) -> None:
        """Hold the queued callback while the entity registry address changes."""
        entered.set()
        await release.wait()

    original_deps = util._state_deps()
    monkeypatch.setattr(
        util,
        "_state_deps",
        lambda: replace(original_deps, sleep=blocked_sleep),
    )
    await async_start_listener(hass, coordinator.config_entry)
    lifecycle = get_keymaster_lifecycle(hass, entry_id)
    assert lifecycle is not None
    original_generation = lifecycle.generation

    try:
        hass.states.async_set(name_id, "Stale feedback")
        await entered.wait()

        registry = er.async_get(hass)
        hass.states.async_remove(name_id)
        registry.async_remove(name_id)
        replacement = registry.async_get_or_create(
            "text",
            "keymaster",
            "unrelated_keymaster_identity",
            suggested_object_id=name_id.split(".", maxsplit=1)[1],
            config_entry=coordinator.config_entry,
        )
        assert replacement.entity_id == name_id
        hass.states.async_set(name_id, "Unrelated")
        for _ in range(10):
            if lifecycle.generation > original_generation:
                break
            await asyncio.sleep(0)
        assert lifecycle.generation > original_generation
    finally:
        release.set()
        await hass.async_block_till_done()
        for unsubscribe in entry_data[UNSUB_LISTENERS]:
            unsubscribe()
        entry_data[UNSUB_LISTENERS].clear()
        stop_keymaster_lifecycle(hass, entry_id)

    coordinator.update_event_overrides.assert_not_awaited()
