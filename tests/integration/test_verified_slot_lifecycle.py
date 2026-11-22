# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Verify overlapping managed slots bind to the selected Keymaster identity."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from importlib import import_module
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any
from typing import cast
from unittest.mock import patch

from homeassistant import loader
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.issue_registry import async_get as async_get_issue_registry
from homeassistant.util import slugify
import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.rental_control.allocator.association_references import (
    ledger_allocation_references,
)
from custom_components.rental_control.allocator.association_transactions import (
    AssociationTransaction,
)
from custom_components.rental_control.allocator.association_transactions import (
    association_storage_record,
)
from custom_components.rental_control.allocator.associations import AssociationChange
from custom_components.rental_control.allocator.associations import AssociationMode
from custom_components.rental_control.allocator.associations import AssociationTuple
from custom_components.rental_control.allocator.models import AllocationOrigin
from custom_components.rental_control.allocator.models import AllocationOwner
from custom_components.rental_control.allocator.models import AllocationRecord
from custom_components.rental_control.allocator.models import (
    ProtectionAuthorizationState,
)
from custom_components.rental_control.allocator.models import ProtectionCommitToken
from custom_components.rental_control.allocator.models import ProtectionLoadResult
from custom_components.rental_control.allocator.models import ProtectionLoadState
from custom_components.rental_control.allocator.registry import AllocationRegistry
from custom_components.rental_control.allocator.registry_safety_models import (
    PendingSlotFence,
)
from custom_components.rental_control.allocator.store import code_ref_for
from custom_components.rental_control.allocator.store import serialize_registry_ledger
from custom_components.rental_control.const import DOMAIN
from custom_components.rental_control.const import PROTECTED_REGISTRY_STORE
from custom_components.rental_control.coordinator_helpers import keymaster_version_issue
from custom_components.rental_control.keymaster_identity import CapabilityStatus
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
from custom_components.rental_control.keymaster_operations import _authorization_inputs
from custom_components.rental_control.keymaster_operations import _matching_fence
from custom_components.rental_control.keymaster_operations import (
    _resolve_required_capabilities,
)
from custom_components.rental_control.keymaster_operations import (
    create_verified_operation_intent,
)
from custom_components.rental_control.keymaster_version import (
    diagnose_keymaster_version,
)

from tests.fixtures.keymaster_releases import artifact_paths
from tests.fixtures.keymaster_releases import extract_keymaster_release
from tests.fixtures.keymaster_releases import import_keymaster_release
from tests.fixtures.keymaster_releases import remove_imported_package
from tests.integration.test_verified_consumer_dispatch import _setup_verified_consumer

_RELEASES = ("v0.6.1", "v0.6.5")
_PROPERTIES = (
    "button.code_slots:21.reset",
    "switch.code_slots:21.enabled",
    "switch.code_slots:21.accesslimit_date_range_enabled",
    "switch.code_slots:21.override_parent",
    "switch.code_slots:21.notifications",
    "datetime.code_slots:21.accesslimit_date_range_start",
    "datetime.code_slots:21.accesslimit_date_range_end",
    "text.code_slots:21.pin",
    "text.code_slots:21.name",
)
_TX = "a" * 32


class _ProtectedStore:
    """Keep the test snapshot/token relationship explicit and revocable."""

    authorization_state = ProtectionAuthorizationState.READY

    def __init__(
        self,
        data: dict[str, Any],
        associations: dict[str, AssociationTuple],
    ) -> None:
        """Bind one acknowledged snapshot and its selected entry tuples."""
        self.loaded_result = ProtectionLoadResult(
            ProtectionLoadState.VALID_V2,
            data=data,
            transaction_id=_TX,
            generation=1,
        )
        self.associations = associations
        self.token_current = True

    def association_for_entry(self, rc_entry_id: str) -> AssociationTuple | None:
        """Return the exact tuple only while the test store is authorized."""
        return self.associations.get(rc_entry_id) if self.token_current else None

    def is_current_commit_token(self, token: ProtectionCommitToken) -> bool:
        """Reject a superseded or otherwise stale snapshot token."""
        return bool(
            self.token_current
            and token.transaction_id == _TX
            and token.generation == 1
            and self.loaded_result.transaction_id == token.transaction_id
            and self.loaded_result.generation == token.generation
        )

    def operation_snapshot_ready(self) -> bool:
        """Expose a settled READY snapshot for a verified consumer intent."""
        return bool(
            self.authorization_state is ProtectionAuthorizationState.READY
            and self.loaded_result.state is ProtectionLoadState.VALID_V2
            and self.loaded_result.data is not None
        )


def _association(rc_entry_id: str, km_entry_id: str) -> AssociationTuple:
    """Return one exact committed verified tuple for synthetic test entries."""
    return AssociationTuple(
        legacy_lock_selection_present=True,
        legacy_lock_selection_raw=f"lock.{rc_entry_id}",
        verified_keymaster_entry_id=km_entry_id,
        association_generation=1,
        association_transaction_id="b" * 32,
        mode=AssociationMode.VERIFIED,
    )


def _registry_data(
    rc_entries: tuple[MockConfigEntry, MockConfigEntry],
    km_entries: tuple[MockConfigEntry, MockConfigEntry],
    associations: dict[str, AssociationTuple],
) -> dict[str, Any]:
    """Create PIN-free references and acknowledged slot fences for test owners."""
    salt = "persisted-test-reference-salt"
    registry = AllocationRegistry(code_ref_salt=salt)
    for index, (rc_entry, _km_entry) in enumerate(
        zip(rc_entries, km_entries, strict=True)
    ):
        code = ("1234", "5678")[index]
        identity_key = f"reservation-{index}"
        owner = AllocationOwner(
            entry_id=rc_entry.entry_id,
            identity_key=identity_key,
            origin=AllocationOrigin.ADOPTED,
            lockname=f"lock.{rc_entry.entry_id}",
            slot=21,
            lock_observed=True,
            first_seen="2026-11-21T00:00:00+00:00",
            last_seen="2026-11-21T00:00:00+00:00",
        )
        code_reference = code_ref_for(code, salt)
        registry.records[code_reference] = AllocationRecord(
            code=code,
            code_ref=code_reference,
            encoding_salt_value=rc_entry.entry_id,
            code_length=4,
            owners=[owner],
            created_at="2026-11-21T00:00:00+00:00",
            updated_at="2026-11-21T00:00:00+00:00",
        )
    data: dict[str, Any] = {
        "schema_version": 2,
        "transaction_id": _TX,
        "generation": 1,
        "ledger": serialize_registry_ledger(registry),
        "associations": [
            association_storage_record(
                rc_entry.entry_id, associations[rc_entry.entry_id]
            )
            for rc_entry in rc_entries
        ],
        "pending_fences": [],
    }
    for index, (rc_entry, km_entry) in enumerate(
        zip(rc_entries, km_entries, strict=True)
    ):
        refs = ledger_allocation_references(
            data, rc_entry.entry_id, f"reservation-{index}"
        )
        assert refs is not None
        owner_reference, allocation_reference, slot = refs
        data["pending_fences"].append(
            PendingSlotFence(
                fence_id=(str(index + 1) * 32),
                original_km_entry_id=km_entry.entry_id,
                slot=slot,
                rc_entry_id=rc_entry.entry_id,
                owner_ref=owner_reference,
                allocation_ref=allocation_reference,
                created_transaction_id=_TX,
                created_generation=1,
                fence_type="allocation",
            ).to_record()
        )
    return data


def _upstream_unique_id(
    hass,
    module_name: str,
    entry: MockConfigEntry,
    property_key: str,
) -> str:
    """Read the exact unique ID generated by the pinned upstream entity class."""
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
    return cast(str, entity.unique_id)


@pytest.mark.parametrize("release", _RELEASES)
@pytest.mark.parametrize("object_style", ("default", "area", "arbitrary"))
async def test_selected_entry_routes_overlapping_slots_by_upstream_identity(
    hass,
    tmp_path: Path,
    release: str,
    object_style: str,
) -> None:
    """Release identities survive default, area, and arbitrary object IDs."""
    artifacts = artifact_paths()
    assert f"keymaster-{release}" in artifacts
    rc_ids = ("rc-0", "rc-1")
    km_ids = ("km-0", "km-1")
    associations = {
        rc_id: _association(rc_id, km_id)
        for rc_id, km_id in zip(rc_ids, km_ids, strict=True)
    }
    rc_entries = cast(
        tuple[MockConfigEntry, MockConfigEntry],
        tuple(
            MockConfigEntry(
                domain=DOMAIN,
                title=f"Rental {index}",
                entry_id=rc_id,
                data=associations[rc_id].merge_into_config({}),
            )
            for index, rc_id in enumerate(rc_ids)
        ),
    )
    km_entries = cast(
        tuple[MockConfigEntry, MockConfigEntry],
        tuple(
            MockConfigEntry(
                domain="keymaster",
                title=f"Lock {index}",
                entry_id=km_id,
                data={"lock_entity_id": f"lock.physical_{index}"},
            )
            for index, km_id in enumerate(km_ids)
        ),
    )
    for rc_entry, km_entry in zip(rc_entries, km_entries, strict=True):
        rc_entry.add_to_hass(hass)
        km_entry.add_to_hass(hass)
        km_entry._async_set_state(hass, ConfigEntryState.LOADED, None)
        hass.data.setdefault(DOMAIN, {})[rc_entry.entry_id] = {
            "coordinator": SimpleNamespace(start_slot=21, max_events=1)
        }

    data = _registry_data(rc_entries, km_entries, associations)
    store = _ProtectedStore(data, associations)
    hass.data[DOMAIN][PROTECTED_REGISTRY_STORE] = store

    property_entity_ids: dict[tuple[str, str], str] = {}
    module_name = "custom_components.keymaster"
    assert module_name not in sys.modules
    import_keymaster_release(release, tmp_path / release, module_name)
    try:
        for rc_entry, _km_entry in zip(rc_entries, km_entries, strict=True):
            start_keymaster_lifecycle(
                hass,
                rc_entry,
                hass.data[DOMAIN][rc_entry.entry_id]["coordinator"],
            )
        for km_entry in km_entries:
            for property_key in _PROPERTIES:
                unique_id = _upstream_unique_id(
                    hass, module_name, km_entry, property_key
                )
                identity = build_capability_identity(
                    km_entry.entry_id, property_key, generation=1
                )
                assert identity is not None
                assert unique_id == identity.unique_id
                slot_property = slugify(property_key.split("code_slots:", 1)[1])
                object_id = {
                    "default": f"physical_{km_entry.entry_id}_{slot_property}",
                    "area": f"area_{km_entry.entry_id}_{slot_property}",
                    "arbitrary": f"custom_{km_entry.entry_id}_{slot_property}",
                }[object_style]
                registry_entry = er.async_get(hass).async_get_or_create(
                    identity.domain,
                    identity.platform,
                    unique_id,
                    suggested_object_id=object_id,
                    config_entry=km_entry,
                )
                hass.states.async_set(
                    registry_entry.entity_id,
                    "off" if identity.domain == "switch" else "ready",
                )
                property_entity_ids[(km_entry.entry_id, property_key)] = (
                    registry_entry.entity_id
                )
        await hass.async_block_till_done()

        intents = []
        for index, rc_entry in enumerate(rc_entries):
            lifecycle = get_keymaster_lifecycle(hass, rc_entry.entry_id)
            assert lifecycle is not None
            assert all(lifecycle.current[key].ready for key in _PROPERTIES)
            assert lifecycle.keymaster_entry_id == km_entries[index].entry_id
            assert (
                AssociationTuple.from_config(rc_entry.data)
                == associations[rc_entry.entry_id]
            )
            assert associations[rc_entry.entry_id].mode is AssociationMode.VERIFIED
            assert (
                associations[rc_entry.entry_id].verified_keymaster_entry_id
                == lifecycle.keymaster_entry_id
            )
            assert hass.config_entries.async_get_entry(rc_entry.entry_id) is rc_entry
            assert isinstance(rc_entry.data, Mapping)
            assert hass.data[DOMAIN][rc_entry.entry_id]
            assert hass.data[DOMAIN][PROTECTED_REGISTRY_STORE] is store
            assert km_entries[index].state is ConfigEntryState.LOADED
            assert km_entries[index].data["lock_entity_id"].startswith("lock.")
            assert (
                store.association_for_entry(rc_entry.entry_id)
                == associations[rc_entry.entry_id]
            )
            assert store.is_current_commit_token(ProtectionCommitToken(_TX, 1))
            assert store.loaded_result.state is ProtectionLoadState.VALID_V2
            assert store.loaded_result.data is not None
            assert store.loaded_result.transaction_id == _TX
            assert store.loaded_result.generation == 1
            assert (
                ledger_allocation_references(
                    data, rc_entry.entry_id, f"reservation-{index}"
                )
                is not None
            )
            inputs = _authorization_inputs(hass, rc_entry.entry_id)
            assert inputs is not None
            assert (
                _resolve_required_capabilities(hass, inputs, 21, _PROPERTIES)
                is not None
            )
            refs = ledger_allocation_references(
                data, rc_entry.entry_id, f"reservation-{index}"
            )
            assert refs is not None
            assert (
                _matching_fence(
                    inputs,
                    rc_entry.entry_id,
                    21,
                    refs[0],
                    refs[1],
                    "allocation",
                )
                is not None
            )
            intent = create_verified_operation_intent(
                hass,
                rc_entry.entry_id,
                slot=21,
                owner_identity_key=f"reservation-{index}",
                required_properties=_PROPERTIES,
            )
            assert intent is not None
            intents.append(intent)

        assert intents[0].keymaster_entry_id == "km-0"
        assert intents[1].keymaster_entry_id == "km-1"
        assert intents[0].physical_lock_entity_id == "lock.physical_0"
        assert intents[1].physical_lock_entity_id == "lock.physical_1"
        assert (
            intents[0].entity_id("text.code_slots:21.name")
            == property_entity_ids[("km-0", "text.code_slots:21.name")]
        )
        assert (
            intents[1].entity_id("text.code_slots:21.name")
            == property_entity_ids[("km-1", "text.code_slots:21.name")]
        )
        assert intents[0].entity_id("text.code_slots:21.name") != (
            intents[1].entity_id("text.code_slots:21.name")
        )

        old_entity_id = intents[0].entity_id("text.code_slots:21.name")
        new_entity_id = "text.arbitrary_renamed_name"
        er.async_get(hass).async_update_entity(
            old_entity_id, new_entity_id=new_entity_id
        )
        hass.states.async_set(new_entity_id, "ready")
        assert not intents[0].is_current()
        await hass.async_block_till_done()
        assert intents[1].is_current()
        rebound = create_verified_operation_intent(
            hass,
            rc_entries[0].entry_id,
            slot=21,
            owner_identity_key="reservation-0",
            required_properties=_PROPERTIES,
        )
        assert rebound is not None
        assert rebound.entity_id("text.code_slots:21.name") == new_entity_id
    finally:
        for rc_entry in rc_entries:
            lifecycle = (
                hass.data.get(DOMAIN, {})
                .get(rc_entry.entry_id, {})
                .get("keymaster_lifecycle")
            )
            if lifecycle is not None:
                lifecycle.stop()
        remove_imported_package(module_name)


@pytest.mark.parametrize("release", _RELEASES)
async def test_selected_entry_manages_only_configured_ranges_and_ignores_gaps(
    hass,
    tmp_path: Path,
    release: str,
) -> None:
    """Two lock ranges remain exact while gap and out-of-range IDs distract."""
    ranges = {
        "rc-low": (20, 2, "km-primary"),
        "rc-high": (23, 2, "km-primary"),
        "rc-other-lock": (20, 2, "km-secondary"),
    }
    associations = {
        rc_id: _association(rc_id, km_id)
        for rc_id, (_start, _count, km_id) in ranges.items()
    }
    rc_entries = {
        rc_id: MockConfigEntry(
            domain=DOMAIN,
            title=rc_id,
            entry_id=rc_id,
            data=associations[rc_id].merge_into_config({}),
        )
        for rc_id in ranges
    }
    km_entries = {
        km_id: MockConfigEntry(
            domain="keymaster",
            title=km_id,
            entry_id=km_id,
            data={"lock_entity_id": f"lock.{km_id}"},
        )
        for km_id in ("km-primary", "km-secondary")
    }
    domain_data = hass.data.setdefault(DOMAIN, {})
    for km_entry in km_entries.values():
        km_entry.add_to_hass(hass)
        km_entry._async_set_state(hass, ConfigEntryState.LOADED, None)
    for rc_id, rc_entry in rc_entries.items():
        rc_entry.add_to_hass(hass)
        start, count, _km_id = ranges[rc_id]
        domain_data[rc_id] = {
            "coordinator": SimpleNamespace(start_slot=start, max_events=count)
        }

    class _AssociationStore:
        """Expose only the acknowledged tuple used to start each lifecycle."""

        def association_for_entry(self, entry_id: str) -> AssociationTuple | None:
            """Return the committed association for this synthetic entry."""
            return associations.get(entry_id)

    domain_data[PROTECTED_REGISTRY_STORE] = _AssociationStore()
    property_keys_by_lock: dict[str, set[str]] = {km_id: set() for km_id in km_entries}
    for rc_id, (start, count, km_id) in ranges.items():
        property_keys_by_lock[km_id].update(managed_slot_property_keys(start, count))
        start_keymaster_lifecycle(
            hass,
            rc_entries[rc_id],
            domain_data[rc_id]["coordinator"],
        )

    module_name = "custom_components.keymaster"
    assert module_name not in sys.modules
    import_keymaster_release(release, tmp_path / release, module_name)
    try:
        for km_id, km_entry in km_entries.items():
            # Exact upstream identities outside the configured ranges are
            # deliberate distractors, not capabilities for these RC entries.
            properties = property_keys_by_lock[km_id] | {
                f"text.code_slots:{slot}.name" for slot in (22, 25)
            }
            for property_key in sorted(properties):
                unique_id = _upstream_unique_id(
                    hass, module_name, km_entry, property_key
                )
                identity = build_capability_identity(km_id, property_key, generation=1)
                assert identity is not None
                assert unique_id == identity.unique_id
                registry_entry = er.async_get(hass).async_get_or_create(
                    identity.domain,
                    identity.platform,
                    unique_id,
                    suggested_object_id=f"distractor_{km_id}_{slugify(property_key)}",
                    config_entry=km_entry,
                )
                hass.states.async_set(
                    registry_entry.entity_id,
                    "off" if identity.domain == "switch" else "ready",
                )
        await hass.async_block_till_done()

        for rc_id, (start, count, km_id) in ranges.items():
            lifecycle = get_keymaster_lifecycle(hass, rc_id)
            assert lifecycle is not None
            expected = set(managed_slot_property_keys(start, count))
            assert set(lifecycle.current) == expected
            assert all(lifecycle.current[key].ready for key in expected)
            assert lifecycle.keymaster_entry_id == km_id
            assert all(
                f"text.code_slots:{slot}.name" not in lifecycle.current
                for slot in (22, 25)
            )
        low = get_keymaster_lifecycle(hass, "rc-low")
        other_lock = get_keymaster_lifecycle(hass, "rc-other-lock")
        assert low is not None and other_lock is not None
        assert low.current["text.code_slots:20.name"].identity is not None
        assert other_lock.current["text.code_slots:20.name"].identity is not None
        assert (
            low.current["text.code_slots:20.name"].identity.keymaster_entry_id
            != other_lock.current["text.code_slots:20.name"].identity.keymaster_entry_id
        )
    finally:
        for rc_id in rc_entries:
            stop_keymaster_lifecycle(hass, rc_id)
        remove_imported_package(module_name)


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize("release", _RELEASES)
@pytest.mark.parametrize(
    ("reset_case", "expected_status"),
    [
        ("missing", CapabilityStatus.MISSING),
        ("disabled", CapabilityStatus.DISABLED),
        ("unavailable", CapabilityStatus.UNAVAILABLE),
        ("ambiguous", CapabilityStatus.AMBIGUOUS),
    ],
)
async def test_reset_readiness_does_not_trigger_outdated_version_warning(
    hass,
    tmp_path: Path,
    release: str,
    reset_case: str,
    expected_status: CapabilityStatus,
) -> None:
    """Real release metadata is support-only across reset readiness outcomes."""
    coordinator, _store, entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage", release
    )
    reset_id = entity_ids["button.code_slots:21.reset"]
    lifecycle = get_keymaster_lifecycle(hass, coordinator.config_entry.entry_id)
    assert lifecycle is not None
    reset_key = "button.code_slots:21.reset"
    if reset_case == "missing":
        er.async_get(hass).async_remove(reset_id)
        await hass.async_block_till_done()
    elif reset_case == "disabled":
        er.async_get(hass).async_update_entity(
            reset_id, disabled_by=er.RegistryEntryDisabler.USER
        )
        await hass.async_block_till_done()
    elif reset_case == "unavailable":
        hass.states.async_set(reset_id, "unavailable")
        await hass.async_block_till_done()
    else:
        # HA enforces unique IDs; model the resolver's ambiguous outcome
        # without corrupting its registry to test version independence.
        resolutions = dict(lifecycle.current)
        resolutions[reset_key] = replace(
            lifecycle.current[reset_key],
            status=CapabilityStatus.AMBIGUOUS,
            entity_id=None,
            registry_entry_id=None,
        )
        lifecycle._resolutions = resolutions
    assert lifecycle.current[reset_key].status is expected_status
    association = AssociationTuple.from_config(coordinator.config_entry.data)
    assert association is not None
    assert association.verified_keymaster_entry_id is not None
    selected = hass.config_entries.async_get_entry(
        association.verified_keymaster_entry_id
    )
    assert selected is not None
    _cache_real_keymaster_fixture_metadata(hass, release, tmp_path / "manifest")
    coordinator.config_entry._async_set_state(hass, ConfigEntryState.LOADED, None)
    keymaster_version_issue.register_keymaster_version_tracking(
        hass, coordinator.config_entry.entry_id
    )
    try:
        assert diagnose_keymaster_version(hass).status.value == "supported"
        association = AssociationTuple.from_config(coordinator.config_entry.data)
        assert association is not None
        assert association.verified_keymaster_entry_id is not None
        issue_id = f"keymaster_version_{association.verified_keymaster_entry_id}"
        assert async_get_issue_registry(hass).async_get_issue(DOMAIN, issue_id) is None
    finally:
        keymaster_version_issue.unregister_keymaster_version_tracking(
            hass, coordinator.config_entry.entry_id
        )
        selected._async_set_state(hass, ConfigEntryState.NOT_LOADED, None)
        coordinator.config_entry._async_set_state(
            hass, ConfigEntryState.NOT_LOADED, None
        )
        stop_keymaster_lifecycle(hass, coordinator.config_entry.entry_id)
        hass.data[loader.DATA_INTEGRATIONS].pop("keymaster", None)
        await hass.async_block_till_done()


def _cache_real_keymaster_fixture_metadata(
    hass, release: str, destination: Path
) -> None:
    """Expose the exact pinned package manifest as HA's already-loaded cache."""
    package_root = extract_keymaster_release(release, destination)
    manifest = cast(
        loader.Manifest,
        json.loads((package_root / "manifest.json").read_text(encoding="utf-8")),
    )
    integration = loader.Integration(
        hass,
        "fixture.keymaster",
        package_root,
        manifest,
    )
    hass.data.setdefault(loader.DATA_INTEGRATIONS, {})["keymaster"] = integration


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_setup_unload_entry_tracks_selected_keymaster_version_issue(
    hass,
    tmp_path: Path,
    mock_config_entry: MockConfigEntry,
    mock_aiohttp_session,
) -> None:
    """Setup and unload wire the real version tracker without lockless warnings."""
    association = AssociationTuple(
        legacy_lock_selection_present=True,
        legacy_lock_selection_raw="front_door",
        verified_keymaster_entry_id="km-selected-for-entry-lifecycle",
        association_generation=1,
        association_transaction_id="a" * 32,
        mode=AssociationMode.VERIFIED,
    )
    selected = MockConfigEntry(
        domain="keymaster",
        title="Selected lock",
        entry_id="km-selected-for-entry-lifecycle",
        data={"lock_entity_id": "lock.front_door"},
    )
    selected.add_to_hass(hass)
    verified_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Verified rental",
        version=10,
        entry_id="verified-entry-lifecycle",
        unique_id="verified-entry-lifecycle-unique",
        data={
            **association.merge_into_config(mock_config_entry.data),
            "name": "Verified Rental",
        },
        options=mock_config_entry.options,
    )
    verified_entry.add_to_hass(hass)

    mock_aiohttp_session.get(
        mock_config_entry.data["url"],
        status=200,
        body="BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n",
        repeat=True,
    )
    _cache_real_keymaster_fixture_metadata(
        hass, "v0.6.0", tmp_path / "old-keymaster-manifest"
    )

    assert await hass.config_entries.async_setup(verified_entry.entry_id)
    await hass.async_block_till_done()

    # Give the registered tracker the exact acknowledged association snapshot.
    # The real integration setup/unload entrypoints and tracker remain active.
    hass.data[DOMAIN][PROTECTED_REGISTRY_STORE] = _ProtectedStore(
        {}, {verified_entry.entry_id: association}
    )
    selected._async_set_state(hass, ConfigEntryState.LOADED, None)
    await hass.async_block_till_done()

    issue_registry = async_get_issue_registry(hass)
    issue_id = f"keymaster_version_{selected.entry_id}"
    issue = issue_registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_key == "keymaster_version_outside_support"
    assert issue.config_entry_id == selected.entry_id
    assert (
        sum(
            domain == DOMAIN and candidate_id.startswith("keymaster_version_")
            for domain, candidate_id in issue_registry.issues
        )
        == 1
    )

    assert await hass.config_entries.async_unload(verified_entry.entry_id)
    await hass.async_block_till_done()
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None

    lockless_association = AssociationTuple(
        legacy_lock_selection_present=False,
        legacy_lock_selection_raw=None,
        verified_keymaster_entry_id=None,
        association_generation=1,
        association_transaction_id="b" * 32,
        mode=AssociationMode.EXPLICIT_LOCKLESS,
    )
    lockless_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Lockless rental",
        version=10,
        entry_id="lockless-entry-lifecycle",
        unique_id="lockless-entry-lifecycle-unique",
        data={
            **lockless_association.merge_into_config(mock_config_entry.data),
            "name": "Lockless Rental",
        },
        options=mock_config_entry.options,
    )
    lockless_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(lockless_entry.entry_id)
    await hass.async_block_till_done()
    hass.data[DOMAIN][PROTECTED_REGISTRY_STORE] = _ProtectedStore(
        {}, {lockless_entry.entry_id: lockless_association}
    )
    selected._async_set_state(hass, ConfigEntryState.NOT_LOADED, None)
    selected._async_set_state(hass, ConfigEntryState.LOADED, None)
    await hass.async_block_till_done()
    assert not any(
        domain == DOMAIN and candidate_id.startswith("keymaster_version_")
        for domain, candidate_id in issue_registry.issues
    )

    assert await hass.config_entries.async_unload(lockless_entry.entry_id)
    await hass.async_block_till_done()
    selected._async_set_state(hass, ConfigEntryState.NOT_LOADED, None)
    hass.data[loader.DATA_INTEGRATIONS].pop("keymaster", None)
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_version_issue_tracks_late_selected_entry_and_shared_selection(
    hass,
    tmp_path: Path,
) -> None:
    """Loaded selected-entry events diagnose and deduplicate one shared issue."""
    coordinator, store, _entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage", "v0.6.1"
    )
    association = AssociationTuple.from_config(coordinator.config_entry.data)
    assert association is not None
    assert association.verified_keymaster_entry_id is not None
    selected = hass.config_entries.async_get_entry(
        association.verified_keymaster_entry_id
    )
    assert selected is not None
    coordinator.config_entry._async_set_state(hass, ConfigEntryState.LOADED, None)
    selected._async_set_state(hass, ConfigEntryState.NOT_LOADED, None)
    _cache_real_keymaster_fixture_metadata(hass, "v0.6.0", tmp_path / "old-manifest")

    second_entry: MockConfigEntry | None = None
    second_entry_id = "consumer-rc-second"
    keymaster_version_issue.register_keymaster_version_tracking(
        hass, coordinator.config_entry.entry_id
    )
    registry = async_get_issue_registry(hass)
    issue_id = f"keymaster_version_{selected.entry_id}"
    try:
        assert registry.async_get_issue(DOMAIN, issue_id) is None

        selected._async_set_state(hass, ConfigEntryState.LOADED, None)
        issue = registry.async_get_issue(DOMAIN, issue_id)
        assert issue is not None
        assert issue.translation_key == "keymaster_version_outside_support"
        assert issue.config_entry_id == selected.entry_id

        second_entry = MockConfigEntry(
            domain=DOMAIN,
            title="Second consumer",
            entry_id=second_entry_id,
            data={},
        )
        second_entry.add_to_hass(hass)
        prior_data = store._loaded_result.data
        assert prior_data is not None
        transaction_id = "2" * 32
        second_association = AssociationTuple(
            True,
            "lock.consumer",
            selected.entry_id,
            1,
            transaction_id,
            AssociationMode.VERIFIED,
        )
        old_association = AssociationTuple.from_config(second_entry.data)
        assert old_association is not None
        transaction = AssociationTransaction(
            transaction_id,
            prior_data["generation"] + 1,
            prior_data["transaction_id"],
            (
                AssociationChange(
                    second_entry_id,
                    old_association,
                    second_association,
                ),
            ),
        )
        committed = await store.async_commit_association(transaction)
        assert committed.state is ProtectionLoadState.VALID_V2
        hass.config_entries.async_update_entry(
            second_entry,
            data=second_association.merge_into_config(second_entry.data),
        )
        hass.data[DOMAIN][second_entry_id] = {"coordinator": object()}
        second_entry._async_set_state(hass, ConfigEntryState.LOADED, None)
        keymaster_version_issue.register_keymaster_version_tracking(
            hass, second_entry_id
        )
        version_issues = [
            item
            for (domain, candidate_id), item in registry.issues.items()
            if domain == DOMAIN and candidate_id.startswith("keymaster_version_")
        ]
        assert len(version_issues) == 1

        selected._async_set_state(hass, ConfigEntryState.NOT_LOADED, None)
        assert registry.async_get_issue(DOMAIN, issue_id) is None
        selected._async_set_state(hass, ConfigEntryState.LOADED, None)
        assert registry.async_get_issue(DOMAIN, issue_id) is not None

        keymaster_version_issue.unregister_keymaster_version_tracking(
            hass, coordinator.config_entry.entry_id
        )
        assert registry.async_get_issue(DOMAIN, issue_id) is not None
        keymaster_version_issue.unregister_keymaster_version_tracking(
            hass, second_entry_id
        )
        assert registry.async_get_issue(DOMAIN, issue_id) is None
    finally:
        keymaster_version_issue.unregister_keymaster_version_tracking(
            hass, coordinator.config_entry.entry_id
        )
        keymaster_version_issue.unregister_keymaster_version_tracking(
            hass, second_entry_id
        )
        selected._async_set_state(hass, ConfigEntryState.NOT_LOADED, None)
        coordinator.config_entry._async_set_state(
            hass, ConfigEntryState.NOT_LOADED, None
        )
        stop_keymaster_lifecycle(hass, coordinator.config_entry.entry_id)
        if second_entry is not None:
            second_entry._async_set_state(hass, ConfigEntryState.NOT_LOADED, None)
        hass.data[loader.DATA_INTEGRATIONS].pop("keymaster", None)
        await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_explicit_lockless_entry_never_gets_keymaster_version_issue(
    hass,
    tmp_path: Path,
) -> None:
    """Even loaded old Keymaster metadata is irrelevant to a lockless entry."""
    association = AssociationTuple(
        False,
        None,
        None,
        1,
        "3" * 32,
        AssociationMode.EXPLICIT_LOCKLESS,
    )
    rc_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Lockless calendar",
        entry_id="consumer-lockless",
        data=association.merge_into_config({}),
    )
    km_entry = MockConfigEntry(
        domain="keymaster",
        title="Unused Keymaster",
        entry_id="unused-keymaster",
        data={"lock_entity_id": "lock.unused"},
    )
    rc_entry.add_to_hass(hass)
    km_entry.add_to_hass(hass)
    hass.data.setdefault(DOMAIN, {})[rc_entry.entry_id] = {"coordinator": object()}
    rc_entry._async_set_state(hass, ConfigEntryState.LOADED, None)
    _cache_real_keymaster_fixture_metadata(hass, "v0.6.0", tmp_path / "manifest")

    keymaster_version_issue.register_keymaster_version_tracking(hass, rc_entry.entry_id)
    try:
        km_entry._async_set_state(hass, ConfigEntryState.LOADED, None)

        registry = async_get_issue_registry(hass)
        assert (
            registry.async_get_issue(DOMAIN, f"keymaster_version_{km_entry.entry_id}")
            is None
        )
    finally:
        keymaster_version_issue.unregister_keymaster_version_tracking(
            hass, rc_entry.entry_id
        )
        km_entry._async_set_state(hass, ConfigEntryState.NOT_LOADED, None)
        rc_entry._async_set_state(hass, ConfigEntryState.NOT_LOADED, None)
        hass.data[loader.DATA_INTEGRATIONS].pop("keymaster", None)
        await hass.async_block_till_done()
