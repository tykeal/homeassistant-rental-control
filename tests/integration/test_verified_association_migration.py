# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Verify migration, restart evidence, and unknown-state issuance boundaries."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from typing import Any
from typing import cast

from aioresponses import aioresponses
from homeassistant.config_entries import ConfigEntryState
from pytest_homeassistant.common import MockConfigEntry

from custom_components.rental_control.allocator.allocator import DoorCodeAllocator
from custom_components.rental_control.allocator.association_references import (
    association_references_for_snapshot,
)
from custom_components.rental_control.allocator.associations import AssociationMode
from custom_components.rental_control.allocator.associations import AssociationTuple
from custom_components.rental_control.allocator.models import AdoptionRequest
from custom_components.rental_control.allocator.models import AllocationOrigin
from custom_components.rental_control.allocator.models import AllocationRequest
from custom_components.rental_control.allocator.models import CycleObservation
from custom_components.rental_control.allocator.models import CycleRequest
from custom_components.rental_control.allocator.models import (
    ProtectionAuthorizationState,
)
from custom_components.rental_control.allocator.models import ProtectionLoadResult
from custom_components.rental_control.allocator.models import ProtectionLoadState
from custom_components.rental_control.allocator.models import ReissuePreviewRequest
from custom_components.rental_control.allocator.protected_store import (
    ProtectedRegistryStore,
)
from custom_components.rental_control.allocator.registry import AllocationRegistry
from custom_components.rental_control.allocator.registry_snapshot import (
    serialize_exclusion_snapshots,
)
from custom_components.rental_control.config_flow_helpers.association import (
    apply_association_selection,
)
from custom_components.rental_control.const import ALLOCATOR
from custom_components.rental_control.const import CONF_ASSOCIATION_ATTEST_SAME_LOCK
from custom_components.rental_control.const import CONF_ASSOCIATION_ENTRY_CHOICE
from custom_components.rental_control.const import CONF_EXCLUDED_SLOTS
from custom_components.rental_control.const import CONF_LOCK_ENTRY
from custom_components.rental_control.const import COORDINATOR
from custom_components.rental_control.const import DOMAIN
from custom_components.rental_control.const import LOCK_MANAGER
from custom_components.rental_control.const import PROTECTED_REGISTRY_STORE
from custom_components.rental_control.const import STORE_CODE_REGISTRY_KEY
from custom_components.rental_control.migrations import async_migrate_entry

from tests.fixtures import calendar_data
from tests.integration.test_verified_consumer_dispatch import _setup_verified_consumer


class _ProtectedStore:
    """Keep acknowledged configuration and protective state explicit."""

    def __init__(
        self,
        *,
        data: dict[str, Any],
        association: AssociationTuple | None = None,
        references: tuple[str, ...] = ("owner-reference",),
    ) -> None:
        """Create a valid v2 test snapshot."""
        self.authorization_state = ProtectionAuthorizationState.READY
        self.data = data
        self.association = association
        self.references = references
        self.envelopes: list[dict[str, Any]] = []
        self.loaded_result = ProtectionLoadResult(
            ProtectionLoadState.VALID_V2,
            transaction_id=data["transaction_id"],
            generation=data["generation"],
            data=data,
        )

    def association_for_entry(self, entry_id: str) -> AssociationTuple | None:
        """Return the committed tuple only for its RC owner."""
        return self.association if entry_id == "migration-rc" else None

    def association_references_for_entry(self, _entry_id: str) -> tuple[str, ...]:
        """Return the opaque references attached to this fixture."""
        return self.references

    async def async_commit(self, envelope: dict[str, Any]) -> ProtectionLoadResult:
        """Acknowledge the exact transaction built by the association flow."""
        self.envelopes.append(envelope)
        committed = envelope["data"]
        result = ProtectionLoadResult(
            ProtectionLoadState.VALID_V2,
            transaction_id=committed["transaction_id"],
            generation=committed["generation"],
            data=committed,
        )
        self.loaded_result = result
        self.data = committed
        return result


class _UnknownStore:
    """Represent an installed but untrusted protective registry."""

    async def async_wait_for_commit_completion(self) -> bool:
        """Decline authorization while the evidence state is unknown."""
        return False

    def operation_snapshot_ready(self) -> bool:
        """Keep allocation paths fail-closed."""
        return False


def _empty_v2_data() -> dict[str, Any]:
    """Build the complete empty version-2 protected payload."""
    return {
        "schema_version": 1,
        "transaction_id": "a" * 32,
        "generation": 1,
        "ledger": {
            "schema_version": 1,
            "updated_at": "2026-11-21T00:00:00+00:00",
            "code_ref_salt": "migration-test-salt",
            "records": [],
            "announced_conflicts": {},
        },
        "associations": [],
        "attestations": [],
        "pending_fences": [],
        "holds": [],
        "tombstones": [],
        "pending_config": [],
    }


async def test_v10_upgrade_preserves_legacy_choices_and_unrelated_settings(
    hass,
) -> None:
    """Migration advances schema without guessing any Keymaster identity."""
    data = {
        CONF_LOCK_ENTRY: "Front Door",
        "name": "Rental",
        "start_slot": "20",
        "max_events": "4",
        "days": "3",
        "code_length": 6,
        "excluded_slots": [{"slot": 23, "reason": "unreadable"}],
        "operator_note": "keep exactly",
    }
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Rental",
        entry_id="migration-rc",
        version=10,
        data=data,
        options={"existing_option": "unchanged"},
    )
    entry.add_to_hass(hass)
    original = deepcopy(dict(entry.data))

    assert await async_migrate_entry(hass, entry)
    assert entry.version == 11
    assert dict(entry.data) == original
    assert dict(entry.options) == {"existing_option": "unchanged"}
    association = AssociationTuple.from_config(entry.data)
    assert association is not None
    assert association.mode is AssociationMode.LEGACY_UNRESOLVED
    assert association.verified_keymaster_entry_id is None

    assert await async_migrate_entry(hass, entry)
    assert entry.version == 11
    assert dict(entry.data) == original


async def test_migration_restart_preserves_active_claim_holds_and_exclusions(
    hass, tmp_path
) -> None:
    """Migration and uncertain restart retain acknowledged safety evidence."""
    storage = tmp_path / ".storage"
    coordinator, store, _entity_ids = await _setup_verified_consumer(hass, storage)
    entry = coordinator.config_entry
    result = store.loaded_result
    assert (
        result is not None and result.data is not None and result.generation is not None
    )
    association = store.association_for_entry(entry.entry_id)
    assert association is not None
    references = association_references_for_snapshot(result.data, entry.entry_id)
    assert references
    excluded = [{"slot": 99, "reason": "prior unreadable slot"}]
    hass.config_entries.async_update_entry(
        entry,
        data={**entry.data, CONF_EXCLUDED_SLOTS: excluded},
    )
    exclusion_snapshot = deepcopy(result.data)
    exclusion_snapshot.update(
        {
            "transaction_id": "d" * 32,
            "generation": result.generation + 1,
            "exclusions": serialize_exclusion_snapshots([entry]),
        }
    )
    exclusion_commit = await store.async_commit(
        {
            "version": 2,
            "minor_version": 1,
            "key": STORE_CODE_REGISTRY_KEY,
            "data": exclusion_snapshot,
        }
    )
    assert exclusion_commit.state is ProtectionLoadState.VALID_V2

    replacement = MockConfigEntry(
        domain=LOCK_MANAGER,
        title="Replacement Front Door",
        entry_id="replacement-keymaster",
        data={
            "lock_entity_id": "lock.replacement_front_door",
            "lockname": "Replacement Front Door",
        },
    )
    replacement.add_to_hass(hass)
    replacement._async_set_state(hass, ConfigEntryState.LOADED, None)
    await apply_association_selection(
        SimpleNamespace(hass=hass, config_entry=entry),
        {
            CONF_ASSOCIATION_ENTRY_CHOICE: replacement.entry_id,
            CONF_ASSOCIATION_ATTEST_SAME_LOCK: False,
        },
    )

    committed = store.loaded_result
    assert committed is not None and committed.data is not None
    protected_data = deepcopy(committed.data)
    holds = protected_data["holds"]
    assert len(holds) == 1
    hold = holds[0]
    assert hold["kind"] == "association_rebind"
    assert hold["rc_entry_id"] == entry.entry_id
    assert hold["original_km_entry_id"] == association.verified_keymaster_entry_id
    assert hold["selected_keymaster_entry_id"] == replacement.entry_id
    assert hold["claim_references"] == list(references)
    assert protected_data["exclusions"] == serialize_exclusion_snapshots([entry])
    assert protected_data["ledger"]["records"][0]["owners"][0]["entry_id"] == (
        entry.entry_id
    )
    assert set(hold) == {
        "kind",
        "hold_id",
        "rc_entry_id",
        "original_km_entry_id",
        "selected_keymaster_entry_id",
        "claim_references",
        "transaction_id",
        "generation",
    }

    legacy_data = {
        CONF_LOCK_ENTRY: "Front Door",
        "name": "Consumer test",
        "start_slot": "21",
        "max_events": "1",
        "excluded_slots": excluded,
        "operator_note": "keep through restart",
    }
    hass.config_entries.async_update_entry(entry, data=legacy_data, version=10)
    assert await async_migrate_entry(hass, entry)
    assert entry.version == 11
    assert dict(entry.data) == legacy_data

    restarted = ProtectedRegistryStore(hass, asyncio.Lock(), storage_dir=storage)
    loaded = await restarted.async_load()
    assert loaded.state is ProtectionLoadState.VALID_V2
    assert loaded.data is not None
    assert loaded.data == protected_data
    assert (
        loaded.data["ledger"]["code_ref_salt"]
        == protected_data["ledger"]["code_ref_salt"]
    )

    registry_bytes = (storage / STORE_CODE_REGISTRY_KEY).read_bytes()
    uncertain_restart = ProtectedRegistryStore(
        hass, asyncio.Lock(), storage_dir=storage
    )
    uncertain_restart.mark_external_restore_suspected()
    uncertain = await uncertain_restart.async_load()
    assert uncertain.state is ProtectionLoadState.PROTECTIVE_UNKNOWN
    assert (
        uncertain_restart.authorization_state
        is ProtectionAuthorizationState.PROTECTIVE_UNKNOWN
    )
    blocked_commit = await uncertain_restart.async_commit(
        {
            "version": 2,
            "minor_version": 1,
            "key": STORE_CODE_REGISTRY_KEY,
            "data": protected_data,
        }
    )
    assert blocked_commit.state is ProtectionLoadState.PROTECTIVE_UNKNOWN
    assert (storage / STORE_CODE_REGISTRY_KEY).read_bytes() == registry_bytes
    assert uncertain_restart.loaded_result is None


async def test_same_name_recreated_entry_requires_explicit_same_lock_claim(
    hass,
) -> None:
    """A recreated config-entry ID is attested explicitly, not name-inferred."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Rental",
        entry_id="migration-rc",
        data={CONF_LOCK_ENTRY: "Front Door"},
    )
    entry.add_to_hass(hass)
    candidate = MockConfigEntry(
        domain=LOCK_MANAGER,
        title="Front Door",
        entry_id="km-recreated",
        data={"lockname": "Front Door", "lock_entity_id": "lock.front_door"},
    )
    candidate.add_to_hass(hass)
    data = _empty_v2_data()
    store = _ProtectedStore(data=data)
    hass.data.setdefault(DOMAIN, {})[PROTECTED_REGISTRY_STORE] = store
    flow = SimpleNamespace(hass=hass, config_entry=entry)

    refused = await apply_association_selection(
        flow,
        {
            CONF_ASSOCIATION_ENTRY_CHOICE: candidate.entry_id,
            CONF_ASSOCIATION_ATTEST_SAME_LOCK: False,
            CONF_LOCK_ENTRY: "Front Door",
        },
    )
    assert refused.errors == {CONF_ASSOCIATION_ATTEST_SAME_LOCK: "attestation_required"}
    assert not store.envelopes

    accepted = await apply_association_selection(
        flow,
        {
            CONF_ASSOCIATION_ENTRY_CHOICE: candidate.entry_id,
            CONF_ASSOCIATION_ATTEST_SAME_LOCK: True,
            CONF_LOCK_ENTRY: "Front Door",
        },
    )
    assert not accepted.errors
    assert store.envelopes
    assert (
        store.envelopes[-1]["data"]["attestations"][0]["selected_keymaster_entry_id"]
        == candidate.entry_id
    )
    assert store.envelopes[-1]["data"]["pending_config"]


async def test_protective_unknown_blocks_lockless_issue_and_existing_code_reuse(
    hass,
) -> None:
    """A current protective-unknown snapshot cannot allocate or reuse codes."""
    allocator = DoorCodeAllocator(hass)
    saves: list[object] = []
    allocator._store = cast(Any, SimpleNamespace(async_save=saves.append))
    hass.data.setdefault(DOMAIN, {})[PROTECTED_REGISTRY_STORE] = _UnknownStore()
    registry = AllocationRegistry(code_ref_salt="migration-test-salt")
    existing = AllocationRequest(
        entry_id="lockless-entry",
        identity_key="existing-stay",
        preferred_code="2718",
        code_length=4,
        lockname=None,
        slot=None,
    )
    record = registry.allocate(
        existing,
        "2718",
        AllocationOrigin.PREFERRED,
        "existing-code-reference",
        "2026-11-21T00:00:00+00:00",
    )
    allocator._registry = registry
    before_records = deepcopy(allocator._registry.records)
    before_index = dict(allocator._registry.by_identity)
    request = CycleRequest(
        observation=CycleObservation(
            entry_id="lockless-entry",
            lockname=None,
            managed_slots=frozenset(),
            observed_codes={},
            unreadable_slots=frozenset(),
        ),
        adoptions=[],
        rekeys=[],
        allocations=[
            existing,
            AllocationRequest(
                entry_id="lockless-entry",
                identity_key="new-stay",
                preferred_code="3141",
                code_length=4,
                lockname=None,
                slot=None,
            ),
        ],
        active_keys={"existing-stay", "new-stay"},
    )

    result = await allocator.async_resolve_cycle(request)

    assert set(result.allocated) == {"existing-stay", "new-stay"}
    assert all(
        allocation.code is None and allocation.reason == "protective_unknown"
        for allocation in result.allocated.values()
    )
    assert result.adopted == {}
    assert not result.released.released
    assert allocator._registry.records == before_records
    assert allocator._registry.by_identity == before_index
    assert saves == []

    assert record.code == "2718"

    direct_allocation = await allocator.async_allocate(existing)
    assert direct_allocation.code is None
    assert direct_allocation.reason == "protective_unknown"
    adopted = await allocator.async_adopt(
        AdoptionRequest(
            entry_id="lockless-entry",
            identity_key="observed-stay",
            code="1618",
            code_length=4,
            lockname="Front Door",
            slot=20,
        )
    )
    assert adopted.code is None
    assert adopted.reason == "protective_unknown"
    preview = await allocator.async_preview_reissue(
        ReissuePreviewRequest(
            entry_id="lockless-entry",
            identity_key="existing-stay",
            preferred_code="2718",
            code_length=4,
            lockname=None,
            slot=None,
        )
    )
    assert preview.replacement_code is None
    assert preview.reason == "protective_unknown"
    assert not await allocator.async_rekey("existing-stay", "renamed-stay")

    swept = await allocator.async_sweep(request.observation, active_keys=set())
    assert not swept.released
    assert [(item.identity_key, item.reason) for item in swept.retained] == [
        ("existing-stay", "protective_unknown")
    ]
    removed = await allocator.async_mark_entry_removed("lockless-entry")
    assert not removed.released
    assert [(item.identity_key, item.reason) for item in removed.retained] == [
        ("existing-stay", "protective_unknown")
    ]
    cleanup = await allocator.async_clear_orphans(set(), [])
    assert not cleanup.cleared
    assert [(item.identity_key, item.reason) for item in cleanup.retained] == [
        ("existing-stay", "protective_unknown")
    ]
    assert allocator._registry.records == before_records
    assert allocator._registry.by_identity == before_index
    assert saves == []


async def test_protective_unknown_allows_lockless_setup_and_calendar_display(hass):
    """Current-version lockless setup and calendar refresh remain available."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Calendar Only",
        entry_id="calendar-only-protective-unknown",
        version=11,
        unique_id="calendar-only-protective-unknown",
        data={
            "name": "Calendar Only",
            "url": "https://example.com/calendar.ics",
            "timezone": "America/New_York",
            "checkin": "16:00",
            "checkout": "11:00",
            "start_slot": 10,
            "max_events": 3,
            "days": 90,
            "verify_ssl": True,
            "ignore_non_reserved": False,
            "honor_event_times": False,
            "code_buffer_before": 0,
            "code_buffer_after": 0,
        },
        options={"refresh_frequency": 5},
    )
    hass.data.setdefault(DOMAIN, {})[PROTECTED_REGISTRY_STORE] = _UnknownStore()
    entry.add_to_hass(hass)

    with aioresponses() as mock_session:
        mock_session.get(
            "https://example.com/calendar.ics",
            status=200,
            body=calendar_data.AIRBNB_ICS_CALENDAR,
            repeat=True,
        )
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    allocator = hass.data[DOMAIN][ALLOCATOR]
    coordinator = hass.data[DOMAIN][entry.entry_id][COORDINATOR]
    assert coordinator.data is not None
    assert not allocator._registry.records
