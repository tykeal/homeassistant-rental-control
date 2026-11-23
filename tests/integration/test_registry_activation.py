# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Verify production allocator activation and protective failure boundaries."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from pathlib import Path
import secrets
from types import SimpleNamespace
from typing import Any

from homeassistant.helpers.issue_registry import async_get as async_get_issue_registry
from homeassistant.helpers.storage import Store
import pytest

from custom_components.rental_control.allocator import allocator as allocator_module
from custom_components.rental_control.allocator import issuance
from custom_components.rental_control.allocator import registry_commit
from custom_components.rental_control.allocator.allocator import DoorCodeAllocator
from custom_components.rental_control.allocator.association_evidence import (
    AssociationRebindHold,
)
from custom_components.rental_control.allocator.association_transactions import (
    AssociationTransaction,
)
from custom_components.rental_control.allocator.associations import AssociationChange
from custom_components.rental_control.allocator.associations import AssociationMode
from custom_components.rental_control.allocator.associations import AssociationTuple
from custom_components.rental_control.allocator.models import AdoptionRequest
from custom_components.rental_control.allocator.models import AllocationOrigin
from custom_components.rental_control.allocator.models import AllocationOwner
from custom_components.rental_control.allocator.models import AllocationRecord
from custom_components.rental_control.allocator.models import AllocationRequest
from custom_components.rental_control.allocator.models import CycleObservation
from custom_components.rental_control.allocator.models import CycleRequest
from custom_components.rental_control.allocator.models import ForcedReissueDirective
from custom_components.rental_control.allocator.models import ProtectionLoadResult
from custom_components.rental_control.allocator.models import ProtectionLoadState
from custom_components.rental_control.allocator.models import ReissuePreviewRequest
from custom_components.rental_control.allocator.protected_store import (
    ProtectedRegistryStore,
)
from custom_components.rental_control.allocator.registry import AllocationRegistry
from custom_components.rental_control.allocator.registry_mutation import (
    registry_changed,
)
from custom_components.rental_control.allocator.registry_snapshot import (
    async_commit_registry_snapshot,
)
from custom_components.rental_control.allocator.registry_snapshot import (
    serialize_exclusion_snapshots,
)
from custom_components.rental_control.allocator.singleton import get_allocator
from custom_components.rental_control.allocator.store import code_ref_for
from custom_components.rental_control.allocator.store import serialize_registry_ledger
from custom_components.rental_control.config_flow_helpers.association import (
    apply_association_selection,
)
from custom_components.rental_control.const import ALLOCATOR
from custom_components.rental_control.const import CONF_ASSOCIATION_ATTEST_SAME_LOCK
from custom_components.rental_control.const import CONF_ASSOCIATION_ENTRY_CHOICE
from custom_components.rental_control.const import CONF_ASSOCIATION_GENERATION
from custom_components.rental_control.const import CONF_ASSOCIATION_MODE
from custom_components.rental_control.const import CONF_ASSOCIATION_TRANSACTION_ID
from custom_components.rental_control.const import CONF_LOCK_ENTRY
from custom_components.rental_control.const import CONF_VERIFIED_KEYMASTER_ENTRY_ID
from custom_components.rental_control.const import COORDINATOR
from custom_components.rental_control.const import DOMAIN
from custom_components.rental_control.const import PROTECTED_REGISTRY_STORE
from custom_components.rental_control.const import STORE_CODE_REGISTRY_KEY
from custom_components.rental_control.keymaster_lifecycle import get_keymaster_lifecycle
from custom_components.rental_control.keymaster_lifecycle import (
    start_keymaster_lifecycle,
)
from custom_components.rental_control.keymaster_lifecycle import (
    stop_keymaster_lifecycle,
)
from custom_components.rental_control.keymaster_operation_fences import (
    _current_exclusion_snapshot,
)
from custom_components.rental_control.keymaster_operations import (
    async_prepare_verified_operation_intent,
)

from tests.integration.test_lockless_end_to_end import _entry as _lockless_entry
from tests.integration.test_lockless_end_to_end import _setup as _setup_lockless_entry
from tests.integration.test_verified_consumer_dispatch import _CODE
from tests.integration.test_verified_consumer_dispatch import _OWNER_IDENTITY
from tests.integration.test_verified_consumer_dispatch import _RC_ENTRY_ID
from tests.integration.test_verified_consumer_dispatch import _SLOT
from tests.integration.test_verified_consumer_dispatch import (
    _register_recording_services,
)
from tests.integration.test_verified_consumer_dispatch import _setup_verified_consumer

_RELEASES = ("v0.6.1", "v0.6.5")
_CREATED = "2026-11-21T00:00:00+00:00"
_V1_STORE_VERSION = 1


def _legacy_registry() -> AllocationRegistry:
    """Create one active legacy owner for an intact-v1 upgrade fixture."""
    registry = AllocationRegistry(code_ref_salt="activation-reference-salt")
    owner = AllocationOwner(
        entry_id="activation-rc",
        identity_key="activation-reservation",
        origin=AllocationOrigin.PREFERRED,
        lockname="front-door",
        slot=21,
        lock_observed=True,
        first_seen=_CREATED,
        last_seen=_CREATED,
    )
    registry.records[_CODE] = AllocationRecord(
        code=_CODE,
        code_ref=code_ref_for(_CODE, registry.code_ref_salt),
        encoding_salt_value=owner.entry_id,
        code_length=len(_CODE),
        owners=[owner],
        created_at=_CREATED,
        updated_at=_CREATED,
    )
    registry.rebuild_index()
    return registry


async def _write_v1_store(hass, registry: AllocationRegistry) -> dict[str, Any]:
    """Write a real Home Assistant v1 Store envelope to the fixture storage."""
    legacy: Store[dict[str, Any]] = Store(
        hass, _V1_STORE_VERSION, STORE_CODE_REGISTRY_KEY
    )
    payload = serialize_registry_ledger(registry)
    _, envelope = legacy._prepare_data(
        {
            "version": _V1_STORE_VERSION,
            "minor_version": 1,
            "key": STORE_CODE_REGISTRY_KEY,
            "data": payload,
        }
    )
    if isinstance(envelope, str):
        envelope = envelope.encode()
    Path(legacy.path).write_bytes(envelope)
    return payload


def _registry_bytes(hass) -> bytes:
    """Read the real storage fixture for exact post-activation assertions."""
    path = Path(hass.config.path(".storage", STORE_CODE_REGISTRY_KEY))
    return path.read_bytes()


def _clear_registry_fixture(hass) -> None:
    """Isolate activation cases from HA's shared test-config storage directory."""
    for key in (
        STORE_CODE_REGISTRY_KEY,
        "rental_control.code_registry_protection",
        "rental_control.code_registry.io_lock",
    ):
        Path(hass.config.path(".storage", key)).unlink(missing_ok=True)


@pytest.fixture(autouse=True)
def _isolated_registry_files(hass):
    """Keep these on-disk integration fixtures out of other tests."""
    _clear_registry_fixture(hass)
    yield
    _clear_registry_fixture(hass)


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_v1_activation_quiesces_delayed_writer_and_preserves_owner(
    hass,
) -> None:
    """A pending HA Store callback cannot overwrite the acknowledged v2 data."""
    Path(hass.config.path(".storage")).mkdir(parents=True, exist_ok=True)
    _clear_registry_fixture(hass)
    expected = _legacy_registry()
    payload = await _write_v1_store(hass, expected)
    assert Path(hass.config.path(".storage", STORE_CODE_REGISTRY_KEY)).is_file()
    allocator = DoorCodeAllocator(hass)
    allocator._protection_required = True
    allocator._store._store.async_delay_save(lambda: deepcopy(payload), delay=60)
    hass.data[DOMAIN] = {ALLOCATOR: allocator}

    await allocator.async_load()

    protected = hass.data[DOMAIN][PROTECTED_REGISTRY_STORE]
    loaded = protected.loaded_result
    assert loaded is not None, protected.last_result
    assert loaded.state is ProtectionLoadState.VALID_V2
    assert loaded.data is not None
    assert loaded.data["ledger"]["code_ref_salt"] == expected.code_ref_salt
    assert (
        loaded.data["ledger"]["records"]
        == serialize_registry_ledger(expected)["records"]
    )
    legacy_store = allocator._store._store
    assert legacy_store._read_only
    assert legacy_store._delay_handle is None
    assert legacy_store._data is None

    before = _registry_bytes(hass)
    await legacy_store._async_callback_delayed_write()
    assert _registry_bytes(hass) == before
    assert json.loads(before)["version"] == 2


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_v1_legacy_owner_is_attested_refenced_before_release_without_reprogram(
    hass,
    tmp_path: Path,
) -> None:
    """A same-slot v1 owner gets a durable fence before a later safe release."""
    from custom_components.rental_control.allocator.models import CycleObservation
    from custom_components.rental_control.allocator.models import CycleRequest
    from custom_components.rental_control.allocator.models import ProtectionLoadState

    coordinator, _initial_store, entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage", "v0.6.1"
    )
    rc_entry = coordinator.config_entry
    selected_id = "consumer-km"
    original_allocator = hass.data[DOMAIN][ALLOCATOR]
    legacy_data = dict(rc_entry.data)
    for key in (
        CONF_ASSOCIATION_GENERATION,
        CONF_ASSOCIATION_MODE,
        CONF_ASSOCIATION_TRANSACTION_ID,
        CONF_VERIFIED_KEYMASTER_ENTRY_ID,
    ):
        legacy_data.pop(key, None)
    legacy_data[CONF_LOCK_ENTRY] = "lock.consumer"
    hass.config_entries.async_update_entry(rc_entry, data=legacy_data)
    stop_keymaster_lifecycle(hass, rc_entry.entry_id)

    Path(hass.config.path(".storage")).mkdir(parents=True, exist_ok=True)
    _clear_registry_fixture(hass)
    await _write_v1_store(hass, deepcopy(original_allocator._registry))
    allocator = DoorCodeAllocator(hass)
    allocator._protection_required = True
    hass.data[DOMAIN][ALLOCATOR] = allocator
    await allocator.async_load()

    store = hass.data[DOMAIN][PROTECTED_REGISTRY_STORE]
    migrated = store.loaded_result
    assert migrated is not None
    assert migrated.state is ProtectionLoadState.VALID_V2
    assert migrated.data is not None
    assert migrated.data["pending_fences"] == []
    assert migrated.data["attestations"] == []

    result = await apply_association_selection(
        SimpleNamespace(hass=hass, config_entry=rc_entry),
        {
            CONF_ASSOCIATION_ENTRY_CHOICE: selected_id,
            CONF_ASSOCIATION_ATTEST_SAME_LOCK: True,
            CONF_LOCK_ENTRY: "lock.consumer",
        },
    )
    assert not result.errors
    selected = AssociationTuple.from_config(rc_entry.data)
    assert selected is not None
    assert selected.mode is AssociationMode.VERIFIED
    assert selected.verified_keymaster_entry_id == selected_id
    assert store.loaded_result is not None
    assert store.loaded_result.data is not None
    assert len(store.loaded_result.data["attestations"]) == 1

    coordinator._slot_mappings = {"mappings": {_OWNER_IDENTITY: {"slot": _SLOT}}}
    start_keymaster_lifecycle(hass, rc_entry, coordinator)
    await hass.async_block_till_done()
    lifecycle = get_keymaster_lifecycle(hass, rc_entry.entry_id)
    assert lifecycle is not None and lifecycle.current
    hass.states.async_set(entity_ids[f"text.code_slots:{_SLOT}.pin"], _CODE)
    hass.states.async_set(entity_ids[f"text.code_slots:{_SLOT}.name"], "Guest")

    events: list[dict[str, Any]] = []
    await _register_recording_services(hass, store, entity_ids, events)
    observation = CycleObservation(
        entry_id=rc_entry.entry_id,
        lockname="lock.consumer",
        managed_slots=frozenset({_SLOT}),
        observed_codes={_CODE: _SLOT},
        unreadable_slots=frozenset(),
        keymaster_entry_id=selected_id,
        keymaster_generation=lifecycle.generation,
    )
    cycle = CycleRequest(
        observation=observation,
        adoptions=[],
        rekeys=[],
        allocations=[],
        active_keys={_OWNER_IDENTITY},
    )
    cycle_result = await allocator.async_resolve_cycle(cycle)
    assert not cycle_result.released.released
    assert store.loaded_result is not None
    assert store.loaded_result.data is not None
    exact_fences = [
        fence
        for fence in store.loaded_result.data["pending_fences"]
        if fence["fence_type"] == "allocation"
        and fence["original_km_entry_id"] == selected_id
        and fence["rc_entry_id"] == rc_entry.entry_id
        and fence["slot"] == _SLOT
    ]
    assert len(exact_fences) == 1
    assert store.loaded_result.data["attestations"]
    assert events == []

    empty_observation = CycleObservation(
        entry_id=rc_entry.entry_id,
        lockname="lock.consumer",
        managed_slots=frozenset({_SLOT}),
        observed_codes={},
        unreadable_slots=frozenset(),
        keymaster_entry_id=selected_id,
        keymaster_generation=lifecycle.generation,
    )
    release = await allocator.async_sweep(empty_observation, set())
    assert len(release.released) == 1
    assert events == []
    assert store.loaded_result.data["pending_fences"] == []
    tombstone = store.loaded_result.data["tombstones"][-1]
    assert tombstone["original_km_entry_id"] == selected_id
    assert tombstone["slot"] == _SLOT
    assert tombstone["resolution_kind"] == "owner_released"

    stop_keymaster_lifecycle(hass, rc_entry.entry_id)
    _clear_registry_fixture(hass)


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_two_entry_cycles_serialize_with_unrelated_snapshot_activity(
    hass,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent RC cycles and a separate snapshot preserve every mutation."""
    allocator = DoorCodeAllocator(hass)
    allocator._protection_required = True
    hass.data[DOMAIN] = {ALLOCATOR: allocator}
    await allocator.async_load()
    store = hass.data[DOMAIN][PROTECTED_REGISTRY_STORE]

    commit_entered = asyncio.Event()
    finish_commit = asyncio.Event()
    original_commit = store.async_commit
    commit_count = 0

    async def hold_first_commit(envelope: dict[str, Any], **kwargs: Any):
        """Pause the first acknowledged write for deterministic concurrency."""
        nonlocal commit_count
        commit_count += 1
        if commit_count == 1:
            commit_entered.set()
            await finish_commit.wait()
        return await original_commit(envelope, **kwargs)

    monkeypatch.setattr(store, "async_commit", hold_first_commit)

    def request(entry_id: str, identity: str, code: str) -> CycleRequest:
        """Create one independent lockless entry allocation."""
        return CycleRequest(
            observation=CycleObservation(
                entry_id=entry_id,
                lockname=None,
                managed_slots=frozenset(),
                observed_codes={},
                unreadable_slots=frozenset(),
            ),
            adoptions=[],
            rekeys=[],
            allocations=[
                AllocationRequest(
                    entry_id=entry_id,
                    identity_key=identity,
                    preferred_code=code,
                    code_length=4,
                    lockname=None,
                    slot=None,
                )
            ],
            active_keys={identity},
        )

    first = asyncio.create_task(
        allocator.async_resolve_cycle(request("rc-a", "stay-a", "1357"))
    )
    await commit_entered.wait()
    exclusions = serialize_exclusion_snapshots(
        hass.config_entries.async_entries(DOMAIN)
    )

    unrelated = asyncio.create_task(
        async_commit_registry_snapshot(
            allocator,
            store,
            lambda _candidate: None,
            exclusions,
            lambda: _current_exclusion_snapshot(hass, serialize_exclusion_snapshots),
        )
    )
    second = asyncio.create_task(
        allocator.async_resolve_cycle(request("rc-b", "stay-b", "2468"))
    )
    await asyncio.sleep(0)
    assert not first.done()
    assert not unrelated.done()
    assert not second.done()
    assert allocator._mutation_lock.locked()

    finish_commit.set()
    first_result, unrelated_result, second_result = await asyncio.gather(
        first, unrelated, second
    )
    assert first_result.allocated["stay-a"].code == "1357"
    assert second_result.allocated["stay-b"].code == "2468"
    assert unrelated_result.state is ProtectionLoadState.VALID_V2
    owners = {
        owner.identity_key
        for record in allocator._registry.records.values()
        for owner in record.owners
    }
    assert owners == {"stay-a", "stay-b"}
    assert store.loaded_result is not None
    assert store.loaded_result.data is not None
    persisted_owners = {
        owner["identity_key"]
        for record in store.loaded_result.data["ledger"]["records"]
        for owner in record["owners"]
    }
    assert persisted_owners == owners


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_conflict_notice_is_created_after_commit_acknowledgement(
    hass,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful staged conflict notices observe the acknowledged ledger."""
    allocator = DoorCodeAllocator(hass)
    allocator._protection_required = True
    hass.data[DOMAIN] = {ALLOCATOR: allocator}
    await allocator.async_load()
    store = hass.data[DOMAIN][PROTECTED_REGISTRY_STORE]
    notices: list[tuple[str, int, set[str]]] = []

    def capture_notice(_hass, _message: str, **_kwargs: Any) -> None:
        """Record the exact durable owner set visible to the notification."""
        loaded = store.loaded_result
        assert loaded is not None and loaded.data is not None
        owner_ids = {
            owner["identity_key"]
            for record in loaded.data["ledger"]["records"]
            for owner in record["owners"]
        }
        notices.append((store.authorization_state.value, loaded.generation, owner_ids))

    monkeypatch.setattr(allocator_module, "async_create", capture_notice)
    # This test isolates durable notice ordering; identity authorization has
    # separate integration coverage.
    monkeypatch.setattr(
        "custom_components.rental_control.allocator.issuance.observation_preflight",
        lambda *_args: None,
    )
    result = await allocator.async_resolve_cycle(
        CycleRequest(
            observation=CycleObservation(
                entry_id="notice-entry",
                lockname="front",
                managed_slots=frozenset({1, 2}),
                observed_codes={"4831": 1},
                unreadable_slots=frozenset(),
            ),
            adoptions=[
                AdoptionRequest(
                    entry_id="notice-entry",
                    identity_key="notice-owner-one",
                    code="4831",
                    code_length=4,
                    lockname="front",
                    slot=1,
                ),
                AdoptionRequest(
                    entry_id="notice-entry",
                    identity_key="notice-owner-two",
                    code="4831",
                    code_length=4,
                    lockname="front",
                    slot=2,
                ),
            ],
            rekeys=[],
            allocations=[],
            active_keys={"notice-owner-one", "notice-owner-two"},
        )
    )
    assert set(result.adopted) == {"notice-owner-one", "notice-owner-two"}
    assert notices == [
        (
            "ready",
            store.loaded_result.generation,
            {"notice-owner-one", "notice-owner-two"},
        )
    ]


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_absent_store_commits_genesis_and_restart_preserves_it(hass) -> None:
    """First activation acknowledges v2 before a later runtime may use it."""
    Path(hass.config.path(".storage")).mkdir(parents=True, exist_ok=True)
    _clear_registry_fixture(hass)
    allocator = DoorCodeAllocator(hass)
    allocator._protection_required = True
    hass.data[DOMAIN] = {ALLOCATOR: allocator}

    await allocator.async_load()

    first_store = hass.data[DOMAIN][PROTECTED_REGISTRY_STORE]
    first = first_store.loaded_result
    assert first_store.authorization_state.value == "ready"
    assert first is not None
    assert first.state is ProtectionLoadState.VALID_V2
    assert first.data is not None
    assert first.data["generation"] == 1
    assert allocator._registry_missing
    assert allocator._registry_lost
    committed = _registry_bytes(hass)
    committed_salt = first.data["ledger"]["code_ref_salt"]
    assert committed_salt

    restarted = DoorCodeAllocator(hass)
    restarted._protection_required = True
    hass.data[DOMAIN][ALLOCATOR] = restarted

    await restarted.async_load()

    second_store = hass.data[DOMAIN][PROTECTED_REGISTRY_STORE]
    second = second_store.loaded_result
    assert second_store.authorization_state.value == "ready"
    assert second is not None
    assert second.state is ProtectionLoadState.VALID_V2
    assert second.data is not None
    assert second.data["generation"] == 1
    assert second.data["ledger"]["code_ref_salt"] == committed_salt
    assert not restarted._registry_missing
    assert not restarted._registry_lost
    assert _registry_bytes(hass) == committed


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_v1_activation_rejects_mismatched_live_registry_without_rewrite(
    hass,
) -> None:
    """A valid disk ledger cannot silently replace a divergent live writer."""
    Path(hass.config.path(".storage")).mkdir(parents=True, exist_ok=True)
    _clear_registry_fixture(hass)
    await _write_v1_store(hass, _legacy_registry())
    legacy_bytes = _registry_bytes(hass)

    allocator = DoorCodeAllocator(hass)
    allocator._protection_required = True
    allocator._registry = AllocationRegistry(code_ref_salt="different-live-ledger")
    hass.data[DOMAIN] = {ALLOCATOR: allocator}

    await allocator.async_load()

    protected = hass.data[DOMAIN][PROTECTED_REGISTRY_STORE]
    assert protected.authorization_state.value == "protective_unknown"
    assert protected.last_result is not None
    assert allocator._registry.records == {}
    assert _registry_bytes(hass) == legacy_bytes


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_protected_unresolved_cycle_withholds_adoption_and_issuance(hass) -> None:
    """Unverified observations cannot reserve codes, even for no-op cycles."""
    Path(hass.config.path(".storage")).mkdir(parents=True, exist_ok=True)
    _clear_registry_fixture(hass)
    allocator = DoorCodeAllocator(hass)
    allocator._protection_required = True
    hass.data[DOMAIN] = {ALLOCATOR: allocator}
    await allocator.async_load()

    entry = _lockless_entry("legacy-unverified")
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_LOCK_ENTRY: "front_door"}
    )
    observation = CycleObservation(
        entry_id=entry.entry_id,
        lockname="front_door",
        managed_slots=frozenset({10}),
        observed_codes={"2468": 10},
        unreadable_slots=frozenset(),
    )
    adoption = AdoptionRequest(
        entry_id=entry.entry_id,
        identity_key="guest",
        code="2468",
        code_length=4,
        lockname="front_door",
        slot=10,
    )
    before_registry = deepcopy(allocator._registry)
    before_pending = deepcopy(allocator._pending_adoption)
    for allocations in (
        [],
        [
            AllocationRequest(
                entry_id=entry.entry_id,
                identity_key="guest",
                preferred_code="2468",
                code_length=4,
                lockname="front_door",
                slot=10,
            )
        ],
    ):
        result = await allocator.async_resolve_cycle(
            CycleRequest(
                observation=observation,
                adoptions=[adoption],
                rekeys=[],
                allocations=allocations,
                active_keys={"guest"},
                observed_reservations=(adoption,),
            )
        )
        assert result.blocked_reason == "protective_unknown"
        assert result.adopted == {}
        assert all(
            allocation.code is None and allocation.reason == "protective_unknown"
            for allocation in result.allocated.values()
        )
        assert allocator._registry == before_registry
        assert allocator._pending_adoption == before_pending

    direct = await allocator.async_allocate(
        AllocationRequest(
            entry_id=entry.entry_id,
            identity_key="guest",
            preferred_code="2468",
            code_length=4,
            lockname="front_door",
            slot=10,
        )
    )
    direct_adoption = await allocator.async_adopt(adoption)
    assert direct.code is None and direct.reason == "protective_unknown"
    assert direct_adoption.code is None
    assert direct_adoption.reason == "protective_unknown"
    disguised = await allocator.async_allocate(
        AllocationRequest(
            entry_id=entry.entry_id,
            identity_key="guest",
            preferred_code="2468",
            code_length=4,
        )
    )
    assert disguised.code is None and disguised.reason == "protective_unknown"
    preview = await allocator.async_preview_reissue(
        ReissuePreviewRequest(
            entry_id=entry.entry_id,
            identity_key="guest",
            preferred_code="2468",
            code_length=4,
            lockname="front_door",
            slot=10,
        )
    )
    assert preview.replacement_code is None
    assert preview.reason == "protective_unknown"
    lockless_preview = await allocator.async_preview_reissue(
        ReissuePreviewRequest(
            entry_id=entry.entry_id,
            identity_key="guest",
            preferred_code="2468",
            code_length=4,
            lockname=None,
            slot=None,
        )
    )
    assert lockless_preview.replacement_code is None
    assert lockless_preview.reason == "protective_unknown"
    assert allocator._registry == before_registry


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize("change", ["lifecycle", "commit_in_flight"])
async def test_noop_protected_cycle_withholds_code_after_authorization_changes(
    hass, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    """A no-op cycle cannot return an existing code after authority changes."""
    coordinator, store, _entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage"
    )
    allocator = get_allocator(hass)
    assert allocator is not None
    lifecycle = get_keymaster_lifecycle(hass, _RC_ENTRY_ID)
    assert lifecycle is not None
    before_registry = deepcopy(allocator._registry)
    original_resolve = issuance._resolve_cycle_unlocked
    original_commit = issuance.async_commit_staged_registry
    candidate_codes: list[str | None] = []
    in_flight: list[int] = []

    def observe_candidate(*args: Any, **kwargs: Any) -> Any:
        """Record the real staged result before revoking its authorization."""
        result = original_resolve(*args, **kwargs)
        candidate_codes.append(result.allocated[_OWNER_IDENTITY].code)
        return result

    async def revoke_before_commit(
        current_allocator: Any, baseline: Any, staged: Any, **kwargs: Any
    ) -> Any:
        """Invalidate authorization after calculation but before publication."""
        assert not registry_changed(baseline, staged._registry)
        if change == "lifecycle":
            stop_keymaster_lifecycle(hass, _RC_ENTRY_ID)
        else:
            in_flight.append(store._begin_commit_operation())
        return await original_commit(current_allocator, baseline, staged, **kwargs)

    monkeypatch.setattr(issuance, "_resolve_cycle_unlocked", observe_candidate)
    monkeypatch.setattr(issuance, "async_commit_staged_registry", revoke_before_commit)
    observation = CycleObservation(
        entry_id=_RC_ENTRY_ID,
        lockname="lock.consumer",
        managed_slots=frozenset({_SLOT}),
        observed_codes={_CODE: _SLOT},
        unreadable_slots=frozenset(),
        keymaster_entry_id=lifecycle.keymaster_entry_id,
        keymaster_generation=lifecycle.generation,
    )
    allocation = AllocationRequest(
        entry_id=_RC_ENTRY_ID,
        identity_key=_OWNER_IDENTITY,
        preferred_code=_CODE,
        code_length=len(_CODE),
        lockname="lock.consumer",
        slot=_SLOT,
    )
    try:
        result = await allocator.async_resolve_cycle(
            CycleRequest(
                observation=observation,
                adoptions=[],
                rekeys=[],
                allocations=[allocation],
                active_keys={_OWNER_IDENTITY},
            )
        )
    finally:
        if in_flight:
            store._apply_commit_completion(
                ProtectionLoadResult(ProtectionLoadState.UNAVAILABLE),
                operation_id=in_flight[0],
            )
        stop_keymaster_lifecycle(hass, _RC_ENTRY_ID)

    assert candidate_codes == [_CODE]
    assert result.blocked_reason == "protective_unknown"
    assert result.allocated[_OWNER_IDENTITY].code is None
    assert allocator._registry == before_registry
    assert coordinator.config_entry.entry_id == _RC_ENTRY_ID


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_protected_cycle_rejects_mismatched_observation_owner(hass) -> None:
    """A lockless observation cannot authorize a different lock's adoption."""
    Path(hass.config.path(".storage")).mkdir(parents=True, exist_ok=True)
    _clear_registry_fixture(hass)
    allocator = DoorCodeAllocator(hass)
    allocator._protection_required = True
    hass.data[DOMAIN] = {ALLOCATOR: allocator}
    await allocator.async_load()
    entry = _lockless_entry("lockless-mismatch")
    entry.add_to_hass(hass)
    adoption = AdoptionRequest(
        entry_id=entry.entry_id,
        identity_key="guest",
        code="2468",
        code_length=4,
        lockname="other_lock",
        slot=10,
    )
    before_registry = deepcopy(allocator._registry)
    result = await allocator.async_resolve_cycle(
        CycleRequest(
            observation=CycleObservation(
                entry_id=entry.entry_id,
                lockname=None,
                managed_slots=frozenset(),
                observed_codes={},
                unreadable_slots=frozenset(),
            ),
            adoptions=[adoption],
            rekeys=[],
            allocations=[],
            active_keys={"guest"},
            observed_reservations=(adoption,),
        )
    )
    assert result.blocked_reason == "protective_unknown"
    assert result.adopted == {}
    assert allocator._registry == before_registry


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_failed_first_allocation_commit_does_not_publish_staged_owner(
    hass,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed commits discard staged state and its user-visible notices."""
    from custom_components.rental_control.allocator import registry_commit

    Path(hass.config.path(".storage")).mkdir(parents=True, exist_ok=True)
    _clear_registry_fixture(hass)
    allocator = DoorCodeAllocator(hass)
    allocator._protection_required = True
    hass.data[DOMAIN] = {ALLOCATOR: allocator}
    await allocator.async_load()

    before = deepcopy(allocator._registry)
    notices: list[str] = []

    def capture_notice(_hass, message: str, **_kwargs: Any) -> None:
        """Record staged user notices without creating HA notification state."""
        notices.append(message)

    monkeypatch.setattr(allocator_module, "async_create", capture_notice)

    def reject_marker_write(_path: Path, _data: bytes, _maximum: int) -> bool:
        """Model a failed marker acknowledgement before publication."""
        return False

    monkeypatch.setattr(
        registry_commit, "_atomic_write_acknowledged", reject_marker_write
    )
    # Commit-failure behavior is independent of the observation authorization.
    monkeypatch.setattr(
        "custom_components.rental_control.allocator.issuance.observation_preflight",
        lambda *_args: None,
    )
    result = await allocator.async_resolve_cycle(
        CycleRequest(
            observation=CycleObservation(
                entry_id="lockless-entry",
                lockname="front",
                managed_slots=frozenset({1, 2}),
                observed_codes={"4831": 1},
                unreadable_slots=frozenset(),
            ),
            adoptions=[
                AdoptionRequest(
                    entry_id="lockless-entry",
                    identity_key="reservation-one",
                    code="4831",
                    code_length=4,
                    lockname="front",
                    slot=1,
                ),
                AdoptionRequest(
                    entry_id="lockless-entry",
                    identity_key="reservation-two",
                    code="4831",
                    code_length=4,
                    lockname="front",
                    slot=2,
                ),
            ],
            rekeys=[],
            allocations=[],
            active_keys={"reservation-one", "reservation-two"},
        )
    )

    assert not result.adopted
    assert result.released.released == []
    assert allocator._registry == before
    assert notices == []
    assert allocator._registry_lost
    assert allocator._registry_missing
    protected = hass.data[DOMAIN][PROTECTED_REGISTRY_STORE]
    assert protected.authorization_state.value == "protective_unknown"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_absent_disk_cannot_replace_already_published_owner_state(hass) -> None:
    """A missing file plus live owner is unknown, never a fresh empty genesis."""
    Path(hass.config.path(".storage")).mkdir(parents=True, exist_ok=True)
    _clear_registry_fixture(hass)
    allocator = DoorCodeAllocator(hass)
    allocator._protection_required = True
    allocator._registry = _legacy_registry()
    hass.data[DOMAIN] = {ALLOCATOR: allocator}

    await allocator.async_load()

    protected = hass.data[DOMAIN][PROTECTED_REGISTRY_STORE]
    assert protected.authorization_state.value == "protective_unknown"
    assert allocator._registry.records == {}
    assert not Path(hass.config.path(".storage", STORE_CODE_REGISTRY_KEY)).exists()
    assert not Path(
        hass.config.path(".storage", "rental_control.code_registry_protection")
    ).exists()


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_v2_activation_rejects_mismatched_delayed_legacy_writer(hass) -> None:
    """A stale v1 callback cannot bless or overwrite a different v2 ledger."""
    Path(hass.config.path(".storage")).mkdir(parents=True, exist_ok=True)
    _clear_registry_fixture(hass)
    expected = _legacy_registry()
    await _write_v1_store(hass, expected)

    initial = DoorCodeAllocator(hass)
    initial._protection_required = True
    hass.data[DOMAIN] = {ALLOCATOR: initial}
    await initial.async_load()
    before = _registry_bytes(hass)

    stale = deepcopy(expected)
    stale.records[_CODE].owners[0].identity_key = "stale-v1-owner"
    stale.rebuild_index()
    stale_payload = serialize_registry_ledger(stale)
    restarted = DoorCodeAllocator(hass)
    restarted._protection_required = True
    restarted._store._store.async_delay_save(lambda: deepcopy(stale_payload), delay=60)
    hass.data[DOMAIN][ALLOCATOR] = restarted

    await restarted.async_load()

    protected = hass.data[DOMAIN][PROTECTED_REGISTRY_STORE]
    assert protected.authorization_state.value == "protective_unknown"
    assert protected.last_result is not None
    legacy_store = restarted._store._store
    assert legacy_store._read_only
    assert legacy_store._delay_handle is None
    await legacy_store._async_callback_delayed_write()
    assert _registry_bytes(hass) == before


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_activation_writer_quiesce_failure_stays_non_raising(
    hass,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed v1-writer shutdown keeps activation protective-unknown."""
    allocator = DoorCodeAllocator(hass)
    allocator._protection_required = True
    hass.data[DOMAIN] = {ALLOCATOR: allocator}
    allocator._store._store.async_delay_save(lambda: {"records": []}, delay=60)

    def fail_read_only() -> None:
        """Simulate an inability to stop the pending legacy writer."""
        raise OSError("synthetic writer failure")

    monkeypatch.setattr(allocator._store._store, "make_read_only", fail_read_only)

    await allocator.async_load()

    protected = hass.data[DOMAIN][PROTECTED_REGISTRY_STORE]
    assert protected.authorization_state.value == "protective_unknown"
    assert allocator._registry.records == {}


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_protected_rekey_refuses_an_unproven_owner_transition(hass) -> None:
    """A persisted but unattributed owner's rekey cannot bypass the safety gate."""
    Path(hass.config.path(".storage")).mkdir(parents=True, exist_ok=True)
    _clear_registry_fixture(hass)
    expected = _legacy_registry()
    await _write_v1_store(hass, expected)

    allocator = DoorCodeAllocator(hass)
    allocator._protection_required = True
    hass.data[DOMAIN] = {ALLOCATOR: allocator}
    await allocator.async_load()

    assert not await allocator.async_rekey(
        "activation-reservation", "renamed-activation-reservation"
    )
    existing = allocator._registry.record_for_identity("activation-reservation")
    assert existing is not None
    assert existing.code == _CODE
    assert (
        allocator._registry.record_for_identity("renamed-activation-reservation")
        is None
    )
    protected = hass.data[DOMAIN][PROTECTED_REGISTRY_STORE]
    assert protected.loaded_result is not None
    assert protected.loaded_result.data is not None
    owners = [
        owner
        for record in protected.loaded_result.data["ledger"]["records"]
        for owner in record["owners"]
    ]
    assert any(owner["identity_key"] == "activation-reservation" for owner in owners)
    assert not any(
        owner["identity_key"] == "renamed-activation-reservation" for owner in owners
    )


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_actual_setup_migrates_v1_for_two_lockless_entries(hass) -> None:
    """The first and later RC entries share one safely migrated allocator."""
    Path(hass.config.path(".storage")).mkdir(parents=True, exist_ok=True)
    _clear_registry_fixture(hass)
    expected = _legacy_registry()
    await _write_v1_store(hass, expected)

    first = _lockless_entry("activation-first-entry")
    second = _lockless_entry("activation-second-entry")
    await _setup_lockless_entry(hass, first)
    await _setup_lockless_entry(hass, second)

    allocator = get_allocator(hass)
    assert allocator is not None
    store = hass.data[DOMAIN][PROTECTED_REGISTRY_STORE]
    assert store.loaded_result is not None
    assert store.loaded_result.state is ProtectionLoadState.VALID_V2
    assert store.loaded_result.data is not None
    records = store.loaded_result.data["ledger"]["records"]
    expected_owners = [
        owner
        for record in serialize_registry_ledger(expected)["records"]
        for owner in record["owners"]
    ]
    actual_owners = [owner for record in records for owner in record["owners"]]
    assert all(owner in actual_owners for owner in expected_owners)
    assert {
        first.entry_id,
        second.entry_id,
    } <= {owner["entry_id"] for owner in actual_owners}
    assert hass.data[DOMAIN][first.entry_id][COORDINATOR].last_update_success
    assert hass.data[DOMAIN][second.entry_id][COORDINATOR].last_update_success
    assert hass.data[DOMAIN][first.entry_id][COORDINATOR]._latest_res_by_key
    assert hass.data[DOMAIN][second.entry_id][COORDINATOR]._latest_res_by_key


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_protected_lockless_sweep_and_orphan_cleanup_commit(hass) -> None:
    """Protected release paths acknowledge lockless owners before publication."""
    first = _lockless_entry("protected-sweep-entry")
    await _setup_lockless_entry(hass, first)
    allocator = get_allocator(hass)
    assert allocator is not None
    store = hass.data[DOMAIN][PROTECTED_REGISTRY_STORE]

    first_identities = {
        owner.identity_key
        for record in allocator._registry.records.values()
        for owner in record.owners
        if owner.entry_id == first.entry_id
    }
    assert first_identities
    assert all(
        owner.lockname is None and owner.slot is None
        for record in allocator._registry.records.values()
        for owner in record.owners
        if owner.identity_key in first_identities
    )

    swept = await allocator.async_sweep(
        CycleObservation(
            first.entry_id,
            None,
            frozenset(),
            {},
            frozenset(),
        ),
        active_keys=set(),
    )
    assert {item.identity_key for item in swept.released} == first_identities, (
        store.last_result.failure_code if store.last_result is not None else None
    )
    assert not swept.retained
    assert store.loaded_result is not None
    assert store.loaded_result.data is not None
    durable_after_sweep = {
        owner["identity_key"]
        for record in store.loaded_result.data["ledger"]["records"]
        for owner in record["owners"]
    }
    assert not first_identities.intersection(durable_after_sweep)

    orphan = await allocator.async_allocate(
        AllocationRequest(
            entry_id="removed-lockless-entry",
            identity_key="removed-lockless-reservation",
            preferred_code="5927",
            code_length=4,
        )
    )
    assert orphan.code == "5927"
    orphan_identities = {"removed-lockless-reservation"}

    preview = await allocator.async_clear_orphans(set(), [], dry_run=True)
    assert preview.dry_run
    assert {item.identity_key for item in preview.cleared} == orphan_identities
    assert orphan_identities <= allocator._registry.by_identity.keys()

    cleared = await allocator.async_clear_orphans(set(), [], dry_run=False)
    assert not cleared.dry_run
    assert {item.identity_key for item in cleared.cleared} == orphan_identities
    assert not orphan_identities.intersection(allocator._registry.by_identity)
    assert store.loaded_result is not None
    assert store.loaded_result.data is not None
    durable_after_cleanup = {
        owner["identity_key"]
        for record in store.loaded_result.data["ledger"]["records"]
        for owner in record["owners"]
    }
    assert not orphan_identities.intersection(durable_after_cleanup)

    removed = await allocator.async_allocate(
        AllocationRequest(
            entry_id="removed-lockless-entry",
            identity_key="removed-lockless-owner",
            preferred_code="6842",
            code_length=4,
        )
    )
    assert removed.code == "6842"

    report = await allocator.async_mark_entry_removed("removed-lockless-entry")

    assert {item.identity_key for item in report.released} == {"removed-lockless-owner"}
    assert not report.retained
    assert "removed-lockless-owner" not in allocator._registry.by_identity
    assert store.loaded_result is not None
    assert store.loaded_result.data is not None
    assert all(
        owner["identity_key"] != "removed-lockless-owner"
        for record in store.loaded_result.data["ledger"]["records"]
        for owner in record["owners"]
    )


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize("failed_write", (1, 2, 3))
async def test_failed_activation_keeps_two_lockless_entries_calendar_only(
    hass,
    tmp_path: Path,
    failed_write: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown activation preserves calendar tracking but issues no codes."""
    monkeypatch.setattr(
        hass.config,
        "path",
        lambda *parts: str(tmp_path.joinpath(*parts)),
    )
    Path(hass.config.path(".storage")).mkdir(parents=True, exist_ok=True)
    legacy = AllocationRegistry(code_ref_salt="lockless-activation-salt")
    await _write_v1_store(hass, legacy)
    allocator = DoorCodeAllocator(hass)
    allocator._protection_required = True
    hass.data[DOMAIN] = {ALLOCATOR: allocator}
    original_write = registry_commit._atomic_write_acknowledged
    write_count = 0

    def fail_one_boundary(path: Path, data: bytes, maximum: int) -> bool:
        """Stop the selected migration write before touching disk."""
        nonlocal write_count
        write_count += 1
        if write_count == failed_write:
            return False
        return original_write(path, data, maximum)

    monkeypatch.setattr(
        registry_commit, "_atomic_write_acknowledged", fail_one_boundary
    )
    await allocator.async_load()
    assert write_count == failed_write
    assert (
        hass.data[DOMAIN][PROTECTED_REGISTRY_STORE].authorization_state.value
        == "protective_unknown"
    )

    first = _lockless_entry("activation-unknown-first")
    second = _lockless_entry("activation-unknown-second")
    await _setup_lockless_entry(hass, first)
    await _setup_lockless_entry(hass, second)

    for entry in (first, second):
        coordinator = hass.data[DOMAIN][entry.entry_id][COORDINATOR]
        assert coordinator.last_update_success
        assert coordinator._latest_res_by_key
        assert all(
            reservation.slot_code is None
            for reservation in coordinator._latest_res_by_key.values()
        )
    assert get_allocator(hass) is allocator


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize("release", _RELEASES)
@pytest.mark.parametrize("failed_write", (1, 2, 3))
@pytest.mark.parametrize("fail_after_write", (False, True))
async def test_v1_activation_write_failures_stay_protective_unknown(
    hass,
    tmp_path: Path,
    release: str,
    failed_write: int,
    fail_after_write: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every migration boundary blocks real consumers until restart recovery."""
    monkeypatch.setattr(
        hass.config,
        "path",
        lambda *parts: str(tmp_path.joinpath(*parts)),
    )
    coordinator, _initial_store, entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage", release
    )
    events: list[dict[str, Any]] = []
    await _register_recording_services(hass, _initial_store, entity_ids, events)
    original_allocator = get_allocator(hass)
    assert original_allocator is not None
    legacy_registry = deepcopy(original_allocator._registry)
    assert legacy_registry.records[_CODE].owners[0].identity_key == _OWNER_IDENTITY
    payload = await _write_v1_store(hass, legacy_registry)
    Path(
        hass.config.path(".storage", "rental_control.code_registry_protection")
    ).unlink(missing_ok=True)

    allocator = DoorCodeAllocator(hass)
    allocator._protection_required = True
    hass.data[DOMAIN][ALLOCATOR] = allocator
    legacy_store = allocator._store._store
    legacy_store.async_delay_save(lambda: deepcopy(payload), delay=60)
    original_write = registry_commit._atomic_write_acknowledged
    write_count = 0

    def fail_one_boundary(path: Path, data: bytes, maximum: int) -> bool:
        """Simulate failure immediately before/after one acknowledged write."""
        nonlocal write_count
        write_count += 1
        if write_count == failed_write:
            if fail_after_write:
                assert original_write(path, data, maximum)
                return False
            return False
        return original_write(path, data, maximum)

    monkeypatch.setattr(
        registry_commit, "_atomic_write_acknowledged", fail_one_boundary
    )
    await allocator.async_load()

    protected = hass.data[DOMAIN][PROTECTED_REGISTRY_STORE]
    assert protected.authorization_state.value == "protective_unknown"
    unknown_issue = async_get_issue_registry(hass).async_get_issue(
        DOMAIN, "protected_registry_unknown"
    )
    assert unknown_issue is not None
    assert unknown_issue.translation_key == "protected_registry_unknown"
    allocation = await allocator.async_allocate(
        AllocationRequest(
            entry_id="activation-rc",
            identity_key="new-guest",
            preferred_code="9384",
            code_length=4,
            lockname="front-door",
            slot=22,
        )
    )
    assert allocation.code is None
    assert allocation.reason == "protective_unknown"
    assert write_count == failed_write
    preview = await allocator.async_preview_reissue(
        ReissuePreviewRequest(
            entry_id="activation-rc",
            identity_key="activation-reservation",
            preferred_code="9384",
            code_length=4,
            lockname="front-door",
            slot=21,
            observed_code=_CODE,
        )
    )
    assert preview.reason == "protective_unknown"

    reuse = await allocator.async_allocate(
        AllocationRequest(
            entry_id=_RC_ENTRY_ID,
            identity_key=_OWNER_IDENTITY,
            preferred_code=_CODE,
            code_length=len(_CODE),
            lockname="lock.consumer",
            slot=_SLOT,
        )
    )
    assert reuse.code is None
    assert reuse.reason == "protective_unknown"
    new_issuance = await allocator.async_allocate(
        AllocationRequest(
            entry_id=_RC_ENTRY_ID,
            identity_key="new-activation-reservation",
            preferred_code="9384",
            code_length=4,
            lockname="lock.consumer",
            slot=_SLOT + 1,
        )
    )
    assert new_issuance.code is None
    assert new_issuance.reason == "protective_unknown"
    cycle = await allocator.async_resolve_cycle(
        CycleRequest(
            observation=CycleObservation(
                _RC_ENTRY_ID,
                "lock.consumer",
                frozenset({_SLOT}),
                {},
                frozenset(),
            ),
            adoptions=[],
            rekeys=[],
            allocations=[
                AllocationRequest(
                    entry_id=_RC_ENTRY_ID,
                    identity_key="cycle-activation-reservation",
                    preferred_code="9384",
                    code_length=4,
                    lockname="lock.consumer",
                    slot=_SLOT,
                )
            ],
            active_keys={"cycle-activation-reservation"},
            forced_reissues=(
                ForcedReissueDirective(
                    _RC_ENTRY_ID, _OWNER_IDENTITY, "lock.consumer", _SLOT
                ),
            ),
        )
    )
    assert (
        cycle.allocated["cycle-activation-reservation"].reason == "protective_unknown"
    )
    assert cycle.released.released == []
    assert cycle.reissues[0].retention_reason == "protective_unknown"
    set_result = await coordinator.event_overrides._apply_set(
        coordinator,
        _SLOT,
        SimpleNamespace(identity_key=_OWNER_IDENTITY),
        "activation-boundary",
    )
    assert set_result.unconfirmed
    clear_result = await coordinator.event_overrides._apply_clear(
        coordinator,
        _SLOT,
        owner_identity_key=_OWNER_IDENTITY,
    )
    assert clear_result.unconfirmed
    removed = await allocator.async_mark_entry_removed(_RC_ENTRY_ID)
    assert removed.released == []
    assert write_count == failed_write
    assert events == []

    # A quiesced delayed v1 writer cannot overwrite even an ambiguous partial
    # commit, and restart resolves the surviving bytes without a hardware call.
    assert legacy_store._read_only
    assert legacy_store._delay_handle is None
    before_delayed_callback = _registry_bytes(hass)
    await legacy_store._async_callback_delayed_write()
    assert _registry_bytes(hass) == before_delayed_callback
    monkeypatch.setattr(registry_commit, "_atomic_write_acknowledged", original_write)
    restarted = DoorCodeAllocator(hass)
    restarted._protection_required = True
    hass.data[DOMAIN][ALLOCATOR] = restarted
    await restarted.async_load()
    reloaded = hass.data[DOMAIN][PROTECTED_REGISTRY_STORE]
    assert reloaded.authorization_state.value == "ready"
    assert reloaded.loaded_result is not None
    assert reloaded.loaded_result.state is ProtectionLoadState.VALID_V2
    assert reloaded.loaded_result.data is not None
    assert any(
        owner["entry_id"] == _RC_ENTRY_ID and owner["identity_key"] == _OWNER_IDENTITY
        for record in reloaded.loaded_result.data["ledger"]["records"]
        for owner in record["owners"]
    )
    assert events == []


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize("release", _RELEASES)
async def test_missing_protection_store_blocks_allocator_and_clear_consumers(
    hass,
    tmp_path: Path,
    release: str,
) -> None:
    """No missing-store legacy fallback authorizes reuse, release, or reset."""
    coordinator, _store, entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage", release
    )
    events: list[dict[str, Any]] = []
    await _register_recording_services(hass, _store, entity_ids, events)
    allocator = get_allocator(hass)
    assert allocator is not None
    hass.data[DOMAIN].pop(PROTECTED_REGISTRY_STORE)

    allocation = await allocator.async_allocate(
        AllocationRequest(
            entry_id=_RC_ENTRY_ID,
            identity_key="new-reservation",
            preferred_code="9384",
            code_length=4,
            lockname="lock.consumer",
            slot=_SLOT + 1,
        )
    )
    assert allocation.code is None
    assert allocation.reason == "protective_unknown"

    reuse = await allocator.async_allocate(
        AllocationRequest(
            entry_id=_RC_ENTRY_ID,
            identity_key=_OWNER_IDENTITY,
            preferred_code=_CODE,
            code_length=len(_CODE),
            lockname="lock.consumer",
            slot=_SLOT,
        )
    )
    assert reuse.code is None
    assert reuse.reason == "protective_unknown"
    preview = await allocator.async_preview_reissue(
        ReissuePreviewRequest(
            entry_id=_RC_ENTRY_ID,
            identity_key=_OWNER_IDENTITY,
            preferred_code="9384",
            code_length=4,
            lockname="lock.consumer",
            slot=_SLOT,
            observed_code=_CODE,
        )
    )
    assert preview.reason == "protective_unknown"

    cycle = await allocator.async_resolve_cycle(
        CycleRequest(
            observation=CycleObservation(
                _RC_ENTRY_ID,
                "lock.consumer",
                frozenset({_SLOT}),
                {},
                frozenset(),
            ),
            adoptions=[],
            rekeys=[],
            allocations=[
                AllocationRequest(
                    entry_id=_RC_ENTRY_ID,
                    identity_key="missing-store-cycle-reservation",
                    preferred_code="9384",
                    code_length=4,
                    lockname="lock.consumer",
                    slot=_SLOT,
                )
            ],
            active_keys={"missing-store-cycle-reservation"},
            forced_reissues=(
                ForcedReissueDirective(
                    _RC_ENTRY_ID, _OWNER_IDENTITY, "lock.consumer", _SLOT
                ),
            ),
        )
    )
    assert (
        cycle.allocated["missing-store-cycle-reservation"].reason
        == "protective_unknown"
    )
    assert cycle.released.released == []
    assert cycle.reissues[0].retention_reason == "protective_unknown"

    set_result = await coordinator.event_overrides._apply_set(
        coordinator,
        _SLOT,
        SimpleNamespace(identity_key=_OWNER_IDENTITY),
        "missing-store",
    )
    assert set_result.unconfirmed
    clear_result = await coordinator.event_overrides._apply_clear(
        coordinator,
        _SLOT,
        owner_identity_key=_OWNER_IDENTITY,
    )
    assert clear_result.unconfirmed
    assert events == []

    removed = await allocator.async_mark_entry_removed(_RC_ENTRY_ID)
    assert removed.released == []
    assert removed.retained
    assert events == []


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_registry_size_limit_retains_existing_owner_and_fails_closed(
    hass,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A full registry never publishes a staged release or loses its owner."""
    monkeypatch.setattr(
        hass.config,
        "path",
        lambda *parts: str(tmp_path.joinpath(*parts)),
    )
    _coordinator, _store, _entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage"
    )
    allocator = get_allocator(hass)
    assert allocator is not None
    # The fixture persists a previously verified owner directly rather than
    # driving a first coordinator cycle for this entry.
    allocator._pending_adoption.clear()
    store = hass.data[DOMAIN][PROTECTED_REGISTRY_STORE]
    original_bytes = _registry_bytes(hass)
    original_writer = registry_commit.MAX_REGISTRY_BYTES
    monkeypatch.setattr(registry_commit, "MAX_REGISTRY_BYTES", 1)

    allocation = await allocator.async_allocate(
        AllocationRequest(
            entry_id="capacity-pressure-lockless",
            identity_key="capacity-pressure-new-owner",
            preferred_code="9384",
            code_length=4,
        )
    )
    assert allocation.code is None
    assert allocation.reason == "protective_unknown"
    assert registry_commit.MAX_REGISTRY_BYTES == 1
    assert store.authorization_state.value == "protective_unknown"
    assert store.last_result is not None
    assert store.last_result.failure_code == "transaction_preflight_failed"

    retained = await allocator.async_mark_entry_removed(_RC_ENTRY_ID)
    assert retained.released == []
    assert any(owner.identity_key == _OWNER_IDENTITY for owner in retained.retained)
    assert allocator._registry.records[_CODE].owners[0].identity_key == (
        _OWNER_IDENTITY
    )
    assert _registry_bytes(hass) == original_bytes
    assert original_writer > len(original_bytes)


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_protected_restart_retains_pending_clear_without_hardware_replay(
    hass,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An acknowledged pending-clear fence remains after loading fresh bytes."""
    coordinator, store, entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage", "v0.6.1"
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
        required_properties=(
            f"button.code_slots:{_SLOT}.reset",
            f"text.code_slots:{_SLOT}.name",
            f"text.code_slots:{_SLOT}.pin",
        ),
        fence_type="pending_clear",
    )
    assert clear is not None
    loaded = store.loaded_result
    assert loaded is not None and loaded.data is not None
    assert any(
        fence["fence_type"] == "pending_clear"
        and fence["original_km_entry_id"] == clear.keymaster_entry_id
        and fence["slot"] == _SLOT
        for fence in loaded.data["pending_fences"]
    )
    allocator = get_allocator(hass)
    assert allocator is not None
    restarted = ProtectedRegistryStore(
        hass, allocator._lock, storage_dir=tmp_path / ".storage"
    )
    result = await restarted.async_load()
    assert result.state is ProtectionLoadState.VALID_V2
    assert restarted.loaded_result is not None
    assert restarted.loaded_result.data is not None
    assert any(
        fence["fence_id"] == clear.fence_id and fence["fence_type"] == "pending_clear"
        for fence in restarted.loaded_result.data["pending_fences"]
    )

    monkeypatch.setattr(
        hass.config,
        "path",
        lambda *parts: str(tmp_path.joinpath(*parts)),
    )
    restarted_allocator = DoorCodeAllocator(hass)
    restarted_allocator._protection_required = True
    hass.data[DOMAIN][ALLOCATOR] = restarted_allocator
    await restarted_allocator.async_load()
    reloaded_store = hass.data[DOMAIN][PROTECTED_REGISTRY_STORE]
    assert reloaded_store.loaded_result is not None
    assert reloaded_store.loaded_result.state is ProtectionLoadState.VALID_V2
    assert restarted_allocator._registry.records[_CODE].owners[0].identity_key == (
        _OWNER_IDENTITY
    )
    assert any(
        fence["fence_id"] == clear.fence_id and fence["fence_type"] == "pending_clear"
        for fence in reloaded_store.loaded_result.data["pending_fences"]
    )
    assert events == []
    assert coordinator.config_entry.entry_id == _RC_ENTRY_ID


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize("release", _RELEASES)
@pytest.mark.parametrize("failed_write", (1, 2, 3))
@pytest.mark.parametrize("fail_after_write", (False, True))
async def test_failed_v2_commit_restarts_with_owner_hold_and_clear_fence(
    hass,
    tmp_path: Path,
    release: str,
    failed_write: int,
    fail_after_write: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every uncertain write boundary retains existing ledger and safety claims."""
    monkeypatch.setattr(
        hass.config,
        "path",
        lambda *parts: str(tmp_path.joinpath(*parts)),
    )
    second_rc_entry_id = "activation-second-rc"
    coordinator, store, entity_ids = await _setup_verified_consumer(
        hass,
        tmp_path / ".storage",
        release,
        second_rc_entry_id=second_rc_entry_id,
    )
    events: list[dict[str, Any]] = []
    await _register_recording_services(hass, store, entity_ids, events)
    allocator = get_allocator(hass)
    assert allocator is not None
    initial = store.loaded_result
    assert initial is not None and initial.data is not None
    initial_association = store.association_for_entry(_RC_ENTRY_ID)
    assert initial_association is not None

    clear = await async_prepare_verified_operation_intent(
        hass,
        _RC_ENTRY_ID,
        slot=_SLOT,
        owner_identity_key=_OWNER_IDENTITY,
        required_properties=(
            f"button.code_slots:{_SLOT}.reset",
            f"text.code_slots:{_SLOT}.name",
            f"text.code_slots:{_SLOT}.pin",
        ),
        fence_type="pending_clear",
    )
    assert clear is not None
    old_association = store.association_for_entry(_RC_ENTRY_ID)
    association_base = store.loaded_result
    references = store.association_references_for_entry(_RC_ENTRY_ID)
    assert old_association is not None and association_base is not None
    assert references

    association_tx = secrets.token_hex(16)
    association_generation = old_association.association_generation + 1
    transaction_generation = association_base.generation + 1
    selected_association = AssociationTuple(
        old_association.legacy_lock_selection_present,
        old_association.legacy_lock_selection_raw,
        "reselected-keymaster",
        association_generation,
        association_tx,
        AssociationMode.VERIFIED,
    )
    change = AssociationChange(
        _RC_ENTRY_ID,
        old_association,
        selected_association,
        references,
    )
    hold = AssociationRebindHold(
        secrets.token_hex(16),
        _RC_ENTRY_ID,
        clear.keymaster_entry_id,
        "reselected-keymaster",
        references,
        association_tx,
        transaction_generation,
    )
    association_result = await store.async_commit_association(
        AssociationTransaction(
            association_tx,
            transaction_generation,
            association_base.transaction_id,
            (change,),
            retained_holds=(hold,),
        )
    )
    assert association_result.state is ProtectionLoadState.VALID_V2

    prior = store.loaded_result
    assert prior is not None and prior.data is not None
    assert hold.to_record() in prior.data["holds"]
    prior_clear_fences = [
        fence
        for fence in prior.data["pending_fences"]
        if fence["fence_type"] == "pending_clear"
    ]
    assert any(fence["fence_id"] == clear.fence_id for fence in prior_clear_fences)
    owner_before = deepcopy(prior.data["ledger"])
    target_data = deepcopy(prior.data)
    target_data["transaction_id"] = secrets.token_hex(16)
    target_data["generation"] = prior.generation + 1
    target = {
        "version": 2,
        "minor_version": 1,
        "key": STORE_CODE_REGISTRY_KEY,
        "data": target_data,
    }

    original_write = registry_commit._atomic_write_acknowledged
    write_count = 0

    def fail_one_boundary(path: Path, data: bytes, maximum: int) -> bool:
        """Inject ambiguity at the selected exact-byte protocol write."""
        nonlocal write_count
        write_count += 1
        if write_count == failed_write:
            if fail_after_write:
                assert original_write(path, data, maximum)
            return False
        return original_write(path, data, maximum)

    monkeypatch.setattr(
        registry_commit, "_atomic_write_acknowledged", fail_one_boundary
    )
    failed = await store.async_commit(target)
    assert failed.state is ProtectionLoadState.PROTECTIVE_UNKNOWN
    assert write_count == failed_write
    assert store.authorization_state.value == "protective_unknown"

    blocked_new = await allocator.async_allocate(
        AllocationRequest(
            entry_id=_RC_ENTRY_ID,
            identity_key="post-boundary-new-owner",
            preferred_code="9384",
            code_length=4,
            lockname="lock.consumer",
            slot=_SLOT + 1,
        )
    )
    blocked_reuse = await allocator.async_allocate(
        AllocationRequest(
            entry_id=_RC_ENTRY_ID,
            identity_key=_OWNER_IDENTITY,
            preferred_code=_CODE,
            code_length=len(_CODE),
            lockname="lock.consumer",
            slot=_SLOT,
        )
    )
    blocked_second_entry = await allocator.async_allocate(
        AllocationRequest(
            entry_id=second_rc_entry_id,
            identity_key="second-new-owner",
            preferred_code="9385",
            code_length=4,
            lockname="lock.consumer",
            slot=_SLOT + 3,
        )
    )
    assert blocked_new.code is None
    assert blocked_reuse.code is None
    assert blocked_second_entry.code is None
    assert (
        blocked_new.reason
        == blocked_reuse.reason
        == blocked_second_entry.reason
        == "protective_unknown"
    )
    preview = await allocator.async_preview_reissue(
        ReissuePreviewRequest(
            entry_id=_RC_ENTRY_ID,
            identity_key=_OWNER_IDENTITY,
            preferred_code="9384",
            code_length=4,
            lockname="lock.consumer",
            slot=_SLOT,
            observed_code=_CODE,
        )
    )
    assert preview.reason == "protective_unknown"
    cycle = await allocator.async_resolve_cycle(
        CycleRequest(
            observation=CycleObservation(
                _RC_ENTRY_ID,
                "lock.consumer",
                frozenset({_SLOT}),
                {},
                frozenset(),
            ),
            adoptions=[],
            rekeys=[],
            allocations=[
                AllocationRequest(
                    entry_id=_RC_ENTRY_ID,
                    identity_key="post-boundary-cycle-owner",
                    preferred_code="9384",
                    code_length=4,
                    lockname="lock.consumer",
                    slot=_SLOT,
                )
            ],
            active_keys={"post-boundary-cycle-owner"},
            forced_reissues=(
                ForcedReissueDirective(
                    _RC_ENTRY_ID, _OWNER_IDENTITY, "lock.consumer", _SLOT
                ),
            ),
        )
    )
    assert cycle.allocated["post-boundary-cycle-owner"].reason == "protective_unknown"
    assert cycle.released.released == []
    assert cycle.reissues[0].retention_reason == "protective_unknown"
    removed = await allocator.async_mark_entry_removed(_RC_ENTRY_ID)
    assert removed.released == []
    assert removed.retained
    assert write_count == failed_write

    set_result = await coordinator.event_overrides._apply_set(
        coordinator,
        _SLOT,
        SimpleNamespace(identity_key=_OWNER_IDENTITY),
        "uncertain-v2-commit",
    )
    clear_result = await coordinator.event_overrides._apply_clear(
        coordinator,
        _SLOT,
        owner_identity_key=_OWNER_IDENTITY,
    )
    assert set_result.unconfirmed
    assert clear_result.unconfirmed
    assert events == []

    monkeypatch.setattr(registry_commit, "_atomic_write_acknowledged", original_write)
    restarted = ProtectedRegistryStore(
        hass, allocator._lock, storage_dir=tmp_path / ".storage"
    )
    recovered = await restarted.async_load()
    assert recovered.state is ProtectionLoadState.VALID_V2
    assert restarted.loaded_result is not None
    assert restarted.loaded_result.data is not None
    recovered_data = restarted.loaded_result.data
    assert recovered_data["ledger"] == owner_before
    assert hold.to_record() in recovered_data["holds"]
    assert {
        (owner["entry_id"], owner["identity_key"])
        for record in recovered_data["ledger"]["records"]
        for owner in record["owners"]
    } >= {
        (_RC_ENTRY_ID, _OWNER_IDENTITY),
        (second_rc_entry_id, "consumer-second-active-owner"),
    }
    assert any(
        item["rc_entry_id"] == second_rc_entry_id
        for item in recovered_data["associations"]
    )
    assert any(
        fence["fence_id"] == clear.fence_id
        and fence["original_km_entry_id"] == clear.keymaster_entry_id
        and fence["slot"] == _SLOT
        for fence in recovered_data["pending_fences"]
    )
    assert events == []
