# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Exercise verified intent acknowledgement through real HA consumers."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from dataclasses import replace
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from importlib import import_module
import logging
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
from typing import Any
from typing import cast
from unittest.mock import patch

from awesomeversion import AwesomeVersion
from homeassistant import loader
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import EntityPlatform
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.rental_control.allocator.allocator import DoorCodeAllocator
from custom_components.rental_control.allocator.association_transactions import (
    association_storage_record,
)
from custom_components.rental_control.allocator.associations import AssociationMode
from custom_components.rental_control.allocator.associations import AssociationTuple
from custom_components.rental_control.allocator.models import AllocationOrigin
from custom_components.rental_control.allocator.models import AllocationOwner
from custom_components.rental_control.allocator.models import AllocationRecord
from custom_components.rental_control.allocator.models import (
    ProtectionAuthorizationState,
)
from custom_components.rental_control.allocator.models import ProtectionLoadResult
from custom_components.rental_control.allocator.models import ProtectionLoadState
from custom_components.rental_control.allocator.protected_store import (
    ProtectedRegistryStore,
)
from custom_components.rental_control.allocator.registry import AllocationRegistry
from custom_components.rental_control.allocator.registry_commit import (
    async_commit_protection_transaction,
)
from custom_components.rental_control.allocator.store import code_ref_for
from custom_components.rental_control.allocator.store import serialize_registry_ledger
from custom_components.rental_control.const import ALLOCATOR
from custom_components.rental_control.const import DOMAIN
from custom_components.rental_control.const import PROTECTED_REGISTRY_STORE
from custom_components.rental_control.coordinator_helpers.coordinator_config_shell import (
    CoordinatorConfigMixin,
)
from custom_components.rental_control.coordinator_helpers.coordinator_setup_shell import (
    CoordinatorSetupMixin,
)
from custom_components.rental_control.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.rental_control.event_overrides import EventOverrides
from custom_components.rental_control.keymaster_identity import (
    build_capability_identity,
)
from custom_components.rental_control.keymaster_lifecycle import get_keymaster_lifecycle
from custom_components.rental_control.keymaster_lifecycle import (
    managed_slot_property_keys,
)
from custom_components.rental_control.keymaster_lifecycle import (
    start_keymaster_lifecycle,
)
from custom_components.rental_control.keymaster_lifecycle import (
    stop_keymaster_lifecycle,
)
from custom_components.rental_control.keymaster_operations import (
    async_prepare_verified_operation_intent,
)
from custom_components.rental_control.keymaster_operations import (
    create_verified_operation_intent,
)
from custom_components.rental_control.keymaster_slot_operations import _LOCKS_KEY
from custom_components.rental_control.keymaster_slot_operations import SlotOperationBusy
from custom_components.rental_control.keymaster_slot_operations import (
    async_keymaster_slot_operation,
)
from custom_components.rental_control.keymaster_topology import verified_slot_source
from custom_components.rental_control.keymaster_version import KeymasterVersionStatus
from custom_components.rental_control.keymaster_version import (
    diagnose_keymaster_version,
)

from tests.fixtures.keymaster_releases import artifact_paths
from tests.fixtures.keymaster_releases import import_keymaster_release
from tests.fixtures.keymaster_releases import remove_imported_package

_RELEASES = ("v0.6.1", "v0.6.5")
_RC_ENTRY_ID = "consumer-rc"
_KM_ENTRY_ID = "consumer-km"
_SLOT = 21
_OWNER_IDENTITY = "consumer-reservation"
_CODE = "2718"
_TX = "a" * 32
_ASSOCIATION_TX = "b" * 32


async def test_selected_keymaster_slot_lock_is_shared_and_cancellation_safe(
    hass,
) -> None:
    """Different RC owners share one KM slot lock; cancelled waiters unmark."""
    first_pending: dict[int, int] = {}
    second_pending: dict[int, int] = {}
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    second_entered = asyncio.Event()

    async def hold_first() -> None:
        """Hold the selected Keymaster slot until the test releases it."""
        async with async_keymaster_slot_operation(
            hass, _KM_ENTRY_ID, _SLOT, pending=first_pending
        ):
            first_entered.set()
            await release_first.wait()

    async def wait_from_other_rc() -> None:
        """Attempt the same Keymaster slot lock from another RC owner."""
        async with async_keymaster_slot_operation(
            hass, _KM_ENTRY_ID, _SLOT, pending=second_pending
        ):
            second_entered.set()

    first = asyncio.create_task(hold_first())
    await first_entered.wait()
    lock_states = hass.data[DOMAIN][_LOCKS_KEY]
    key = (_KM_ENTRY_ID, _SLOT)
    assert lock_states[key].claims == 1
    waiting = asyncio.create_task(wait_from_other_rc())
    await asyncio.sleep(0)
    assert second_pending == {_SLOT: 1}
    assert lock_states[key].claims == 2
    assert not second_entered.is_set()

    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert second_pending == {}
    assert lock_states[key].claims == 1
    assert not second_entered.is_set()

    replacement_waiter = asyncio.create_task(wait_from_other_rc())
    await asyncio.sleep(0)
    assert second_pending == {_SLOT: 1}
    assert lock_states[key].claims == 2
    release_first.set()
    await asyncio.gather(first, replacement_waiter)
    assert second_entered.is_set()
    assert first_pending == {}
    assert second_pending == {}
    assert key not in lock_states


async def test_same_task_slot_operation_is_reentrant_without_extra_claim(hass) -> None:
    """Nested operations in one task keep one lock claim and unwind cleanly."""
    pending: dict[int, int] = {}
    key = (_KM_ENTRY_ID, _SLOT)
    lock_states = hass.data.setdefault(DOMAIN, {}).setdefault(_LOCKS_KEY, {})

    async with async_keymaster_slot_operation(
        hass, _KM_ENTRY_ID, _SLOT, pending=pending
    ):
        state = lock_states[key]
        assert state.claims == 1
        assert pending == {_SLOT: 1}
        async with async_keymaster_slot_operation(
            hass, _KM_ENTRY_ID, _SLOT, pending=pending
        ):
            assert lock_states[key] is state
            assert state.claims == 1
            assert pending == {_SLOT: 2}
        assert state.claims == 1
        assert pending == {_SLOT: 1}

    assert pending == {}
    assert key not in lock_states


async def test_waiter_claim_blocks_transition_during_lock_handoff(hass, monkeypatch):
    """A queued task's reservation survives release-before-resume handoff."""
    holder_entered = asyncio.Event()
    release_holder = asyncio.Event()
    waiter_started = asyncio.Event()
    waiter_entered = asyncio.Event()
    release_waiter = asyncio.Event()
    handoff_started = asyncio.Event()
    transition_finished = asyncio.Event()
    transition_mutations: list[str] = []
    transition_observations: list[tuple[bool, int]] = []
    key = (_KM_ENTRY_ID, _SLOT)

    async def holder() -> None:
        """Own the slot until a queued waiter has reserved it."""
        async with async_keymaster_slot_operation(hass, _KM_ENTRY_ID, _SLOT):
            holder_entered.set()
            await release_holder.wait()

    async def queued_waiter() -> None:
        """Queue behind the holder and keep ownership after handoff."""
        waiter_started.set()
        async with async_keymaster_slot_operation(hass, _KM_ENTRY_ID, _SLOT):
            waiter_entered.set()
            await release_waiter.wait()

    holder_task = asyncio.create_task(holder())
    await holder_entered.wait()
    waiter_task = asyncio.create_task(queued_waiter())
    await waiter_started.wait()
    await asyncio.sleep(0)
    lock_states = hass.data[DOMAIN][_LOCKS_KEY]
    state = lock_states[key]
    assert state.claims == 2

    async def transition() -> None:
        """Attempt the allocator-side nonwaiting reservation at handoff."""
        await handoff_started.wait()
        try:
            async with async_keymaster_slot_operation(
                hass, _KM_ENTRY_ID, _SLOT, wait=False
            ):
                transition_mutations.append("must-not-run")
        except SlotOperationBusy:
            transition_observations.append((waiter_entered.is_set(), state.claims))
        finally:
            transition_finished.set()

    transition_task = asyncio.create_task(transition())
    target_lock = state.lock
    original_release = asyncio.Lock.release

    def release_with_handoff(lock: asyncio.Lock) -> None:
        """Schedule the transition before waking the queued lock waiter."""
        if lock is target_lock and not handoff_started.is_set():
            handoff_started.set()
        original_release(lock)

    monkeypatch.setattr(asyncio.Lock, "release", release_with_handoff)
    release_holder.set()
    await asyncio.wait_for(transition_finished.wait(), timeout=1)

    # The transition ran before the waiter resumed. Its synchronous reservation
    # check saw the queued claim and rejected without entering/mutating.
    assert transition_observations == [(False, 1)]
    assert transition_mutations == []
    assert waiter_entered.is_set()

    release_waiter.set()
    await asyncio.wait_for(asyncio.gather(holder_task, waiter_task, transition_task), 1)
    assert key not in lock_states


async def test_multislot_busy_unwinds_earlier_reservation(hass) -> None:
    """A later busy slot unwinds every earlier AsyncExitStack claim."""
    first_key = (_KM_ENTRY_ID, _SLOT)
    blocked_slot = _SLOT + 1
    blocked_key = (_KM_ENTRY_ID, blocked_slot)
    holder_entered = asyncio.Event()
    release_holder = asyncio.Event()

    async def hold_second_slot() -> None:
        """Keep the second physical slot unavailable."""
        async with async_keymaster_slot_operation(hass, _KM_ENTRY_ID, blocked_slot):
            holder_entered.set()
            await release_holder.wait()

    holder_task = asyncio.create_task(hold_second_slot())
    await holder_entered.wait()
    lock_states = hass.data[DOMAIN][_LOCKS_KEY]

    with pytest.raises(SlotOperationBusy):
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(
                async_keymaster_slot_operation(hass, _KM_ENTRY_ID, _SLOT)
            )
            assert lock_states[first_key].claims == 1
            await stack.enter_async_context(
                async_keymaster_slot_operation(
                    hass, _KM_ENTRY_ID, blocked_slot, wait=False
                )
            )

    assert first_key not in lock_states
    assert lock_states[blocked_key].claims == 1
    release_holder.set()
    await asyncio.wait_for(holder_task, 1)
    assert blocked_key not in lock_states


def _association() -> AssociationTuple:
    """Return one exact committed association for the synthetic entries."""
    return AssociationTuple(
        legacy_lock_selection_present=True,
        legacy_lock_selection_raw="lock.consumer",
        verified_keymaster_entry_id=_KM_ENTRY_ID,
        association_generation=1,
        association_transaction_id=_ASSOCIATION_TX,
        mode=AssociationMode.VERIFIED,
    )


async def _setup_verified_consumer(
    hass,
    storage: Path,
    release: str = "v0.6.1",
    *,
    parented: bool = False,
    shared_owner: bool = False,
    native_empty_text: bool = False,
    second_rc_entry_id: str | None = None,
):
    """Create real protected storage, allocator, lifecycle, and HA entities."""
    association = _association()
    rc_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Consumer test",
        entry_id=_RC_ENTRY_ID,
        data=association.merge_into_config({}),
    )
    second_rc_entry = None
    second_association = None
    if second_rc_entry_id is not None:
        second_association = AssociationTuple(
            legacy_lock_selection_present=association.legacy_lock_selection_present,
            legacy_lock_selection_raw=association.legacy_lock_selection_raw,
            verified_keymaster_entry_id=association.verified_keymaster_entry_id,
            association_generation=1,
            association_transaction_id="2" * 32,
            mode=AssociationMode.VERIFIED,
        )
        second_rc_entry = MockConfigEntry(
            domain=DOMAIN,
            title="Second consumer test",
            entry_id=second_rc_entry_id,
            data=second_association.merge_into_config({}),
        )
    km_data = {"lock_entity_id": "lock.selected_physical_lock"}
    if parented:
        parent_entry = MockConfigEntry(
            domain="keymaster",
            title="Parent lock",
            entry_id="consumer-km-parent",
            data={"lock_entity_id": "lock.parent_physical_lock"},
        )
        parent_entry.add_to_hass(hass)
        parent_entry._async_set_state(hass, ConfigEntryState.LOADED, None)
        km_data["parent_entry_id"] = parent_entry.entry_id
    km_entry = MockConfigEntry(
        domain="keymaster",
        title="Selected lock",
        entry_id=_KM_ENTRY_ID,
        data=km_data,
    )
    rc_entry.add_to_hass(hass)
    if second_rc_entry is not None:
        second_rc_entry.add_to_hass(hass)
        second_rc_entry._async_set_state(hass, ConfigEntryState.LOADED, None)
    km_entry.add_to_hass(hass)
    km_entry._async_set_state(hass, ConfigEntryState.LOADED, None)

    registry = AllocationRegistry(code_ref_salt="persisted-test-reference-salt")
    owner = AllocationOwner(
        entry_id=_RC_ENTRY_ID,
        identity_key=_OWNER_IDENTITY,
        origin=AllocationOrigin.ADOPTED,
        lockname="lock.consumer",
        slot=_SLOT,
        lock_observed=True,
        first_seen="2026-11-21T00:00:00+00:00",
        last_seen="2026-11-21T00:00:00+00:00",
    )
    code_reference = code_ref_for(_CODE, registry.code_ref_salt)
    registry.records[_CODE] = AllocationRecord(
        code=_CODE,
        code_ref=code_reference,
        encoding_salt_value=_RC_ENTRY_ID,
        code_length=len(_CODE),
        owners=[owner],
        created_at="2026-11-21T00:00:00+00:00",
        updated_at="2026-11-21T00:00:00+00:00",
    )
    if second_rc_entry_id is not None:
        registry.records[_CODE].owners.append(
            AllocationOwner(
                entry_id=second_rc_entry_id,
                identity_key="consumer-second-active-owner",
                origin=AllocationOrigin.ADOPTED,
                lockname="lock.consumer",
                slot=_SLOT + 2,
                lock_observed=True,
                first_seen="2026-11-21T00:00:00+00:00",
                last_seen="2026-11-21T00:00:00+00:00",
            )
        )
    if shared_owner:
        registry.records[_CODE].owners.append(
            AllocationOwner(
                entry_id=_RC_ENTRY_ID,
                identity_key="consumer-reservation-adopted",
                origin=AllocationOrigin.ADOPTED,
                lockname="lock.consumer",
                slot=_SLOT + 1,
                lock_observed=True,
                first_seen="2026-11-21T00:00:00+00:00",
                last_seen="2026-11-21T00:00:00+00:00",
            )
        )
    registry.rebuild_index()
    data: dict[str, Any] = {
        "schema_version": 2,
        "transaction_id": _TX,
        "generation": 1,
        "ledger": serialize_registry_ledger(registry),
        "associations": [
            association_storage_record(_RC_ENTRY_ID, association),
            *(
                [association_storage_record(second_rc_entry_id, second_association)]
                if second_rc_entry_id is not None and second_association is not None
                else []
            ),
        ],
        "attestations": [],
        "pending_fences": [],
        "holds": [],
        "tombstones": [],
        "pending_config": [],
        "exclusions": [],
    }

    storage.mkdir(parents=True, exist_ok=True)
    lock = asyncio.Lock()
    initial = await async_commit_protection_transaction(
        lock,
        storage,
        {
            "version": 2,
            "minor_version": 1,
            "key": "rental_control.code_registry",
            "data": data,
        },
    )
    assert initial.state is ProtectionLoadState.VALID_V2
    store = ProtectedRegistryStore(hass, lock, storage_dir=storage)
    loaded = await store.async_load()
    assert loaded.state is ProtectionLoadState.VALID_V2

    allocator = DoorCodeAllocator(hass)
    allocator._protection_required = True
    allocator._lock = lock
    allocator._registry = registry
    domain_data = hass.data.setdefault(DOMAIN, {})
    domain_data[PROTECTED_REGISTRY_STORE] = store
    domain_data[ALLOCATOR] = allocator

    overrides = EventOverrides(_SLOT, 1)
    overrides._overrides[_SLOT] = overrides._override_dict(
        _CODE,
        "Guest",
        datetime.now(UTC) + timedelta(hours=1),
        datetime.now(UTC) + timedelta(hours=2),
    )
    coordinator = SimpleNamespace(
        hass=hass,
        config_entry=rc_entry,
        _entry_id=_RC_ENTRY_ID,
        start_slot=_SLOT,
        max_events=1,
        event_overrides=overrides,
        lockname=None,
        event_prefix=None,
        trim_names=False,
        max_name_length=32,
        code_buffer_before=0,
        code_buffer_after=0,
        name="Consumer test",
        timezone=UTC,
    )
    domain_data[_RC_ENTRY_ID] = {"coordinator": coordinator}

    entity_ids: dict[str, str] = {}
    module_name = "custom_components.keymaster"
    assert f"keymaster-{release}" in artifact_paths()
    assert module_name not in sys.modules
    import_keymaster_release(release, storage.parent / release, module_name)
    try:
        for property_key in managed_slot_property_keys(_SLOT, 1):
            if native_empty_text and property_key.startswith("text."):
                continue
            identity = build_capability_identity(
                _KM_ENTRY_ID, property_key, generation=1
            )
            assert identity is not None
            assert identity.unique_id == _upstream_unique_id(
                hass, km_entry, property_key, module_name
            )
            registry_entry = er.async_get(hass).async_get_or_create(
                identity.domain,
                identity.platform,
                identity.unique_id,
                suggested_object_id=f"renamed_{property_key.replace(':', '_').replace('.', '_')}",
                config_entry=km_entry,
            )
            if identity.domain == "text":
                initial_state = ""
            elif identity.domain == "switch":
                initial_state = "off"
            else:
                initial_state = "ready"
            hass.states.async_set(registry_entry.entity_id, initial_state)
            entity_ids[property_key] = registry_entry.entity_id
        if native_empty_text:
            entity_ids.update(
                await _add_real_native_empty_text_entities(
                    hass, km_entry, _SLOT, sys.modules[module_name]
                )
            )
    finally:
        remove_imported_package(module_name)

    start_keymaster_lifecycle(hass, rc_entry, coordinator)
    await hass.async_block_till_done()
    return coordinator, store, entity_ids


async def _add_real_native_empty_text_entities(
    hass, config_entry, slot: int, keymaster_module
) -> dict[str, str]:
    """Add actual packaged Keymaster Text entities publishing native ``None``."""
    lock = SimpleNamespace(
        lock_name="Selected lock",
        connected=True,
        parent_name=None,
        code_slots={slot: SimpleNamespace(name=None, pin=None, override_parent=True)},
    )
    provider_coordinator = DataUpdateCoordinator(
        hass,
        logging.getLogger(__name__),
        config_entry=config_entry,
        name=f"Keymaster fixture {config_entry.entry_id}",
    )
    cast(Any, provider_coordinator).sync_get_lock_by_config_entry_id = (
        lambda _entry_id: lock
    )
    provider_coordinator.async_set_updated_data({})
    text_module = import_module(f"{keymaster_module.__name__}.text")
    platform = EntityPlatform(
        hass=hass,
        logger=logging.getLogger(__name__),
        domain="text",
        platform_name="keymaster",
        platform=text_module,
        scan_interval=timedelta(seconds=30),
        entity_namespace=None,
    )
    platform.config_entry = config_entry
    property_keys = (
        f"text.code_slots:{slot}.name",
        f"text.code_slots:{slot}.pin",
    )
    entities = [
        text_module.KeymasterText(
            entity_description=text_module.KeymasterTextEntityDescription(
                hass=hass,
                config_entry=config_entry,
                coordinator=provider_coordinator,
                key=property_key,
                name=f"Native empty {property_key.rsplit('.', maxsplit=1)[-1]}",
                icon="mdi:form-textbox-lock",
                entity_registry_enabled_default=True,
            )
        )
        for property_key in property_keys
    ]
    await platform.async_add_entities(entities)
    await hass.async_block_till_done()
    entity_ids = {
        property_key: entity.entity_id
        for property_key, entity in zip(property_keys, entities, strict=True)
    }
    for entity in entities:
        assert entity.available
        assert entity.native_value is None
        state = hass.states.get(entity.entity_id)
        assert state is not None
        assert state.state == "unknown"
        assert state.attributes.get("restored") is not True
        assert state.attributes.get("masked") is not True
    return entity_ids


def _upstream_unique_id(hass, entry, property_key: str, module_name: str) -> str:
    """Return a unique ID generated by one pinned upstream Keymaster package."""
    domain, _ = property_key.split(".", maxsplit=1)
    class_name = {
        "button": "Button",
        "datetime": "DateTime",
        "switch": "Switch",
        "text": "Text",
    }[domain]
    platform = import_module(f"{module_name}.{domain}")
    base = import_module(f"{module_name}.entity")
    description = getattr(platform, f"Keymaster{class_name}EntityDescription")(
        hass=hass,
        config_entry=entry,
        coordinator=SimpleNamespace(sync_get_lock_by_config_entry_id=lambda _id: None),
        key=property_key,
        name="Synthetic property",
    )
    with patch.object(base.CoordinatorEntity, "__init__", lambda *args: None):
        entity = getattr(platform, f"Keymaster{class_name}")(description)
    return str(entity.unique_id)


async def _register_recording_services(
    hass,
    store,
    entity_ids: dict[str, str],
    events: list[dict[str, Any]],
    *,
    reset_clears_state: bool = True,
):
    """Register local HA services that record target identity and durable state."""

    def service_handler(domain: str, service: str):
        """Create a recorder for one synthetic Home Assistant service."""

        async def handle(call) -> None:
            """Record the exact target and emulate the provider state update."""
            entity_id = call.data["entity_id"]
            loaded = store.loaded_result
            assert loaded is not None and loaded.data is not None
            fences = loaded.data["pending_fences"]
            expected_type = "pending_clear" if service == "press" else "allocation"
            assert any(item["fence_type"] == expected_type for item in fences)
            events.append(
                {
                    "domain": domain,
                    "service": service,
                    "entity_id": entity_id,
                    "fences": tuple(item["fence_type"] for item in fences),
                }
            )
            if domain == "text":
                hass.states.async_set(entity_id, call.data["value"])
            elif domain == "switch":
                hass.states.async_set(
                    entity_id, "on" if service == "turn_on" else "off"
                )
            elif domain == "datetime":
                hass.states.async_set(entity_id, call.data["datetime"].isoformat())
            elif domain == "button":
                if reset_clears_state:
                    for property_key in (
                        f"text.code_slots:{_SLOT}.name",
                        f"text.code_slots:{_SLOT}.pin",
                    ):
                        hass.states.async_set(entity_ids[property_key], "")

        return handle

    for domain, service in (
        ("switch", "turn_off"),
        ("switch", "turn_on"),
        ("datetime", "set_value"),
        ("text", "set_value"),
        ("button", "press"),
    ):
        hass.services.async_register(domain, service, service_handler(domain, service))


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize("release", _RELEASES)
async def test_real_set_and_clear_consumers_acknowledge_before_exact_dispatch(
    hass,
    tmp_path: Path,
    release: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Set/reset calls use renamed exact entities only after durable fences."""
    from dataclasses import replace

    from custom_components.rental_control import util

    coordinator, store, entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage", release
    )
    events: list[dict[str, Any]] = []
    await _register_recording_services(hass, store, entity_ids, events)

    original_deps = util._keymaster_deps

    async def immediate_sleep(_delay: float) -> None:
        """Skip retry delay in this deterministic consumer test."""
        return None

    monkeypatch.setattr(
        util,
        "_keymaster_deps",
        lambda: replace(original_deps(), sleep=immediate_sleep),
    )

    overrides = coordinator.event_overrides
    reservation = SimpleNamespace(
        identity_key=_OWNER_IDENTITY,
        slot_name="Guest",
        slot_code=_CODE,
        start=datetime.now(UTC) + timedelta(hours=1),
        end=datetime.now(UTC) + timedelta(hours=2),
        buffered_start=datetime.now(UTC) + timedelta(hours=1),
        buffered_end=datetime.now(UTC) + timedelta(hours=2),
        display_slot_name="Guest",
    )

    set_result = await overrides._apply_set(coordinator, _SLOT, reservation, "set-1")

    assert set_result.confirmed
    assert events
    assert all(event["entity_id"] in set(entity_ids.values()) for event in events)
    assert events[0]["fences"] == ("allocation",)
    assert events[0]["entity_id"] == entity_ids[f"switch.code_slots:{_SLOT}.enabled"]
    loaded = store.loaded_result
    assert loaded is not None and loaded.data is not None
    assert any(
        fence["fence_type"] == "allocation" for fence in loaded.data["pending_fences"]
    )

    previous_event_count = len(events)
    updated_start = datetime.now(UTC) + timedelta(hours=3)
    updated_end = datetime.now(UTC) + timedelta(hours=4)
    update_result = await overrides._apply_update_times(
        coordinator,
        _SLOT,
        SimpleNamespace(
            identity_key=_OWNER_IDENTITY,
            slot_name="Guest",
            slot_code=_CODE,
            start=updated_start,
            end=updated_end,
            buffered_start=updated_start,
            buffered_end=updated_end,
        ),
    )

    assert update_result.confirmed
    date_events = events[previous_event_count:]
    assert {event["entity_id"] for event in date_events} == {
        entity_ids[f"datetime.code_slots:{_SLOT}.accesslimit_date_range_start"],
        entity_ids[f"datetime.code_slots:{_SLOT}.accesslimit_date_range_end"],
    }
    assert all(event["fences"] == ("allocation",) for event in date_events)

    previous_event_count = len(events)
    coordinator.code_buffer_before = 10
    coordinator.code_buffer_after = 20
    coordinator._slot_mappings = {"mappings": {_OWNER_IDENTITY: {"slot": _SLOT}}}
    await CoordinatorConfigMixin._async_update_buffer_times(
        coordinator, old_before=0, old_after=0
    )
    buffered_events = events[previous_event_count:]
    assert {event["entity_id"] for event in buffered_events} == {
        entity_ids[f"datetime.code_slots:{_SLOT}.accesslimit_date_range_start"],
        entity_ids[f"datetime.code_slots:{_SLOT}.accesslimit_date_range_end"],
    }
    assert all(event["fences"] == ("allocation",) for event in buffered_events)
    assert (
        hass.states.get(
            entity_ids[f"datetime.code_slots:{_SLOT}.accesslimit_date_range_start"]
        ).state
        == (updated_start - timedelta(minutes=10)).isoformat()
    )
    assert (
        hass.states.get(
            entity_ids[f"datetime.code_slots:{_SLOT}.accesslimit_date_range_end"]
        ).state
        == (updated_end + timedelta(minutes=20)).isoformat()
    )

    clear_result = await overrides._apply_clear(
        coordinator,
        _SLOT,
        owner_identity_key=_OWNER_IDENTITY,
    )

    assert clear_result.confirmed
    assert events[-1]["domain"] == "button"
    assert events[-1]["entity_id"] == entity_ids[f"button.code_slots:{_SLOT}.reset"]
    assert events[-1]["fences"] == ("allocation", "pending_clear")
    loaded = store.loaded_result
    assert loaded is not None and loaded.data is not None
    assert all(
        fence["fence_type"] != "pending_clear"
        for fence in loaded.data["pending_fences"]
    )
    assert any(
        tombstone["resolution_kind"] == "clear_confirmed"
        for tombstone in loaded.data["tombstones"]
    )

    # A confirmed hardware reset is not enough to release the durable
    # allocation guard. Remove the exact owner only through the allocator's
    # normal release guard, fed by the live exact-slot Keymaster state.
    from custom_components.rental_control.allocator.models import CycleObservation
    from custom_components.rental_control.allocator.registry_snapshot import (
        SnapshotCommitHooks,
    )
    from custom_components.rental_control.allocator.registry_snapshot import (
        async_commit_registry_snapshot,
    )
    from custom_components.rental_control.allocator.registry_snapshot import (
        serialize_exclusion_snapshots,
    )
    from custom_components.rental_control.allocator.singleton import get_allocator
    from custom_components.rental_control.keymaster_operations import (
        _current_exclusion_snapshot,
    )

    allocator = get_allocator(hass)
    assert allocator is not None
    exclusions = serialize_exclusion_snapshots(
        hass.config_entries.async_entries(DOMAIN)
    )

    def authorize_release(baseline, _candidate) -> bool:
        """Use the allocator's live slot observation to authorize release."""
        record = baseline.record_for_identity(_OWNER_IDENTITY)
        if record is None:
            return False
        owner = next(
            (item for item in record.owners if item.identity_key == _OWNER_IDENTITY),
            None,
        )
        if owner is None:
            return False
        pin_state = hass.states.get(entity_ids[f"text.code_slots:{_SLOT}.pin"])
        unreadable = (
            frozenset({_SLOT})
            if pin_state is None or pin_state.state in {"unknown", "unavailable"}
            else frozenset()
        )
        observed_codes = (
            {pin_state.state: _SLOT}
            if pin_state is not None
            and pin_state.state not in {"", "unknown", "unavailable"}
            else {}
        )
        lifecycle = get_keymaster_lifecycle(hass, _RC_ENTRY_ID)
        assert lifecycle is not None
        observation = CycleObservation(
            entry_id=_RC_ENTRY_ID,
            lockname=owner.lockname,
            managed_slots=frozenset({_SLOT}),
            observed_codes=observed_codes,
            unreadable_slots=unreadable,
            keymaster_entry_id=lifecycle.keymaster_entry_id,
            keymaster_generation=lifecycle.generation,
        )
        return allocator._release_guard_reason(record, [owner], [observation]) is None

    def release_owner(candidate) -> None:
        """Remove only the verified original owner from the staged model."""
        candidate.release(_OWNER_IDENTITY)

    release_result = await async_commit_registry_snapshot(
        allocator,
        store,
        release_owner,
        exclusions,
        lambda: _current_exclusion_snapshot(hass, serialize_exclusion_snapshots),
        hooks=SnapshotCommitHooks(authorize_owner_transition=authorize_release),
    )
    assert release_result.state is ProtectionLoadState.VALID_V2
    loaded = store.loaded_result
    assert loaded is not None and loaded.data is not None
    assert loaded.data["pending_fences"] == []
    assert any(
        tombstone["resolution_kind"] == "owner_released"
        and tombstone["fence_type"] == "allocation"
        for tombstone in loaded.data["tombstones"]
    )

    # A new owner may reuse the physical slot only after its own protected
    # allocation and exact new-generation fence have been acknowledged.
    from custom_components.rental_control.allocator.association_references import (
        ledger_allocation_references,
    )
    from custom_components.rental_control.allocator.models import AllocationOrigin
    from custom_components.rental_control.allocator.models import AllocationRequest
    from custom_components.rental_control.allocator.registry_safety_models import (
        PendingSlotFence,
    )
    from custom_components.rental_control.allocator.store import code_ref_for

    next_owner = "consumer-reservation-next"
    next_code = "8346"

    def allocate_next(candidate) -> None:
        """Stage a separate owner after guarded release of the original."""
        candidate.allocate(
            AllocationRequest(
                entry_id=_RC_ENTRY_ID,
                identity_key=next_owner,
                preferred_code=next_code,
                code_length=len(next_code),
                lockname="lock.consumer",
                slot=_SLOT,
            ),
            next_code,
            AllocationOrigin.PREFERRED,
            code_ref_for(next_code, allocator._registry.code_ref_salt),
            datetime.now(UTC).isoformat(),
        )

    def append_next_owner_fence(
        _candidate,
        data,
        transaction_id: str,
        generation: int,
    ) -> None:
        """Bind the new owner fence to the staged ledger transaction."""
        references = ledger_allocation_references(data, _RC_ENTRY_ID, next_owner)
        assert references is not None
        owner_reference, allocation_reference, owner_slot = references
        data["pending_fences"].append(
            PendingSlotFence(
                fence_id="c" * 32,
                original_km_entry_id=_KM_ENTRY_ID,
                slot=owner_slot,
                rc_entry_id=_RC_ENTRY_ID,
                owner_ref=owner_reference,
                allocation_ref=allocation_reference,
                created_transaction_id=transaction_id,
                created_generation=generation,
                fence_type="allocation",
            ).to_record()
        )

    next_result = await async_commit_registry_snapshot(
        allocator,
        store,
        allocate_next,
        exclusions,
        lambda: _current_exclusion_snapshot(hass, serialize_exclusion_snapshots),
        hooks=SnapshotCommitHooks(safety_transition=append_next_owner_fence),
    )
    assert next_result.state is ProtectionLoadState.VALID_V2
    loaded = store.loaded_result
    assert loaded is not None and loaded.data is not None
    next_references = ledger_allocation_references(
        loaded.data, _RC_ENTRY_ID, next_owner
    )
    assert next_references is not None
    overrides._overrides[_SLOT] = overrides._override_dict(
        next_code,
        "Next Guest",
        datetime.now(UTC) + timedelta(hours=3),
        datetime.now(UTC) + timedelta(hours=4),
    )
    next_now = datetime.now(UTC)
    reuse_result = await overrides._apply_set(
        coordinator,
        _SLOT,
        SimpleNamespace(
            identity_key=next_owner,
            slot_name="Next Guest",
            slot_code=next_code,
            start=next_now + timedelta(hours=3),
            end=next_now + timedelta(hours=4),
            buffered_start=next_now + timedelta(hours=3),
            buffered_end=next_now + timedelta(hours=4),
            display_slot_name="Next Guest",
        ),
        "set-after-guarded-release",
    )
    assert reuse_result.confirmed
    loaded = store.loaded_result
    assert loaded is not None and loaded.data is not None
    assert any(
        fence["fence_type"] == "allocation" and fence["owner_ref"] == next_references[0]
        for fence in loaded.data["pending_fences"]
    )


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize("release", _RELEASES)
@pytest.mark.parametrize(
    ("reported_version", "expected_status"),
    [
        ("v0.6.0", KeymasterVersionStatus.OUTSIDE_SUPPORT),
        ("v0.6.1+local", KeymasterVersionStatus.UNKNOWN),
    ],
)
async def test_version_diagnosis_alone_does_not_block_real_set_consumer(
    hass,
    tmp_path: Path,
    release: str,
    reported_version: str,
    expected_status: KeymasterVersionStatus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The actual supported package consumer still uses independent gates."""
    from dataclasses import replace

    from custom_components.rental_control import util

    coordinator, store, entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage", release
    )
    events: list[dict[str, Any]] = []
    await _register_recording_services(hass, store, entity_ids, events)
    monkeypatch.setattr(
        loader,
        "async_get_loaded_integration",
        lambda _hass, _domain: SimpleNamespace(
            manifest={"version": reported_version},
            version=AwesomeVersion(reported_version),
        ),
    )
    assert diagnose_keymaster_version(hass).status is expected_status

    original_deps = util._keymaster_deps

    async def immediate_sleep(_delay: float) -> None:
        """Skip retry delay in the real consumer test."""
        return None

    monkeypatch.setattr(
        util,
        "_keymaster_deps",
        lambda: replace(original_deps(), sleep=immediate_sleep),
    )
    now = datetime.now(UTC)
    reservation = SimpleNamespace(
        identity_key=_OWNER_IDENTITY,
        slot_name="Guest",
        slot_code=_CODE,
        start=now + timedelta(hours=1),
        end=now + timedelta(hours=2),
        buffered_start=now + timedelta(hours=1),
        buffered_end=now + timedelta(hours=2),
        display_slot_name="Guest",
    )

    result = await coordinator.event_overrides._apply_set(
        coordinator, _SLOT, reservation, "version-diagnostic-only"
    )

    assert result.confirmed
    assert events
    assert all(event["entity_id"] in set(entity_ids.values()) for event in events)


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize("release", _RELEASES)
@pytest.mark.parametrize(
    ("reported_version", "expected_status"),
    [
        ("v0.6.0", KeymasterVersionStatus.OUTSIDE_SUPPORT),
        ("v0.6.1+local", KeymasterVersionStatus.UNKNOWN),
    ],
)
async def test_version_diagnosis_cannot_bypass_protective_unknown(
    hass,
    tmp_path: Path,
    release: str,
    reported_version: str,
    expected_status: KeymasterVersionStatus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unverified store history blocks the real consumer at every version."""
    monkeypatch.setattr(
        loader,
        "async_get_loaded_integration",
        lambda _hass, _domain: SimpleNamespace(
            manifest={"version": reported_version},
            version=AwesomeVersion(reported_version),
        ),
    )
    assert diagnose_keymaster_version(hass).status is expected_status

    coordinator, store, entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage", release
    )
    events: list[dict[str, Any]] = []
    await _register_recording_services(hass, store, entity_ids, events)
    store._enter_protective_unknown(
        ProtectionLoadResult(
            ProtectionLoadState.PROTECTIVE_UNKNOWN,
            failure_code="external_restore_suspected",
        )
    )
    now = datetime.now(UTC)
    reservation = SimpleNamespace(
        identity_key=_OWNER_IDENTITY,
        slot_name="Guest",
        slot_code=_CODE,
        start=now + timedelta(hours=1),
        end=now + timedelta(hours=2),
        buffered_start=now + timedelta(hours=1),
        buffered_end=now + timedelta(hours=2),
        display_slot_name="Guest",
    )

    result = await coordinator.event_overrides._apply_set(
        coordinator, _SLOT, reservation, "unknown-protection"
    )

    assert not result.confirmed
    assert not events


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize("release", _RELEASES)
async def test_keymaster_diagnostics_separate_identity_capability_and_version(
    hass,
    tmp_path: Path,
    release: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Diagnostics expose only status counts and actionable fixed guidance."""
    coordinator, _store, entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage", release
    )
    monkeypatch.setattr(
        loader,
        "async_get_loaded_integration",
        lambda _hass, _domain: SimpleNamespace(
            manifest={"version": "v0.6.1+local"},
            version=AwesomeVersion("v0.6.1+local"),
        ),
    )

    ready = await async_get_config_entry_diagnostics(hass, coordinator.config_entry)
    keymaster_status = ready["keymaster_status"]
    assert keymaster_status["association"] == {
        "status": "verified",
        "next_step": "wait_for_keymaster_entities",
    }
    assert keymaster_status["capabilities"]["status_counts"]["ready"] > 0
    assert keymaster_status["capabilities"]["next_step"] == "none"
    assert keymaster_status["version"]["status"] == "unknown"
    assert keymaster_status["version"]["informational_only"] is True
    assert keymaster_status["protection"]["status"] == "ready"
    assert _KM_ENTRY_ID not in repr(keymaster_status)
    assert all(
        entity_id not in repr(keymaster_status) for entity_id in entity_ids.values()
    )

    er.async_get(hass).async_update_entity(
        entity_ids[f"text.code_slots:{_SLOT}.pin"],
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    await hass.async_block_till_done()
    blocked = await async_get_config_entry_diagnostics(hass, coordinator.config_entry)
    blocked_status = blocked["keymaster_status"]["capabilities"]
    assert blocked_status["status_counts"]["disabled"] == 1
    assert blocked_status["next_step"] == "enable_required_keymaster_entity"


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize("release", _RELEASES)
async def test_verified_set_accepts_loaded_native_none_text_entities(
    hass,
    tmp_path: Path,
    release: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Set into live native-None entities using the package's real Text class."""
    from dataclasses import replace

    from custom_components.rental_control import util

    coordinator, store, entity_ids = await _setup_verified_consumer(
        hass,
        tmp_path / ".storage",
        release,
        native_empty_text=True,
    )
    events: list[dict[str, Any]] = []
    await _register_recording_services(hass, store, entity_ids, events)
    original_deps = util._keymaster_deps

    async def immediate_sleep(_delay: float) -> None:
        """Skip retry waiting while running the actual set consumer."""
        return None

    monkeypatch.setattr(
        util,
        "_keymaster_deps",
        lambda: replace(original_deps(), sleep=immediate_sleep),
    )
    now = datetime.now(UTC)
    reservation = SimpleNamespace(
        identity_key=_OWNER_IDENTITY,
        slot_name="Guest",
        slot_code=_CODE,
        start=now + timedelta(hours=1),
        end=now + timedelta(hours=2),
        buffered_start=now + timedelta(hours=1),
        buffered_end=now + timedelta(hours=2),
        display_slot_name="Guest",
    )

    result = await coordinator.event_overrides._apply_set(
        coordinator, _SLOT, reservation, f"native-none-{release}"
    )

    lifecycle = get_keymaster_lifecycle(hass, _RC_ENTRY_ID)
    assert lifecycle is not None
    assert result.confirmed, (
        f"result={result!r} events={events!r} "
        f"current={dict(lifecycle.current)!r} "
        f"pending={coordinator.event_overrides._pending_fences!r}"
    )
    assert events
    assert events[0]["fences"] == ("allocation",)
    assert all(event["entity_id"] in set(entity_ids.values()) for event in events)
    assert hass.states.get(entity_ids[f"text.code_slots:{_SLOT}.name"]).state == "Guest"
    assert hass.states.get(entity_ids[f"text.code_slots:{_SLOT}.pin"]).state == _CODE
    assert _CODE not in caplog.text


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize("release", _RELEASES)
@pytest.mark.parametrize(
    ("invalid_state", "attributes"),
    (
        ("unavailable", {}),
        ("unknown", {"restored": True}),
        ("unknown", {"masked": True}),
        ("[REDACTED]", {}),
    ),
)
async def test_verified_set_rejects_nonlive_text_capabilities(
    hass,
    tmp_path: Path,
    release: str,
    invalid_state: str,
    attributes: dict[str, bool],
) -> None:
    """Unavailability, restoration, and synthetic masking cannot authorize set."""
    coordinator, store, entity_ids = await _setup_verified_consumer(
        hass,
        tmp_path / ".storage",
        release,
        native_empty_text=True,
    )
    events: list[dict[str, Any]] = []
    await _register_recording_services(hass, store, entity_ids, events)
    for property_name in ("name", "pin"):
        hass.states.async_set(
            entity_ids[f"text.code_slots:{_SLOT}.{property_name}"],
            invalid_state,
            attributes=attributes,
        )
    await hass.async_block_till_done()
    now = datetime.now(UTC)

    result = await coordinator.event_overrides._apply_set(
        coordinator,
        _SLOT,
        SimpleNamespace(
            identity_key=_OWNER_IDENTITY,
            slot_name="Guest",
            slot_code=_CODE,
            start=now + timedelta(hours=1),
            end=now + timedelta(hours=2),
            buffered_start=now + timedelta(hours=1),
            buffered_end=now + timedelta(hours=2),
            display_slot_name="Guest",
        ),
        f"invalid-native-none-{release}-{invalid_state}",
    )

    assert result.unconfirmed
    assert events == []
    loaded = store.loaded_result
    assert loaded is not None and loaded.data is not None
    assert loaded.data["pending_fences"] == []


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize("release", _RELEASES)
async def test_native_none_set_stops_after_lifecycle_generation_changes(
    hass,
    tmp_path: Path,
    release: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A stale native-empty set intent cannot continue after Keymaster reload."""
    from dataclasses import replace

    from custom_components.rental_control import util

    coordinator, store, entity_ids = await _setup_verified_consumer(
        hass,
        tmp_path / ".storage",
        release,
        native_empty_text=True,
    )
    events: list[dict[str, Any]] = []
    await _register_recording_services(hass, store, entity_ids, events)
    original_deps = util._keymaster_deps

    async def immediate_sleep(_delay: float) -> None:
        """Skip retry waiting while exercising the lifecycle fence."""
        return None

    monkeypatch.setattr(
        util,
        "_keymaster_deps",
        lambda: replace(original_deps(), sleep=immediate_sleep),
    )
    service_registry_type = type(hass.services)
    original_async_call = service_registry_type.async_call
    invalidated = False

    async def reload_after_first_call(service_registry, *args, **kwargs):
        """Rebind after the first real targeted service call."""
        nonlocal invalidated
        result = await original_async_call(service_registry, *args, **kwargs)
        if (
            not invalidated
            and kwargs.get("domain") == "switch"
            and kwargs.get("service") == "turn_off"
        ):
            invalidated = True
            stop_keymaster_lifecycle(hass, _RC_ENTRY_ID)
            start_keymaster_lifecycle(hass, coordinator.config_entry, coordinator)
            await hass.async_block_till_done()
        return result

    monkeypatch.setattr(service_registry_type, "async_call", reload_after_first_call)
    now = datetime.now(UTC)
    result = await coordinator.event_overrides._apply_set(
        coordinator,
        _SLOT,
        SimpleNamespace(
            identity_key=_OWNER_IDENTITY,
            slot_name="Guest",
            slot_code=_CODE,
            start=now + timedelta(hours=1),
            end=now + timedelta(hours=2),
            buffered_start=now + timedelta(hours=1),
            buffered_end=now + timedelta(hours=2),
            display_slot_name="Guest",
        ),
        f"stale-native-none-{release}",
    )

    assert result.unconfirmed
    assert invalidated
    assert len(events) == 1
    assert events[0]["fences"] == ("allocation",)
    assert _CODE not in caplog.text
    loaded = store.loaded_result
    assert loaded is not None and loaded.data is not None
    assert any(
        fence["fence_type"] == "allocation" for fence in loaded.data["pending_fences"]
    )


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize("release", _RELEASES)
async def test_t062_missing_store_never_falls_back_from_verified_set(
    hass,
    tmp_path: Path,
    release: str,
) -> None:
    """Activation regression: missing protected store cannot enable legacy set."""
    coordinator, store, entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage", release
    )
    events: list[dict[str, Any]] = []
    await _register_recording_services(hass, store, entity_ids, events)
    coordinator.lockname = "legacy_name_must_not_be_used"
    hass.data[DOMAIN].pop(PROTECTED_REGISTRY_STORE)
    now = datetime.now(UTC)

    result = await coordinator.event_overrides._apply_set(
        coordinator,
        _SLOT,
        SimpleNamespace(
            identity_key=_OWNER_IDENTITY,
            slot_name="Guest",
            slot_code=_CODE,
            start=now + timedelta(hours=1),
            end=now + timedelta(hours=2),
            buffered_start=now + timedelta(hours=1),
            buffered_end=now + timedelta(hours=2),
            display_slot_name="Guest",
        ),
        f"missing-store-{release}",
    )

    assert result.unconfirmed
    assert events == []
    assert hass.data[DOMAIN].get(PROTECTED_REGISTRY_STORE) is None


@pytest.mark.parametrize("release", _RELEASES)
async def test_set_and_cleanup_share_one_slot_operation_lock(
    hass,
    tmp_path: Path,
    release: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A same-slot reset waits until set dispatch and confirmation complete."""
    from dataclasses import replace

    from custom_components.rental_control import util

    coordinator, store, entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage", release
    )
    events: list[dict[str, Any]] = []
    await _register_recording_services(hass, store, entity_ids, events)
    original_deps = util._keymaster_deps

    async def immediate_sleep(_delay: float) -> None:
        """Avoid retry delay while testing operation serialization."""
        return None

    monkeypatch.setattr(
        util,
        "_keymaster_deps",
        lambda: replace(original_deps(), sleep=immediate_sleep),
    )
    entered_first_dispatch = asyncio.Event()
    release_first_dispatch = asyncio.Event()
    service_registry_type = type(hass.services)
    original_async_call = service_registry_type.async_call
    blocked = False

    async def pause_first_set_call(service_registry, *args, **kwargs):
        """Pause the first dispatch so a same-slot clear must wait."""
        nonlocal blocked
        if (
            not blocked
            and kwargs.get("domain") == "switch"
            and kwargs.get("service") == "turn_off"
        ):
            blocked = True
            entered_first_dispatch.set()
            await release_first_dispatch.wait()
        return await original_async_call(service_registry, *args, **kwargs)

    monkeypatch.setattr(service_registry_type, "async_call", pause_first_set_call)
    now = datetime.now(UTC)
    reservation = SimpleNamespace(
        identity_key=_OWNER_IDENTITY,
        slot_name="Guest",
        slot_code=_CODE,
        start=now + timedelta(hours=1),
        end=now + timedelta(hours=2),
        buffered_start=now + timedelta(hours=1),
        buffered_end=now + timedelta(hours=2),
        display_slot_name="Guest",
    )
    overrides = coordinator.event_overrides
    set_task = asyncio.create_task(
        overrides._apply_set(coordinator, _SLOT, reservation, "serialized-set")
    )
    await entered_first_dispatch.wait()
    clear_task = None
    pending_before_clear = overrides._slot_operations_pending[_SLOT]
    pending_while_waiting = 0
    events_before_release: list[dict[str, Any]] = []
    try:
        clear_task = asyncio.create_task(
            overrides._apply_clear(
                coordinator, _SLOT, owner_identity_key=_OWNER_IDENTITY
            )
        )
        await asyncio.sleep(0)
        pending_while_waiting = overrides._slot_operations_pending[_SLOT]
        events_before_release = list(events)
    finally:
        release_first_dispatch.set()
    assert clear_task is not None
    set_result, clear_result = await asyncio.gather(set_task, clear_task)

    assert pending_before_clear >= 1
    assert pending_while_waiting >= 3
    assert events_before_release == []
    assert set_result.confirmed
    assert clear_result.confirmed
    assert events[0]["service"] == "turn_off"
    assert events[-1]["service"] == "press"
    assert all(event["service"] != "press" for event in events[:-1])
    assert _SLOT not in overrides._slot_operations_pending


@pytest.mark.parametrize("release", _RELEASES)
@pytest.mark.parametrize("guard_case", ("adoption_conflict", "observed_pin"))
async def test_confirmed_clear_cannot_bypass_release_guards(
    hass,
    tmp_path: Path,
    release: str,
    guard_case: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real reset cannot bypass shared-owner or observed-PIN guards."""
    from dataclasses import replace

    from custom_components.rental_control import util
    from custom_components.rental_control.allocator.models import CycleObservation
    from custom_components.rental_control.allocator.registry_snapshot import (
        SnapshotCommitHooks,
    )
    from custom_components.rental_control.allocator.registry_snapshot import (
        async_commit_registry_snapshot,
    )
    from custom_components.rental_control.allocator.registry_snapshot import (
        serialize_exclusion_snapshots,
    )
    from custom_components.rental_control.allocator.singleton import get_allocator
    from custom_components.rental_control.keymaster_operations import (
        _current_exclusion_snapshot,
    )
    from custom_components.rental_control.keymaster_operations import (
        async_prepare_verified_operation_intent,
    )

    coordinator, store, entity_ids = await _setup_verified_consumer(
        hass,
        tmp_path / ".storage",
        release,
        shared_owner=guard_case == "adoption_conflict",
    )
    events: list[dict[str, Any]] = []
    await _register_recording_services(hass, store, entity_ids, events)
    adopted_identity = "consumer-reservation-adopted"
    allocator = get_allocator(hass)
    assert allocator is not None
    allocation = await async_prepare_verified_operation_intent(
        hass,
        _RC_ENTRY_ID,
        slot=_SLOT,
        owner_identity_key=_OWNER_IDENTITY,
        required_properties=(
            f"switch.code_slots:{_SLOT}.enabled",
            f"text.code_slots:{_SLOT}.pin",
            f"text.code_slots:{_SLOT}.name",
        ),
    )
    assert allocation is not None

    now = datetime.now(UTC)

    original_deps = util._keymaster_deps

    async def immediate_sleep(_delay: float) -> None:
        """Avoid retry delay while exercising the actual clear consumer."""
        return None

    monkeypatch.setattr(
        util,
        "_keymaster_deps",
        lambda: replace(original_deps(), sleep=immediate_sleep),
    )
    coordinator.event_overrides._overrides[_SLOT] = (
        coordinator.event_overrides._override_dict(
            _CODE, "Guest", now + timedelta(hours=1), now + timedelta(hours=2)
        )
    )
    cleared = await coordinator.event_overrides._apply_clear(
        coordinator,
        _SLOT,
        owner_identity_key=_OWNER_IDENTITY,
    )
    assert cleared.confirmed
    loaded = store.loaded_result
    assert loaded is not None and loaded.data is not None
    assert any(
        tombstone["resolution_kind"] == "clear_confirmed"
        for tombstone in loaded.data["tombstones"]
    )

    def authorize_release(baseline, _candidate) -> bool:
        """Use the production release guard with exact live slot evidence."""
        record = baseline.record_for_identity(_OWNER_IDENTITY)
        if record is None:
            return False
        owner = next(
            (item for item in record.owners if item.identity_key == _OWNER_IDENTITY),
            None,
        )
        if owner is None:
            return False
        lifecycle = get_keymaster_lifecycle(hass, _RC_ENTRY_ID)
        assert lifecycle is not None
        observation = CycleObservation(
            entry_id=_RC_ENTRY_ID,
            lockname=owner.lockname,
            managed_slots=frozenset({_SLOT}),
            observed_codes={_CODE: _SLOT} if guard_case == "observed_pin" else {},
            unreadable_slots=frozenset(),
            keymaster_entry_id=lifecycle.keymaster_entry_id,
            keymaster_generation=lifecycle.generation,
        )
        return allocator._release_guard_reason(record, [owner], [observation]) is None

    def release_original(candidate) -> None:
        """Attempt to retire only the cleared original reservation."""
        candidate.release(_OWNER_IDENTITY)

    rejected = await async_commit_registry_snapshot(
        allocator,
        store,
        release_original,
        serialize_exclusion_snapshots(hass.config_entries.async_entries(DOMAIN)),
        lambda: _current_exclusion_snapshot(hass, serialize_exclusion_snapshots),
        hooks=SnapshotCommitHooks(authorize_owner_transition=authorize_release),
    )
    assert rejected.state is not ProtectionLoadState.VALID_V2
    record = allocator._registry.record_for_identity(_OWNER_IDENTITY)
    assert record is not None
    expected_owners = {_OWNER_IDENTITY}
    if guard_case == "adoption_conflict":
        expected_owners.add(adopted_identity)
    assert {owner.identity_key for owner in record.owners} == expected_owners
    assert any(
        fence["fence_type"] == "allocation"
        for fence in store.loaded_result.data["pending_fences"]
    )


@pytest.mark.parametrize("release", _RELEASES)
async def test_greedy_cleanup_acknowledges_clear_before_reset_and_keeps_allocation(
    hass, tmp_path: Path, release: str
) -> None:
    """The actual greedy cleanup path resolves only its clear fence."""
    from custom_components.rental_control.event_overrides_helpers.models import (
        EvictionAction,
    )
    from custom_components.rental_control.event_overrides_helpers.models import (
        EvictionDecision,
    )
    from custom_components.rental_control.event_overrides_helpers.shell_cleanup import (
        _async_apply_clear_decision,
    )
    from custom_components.rental_control.keymaster_operations import (
        async_prepare_verified_operation_intent,
    )

    coordinator, store, entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage", release
    )
    events: list[dict[str, Any]] = []
    await _register_recording_services(hass, store, entity_ids, events)
    allocation = await async_prepare_verified_operation_intent(
        hass,
        _RC_ENTRY_ID,
        slot=_SLOT,
        owner_identity_key=_OWNER_IDENTITY,
        required_properties=(
            f"switch.code_slots:{_SLOT}.enabled",
            f"text.code_slots:{_SLOT}.pin",
            f"text.code_slots:{_SLOT}.name",
        ),
    )
    assert allocation is not None
    now = datetime.now(UTC)
    overrides = coordinator.event_overrides
    coordinator._slot_mappings = {"mappings": {_OWNER_IDENTITY: {"slot": _SLOT}}}
    overrides._overrides[_SLOT] = overrides._override_dict(
        _CODE, "Guest", now + timedelta(hours=1), now + timedelta(hours=2)
    )

    await _async_apply_clear_decision(
        overrides,
        coordinator,
        EvictionDecision(slot=_SLOT, action=EvictionAction.CLEAR),
    )

    assert events
    assert events[0]["service"] == "press"
    assert events[0]["fences"] == ("allocation", "pending_clear")
    loaded = store.loaded_result
    assert loaded is not None and loaded.data is not None
    assert loaded.data["pending_fences"] == [
        fence
        for fence in loaded.data["pending_fences"]
        if fence["fence_type"] == "allocation"
    ]
    assert any(
        tombstone["resolution_kind"] == "clear_confirmed"
        and tombstone["fence_type"] == "pending_clear"
        and tombstone["original_km_entry_id"] == _KM_ENTRY_ID
        and tombstone["slot"] == _SLOT
        for tombstone in loaded.data["tombstones"]
    )
    assert overrides._overrides.get(_SLOT) is None


@pytest.mark.parametrize("release", _RELEASES)
async def test_greedy_cleanup_blocks_unprepared_later_owner(
    hass,
    tmp_path: Path,
    release: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A later booking cannot dispatch without its own acknowledged allocation."""
    from dataclasses import replace

    from custom_components.rental_control import util
    from custom_components.rental_control.allocator.singleton import get_allocator
    from custom_components.rental_control.event_overrides_helpers.models import (
        EvictionAction,
    )
    from custom_components.rental_control.event_overrides_helpers.models import (
        EvictionDecision,
    )
    from custom_components.rental_control.event_overrides_helpers.shell_cleanup import (
        _async_apply_clear_decision,
    )
    from custom_components.rental_control.keymaster_operations import (
        async_prepare_verified_operation_intent,
    )

    coordinator, store, entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage", release
    )
    events: list[dict[str, Any]] = []
    await _register_recording_services(hass, store, entity_ids, events)
    allocation = await async_prepare_verified_operation_intent(
        hass,
        _RC_ENTRY_ID,
        slot=_SLOT,
        owner_identity_key=_OWNER_IDENTITY,
        required_properties=(
            f"switch.code_slots:{_SLOT}.enabled",
            f"text.code_slots:{_SLOT}.pin",
            f"text.code_slots:{_SLOT}.name",
        ),
    )
    assert allocation is not None
    original_deps = util._keymaster_deps

    async def immediate_sleep(_delay: float) -> None:
        """Reach clear confirmation without adding nondeterministic delay."""
        return None

    monkeypatch.setattr(
        util,
        "_keymaster_deps",
        lambda: replace(original_deps(), sleep=immediate_sleep),
    )
    overrides = coordinator.event_overrides
    coordinator._slot_mappings = {"mappings": {_OWNER_IDENTITY: {"slot": _SLOT}}}
    now = datetime.now(UTC)
    overrides._overrides[_SLOT] = overrides._override_dict(
        _CODE, "Guest", now + timedelta(hours=1), now + timedelta(hours=2)
    )

    reset_entered = asyncio.Event()
    release_reset = asyncio.Event()
    service_registry_type = type(hass.services)
    original_async_call = service_registry_type.async_call

    async def pause_reset(service_registry, *args, **kwargs):
        """Hold reset after its durable fence has been acknowledged."""
        if kwargs.get("domain") == "button" and kwargs.get("service") == "press":
            reset_entered.set()
            await release_reset.wait()
        return await original_async_call(service_registry, *args, **kwargs)

    monkeypatch.setattr(service_registry_type, "async_call", pause_reset)
    cleanup_task = asyncio.create_task(
        _async_apply_clear_decision(
            overrides,
            coordinator,
            EvictionDecision(slot=_SLOT, action=EvictionAction.CLEAR),
        )
    )
    await reset_entered.wait()
    loaded = store.loaded_result
    assert loaded is not None and loaded.data is not None
    assert any(
        fence["fence_type"] == "pending_clear"
        and fence["original_km_entry_id"] == allocation.keymaster_entry_id
        and fence["slot"] == _SLOT
        for fence in loaded.data["pending_fences"]
    )

    next_owner = "consumer-reservation-next"
    next_code = "8346"
    next_start = now + timedelta(hours=3)
    next_end = now + timedelta(hours=4)
    reservation = SimpleNamespace(
        identity_key=next_owner,
        slot_name="Next Guest",
        slot_code=next_code,
        start=next_start,
        end=next_end,
        buffered_start=next_start,
        buffered_end=next_end,
        display_slot_name="Next Guest",
    )
    set_task = asyncio.create_task(
        overrides._apply_set(coordinator, _SLOT, reservation, "after-cleanup")
    )
    await asyncio.sleep(0)
    assert events == []
    assert overrides._slot_operations_pending[_SLOT] >= 2
    release_reset.set()
    _, waiting_result = await asyncio.gather(cleanup_task, set_task)

    assert not waiting_result.confirmed
    assert [event["service"] for event in events] == ["press"]
    loaded = store.loaded_result
    assert loaded is not None and loaded.data is not None
    assert any(
        tombstone["resolution_kind"] == "clear_confirmed"
        and tombstone["original_km_entry_id"] == allocation.keymaster_entry_id
        and tombstone["slot"] == _SLOT
        for tombstone in loaded.data["tombstones"]
    )
    allocator = get_allocator(hass)
    assert allocator is not None
    assert allocator._registry.record_for_identity(next_owner) is None
    assert events[0]["service"] == "press"
    assert [event["service"] for event in events] == ["press"]


@pytest.mark.parametrize("release", _RELEASES)
@pytest.mark.parametrize("operation", ("set", "date", "clear", "overwrite"))
async def test_held_unrelated_protected_commit_waits_for_verified_operation(
    hass,
    tmp_path: Path,
    release: str,
    operation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Held unrelated commits settle before set/date/clear confirmation."""
    import copy
    import secrets

    from custom_components.rental_control.allocator import registry_commit

    coordinator, store, entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage", release
    )
    events: list[dict[str, Any]] = []
    await _register_recording_services(hass, store, entity_ids, events)
    overrides = coordinator.event_overrides
    now = datetime.now(UTC)
    reservation = SimpleNamespace(
        identity_key=_OWNER_IDENTITY,
        slot_name="Guest",
        slot_code=_CODE,
        start=now + timedelta(hours=1),
        end=now + timedelta(hours=2),
        buffered_start=now + timedelta(hours=1),
        buffered_end=now + timedelta(hours=2),
        display_slot_name="Guest",
    )
    if operation != "set":
        initial = await overrides._apply_set(
            coordinator, _SLOT, reservation, "held-commit-seed"
        )
        assert initial.confirmed
        events.clear()

    service_registry_type = type(hass.services)
    original_async_call = service_registry_type.async_call
    original_commit = registry_commit._commit_transaction_locked
    loop = asyncio.get_running_loop()
    writer_started = asyncio.Event()
    release_writer = threading.Event()
    commit_task: asyncio.Task | None = None
    held = False
    hold_requested = False

    trigger_entity = {
        "set": entity_ids[f"text.code_slots:{_SLOT}.name"],
        "date": entity_ids[f"datetime.code_slots:{_SLOT}.accesslimit_date_range_start"],
        "clear": entity_ids[f"button.code_slots:{_SLOT}.reset"],
        "overwrite": entity_ids[f"text.code_slots:{_SLOT}.name"],
    }[operation]

    def hold_unrelated_commit(*args, **kwargs):
        """Pause only the injected unrelated transaction's worker write."""
        nonlocal held, hold_requested
        if hold_requested and not held:
            held = True
            loop.call_soon_threadsafe(writer_started.set)
            if not release_writer.wait(timeout=10):
                raise TimeoutError("test did not release unrelated commit")
        return original_commit(*args, **kwargs)

    monkeypatch.setattr(
        registry_commit, "_commit_transaction_locked", hold_unrelated_commit
    )

    async def commit_after_first_dispatch(service_registry, *args, **kwargs):
        """Start a generation-only commit after the selected state update."""
        result = await original_async_call(service_registry, *args, **kwargs)
        nonlocal commit_task, hold_requested
        if (
            commit_task is None
            and kwargs.get("target", {}).get("entity_id") == trigger_entity
        ):
            loaded = store.loaded_result
            assert loaded is not None and loaded.data is not None
            data = copy.deepcopy(loaded.data)
            data["transaction_id"] = secrets.token_hex(16)
            data["generation"] += 1
            hold_requested = True
            commit_task = asyncio.create_task(
                store.async_commit(
                    {
                        "version": 2,
                        "minor_version": 1,
                        "key": "rental_control.code_registry",
                        "data": data,
                    }
                )
            )
            await writer_started.wait()
        return result

    monkeypatch.setattr(
        service_registry_type, "async_call", commit_after_first_dispatch
    )
    if operation == "set":
        operation_task = asyncio.create_task(
            overrides._apply_set(coordinator, _SLOT, reservation, "held-set")
        )
    elif operation == "date":
        operation_task = asyncio.create_task(
            overrides._apply_update_times(coordinator, _SLOT, reservation)
        )
    elif operation == "clear":
        operation_task = asyncio.create_task(
            overrides._apply_clear(
                coordinator, _SLOT, owner_identity_key=_OWNER_IDENTITY
            )
        )
    else:
        operation_task = asyncio.create_task(
            overrides._apply_overwrite_manual_change(
                coordinator,
                _SLOT,
                reservation,
                SimpleNamespace(reason="manual_drift", preflight_read=False),
            )
        )

    try:
        await writer_started.wait()
        await asyncio.sleep(0)
        assert not operation_task.done()
    finally:
        release_writer.set()
    result = await operation_task
    assert commit_task is not None
    commit_result = await commit_task
    assert held
    assert commit_result.state is ProtectionLoadState.VALID_V2
    assert result.confirmed
    assert len(events) > 0
    loaded = store.loaded_result
    assert loaded is not None and loaded.data is not None
    if operation == "clear":
        assert loaded.generation >= 4
        assert not any(
            fence["fence_type"] == "pending_clear" and fence["slot"] == _SLOT
            for fence in loaded.data["pending_fences"]
        )
    else:
        assert any(
            fence["fence_type"] == "allocation" and fence["slot"] == _SLOT
            for fence in loaded.data["pending_fences"]
        )


@pytest.mark.parametrize("release", _RELEASES)
async def test_failed_unrelated_commit_freezes_and_retains_set_fence(
    hass,
    tmp_path: Path,
    release: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed held write blocks confirmation and preserves the allocation fence."""
    import copy
    import secrets

    from custom_components.rental_control.allocator import registry_commit

    coordinator, store, entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage", release
    )
    events: list[dict[str, Any]] = []
    await _register_recording_services(hass, store, entity_ids, events)
    service_registry_type = type(hass.services)
    original_async_call = service_registry_type.async_call
    loop = asyncio.get_running_loop()
    writer_started = asyncio.Event()
    release_writer = threading.Event()
    original_commit = registry_commit._commit_transaction_locked
    hold_requested = False
    commit_task: asyncio.Task | None = None

    def fail_held_commit(*args, **kwargs):
        """Fail the requested unrelated write after proving it is in flight."""
        nonlocal hold_requested
        if hold_requested:
            hold_requested = False
            loop.call_soon_threadsafe(writer_started.set)
            if not release_writer.wait(timeout=10):
                raise TimeoutError("test did not release failed commit")
            raise OSError("synthetic storage failure")
        return original_commit(*args, **kwargs)

    monkeypatch.setattr(registry_commit, "_commit_transaction_locked", fail_held_commit)

    async def start_failed_commit(service_registry, *args, **kwargs):
        """Launch the synthetic failing generation update at the test boundary."""
        nonlocal commit_task, hold_requested
        result = await original_async_call(service_registry, *args, **kwargs)
        if (
            commit_task is None
            and kwargs.get("target", {}).get("entity_id")
            == entity_ids[f"text.code_slots:{_SLOT}.name"]
        ):
            loaded = store.loaded_result
            assert loaded is not None and loaded.data is not None
            data = copy.deepcopy(loaded.data)
            data["transaction_id"] = secrets.token_hex(16)
            data["generation"] += 1
            hold_requested = True
            commit_task = asyncio.create_task(
                store.async_commit(
                    {
                        "version": 2,
                        "minor_version": 1,
                        "key": "rental_control.code_registry",
                        "data": data,
                    }
                )
            )
            await writer_started.wait()
        return result

    monkeypatch.setattr(service_registry_type, "async_call", start_failed_commit)
    now = datetime.now(UTC)
    operation_task = asyncio.create_task(
        coordinator.event_overrides._apply_set(
            coordinator,
            _SLOT,
            SimpleNamespace(
                identity_key=_OWNER_IDENTITY,
                slot_name="Guest",
                slot_code=_CODE,
                start=now + timedelta(hours=1),
                end=now + timedelta(hours=2),
                buffered_start=now + timedelta(hours=1),
                buffered_end=now + timedelta(hours=2),
                display_slot_name="Guest",
            ),
            "set-before-failed-commit",
        )
    )
    try:
        await writer_started.wait()
        await asyncio.sleep(0)
        assert not operation_task.done()
    finally:
        release_writer.set()
    set_result = await operation_task
    assert commit_task is not None
    commit_result = await commit_task
    assert commit_result.state is ProtectionLoadState.PROTECTIVE_UNKNOWN
    assert not set_result.confirmed
    assert store.authorization_state is ProtectionAuthorizationState.PROTECTIVE_UNKNOWN
    loaded = store.loaded_result
    assert loaded is not None and loaded.data is not None
    assert any(
        fence["fence_type"] == "allocation" and fence["slot"] == _SLOT
        for fence in loaded.data["pending_fences"]
    )
    assert coordinator.event_overrides._pending_fences[_SLOT]


@pytest.mark.parametrize(
    "damage",
    (
        "missing_snapshot",
        "missing_generation",
        "zero_generation",
        "boolean_generation",
        "missing_transaction",
        "missing_association",
        "missing_entry_data",
        "missing_lifecycle",
        "missing_store",
        "protective_unknown",
        "unloaded_keymaster",
        "malformed_physical_lock",
        "stale_association_generation",
    ),
)
async def test_real_intent_factory_rejects_malformed_authority_snapshots(
    hass,
    tmp_path: Path,
    damage: str,
) -> None:
    """Malformed or stale authority evidence never yields a dispatch intent."""
    coordinator, store, entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage"
    )
    events: list[dict[str, Any]] = []
    await _register_recording_services(hass, store, entity_ids, events)
    required = (
        f"switch.code_slots:{_SLOT}.enabled",
        f"text.code_slots:{_SLOT}.pin",
        f"text.code_slots:{_SLOT}.name",
    )
    acknowledged = await async_prepare_verified_operation_intent(
        hass,
        _RC_ENTRY_ID,
        slot=_SLOT,
        owner_identity_key=_OWNER_IDENTITY,
        required_properties=required,
    )
    assert acknowledged is not None
    loaded = store.loaded_result
    assert loaded is not None

    if damage == "missing_snapshot":
        store._loaded_result = None
    elif damage in {"missing_generation", "zero_generation", "boolean_generation"}:
        generation = {
            "missing_generation": None,
            "zero_generation": 0,
            "boolean_generation": True,
        }[damage]
        store._loaded_result = replace(loaded, generation=generation)
    elif damage == "missing_transaction":
        store._loaded_result = replace(loaded, transaction_id=None)
    elif damage == "missing_association":
        assert loaded.data is not None
        data = dict(loaded.data)
        data["associations"] = []
        store._loaded_result = replace(loaded, data=data)
    elif damage == "protective_unknown":
        store._enter_protective_unknown(
            ProtectionLoadResult(
                ProtectionLoadState.PROTECTIVE_UNKNOWN,
                failure_code="operation_failed",
            )
        )
    elif damage == "unloaded_keymaster":
        km_entry = hass.config_entries.async_get_entry(acknowledged.keymaster_entry_id)
        assert km_entry is not None
        km_entry._async_set_state(hass, ConfigEntryState.NOT_LOADED, None)
    elif damage == "missing_entry_data":
        hass.data[DOMAIN].pop(_RC_ENTRY_ID)
    elif damage == "missing_lifecycle":
        stop_keymaster_lifecycle(hass, _RC_ENTRY_ID)
    elif damage == "missing_store":
        hass.data[DOMAIN].pop(PROTECTED_REGISTRY_STORE)
    elif damage in {"malformed_physical_lock", "stale_association_generation"}:
        if damage == "malformed_physical_lock":
            entry = hass.config_entries.async_get_entry(acknowledged.keymaster_entry_id)
            assert entry is not None
            hass.config_entries.async_update_entry(
                entry,
                data={**entry.data, "lock_entity_id": "invalid"},
            )
        else:
            entry = hass.config_entries.async_get_entry(_RC_ENTRY_ID)
            assert entry is not None
            hass.config_entries.async_update_entry(
                entry,
                data={**entry.data, "association_generation": 2},
            )

    assert (
        create_verified_operation_intent(
            hass,
            _RC_ENTRY_ID,
            slot=_SLOT,
            owner_identity_key=_OWNER_IDENTITY,
            required_properties=required,
        )
        is None
    )
    assert events == []
    assert coordinator.config_entry.entry_id == _RC_ENTRY_ID


@pytest.mark.parametrize("release", _RELEASES)
async def test_empty_live_slot_name_is_not_an_event_source(
    hass,
    tmp_path: Path,
    release: str,
) -> None:
    """A selected lock and matching label cannot replace unknown live name state."""
    coordinator, store, _entity_ids = await _setup_verified_consumer(
        hass,
        tmp_path / ".storage",
        release,
    )
    selected = hass.config_entries.async_get_entry(_KM_ENTRY_ID)
    assert selected is not None
    hass.config_entries.async_update_entry(
        selected,
        data={**selected.data, "lockname": "selected_front_door"},
    )

    assert store.association_for_entry(_RC_ENTRY_ID) is not None
    assert verified_slot_source(hass, coordinator.config_entry, _SLOT) is None


@pytest.mark.parametrize("release", _RELEASES)
async def test_live_name_from_selected_keymaster_resolves_verified_source(
    hass,
    tmp_path: Path,
    release: str,
) -> None:
    """Both packaged releases resolve routing only from current live capability."""
    coordinator, store, entity_ids = await _setup_verified_consumer(
        hass,
        tmp_path / ".storage",
        release,
    )
    selected = hass.config_entries.async_get_entry(_KM_ENTRY_ID)
    assert selected is not None
    hass.config_entries.async_update_entry(
        selected,
        data={**selected.data, "lockname": "selected_front_door"},
    )
    name_entity_id = entity_ids[f"text.code_slots:{_SLOT}.name"]
    hass.states.async_set(name_entity_id, "Guest")
    await hass.async_block_till_done()

    source = verified_slot_source(hass, coordinator.config_entry, _SLOT)

    assert store.association_for_entry(_RC_ENTRY_ID) is not None
    assert source is not None
    assert source.entry.entry_id == _KM_ENTRY_ID
    assert source.lock_entity_id == selected.data["lock_entity_id"]
    assert source.lockname == "selected_front_door"
    assert source.name_capability.entity_id == name_entity_id
    assert source.lifecycle_generation > 0


@pytest.mark.parametrize("release", _RELEASES)
async def test_child_reset_requires_live_parent_override_control(
    hass,
    tmp_path: Path,
    release: str,
) -> None:
    """A child reset is blocked while inherited and allowed after override."""
    coordinator, store, entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage", release, parented=True
    )
    events: list[dict[str, Any]] = []
    await _register_recording_services(hass, store, entity_ids, events)

    blocked = await coordinator.event_overrides._apply_clear(
        coordinator,
        _SLOT,
        owner_identity_key=_OWNER_IDENTITY,
    )
    assert blocked.unconfirmed
    assert events == []
    loaded = store.loaded_result
    assert loaded is not None and loaded.data is not None
    assert all(
        fence["fence_type"] != "pending_clear"
        for fence in loaded.data["pending_fences"]
    )

    hass.states.async_set(
        entity_ids[f"switch.code_slots:{_SLOT}.override_parent"], "on"
    )
    await hass.async_block_till_done()
    lifecycle = get_keymaster_lifecycle(hass, _RC_ENTRY_ID)
    assert lifecycle is not None
    assert (
        lifecycle.current[f"switch.code_slots:{_SLOT}.override_parent"].boolean_state
        is True
    )
    assert lifecycle.current[f"text.code_slots:{_SLOT}.name"].ready
    allowed = await coordinator.event_overrides._apply_clear(
        coordinator,
        _SLOT,
        owner_identity_key=_OWNER_IDENTITY,
    )
    assert allowed.confirmed
    assert events[-1]["domain"] == "button"
    assert events[-1]["entity_id"] == entity_ids[f"button.code_slots:{_SLOT}.reset"]


@pytest.mark.parametrize("release", _RELEASES)
async def test_unconfirmed_reset_retains_both_durable_slot_fences(
    hass,
    tmp_path: Path,
    release: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unconfirmed hardware reset cannot release slot or owner protection."""
    from dataclasses import replace

    from custom_components.rental_control import util

    coordinator, store, entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage", release
    )
    events: list[dict[str, Any]] = []
    await _register_recording_services(
        hass,
        store,
        entity_ids,
        events,
        reset_clears_state=False,
    )
    original_deps = util._keymaster_deps

    async def immediate_sleep(_delay: float) -> None:
        """Skip the reset retry delay for the inheritance test."""
        return None

    monkeypatch.setattr(
        util,
        "_keymaster_deps",
        lambda: replace(original_deps(), sleep=immediate_sleep),
    )
    now = datetime.now(UTC)
    set_result = await coordinator.event_overrides._apply_set(
        coordinator,
        _SLOT,
        SimpleNamespace(
            identity_key=_OWNER_IDENTITY,
            slot_name="Guest",
            slot_code=_CODE,
            start=now + timedelta(hours=1),
            end=now + timedelta(hours=2),
            buffered_start=now + timedelta(hours=1),
            buffered_end=now + timedelta(hours=2),
            display_slot_name="Guest",
        ),
        "set-before-unconfirmed-reset",
    )
    assert set_result.confirmed
    clear_result = await coordinator.event_overrides._apply_clear(
        coordinator,
        _SLOT,
        owner_identity_key=_OWNER_IDENTITY,
    )
    assert clear_result.unconfirmed
    loaded = store.loaded_result
    assert loaded is not None and loaded.data is not None
    fences = loaded.data["pending_fences"]
    assert {fence["fence_type"] for fence in fences} == {
        "allocation",
        "pending_clear",
    }
    assert all(fence["slot"] == _SLOT for fence in fences)
    assert all(
        tombstone["resolution_kind"] != "clear_confirmed"
        for tombstone in loaded.data["tombstones"]
    )
    assert _SLOT in coordinator.event_overrides._pending_clear_slots


@pytest.mark.parametrize("release", _RELEASES)
async def test_association_change_after_dispatch_stops_remaining_set_calls(
    hass,
    tmp_path: Path,
    release: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A same-slot association rebind invalidates an in-flight intent."""
    from dataclasses import replace

    from custom_components.rental_control import util

    coordinator, store, entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage", release
    )
    events: list[dict[str, Any]] = []
    await _register_recording_services(hass, store, entity_ids, events)
    original_deps = util._keymaster_deps

    async def immediate_sleep(_delay: float) -> None:
        """Skip retry delay while checking association invalidation."""
        return None

    monkeypatch.setattr(
        util,
        "_keymaster_deps",
        lambda: replace(original_deps(), sleep=immediate_sleep),
    )
    rc_entry = hass.config_entries.async_get_entry(_RC_ENTRY_ID)
    assert rc_entry is not None
    service_registry_type = type(hass.services)
    original_async_call = service_registry_type.async_call
    rebound = False

    async def rebind_after_first_dispatch(service_registry, *args, **kwargs):
        """Change the association after one real service dispatch."""
        nonlocal rebound
        result = await original_async_call(service_registry, *args, **kwargs)
        if not rebound and kwargs.get("domain") == "switch":
            rebound = True
            association = replace(
                _association(),
                association_generation=2,
                association_transaction_id="d" * 32,
            )
            hass.config_entries.async_update_entry(
                rc_entry,
                data=association.merge_into_config(rc_entry.data),
            )
        return result

    monkeypatch.setattr(
        service_registry_type, "async_call", rebind_after_first_dispatch
    )
    now = datetime.now(UTC)
    result = await coordinator.event_overrides._apply_set(
        coordinator,
        _SLOT,
        SimpleNamespace(
            identity_key=_OWNER_IDENTITY,
            slot_name="Guest",
            slot_code=_CODE,
            start=now + timedelta(hours=1),
            end=now + timedelta(hours=2),
            buffered_start=now + timedelta(hours=1),
            buffered_end=now + timedelta(hours=2),
            display_slot_name="Guest",
        ),
        "set-after-association-change",
    )

    assert rebound
    assert result.unconfirmed
    assert len(events) == 1
    loaded = store.loaded_result
    assert loaded is not None and loaded.data is not None
    assert any(
        fence["fence_type"] == "allocation" and fence["slot"] == _SLOT
        for fence in loaded.data["pending_fences"]
    )


@pytest.mark.parametrize("release", _RELEASES)
async def test_failed_protected_commit_never_dispatches_a_set(
    hass,
    tmp_path: Path,
    release: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rejected durable fence prevents every downstream service call."""
    coordinator, store, entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage", release
    )
    events: list[dict[str, Any]] = []
    await _register_recording_services(hass, store, entity_ids, events)

    async def reject_commit(*_args, **_kwargs) -> ProtectionLoadResult:
        """Reject fence persistence before any hardware service is called."""
        return ProtectionLoadResult(
            ProtectionLoadState.UNAVAILABLE,
            failure_code="injected_commit_rejection",
        )

    monkeypatch.setattr(store, "async_commit", reject_commit)
    result = await coordinator.event_overrides._apply_set(
        coordinator,
        _SLOT,
        SimpleNamespace(
            identity_key=_OWNER_IDENTITY,
            slot_name="Guest",
            slot_code=_CODE,
            start=datetime.now(UTC) + timedelta(hours=1),
            end=datetime.now(UTC) + timedelta(hours=2),
            buffered_start=datetime.now(UTC) + timedelta(hours=1),
            buffered_end=datetime.now(UTC) + timedelta(hours=2),
            display_slot_name="Guest",
        ),
        "set-rejected",
    )

    assert result.unconfirmed
    assert not events
    loaded = store.loaded_result
    assert loaded is not None and loaded.data is not None
    assert loaded.data["pending_fences"] == []


@pytest.mark.parametrize("release", _RELEASES)
async def test_renamed_capability_rejects_consumer_without_legacy_fallback(
    hass,
    tmp_path: Path,
    release: str,
) -> None:
    """A same-generation registry rename blocks writes until lifecycle rebind."""
    coordinator, store, entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage", release
    )
    events: list[dict[str, Any]] = []
    await _register_recording_services(hass, store, entity_ids, events)
    old_entity_id = entity_ids[f"text.code_slots:{_SLOT}.name"]
    new_entity_id = "text.manually_renamed_keymaster_slot"
    er.async_get(hass).async_update_entity(old_entity_id, new_entity_id=new_entity_id)
    hass.states.async_set(new_entity_id, "")
    reused_entry = er.async_get(hass).async_get_or_create(
        "text",
        "keymaster",
        "unrelated_keymaster_identity",
        suggested_object_id=old_entity_id.split(".", maxsplit=1)[1],
        config_entry=hass.config_entries.async_get_entry(_KM_ENTRY_ID),
    )
    hass.states.async_set(reused_entry.entity_id, "Unrelated")
    coordinator._state_value = lambda entity_id: (
        state.state if (state := hass.states.get(entity_id)) is not None else None
    )
    lifecycle = get_keymaster_lifecycle(hass, _RC_ENTRY_ID)
    assert lifecycle is not None
    stale_snapshot = CoordinatorSetupMixin._read_verified_slot_snapshot(
        coordinator, _SLOT, lifecycle
    )
    assert stale_snapshot.name_state is None

    result = await coordinator.event_overrides._apply_set(
        coordinator,
        _SLOT,
        SimpleNamespace(
            identity_key=_OWNER_IDENTITY,
            slot_name="Guest",
            slot_code=_CODE,
            start=datetime.now(UTC) + timedelta(hours=1),
            end=datetime.now(UTC) + timedelta(hours=2),
            buffered_start=datetime.now(UTC) + timedelta(hours=1),
            buffered_end=datetime.now(UTC) + timedelta(hours=2),
            display_slot_name="Guest",
        ),
        "set-renamed",
    )

    assert result.unconfirmed
    assert not events
    loaded = store.loaded_result
    assert loaded is not None and loaded.data is not None
    assert loaded.data["pending_fences"] == []


@pytest.mark.parametrize("release", _RELEASES)
async def test_rename_during_service_call_preserves_fence_and_stops_followups(
    hass,
    tmp_path: Path,
    release: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rename after one real side effect blocks the remaining operation."""
    coordinator, store, entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage", release
    )
    events: list[dict[str, Any]] = []
    await _register_recording_services(hass, store, entity_ids, events)
    service_registry_type = type(hass.services)
    original_async_call = service_registry_type.async_call
    rename_id = entity_ids[f"switch.code_slots:{_SLOT}.enabled"]
    replacement_id = "switch.renamed_during_service"
    renamed = False

    async def rename_after_first_dispatch(service_registry, *args, **kwargs):
        """Rename the target after one side effect to invalidate follow-ups."""
        nonlocal renamed
        await original_async_call(service_registry, *args, **kwargs)
        if not renamed:
            renamed = True
            er.async_get(hass).async_update_entity(
                rename_id, new_entity_id=replacement_id
            )
            hass.states.async_set(replacement_id, "off")

    monkeypatch.setattr(
        service_registry_type, "async_call", rename_after_first_dispatch
    )
    result = await coordinator.event_overrides._apply_set(
        coordinator,
        _SLOT,
        SimpleNamespace(
            identity_key=_OWNER_IDENTITY,
            slot_name="Guest",
            slot_code=_CODE,
            start=datetime.now(UTC) + timedelta(hours=1),
            end=datetime.now(UTC) + timedelta(hours=2),
            buffered_start=datetime.now(UTC) + timedelta(hours=1),
            buffered_end=datetime.now(UTC) + timedelta(hours=2),
            display_slot_name="Guest",
        ),
        "set-renamed-mid-flight",
    )

    assert result.unconfirmed
    assert len(events) == 1
    assert events[0]["entity_id"] == rename_id
    loaded = store.loaded_result
    assert loaded is not None and loaded.data is not None
    assert any(
        fence["fence_type"] == "allocation" and fence["slot"] == _SLOT
        for fence in loaded.data["pending_fences"]
    )


@pytest.mark.parametrize("release", _RELEASES)
async def test_calendar_cleanup_uses_acknowledged_exact_reset(
    hass,
    tmp_path: Path,
    release: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cleanup consumer commits pending-clear before exact reset dispatch."""
    from dataclasses import replace

    from custom_components.rental_control import util

    coordinator, store, entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage", release
    )
    events: list[dict[str, Any]] = []
    await _register_recording_services(hass, store, entity_ids, events)
    original_deps = util._keymaster_deps

    async def immediate_sleep(_delay: float) -> None:
        """Skip delay while testing the calendar cleanup consumer."""
        return None

    monkeypatch.setattr(
        util,
        "_keymaster_deps",
        lambda: replace(original_deps(), sleep=immediate_sleep),
    )
    reservation = SimpleNamespace(
        identity_key=_OWNER_IDENTITY,
        slot_name="Guest",
        slot_code=_CODE,
        start=datetime.now(UTC) - timedelta(hours=2),
        end=datetime.now(UTC) + timedelta(hours=2),
        buffered_start=datetime.now(UTC) - timedelta(hours=2),
        buffered_end=datetime.now(UTC) + timedelta(hours=2),
        display_slot_name="Guest",
    )
    set_result = await coordinator.event_overrides._apply_set(
        coordinator, _SLOT, reservation, "cleanup-set"
    )
    assert set_result.confirmed
    coordinator._slot_mappings = {"mappings": {_OWNER_IDENTITY: {"slot": _SLOT}}}

    await coordinator.event_overrides.async_check_overrides(coordinator, calendar=[])

    assert events[-1]["domain"] == "button"
    assert events[-1]["entity_id"] == entity_ids[f"button.code_slots:{_SLOT}.reset"]
    assert events[-1]["fences"] == ("allocation", "pending_clear")
    loaded = store.loaded_result
    assert loaded is not None and loaded.data is not None
    assert all(
        fence["fence_type"] != "pending_clear"
        for fence in loaded.data["pending_fences"]
    )
    assert any(
        tombstone["resolution_kind"] == "clear_confirmed"
        for tombstone in loaded.data["tombstones"]
    )


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize("release", _RELEASES)
async def test_greedy_cleanup_failure_logs_no_exception_payload(
    hass,
    tmp_path: Path,
    release: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Actual cleanup retains its fence without logging provider error text."""
    from dataclasses import replace

    from custom_components.rental_control import util

    coordinator, _store, entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage", release
    )
    events: list[dict[str, Any]] = []
    await _register_recording_services(hass, _store, entity_ids, events)
    original_deps = util._keymaster_deps

    async def immediate_sleep(_delay: float) -> None:
        """Skip retry delay in this real cleanup test."""
        return None

    monkeypatch.setattr(
        util,
        "_keymaster_deps",
        lambda: replace(original_deps(), sleep=immediate_sleep),
    )
    guest = "Sensitive Guest"
    secret = "service echoed pin 987654"
    reservation = SimpleNamespace(
        identity_key=_OWNER_IDENTITY,
        slot_name=guest,
        slot_code=_CODE,
        start=datetime.now(UTC) - timedelta(hours=2),
        end=datetime.now(UTC) + timedelta(hours=2),
        buffered_start=datetime.now(UTC) - timedelta(hours=2),
        buffered_end=datetime.now(UTC) + timedelta(hours=2),
        display_slot_name=guest,
    )
    assert (
        await coordinator.event_overrides._apply_set(
            coordinator, _SLOT, reservation, "cleanup-privacy-set"
        )
    ).confirmed
    coordinator._slot_mappings = {"mappings": {_OWNER_IDENTITY: {"slot": _SLOT}}}

    async def fail_with_sensitive_provider_text(
        _service_registry, *_args, **_kwargs
    ) -> None:
        """Simulate an upstream service error which echoes private payload."""
        raise RuntimeError(f"{guest}: {secret}")

    monkeypatch.setattr(
        type(hass.services), "async_call", fail_with_sensitive_provider_text
    )
    with caplog.at_level(logging.DEBUG):
        await coordinator.event_overrides.async_check_overrides(
            coordinator, calendar=[]
        )

    assert secret not in caplog.text
    assert guest not in caplog.text
    records = [
        record
        for record in caplog.records
        if "service dispatch failed" in record.getMessage()
    ]
    assert len(records) == 1
    message = records[0].getMessage()
    assert "Selected Keymaster entry reset capability" in message
    assert "failure category service_operation_failed" in message
    assert f"slot {_SLOT}" in message
    assert "verified operation remains unconfirmed" in message
    assert "Verify the physical slot and service availability" in message
    assert records[0].exc_info is None
    assert records[0].exc_text is None
    assert "RuntimeError" not in caplog.text
    loaded = _store.loaded_result
    assert loaded is not None and loaded.data is not None
    assert any(
        fence["fence_type"] == "pending_clear"
        for fence in loaded.data["pending_fences"]
    )
