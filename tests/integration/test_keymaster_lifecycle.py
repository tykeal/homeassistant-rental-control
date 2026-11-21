# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Exercise Keymaster identity invalidation against real HA events."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_registry import RegistryEntryDisabler
from pytest_homeassistant.common import MockConfigEntry

from custom_components.rental_control.allocator.associations import AssociationMode
from custom_components.rental_control.allocator.associations import AssociationTuple
from custom_components.rental_control.const import DOMAIN
from custom_components.rental_control.const import PROTECTED_REGISTRY_STORE
from custom_components.rental_control.keymaster_identity import CapabilityStatus
from custom_components.rental_control.keymaster_identity import (
    build_capability_identity,
)
from custom_components.rental_control.keymaster_lifecycle import (
    KeymasterEntityLifecycle,
)
from custom_components.rental_control.keymaster_lifecycle import (
    start_keymaster_lifecycle,
)
from custom_components.rental_control.keymaster_lifecycle import (
    stop_keymaster_lifecycle,
)

_NAME = "text.code_slots:21.name"
_PIN = "text.code_slots:21.pin"


def _entry(
    hass, entry_id: str = "km-lifecycle", *, data: dict[str, Any] | None = None
) -> MockConfigEntry:
    """Add a loaded Keymaster config entry through HA's state hook."""
    entry = MockConfigEntry(
        domain="keymaster",
        title="Lifecycle Lock",
        entry_id=entry_id,
        data=data or {},
    )
    entry.add_to_hass(hass)
    entry._async_set_state(hass, ConfigEntryState.LOADED, None)
    return entry


def _create_property(
    hass,
    entry: MockConfigEntry,
    property_key: str,
    *,
    suggested_object_id: str | None = None,
) -> str:
    """Create the exact Keymaster registry record and an optional state."""
    identity = build_capability_identity(entry.entry_id, property_key, generation=1)
    assert identity is not None
    registry_entry = er.async_get(hass).async_get_or_create(
        identity.domain,
        identity.platform,
        identity.unique_id,
        suggested_object_id=suggested_object_id,
        config_entry=entry,
    )
    hass.states.async_set(registry_entry.entity_id, "ready")
    return registry_entry.entity_id


def _lifecycle(hass, entry_id: str, updates: list[tuple[int, dict[str, Any]]]):
    """Construct a lifecycle manager with a recording consumer boundary."""
    return KeymasterEntityLifecycle(
        hass,
        entry_id,
        (_NAME, _PIN),
        lambda generation, resolutions: updates.append((generation, dict(resolutions))),
    )


async def test_listen_before_snapshot_closes_registration_race(hass) -> None:
    """A property registered immediately after subscription is not missed."""
    entry = _entry(hass)
    updates: list[tuple[int, dict[str, Any]]] = []
    lifecycle = _lifecycle(hass, entry.entry_id, updates)

    lifecycle.start()
    entity_id = _create_property(hass, entry, _NAME)
    await hass.async_block_till_done()

    assert lifecycle.current[_NAME].status is CapabilityStatus.READY
    assert lifecycle.current[_NAME].entity_id == entity_id
    assert updates
    assert updates[-1][1][_NAME].status is CapabilityStatus.READY

    lifecycle.stop()


async def test_snapshot_indexes_registry_once_for_all_properties(
    hass, monkeypatch
) -> None:
    """One snapshot performs a single registry pass for all slot properties."""
    entry = _entry(hass)
    _create_property(hass, entry, _NAME)
    _create_property(hass, entry, _PIN)
    registry = er.async_get(hass)

    class CountingEntities(dict):
        """Count values traversals on the registry snapshot."""

        values_calls = 0

        def values(self):
            """Count indexed registry scans for the test assertion."""
            self.values_calls += 1
            return super().values()

    entities = CountingEntities(registry.entities)
    indexed_registry = SimpleNamespace(
        entities=entities,
        async_get=registry.async_get,
    )
    monkeypatch.setattr(
        "custom_components.rental_control.keymaster_identity.er.async_get",
        lambda _hass: indexed_registry,
    )
    lifecycle = _lifecycle(hass, entry.entry_id, [])
    lifecycle.start()
    await hass.async_block_till_done()

    assert entities.values_calls == 1
    lifecycle.stop()


async def test_real_registry_rename_update_remove_and_id_reuse(hass) -> None:
    """Registry payloads invalidate and atomically replace stale bindings."""
    entry = _entry(hass)
    updates: list[tuple[int, dict[str, Any]]] = []
    lifecycle = _lifecycle(hass, entry.entry_id, updates)
    payloads: list[dict[str, Any]] = []

    @callback
    def capture_registry_event(event) -> None:
        """Capture actual HA registry event payloads synchronously."""
        payloads.append(dict(event.data))

    unsubscribe_capture = hass.bus.async_listen(
        er.EVENT_ENTITY_REGISTRY_UPDATED, capture_registry_event
    )
    entity_id = _create_property(hass, entry, _NAME)
    lifecycle.start()
    await hass.async_block_till_done()
    first_registry_id = lifecycle.current[_NAME].registry_entry_id
    assert lifecycle.current[_NAME].status is CapabilityStatus.READY
    assert first_registry_id is not None

    new_entity_id = "text.lifecycle_renamed"
    er.async_get(hass).async_update_entity(
        entity_id, new_entity_id=new_entity_id, name="Renamed"
    )
    hass.states.async_set(new_entity_id, "ready")
    await hass.async_block_till_done()
    assert lifecycle.current[_NAME].status is CapabilityStatus.READY
    assert lifecycle.current[_NAME].entity_id == new_entity_id
    rename_event = next(
        payload
        for payload in payloads
        if payload.get("action") == "update"
        and payload.get("entity_id") == new_entity_id
    )
    assert rename_event["old_entity_id"] == entity_id
    assert rename_event["changes"]["name"] is None

    registry = er.async_get(hass)
    registry.async_update_entity(new_entity_id, disabled_by=RegistryEntryDisabler.USER)
    await hass.async_block_till_done()
    assert lifecycle.current[_NAME].status is CapabilityStatus.DISABLED

    registry.async_update_entity(new_entity_id, disabled_by=None)
    await hass.async_block_till_done()
    assert lifecycle.current[_NAME].status is CapabilityStatus.READY

    registry.async_remove(new_entity_id)
    await hass.async_block_till_done()
    assert lifecycle.current[_NAME].status is CapabilityStatus.MISSING
    assert lifecycle.current[_NAME].entity_id is None

    reused_entity_id = _create_property(
        hass,
        entry,
        _NAME,
        suggested_object_id=new_entity_id.split(".", maxsplit=1)[1],
    )
    await hass.async_block_till_done()
    assert lifecycle.current[_NAME].status is CapabilityStatus.READY
    assert lifecycle.current[_NAME].entity_id == reused_entity_id
    assert first_registry_id == entry.entry_id
    assert lifecycle.current[_NAME].registry_entry_id == entry.entry_id
    assert any(
        payload.get("action") == "create" and payload.get("entity_id") == entity_id
        for payload in payloads
    )
    assert any(payload.get("action") == "remove" for payload in payloads)

    lifecycle.stop()
    unsubscribe_capture()


async def test_late_state_and_selected_entry_state_hooks(hass) -> None:
    """A late state and config-entry unload/reload each refresh evidence."""
    entry = _entry(hass)
    identity = build_capability_identity(entry.entry_id, _NAME, generation=1)
    assert identity is not None
    registry_entry = er.async_get(hass).async_get_or_create(
        identity.domain,
        identity.platform,
        identity.unique_id,
        config_entry=entry,
    )
    updates: list[tuple[int, dict[str, Any]]] = []
    lifecycle = _lifecycle(hass, entry.entry_id, updates)
    lifecycle.start()
    await hass.async_block_till_done()
    assert lifecycle.current[_NAME].status is CapabilityStatus.STATE_MISSING

    hass.states.async_set(registry_entry.entity_id, "ready")
    await hass.async_block_till_done()
    assert lifecycle.current[_NAME].status is CapabilityStatus.READY

    entry._async_set_state(hass, ConfigEntryState.NOT_LOADED, None)
    await hass.async_block_till_done()
    assert lifecycle.current[_NAME].status is CapabilityStatus.KEYMASTER_NOT_LOADED

    entry._async_set_state(hass, ConfigEntryState.LOADED, None)
    await hass.async_block_till_done()
    assert lifecycle.current[_NAME].status is CapabilityStatus.READY
    assert len({generation for generation, _ in updates}) >= 4
    lifecycle.stop()


async def test_coalesced_stale_updates_id_reuse_and_repeated_teardown(hass) -> None:
    """Queued callbacks publish only latest state, and teardown is idempotent."""
    entry = _entry(hass)
    updates: list[tuple[int, dict[str, Any]]] = []
    lifecycle = _lifecycle(hass, entry.entry_id, updates)
    lifecycle.start()

    first_id = _create_property(hass, entry, _NAME)
    er.async_get(hass).async_remove(first_id)
    replacement_id = _create_property(hass, entry, _NAME)
    await hass.async_block_till_done()

    assert lifecycle.current[_NAME].status is CapabilityStatus.READY
    assert lifecycle.current[_NAME].entity_id == replacement_id
    assert updates[-1][1][_NAME].entity_id == replacement_id

    update_count = len(updates)
    lifecycle.stop()
    lifecycle.stop()
    _create_property(hass, entry, _PIN)
    await hass.async_block_till_done()
    assert len(updates) == update_count
    assert lifecycle.current == {}


async def test_rc_setup_starts_only_explicit_verified_association(hass) -> None:
    """Setup creates passive lifecycle tracking only for a committed identity."""
    _entry(hass, "km-verified")
    rc_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Lifecycle RC",
        entry_id="rc-lifecycle",
        data={
            "verified_keymaster_entry_id": "km-verified",
            "association_generation": 4,
            "association_transaction_id": "f" * 32,
            "association_mode": AssociationMode.VERIFIED.value,
        },
    )
    hass.data[DOMAIN] = {
        rc_entry.entry_id: {},
        PROTECTED_REGISTRY_STORE: SimpleNamespace(
            association_for_entry=lambda entry_id: (
                AssociationTuple.from_config(rc_entry.data)
                if entry_id == rc_entry.entry_id
                else None
            )
        ),
    }

    start_keymaster_lifecycle(
        hass,
        rc_entry,
        SimpleNamespace(start_slot=21, max_events=2),
    )
    await hass.async_block_till_done()

    entry_data = hass.data[DOMAIN][rc_entry.entry_id]
    lifecycle = entry_data["keymaster_lifecycle"]
    assert lifecycle.current
    assert all(
        result.identity is not None and result.identity.slot in {21, 22}
        for result in lifecycle.current.values()
    )
    first_generation = lifecycle.generation
    stop_keymaster_lifecycle(hass, rc_entry.entry_id)
    start_keymaster_lifecycle(
        hass,
        rc_entry,
        SimpleNamespace(start_slot=21, max_events=2),
    )
    replacement = hass.data[DOMAIN][rc_entry.entry_id]["keymaster_lifecycle"]
    assert replacement.generation > first_generation
    stop_keymaster_lifecycle(hass, rc_entry.entry_id)
    stop_keymaster_lifecycle(hass, rc_entry.entry_id)
    assert "keymaster_lifecycle" not in entry_data


async def test_rc_setup_does_not_infer_verified_entry_from_legacy_selector(
    hass,
) -> None:
    """A legacy lockname and explicit lockless mode do not create an identity."""
    unresolved = MockConfigEntry(
        domain=DOMAIN,
        title="Unresolved RC",
        entry_id="rc-unresolved",
        data={"keymaster_entry_id": "Front Door"},
    )
    lockless = MockConfigEntry(
        domain=DOMAIN,
        title="Lockless RC",
        entry_id="rc-lockless",
        data={
            "keymaster_entry_id": "(none)",
            "verified_keymaster_entry_id": None,
            "association_generation": 1,
            "association_transaction_id": "f" * 32,
            "association_mode": AssociationMode.EXPLICIT_LOCKLESS.value,
        },
    )
    hass.data[DOMAIN] = {
        unresolved.entry_id: {},
        lockless.entry_id: {},
    }

    coordinator = SimpleNamespace(start_slot=21, max_events=1)
    start_keymaster_lifecycle(hass, unresolved, coordinator)
    start_keymaster_lifecycle(hass, lockless, coordinator)

    assert "keymaster_lifecycle" not in hass.data[DOMAIN][unresolved.entry_id]
    assert "keymaster_lifecycle" not in hass.data[DOMAIN][lockless.entry_id]


async def test_rc_setup_rejects_unacknowledged_verified_tuple(hass) -> None:
    """Config data alone cannot authorize a verified-identity lifecycle."""
    _entry(hass, "km-unacknowledged")
    rc_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Unacknowledged RC",
        entry_id="rc-unacknowledged",
        data={
            "verified_keymaster_entry_id": "km-unacknowledged",
            "association_generation": 4,
            "association_transaction_id": "f" * 32,
            "association_mode": AssociationMode.VERIFIED.value,
        },
    )
    hass.data[DOMAIN] = {rc_entry.entry_id: {}}

    start_keymaster_lifecycle(
        hass,
        rc_entry,
        SimpleNamespace(start_slot=21, max_events=1),
    )

    assert "keymaster_lifecycle" not in hass.data[DOMAIN][rc_entry.entry_id]


async def test_parent_entry_changes_invalidate_child_capabilities(hass) -> None:
    """Parent unloads and child parent-link changes revoke inherited evidence."""
    parent_a = _entry(hass, "km-parent-a")
    parent_b = _entry(hass, "km-parent-b")
    parent_b._async_set_state(hass, ConfigEntryState.NOT_LOADED, None)
    child = _entry(
        hass,
        "km-child-parented",
        data={"parent_entry_id": parent_a.entry_id},
    )
    _create_property(hass, child, _NAME)
    override_id = _create_property(hass, child, "switch.code_slots:21.override_parent")
    hass.states.async_set(override_id, "on")

    updates: list[tuple[int, dict[str, Any]]] = []
    lifecycle = _lifecycle(hass, child.entry_id, updates)
    lifecycle.start()
    await hass.async_block_till_done()
    assert lifecycle.current[_NAME].status is CapabilityStatus.READY

    first_generation = lifecycle.generation
    parent_a._async_set_state(hass, ConfigEntryState.NOT_LOADED, None)
    await hass.async_block_till_done()
    assert lifecycle.generation > first_generation
    assert lifecycle.current[_NAME].status is CapabilityStatus.INHERITANCE_UNVERIFIED

    parent_a._async_set_state(hass, ConfigEntryState.LOADED, None)
    await hass.async_block_till_done()
    assert lifecycle.current[_NAME].status is CapabilityStatus.READY

    before_link_change = lifecycle.generation
    hass.config_entries.async_update_entry(
        child,
        data={"parent_entry_id": parent_b.entry_id},
    )
    await hass.async_block_till_done()
    assert lifecycle.generation > before_link_change
    assert lifecycle.current[_NAME].status is CapabilityStatus.INHERITANCE_UNVERIFIED

    lifecycle.stop()
