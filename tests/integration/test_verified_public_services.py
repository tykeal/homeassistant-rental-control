# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Verify public selector compatibility, ambiguity refusal, and inert previews."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from typing import cast
from unittest.mock import AsyncMock

from homeassistant.core import ServiceCall
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant.common import MockConfigEntry

from custom_components.rental_control.allocator import reissue_service
from custom_components.rental_control.allocator.exclusion_service import (
    _handle_set_slot_exclusion_call,
)
from custom_components.rental_control.const import CONF_EXCLUDED_SLOTS
from custom_components.rental_control.const import COORDINATOR
from custom_components.rental_control.const import DOMAIN
from custom_components.rental_control.coordinator_helpers import exclusion_targets
from custom_components.rental_control.coordinator_helpers import reissue_targets
from custom_components.rental_control.reconciliation import make_reservation_fingerprint

_START = datetime(2026, 11, 21, 15, tzinfo=UTC)
_END = _START + timedelta(days=3)


def _service_call(data: dict[str, Any]) -> ServiceCall:
    """Build the service payload shape expected by Home Assistant handlers."""
    return cast(ServiceCall, SimpleNamespace(data=data))


def _entry(hass, entry_id: str, *, excluded: list[dict[str, Any]] | None = None):
    """Create one Rental Control entry and its loaded coordinator state."""
    data: dict[str, Any] = {"name": entry_id}
    if excluded is not None:
        data[CONF_EXCLUDED_SLOTS] = excluded
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=entry_id,
        entry_id=entry_id,
        data=data,
    )
    entry.add_to_hass(hass)
    coordinator = SimpleNamespace(
        lockname="front",
        start_slot=20,
        max_events=3,
        _latest_res_by_key={},
        _observed_slot_codes={},
    )
    hass.data.setdefault(DOMAIN, {})[entry_id] = {COORDINATOR: coordinator}
    return entry, coordinator


def _sensor(
    hass,
    entry,
    entity_id: str,
    *,
    slot_name: str = "Guest",
    start: datetime = _START,
    end: datetime = _END,
    slot_number: int = 20,
    register: bool = True,
) -> None:
    """Create a sensor state with attributes accepted by public selectors."""
    if register:
        er.async_get(hass).async_get_or_create(
            "sensor",
            DOMAIN,
            f"{entry.entry_id}-reservation",
            suggested_object_id=entity_id.split(".", maxsplit=1)[1],
            config_entry=entry,
        )
    hass.states.async_set(
        entity_id,
        "reserved",
        {
            "slot_name": slot_name,
            "start": start,
            "end": end,
            "slot_number": slot_number,
        },
    )


def test_slot_selectors_keep_disjoint_ranges_and_refuse_overlap(hass) -> None:
    """Lock plus slot selects one disjoint owner and rejects a collision."""
    _first, first_coordinator = _entry(hass, "service-first")
    _second, second_coordinator = _entry(hass, "service-second")
    first_coordinator.start_slot = 10
    second_coordinator.start_slot = 20
    first_coordinator.lockname = second_coordinator.lockname = "front"

    target, refusal = reissue_targets.resolve_slot_target(hass, "front", 11)
    assert not refusal
    assert target is not None and target.entry_id == "service-first"
    exclusion_target, exclusion_refusal = exclusion_targets.resolve_exclusion_target(
        hass,
        entity_id=None,
        lockname="front",
        slot=11,
        excluded=True,
    )
    assert not exclusion_refusal
    assert exclusion_target is not None
    assert exclusion_target.entry_id == "service-first"

    second_coordinator.start_slot = 11
    ambiguous, reason = reissue_targets.resolve_slot_target(hass, "front", 11)
    assert ambiguous is None
    assert reason == "ambiguous_lock"
    excluded, exclusion_reason = exclusion_targets.resolve_exclusion_target(
        hass,
        entity_id=None,
        lockname="front",
        slot=11,
        excluded=True,
    )
    assert excluded is None
    assert exclusion_reason == exclusion_targets.REFUSAL_AMBIGUOUS_LOCK


def test_sensor_registry_precedes_attributes_and_unique_attribute_fallback(
    hass,
) -> None:
    """Registry ownership wins; absent registry uses a unique fingerprint."""
    first, first_coordinator = _entry(hass, "registry-owner")
    _second, second_coordinator = _entry(hass, "fallback-owner")
    identity = make_reservation_fingerprint("fallback-owner", "Guest", _START, _END)
    second_coordinator._latest_res_by_key[identity] = object()

    registered_entity = "sensor.registry_owned_reservation"
    _sensor(
        hass,
        first,
        registered_entity,
        slot_name="Guest",
        start=_START,
        end=_END,
    )
    # Conflicting attributes must not override a registry's exact entry ID.
    state = hass.states.get(registered_entity)
    assert state is not None
    attrs = dict(state.attributes)
    attrs["slot_name"] = "Different Guest"
    hass.states.async_set(registered_entity, state.state, attrs)
    registry_target, registry_refusal = reissue_targets.resolve_entity_target(
        hass, registered_entity
    )
    assert not registry_refusal
    assert registry_target is not None
    assert registry_target.entry_id == first.entry_id

    unregistered_entity = "sensor.fallback_reservation"
    _sensor(
        hass,
        first,
        unregistered_entity,
        register=False,
        slot_name="Guest",
        start=_START,
        end=_END,
    )
    fallback_target, fallback_refusal = reissue_targets.resolve_entity_target(
        hass, unregistered_entity
    )
    assert not fallback_refusal
    assert fallback_target is not None
    assert fallback_target.entry_id == "fallback-owner"


def test_attribute_fallback_refuses_colliding_reservation_aliases(hass) -> None:
    """Identical fallback fingerprints never select an arbitrary owner."""
    first, first_coordinator = _entry(hass, "alias-first")
    second, second_coordinator = _entry(hass, "alias-second")
    identity_first = make_reservation_fingerprint(first.entry_id, "Guest", _START, _END)
    identity_second = make_reservation_fingerprint(
        second.entry_id, "Guest", _START, _END
    )
    first_coordinator._latest_res_by_key[identity_first] = object()
    second_coordinator._latest_res_by_key[identity_second] = object()
    entity_id = "sensor.ambiguous_alias"
    _sensor(hass, first, entity_id, register=False)

    target, refusal = reissue_targets.resolve_entity_target(hass, entity_id)

    assert target is None
    assert refusal == "not_a_reservation_sensor"


async def test_out_of_range_exclusion_withdrawal_and_dry_run_are_distinct(hass) -> None:
    """An old exclusion can be removed; a preview changes no config."""
    entry, coordinator = _entry(
        hass,
        "exclusion-owner",
        excluded=[{"slot": 99, "reason": "old unreadable slot"}],
    )
    coordinator.start_slot = 20
    coordinator.max_events = 3
    entity_id = "sensor.exclusion_owner_reservation"
    _sensor(hass, entry, entity_id, slot_number=20)

    preview = await _handle_set_slot_exclusion_call(
        hass,
        _service_call(
            {
                "entity_id": entity_id,
                "slot": 99,
                "excluded": False,
                "dry_run": True,
            }
        ),
    )
    assert preview["status"] == "ok"
    assert preview["dry_run"] is True
    assert preview["excluded_slots"] == []
    assert entry.data[CONF_EXCLUDED_SLOTS] == [
        {"slot": 99, "reason": "old unreadable slot"}
    ]

    result = await _handle_set_slot_exclusion_call(
        hass,
        _service_call(
            {
                "entity_id": entity_id,
                "slot": 99,
                "excluded": False,
                "dry_run": False,
            }
        ),
    )
    assert result["status"] == "ok"
    assert result["changed"] is True
    assert CONF_EXCLUDED_SLOTS not in entry.data


async def test_force_reissue_preview_does_not_create_pending_work(
    hass, monkeypatch
) -> None:
    """The public force-reissue dry run remains observational only."""
    entry, coordinator = _entry(hass, "reissue-owner")
    coordinator.lockname = None
    identity = make_reservation_fingerprint("reissue-owner", "Guest", _START, _END)
    coordinator._latest_res_by_key[identity] = object()
    entity_id = "sensor.reissue_owner_reservation"
    _sensor(hass, entry, entity_id, slot_number=20)

    allocator = object()
    get_or_create = AsyncMock(return_value=allocator)
    monkeypatch.setattr(
        "custom_components.rental_control.allocator.singleton.async_get_or_create_allocator",
        get_or_create,
    )
    preview = SimpleNamespace(
        reason=None,
        replacement_code="3141",
        replacement_code_ref="opaque-reference",
        origin=None,
        replaced_code_ref=None,
    )
    preview_request = object()
    preview_call = AsyncMock(return_value=(preview_request, preview))
    monkeypatch.setattr(
        "custom_components.rental_control.allocator.reissue_preview.preview_request_and_result",
        preview_call,
    )

    result = await reissue_service._handle_force_reissue_call(
        hass,
        _service_call(
            {
                "entity_id": entity_id,
                "force": False,
                "dry_run": True,
            }
        ),
    )

    assert result["status"] == "preview"
    assert result["dry_run"] is True
    get_or_create.assert_awaited_once_with(hass)
    assert preview_call.await_count == 1
    assert preview_call.await_args is not None
    assert preview_call.await_args.args[0] is allocator
    assert preview_call.await_args.args[1] is coordinator
    assert preview_call.await_args.args[2].entry_id == entry.entry_id
    assert not hasattr(coordinator, "_pending_reissues")
