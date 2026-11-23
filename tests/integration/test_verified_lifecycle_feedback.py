# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Exercise real HA feedback tracking across rename and address reuse."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

from homeassistant.helpers import entity_registry as er
import pytest

import custom_components.rental_control as integration
from custom_components.rental_control import util
from custom_components.rental_control.const import DOMAIN
from custom_components.rental_control.const import UNSUB_LISTENERS
from custom_components.rental_control.keymaster_feedback_listener import (
    async_start_listener,
)
from custom_components.rental_control.keymaster_lifecycle import get_keymaster_lifecycle
from custom_components.rental_control.keymaster_lifecycle import (
    start_keymaster_lifecycle,
)
from custom_components.rental_control.keymaster_lifecycle import (
    stop_keymaster_lifecycle,
)
from custom_components.rental_control.startup_readability import (
    reset_startup_readability_watcher,
)

from tests.integration.test_verified_consumer_dispatch import _CODE
from tests.integration.test_verified_consumer_dispatch import _SLOT
from tests.integration.test_verified_consumer_dispatch import _setup_verified_consumer


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_real_feedback_listener_tracks_renames_not_reused_addresses(
    hass,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only currently verified addresses reach override reconciliation."""
    coordinator, _store, entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage"
    )
    hass.data[DOMAIN][coordinator.config_entry.entry_id][UNSUB_LISTENERS] = []
    start_id = entity_ids[f"datetime.code_slots:{_SLOT}.accesslimit_date_range_start"]
    end_id = entity_ids[f"datetime.code_slots:{_SLOT}.accesslimit_date_range_end"]
    enabled_id = entity_ids[f"switch.code_slots:{_SLOT}.enabled"]
    dates_enabled_id = entity_ids[
        f"switch.code_slots:{_SLOT}.accesslimit_date_range_enabled"
    ]
    name_id = entity_ids[f"text.code_slots:{_SLOT}.name"]
    pin_id = entity_ids[f"text.code_slots:{_SLOT}.pin"]

    hass.states.async_set(enabled_id, "on")
    hass.states.async_set(dates_enabled_id, "off")
    hass.states.async_set(name_id, "Guest")
    hass.states.async_set(pin_id, _CODE)
    hass.states.async_set(start_id, "2026-11-25T00:00:00+00:00")
    hass.states.async_set(end_id, "2026-11-27T00:00:00+00:00")
    coordinator.update_event_overrides = AsyncMock()

    async def immediate_sleep(_delay: float) -> None:
        """Skip retry delays while verifying the rebound listener."""
        return None

    monkeypatch.setattr(util.asyncio, "sleep", immediate_sleep)
    monkeypatch.setattr(
        integration,
        "async_arm_startup_readability_refresh",
        lambda *_args, **_kwargs: None,
    )
    start_keymaster_lifecycle(
        hass,
        coordinator.config_entry,
        coordinator,
        on_resolved=integration._lifecycle_listener_callback(
            hass, coordinator.config_entry, coordinator
        ),
    )
    await hass.async_block_till_done()
    await async_start_listener(hass, coordinator.config_entry)

    hass.states.async_set(name_id, "Guest Updated")
    await hass.async_block_till_done()
    coordinator.update_event_overrides.assert_awaited_once()
    assert coordinator.update_event_overrides.await_args.args[:3] == (
        _SLOT,
        _CODE,
        "Guest Updated",
    )

    lifecycle = get_keymaster_lifecycle(hass, coordinator.config_entry.entry_id)
    assert lifecycle is not None
    old_generation = lifecycle.generation
    renamed_id = "text.manual_verified_feedback_rename"
    er.async_get(hass).async_update_entity(name_id, new_entity_id=renamed_id)
    hass.states.async_set(renamed_id, "Guest Updated")
    await hass.async_block_till_done()
    assert lifecycle.generation > old_generation
    coordinator.update_event_overrides.reset_mock()

    hass.states.async_set(renamed_id, "Guest Renamed")
    await hass.async_block_till_done()
    coordinator.update_event_overrides.assert_awaited_once()
    assert coordinator.update_event_overrides.await_args.args[2] == "Guest Renamed"

    # This fixture has no loaded Entity platform to remove the old HA state
    # automatically when its registry address changes.
    hass.states.async_remove(name_id)
    unrelated = er.async_get(hass).async_get_or_create(
        "text",
        "keymaster",
        "unrelated_keymaster_identity",
        suggested_object_id=name_id.split(".", maxsplit=1)[1],
        config_entry=hass.config_entries.async_get_entry("consumer-km"),
    )
    assert unrelated.entity_id == name_id
    hass.states.async_set(name_id, "Unrelated")
    unrelated_reset = er.async_get(hass).async_get_or_create(
        "button",
        "keymaster",
        "unrelated_reset_identity",
        suggested_object_id="code_slot_21_reset",
        config_entry=hass.config_entries.async_get_entry("consumer-km"),
    )
    hass.states.async_set(unrelated_reset.entity_id, "unknown")
    await hass.async_block_till_done()
    coordinator.update_event_overrides.assert_awaited_once()
    entry_id = coordinator.config_entry.entry_id
    reset_startup_readability_watcher(hass, entry_id)
    entry_data = hass.data[DOMAIN][entry_id]
    for unsubscribe in entry_data[UNSUB_LISTENERS]:
        unsubscribe()
    entry_data[UNSUB_LISTENERS].clear()
    stop_keymaster_lifecycle(hass, entry_id)


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_feedback_captured_before_sleep_is_discarded_after_rename(
    hass,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A queued old-address event cannot mutate overrides after identity changes."""
    coordinator, _store, entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage"
    )
    entry_id = coordinator.config_entry.entry_id
    hass.data[DOMAIN][entry_id][UNSUB_LISTENERS] = []
    coordinator.update_event_overrides = AsyncMock()
    name_id = entity_ids[f"text.code_slots:{_SLOT}.name"]

    async def blocked_sleep(_delay: float) -> None:
        """Pause feedback handling until the test changes lifecycle identity."""
        entered.set()
        await release.wait()

    entered = asyncio.Event()
    release = asyncio.Event()
    original_deps = util._state_deps()
    monkeypatch.setattr(
        util,
        "_state_deps",
        lambda: replace(original_deps, sleep=blocked_sleep),
    )
    await async_start_listener(hass, coordinator.config_entry)

    lifecycle = get_keymaster_lifecycle(hass, entry_id)
    assert lifecycle is not None
    old_generation = lifecycle.generation
    try:
        hass.states.async_set(name_id, "Stale feedback")
        await entered.wait()

        renamed_id = "text.renamed_while_feedback_waited"
        er.async_get(hass).async_update_entity(name_id, new_entity_id=renamed_id)
        hass.states.async_set(renamed_id, "Stale feedback")
        for _ in range(10):
            if lifecycle.generation > old_generation:
                break
            await asyncio.sleep(0)
        assert lifecycle.generation > old_generation
    finally:
        release.set()
        await hass.async_block_till_done()
        for unsubscribe in hass.data[DOMAIN][entry_id][UNSUB_LISTENERS]:
            unsubscribe()
        hass.data[DOMAIN][entry_id][UNSUB_LISTENERS].clear()
        stop_keymaster_lifecycle(hass, entry_id)

    coordinator.update_event_overrides.assert_not_awaited()
