# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Verify dormant staged allocator snapshots commit before publication."""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.rental_control.allocator import registry_commit
from custom_components.rental_control.allocator.association_references import (
    ledger_owner_reference,
)
from custom_components.rental_control.allocator.models import AllocationOrigin
from custom_components.rental_control.allocator.models import AllocationOwner
from custom_components.rental_control.allocator.models import AllocationRecord
from custom_components.rental_control.allocator.models import CycleObservation
from custom_components.rental_control.allocator.models import ProtectionLoadResult
from custom_components.rental_control.allocator.models import ProtectionLoadState
from custom_components.rental_control.allocator.protected_store import (
    ProtectedRegistryStore,
)
from custom_components.rental_control.allocator.registry import AllocationRegistry
from custom_components.rental_control.allocator.registry_commit import (
    async_commit_protection_transaction,
)
from custom_components.rental_control.allocator.registry_safety_models import (
    PendingSlotFence,
)
from custom_components.rental_control.allocator.registry_safety_models import (
    resolve_fence_to_tombstone,
)
from custom_components.rental_control.allocator.registry_safety_transitions import (
    validate_snapshot_transition,
)
from custom_components.rental_control.allocator.registry_snapshot import (
    async_commit_registry_snapshot,
)
from custom_components.rental_control.allocator.registry_snapshot import (
    serialize_exclusion_snapshots,
)
from custom_components.rental_control.allocator.release_guard import (
    release_guard_reason,
)
from custom_components.rental_control.allocator.store import serialize_registry_ledger
from custom_components.rental_control.const import CONF_EXCLUDED_SLOTS

_BASE_TX = "a" * 32


def _empty_envelope() -> dict[str, object]:
    """Return one valid initial protected snapshot."""
    return {
        "version": 2,
        "minor_version": 1,
        "key": "rental_control.code_registry",
        "data": {
            "schema_version": 1,
            "transaction_id": _BASE_TX,
            "generation": 1,
            "ledger": {
                "schema_version": 1,
                "updated_at": "2026-11-21T00:00:00+00:00",
                "code_ref_salt": "persisted-code-reference-salt",
                "records": [],
                "announced_conflicts": {},
            },
            "associations": [],
            "attestations": [],
            "pending_fences": [],
            "holds": [],
            "tombstones": [],
            "pending_config": [],
        },
    }


def _owner(
    entry_id: str,
    identity: str,
    *,
    slot: int,
) -> AllocationOwner:
    """Return one non-sensitive test owner."""
    return AllocationOwner(
        entry_id=entry_id,
        identity_key=identity,
        origin=AllocationOrigin.ADOPTED,
        lockname=f"lock.{entry_id}",
        slot=slot,
        lock_observed=True,
        first_seen="2026-11-21T00:00:00+00:00",
        last_seen="2026-11-21T00:00:00+00:00",
    )


async def _ready_store(
    hass, storage: Path
) -> tuple[ProtectedRegistryStore, asyncio.Lock]:
    """Create an acknowledged v2 baseline and its READY store."""
    storage.mkdir(parents=True, exist_ok=True)
    lock = asyncio.Lock()
    initial = await async_commit_protection_transaction(
        lock,
        storage,
        _empty_envelope(),
    )
    assert initial.state is ProtectionLoadState.VALID_V2
    store = ProtectedRegistryStore(hass, lock, storage_dir=storage)
    loaded = await store.async_load()
    assert loaded.state is ProtectionLoadState.VALID_V2
    return store, lock


async def test_staged_registry_and_exclusions_publish_only_after_ack(
    hass,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """The protected write completes before a fake consumer sees new owners."""
    storage = tmp_path / ".storage"
    store, lock = await _ready_store(hass, storage)
    original_registry = AllocationRegistry(
        code_ref_salt="persisted-code-reference-salt"
    )
    allocator = SimpleNamespace(_lock=lock, _registry=original_registry)
    owner = _owner("rc-a", "booking-a", slot=4)
    record = AllocationRecord(
        code="4729",
        code_ref="opaque-code-ref",
        encoding_salt_value="rc-a",
        owners=[owner],
        created_at="2026-11-21T00:00:00+00:00",
        updated_at="2026-11-21T00:00:00+00:00",
    )

    def add_owner(candidate: AllocationRegistry) -> None:
        """Add the staged owner to the candidate registry."""
        candidate.records[record.code] = record

    exclusions = [
        {
            "rc_entry_id": "rc-a",
            "slots": [{"slot": 7, "reason": "unreadable"}],
        }
    ]
    durable_write = registry_commit._write_prepared_transaction

    def verify_not_published_before_ack(prepared):
        """Confirm publication remains deferred until the durable write."""
        assert allocator._registry is original_registry
        assert allocator._registry.records == {}
        result = durable_write(prepared)
        assert allocator._registry is original_registry
        return result

    monkeypatch.setattr(
        registry_commit,
        "_write_prepared_transaction",
        verify_not_published_before_ack,
    )
    committed = await async_commit_registry_snapshot(
        allocator,
        store,
        add_owner,
        exclusions,
    )

    assert committed.state is ProtectionLoadState.VALID_V2
    assert allocator._registry is not original_registry
    assert allocator._registry.records["4729"].owners == [owner]
    envelope = json.loads((storage / "rental_control.code_registry").read_bytes())
    assert envelope["data"]["schema_version"] == 2
    assert envelope["data"]["exclusions"] == exclusions
    assert envelope["data"]["ledger"]["records"][0]["owners"][0]["identity_key"] == (
        "booking-a"
    )
    assert store.loaded_result is not None
    assert store.loaded_result.transaction_id == committed.transaction_id


async def test_snapshot_rejects_live_base_change_before_storage_io(
    hass,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A candidate staged from stale live state cannot overwrite a newer owner."""
    storage = tmp_path / ".storage"
    store, lock = await _ready_store(hass, storage)
    registry = AllocationRegistry(code_ref_salt="persisted-code-reference-salt")
    allocator = SimpleNamespace(_lock=lock, _registry=registry)
    owner = _owner("rc-a", "newer-booking", slot=5)

    def stage_unrelated_owner(candidate: AllocationRegistry) -> None:
        """Stage a separate owner that must not overwrite live changes."""
        candidate.records["4729"] = AllocationRecord(
            code="4729",
            code_ref="staged-code-ref",
            encoding_salt_value="rc-a",
            owners=[_owner("rc-a", "staged-booking", slot=4)],
            created_at="2026-11-21T00:00:00+00:00",
            updated_at="2026-11-21T00:00:00+00:00",
        )

    original_commit = store.async_commit

    async def change_live_registry_before_commit(envelope, **kwargs):
        """Simulate a newer allocator mutation before store preflight."""
        async with lock:
            registry.records["6813"] = AllocationRecord(
                code="6813",
                code_ref="newer-code-ref",
                encoding_salt_value="rc-a",
                owners=[owner],
            )
        return await original_commit(envelope, **kwargs)

    monkeypatch.setattr(store, "async_commit", change_live_registry_before_commit)
    result = await async_commit_registry_snapshot(
        allocator,
        store,
        stage_unrelated_owner,
        [],
    )

    assert result.state is ProtectionLoadState.UNAVAILABLE
    assert result.failure_code == "registry_changed"
    durable = json.loads((storage / "rental_control.code_registry").read_bytes())
    assert durable["data"]["transaction_id"] == _BASE_TX
    assert "6813" in allocator._registry.records
    assert "4729" not in allocator._registry.records


async def test_snapshot_rejects_a_store_with_a_different_allocator_lock(
    hass,
    tmp_path: Path,
) -> None:
    """Publication cannot race when a store fails to share the allocator lock."""
    storage = tmp_path / ".storage"
    store, _store_lock = await _ready_store(hass, storage)
    allocator = SimpleNamespace(
        _lock=asyncio.Lock(),
        _registry=AllocationRegistry(code_ref_salt="persisted-code-reference-salt"),
    )
    assert not store.uses_async_lock(allocator._lock)
    called = False

    def stage(_candidate: AllocationRegistry) -> None:
        """Record whether staging ran despite a lock mismatch."""
        nonlocal called
        called = True

    result = await async_commit_registry_snapshot(
        allocator,
        store,
        stage,
        [],
    )

    assert result.state is ProtectionLoadState.UNAVAILABLE
    assert result.failure_code == "allocator_lock_mismatch"
    assert not called


async def test_uncertain_snapshot_write_does_not_publish_staged_registry(
    hass,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ambiguous durable outcome freezes protection and keeps live owners intact."""
    storage = tmp_path / ".storage"
    store, lock = await _ready_store(hass, storage)
    original_registry = AllocationRegistry(
        code_ref_salt="persisted-code-reference-salt"
    )
    allocator = SimpleNamespace(_lock=lock, _registry=original_registry)

    def stage_owner(candidate: AllocationRegistry) -> None:
        """Stage an owner whose uncertain write must not be published."""
        candidate.records["4729"] = AllocationRecord(
            code="4729",
            code_ref="staged-code-ref",
            encoding_salt_value="rc-a",
            owners=[_owner("rc-a", "booking-a", slot=4)],
            created_at="2026-11-21T00:00:00+00:00",
            updated_at="2026-11-21T00:00:00+00:00",
        )

    def fail_after_possible_write(_prepared):
        """Return ambiguity after a write may already have occurred."""
        return ProtectionLoadResult(
            ProtectionLoadState.PROTECTIVE_UNKNOWN,
            failure_code="envelope_write_failed",
        )

    monkeypatch.setattr(
        registry_commit,
        "_write_prepared_transaction",
        fail_after_possible_write,
    )
    result = await async_commit_registry_snapshot(
        allocator,
        store,
        stage_owner,
        [],
    )

    assert result.state is ProtectionLoadState.PROTECTIVE_UNKNOWN
    assert allocator._registry is original_registry
    assert allocator._registry.records == {}
    assert store.authorization_state.value == "protective_unknown"


def test_pure_ledger_serializer_does_not_invent_missing_salt() -> None:
    """A missing persisted salt is not replaced while preparing protected state."""
    registry = AllocationRegistry()

    with pytest.raises(ValueError, match="salt is missing"):
        serialize_registry_ledger(registry)

    assert registry.code_ref_salt == ""


async def test_transition_allows_exclusions_but_rejects_unproven_owner_loss(
    hass,
    tmp_path: Path,
) -> None:
    """Acknowledged owner and exclusion state cannot disappear silently."""
    storage = tmp_path / ".storage"
    store, lock = await _ready_store(hass, storage)
    allocator = SimpleNamespace(
        _lock=lock,
        _registry=AllocationRegistry(code_ref_salt="persisted-code-reference-salt"),
    )
    owner = _owner("rc-a", "booking-a", slot=4)

    def add_owner(candidate: AllocationRegistry) -> None:
        """Add the owner whose loss requires an exact transition proof."""
        candidate.records["4729"] = AllocationRecord(
            code="4729",
            code_ref="opaque-code-ref",
            encoding_salt_value="rc-a",
            owners=[copy.deepcopy(owner)],
            created_at="2026-11-21T00:00:00+00:00",
            updated_at="2026-11-21T00:00:00+00:00",
        )

    exclusions = [{"rc_entry_id": "rc-a", "slots": [{"slot": 7, "reason": None}]}]
    first = await async_commit_registry_snapshot(
        allocator,
        store,
        add_owner,
        exclusions,
    )
    assert first.state is ProtectionLoadState.VALID_V2
    prior = store.loaded_result
    assert prior is not None and prior.data is not None

    missing_owner = copy.deepcopy(prior.data)
    missing_owner["ledger"]["records"] = []
    assert not validate_snapshot_transition(prior.data, missing_owner)

    changed_exclusions = copy.deepcopy(prior.data)
    changed_exclusions["exclusions"] = []
    assert validate_snapshot_transition(prior.data, changed_exclusions)

    updated = await async_commit_registry_snapshot(
        allocator,
        store,
        lambda _candidate: None,
        [],
    )
    assert updated.state is ProtectionLoadState.VALID_V2
    assert store.loaded_result is not None
    assert store.loaded_result.data is not None
    assert store.loaded_result.data["exclusions"] == []


async def test_guarded_slot_move_and_release_require_current_tombstones(
    hass,
    tmp_path: Path,
) -> None:
    """A guarded release publishes only with its exact durable fence resolution."""
    storage = tmp_path / ".storage"
    store, lock = await _ready_store(hass, storage)
    allocator = SimpleNamespace(
        _lock=lock,
        _registry=AllocationRegistry(code_ref_salt="persisted-code-reference-salt"),
    )
    owner = _owner("rc-a", "booking-release", slot=4)
    record = AllocationRecord(
        code="4729",
        code_ref="opaque-code-ref",
        encoding_salt_value="rc-a",
        owners=[copy.deepcopy(owner)],
        created_at="2026-11-21T00:00:00+00:00",
        updated_at="2026-11-21T00:00:00+00:00",
    )

    def add_owner(candidate: AllocationRegistry) -> None:
        """Add the owner that will be moved and subsequently released."""
        candidate.records[record.code] = copy.deepcopy(record)
        candidate.rebuild_index()

    def create_fence(
        _candidate: AllocationRegistry,
        data: dict[str, Any],
        transaction_id: str,
        generation: int,
    ) -> None:
        """Create the initial persisted fence for the owner."""
        ledger = data["ledger"]
        assert isinstance(ledger, dict)
        ledger_record = ledger["records"][0]
        ledger_owner = ledger_record["owners"][0]
        owner_ref = ledger_owner_reference(data, ledger_record, ledger_owner)
        assert owner_ref is not None
        fence = PendingSlotFence(
            fence_id="1" * 32,
            original_km_entry_id="km-a",
            slot=4,
            rc_entry_id="rc-a",
            owner_ref=owner_ref,
            allocation_ref="allocation-ref-a",
            created_transaction_id=transaction_id,
            created_generation=generation,
            fence_type="allocation",
        )
        data["pending_fences"].append(fence.to_record())

    first = await async_commit_registry_snapshot(
        allocator,
        store,
        add_owner,
        [],
        safety_transition=create_fence,
    )
    assert first.state is ProtectionLoadState.VALID_V2

    original = store.loaded_result
    assert original is not None and original.data is not None
    assert original.generation is not None
    prior_fence = original.data["pending_fences"][0]
    unapproved_data = copy.deepcopy(original.data)
    unapproved_data["ledger"]["records"] = []
    unapproved_data["transaction_id"] = "5" * 32
    unapproved_data["generation"] = original.generation + 1
    unapproved = await store.async_commit(
        {
            "version": 2,
            "minor_version": 1,
            "key": "rental_control.code_registry",
            "data": unapproved_data,
        }
    )
    assert unapproved.state is ProtectionLoadState.UNAVAILABLE
    assert unapproved.failure_code == "owner_transition_unauthorized"
    assert store.loaded_result is not None
    assert store.loaded_result.transaction_id == original.transaction_id

    moved_lockname = "lock.front_door_moved"

    def move_owner(candidate: AllocationRegistry) -> None:
        """Move the existing owner to the newly observed slot."""
        moved = candidate.record_for_identity(owner.identity_key)
        assert moved is not None
        moved_owner = next(
            item for item in moved.owners if item.identity_key == owner.identity_key
        )
        moved_owner.lockname = moved_lockname
        moved_owner.slot = 5

    def record_slot_move(
        _candidate: AllocationRegistry,
        data: dict[str, Any],
        transaction_id: str,
        generation: int,
    ) -> None:
        """Resolve the old fence and create the new-slot fence."""
        data["pending_fences"] = []
        data["tombstones"].append(
            resolve_fence_to_tombstone(
                prior_fence,
                tombstone_id="2" * 32,
                resolved_transaction_id=transaction_id,
                resolved_generation=generation,
                resolution_kind="owner_released",
                retained_at="2026-11-21T00:01:00+00:00",
            )
        )
        ledger = data["ledger"]
        assert isinstance(ledger, dict)
        ledger_record = ledger["records"][0]
        ledger_owner = ledger_record["owners"][0]
        moved_ref = ledger_owner_reference(data, ledger_record, ledger_owner)
        assert moved_ref is not None
        data["pending_fences"].append(
            PendingSlotFence(
                fence_id="3" * 32,
                original_km_entry_id="km-a",
                slot=5,
                rc_entry_id="rc-a",
                owner_ref=moved_ref,
                allocation_ref="allocation-ref-b",
                created_transaction_id=transaction_id,
                created_generation=generation,
                fence_type="allocation",
            ).to_record()
        )

    old_slot_observation = CycleObservation(
        entry_id="rc-a",
        lockname=owner.lockname,
        managed_slots=frozenset({4}),
        observed_codes={},
        unreadable_slots=frozenset(),
    )
    new_slot_observation = CycleObservation(
        entry_id="rc-a",
        lockname=moved_lockname,
        managed_slots=frozenset({5}),
        observed_codes={},
        unreadable_slots=frozenset(),
    )

    def authorize_slot_move(
        prior_registry: AllocationRegistry,
        target_registry: AllocationRegistry,
    ) -> bool:
        """Require release-guard evidence for the slot move."""
        prior_record = prior_registry.record_for_identity(owner.identity_key)
        prior_owner = next(
            item
            for candidate_record in prior_registry.records.values()
            for item in candidate_record.owners
            if item.identity_key == owner.identity_key
        )
        moved_owner = next(
            item
            for candidate_record in target_registry.records.values()
            for item in candidate_record.owners
            if item.identity_key == owner.identity_key
        )
        return bool(
            prior_record is not None
            and release_guard_reason(
                prior_record,
                [prior_owner],
                [old_slot_observation],
                refresh_observed=False,
            )
            is None
            and moved_owner.lockname == moved_lockname
            and moved_owner.slot == 5
            and moved_owner.slot in new_slot_observation.managed_slots
            and moved_owner.slot not in new_slot_observation.unreadable_slots
            and prior_record.code not in new_slot_observation.observed_codes
        )

    moved = await async_commit_registry_snapshot(
        allocator,
        store,
        move_owner,
        [],
        authorize_owner_transition=authorize_slot_move,
        safety_transition=record_slot_move,
    )
    assert moved.state is ProtectionLoadState.VALID_V2
    assert allocator._registry.record_for_identity(owner.identity_key) is not None
    moved_result = store.loaded_result
    assert moved_result is not None and moved_result.data is not None
    prior_fence = moved_result.data["pending_fences"][0]

    def release_owner(candidate: AllocationRegistry) -> None:
        """Remove the owner only after its release guard succeeds."""
        assert candidate.release(owner.identity_key)

    def resolve_fence(
        _candidate: AllocationRegistry,
        data: dict[str, Any],
        transaction_id: str,
        generation: int,
    ) -> None:
        """Resolve the current fence in the target transaction."""
        data["pending_fences"] = []
        data["tombstones"].append(
            resolve_fence_to_tombstone(
                prior_fence,
                tombstone_id="4" * 32,
                resolved_transaction_id=transaction_id,
                resolved_generation=generation,
                resolution_kind="owner_released",
                retained_at="2026-11-21T00:02:00+00:00",
            )
        )

    observation = CycleObservation(
        entry_id="rc-a",
        lockname=moved_lockname,
        managed_slots=frozenset({5}),
        observed_codes={},
        unreadable_slots=frozenset(),
    )

    def authorize_release(
        prior_registry: AllocationRegistry,
        target_registry: AllocationRegistry,
    ) -> bool:
        """Authorize release only after observed state and guard checks."""
        prior_record = prior_registry.record_for_identity(owner.identity_key)
        prior_owner = next(
            candidate
            for candidate_record in prior_registry.records.values()
            for candidate in candidate_record.owners
            if candidate.identity_key == owner.identity_key
        )
        return bool(
            prior_record is not None
            and release_guard_reason(
                prior_record,
                [prior_owner],
                [observation],
                refresh_observed=False,
            )
            is None
            and target_registry.record_for_identity(owner.identity_key) is None
        )

    released = await async_commit_registry_snapshot(
        allocator,
        store,
        release_owner,
        [],
        authorize_owner_transition=authorize_release,
        safety_transition=resolve_fence,
    )

    assert released.state is ProtectionLoadState.VALID_V2
    assert allocator._registry.record_for_identity(owner.identity_key) is None
    assert store.loaded_result is not None
    assert store.loaded_result.data is not None
    assert store.loaded_result.data["pending_fences"] == []
    assert (
        store.loaded_result.data["tombstones"][-1]["resolved_transaction_id"]
        == released.transaction_id
    )


def test_exclusion_serializer_preserves_values_and_rejects_malformed_history() -> None:
    """Snapshot capture is deterministic and never silently discards entries."""
    entries = [
        SimpleNamespace(
            entry_id="rc-b",
            data={
                CONF_EXCLUDED_SLOTS: [
                    {"slot": 9, "reason": "unreachable"},
                    {"slot": 3},
                ]
            },
        ),
        SimpleNamespace(entry_id="rc-a", data={}),
    ]

    assert serialize_exclusion_snapshots(entries) == [
        {
            "rc_entry_id": "rc-b",
            "slots": [
                {"slot": 3, "reason": None},
                {"slot": 9, "reason": "unreachable"},
            ],
        }
    ]

    malformed = SimpleNamespace(
        entry_id="rc-a",
        data={CONF_EXCLUDED_SLOTS: [{"slot": True, "reason": "invalid"}]},
    )
    with pytest.raises(ValueError, match="invalid exclusion record"):
        serialize_exclusion_snapshots([malformed])
