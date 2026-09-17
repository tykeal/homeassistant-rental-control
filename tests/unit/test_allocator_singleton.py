# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for allocator singleton lifecycle integration."""

from __future__ import annotations

import asyncio
import functools
from types import SimpleNamespace
from typing import Any

from homeassistant.exceptions import ConfigEntryNotReady
import pytest

import custom_components.rental_control as integration
from custom_components.rental_control import async_remove_entry
from custom_components.rental_control import async_setup_entry
from custom_components.rental_control import async_unload_entry
from custom_components.rental_control.allocator import singleton
from custom_components.rental_control.const import ALLOCATOR
from custom_components.rental_control.const import CONF_GENERATE
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
        self.unload_calls = 0
        self.test_unsubscribed: list[str] = []

    async def _async_unload_platforms(self, *_args: Any, **_kwargs: Any) -> bool:
        """Pretend platform unload succeeds."""
        self.unload_calls += 1
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


async def test_setup_failure_cleans_partial_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A later setup failure unloads platforms and drains listeners."""
    hass = FakeHass()
    allocator = FakeAllocator(hass)
    unsubscribed: list[str] = []
    hass.test_unsubscribed = unsubscribed

    monkeypatch.setattr(
        integration,
        "async_get_or_create_allocator",
        lambda _hass: _async_return(allocator),
    )
    monkeypatch.setattr(integration, "RentalControlCoordinator", LateFailCoordinator)
    monkeypatch.setattr(
        integration, "_needs_startup_readability_refresh", lambda *_args: (False, set())
    )
    monkeypatch.setattr(
        integration, "async_arm_startup_readability_refresh", _append_startup_unsub
    )
    monkeypatch.setattr(integration, "async_start_listener", _append_listener_unsub)
    hass.config_entries.async_forward_entry_setups = _async_fail_forward

    with pytest.raises(ConfigEntryNotReady):
        await async_setup_entry(
            hass,
            SimpleNamespace(
                entry_id="entry-late",
                data={"name": "Entry Late"},
                add_update_listener=lambda *_args: None,
            ),
        )

    assert hass.unload_calls == 1
    assert unsubscribed == ["startup", "listener"]
    assert "entry-late" not in allocator.pending
    assert "entry-late" not in hass.data[DOMAIN]


async def test_setup_cancellation_cleans_partial_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setup cancellation uses the same cleanup path as setup failure."""
    hass = FakeHass()
    allocator = FakeAllocator(hass)
    hass.test_unsubscribed = []

    monkeypatch.setattr(
        integration,
        "async_get_or_create_allocator",
        lambda _hass: _async_return(allocator),
    )
    monkeypatch.setattr(integration, "RentalControlCoordinator", LateFailCoordinator)
    monkeypatch.setattr(
        integration, "_needs_startup_readability_refresh", lambda *_args: (False, set())
    )
    monkeypatch.setattr(
        integration, "async_arm_startup_readability_refresh", _append_startup_unsub
    )
    monkeypatch.setattr(integration, "async_start_listener", _async_cancel_listener)

    with pytest.raises(asyncio.CancelledError):
        await async_setup_entry(
            hass,
            SimpleNamespace(
                entry_id="entry-cancel",
                data={"name": "Entry Cancel"},
                add_update_listener=lambda *_args: None,
            ),
        )

    assert hass.unload_calls == 1
    assert hass.test_unsubscribed == ["startup", "listener"]
    assert "entry-cancel" not in allocator.pending
    assert "entry-cancel" not in hass.data[DOMAIN]


async def test_remove_entry_skips_missing_allocator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Removing an uninitialized entry does not create the allocator."""
    hass = FakeHass()
    monkeypatch.setattr(
        integration,
        "async_get_or_create_allocator",
        _raise_delete,
    )

    await async_remove_entry(
        hass,
        SimpleNamespace(entry_id="entry-missing", data={"name": "Entry Missing"}),
    )

    assert hass.data == {}
    assert FakeAllocator.load_count == 0


async def test_setup_failure_keeps_data_when_unload_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setup cleanup preserves entry data if platform teardown fails."""
    hass = FakeHass()
    allocator = FakeAllocator(hass)
    hass.test_unsubscribed = []

    monkeypatch.setattr(
        integration,
        "async_get_or_create_allocator",
        lambda _hass: _async_return(allocator),
    )
    monkeypatch.setattr(integration, "RentalControlCoordinator", LateFailCoordinator)
    monkeypatch.setattr(
        integration, "_needs_startup_readability_refresh", lambda *_args: (False, set())
    )
    monkeypatch.setattr(
        integration, "async_arm_startup_readability_refresh", _append_startup_unsub
    )
    monkeypatch.setattr(integration, "async_start_listener", _append_listener_unsub)
    hass.config_entries.async_forward_entry_setups = _async_fail_forward
    hass.config_entries.async_unload_platforms = _async_unload_false

    with pytest.raises(ConfigEntryNotReady):
        await async_setup_entry(
            hass,
            SimpleNamespace(
                entry_id="entry-stuck",
                data={"name": "Entry Stuck"},
                add_update_listener=lambda *_args: None,
            ),
        )

    assert hass.test_unsubscribed == []
    assert "entry-stuck" not in allocator.pending
    assert hass.data[DOMAIN]["entry-stuck"][UNSUB_LISTENERS]


async def test_setup_failure_removes_update_listener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setup cleanup removes config-entry update callbacks."""
    hass = FakeHass()
    allocator = FakeAllocator(hass)
    listener_events: list[str] = []

    monkeypatch.setattr(
        integration,
        "async_get_or_create_allocator",
        lambda _hass: _async_return(allocator),
    )
    monkeypatch.setattr(integration, "RentalControlCoordinator", LateFailCoordinator)
    monkeypatch.setattr(
        integration, "_needs_startup_readability_refresh", lambda *_args: (False, set())
    )
    monkeypatch.setattr(integration, "async_arm_startup_readability_refresh", _noop)
    monkeypatch.setattr(integration, "async_start_listener", _async_noop)
    monkeypatch.setattr(integration, "async_register_keymaster_listener", _noop)
    monkeypatch.setattr(integration, "delete_rc_and_base_folder", _raise_delete)
    hass.config_entries.async_forward_entry_setups = _async_noop

    with pytest.raises(ConfigEntryNotReady):
        await async_setup_entry(
            hass,
            SimpleNamespace(
                entry_id="entry-update",
                data={"name": "Entry Update", CONF_GENERATE: True},
                add_update_listener=lambda *_args: _record_listener(listener_events),
            ),
        )

    assert listener_events == ["registered", "removed"]
    assert "entry-update" not in allocator.pending
    assert "entry-update" not in hass.data[DOMAIN]


class FailingCoordinator:
    """Coordinator test double that fails after allocator registration."""

    event_overrides = None
    lockname = "front"

    def __init__(self, **_kwargs: Any) -> None:
        """Accept the production constructor shape."""

    async def async_load_slot_store(self) -> None:
        """Fail setup at the first await after allocator registration."""
        raise ConfigEntryNotReady("slot store unavailable")


class LateFailCoordinator:
    """Coordinator test double that reaches platform setup."""

    event_overrides = None
    lockname = "front"
    _checkin_restore_pending = False

    def __init__(self, **_kwargs: Any) -> None:
        """Accept the production constructor shape."""

    async def async_load_slot_store(self) -> None:
        """Pretend slot storage loaded."""

    def get_persisted_slot_mappings(self) -> None:
        """Return no persisted mappings."""
        return None

    async def async_setup_keymaster_overrides(self) -> None:
        """Pretend Keymaster override setup succeeded."""

    async def async_config_entry_first_refresh(self) -> None:
        """Pretend first refresh succeeded."""


async def _async_noop(*_args: Any, **_kwargs: Any) -> None:
    """Async no-op helper for monkeypatches."""


async def _async_return(value: Any) -> Any:
    """Return a value from an awaitable helper."""
    return value


def _noop(*_args: Any, **_kwargs: Any) -> None:
    """Synchronous no-op helper for monkeypatches."""


def _raise_delete(*_args: Any, **_kwargs: Any) -> None:
    """Raise during generated package cleanup for setup tests."""
    raise ConfigEntryNotReady("delete failed")


def _record_listener(events: list[str]) -> Any:
    """Record update-listener registration and return its remover."""
    events.append("registered")
    return functools.partial(_record_listener_removed, events)


def _record_listener_removed(events: list[str]) -> None:
    """Record update-listener removal."""
    events.append("removed")


async def _async_fail_forward(*_args: Any, **_kwargs: Any) -> None:
    """Fail platform setup for setup-cleanup tests."""
    raise ConfigEntryNotReady("platform unavailable")


async def _async_unload_false(*_args: Any, **_kwargs: Any) -> bool:
    """Report an unsuccessful platform unload for cleanup tests."""
    return False


def _append_startup_unsub(
    hass: FakeHass, entry: Any, _coordinator: Any, **_kwargs: Any
) -> None:
    """Append a startup unsubscriber for setup-cleanup tests."""
    hass.data[DOMAIN][entry.entry_id][UNSUB_LISTENERS].append(
        lambda: hass.test_unsubscribed.append("startup")
    )


async def _append_listener_unsub(hass: FakeHass, entry: Any) -> None:
    """Append a listener unsubscriber for setup-cleanup tests."""
    hass.data[DOMAIN][entry.entry_id][UNSUB_LISTENERS].append(
        lambda: hass.test_unsubscribed.append("listener")
    )


async def _async_cancel_listener(hass: FakeHass, entry: Any) -> None:
    """Append a listener unsubscriber and cancel setup."""
    await _append_listener_unsub(hass, entry)
    raise asyncio.CancelledError
