# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Verify overlapping managed slots bind to the selected Keymaster identity."""

from __future__ import annotations

from collections.abc import Mapping
from importlib import import_module
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any
from typing import cast
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify
import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.rental_control.allocator.association_references import (
    ledger_allocation_references,
)
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

from tests.fixtures.keymaster_releases import artifact_paths
from tests.fixtures.keymaster_releases import import_keymaster_release
from tests.fixtures.keymaster_releases import remove_imported_package

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
