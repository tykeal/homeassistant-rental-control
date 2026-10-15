# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the set_slot_exclusion service surface."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from typing import cast

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import voluptuous as vol

from custom_components.rental_control.allocator import exclusion_service
from custom_components.rental_control.allocator import reissue_preview
from custom_components.rental_control.allocator.allocator import DoorCodeAllocator
from custom_components.rental_control.allocator.exclusion_service import (
    SERVICE_SET_SLOT_EXCLUSION,
)
from custom_components.rental_control.allocator.exclusion_service import (
    SET_SLOT_EXCLUSION_SCHEMA,
)
from custom_components.rental_control.allocator.models import AllocationOrigin
from custom_components.rental_control.allocator.models import AllocationOwner
from custom_components.rental_control.allocator.models import AllocationRecord
from custom_components.rental_control.allocator.services import (
    register_allocator_services,
)
from custom_components.rental_control.const import ALLOCATOR
from custom_components.rental_control.const import CONF_EXCLUDED_SLOTS
from custom_components.rental_control.const import COORDINATOR
from custom_components.rental_control.const import DOMAIN
from custom_components.rental_control.coordinator_helpers import slot_exclusions
from custom_components.rental_control.reconciliation import ManagedSlot
from custom_components.rental_control.reconciliation import SlotStatus

_START_SLOT = 10
_MAX_EVENTS = 3
_DEAD_SLOT = 11
_NOW = "2026-10-15T12:00:00+00:00"


def _managed(slot: int, status: SlotStatus, code: str | None) -> ManagedSlot:
    """Build one observed managed slot."""
    return ManagedSlot(slot=slot, managed=True, status=status, actual_code=code)


def _default_slots() -> list[ManagedSlot]:
    """Observe one dead slot alongside two healthy ones."""
    return [
        _managed(10, SlotStatus.FREE, None),
        _managed(_DEAD_SLOT, SlotStatus.UNKNOWN, None),
        _managed(12, SlotStatus.FREE, None),
    ]


async def _install(
    hass: HomeAssistant,
    *,
    slots: list[ManagedSlot] | None = None,
    stored: list[dict[str, Any]] | None = None,
    claim_slot: int | None = None,
    monkeypatch: pytest.MonkeyPatch,
) -> MockConfigEntry:
    """Install a coordinator, allocator, and config entry for the service."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="entry-a",
        data={"name": "Entry A"}
        | ({CONF_EXCLUDED_SLOTS: stored} if stored is not None else {}),
        options={},
    )
    entry.add_to_hass(hass)
    allocator = DoorCodeAllocator(hass)
    allocator._store = cast(Any, SimpleNamespace(async_save=lambda _registry: None))
    if claim_slot is not None:
        allocator._registry.records["1234"] = AllocationRecord(
            code="1234",
            code_ref="ref-1234",
            encoding_salt_value="entry-a",
            owners=[
                AllocationOwner(
                    entry_id="entry-a",
                    identity_key="identity-a",
                    origin=AllocationOrigin.ADOPTED,
                    lockname="front",
                    slot=claim_slot,
                    lock_observed=True,
                    first_seen=_NOW,
                    last_seen=_NOW,
                )
            ],
            code_length=4,
        )
        allocator._registry.rebuild_index()
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][ALLOCATOR] = allocator
    hass.data[DOMAIN]["entry-a"] = {
        COORDINATOR: SimpleNamespace(
            lockname="front", start_slot=_START_SLOT, max_events=_MAX_EVENTS
        )
    }
    monkeypatch.setattr(
        reissue_preview,
        "observe_managed_slots_without_mutation",
        lambda _coordinator: slots if slots is not None else _default_slots(),
    )
    register_allocator_services(hass)
    return entry


async def _call(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Invoke the service and return its response."""
    response = await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_SLOT_EXCLUSION,
        data,
        blocking=True,
        return_response=True,
    )
    return cast(dict[str, Any], response)


def test_schema_has_no_force_override() -> None:
    """The three guards must have no escape hatch."""
    assert "force" not in {str(key) for key in SET_SLOT_EXCLUSION_SCHEMA.schema}


def test_schema_requires_a_scalar_slot() -> None:
    """Bulk exclusion is unrepresentable in the schema."""
    with pytest.raises(vol.Invalid):
        SET_SLOT_EXCLUSION_SCHEMA({"lockname": "front"})
    with pytest.raises(vol.Invalid):
        SET_SLOT_EXCLUSION_SCHEMA({"lockname": "front", "slot": [1, 2]})
    with pytest.raises(vol.Invalid):
        SET_SLOT_EXCLUSION_SCHEMA({"lockname": "front", "slot": True})


async def test_excluding_a_dead_slot_persists_it(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreadable, unclaimed, in-range slot may be excluded."""
    entry = await _install(hass, monkeypatch=monkeypatch)

    response = await _call(
        hass, {"lockname": "front", "slot": _DEAD_SLOT, "reason": "dead relay"}
    )

    assert response == {
        "status": "ok",
        "entry_id": "entry-a",
        "lockname": "front",
        "slot": _DEAD_SLOT,
        "excluded": True,
        "changed": True,
        "excluded_slots": [_DEAD_SLOT],
        "dry_run": False,
    }
    assert entry.data[CONF_EXCLUDED_SLOTS] == [
        {"slot": _DEAD_SLOT, "reason": "dead relay"}
    ]


async def test_repeating_an_exclusion_reports_no_change(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A repeated identical request writes nothing."""
    await _install(
        hass,
        stored=[{"slot": _DEAD_SLOT, "reason": "dead relay"}],
        monkeypatch=monkeypatch,
    )

    response = await _call(
        hass, {"lockname": "front", "slot": _DEAD_SLOT, "reason": "dead relay"}
    )

    assert response["status"] == "ok"
    assert response["changed"] is False


async def test_readable_slot_is_refused(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A readable slot cannot be excluded, because it cannot be freezing."""
    entry = await _install(hass, monkeypatch=monkeypatch)

    response = await _call(hass, {"lockname": "front", "slot": 10})

    assert response["status"] == "refused"
    assert response["reason"] == "slot_readable"
    assert response["changed"] is False
    assert CONF_EXCLUDED_SLOTS not in entry.data


async def test_claimed_slot_is_refused(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A slot already accounted for by an owner is not causing the freeze."""
    entry = await _install(hass, claim_slot=_DEAD_SLOT, monkeypatch=monkeypatch)

    response = await _call(hass, {"lockname": "front", "slot": _DEAD_SLOT})

    assert response["reason"] == "slot_claimed"
    assert CONF_EXCLUDED_SLOTS not in entry.data


async def test_out_of_range_slot_is_refused(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A slot outside the entry's managed range cannot be excluded."""
    entry = await _install(hass, monkeypatch=monkeypatch)

    response = await _call(hass, {"lockname": "front", "slot": 99})

    assert response["reason"] == "slot_out_of_range"
    assert CONF_EXCLUDED_SLOTS not in entry.data


@pytest.mark.parametrize("slot", [10, 99])
async def test_clearing_skips_every_guard(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, slot: int
) -> None:
    """Restoring a slot is always permitted, whatever its current state."""
    await _install(
        hass, stored=[{"slot": slot}, {"slot": _DEAD_SLOT}], monkeypatch=monkeypatch
    )

    response = await _call(hass, {"lockname": "front", "slot": slot, "excluded": False})

    assert response["status"] == "ok"
    assert response["changed"] is True
    assert response["excluded_slots"] == [_DEAD_SLOT]


async def test_clearing_a_claimed_slot_is_permitted(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The claim guard never blocks a return to fail-closed behaviour."""
    await _install(
        hass,
        stored=[{"slot": _DEAD_SLOT}],
        claim_slot=_DEAD_SLOT,
        monkeypatch=monkeypatch,
    )

    response = await _call(
        hass, {"lockname": "front", "slot": _DEAD_SLOT, "excluded": False}
    )

    assert response["status"] == "ok"
    assert response["excluded_slots"] == []


async def test_repeated_clear_reports_no_change(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Clearing a slot that was never excluded writes nothing."""
    entry = await _install(hass, monkeypatch=monkeypatch)

    response = await _call(
        hass, {"lockname": "front", "slot": _DEAD_SLOT, "excluded": False}
    )

    assert response["status"] == "ok"
    assert response["changed"] is False
    assert CONF_EXCLUDED_SLOTS not in entry.data


async def test_dry_run_reports_the_result_without_writing(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dry run reports what would happen and persists nothing."""
    entry = await _install(hass, monkeypatch=monkeypatch)
    writes: list[Any] = []
    monkeypatch.setattr(
        slot_exclusions,
        "persist_excluded_slots",
        lambda *args, **kwargs: writes.append(args),
    )

    response = await _call(
        hass, {"lockname": "front", "slot": _DEAD_SLOT, "dry_run": True}
    )

    assert response["status"] == "ok"
    assert response["dry_run"] is True
    assert response["changed"] is True
    assert response["excluded_slots"] == [_DEAD_SLOT]
    assert writes == []
    assert CONF_EXCLUDED_SLOTS not in entry.data


async def test_dry_run_reports_the_refusal_that_would_apply(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dry run surfaces a guard without tripping it."""
    await _install(hass, monkeypatch=monkeypatch)

    response = await _call(hass, {"lockname": "front", "slot": 10, "dry_run": True})

    assert response["reason"] == "slot_readable"
    assert response["dry_run"] is True


async def test_unknown_lock_is_refused_without_an_entry(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unresolvable target reports null identifiers."""
    await _install(hass, monkeypatch=monkeypatch)

    response = await _call(hass, {"lockname": "back", "slot": _DEAD_SLOT})

    assert response["status"] == "refused"
    assert response["reason"] == "unknown_lock"
    assert response["entry_id"] is None


async def test_response_carries_no_door_code(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No response field may ever carry a door code."""
    await _install(hass, claim_slot=_DEAD_SLOT, monkeypatch=monkeypatch)

    response = await _call(hass, {"lockname": "front", "slot": _DEAD_SLOT})

    assert "1234" not in str(response)


def test_service_module_exports_its_public_surface() -> None:
    """The service module advertises only its intended entry points."""
    assert exclusion_service.__all__ == [
        "SERVICE_SET_SLOT_EXCLUSION",
        "SET_SLOT_EXCLUSION_SCHEMA",
        "ExclusionRequest",
        "register_set_slot_exclusion_service",
    ]
