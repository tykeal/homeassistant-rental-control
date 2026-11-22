# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Verify durable clear fences survive restart without changing their target."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_registry import RegistryEntryDisabler
import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.rental_control import util
from custom_components.rental_control.allocator.association_evidence import (
    AssociationRebindHold,
)
from custom_components.rental_control.allocator.association_references import (
    association_references_for_snapshot,
)
from custom_components.rental_control.allocator.association_references import (
    ledger_allocation_references,
)
from custom_components.rental_control.allocator.association_transactions import (
    AssociationTransaction,
)
from custom_components.rental_control.allocator.associations import AssociationChange
from custom_components.rental_control.allocator.associations import AssociationMode
from custom_components.rental_control.allocator.associations import AssociationTuple
from custom_components.rental_control.allocator.models import CycleObservation
from custom_components.rental_control.allocator.models import ProtectionLoadState
from custom_components.rental_control.allocator.protected_store import (
    ProtectedRegistryStore,
)
from custom_components.rental_control.allocator.singleton import get_allocator
from custom_components.rental_control.const import DOMAIN
from custom_components.rental_control.const import PROTECTED_REGISTRY_STORE
from custom_components.rental_control.event_overrides_helpers import shell_apply_clear
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
from custom_components.rental_control.keymaster_operations import (
    async_prepare_verified_operation_intent,
)
from custom_components.rental_control.keymaster_operations import (
    async_resolve_verified_clear_fence,
)

from tests.integration.test_verified_consumer_dispatch import _KM_ENTRY_ID
from tests.integration.test_verified_consumer_dispatch import _OWNER_IDENTITY
from tests.integration.test_verified_consumer_dispatch import _RC_ENTRY_ID
from tests.integration.test_verified_consumer_dispatch import _SLOT
from tests.integration.test_verified_consumer_dispatch import (
    _register_recording_services,
)
from tests.integration.test_verified_consumer_dispatch import _setup_verified_consumer

_RELEASES = ("v0.6.1", "v0.6.5")
_CLEAR_PROPERTIES = (
    f"button.code_slots:{_SLOT}.reset",
    f"text.code_slots:{_SLOT}.name",
    f"text.code_slots:{_SLOT}.pin",
)
_KM_B_ENTRY_ID = "replacement-keymaster-b"
_REBIND_TX = "d" * 32


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize("release", _RELEASES)
async def test_clear_fence_is_original_entry_and_slot_scoped_across_restart(
    hass,
    tmp_path: Path,
    release: str,
) -> None:
    """A recreated/reselected target cannot resolve A's pending clear."""
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
    allocator = get_allocator(hass)
    assert allocator is not None
    owner_record = allocator._registry.record_for_identity(_OWNER_IDENTITY)
    assert owner_record is not None
    owner = next(
        item for item in owner_record.owners if item.identity_key == _OWNER_IDENTITY
    )
    lifecycle = get_keymaster_lifecycle(hass, _RC_ENTRY_ID)
    assert lifecycle is not None

    observation_from_b = CycleObservation(
        entry_id=_RC_ENTRY_ID,
        lockname=owner.lockname,
        managed_slots=frozenset({_SLOT}),
        observed_codes={},
        unreadable_slots=frozenset(),
        keymaster_entry_id=_KM_B_ENTRY_ID,
        keymaster_generation=lifecycle.generation,
    )
    assert (
        allocator._release_guard_reason(
            owner_record, [owner], [observation_from_b], refresh_observed=False
        )
        == "unverifiable_original_keymaster"
    )
    observation_from_a = replace(
        observation_from_b,
        keymaster_entry_id=lifecycle.keymaster_entry_id,
    )
    assert (
        allocator._release_guard_reason(
            owner_record, [owner], [observation_from_a], refresh_observed=False
        )
        is None
    )
    clear = await async_prepare_verified_operation_intent(
        hass,
        _RC_ENTRY_ID,
        slot=_SLOT,
        owner_identity_key=_OWNER_IDENTITY,
        required_properties=_CLEAR_PROPERTIES,
        fence_type="pending_clear",
    )
    assert clear is not None
    loaded = store.loaded_result
    assert loaded is not None and loaded.data is not None
    pending = [
        fence
        for fence in loaded.data["pending_fences"]
        if fence["fence_type"] == "pending_clear"
    ]
    assert len(pending) == 1
    assert pending[0]["original_km_entry_id"] == clear.keymaster_entry_id
    assert pending[0]["slot"] == _SLOT
    assert not await async_resolve_verified_clear_fence(
        replace(clear, lifecycle_generation=clear.lifecycle_generation + 1)
    )
    reset_entity_id = entity_ids[f"button.code_slots:{_SLOT}.reset"]
    er.async_get(hass).async_update_entity(
        reset_entity_id, disabled_by=RegistryEntryDisabler.USER
    )
    await hass.async_block_till_done()
    assert not await async_resolve_verified_clear_fence(clear)

    km_b_entry = MockConfigEntry(
        domain="keymaster",
        title="Replacement lock",
        entry_id=_KM_B_ENTRY_ID,
        data={"lock_entity_id": "lock.selected_physical_lock"},
    )
    km_b_entry.add_to_hass(hass)
    km_b_entry._async_set_state(hass, ConfigEntryState.LOADED, None)
    old_association = store.association_for_entry(_RC_ENTRY_ID)
    assert old_association is not None
    new_association = AssociationTuple(
        legacy_lock_selection_present=old_association.legacy_lock_selection_present,
        legacy_lock_selection_raw=old_association.legacy_lock_selection_raw,
        verified_keymaster_entry_id=_KM_B_ENTRY_ID,
        association_generation=old_association.association_generation + 1,
        association_transaction_id=_REBIND_TX,
        mode=AssociationMode.VERIFIED,
    )
    loaded = store.loaded_result
    assert loaded is not None and loaded.data is not None
    references = association_references_for_snapshot(loaded.data, _RC_ENTRY_ID)
    assert references
    transaction_generation = loaded.generation + 1
    rebind = AssociationTransaction(
        transaction_id=_REBIND_TX,
        generation=transaction_generation,
        predecessor_transaction_id=loaded.transaction_id,
        changes=(
            AssociationChange(
                _RC_ENTRY_ID,
                old_association,
                new_association,
                references,
            ),
        ),
        retained_holds=(
            AssociationRebindHold(
                hold_id="e" * 32,
                rc_entry_id=_RC_ENTRY_ID,
                original_km_entry_id=clear.keymaster_entry_id,
                selected_keymaster_entry_id=_KM_B_ENTRY_ID,
                claim_references=references,
                transaction_id=_REBIND_TX,
                generation=transaction_generation,
            ),
        ),
    )
    rebind_result = await store.async_commit_association(rebind)
    assert rebind_result.state is ProtectionLoadState.VALID_V2
    hass.config_entries.async_update_entry(
        coordinator.config_entry,
        data=new_association.merge_into_config(coordinator.config_entry.data),
    )
    stop_keymaster_lifecycle(hass, _RC_ENTRY_ID)
    start_keymaster_lifecycle(hass, coordinator.config_entry, coordinator)
    await hass.async_block_till_done()
    assert (
        allocator._release_guard_reason(
            owner_record, [owner], [observation_from_b], refresh_observed=False
        )
        == "unverifiable_original_keymaster"
    )
    assert not await async_resolve_verified_clear_fence(
        replace(clear, keymaster_entry_id=_KM_B_ENTRY_ID)
    )
    rebound = store.loaded_result
    assert rebound is not None and rebound.data is not None
    hold = rebound.data["holds"][-1]
    assert hold["kind"] == "association_rebind"
    assert hold["original_km_entry_id"] == clear.keymaster_entry_id
    assert any(
        fence["fence_id"] == pending[0]["fence_id"]
        and fence["original_km_entry_id"] == clear.keymaster_entry_id
        for fence in rebound.data["pending_fences"]
    )

    # Neither a replacement config entry nor another slot can resolve A's
    # durable fence, even when the intent's other fields remain intact.
    assert not await async_resolve_verified_clear_fence(
        replace(clear, keymaster_entry_id="replacement-km-entry")
    )
    assert not await async_resolve_verified_clear_fence(replace(clear, slot=_SLOT + 1))
    loaded = store.loaded_result
    assert loaded is not None and loaded.data is not None
    assert any(
        fence["fence_id"] == pending[0]["fence_id"]
        for fence in loaded.data["pending_fences"]
    )
    assert events == []

    # Reload only the durable store and selected lifecycle. A restored intent
    # is evidence to retain, not a command to replay reset hardware.
    restarted_store = ProtectedRegistryStore(
        hass, allocator._lock, storage_dir=tmp_path / ".storage"
    )
    restarted = await restarted_store.async_load()
    assert restarted.state is ProtectionLoadState.VALID_V2
    domain_data = hass.data[DOMAIN]
    domain_data[PROTECTED_REGISTRY_STORE] = restarted_store
    stop_keymaster_lifecycle(hass, _RC_ENTRY_ID)
    start_keymaster_lifecycle(hass, coordinator.config_entry, coordinator)
    await hass.async_block_till_done()

    assert events == []
    loaded = restarted_store.loaded_result
    assert loaded is not None and loaded.data is not None
    assert any(
        fence["fence_id"] == pending[0]["fence_id"]
        and fence["original_km_entry_id"] == clear.keymaster_entry_id
        and fence["slot"] == _SLOT
        for fence in loaded.data["pending_fences"]
    )


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize("release", _RELEASES)
async def test_reused_entity_ids_cannot_resolve_original_entry_fence(
    hass,
    tmp_path: Path,
    release: str,
) -> None:
    """A's pending clear cannot follow the same entity IDs into entry B."""
    _coordinator, store, entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage", release
    )
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
    clear = await async_prepare_verified_operation_intent(
        hass,
        _RC_ENTRY_ID,
        slot=_SLOT,
        owner_identity_key=_OWNER_IDENTITY,
        required_properties=_CLEAR_PROPERTIES,
        fence_type="pending_clear",
    )
    assert clear is not None

    replacement = MockConfigEntry(
        domain="keymaster",
        title="Recreated physical lock",
        entry_id=_KM_B_ENTRY_ID,
        data={"lock_entity_id": "lock.selected_physical_lock"},
    )
    replacement.add_to_hass(hass)
    replacement._async_set_state(hass, ConfigEntryState.LOADED, None)
    registry = er.async_get(hass)
    for property_key in _CLEAR_PROPERTIES:
        old_entity_id = entity_ids[property_key]
        old_registry_entry = registry.async_get(old_entity_id)
        assert old_registry_entry is not None
        registry.async_remove(old_entity_id)
        hass.states.async_remove(old_entity_id)
        identity = build_capability_identity(_KM_B_ENTRY_ID, property_key, generation=1)
        assert identity is not None
        replacement_registry_entry = registry.async_get_or_create(
            identity.domain,
            identity.platform,
            identity.unique_id,
            suggested_object_id=old_entity_id.split(".", maxsplit=1)[1],
            config_entry=replacement,
        )
        assert replacement_registry_entry.entity_id == old_entity_id
        hass.states.async_set(old_entity_id, "")
    await hass.async_block_till_done()

    assert not await async_resolve_verified_clear_fence(clear)
    loaded = store.loaded_result
    assert loaded is not None and loaded.data is not None
    assert any(
        fence["fence_id"] == clear.fence_id
        and fence["original_km_entry_id"] == clear.keymaster_entry_id
        for fence in loaded.data["pending_fences"]
    )
    assert not any(
        tombstone["resolution_kind"] == "clear_confirmed"
        for tombstone in loaded.data["tombstones"]
    )


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize("release", _RELEASES)
@pytest.mark.parametrize(
    "interruption",
    (
        "during_provider_call",
        "after_provider_before_confirmation",
        "after_local_confirmation_before_resolution",
    ),
)
async def test_interrupted_clear_never_replays_restored_intent(
    hass,
    tmp_path: Path,
    release: str,
    interruption: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both hardware-call interruption boundaries retain fences without retry."""
    coordinator, store, entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage", release
    )
    events: list[dict[str, Any]] = []
    await _register_recording_services(hass, store, entity_ids, events)
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
    hass.services.async_remove("button", "press")
    provider_entered = asyncio.Event()
    confirmation_sleep_entered = asyncio.Event()

    async def interruptible_provider(call) -> None:
        """Hold actual verified dispatch at the requested failure boundary."""
        loaded = store.loaded_result
        assert loaded is not None and loaded.data is not None
        assert any(
            fence["fence_type"] == "pending_clear"
            and fence["original_km_entry_id"] == allocation.keymaster_entry_id
            for fence in loaded.data["pending_fences"]
        )
        events.append({"entity_id": call.data["entity_id"], "service": "press"})
        provider_entered.set()
        if interruption == "after_provider_before_confirmation":
            hass.states.async_set(
                entity_ids[f"text.code_slots:{_SLOT}.name"], "unknown"
            )
            hass.states.async_set(entity_ids[f"text.code_slots:{_SLOT}.pin"], "unknown")
            return
        if interruption == "after_local_confirmation_before_resolution":
            hass.states.async_set(entity_ids[f"text.code_slots:{_SLOT}.name"], "")
            hass.states.async_set(entity_ids[f"text.code_slots:{_SLOT}.pin"], "")
            return
        await asyncio.Event().wait()

    hass.services.async_register("button", "press", interruptible_provider)
    original_deps = util._keymaster_deps

    async def pause_confirmation(_delay: float) -> None:
        """Pause after successful provider return but before local resolution."""
        confirmation_sleep_entered.set()
        await asyncio.Event().wait()

    if interruption == "after_provider_before_confirmation":
        monkeypatch.setattr(
            util,
            "_keymaster_deps",
            lambda: replace(original_deps(), sleep=pause_confirmation),
        )
    elif interruption == "after_local_confirmation_before_resolution":
        original_resolver = shell_apply_clear.async_resolve_verified_clear_fence

        async def pause_resolution(intent, **kwargs):
            """Pause after provider-local confirmation before durable tombstone."""
            confirmation_sleep_entered.set()
            await asyncio.Event().wait()
            return await original_resolver(intent, **kwargs)

        monkeypatch.setattr(
            shell_apply_clear, "async_resolve_verified_clear_fence", pause_resolution
        )

        async def immediate_sleep(_delay: float) -> None:
            """Reach the local-confirmation boundary without waiting."""
            return None

        monkeypatch.setattr(
            util,
            "_keymaster_deps",
            lambda: replace(original_deps(), sleep=immediate_sleep),
        )
    now = datetime.now(UTC)
    coordinator.event_overrides._overrides[_SLOT] = (
        coordinator.event_overrides._override_dict(
            "2718", "Guest", now + timedelta(hours=1), now + timedelta(hours=2)
        )
    )

    clear_task = asyncio.create_task(
        coordinator.event_overrides._apply_clear(
            coordinator,
            _SLOT,
            owner_identity_key=_OWNER_IDENTITY,
        )
    )
    await provider_entered.wait()
    if interruption != "during_provider_call":
        await confirmation_sleep_entered.wait()
    clear_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await clear_task

    loaded = store.loaded_result
    assert loaded is not None and loaded.data is not None
    assert any(
        fence["fence_type"] == "pending_clear"
        and fence["original_km_entry_id"] == allocation.keymaster_entry_id
        and fence["slot"] == _SLOT
        for fence in loaded.data["pending_fences"]
    )
    assert len(events) == 1

    restarted_store = ProtectedRegistryStore(
        hass, allocator._lock, storage_dir=tmp_path / ".storage"
    )
    restarted = await restarted_store.async_load()
    assert restarted.state is ProtectionLoadState.VALID_V2
    hass.data[DOMAIN][PROTECTED_REGISTRY_STORE] = restarted_store
    stop_keymaster_lifecycle(hass, _RC_ENTRY_ID)
    start_keymaster_lifecycle(hass, coordinator.config_entry, coordinator)
    await hass.async_block_till_done()
    assert len(events) == 1
    restarted_result = restarted_store.loaded_result
    assert restarted_result is not None and restarted_result.data is not None
    assert any(
        fence["fence_type"] == "pending_clear"
        and fence["original_km_entry_id"] == allocation.keymaster_entry_id
        for fence in restarted_result.data["pending_fences"]
    )


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize("release", _RELEASES)
async def test_failed_provider_clear_with_empty_text_keeps_owner_and_fence(
    hass,
    tmp_path: Path,
    release: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Local empty text after a failed provider call cannot release ownership."""
    coordinator, store, entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage", release
    )
    events: list[dict[str, Any]] = []
    await _register_recording_services(hass, store, entity_ids, events)
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
    hass.services.async_remove("button", "press")

    async def fail_after_local_empty(call) -> None:
        """Fail provider reset although Keymaster text is already empty."""
        events.append({"service": "reset_failed", "entity_id": call.data["entity_id"]})
        hass.states.async_set(entity_ids[f"text.code_slots:{_SLOT}.name"], "")
        hass.states.async_set(entity_ids[f"text.code_slots:{_SLOT}.pin"], "")
        raise HomeAssistantError("synthetic provider failure")

    hass.services.async_register("button", "press", fail_after_local_empty)
    original_deps = util._keymaster_deps

    async def immediate_sleep(_delay: float) -> None:
        """Skip retry waiting while retaining the real clear flow."""
        return None

    monkeypatch.setattr(
        util,
        "_keymaster_deps",
        lambda: replace(original_deps(), sleep=immediate_sleep),
    )
    now = datetime.now(UTC)
    coordinator.event_overrides._overrides[_SLOT] = (
        coordinator.event_overrides._override_dict(
            "2718", "Guest", now + timedelta(hours=1), now + timedelta(hours=2)
        )
    )

    result = await coordinator.event_overrides._apply_clear(
        coordinator,
        _SLOT,
        owner_identity_key=_OWNER_IDENTITY,
    )

    assert result.failed or result.unconfirmed
    loaded = store.loaded_result
    assert loaded is not None and loaded.data is not None
    references = ledger_allocation_references(
        loaded.data, _RC_ENTRY_ID, _OWNER_IDENTITY
    )
    assert references is not None
    assert any(
        fence["fence_type"] == "allocation" and fence["owner_ref"] == references[0]
        for fence in loaded.data["pending_fences"]
    )
    assert not any(
        tombstone["resolution_kind"] == "clear_confirmed"
        for tombstone in loaded.data["tombstones"]
    )
    owner_refs = ledger_allocation_references(
        loaded.data, _RC_ENTRY_ID, _OWNER_IDENTITY
    )
    assert owner_refs is not None
    assert any(
        fence["fence_type"] == "allocation" and fence["owner_ref"] == owner_refs[0]
        for fence in loaded.data["pending_fences"]
    )
    assert allocator._registry.record_for_identity(_OWNER_IDENTITY) is not None
    assert events and events[-1]["service"] == "reset_failed"

    restarted_store = ProtectedRegistryStore(
        hass, allocator._lock, storage_dir=tmp_path / ".storage"
    )
    restarted = await restarted_store.async_load()
    assert restarted.state is ProtectionLoadState.VALID_V2
    hass.data[DOMAIN][PROTECTED_REGISTRY_STORE] = restarted_store
    stop_keymaster_lifecycle(hass, _RC_ENTRY_ID)
    start_keymaster_lifecycle(hass, coordinator.config_entry, coordinator)
    await hass.async_block_till_done()
    assert len(events) == 1
    restarted_result = restarted_store.loaded_result
    assert restarted_result is not None and restarted_result.data is not None
    assert any(
        fence["fence_type"] == "pending_clear"
        and fence["original_km_entry_id"] == allocation.keymaster_entry_id
        and fence["slot"] == _SLOT
        for fence in restarted_result.data["pending_fences"]
    )
    assert allocator._registry.record_for_identity(_OWNER_IDENTITY) is not None


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize("release", _RELEASES)
async def test_native_none_publication_confirms_local_clear_not_pin_ownership(
    hass,
    tmp_path: Path,
    release: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Native empty publication resolves clear evidence, not PIN ownership."""
    coordinator, store, entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage", release
    )
    events: list[dict[str, Any]] = []
    await _register_recording_services(
        hass, store, entity_ids, events, reset_clears_state=False
    )
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
    hass.services.async_remove("button", "press")

    async def reset_to_native_none(call) -> None:
        """Publish the state Keymaster uses for its native ``None`` value."""
        entity_id = call.data["entity_id"]
        events.append({"service": "reset_native_none", "entity_id": entity_id})
        hass.states.async_set(entity_ids[f"text.code_slots:{_SLOT}.name"], "unknown")
        hass.states.async_set(entity_ids[f"text.code_slots:{_SLOT}.pin"], "unknown")

    hass.services.async_register("button", "press", reset_to_native_none)
    original_deps = util._keymaster_deps

    async def immediate_sleep(_delay: float) -> None:
        """Skip retry waiting while retaining the real clear flow."""
        return None

    monkeypatch.setattr(
        util,
        "_keymaster_deps",
        lambda: replace(original_deps(), sleep=immediate_sleep),
    )
    now = datetime.now(UTC)
    coordinator.event_overrides._overrides[_SLOT] = (
        coordinator.event_overrides._override_dict(
            "2718", "Guest", now + timedelta(hours=1), now + timedelta(hours=2)
        )
    )

    result = await coordinator.event_overrides._apply_clear(
        coordinator,
        _SLOT,
        owner_identity_key=_OWNER_IDENTITY,
    )

    lifecycle = get_keymaster_lifecycle(hass, _RC_ENTRY_ID)
    assert result.confirmed, (
        result,
        lifecycle.generation if lifecycle else None,
        events,
        hass.states.get(entity_ids[f"text.code_slots:{_SLOT}.name"]),
        hass.states.get(entity_ids[f"text.code_slots:{_SLOT}.pin"]),
    )
    loaded = store.loaded_result
    assert loaded is not None and loaded.data is not None
    assert any(
        tombstone["resolution_kind"] == "clear_confirmed"
        and tombstone["original_km_entry_id"] == allocation.keymaster_entry_id
        for tombstone in loaded.data["tombstones"]
    )
    references = ledger_allocation_references(
        loaded.data, _RC_ENTRY_ID, _OWNER_IDENTITY
    )
    assert references is not None
    assert any(
        fence["fence_type"] == "allocation" and fence["owner_ref"] == references[0]
        for fence in loaded.data["pending_fences"]
    )
    assert allocator._registry.record_for_identity(_OWNER_IDENTITY) is not None
    dispatched_count = len(events)

    restarted_store = ProtectedRegistryStore(
        hass, allocator._lock, storage_dir=tmp_path / ".storage"
    )
    restarted = await restarted_store.async_load()
    assert restarted.state is ProtectionLoadState.VALID_V2
    hass.data[DOMAIN][PROTECTED_REGISTRY_STORE] = restarted_store
    stop_keymaster_lifecycle(hass, _RC_ENTRY_ID)
    start_keymaster_lifecycle(hass, coordinator.config_entry, coordinator)
    await hass.async_block_till_done()

    assert len(events) == dispatched_count
    restarted_result = restarted_store.loaded_result
    assert restarted_result is not None and restarted_result.data is not None
    assert any(
        tombstone["resolution_kind"] == "clear_confirmed"
        and tombstone["original_km_entry_id"] == allocation.keymaster_entry_id
        for tombstone in restarted_result.data["tombstones"]
    )
    assert allocator._registry.record_for_identity(_OWNER_IDENTITY) is not None


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize("release", _RELEASES)
async def test_same_identity_rename_resolves_existing_clear_fence(
    hass,
    tmp_path: Path,
    release: str,
) -> None:
    """A fresh A capability mapping can resolve, but never replay, its fence."""
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
    clear = await async_prepare_verified_operation_intent(
        hass,
        _RC_ENTRY_ID,
        slot=_SLOT,
        owner_identity_key=_OWNER_IDENTITY,
        required_properties=_CLEAR_PROPERTIES,
        fence_type="pending_clear",
    )
    assert clear is not None

    registry = er.async_get(hass)
    renamed: dict[str, str] = {}
    renamed_text_properties = (
        f"text.code_slots:{_SLOT}.name",
        f"text.code_slots:{_SLOT}.pin",
    )
    for property_key in renamed_text_properties:
        old_entity_id = entity_ids[property_key]
        domain, _, property_name = property_key.partition(".")
        new_entity_id = (
            f"{domain}.same_identity_renamed_{_SLOT}_"
            f"{property_name.rsplit('.', maxsplit=1)[-1]}"
        )
        registry.async_update_entity(old_entity_id, new_entity_id=new_entity_id)
        renamed[property_key] = new_entity_id
    await hass.async_block_till_done()
    hass.states.async_set(renamed[f"text.code_slots:{_SLOT}.name"], "")
    hass.states.async_set(renamed[f"text.code_slots:{_SLOT}.pin"], "")
    await hass.async_block_till_done()

    refreshed = await async_prepare_verified_operation_intent(
        hass,
        _RC_ENTRY_ID,
        slot=_SLOT,
        owner_identity_key=_OWNER_IDENTITY,
        required_properties=_CLEAR_PROPERTIES,
        fence_type="pending_clear",
    )
    assert refreshed is not None
    assert refreshed.keymaster_entry_id == clear.keymaster_entry_id
    assert refreshed.fence_id == clear.fence_id
    assert (
        refreshed.entity_id(f"text.code_slots:{_SLOT}.name")
        == renamed[f"text.code_slots:{_SLOT}.name"]
    )
    assert await async_resolve_verified_clear_fence(refreshed)
    assert events == []

    loaded = store.loaded_result
    assert loaded is not None and loaded.data is not None
    assert any(
        tombstone["fence_id"] == clear.fence_id
        and tombstone["original_km_entry_id"] == clear.keymaster_entry_id
        and tombstone["slot"] == _SLOT
        and tombstone["resolution_kind"] == "clear_confirmed"
        for tombstone in loaded.data["tombstones"]
    )
    allocator = get_allocator(hass)
    assert allocator is not None
    assert allocator._registry.record_for_identity(_OWNER_IDENTITY) is not None


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize("release", _RELEASES)
async def test_live_native_none_resolves_pending_fence_without_reset(
    hass,
    tmp_path: Path,
    release: str,
) -> None:
    """Resolve local clear bookkeeping for live empty text without replay."""
    coordinator, store, entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage", release
    )
    events: list[dict[str, Any]] = []
    await _register_recording_services(
        hass, store, entity_ids, events, reset_clears_state=False
    )
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
    hass.states.async_set(entity_ids[f"text.code_slots:{_SLOT}.name"], "unknown")
    hass.states.async_set(entity_ids[f"text.code_slots:{_SLOT}.pin"], "unknown")
    await hass.async_block_till_done()
    pending_clear = await async_prepare_verified_operation_intent(
        hass,
        _RC_ENTRY_ID,
        slot=_SLOT,
        owner_identity_key=_OWNER_IDENTITY,
        required_properties=_CLEAR_PROPERTIES,
        fence_type="pending_clear",
    )
    assert pending_clear is not None

    result = await coordinator.event_overrides._apply_clear(
        coordinator,
        _SLOT,
        owner_identity_key=_OWNER_IDENTITY,
        preflight_read=True,
    )

    assert result.confirmed
    assert events == []
    loaded = store.loaded_result
    assert loaded is not None and loaded.data is not None
    assert any(
        tombstone["fence_id"] == pending_clear.fence_id
        and tombstone["original_km_entry_id"] == allocation.keymaster_entry_id
        and tombstone["slot"] == _SLOT
        and tombstone["resolution_kind"] == "clear_confirmed"
        for tombstone in loaded.data["tombstones"]
    )
    references = ledger_allocation_references(
        loaded.data, _RC_ENTRY_ID, _OWNER_IDENTITY
    )
    assert references is not None
    assert any(
        fence["fence_type"] == "allocation" and fence["owner_ref"] == references[0]
        for fence in loaded.data["pending_fences"]
    )
    assert allocator._registry.record_for_identity(_OWNER_IDENTITY) is not None


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize("release", _RELEASES)
async def test_restart_resolves_live_native_none_without_reset_replay(
    hass,
    tmp_path: Path,
    release: str,
) -> None:
    """A loaded, non-restored unknown entity resolves the original fence."""
    coordinator, store, entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage", release
    )
    events: list[dict[str, Any]] = []
    await _register_recording_services(
        hass, store, entity_ids, events, reset_clears_state=False
    )
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
    for property_name in ("name", "pin"):
        entity_id = entity_ids[f"text.code_slots:{_SLOT}.{property_name}"]
        hass.states.async_set(entity_id, "unknown")
    await hass.async_block_till_done()
    for property_name in ("name", "pin"):
        state = hass.states.get(entity_ids[f"text.code_slots:{_SLOT}.{property_name}"])
        assert state is not None
        assert state.state == "unknown"
        assert state.attributes.get("restored") is not True
    pending_clear = await async_prepare_verified_operation_intent(
        hass,
        _RC_ENTRY_ID,
        slot=_SLOT,
        owner_identity_key=_OWNER_IDENTITY,
        required_properties=_CLEAR_PROPERTIES,
        fence_type="pending_clear",
    )
    assert pending_clear is not None

    restarted_store = ProtectedRegistryStore(
        hass, allocator._lock, storage_dir=tmp_path / ".storage"
    )
    restarted = await restarted_store.async_load()
    assert restarted.state is ProtectionLoadState.VALID_V2
    hass.data[DOMAIN][PROTECTED_REGISTRY_STORE] = restarted_store
    stop_keymaster_lifecycle(hass, _RC_ENTRY_ID)
    start_keymaster_lifecycle(hass, coordinator.config_entry, coordinator)
    await hass.async_block_till_done()

    result = await coordinator.event_overrides._apply_clear(
        coordinator,
        _SLOT,
        owner_identity_key=_OWNER_IDENTITY,
        preflight_read=True,
    )

    assert result.confirmed
    assert events == []
    loaded = restarted_store.loaded_result
    assert loaded is not None and loaded.data is not None
    assert any(
        tombstone["fence_id"] == pending_clear.fence_id
        and tombstone["original_km_entry_id"] == allocation.keymaster_entry_id
        and tombstone["resolution_kind"] == "clear_confirmed"
        for tombstone in loaded.data["tombstones"]
    )
    references = ledger_allocation_references(
        loaded.data, _RC_ENTRY_ID, _OWNER_IDENTITY
    )
    assert references is not None
    assert any(
        fence["fence_type"] == "allocation" and fence["owner_ref"] == references[0]
        for fence in loaded.data["pending_fences"]
    )
    assert allocator._registry.record_for_identity(_OWNER_IDENTITY) is not None


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize("release", _RELEASES)
@pytest.mark.parametrize("property_name", ("name", "pin"))
@pytest.mark.parametrize("attribute", ("restored", "masked"))
async def test_nonlive_text_capability_cannot_authorize_pending_clear(
    hass,
    tmp_path: Path,
    release: str,
    property_name: str,
    attribute: str,
) -> None:
    """Restored or masked text is rejected by the real lifecycle resolver."""
    coordinator, _store, entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage", release
    )
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
    text_id = entity_ids[f"text.code_slots:{_SLOT}.{property_name}"]
    hass.states.async_set(
        text_id,
        "Guest",
        attributes={attribute: True},
    )
    await hass.async_block_till_done()

    pending_clear = await async_prepare_verified_operation_intent(
        hass,
        _RC_ENTRY_ID,
        slot=_SLOT,
        owner_identity_key=_OWNER_IDENTITY,
        required_properties=_CLEAR_PROPERTIES,
        fence_type="pending_clear",
    )

    assert pending_clear is None


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize(
    "corruption",
    ("zero", "multiple", "wrong_slot", "wrong_entry", "unknown_identity"),
)
async def test_required_keymaster_identity_fails_closed(
    hass,
    tmp_path: Path,
    corruption: str,
) -> None:
    """Protected release requires exactly one current original-slot fence."""
    coordinator, store, entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage"
    )
    intent = await async_prepare_verified_operation_intent(
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
    assert intent is not None
    allocator = get_allocator(hass)
    assert allocator is not None
    record = allocator._registry.record_for_identity(_OWNER_IDENTITY)
    assert record is not None
    owner = next(item for item in record.owners if item.identity_key == _OWNER_IDENTITY)
    lifecycle = get_keymaster_lifecycle(hass, _RC_ENTRY_ID)
    assert lifecycle is not None and lifecycle.current
    assert allocator._required_keymaster_identities([owner]) == {
        (_RC_ENTRY_ID, _OWNER_IDENTITY): (_KM_ENTRY_ID, lifecycle.generation)
    }
    loaded = store.loaded_result
    assert loaded is not None and loaded.data is not None
    fences = loaded.data["pending_fences"]
    assert len(fences) == 1

    if corruption == "zero":
        fences.clear()
    elif corruption == "multiple":
        duplicate = deepcopy(fences[0])
        duplicate["fence_id"] = "f" * 32
        fences.append(duplicate)
    elif corruption == "wrong_slot":
        fences[0]["slot"] = _SLOT + 1
    elif corruption == "wrong_entry":
        fences[0]["original_km_entry_id"] = "different-keymaster-entry"
    else:
        stop_keymaster_lifecycle(hass, _RC_ENTRY_ID)

    required = allocator._required_keymaster_identities([owner])
    assert required == {(_RC_ENTRY_ID, _OWNER_IDENTITY): None}


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize("release", _RELEASES)
async def test_reset_event_without_empty_local_state_retains_clear_fence(
    hass,
    tmp_path: Path,
    release: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful reset service event alone is not local-clear evidence."""
    coordinator, store, entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage", release
    )
    events: list[dict[str, Any]] = []
    await _register_recording_services(
        hass, store, entity_ids, events, reset_clears_state=False
    )
    hass.states.async_set(entity_ids[f"text.code_slots:{_SLOT}.name"], "Guest")
    hass.states.async_set(entity_ids[f"text.code_slots:{_SLOT}.pin"], "2718")
    await hass.async_block_till_done()
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
        """Skip retry waiting while retaining the actual reset path."""
        return None

    monkeypatch.setattr(
        util,
        "_keymaster_deps",
        lambda: replace(original_deps(), sleep=immediate_sleep),
    )
    now = datetime.now(UTC)
    coordinator.event_overrides._overrides[_SLOT] = (
        coordinator.event_overrides._override_dict(
            "2718", "Guest", now + timedelta(hours=1), now + timedelta(hours=2)
        )
    )

    result = await coordinator.event_overrides._apply_clear(
        coordinator,
        _SLOT,
        owner_identity_key=_OWNER_IDENTITY,
    )

    assert result.unconfirmed
    assert events and any(event["service"] == "press" for event in events)
    loaded = store.loaded_result
    assert loaded is not None and loaded.data is not None
    assert any(
        fence["fence_type"] == "pending_clear"
        and fence["original_km_entry_id"] == allocation.keymaster_entry_id
        and fence["slot"] == _SLOT
        for fence in loaded.data["pending_fences"]
    )
    assert not any(
        tombstone["resolution_kind"] == "clear_confirmed"
        for tombstone in loaded.data["tombstones"]
    )
    assert hass.states.get(entity_ids[f"text.code_slots:{_SLOT}.pin"]).state == "2718"
