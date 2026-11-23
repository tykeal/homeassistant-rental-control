# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Verify exact physical Keymaster event attribution with pinned releases."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Iterable
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
import importlib
from pathlib import Path
import tempfile
from types import ModuleType
from types import SimpleNamespace
from typing import Any
from typing import cast

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Event
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.rental_control import util
from custom_components.rental_control.allocator.associations import AssociationMode
from custom_components.rental_control.allocator.associations import AssociationTuple
from custom_components.rental_control.const import CHECKIN_SENSOR
from custom_components.rental_control.const import COORDINATOR
from custom_components.rental_control.const import DOMAIN
from custom_components.rental_control.const import EARLY_CHECKOUT_EXPIRY_SWITCH
from custom_components.rental_control.const import KEYMASTER_MONITORING_SWITCH
from custom_components.rental_control.const import LOCK_MANAGER
from custom_components.rental_control.const import PROTECTED_REGISTRY_STORE
from custom_components.rental_control.const import UNSUB_LISTENERS
from custom_components.rental_control.coordinator import RentalControlCoordinator
from custom_components.rental_control.coordinator_helpers.coordinator_config_shell import (
    CoordinatorConfigMixin,
)
from custom_components.rental_control.coordinator_helpers.coordinator_setup_shell import (
    CoordinatorSetupMixin,
)
from custom_components.rental_control.keymaster_feedback_listener import (
    async_start_listener,
)
from custom_components.rental_control.keymaster_feedback_listener import (
    lifecycle_listener_callback,
)
from custom_components.rental_control.keymaster_identity import (
    build_capability_identity,
)
from custom_components.rental_control.keymaster_lifecycle import get_keymaster_lifecycle
from custom_components.rental_control.keymaster_lifecycle import (
    start_keymaster_lifecycle,
)
from custom_components.rental_control.keymaster_lifecycle import (
    stop_keymaster_lifecycle,
)
from custom_components.rental_control.keymaster_lifecycle_registry import (
    managed_slot_property_keys,
)
from custom_components.rental_control.keymaster_slot_operations import (
    async_slot_operation,
)
from custom_components.rental_control.listeners import _handle_keymaster_event
from custom_components.rental_control.listeners import async_register_keymaster_listener
from custom_components.rental_control.sensors.checkin import runtime
from custom_components.rental_control.sensors.checkin import verified_date
from custom_components.rental_control.switch import (
    async_setup_entry as async_setup_switches,
)

from tests.fixtures.keymaster_releases import artifact_paths
from tests.fixtures.keymaster_releases import import_keymaster_release
from tests.fixtures.keymaster_releases import remove_imported_package
from tests.integration.test_verified_consumer_dispatch import _setup_verified_consumer

_RELEASES = ("v0.6.1", "v0.6.5")
_SLOT = 21
_RC_ID = "verified-checkin-rc"
_KM_ID = "verified-checkin-km"
_ASSOCIATION_TX = "b" * 32


def _event(payload: Mapping[str, Any]) -> Event[Mapping[str, Any]]:
    """Build the real HA event shape consumed by the listener."""
    return Event("keymaster_lock_state_changed", data=payload)


class _ProtectedStore:
    """Expose the exact acknowledged association to the verified test path."""

    def __init__(self, association: AssociationTuple) -> None:
        """Bind one validated association to its RC config entry."""
        self.association = association

    def association_for_entry(self, entry_id: str) -> AssociationTuple | None:
        """Return the acknowledged association only for its owner."""
        return self.association if entry_id == _RC_ID else None


class _UnlockRecorder:
    """Capture accepted event forwarding without creating a real sensor."""

    def __init__(self) -> None:
        """Start with no forwarded unlocks."""
        self.calls: list[tuple[int, str]] = []

    def async_handle_keymaster_unlock(
        self, *, code_slot_num: int, lock_name: str
    ) -> None:
        """Record one call delivered by the listener."""
        self.calls.append((code_slot_num, lock_name))


def _association(km_entry_id: str = _KM_ID) -> AssociationTuple:
    """Return one exact committed association for the test RC entry."""
    return AssociationTuple(
        legacy_lock_selection_present=True,
        legacy_lock_selection_raw="Front Door",
        verified_keymaster_entry_id=km_entry_id,
        association_generation=1,
        association_transaction_id=_ASSOCIATION_TX,
        mode=AssociationMode.VERIFIED,
    )


def _config_entry(
    hass,
    domain: str,
    entry_id: str,
    data: dict[str, Any],
) -> MockConfigEntry:
    """Create one loaded config entry with its current source data."""
    entry = MockConfigEntry(
        domain=domain,
        title=entry_id,
        entry_id=entry_id,
        data=data,
    )
    entry.add_to_hass(hass)
    if domain == LOCK_MANAGER:
        entry._async_set_state(hass, ConfigEntryState.LOADED, None)
    return entry


def _create_slot_properties(
    hass,
    entry: MockConfigEntry,
    *,
    slot: int,
    slot_name: str,
    override_parent: bool | None,
) -> None:
    """Create current real HA registry records and safe live states."""
    for property_key in managed_slot_property_keys(slot, 1):
        identity = build_capability_identity(entry.entry_id, property_key, generation=1)
        assert identity is not None
        registry_entry = er.async_get(hass).async_get_or_create(
            identity.domain,
            identity.platform,
            identity.unique_id,
            config_entry=entry,
        )
        if property_key == f"text.code_slots:{slot}.name":
            state = slot_name
        elif property_key == f"switch.code_slots:{slot}.override_parent":
            state = "on" if override_parent else "off"
        elif identity.domain == "switch":
            state = "on"
        elif identity.domain == "text":
            state = "unknown"
        else:
            state = "ready"
        hass.states.async_set(registry_entry.entity_id, state)


async def _world(
    hass,
    *,
    selected_id: str = _KM_ID,
    selected_name: str = "Front Door",
    selected_lock: str = "lock.front_door",
    parent_id: str | None = None,
    parent_name: str = "Front Door",
    parent_lock: str = "lock.front_door",
    override_parent: bool | None = None,
    unrelated_same_source: bool = False,
    sibling_same_source: bool = False,
    create_parent: bool = True,
) -> tuple[Any, Any, _UnlockRecorder, list[Any]]:
    """Build a verified HA topology with live slot properties and listener."""
    association = _association(selected_id)
    rc_entry = _config_entry(
        hass,
        DOMAIN,
        _RC_ID,
        association.merge_into_config({"name": "Rental"}),
    )
    parent = None
    if parent_id is not None and create_parent:
        parent = _config_entry(
            hass,
            LOCK_MANAGER,
            parent_id,
            {"lock_entity_id": parent_lock, "lockname": parent_name},
        )
        _create_slot_properties(
            hass,
            parent,
            slot=_SLOT,
            slot_name="Guest",
            override_parent=None,
        )
    selected_data = {
        "lock_entity_id": selected_lock,
        "lockname": selected_name,
    }
    if parent_id is not None:
        selected_data["parent_entry_id"] = parent_id
    selected = _config_entry(hass, LOCK_MANAGER, selected_id, selected_data)
    _create_slot_properties(
        hass,
        selected,
        slot=_SLOT,
        slot_name="Guest",
        override_parent=override_parent,
    )
    if unrelated_same_source:
        _config_entry(
            hass,
            LOCK_MANAGER,
            "unrelated-same-source",
            {
                "lock_entity_id": selected_lock,
                "lockname": selected_name,
            },
        )
    sibling = None
    if sibling_same_source:
        assert parent_id is not None
        sibling = _config_entry(
            hass,
            LOCK_MANAGER,
            "sibling-same-source",
            {
                "lock_entity_id": selected_lock,
                "lockname": "Sibling Door",
                "parent_entry_id": parent_id,
            },
        )

    recorder = _UnlockRecorder()
    coordinator = SimpleNamespace(
        start_slot=_SLOT,
        max_events=1,
        lockname="front-door",
        monitored_locknames=frozenset({"front-door"}),
        keymaster_event_diagnostics=deque(maxlen=10),
    )
    entry_data = {
        COORDINATOR: coordinator,
        CHECKIN_SENSOR: recorder,
        KEYMASTER_MONITORING_SWITCH: SimpleNamespace(is_on=True),
        UNSUB_LISTENERS: [],
    }
    coordinator.config_entry = rc_entry
    coordinator.hass = hass
    coordinator._entry_id = rc_entry.entry_id
    hass.data.setdefault(DOMAIN, {})[rc_entry.entry_id] = entry_data
    hass.data[DOMAIN][PROTECTED_REGISTRY_STORE] = _ProtectedStore(association)
    start_keymaster_lifecycle(hass, rc_entry, coordinator)
    await hass.async_block_till_done()
    return rc_entry, selected, recorder, [selected, parent, sibling]


async def test_explicit_lockless_association_ignores_retained_legacy_name(
    hass,
) -> None:
    """A retained legacy selector cannot reactivate Keymaster runtime paths."""
    association = AssociationTuple(
        legacy_lock_selection_present=True,
        legacy_lock_selection_raw="Front Door",
        verified_keymaster_entry_id=None,
        association_generation=1,
        association_transaction_id="d" * 32,
        mode=AssociationMode.EXPLICIT_LOCKLESS,
    )
    rc_entry = _config_entry(
        hass,
        DOMAIN,
        _RC_ID,
        association.merge_into_config({"name": "Rental"}),
    )
    coordinator = SimpleNamespace(
        config_entry=rc_entry,
        hass=hass,
        _entry_id=rc_entry.entry_id,
        lockname="front-door",
        keymaster_runtime_enabled=False,
        start_slot=_SLOT,
        max_events=1,
    )
    coordinator._read_slot_snapshot = lambda _slot: pytest.fail(
        "explicitly lockless setup must not read legacy Keymaster entities"
    )
    entry_data = {
        COORDINATOR: coordinator,
        UNSUB_LISTENERS: [],
    }
    hass.data.setdefault(DOMAIN, {})[rc_entry.entry_id] = entry_data

    coordinator_for_mixins: Any = coordinator
    await CoordinatorSetupMixin._async_setup_keymaster_overrides_impl(
        coordinator_for_mixins
    )
    snapshot = CoordinatorSetupMixin._read_slot_snapshot(coordinator_for_mixins, _SLOT)
    await async_start_listener(hass, rc_entry)
    async_register_keymaster_listener(hass, rc_entry)
    added_switches: list[Any] = []

    def add_switches(
        entities: Iterable[Entity], update_before_add: bool = False
    ) -> None:
        """Capture monitoring entities offered by the platform."""
        added_switches.extend(entities)

    await async_setup_switches(hass, rc_entry, cast(AddEntitiesCallback, add_switches))
    await CoordinatorConfigMixin._async_update_buffer_times(
        coordinator_for_mixins, 1, 1
    )
    expiry_updated = await runtime.async_update_lock_code_expiry(
        SimpleNamespace(coordinator=coordinator),
        datetime.now().astimezone(),
    )

    assert snapshot.name_state is None
    assert entry_data[UNSUB_LISTENERS] == []
    assert "keymaster_state_listener_unsub" not in entry_data
    assert not coordinator.keymaster_runtime_enabled
    assert added_switches == []
    assert not expiry_updated


@pytest.mark.parametrize("release", _RELEASES)
async def test_late_selected_keymaster_load_refreshes_monitor_topology(
    hass, monkeypatch, release: str
) -> None:
    """Rebind refreshes cached IDs/names without enabling legacy event fallback."""
    association = _association()
    rc_entry = _config_entry(
        hass,
        DOMAIN,
        _RC_ID,
        association.merge_into_config({"name": "Rental"}),
    )
    selected = MockConfigEntry(
        domain=LOCK_MANAGER,
        title="Front Door",
        entry_id=_KM_ID,
        data={"lock_entity_id": "lock.front_door", "lockname": "Front Door"},
    )
    selected.add_to_hass(hass)
    selected._async_set_state(hass, ConfigEntryState.NOT_LOADED, None)
    _create_slot_properties(
        hass,
        selected,
        slot=_SLOT,
        slot_name="Guest",
        override_parent=None,
    )
    child = _config_entry(
        hass,
        LOCK_MANAGER,
        "late-child",
        {
            "lock_entity_id": "lock.front_door",
            "lockname": "Side Door",
            "parent_entry_id": selected.entry_id,
        },
    )

    coordinator = object.__new__(RentalControlCoordinator)
    coordinator.hass = hass
    coordinator.config_entry = rc_entry
    coordinator._entry_id = rc_entry.entry_id
    coordinator.start_slot = _SLOT
    coordinator.max_events = 1
    coordinator.lockname = "front-door"
    coordinator._parent_entry_id = None
    coordinator._child_locknames = set()
    coordinator.keymaster_event_diagnostics = deque(maxlen=10)
    recorder = _UnlockRecorder()
    entry_data = {
        COORDINATOR: coordinator,
        CHECKIN_SENSOR: recorder,
        KEYMASTER_MONITORING_SWITCH: SimpleNamespace(is_on=True),
        UNSUB_LISTENERS: [],
    }
    hass.data.setdefault(DOMAIN, {})[rc_entry.entry_id] = entry_data
    hass.data[DOMAIN][PROTECTED_REGISTRY_STORE] = _ProtectedStore(association)

    async def no_listener_rebind(*_args, **_kwargs) -> None:
        """Keep this test focused on the lifecycle callback's topology refresh."""

    monkeypatch.setattr(
        "custom_components.rental_control.keymaster_feedback_listener.async_start_listener",
        no_listener_rebind,
    )
    monkeypatch.setattr(
        "custom_components.rental_control.keymaster_feedback_listener.async_arm_startup_readability_refresh",
        lambda *_args, **_kwargs: None,
    )
    start_keymaster_lifecycle(
        hass,
        rc_entry,
        coordinator,
        on_resolved=lifecycle_listener_callback(hass, rc_entry, coordinator),
    )
    await hass.async_block_till_done()
    assert coordinator._parent_entry_id is None
    assert coordinator.monitored_locknames == frozenset({"front-door"})

    selected._async_set_state(hass, ConfigEntryState.LOADED, None)
    await hass.async_block_till_done()
    assert coordinator._parent_entry_id == selected.entry_id
    assert coordinator._child_locknames == {"side_door"}
    assert coordinator.monitored_locknames == frozenset({"front-door", "side_door"})

    _handle_keymaster_event(
        hass,
        rc_entry,
        _event(
            _emit_real_release_event(
                release,
                slot=_SLOT,
                lockname="Side Door",
                entity_id="lock.front_door",
                slot_name="Guest",
            ),
        ),
        coordinator,
        _SLOT,
        1,
    )
    assert recorder.calls == []
    assert child.data["parent_entry_id"] == selected.entry_id
    stop_keymaster_lifecycle(hass, rc_entry.entry_id)


def _emit_real_release_event(
    release: str,
    *,
    slot: int,
    lockname: str,
    entity_id: str,
    slot_name: str,
) -> dict[str, Any]:
    """Call the actual pinned release event emitter and capture its payload."""
    assert f"keymaster-{release}" in artifact_paths()
    module_name = "custom_components.keymaster"
    package_root = Path(tempfile.mkdtemp(prefix=f"km-event-{release}-"))
    import_keymaster_release(release, package_root, module_name)
    fired: list[dict[str, Any]] = []
    hass = SimpleNamespace(
        bus=SimpleNamespace(
            fire=lambda event_type, *, event_data: fired.append(
                {"event_type": event_type, **event_data}
            )
        )
    )
    keymaster_lock = SimpleNamespace(
        lock_name=lockname,
        lock_entity_id=entity_id,
        code_slots={slot: SimpleNamespace(name=slot_name)},
    )
    try:
        if release == "v0.6.1":
            coordinator = importlib.import_module(
                f"{module_name}.coordinator"
            ).KeymasterCoordinator.__new__(
                importlib.import_module(
                    f"{module_name}.coordinator"
                ).KeymasterCoordinator
            )
            coordinator.hass = hass
            coordinator._fire_lock_state_changed_event(
                keymaster_lock,
                "unlocked",
                code_slot_num=slot,
                source="event",
                event_label="Keypad Unlock",
                action_code=5,
            )
        else:
            bus_events: ModuleType = importlib.import_module(
                f"{module_name}.bus_events"
            )
            event = bus_events.LockStateChangedEvent(
                code_slot_num=slot,
                source="event",
                event_label="Keypad Unlock",
                action_code=5,
            )
            bus_events.fire_lock_state_changed_event(
                hass, keymaster_lock, "unlocked", event
            )
    finally:
        remove_imported_package(module_name)
    assert len(fired) == 1
    return fired[0]


@pytest.mark.parametrize("release", _RELEASES)
async def test_real_release_payload_attributes_exact_verified_source(
    hass, release: str
) -> None:
    """Both packaged release emitters carry physical entity ID, not entry ID."""
    rc_entry, selected, recorder, entries = await _world(hass)
    payload = _emit_real_release_event(
        release,
        slot=_SLOT,
        lockname="Front Door",
        entity_id="lock.front_door",
        slot_name="Guest",
    )
    assert payload == {
        "event_type": "keymaster_lock_state_changed",
        "notification_source": "event",
        "lockname": "Front Door",
        "entity_id": "lock.front_door",
        "state": "unlocked",
        "action_code": 5,
        "action_text": "Keypad Unlock",
        "code_slot_num": _SLOT,
        "code_slot_name": "Guest",
    }

    _handle_keymaster_event(
        hass,
        rc_entry,
        _event(payload),
        coordinator=hass.data[DOMAIN][_RC_ID][COORDINATOR],
        start_slot=_SLOT,
        max_events=1,
    )

    assert recorder.calls == [(_SLOT, "Front Door")]
    stop_keymaster_lifecycle(hass, rc_entry.entry_id)
    assert selected.entry_id == _KM_ID
    assert entries


@pytest.mark.parametrize(
    "mode,selector",
    [
        (AssociationMode.EXPLICIT_LOCKLESS, "Front Door"),
        (AssociationMode.VERIFIED, None),
        (AssociationMode.LEGACY_UNRESOLVED, "Front Door"),
    ],
)
async def test_queued_unlock_is_rejected_after_association_disables_runtime(
    hass, mode: AssociationMode, selector: str | None
) -> None:
    """A queued event cannot use a stale live listener or lifecycle."""
    rc_entry, _selected, recorder, _entries = await _world(hass)
    association = _association()
    changed = replace(
        association,
        mode=mode,
        legacy_lock_selection_raw=selector,
        verified_keymaster_entry_id=(
            _KM_ID if mode is AssociationMode.VERIFIED else None
        ),
    )
    hass.config_entries.async_update_entry(
        rc_entry, data=changed.merge_into_config(rc_entry.data)
    )
    _handle_keymaster_event(
        hass,
        rc_entry,
        _event(
            {
                "lockname": "Front Door",
                "entity_id": "lock.front_door",
                "state": "unlocked",
                "code_slot_num": _SLOT,
                "code_slot_name": "Guest",
            }
        ),
        hass.data[DOMAIN][_RC_ID][COORDINATOR],
        _SLOT,
        1,
    )
    assert recorder.calls == []
    stop_keymaster_lifecycle(hass, rc_entry.entry_id)


@pytest.mark.parametrize("release", _RELEASES)
async def test_inherited_child_uses_parent_physical_source(hass, release: str) -> None:
    """A child with override disabled uses its exact live parent property."""
    inherited_rc, _, inherited_recorder, inherited_entries = await _world(
        hass,
        selected_id="child-inherited",
        selected_name="Child Door",
        selected_lock="lock.child_door",
        parent_id="parent-door",
        parent_name="Front Door",
        parent_lock="lock.front_door",
        override_parent=False,
    )
    inherited_payload = _emit_real_release_event(
        release,
        slot=_SLOT,
        lockname="Front Door",
        entity_id="lock.front_door",
        slot_name="Guest",
    )
    inherited_coordinator = hass.data[DOMAIN][_RC_ID][COORDINATOR]
    _handle_keymaster_event(
        hass,
        inherited_rc,
        _event(inherited_payload),
        inherited_coordinator,
        _SLOT,
        1,
    )
    assert inherited_recorder.calls == [(_SLOT, "Front Door")]
    stop_keymaster_lifecycle(hass, inherited_rc.entry_id)


@pytest.mark.parametrize("release", _RELEASES)
async def test_parent_association_accepts_inherited_child_lock_event(
    hass, release: str
) -> None:
    """A linked child's physical unlock is attributable to its inherited slot."""
    rc_entry, selected, recorder, entries = await _world(hass)
    child = _config_entry(
        hass,
        LOCK_MANAGER,
        "linked-child-lock",
        {
            "lock_entity_id": "lock.side_door",
            "lockname": "Side Door",
            "parent_entry_id": selected.entry_id,
        },
    )
    _create_slot_properties(
        hass,
        child,
        slot=_SLOT,
        slot_name="Guest",
        override_parent=False,
    )

    _handle_keymaster_event(
        hass,
        rc_entry,
        _event(
            _emit_real_release_event(
                release,
                slot=_SLOT,
                lockname="Side Door",
                entity_id="lock.side_door",
                slot_name="Guest",
            ),
        ),
        hass.data[DOMAIN][_RC_ID][COORDINATOR],
        _SLOT,
        1,
    )

    assert recorder.calls == [(_SLOT, "Side Door")]
    assert child.data["parent_entry_id"] == selected.entry_id
    assert entries[0] == selected
    stop_keymaster_lifecycle(hass, rc_entry.entry_id)


@pytest.mark.parametrize("release", _RELEASES)
async def test_associated_inherited_child_accepts_its_physical_event(
    hass, release: str
) -> None:
    """A selected inherited child also reports its own lock's physical source."""
    rc_entry, selected, recorder, _entries = await _world(
        hass,
        selected_id="child-inherited",
        selected_name="Child Door",
        selected_lock="lock.child_door",
        parent_id="parent-door",
        parent_name="Front Door",
        parent_lock="lock.front_door",
        override_parent=False,
    )

    _handle_keymaster_event(
        hass,
        rc_entry,
        _event(
            _emit_real_release_event(
                release,
                slot=_SLOT,
                lockname="Child Door",
                entity_id="lock.child_door",
                slot_name="Guest",
            ),
        ),
        hass.data[DOMAIN][_RC_ID][COORDINATOR],
        _SLOT,
        1,
    )

    assert recorder.calls == [(_SLOT, "Child Door")]
    assert selected.entry_id == "child-inherited"
    stop_keymaster_lifecycle(hass, rc_entry.entry_id)


@pytest.mark.parametrize("release", _RELEASES)
async def test_parent_association_rejects_overridden_child_event(
    hass, release: str
) -> None:
    """A child-local override cannot check in a parent-associated guest."""
    rc_entry, selected, recorder, _entries = await _world(hass)
    child = _config_entry(
        hass,
        LOCK_MANAGER,
        "linked-child-override",
        {
            "lock_entity_id": "lock.side_door",
            "lockname": "Side Door",
            "parent_entry_id": selected.entry_id,
        },
    )
    _create_slot_properties(
        hass,
        child,
        slot=_SLOT,
        slot_name="Guest",
        override_parent=True,
    )

    _handle_keymaster_event(
        hass,
        rc_entry,
        _event(
            _emit_real_release_event(
                release,
                slot=_SLOT,
                lockname="Side Door",
                entity_id="lock.side_door",
                slot_name="Guest",
            ),
        ),
        hass.data[DOMAIN][_RC_ID][COORDINATOR],
        _SLOT,
        1,
    )

    assert recorder.calls == []
    stop_keymaster_lifecycle(hass, rc_entry.entry_id)


@pytest.mark.parametrize("release", _RELEASES)
async def test_parent_association_rejects_unrelated_child_event(
    hass, release: str
) -> None:
    """A same-slot child under another parent is not an event source."""
    rc_entry, selected, recorder, _entries = await _world(hass)
    unrelated_parent = _config_entry(
        hass,
        LOCK_MANAGER,
        "unrelated-parent",
        {
            "lock_entity_id": "lock.back_door",
            "lockname": "Back Door",
        },
    )
    child = _config_entry(
        hass,
        LOCK_MANAGER,
        "unrelated-linked-child",
        {
            "lock_entity_id": "lock.side_door",
            "lockname": "Side Door",
            "parent_entry_id": unrelated_parent.entry_id,
        },
    )
    _create_slot_properties(
        hass,
        child,
        slot=_SLOT,
        slot_name="Guest",
        override_parent=False,
    )

    _handle_keymaster_event(
        hass,
        rc_entry,
        _event(
            _emit_real_release_event(
                release,
                slot=_SLOT,
                lockname="Side Door",
                entity_id="lock.side_door",
                slot_name="Guest",
            ),
        ),
        hass.data[DOMAIN][_RC_ID][COORDINATOR],
        _SLOT,
        1,
    )

    assert recorder.calls == []
    assert child.data["parent_entry_id"] == unrelated_parent.entry_id
    assert child.data["parent_entry_id"] != selected.entry_id
    stop_keymaster_lifecycle(hass, rc_entry.entry_id)


@pytest.mark.parametrize("release", _RELEASES)
async def test_parent_association_rejects_ambiguous_child_physical_source(
    hass, release: str
) -> None:
    """Two linked entries sharing a physical source do not identify one lock."""
    rc_entry, selected, recorder, _entries = await _world(hass)
    children = []
    for entry_id, lockname in (
        ("linked-child-one", "Side Door"),
        ("linked-child-two", "Side Entry"),
    ):
        child = _config_entry(
            hass,
            LOCK_MANAGER,
            entry_id,
            {
                "lock_entity_id": "lock.shared_side_door",
                "lockname": lockname,
                "parent_entry_id": selected.entry_id,
            },
        )
        _create_slot_properties(
            hass,
            child,
            slot=_SLOT,
            slot_name="Guest",
            override_parent=False,
        )
        children.append(child)

    _handle_keymaster_event(
        hass,
        rc_entry,
        _event(
            _emit_real_release_event(
                release,
                slot=_SLOT,
                lockname="Side Door",
                entity_id="lock.shared_side_door",
                slot_name="Guest",
            ),
        ),
        hass.data[DOMAIN][_RC_ID][COORDINATOR],
        _SLOT,
        1,
    )

    assert recorder.calls == []
    assert len(children) == 2
    stop_keymaster_lifecycle(hass, rc_entry.entry_id)


@pytest.mark.parametrize("release", _RELEASES)
async def test_overridden_child_uses_child_physical_source(hass, release: str) -> None:
    """A live same-slot override authorizes only the child's current source."""
    override_rc, _, override_recorder, override_entries = await _world(
        hass,
        selected_id="child-override",
        selected_name="Child Door",
        selected_lock="lock.child_door",
        parent_id="parent-override",
        parent_name="Front Door",
        parent_lock="lock.front_door",
        override_parent=True,
    )
    override_coordinator = hass.data[DOMAIN][_RC_ID][COORDINATOR]
    _handle_keymaster_event(
        hass,
        override_rc,
        _event(
            _emit_real_release_event(
                release,
                slot=_SLOT,
                lockname="Child Door",
                entity_id="lock.child_door",
                slot_name="Guest",
            ),
        ),
        override_coordinator,
        _SLOT,
        1,
    )
    assert override_recorder.calls == [(_SLOT, "Child Door")]
    stop_keymaster_lifecycle(hass, override_rc.entry_id)
    assert override_entries


@pytest.mark.parametrize("release", _RELEASES)
async def test_override_on_sibling_child_shared_source_is_rejected(
    hass, release: str
) -> None:
    """Override does not disambiguate sibling entries sharing one physical lock."""
    rc_entry, _, recorder, entries = await _world(
        hass,
        selected_id="child-override",
        selected_name="Child Door",
        selected_lock="lock.shared_child_door",
        parent_id="parent-door",
        parent_name="Front Door",
        parent_lock="lock.front_door",
        override_parent=True,
        sibling_same_source=True,
    )
    _handle_keymaster_event(
        hass,
        rc_entry,
        _event(
            _emit_real_release_event(
                release,
                slot=_SLOT,
                lockname="Child Door",
                entity_id="lock.shared_child_door",
                slot_name="Guest",
            ),
        ),
        hass.data[DOMAIN][_RC_ID][COORDINATOR],
        _SLOT,
        1,
    )
    assert recorder.calls == []
    assert entries[-1].entry_id == "sibling-same-source"
    stop_keymaster_lifecycle(hass, rc_entry.entry_id)


@pytest.mark.parametrize(
    ("payload", "extra_entry"),
    [
        (
            {
                "lockname": "Front Door",
                "entity_id": "lock.other_door",
                "state": "unlocked",
                "code_slot_num": _SLOT,
                "code_slot_name": "Guest",
            },
            False,
        ),
        (
            {
                "lockname": "Conflicting Name",
                "entity_id": "lock.front_door",
                "state": "unlocked",
                "code_slot_num": _SLOT,
                "code_slot_name": "Guest",
            },
            False,
        ),
        (
            {
                "lockname": "Front Door",
                "state": "unlocked",
                "code_slot_num": _SLOT,
                "code_slot_name": "Guest",
            },
            False,
        ),
        (
            {
                "lockname": "Front Door",
                "entity_id": "lock.front_door",
                "state": "unlocked",
                "code_slot_num": _SLOT,
                "code_slot_name": "Guest",
            },
            True,
        ),
    ],
)
async def test_unverifiable_event_identity_and_shared_source_fail_closed(
    hass, payload: dict[str, Any], extra_entry: bool
) -> None:
    """A missing ID, conflicting name, or unrelated shared source is rejected."""
    rc_entry, _, recorder, entries = await _world(
        hass, unrelated_same_source=extra_entry
    )
    coordinator = hass.data[DOMAIN][_RC_ID][COORDINATOR]
    _handle_keymaster_event(
        hass,
        rc_entry,
        _event(payload),
        coordinator,
        _SLOT,
        1,
    )
    assert recorder.calls == []
    stop_keymaster_lifecycle(hass, rc_entry.entry_id)
    assert entries


@pytest.mark.parametrize(
    ("state", "slot"),
    [("locked", _SLOT), ("unlocked", 0), ("unlocked", _SLOT + 1)],
)
async def test_non_unlock_zero_and_out_of_range_events_do_not_check_in(
    hass, state: str, slot: int
) -> None:
    """Only an unlocked event on a nonzero managed slot is forwarded."""
    rc_entry, _, recorder, _entries = await _world(hass)
    _handle_keymaster_event(
        hass,
        rc_entry,
        _event(
            {
                "lockname": "Front Door",
                "entity_id": "lock.front_door",
                "state": state,
                "code_slot_num": slot,
                "code_slot_name": "Guest",
            },
        ),
        hass.data[DOMAIN][_RC_ID][COORDINATOR],
        _SLOT,
        1,
    )
    assert recorder.calls == []
    stop_keymaster_lifecycle(hass, rc_entry.entry_id)


async def test_stale_child_link_fails_closed(hass) -> None:
    """An unavailable recorded parent never authorizes child event routing."""
    rc_entry, _, recorder, _entries = await _world(
        hass,
        selected_id="child-stale",
        selected_name="Child Door",
        selected_lock="lock.child_door",
        parent_id="missing-parent",
        override_parent=False,
        create_parent=False,
    )
    _handle_keymaster_event(
        hass,
        rc_entry,
        _event(
            {
                "lockname": "Front Door",
                "entity_id": "lock.front_door",
                "state": "unlocked",
                "code_slot_num": _SLOT,
                "code_slot_name": "Guest",
            },
        ),
        hass.data[DOMAIN][_RC_ID][COORDINATOR],
        _SLOT,
        1,
    )
    assert recorder.calls == []
    stop_keymaster_lifecycle(hass, rc_entry.entry_id)


async def test_verified_parent_discovery_uses_entry_links_not_matching_names(
    hass,
) -> None:
    """Same-name decoys cannot replace an explicit parent-child relation."""
    rc_entry, selected, _recorder, _entries = await _world(hass)
    coordinator = hass.data[DOMAIN][_RC_ID][COORDINATOR]
    decoy = _config_entry(
        hass,
        LOCK_MANAGER,
        "same-name-decoy",
        {"lock_entity_id": "lock.other_door", "lockname": "Front Door"},
    )
    child = _config_entry(
        hass,
        LOCK_MANAGER,
        "linked-child",
        {
            "parent_entry_id": selected.entry_id,
            "lock_entity_id": "lock.front_door",
            "lockname": "Side Entry",
        },
    )
    unrelated_child = _config_entry(
        hass,
        LOCK_MANAGER,
        "unrelated-child",
        {
            "parent_entry_id": decoy.entry_id,
            "lock_entity_id": "lock.other_door",
            "lockname": "Side Entry",
        },
    )

    coordinator._parent_entry_id = selected.entry_id
    assert CoordinatorSetupMixin._find_parent_entry_id(coordinator) == selected.entry_id
    assert CoordinatorSetupMixin._discover_child_locks(coordinator) == {"side_entry"}
    stop_keymaster_lifecycle(hass, rc_entry.entry_id)
    assert child.entry_id != unrelated_child.entry_id


async def test_unready_child_override_property_blocks_event(hass) -> None:
    """Unready child control fails closed and restored readiness enables routing."""
    rc_entry, _selected, recorder, _entries = await _world(
        hass,
        selected_id="child-unready",
        selected_name="Child Door",
        selected_lock="lock.child_door",
        parent_id="parent-unready",
        parent_name="Front Door",
        parent_lock="lock.front_door",
        override_parent=False,
    )
    lifecycle = hass.data[DOMAIN][rc_entry.entry_id]["keymaster_lifecycle"]
    override = lifecycle.current[f"switch.code_slots:{_SLOT}.override_parent"]
    assert override.entity_id is not None
    hass.states.async_set(override.entity_id, "unknown")
    await hass.async_block_till_done()
    _handle_keymaster_event(
        hass,
        rc_entry,
        _event(
            {
                "lockname": "Front Door",
                "entity_id": "lock.front_door",
                "state": "unlocked",
                "code_slot_num": _SLOT,
                "code_slot_name": "Guest",
            },
        ),
        hass.data[DOMAIN][_RC_ID][COORDINATOR],
        _SLOT,
        1,
    )
    assert recorder.calls == []

    hass.states.async_set(override.entity_id, "off")
    await hass.async_block_till_done()
    _handle_keymaster_event(
        hass,
        rc_entry,
        _event(
            {
                "lockname": "Front Door",
                "entity_id": "lock.front_door",
                "state": "unlocked",
                "code_slot_num": _SLOT,
                "code_slot_name": "Guest",
            },
        ),
        hass.data[DOMAIN][_RC_ID][COORDINATOR],
        _SLOT,
        1,
    )
    assert recorder.calls == [(_SLOT, "Front Door")]
    stop_keymaster_lifecycle(hass, rc_entry.entry_id)


async def test_direct_child_requires_live_current_generation_override(
    hass, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Restored or stale-generation child evidence cannot attribute an unlock."""
    from custom_components.rental_control import keymaster_topology

    rc_entry, selected, recorder, _entries = await _world(hass)
    child = _config_entry(
        hass,
        LOCK_MANAGER,
        "child-restored-state",
        {
            "lock_entity_id": "lock.side_door",
            "lockname": "Side Door",
            "parent_entry_id": selected.entry_id,
        },
    )
    _create_slot_properties(
        hass, child, slot=_SLOT, slot_name="Guest", override_parent=False
    )
    identity = build_capability_identity(
        child.entry_id, f"switch.code_slots:{_SLOT}.override_parent", generation=1
    )
    assert identity is not None
    entity_id = er.async_get(hass).async_get_entity_id(
        "switch", LOCK_MANAGER, identity.unique_id
    )
    assert entity_id is not None
    coordinator = hass.data[DOMAIN][_RC_ID][COORDINATOR]
    payload = _event(
        {
            "lockname": "Side Door",
            "entity_id": "lock.side_door",
            "state": "unlocked",
            "code_slot_num": _SLOT,
            "code_slot_name": "Guest",
        }
    )
    hass.states.async_set(entity_id, "off", {"restored": True})
    _handle_keymaster_event(hass, rc_entry, payload, coordinator, _SLOT, 1)
    assert recorder.calls == []

    hass.states.async_set(entity_id, "off")
    _handle_keymaster_event(hass, rc_entry, payload, coordinator, _SLOT, 1)
    assert recorder.calls == [(_SLOT, "Side Door")]
    recorder.calls.clear()
    old_lifecycle = get_keymaster_lifecycle(hass, rc_entry.entry_id)
    assert old_lifecycle is not None
    old_generation = old_lifecycle.generation
    stop_keymaster_lifecycle(hass, rc_entry.entry_id)
    start_keymaster_lifecycle(hass, rc_entry, coordinator)
    await hass.async_block_till_done()
    current = get_keymaster_lifecycle(hass, rc_entry.entry_id)
    assert current is not None and current.generation > old_generation

    resolve = keymaster_topology.resolve_capability

    def stale_child_resolution(*args, **kwargs):
        """Simulate a child override retained from an older lifecycle epoch."""
        resolution = resolve(*args, **kwargs)
        if args[1] == child.entry_id and resolution.identity is not None:
            return replace(
                resolution,
                identity=replace(resolution.identity, generation=old_generation),
            )
        return resolution

    monkeypatch.setattr(
        keymaster_topology, "resolve_capability", stale_child_resolution
    )
    _handle_keymaster_event(hass, rc_entry, payload, coordinator, _SLOT, 1)
    assert recorder.calls == []
    monkeypatch.setattr(keymaster_topology, "resolve_capability", resolve)
    _handle_keymaster_event(hass, rc_entry, payload, coordinator, _SLOT, 1)
    assert recorder.calls == [(_SLOT, "Side Door")]
    stop_keymaster_lifecycle(hass, rc_entry.entry_id)


async def test_parent_link_change_revokes_old_inherited_event_source(hass) -> None:
    """Changing the live parent link stops accepting events from the old parent."""
    rc_entry, selected, recorder, _entries = await _world(
        hass,
        selected_id="child-relinked",
        selected_name="Child Door",
        selected_lock="lock.child_door",
        parent_id="original-parent",
        parent_name="Front Door",
        parent_lock="lock.front_door",
        override_parent=False,
    )
    coordinator = hass.data[DOMAIN][_RC_ID][COORDINATOR]

    def emit_parent_event() -> None:
        """Emit an unlock event from the formerly inherited parent."""
        _handle_keymaster_event(
            hass,
            rc_entry,
            _event(
                {
                    "lockname": "Front Door",
                    "entity_id": "lock.front_door",
                    "state": "unlocked",
                    "code_slot_num": _SLOT,
                    "code_slot_name": "Guest",
                },
            ),
            coordinator,
            _SLOT,
            1,
        )

    emit_parent_event()
    assert recorder.calls == [(_SLOT, "Front Door")]
    changed_data = dict(selected.data)
    changed_data["parent_entry_id"] = "replacement-parent"
    hass.config_entries.async_update_entry(selected, data=changed_data)
    emit_parent_event()
    assert recorder.calls == [(_SLOT, "Front Door")]
    stop_keymaster_lifecycle(hass, rc_entry.entry_id)


async def test_corrected_valid_event_follows_rejected_zero_slot(hass) -> None:
    """A rejected slot-zero payload does not poison the next valid event."""
    rc_entry, _, recorder, _entries = await _world(hass)
    coordinator = hass.data[DOMAIN][_RC_ID][COORDINATOR]
    for slot in (0, _SLOT):
        _handle_keymaster_event(
            hass,
            rc_entry,
            _event(
                {
                    "lockname": "Front Door",
                    "entity_id": "lock.front_door",
                    "state": "unlocked",
                    "code_slot_num": slot,
                    "code_slot_name": "Guest",
                },
            ),
            coordinator,
            _SLOT,
            1,
        )
    assert recorder.calls == [(_SLOT, "Front Door")]
    stop_keymaster_lifecycle(hass, rc_entry.entry_id)


@pytest.mark.parametrize(
    ("release", "apply_state"),
    [(release, apply_state) for release in _RELEASES for apply_state in (True, False)],
)
async def test_early_checkout_date_uses_verified_live_datetime_capability(
    hass,
    tmp_path: Path,
    release: str,
    apply_state: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verified checkout keeps a dispatched shortening even when unconfirmed."""
    from datetime import UTC
    from datetime import datetime
    from datetime import timedelta
    from unittest.mock import AsyncMock

    coordinator, store, entity_ids = await _setup_verified_consumer(
        hass, tmp_path / release / ".storage", release
    )
    km_entry = hass.config_entries.async_get_entry("consumer-km")
    assert km_entry is not None
    data = dict(km_entry.data)
    data.update(
        {"lockname": "Consumer Door", "lock_entity_id": "lock.selected_physical_lock"}
    )
    hass.config_entries.async_update_entry(km_entry, data=data)
    await hass.async_block_till_done()

    identity_key = "consumer-reservation"
    coordinator.get_slot_assignment = lambda identity: (
        _SLOT if identity == identity_key else None
    )
    start = datetime.now(UTC)
    old_end = start + timedelta(days=3)
    now = datetime.now(UTC)
    new_end = runtime.compute_early_expiry_time(now, old_end)
    monkeypatch.setattr(
        verified_date,
        "make_reservation_fingerprint",
        lambda *_args: identity_key,
    )
    fallback = AsyncMock()
    monkeypatch.setattr(runtime, "add_call", fallback)
    original_deps = util._keymaster_deps
    monkeypatch.setattr(
        util,
        "_keymaster_deps",
        lambda: replace(original_deps(), confirmation_timeout=0.01),
    )
    monkeypatch.setattr(
        runtime,
        "get_entry_data",
        lambda *_args: {EARLY_CHECKOUT_EXPIRY_SWITCH: SimpleNamespace(is_on=True)},
    )
    dispatches: list[dict[str, Any]] = []

    async def set_datetime(call) -> None:
        """Record the exact target and optionally apply the requested state."""
        dispatches.append(dict(call.data))
        if apply_state:
            hass.states.async_set(
                call.data["entity_id"],
                call.data["datetime"],
            )

    hass.services.async_register("datetime", "set_value", set_datetime)
    entity = SimpleNamespace(
        _hass=hass,
        _config_entry=coordinator.config_entry,
        coordinator=coordinator,
        _tracked_event_slot_name="Guest",
        _tracked_event_start=start,
        _tracked_event_end=old_end,
    )

    end_id = entity_ids[f"datetime.code_slots:{_SLOT}.accesslimit_date_range_end"]
    hass.states.async_set(end_id, old_end.isoformat())
    await runtime._maybe_shorten_lock_expiry(entity, now)

    assert entity._tracked_event_end == new_end
    assert len(dispatches) == 1
    assert dispatches[0]["entity_id"] == end_id
    assert dispatches[0]["datetime"] != old_end.isoformat()
    assert (hass.states.get(end_id).state == dispatches[0]["datetime"]) is apply_state
    assert store.loaded_result is not None
    assert any(
        record["fence_type"] == "allocation"
        for record in store.loaded_result.data["pending_fences"]
    )
    fallback.assert_not_awaited()


@pytest.mark.parametrize("phase", ["queued", "pre_dispatch", "in_dispatch"])
@pytest.mark.parametrize("changed", ["override", "feedback", "assignment"])
async def test_early_checkout_rejects_stale_slot_assignment(
    hass,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    changed: str,
) -> None:
    """Retained old ownership cannot authorize expiry on a reassigned slot."""
    from datetime import UTC
    from datetime import timedelta

    coordinator, store, entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage", "v0.6.1"
    )
    km_entry = hass.config_entries.async_get_entry("consumer-km")
    assert km_entry is not None
    hass.config_entries.async_update_entry(
        km_entry,
        data={**km_entry.data, "lockname": "Consumer Door"},
    )
    await hass.async_block_till_done()
    overrides = coordinator.event_overrides
    assigned = True
    coordinator.get_slot_assignment = lambda identity: (
        _SLOT if identity == "consumer-reservation" and assigned else None
    )
    monkeypatch.setattr(
        verified_date,
        "make_reservation_fingerprint",
        lambda *_args: "consumer-reservation",
    )
    start = datetime.now(UTC)
    old_end = start + timedelta(days=3)
    new_end = start + timedelta(hours=1)
    replacement_end = start + timedelta(days=5)
    end_id = entity_ids[f"datetime.code_slots:{_SLOT}.accesslimit_date_range_end"]
    hass.states.async_set(end_id, replacement_end.isoformat())
    dispatches: list[dict[str, Any]] = []
    waiting = asyncio.Event()
    resume = asyncio.Event()

    async def set_datetime(call) -> None:
        """Record and apply an unsafe dispatch if the guard misses the race."""
        dispatches.append(dict(call.data))
        if phase == "in_dispatch":
            waiting.set()
            await resume.wait()
        hass.states.async_set(call.data["entity_id"], call.data["datetime"])

    hass.services.async_register("datetime", "set_value", set_datetime)
    entity = SimpleNamespace(
        _hass=hass,
        _config_entry=coordinator.config_entry,
        coordinator=coordinator,
        _tracked_event_slot_name="Guest",
        _tracked_event_start=start,
        _tracked_event_end=old_end,
    )

    async def replace_assignment() -> None:
        """Change only the relevant in-memory evidence, keeping old ledger owner."""
        nonlocal assigned
        if changed == "override":
            overrides.update(_SLOT, "9999", "Guest", start, replacement_end)
        elif changed == "feedback":
            generation = overrides.get_feedback_generation(_SLOT)
            assert await overrides.async_update(
                _SLOT,
                "9999",
                "Replacement",
                start,
                replacement_end,
                merge_feedback=True,
                feedback_field="slot_name",
                feedback_generation=generation,
            )
            assert overrides.get_feedback_generation(_SLOT) == generation
        else:
            assigned = False

    if phase == "queued":
        async with async_slot_operation(coordinator, _SLOT):
            task = asyncio.create_task(
                verified_date.async_update_verified_lock_code_expiry(
                    entity, new_end, identity_end=old_end
                )
            )

            async def wait_for_queued_checkout() -> None:
                """Wait until checkout has reserved a claim behind the SET."""
                while overrides._slot_operations_pending.get(_SLOT, 0) != 2:
                    await asyncio.sleep(0)

            await asyncio.wait_for(wait_for_queued_checkout(), timeout=1)
            await replace_assignment()
    elif phase == "pre_dispatch":
        original_wait = store.async_wait_for_commit_completion
        wait_count = 0

        async def hold_final_intent_check() -> bool:
            """Pause the second intent check immediately before service dispatch."""
            nonlocal wait_count
            wait_count += 1
            if wait_count == 2:
                waiting.set()
                await resume.wait()
            return (await original_wait()) is True

        monkeypatch.setattr(
            store, "async_wait_for_commit_completion", hold_final_intent_check
        )
        task = asyncio.create_task(
            verified_date.async_update_verified_lock_code_expiry(
                entity, new_end, identity_end=old_end
            )
        )
        await asyncio.wait_for(waiting.wait(), timeout=1)
        await replace_assignment()
        resume.set()
    else:
        task = asyncio.create_task(
            verified_date.async_update_verified_lock_code_expiry(
                entity, new_end, identity_end=old_end
            )
        )
        await asyncio.wait_for(waiting.wait(), timeout=1)
        await replace_assignment()
        resume.set()

    assert not await asyncio.wait_for(task, timeout=5)
    if phase == "in_dispatch":
        assert len(dispatches) == 1
        assert hass.states.get(end_id).state == new_end.isoformat()
    else:
        assert dispatches == []
        assert hass.states.get(end_id).state == replacement_end.isoformat()


async def test_failed_early_checkout_does_not_restore_replaced_reservation(
    hass, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale checkout must not overwrite the next tracked reservation."""
    from datetime import UTC
    from datetime import timedelta

    coordinator, _store, _entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage", "v0.6.1"
    )
    km_entry = hass.config_entries.async_get_entry("consumer-km")
    assert km_entry is not None
    hass.config_entries.async_update_entry(
        km_entry, data={**km_entry.data, "lockname": "Consumer Door"}
    )
    await hass.async_block_till_done()
    coordinator.get_slot_assignment = lambda identity: (
        _SLOT if identity == "consumer-reservation" else None
    )
    monkeypatch.setattr(
        verified_date,
        "make_reservation_fingerprint",
        lambda *_args: "consumer-reservation",
    )
    monkeypatch.setattr(
        runtime,
        "get_entry_data",
        lambda *_args: {EARLY_CHECKOUT_EXPIRY_SWITCH: SimpleNamespace(is_on=True)},
    )
    start = datetime.now(UTC)
    old_end = start + timedelta(days=3)
    replacement_start = start + timedelta(days=4)
    replacement_end = start + timedelta(days=7)
    entity = SimpleNamespace(
        _hass=hass,
        _config_entry=coordinator.config_entry,
        coordinator=coordinator,
        _tracked_event_slot_name="Guest",
        _tracked_event_start=start,
        _tracked_event_end=old_end,
    )
    overrides = coordinator.event_overrides
    async with async_slot_operation(coordinator, _SLOT):
        task = asyncio.create_task(runtime._maybe_shorten_lock_expiry(entity, start))

        async def wait_for_queued_checkout() -> None:
            """Wait until checkout is queued behind the current slot operation."""
            while overrides._slot_operations_pending.get(_SLOT, 0) != 2:
                await asyncio.sleep(0)

        await asyncio.wait_for(wait_for_queued_checkout(), timeout=1)
        entity._tracked_event_start = replacement_start
        entity._tracked_event_end = replacement_end

    await asyncio.wait_for(task, timeout=5)
    assert entity._tracked_event_start == replacement_start
    assert entity._tracked_event_end == replacement_end


async def test_early_checkout_confirms_its_own_date_feedback(
    hass, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The live feedback listener must not invalidate our confirmed expiry."""
    from dataclasses import replace
    from datetime import UTC
    from datetime import timedelta
    from unittest.mock import AsyncMock

    from homeassistant.helpers.event import async_track_state_change_event

    from custom_components.rental_control import util
    from custom_components.rental_control.coordinator import RentalControlCoordinator

    coordinator, _store, entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage", "v0.6.1"
    )
    km_entry = hass.config_entries.async_get_entry("consumer-km")
    assert km_entry is not None
    hass.config_entries.async_update_entry(
        km_entry, data={**km_entry.data, "lockname": "Consumer Door"}
    )
    await hass.async_block_till_done()
    coordinator.get_slot_assignment = lambda identity: (
        _SLOT if identity == "consumer-reservation" else None
    )
    monkeypatch.setattr(
        verified_date,
        "make_reservation_fingerprint",
        lambda *_args: "consumer-reservation",
    )
    coordinator.async_request_refresh = AsyncMock()
    coordinator.update_event_overrides = (
        RentalControlCoordinator.update_event_overrides.__get__(
            coordinator, RentalControlCoordinator
        )
    )
    now = datetime.now(UTC)
    start = now - timedelta(days=1)
    old_end = now + timedelta(days=2)
    new_end = now + timedelta(minutes=10)
    entity = SimpleNamespace(
        _hass=hass,
        _config_entry=coordinator.config_entry,
        coordinator=coordinator,
        _tracked_event_slot_name="Guest",
        _tracked_event_start=start,
        _tracked_event_end=old_end,
    )
    overrides = coordinator.event_overrides
    overrides.update(_SLOT, "2718", "Guest", start, old_end)
    end_id = entity_ids[f"datetime.code_slots:{_SLOT}.accesslimit_date_range_end"]
    for property_key, value in (
        (f"switch.code_slots:{_SLOT}.enabled", "on"),
        (f"switch.code_slots:{_SLOT}.accesslimit_date_range_enabled", "on"),
        (f"text.code_slots:{_SLOT}.name", "Guest"),
        (f"text.code_slots:{_SLOT}.pin", "2718"),
        (
            f"datetime.code_slots:{_SLOT}.accesslimit_date_range_start",
            start.isoformat(),
        ),
        (
            f"datetime.code_slots:{_SLOT}.accesslimit_date_range_end",
            old_end.isoformat(),
        ),
    ):
        hass.states.async_set(entity_ids[property_key], value)
    await hass.async_block_till_done()

    original_deps = util._state_deps()

    async def immediate_sleep(_delay: float) -> None:
        """Process the real listener without its settling delay."""

    monkeypatch.setattr(
        util, "_state_deps", lambda: replace(original_deps, sleep=immediate_sleep)
    )
    feedback_done = asyncio.Event()

    async def listener(event) -> None:
        """Run the actual registered state listener before the service returns."""
        await util.handle_state_change(hass, coordinator.config_entry, event)
        feedback_done.set()

    unsub = async_track_state_change_event(hass, [end_id], listener)

    async def set_datetime(call) -> None:
        """Publish the real state event and wait for its listener."""
        hass.states.async_set(call.data["entity_id"], call.data["datetime"])
        await asyncio.wait_for(feedback_done.wait(), timeout=2)

    hass.services.async_register("datetime", "set_value", set_datetime)
    try:
        assert await verified_date.async_update_verified_lock_code_expiry(
            entity, new_end, identity_end=old_end
        )
    finally:
        unsub()
    assert overrides.overrides[_SLOT]["end_time"] == new_end


async def test_early_checkout_ignores_pin_only_feedback_before_dispatch(
    hass, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A PIN-only feedback edit does not change which reservation is shortened."""
    from datetime import UTC
    from datetime import timedelta

    coordinator, store, entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage", "v0.6.1"
    )
    km_entry = hass.config_entries.async_get_entry("consumer-km")
    assert km_entry is not None
    hass.config_entries.async_update_entry(
        km_entry, data={**km_entry.data, "lockname": "Consumer Door"}
    )
    await hass.async_block_till_done()
    coordinator.get_slot_assignment = lambda identity: (
        _SLOT if identity == "consumer-reservation" else None
    )
    monkeypatch.setattr(
        verified_date,
        "make_reservation_fingerprint",
        lambda *_args: "consumer-reservation",
    )
    start = datetime.now(UTC)
    old_end = start + timedelta(days=3)
    new_end = start + timedelta(hours=1)
    overrides = coordinator.event_overrides
    overrides.update(_SLOT, "2718", "Guest", start, old_end)
    end_id = entity_ids[f"datetime.code_slots:{_SLOT}.accesslimit_date_range_end"]
    dispatches: list[dict[str, Any]] = []

    async def set_datetime(call) -> None:
        """Record and apply the requested expiry."""
        dispatches.append(dict(call.data))
        hass.states.async_set(call.data["entity_id"], call.data["datetime"])

    hass.services.async_register("datetime", "set_value", set_datetime)
    original_wait = store.async_wait_for_commit_completion
    wait_count = 0
    waiting = asyncio.Event()
    resume = asyncio.Event()

    async def hold_final_intent_check() -> bool:
        """Pause the intent check immediately before service dispatch."""
        nonlocal wait_count
        wait_count += 1
        if wait_count == 2:
            waiting.set()
            await resume.wait()
        return (await original_wait()) is True

    monkeypatch.setattr(
        store, "async_wait_for_commit_completion", hold_final_intent_check
    )
    entity = SimpleNamespace(
        _hass=hass,
        _config_entry=coordinator.config_entry,
        coordinator=coordinator,
        _tracked_event_slot_name="Guest",
        _tracked_event_start=start,
        _tracked_event_end=old_end,
    )
    task = asyncio.create_task(
        verified_date.async_update_verified_lock_code_expiry(
            entity, new_end, identity_end=old_end
        )
    )
    await asyncio.wait_for(waiting.wait(), timeout=1)
    generation = overrides.get_feedback_generation(_SLOT)
    assert await overrides.async_update(
        _SLOT,
        "9999",
        "Guest",
        start,
        old_end,
        merge_feedback=True,
        feedback_field="slot_code",
        feedback_generation=generation,
    )
    assert overrides.overrides[_SLOT]["slot_code"] == "9999"
    resume.set()

    assert await asyncio.wait_for(task, timeout=5)
    assert len(dispatches) == 1
    assert hass.states.get(end_id).state == new_end.isoformat()


@pytest.mark.parametrize("outcome", ["not_dispatched", "dispatch_failed"])
async def test_early_checkout_rolls_back_only_when_nothing_was_sent(
    hass, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    """Tracked expiry reverts only when no shortening reached the lock."""
    from datetime import UTC
    from datetime import timedelta

    from homeassistant.exceptions import HomeAssistantError

    coordinator, _store, _entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage", "v0.6.1"
    )
    km_entry = hass.config_entries.async_get_entry("consumer-km")
    assert km_entry is not None
    hass.config_entries.async_update_entry(
        km_entry, data={**km_entry.data, "lockname": "Consumer Door"}
    )
    await hass.async_block_till_done()
    assigned_slot = None if outcome == "not_dispatched" else _SLOT
    coordinator.get_slot_assignment = lambda identity: (
        assigned_slot if identity == "consumer-reservation" else None
    )
    monkeypatch.setattr(
        verified_date,
        "make_reservation_fingerprint",
        lambda *_args: "consumer-reservation",
    )
    monkeypatch.setattr(
        runtime,
        "get_entry_data",
        lambda *_args: {EARLY_CHECKOUT_EXPIRY_SWITCH: SimpleNamespace(is_on=True)},
    )
    dispatches: list[dict[str, Any]] = []

    async def set_datetime(call) -> None:
        """Record the dispatch, then fail as the provider would."""
        dispatches.append(dict(call.data))
        raise HomeAssistantError("lock rejected the write")

    hass.services.async_register("datetime", "set_value", set_datetime)
    now = datetime.now(UTC)
    start = now - timedelta(days=1)
    old_end = now + timedelta(days=3)
    new_end = runtime.compute_early_expiry_time(now, old_end)
    entity = SimpleNamespace(
        _hass=hass,
        _config_entry=coordinator.config_entry,
        coordinator=coordinator,
        _tracked_event_slot_name="Guest",
        _tracked_event_start=start,
        _tracked_event_end=old_end,
    )

    await runtime._maybe_shorten_lock_expiry(entity, now)

    if outcome == "not_dispatched":
        assert dispatches == []
        assert entity._tracked_event_end == old_end
    else:
        assert len(dispatches) == 1
        assert dispatches[0]["datetime"] == new_end.isoformat()
        assert entity._tracked_event_end == new_end
