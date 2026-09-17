# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for allocator singleton lifecycle integration."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from homeassistant.exceptions import ConfigEntryNotReady
import pytest

import custom_components.rental_control as integration
from custom_components.rental_control import async_setup_entry
from custom_components.rental_control import async_unload_entry
from custom_components.rental_control.allocator import singleton
from custom_components.rental_control.const import ALLOCATOR
from custom_components.rental_control.const import COORDINATOR
from custom_components.rental_control.const import DOMAIN
from custom_components.rental_control.const import UNSUB_LISTENERS


class FakeHass(SimpleNamespace):
    """Small Home Assistant stand-in for singleton tests."""

    def __init__(self) -> None:
        """Initialize fake hass state and config-entry helpers."""
        super().__init__()
        self.data: dict[str, Any] = {}
        self.config_entries = SimpleNamespace(
            async_unload_platforms=self._async_unload_platforms,
            async_update_entry=lambda *args, **kwargs: None,
        )
        self.executor_jobs: list[tuple[Any, ...]] = []

    async def _async_unload_platforms(self, *_args: Any, **_kwargs: Any) -> bool:
        """Pretend platform unload succeeds."""
        return True

    async def async_add_executor_job(self, *args: Any) -> None:
        """Record executor jobs without executing destructive helpers."""
        self.executor_jobs.append(args)


class FakeAllocator:
    """Allocator stand-in with visible lifecycle state."""

    fail_load = False
    load_count = 0

    def __init__(self, _hass: FakeHass) -> None:
        """Initialize fake allocator state."""
        self.pending: set[str] = set()
        self.registry_marker = {"kept": True}

    async def async_load(self) -> None:
        """Optionally fail allocator creation."""
        type(self).load_count += 1
        if type(self).fail_load:
            raise ConfigEntryNotReady("boom")

    async def async_register_entry(self, entry_id: str) -> None:
        """Add an entry to the pending set."""
        self.pending.add(entry_id)

    async def async_unregister_entry(self, entry_id: str) -> None:
        """Remove an entry from the pending set."""
        self.pending.discard(entry_id)

    @property
    def diagnostics(self) -> dict[str, Any]:
        """Expose pending adoption diagnostics."""
        return {"pending_adoption": sorted(self.pending)}


@pytest.fixture(autouse=True)
def patch_allocator(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use the fake allocator for singleton tests."""
    monkeypatch.setattr(singleton, "DoorCodeAllocator", FakeAllocator)
    FakeAllocator.fail_load = False
    FakeAllocator.load_count = 0


async def test_second_entry_reuses_first_allocator() -> None:
    """A second caller receives the already-loaded allocator."""
    hass = FakeHass()

    first = await singleton.async_get_or_create_allocator(hass)
    second = await singleton.async_get_or_create_allocator(hass)

    assert second is first
    assert FakeAllocator.load_count == 1


async def test_creation_failure_pops_allocator_key() -> None:
    """A failed allocator load removes the shared hass.data key."""
    hass = FakeHass()
    FakeAllocator.fail_load = True

    with pytest.raises(ConfigEntryNotReady):
        await singleton.async_get_or_create_allocator(hass)

    assert ALLOCATOR not in hass.data[DOMAIN]


async def test_unloading_one_entry_leaves_shared_allocator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unloading one entry leaves the singleton and other entry data intact."""
    hass = FakeHass()
    allocator = FakeAllocator(hass)
    hass.data[DOMAIN] = {
        ALLOCATOR: allocator,
        "entry-a": {UNSUB_LISTENERS: [lambda: None]},
        "entry-b": {COORDINATOR: object(), UNSUB_LISTENERS: []},
    }
    monkeypatch.setattr(integration, "async_create", lambda *args, **kwargs: None)
    monkeypatch.setattr(integration, "async_dismiss", lambda *args, **kwargs: None)
    monkeypatch.setattr(integration, "async_reload_package_platforms", _async_noop)
    monkeypatch.setattr(integration, "delete_rc_and_base_folder", lambda *args: None)

    result = await async_unload_entry(
        hass,
        SimpleNamespace(entry_id="entry-a", data={"name": "Entry A"}),
    )

    assert result is True
    assert hass.data[DOMAIN][ALLOCATOR] is allocator
    assert hass.data[DOMAIN]["entry-b"][COORDINATOR] is not None
    assert allocator.registry_marker == {"kept": True}


async def test_setup_failure_unregisters_pending_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An entry that fails setup is removed from the adoption gate."""
    hass = FakeHass()
    allocator = FakeAllocator(hass)
    monkeypatch.setattr(
        integration,
        "async_get_or_create_allocator",
        lambda _hass: _async_return(allocator),
    )
    monkeypatch.setattr(integration, "RentalControlCoordinator", FailingCoordinator)

    with pytest.raises(ConfigEntryNotReady):
        await async_setup_entry(
            hass,
            SimpleNamespace(
                entry_id="entry-fail",
                data={"name": "Entry Fail"},
            ),
        )

    assert "entry-fail" not in allocator.pending
    assert "entry-fail" not in hass.data[DOMAIN]


class FailingCoordinator:
    """Coordinator test double that fails after allocator registration."""

    event_overrides = None

    def __init__(self, **_kwargs: Any) -> None:
        """Accept the production constructor shape."""

    async def async_load_slot_store(self) -> None:
        """Fail setup at the first await after allocator registration."""
        raise ConfigEntryNotReady("slot store unavailable")


async def _async_noop(*_args: Any, **_kwargs: Any) -> None:
    """Async no-op helper for monkeypatches."""


async def _async_return(value: Any) -> Any:
    """Return a value from an awaitable helper."""
    return value
