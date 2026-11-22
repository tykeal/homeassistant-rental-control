# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Measure registry resolution and protected I/O without policy thresholds."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC
from datetime import datetime
import secrets
import threading
from time import perf_counter
from unittest.mock import patch

from aioresponses import aioresponses
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import homeassistant.util.dt as dt_util
import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.rental_control.allocator import registry_commit
from custom_components.rental_control.allocator.allocator import DoorCodeAllocator
from custom_components.rental_control.allocator.models import AllocationRequest
from custom_components.rental_control.allocator.models import ProtectionLoadState
from custom_components.rental_control.allocator.registry_safety_models import (
    PendingSlotFence,
)
from custom_components.rental_control.allocator.registry_safety_models import (
    resolve_fence_to_tombstone,
)
from custom_components.rental_control.allocator.registry_safety_models import (
    tombstone_pressure,
)
from custom_components.rental_control.const import ALLOCATOR
from custom_components.rental_control.const import COORDINATOR
from custom_components.rental_control.const import DOMAIN
from custom_components.rental_control.const import PROTECTED_REGISTRY_STORE
from custom_components.rental_control.const import STORE_CODE_REGISTRY_KEY
from custom_components.rental_control.keymaster_identity import CapabilityRegistryIndex
from custom_components.rental_control.keymaster_identity import resolve_capability
from custom_components.rental_control.keymaster_lifecycle import get_keymaster_lifecycle
from custom_components.rental_control.keymaster_lifecycle import (
    managed_slot_property_keys,
)
from custom_components.rental_control.keymaster_lifecycle import (
    stop_keymaster_lifecycle,
)

from tests.fixtures import calendar_data
from tests.integration.helpers import FROZEN_START_OF_DAY
from tests.integration.helpers import FROZEN_TIME
from tests.integration.test_verified_consumer_dispatch import _setup_verified_consumer

_RELEASES = ("v0.6.1", "v0.6.5")
_RC_ENTRY_ID = "consumer-rc"
_KM_ENTRY_ID = "consumer-km"
_SLOT = 21


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize("release", _RELEASES)
async def test_indexed_lookup_and_coalesced_registry_storm(
    hass: HomeAssistant,
    tmp_path,
    release: str,
    monkeypatch: pytest.MonkeyPatch,
    record_property,
) -> None:
    """Resolve release-backed properties through one index after a storm."""
    coordinator, _store, _entity_ids = await _setup_verified_consumer(
        hass, tmp_path / release / ".storage", release
    )
    lifecycle = get_keymaster_lifecycle(hass, _RC_ENTRY_ID)
    assert lifecycle is not None
    registry = er.async_get(hass)
    km_entry = hass.config_entries.async_get_entry(_KM_ENTRY_ID)
    assert km_entry is not None
    for number in range(500):
        registry.async_get_or_create(
            "sensor",
            "keymaster",
            f"unrelated-performance-entry-{number}",
            suggested_object_id=f"unrelated_performance_{number}",
            config_entry=km_entry,
        )
    await hass.async_block_till_done()

    original_build = CapabilityRegistryIndex.build
    build_count = 0

    def counted_build(
        cls: type[CapabilityRegistryIndex], current_hass: HomeAssistant
    ) -> CapabilityRegistryIndex:
        """Count whole-registry scans during one burst of invalidations."""
        nonlocal build_count
        build_count += 1
        del cls
        return original_build(current_hass)

    monkeypatch.setattr(
        CapabilityRegistryIndex,
        "build",
        classmethod(counted_build),
    )
    for _ in range(100):
        lifecycle.request_refresh()
    await hass.async_block_till_done()
    assert build_count == 1

    index = CapabilityRegistryIndex.build(hass)
    properties = managed_slot_property_keys(_SLOT, 1)
    started = perf_counter()
    resolutions = [
        resolve_capability(
            hass,
            _KM_ENTRY_ID,
            property_key,
            generation=lifecycle.generation,
            registry_index=index,
        )
        for property_key in properties
    ]
    elapsed = perf_counter() - started
    assert all(resolution.ready for resolution in resolutions)
    assert len(index.by_unique_id) >= 500
    # Record actual timing for CI comparison; correctness is not tied to an
    # invented machine-dependent wall-clock threshold.
    record_property("indexed_resolution_seconds", elapsed)
    stop_keymaster_lifecycle(hass, _RC_ENTRY_ID)
    assert coordinator.config_entry.entry_id == _RC_ENTRY_ID


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize("release", _RELEASES)
async def test_slow_fsync_keeps_event_loop_responsive(
    hass: HomeAssistant,
    tmp_path,
    release: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A blocked atomic writer runs off-loop and retains the shared lock."""
    _coordinator, store, _entity_ids = await _setup_verified_consumer(
        hass, tmp_path / release / ".storage", release
    )
    loaded = store.loaded_result
    assert loaded is not None and loaded.data is not None
    assert loaded.generation is not None
    target = {
        "version": 2,
        "minor_version": 1,
        "key": STORE_CODE_REGISTRY_KEY,
        "data": deepcopy(loaded.data),
    }
    target["data"]["transaction_id"] = secrets.token_hex(16)
    target["data"]["generation"] = loaded.generation + 1
    original_write = registry_commit._atomic_write_acknowledged
    writer_started = threading.Event()
    release_writer = threading.Event()
    first_write = True

    def blocked_write(path, data: bytes, maximum: int) -> bool:
        """Hold the actual writer thread until the test observes loop ticks."""
        nonlocal first_write
        if first_write:
            first_write = False
            writer_started.set()
            if not release_writer.wait(timeout=10):
                return False
        return original_write(path, data, maximum)

    monkeypatch.setattr(registry_commit, "_atomic_write_acknowledged", blocked_write)
    task = asyncio.create_task(store.async_commit(target))
    heartbeat = 0
    try:
        deadline = asyncio.get_running_loop().time() + 5
        while (
            not writer_started.is_set() and asyncio.get_running_loop().time() < deadline
        ):
            heartbeat += 1
            await asyncio.sleep(0.001)
        assert writer_started.is_set()
        for _ in range(10):
            heartbeat += 1
            await asyncio.sleep(0.002)
        assert heartbeat >= 10
    finally:
        release_writer.set()
    result = await task
    assert result.state is ProtectionLoadState.VALID_V2


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize("release", _RELEASES)
async def test_concurrent_protected_entries_do_not_lose_acknowledged_state(
    hass: HomeAssistant,
    tmp_path,
    release: str,
) -> None:
    """Concurrent independent entry setup retains the shared v2 ledger."""
    _coordinator, store, _entity_ids = await _setup_verified_consumer(
        hass, tmp_path / release / ".storage", release
    )
    allocator = hass.data[DOMAIN][ALLOCATOR]
    assert isinstance(allocator, DoorCodeAllocator)
    # These are independent lockless reservations competing for the shared
    # allocator. Their commits must serialize around the current acknowledged
    # ledger, never publish both from a stale common snapshot.
    allocator._pending_adoption.clear()
    requests = (
        AllocationRequest(
            entry_id="performance-entry-a",
            identity_key="performance-reservation-a",
            preferred_code="3841",
            code_length=4,
        ),
        AllocationRequest(
            entry_id="performance-entry-b",
            identity_key="performance-reservation-b",
            preferred_code="5927",
            code_length=4,
        ),
    )
    results = await asyncio.gather(*(allocator.async_allocate(req) for req in requests))
    for index, result in enumerate(results):
        if result.code is None:
            results[index] = await allocator.async_allocate(requests[index])
    assert all(result.code is not None for result in results)
    assert results[0].code != results[1].code
    assert store.authorization_state.value == "ready"
    assert store.loaded_result is not None
    assert store.loaded_result.data is not None
    durable_owners = {
        owner["identity_key"]
        for record in store.loaded_result.data["ledger"]["records"]
        for owner in record["owners"]
    }
    assert {
        "performance-reservation-a",
        "performance-reservation-b",
    } <= durable_owners
    assert hass.data[DOMAIN][PROTECTED_REGISTRY_STORE] is store


def test_tombstone_pressure_scales_without_exposing_identity(
    record_property,
) -> None:
    """Measure retained tombstones while preserving every original proof row."""
    now = datetime.now(UTC)
    fences = [
        PendingSlotFence(
            fence_id=f"{index + 1:032x}",
            original_km_entry_id=f"km-{index}",
            slot=index + 1,
            rc_entry_id=f"rc-{index}",
            owner_ref=f"owner-{index}",
            allocation_ref=f"allocation-{index}",
            created_transaction_id="a" * 32,
            created_generation=1,
            fence_type="pending_clear",
        ).to_record()
        for index in range(500)
    ]
    records = [
        resolve_fence_to_tombstone(
            fence,
            tombstone_id=f"{index + 1001:032x}",
            resolved_transaction_id="b" * 32,
            resolved_generation=2,
            resolution_kind="clear_confirmed",
            retained_at=now.isoformat(),
        )
        for index, fence in enumerate(fences)
    ]
    started = perf_counter()
    pressure = tombstone_pressure(records, now=now)
    record_property("tombstone_pressure_seconds", perf_counter() - started)
    assert pressure.count == len(records)
    assert pressure.oldest_age_seconds == 0
    assert "owner-" not in repr(pressure)
    assert "km-" not in repr(pressure)


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_lockless_setup_refreshes_calendar_and_generates_codes_with_heartbeat(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Actual protected setup and code generation keep the loop schedulable."""
    mock_config_entry.add_to_hass(hass)
    with (
        aioresponses() as mocked,
        patch.object(dt_util, "now", return_value=FROZEN_TIME),
        patch.object(
            dt_util,
            "start_of_local_day",
            return_value=FROZEN_START_OF_DAY,
        ),
    ):
        mocked.get(
            mock_config_entry.data["url"],
            status=200,
            body=calendar_data.AIRBNB_ICS_CALENDAR,
            repeat=True,
        )
        setup = asyncio.create_task(
            hass.config_entries.async_setup(mock_config_entry.entry_id)
        )
        heartbeat = 0
        while not setup.done():
            heartbeat += 1
            await asyncio.sleep(0)
        assert await setup
    await hass.async_block_till_done()
    coordinator = hass.data[DOMAIN][mock_config_entry.entry_id][COORDINATOR]
    assert coordinator.last_update_success
    assert coordinator.data
    assert heartbeat > 0
