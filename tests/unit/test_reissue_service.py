# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the force-reissue service surface."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from typing import cast
from unittest.mock import AsyncMock

from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
import pytest
import voluptuous as vol

from custom_components.rental_control.allocator.allocator import DoorCodeAllocator
from custom_components.rental_control.allocator.models import AllocationOrigin
from custom_components.rental_control.allocator.models import AllocationRequest
from custom_components.rental_control.allocator.reissue_service import ATTR_DRY_RUN
from custom_components.rental_control.allocator.reissue_service import ATTR_FORCE
from custom_components.rental_control.allocator.reissue_service import ATTR_LOCKNAME
from custom_components.rental_control.allocator.reissue_service import ATTR_SLOT
from custom_components.rental_control.allocator.reissue_service import (
    FORCE_REISSUE_SCHEMA,
)
from custom_components.rental_control.allocator.reissue_service import (
    SERVICE_FORCE_REISSUE,
)
from custom_components.rental_control.allocator.services import (
    register_allocator_services,
)
from custom_components.rental_control.const import ALLOCATOR
from custom_components.rental_control.const import CHECKIN_SENSOR
from custom_components.rental_control.const import CHECKIN_STATE_CHECKED_IN
from custom_components.rental_control.const import COORDINATOR
from custom_components.rental_control.const import DOMAIN
from custom_components.rental_control.coordinator_helpers.models import (
    ReservationBuildContext,
)
from custom_components.rental_control.reconciliation import ManagedSlot
from custom_components.rental_control.reconciliation import Reservation
from custom_components.rental_control.reconciliation import SlotStatus
from custom_components.rental_control.reconciliation import make_reservation_fingerprint

_START = datetime(2026, 9, 17, 16, tzinfo=dt_util.UTC)
_END = _START + timedelta(days=3)


async def test_target_form_refusals(hass: HomeAssistant) -> None:
    """Both/neither/half target forms are refused by the handler."""
    coordinator = _install_service(hass)

    both = await _call(
        hass,
        {
            ATTR_ENTITY_ID: "sensor.guest",
            ATTR_LOCKNAME: "front",
            ATTR_SLOT: 10,
        },
    )
    neither = await _call(hass, {})
    half = await _call(hass, {ATTR_LOCKNAME: "front"})

    assert coordinator.async_request_refresh.await_count == 0
    assert both["reason"] == "ambiguous_target"
    assert neither["reason"] == "missing_target"
    assert half["reason"] == "incomplete_slot_target"


def test_schema_rejects_multiple_entity_ids() -> None:
    """The schema accepts only a single entity_id value."""
    with pytest.raises(vol.Invalid):
        FORCE_REISSUE_SCHEMA({ATTR_ENTITY_ID: ["sensor.one", "sensor.two"]})


async def test_unreadable_target_slot_refused(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UNKNOWN target slots are refused instead of treated as empty."""
    _install_service(hass, slot_status=SlotStatus.UNKNOWN, actual_code=None)
    monkeypatch.setattr(
        "custom_components.rental_control.allocator.reissue_preview."
        "observe_managed_slots_without_mutation",
        lambda _coordinator: [_slot(status=SlotStatus.UNKNOWN, code=None)],
    )

    response = await _call(hass, {ATTR_LOCKNAME: "front", ATTR_SLOT: 10})

    assert response == {
        "status": "refused",
        "reason": "slot_unreadable",
        "dry_run": False,
    }


async def test_checked_in_requires_force(hass: HomeAssistant) -> None:
    """Checked-in reservations require an explicit force flag."""
    coordinator = _install_service(hass, checked_in=True)

    refused = await _call(hass, {ATTR_ENTITY_ID: "sensor.guest"})
    accepted = await _call(hass, {ATTR_ENTITY_ID: "sensor.guest", ATTR_FORCE: True})

    assert refused["reason"] == "checked_in_requires_force"
    assert accepted["status"] == "accepted"
    assert accepted["forced_checked_in_override"] is True
    assert coordinator.async_request_refresh.await_count == 1


async def test_repeat_invocation_does_not_rotate_twice(
    hass: HomeAssistant,
) -> None:
    """A pending target returns the prior outcome on repeat."""
    coordinator = _install_service(hass)

    first = await _call(hass, {ATTR_ENTITY_ID: "sensor.guest"})
    second = await _call(hass, {ATTR_ENTITY_ID: "sensor.guest"})

    assert first["status"] == "accepted"
    assert second["status"] == "accepted"
    assert coordinator.async_request_refresh.await_count == 1


async def test_dry_run_after_pending_still_returns_preview(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dry-run after a real request is still a side-effect-free preview."""
    coordinator = _install_service(hass)
    monkeypatch.setattr(
        "custom_components.rental_control.coordinator_helpers.reservations."
        "generate_slot_code",
        lambda *_args: "5678",
    )

    await _call(hass, {ATTR_ENTITY_ID: "sensor.guest"})
    response = await _call(hass, {ATTR_ENTITY_ID: "sensor.guest", ATTR_DRY_RUN: True})

    assert response["status"] == "preview"
    assert response["dry_run"] is True
    assert response["replacement_code"] == "5678"
    assert coordinator.async_request_refresh.await_count == 1


async def test_non_dry_run_response_contains_only_code_refs(
    hass: HomeAssistant,
) -> None:
    """No non-dry-run response field equals a known raw code."""
    _install_service(hass)

    response = await _call(hass, {ATTR_ENTITY_ID: "sensor.guest"})

    for value in response.values():
        assert value not in {"1234", "5678"}


async def test_dry_run_is_side_effect_free(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dry-run preview returns a raw code and mutates no live state."""
    saves: list[object] = []
    coordinator = _install_service(hass, saves=saves)
    monkeypatch.setattr(
        "custom_components.rental_control.coordinator_helpers.reservations."
        "generate_slot_code",
        lambda *_args: "5678",
    )
    before_registry = deepcopy(coordinator.allocator._registry.records)
    before_mappings = deepcopy(coordinator._slot_mappings)
    before_observed = deepcopy(coordinator._observed_slot_codes)
    before_states = _states_snapshot(hass)

    response = await _call(hass, {ATTR_ENTITY_ID: "sensor.guest", ATTR_DRY_RUN: True})

    assert response["status"] == "preview"
    assert response["dry_run"] is True
    assert response["replacement_code"] == "5678"
    assert coordinator.allocator._registry.records == before_registry
    assert coordinator._slot_mappings == before_mappings
    assert coordinator._observed_slot_codes == before_observed
    assert _states_snapshot(hass) == before_states
    assert saves == []


async def test_collision_resolved_dry_run_reports_resolved_code(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preview reports the allocator's collision-resolved replacement."""
    coordinator = _install_service(hass, code_length=1, old_code="0")
    await _allocate(coordinator.allocator, "other", "other-id", "1", None, None)
    monkeypatch.setattr(
        "custom_components.rental_control.coordinator_helpers.reservations."
        "generate_slot_code",
        lambda *_args: "1",
    )

    response = await _call(hass, {ATTR_ENTITY_ID: "sensor.guest", ATTR_DRY_RUN: True})

    assert response["status"] == "preview"
    assert response["origin"] == "collision_resolved"
    assert response["replacement_code"] not in {"0", "1"}


async def test_exhausted_preview_refuses_up_front(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A full code space is refused without staging a pending request."""
    coordinator = _install_service(hass, code_length=1, old_code="0")
    for digit in range(1, 10):
        await _allocate(
            coordinator.allocator,
            f"entry-{digit}",
            f"identity-{digit}",
            str(digit),
            None,
            None,
        )
    monkeypatch.setattr(
        "custom_components.rental_control.coordinator_helpers.reservations."
        "generate_slot_code",
        lambda *_args: "1",
    )

    response = await _call(hass, {ATTR_ENTITY_ID: "sensor.guest"})

    assert response["reason"] == "exhausted"
    assert coordinator.async_request_refresh.await_count == 0


async def test_bare_ghost_slot_preview_is_clear_only(
    hass: HomeAssistant,
) -> None:
    """Bare slot previews return no replacement code."""
    _install_service(hass)

    response = await _call(
        hass, {ATTR_LOCKNAME: "front", ATTR_SLOT: 11, ATTR_DRY_RUN: True}
    )

    assert response["status"] == "preview"
    assert response["replacement_code"] is None
    assert response["replaced_disposition"] == "clear_only"


@dataclass(slots=True)
class ServiceHarness:
    """Test harness for the force-reissue service."""

    coordinator: Any
    allocator: DoorCodeAllocator
    async_request_refresh: AsyncMock

    def __getattr__(self, name: str) -> Any:
        """Delegate coordinator attributes for terse tests."""
        return getattr(self.coordinator, name)


def _install_service(
    hass: HomeAssistant,
    *,
    checked_in: bool = False,
    slot_status: SlotStatus = SlotStatus.OCCUPIED,
    actual_code: str | None = "1234",
    code_length: int = 4,
    old_code: str = "1234",
    saves: list[object] | None = None,
) -> ServiceHarness:
    """Install a fake coordinator and allocator for service tests."""
    identity = make_reservation_fingerprint("entry-a", "Guest", _START, _END)
    allocator = DoorCodeAllocator(hass)
    if saves is not None:
        allocator._store = cast(
            Any, SimpleNamespace(async_save=lambda registry: saves.append(registry))
        )
    else:
        allocator._store = cast(Any, SimpleNamespace(async_save=lambda _registry: None))
    coordinator = _coordinator(
        hass,
        identity,
        slot_status=slot_status,
        actual_code=actual_code,
        code_length=code_length,
    )
    coordinator.async_request_refresh = AsyncMock()
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][ALLOCATOR] = allocator
    hass.data[DOMAIN]["entry-a"] = {
        COORDINATOR: coordinator,
        CHECKIN_SENSOR: SimpleNamespace(
            state=CHECKIN_STATE_CHECKED_IN if checked_in else "awaiting_checkin",
            extra_state_attributes={
                "guest_name": "Guest",
                "start": _START,
                "end": _END,
            },
        ),
    }
    hass.states.async_set("sensor.guest", "Guest", _attrs())
    register_allocator_services(hass)
    coordinator.allocator = allocator
    allocator._registry.allocate(
        AllocationRequest(
            entry_id="entry-a",
            identity_key=identity,
            preferred_code=old_code,
            code_length=len(old_code),
            lockname="front",
            slot=10,
        ),
        old_code,
        AllocationOrigin.PREFERRED,
        allocator.code_ref(old_code),
        _START.isoformat(),
    )
    if saves is not None:
        saves.clear()
    return ServiceHarness(coordinator, allocator, coordinator.async_request_refresh)


async def _call(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Invoke the force-reissue service and return its response."""
    return cast(
        "dict[str, Any]",
        await hass.services.async_call(
            DOMAIN,
            SERVICE_FORCE_REISSUE,
            data,
            blocking=True,
            return_response=True,
        ),
    )


async def _allocate(
    allocator: DoorCodeAllocator,
    entry_id: str,
    identity_key: str,
    code: str,
    lockname: str | None,
    slot: int | None,
) -> None:
    """Allocate one code in the shared registry."""
    await allocator.async_allocate(
        AllocationRequest(
            entry_id=entry_id,
            identity_key=identity_key,
            preferred_code=code,
            code_length=len(code),
            lockname=lockname,
            slot=slot,
        )
    )


def _coordinator(
    hass: HomeAssistant,
    identity: str,
    *,
    slot_status: SlotStatus,
    actual_code: str | None,
    code_length: int,
) -> SimpleNamespace:
    """Return a fake coordinator with the preview surface."""
    event = SimpleNamespace(
        summary="Guest",
        description="",
        location=None,
        start=_START,
        end=_END,
        uid=None,
    )
    return SimpleNamespace(
        hass=hass,
        _entry_id="entry-a",
        data=[event],
        event_prefix="",
        timezone=dt_util.UTC,
        trim_names=False,
        max_name_length=20,
        code_buffer_before=0,
        code_buffer_after=0,
        should_update_code=True,
        code_generator="date_based",
        code_length=code_length,
        lockname="front",
        start_slot=10,
        max_events=3,
        event_overrides=SimpleNamespace(get_last_slot_error=lambda _slot: None),
        _latest_res_by_key={
            identity: Reservation(
                identity_key=identity,
                start=_START,
                end=_END,
                buffered_start=_START,
                buffered_end=_END,
                summary="Guest",
                slot_name="Guest",
                display_slot_name="Guest",
                slot_code=actual_code,
            )
        },
        _latest_plan=SimpleNamespace(selected={identity: 10}),
        _observed_slot_codes={identity: (10, actual_code)} if actual_code else {},
        _slot_mappings={
            "mappings": {
                identity: {
                    "slot": 10,
                    "published_once": True,
                    "fingerprint_history": [],
                }
            }
        },
        get_slot_assignment=lambda _identity: 10,
        _reservation_build_context=lambda: ReservationBuildContext(
            entry_id="entry-a",
            timezone=dt_util.UTC,
            event_prefix="",
            trim_names=False,
            max_name_length=20,
            code_buffer_before=0,
            code_buffer_after=0,
            should_update_code=True,
            code_generator="date_based",
            code_length=code_length,
            active_windows_for_name=lambda _slot_name: set(),
        ),
        _read_slot_snapshot=lambda slot: (
            SimpleNamespace(
                slot=slot,
                name_state="Guest" if slot == 10 else "",
                pin_state=actual_code if slot == 10 else "",
                use_date_range_state="off",
                enabled_state="on",
                start_state=None,
                end_state=None,
            )
            if slot_status is not SlotStatus.UNKNOWN
            else SimpleNamespace(
                slot=slot,
                name_state=None,
                pin_state=None,
                use_date_range_state=None,
                enabled_state=None,
                start_state=None,
                end_state=None,
            )
        ),
    )


def _attrs() -> dict[str, object]:
    """Return reservation sensor attributes."""
    return {
        "slot_name": "Guest",
        "slot_number": 10,
        "slot_code": "1234",
        "start": _START,
        "end": _END,
    }


def _slot(
    *,
    status: SlotStatus = SlotStatus.OCCUPIED,
    code: str | None = "1234",
) -> ManagedSlot:
    """Build a managed slot for patched observation tests."""
    return ManagedSlot(
        slot=10,
        managed=True,
        status=status,
        actual_name="Guest",
        actual_code=code,
        actual_code_present=code is not None,
    )


def _states_snapshot(hass: HomeAssistant) -> dict[str, tuple[str, dict[str, object]]]:
    """Return comparable Home Assistant state values for dry-run tests."""
    return {
        state.entity_id: (state.state, dict(state.attributes))
        for state in hass.states.async_all()
    }
