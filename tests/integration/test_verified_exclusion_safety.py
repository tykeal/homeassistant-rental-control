# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Verify exclusions remain scoped to acknowledged identity and safe slots."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from typing import cast

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import ServiceCall
import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.rental_control.allocator.association_references import (
    association_references_for_snapshot,
)
from custom_components.rental_control.allocator.exclusion_service import (
    _handle_set_slot_exclusion_call,
)
from custom_components.rental_control.allocator.models import CycleObservation
from custom_components.rental_control.allocator.reissue_preview import (
    observe_managed_slots_without_mutation,
)
from custom_components.rental_control.allocator.singleton import get_allocator
from custom_components.rental_control.config_flow_helpers.association import (
    apply_association_selection,
)
from custom_components.rental_control.const import CONF_ASSOCIATION_ATTEST_SAME_LOCK
from custom_components.rental_control.const import CONF_ASSOCIATION_ENTRY_CHOICE
from custom_components.rental_control.const import LOCK_MANAGER
from custom_components.rental_control.coordinator_helpers.cycle_requests import (
    build_cycle_observation,
)
from custom_components.rental_control.coordinator_helpers.models import (
    KeymasterSlotSnapshot,
)
from custom_components.rental_control.coordinator_helpers.slot_exclusions import (
    load as load_exclusions,
)
from custom_components.rental_control.coordinator_helpers.slot_exclusions import (
    persist_excluded_slots,
)
from custom_components.rental_control.coordinator_helpers.slot_exclusions import (
    revalidate_persisted,
)
from custom_components.rental_control.keymaster_operations import (
    async_prepare_verified_operation_intent,
)

from tests.integration.test_verified_consumer_dispatch import _KM_ENTRY_ID
from tests.integration.test_verified_consumer_dispatch import _OWNER_IDENTITY
from tests.integration.test_verified_consumer_dispatch import _RC_ENTRY_ID
from tests.integration.test_verified_consumer_dispatch import _SLOT
from tests.integration.test_verified_consumer_dispatch import _setup_verified_consumer


def _service_call(data: dict[str, Any]) -> ServiceCall:
    """Construct the narrow service-call payload used by the handler."""
    return cast(ServiceCall, SimpleNamespace(data=data))


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_exclusion_creation_requires_current_verified_association(
    hass, tmp_path: Path
) -> None:
    """Unresolved config cannot create an exclusion but can withdraw one."""
    coordinator, _store, _entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage"
    )
    coordinator.lockname = "lock.consumer"
    entry = coordinator.config_entry
    legacy_data = {
        key: value for key, value in entry.data.items() if key == "keymaster_entry_id"
    }
    hass.config_entries.async_update_entry(entry, data=legacy_data)

    creation = await _handle_set_slot_exclusion_call(
        hass,
        _service_call(
            {
                "lockname": "lock.consumer",
                "slot": _SLOT,
                "excluded": True,
                "dry_run": False,
            }
        ),
    )

    assert creation["status"] == "refused"
    assert creation["reason"] == "association_unverified"
    assert load_exclusions(hass, _RC_ENTRY_ID) == {}

    assert persist_excluded_slots(hass, _RC_ENTRY_ID, {_SLOT: "old exclusion"})
    withdrawal = await _handle_set_slot_exclusion_call(
        hass,
        _service_call(
            {
                "lockname": "lock.consumer",
                "slot": _SLOT,
                "excluded": False,
                "dry_run": False,
            }
        ),
    )
    assert withdrawal["status"] == "ok"
    assert withdrawal["changed"] is True
    assert load_exclusions(hass, _RC_ENTRY_ID) == {}


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_exclusion_applies_only_to_unclaimed_unreadable_managed_slot(
    hass, tmp_path: Path
) -> None:
    """A claimed slot stays protected while an eligible gap can be excluded."""
    coordinator, _store, _entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage"
    )
    coordinator.lockname = "lock.consumer"
    coordinator.max_events = 2
    coordinator._read_slot_snapshot = lambda slot: KeymasterSlotSnapshot(slot=slot)

    claimed = await _handle_set_slot_exclusion_call(
        hass,
        _service_call(
            {
                "lockname": "lock.consumer",
                "slot": _SLOT,
                "excluded": True,
                "dry_run": False,
            }
        ),
    )
    assert claimed["status"] == "refused"
    assert claimed["reason"] == "slot_claimed"
    assert load_exclusions(hass, _RC_ENTRY_ID) == {}

    eligible_slot = _SLOT + 1
    excluded = await _handle_set_slot_exclusion_call(
        hass,
        _service_call(
            {
                "lockname": "lock.consumer",
                "slot": eligible_slot,
                "excluded": True,
                "reason": "unreadable slot",
                "dry_run": False,
            }
        ),
    )
    assert excluded["status"] == "ok"
    assert excluded["changed"] is True
    assert load_exclusions(hass, _RC_ENTRY_ID) == {eligible_slot: "unreadable slot"}


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_exclusion_is_gate_only_and_preserves_protected_guest_fence(
    hass, tmp_path: Path
) -> None:
    """Excluding another unreadable slot cannot release guest safety history."""
    coordinator, store, _entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage"
    )
    coordinator.lockname = "lock.consumer"
    coordinator.max_events = 2
    coordinator._read_slot_snapshot = lambda slot: KeymasterSlotSnapshot(slot=slot)

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
    before = store.loaded_result
    assert before is not None and before.data is not None
    protected_before = deepcopy(before.data)

    result = await _handle_set_slot_exclusion_call(
        hass,
        _service_call(
            {
                "lockname": "lock.consumer",
                "slot": _SLOT + 1,
                "excluded": True,
                "reason": "unreadable slot",
                "dry_run": False,
            }
        ),
    )

    assert result["status"] == "ok"
    assert load_exclusions(hass, _RC_ENTRY_ID) == {_SLOT + 1: "unreadable slot"}
    observation = build_cycle_observation(
        _RC_ENTRY_ID,
        "lock.consumer",
        observe_managed_slots_without_mutation(coordinator),
    )
    assert _SLOT + 1 in observation.unreadable_slots
    after = store.loaded_result
    assert after is not None and after.data == protected_before
    assert any(
        fence["fence_type"] == "allocation"
        and fence["original_km_entry_id"] == allocation.keymaster_entry_id
        and fence["slot"] == _SLOT
        for fence in after.data["pending_fences"]
    )
    allocator = get_allocator(hass)
    assert allocator is not None
    assert allocator._registry.record_for_identity(_OWNER_IDENTITY) is not None


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_exclusion_withdrawal_preserves_rebind_hold_and_adopted_claim(
    hass, tmp_path: Path
) -> None:
    """Config-only exclusion withdrawal cannot erase cross-entry safety holds."""
    coordinator, store, _entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage"
    )
    coordinator.lockname = "lock.consumer"
    entry = coordinator.config_entry
    excluded_slot = _SLOT + 5
    assert persist_excluded_slots(
        hass, _RC_ENTRY_ID, {excluded_slot: "unreadable before rebind"}
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
    baseline = store.loaded_result
    assert baseline is not None and baseline.data is not None
    references = association_references_for_snapshot(baseline.data, _RC_ENTRY_ID)
    assert references

    replacement = MockConfigEntry(
        domain=LOCK_MANAGER,
        title="Replacement Front Door",
        entry_id="replacement-keymaster-exclusion",
        data={"lock_entity_id": "lock.replacement_front_door"},
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

    before_withdrawal = store.loaded_result
    assert before_withdrawal is not None and before_withdrawal.data is not None
    protected_before = deepcopy(before_withdrawal.data)
    holds = protected_before["holds"]
    assert len(holds) == 1
    assert holds[0]["kind"] == "association_rebind"
    assert holds[0]["original_km_entry_id"] == _KM_ENTRY_ID
    assert holds[0]["selected_keymaster_entry_id"] == replacement.entry_id
    assert holds[0]["claim_references"] == list(references)
    assert protected_before["ledger"]["records"][0]["owners"][0]["origin"] == "adopted"

    withdrawal = await _handle_set_slot_exclusion_call(
        hass,
        _service_call(
            {
                "lockname": "lock.consumer",
                "slot": excluded_slot,
                "excluded": False,
                "dry_run": False,
            }
        ),
    )
    assert withdrawal["status"] == "ok"
    assert load_exclusions(hass, _RC_ENTRY_ID) == {}
    after_withdrawal = store.loaded_result
    assert after_withdrawal is not None
    assert after_withdrawal.data == protected_before


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_healthy_recovery_clears_only_exclusion_not_protective_evidence(
    hass, tmp_path: Path
) -> None:
    """A readable recovery clears its exclusion without rewriting raw safety."""
    coordinator, store, _entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage"
    )
    coordinator.lockname = "lock.consumer"
    assert persist_excluded_slots(
        hass, _RC_ENTRY_ID, {_SLOT + 1: "temporary unreadable slot"}
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
    before = store.loaded_result
    assert before is not None and before.data is not None
    protected_before = deepcopy(before.data)

    observation = CycleObservation(
        entry_id=_RC_ENTRY_ID,
        lockname="lock.consumer",
        managed_slots=frozenset({_SLOT, _SLOT + 1}),
        observed_codes={},
        unreadable_slots=frozenset(),
        keymaster_entry_id=allocation.keymaster_entry_id,
        keymaster_generation=allocation.lifecycle_generation,
    )
    remaining = load_exclusions(hass, _RC_ENTRY_ID)
    assert remaining == {_SLOT + 1: "temporary unreadable slot"}
    kept = revalidate_persisted(hass, _RC_ENTRY_ID, "lock.consumer", observation)
    assert kept == {}
    assert load_exclusions(hass, _RC_ENTRY_ID) == {}
    after = store.loaded_result
    assert after is not None and after.data == protected_before
    assert any(
        fence["fence_type"] == "allocation"
        and fence["original_km_entry_id"] == allocation.keymaster_entry_id
        and fence["slot"] == _SLOT
        for fence in after.data["pending_fences"]
    )


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_exclusion_expires_when_slot_leaves_managed_range(
    hass, tmp_path: Path
) -> None:
    """Unreadability outside the current managed range cannot retain an exclusion."""
    coordinator, _store, _entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage"
    )
    coordinator.lockname = "lock.consumer"
    excluded_slot = _SLOT + 1
    assert persist_excluded_slots(
        hass, _RC_ENTRY_ID, {excluded_slot: "unreadable before range change"}
    )
    observation = CycleObservation(
        entry_id=_RC_ENTRY_ID,
        lockname="lock.consumer",
        managed_slots=frozenset({_SLOT}),
        observed_codes={},
        unreadable_slots=frozenset({excluded_slot}),
        keymaster_entry_id=_KM_ENTRY_ID,
        keymaster_generation=1,
    )

    kept = revalidate_persisted(hass, _RC_ENTRY_ID, "lock.consumer", observation)

    assert kept == {}
    assert load_exclusions(hass, _RC_ENTRY_ID) == {}
