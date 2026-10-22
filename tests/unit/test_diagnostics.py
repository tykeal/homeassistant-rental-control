# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the config entry diagnostics platform."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone as dt_timezone
import json
from types import SimpleNamespace
from typing import Any
from typing import cast

from homeassistant.components.diagnostics.const import REDACTED
from homeassistant.core import HomeAssistant
from homeassistant.helpers.json import ExtendedJSONEncoder
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rental_control.allocator.allocator import DoorCodeAllocator
from custom_components.rental_control.allocator.models import AllocationRequest
from custom_components.rental_control.const import ALLOCATOR
from custom_components.rental_control.const import COORDINATOR
from custom_components.rental_control.const import DOMAIN
from custom_components.rental_control.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.rental_control.reconciliation import ManagedSlot
from custom_components.rental_control.reconciliation import Reservation
from custom_components.rental_control.reconciliation import SlotStatus

SECRET_CODE = "987654"
GUEST_NAME = "Jane Q. Guest"
FEED_URL = "https://calendar.example.com/private-token-abc123/ical"


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    """Register a config entry with representative Rental Control options."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="entry-diag",
        unique_id="entry-diag",
        title="Beach House",
        data={
            "name": "Beach House",
            "url": FEED_URL,
            "timezone": "UTC",
            "checkin": "16:00",
            "checkout": "11:00",
            "days": 30,
            "refresh_frequency": 2,
            "code_length": 6,
            "code_generation": "date_based",
            "start_slot": 10,
            "max_events": 3,
            "keymaster_entry_id": "Front Door",
            "event_prefix": "RC",
            "excluded_slots": [{"slot": 11, "reason": "maintenance"}],
        },
        options={},
    )
    entry.add_to_hass(hass)
    return entry


def _reservation() -> Reservation:
    """Build a reservation holding the secret door code."""
    start = datetime(2026, 5, 1, 16, 0, tzinfo=dt_timezone.utc)
    end = datetime(2026, 5, 5, 11, 0, tzinfo=dt_timezone.utc)
    return Reservation(
        identity_key="identity-a",
        start=start,
        end=end,
        buffered_start=start,
        buffered_end=end,
        summary=f"{GUEST_NAME} - 4 nights",
        slot_name=GUEST_NAME,
        display_slot_name=f"RC {GUEST_NAME}",
        slot_code=SECRET_CODE,
        desired_slot=10,
        code_source="allocated",
    )


def _slots() -> list[ManagedSlot]:
    """Build observed slots including one carrying the secret code."""
    return [
        ManagedSlot(
            slot=10,
            managed=True,
            status=SlotStatus.OCCUPIED,
            actual_name=f"RC {GUEST_NAME}",
            actual_code=SECRET_CODE,
            actual_code_present=True,
            enabled=True,
            date_range_enabled=True,
            desired_identity_key="identity-a",
        ),
        ManagedSlot(slot=11, managed=True, status=SlotStatus.FREE),
        ManagedSlot(
            slot=12,
            managed=True,
            status=SlotStatus.UNKNOWN,
            blocked_reason="unreadable",
        ),
    ]


def _install_coordinator(hass: HomeAssistant, entry: MockConfigEntry) -> Any:
    """Install a coordinator stub exposing observations and reservations."""
    coordinator = SimpleNamespace(
        lockname="front_door",
        timezone="UTC",
        latest_reconciliation_diagnostics={
            "selected": {"identity-a": 10},
            "overflow": {},
        },
        _observe_managed_slots=_slots,
        _latest_res_by_key={"identity-a": _reservation()},
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {COORDINATOR: coordinator}
    return coordinator


async def _allocator(hass: HomeAssistant) -> DoorCodeAllocator:
    """Install an allocator holding the secret code for the entry."""
    allocator = DoorCodeAllocator(hass)
    allocator._store = cast(Any, SimpleNamespace(async_save=lambda _registry: None))
    await allocator.async_allocate(
        AllocationRequest(
            entry_id="entry-diag",
            identity_key="identity-a",
            preferred_code=SECRET_CODE,
            code_length=len(SECRET_CODE),
            lockname="front_door",
            slot=10,
        )
    )
    hass.data.setdefault(DOMAIN, {})[ALLOCATOR] = allocator
    return allocator


async def test_diagnostics_report_allocator_and_slot_state(
    hass: HomeAssistant,
) -> None:
    """The payload carries allocator, reconciliation and per-slot state."""
    entry = _entry(hass)
    _install_coordinator(hass, entry)
    await _allocator(hass)

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["allocator"]["record_count"] == 1
    assert result["reconciliation"]["selected"] == {"identity-a": 10}
    assert result["config"]["code_length"] == 6
    assert result["config"]["start_slot"] == 10
    assert result["config"]["max_events"] == 3
    assert result["config"]["code_generation"] == "date_based"
    assert result["config"]["lockname"] == "front_door"
    assert result["excluded_slots"] == [{"slot": 11, "reason": "maintenance"}]

    slots = {row["slot"]: row for row in result["slots"]}
    assert slots[10]["status"] == "occupied"
    assert slots[10]["code_present"] is True
    assert slots[10]["code_length"] == len(SECRET_CODE)
    assert slots[10]["readable"] is True
    assert slots[10]["excluded"] is False
    assert slots[11]["excluded"] is True
    assert slots[12]["readable"] is False
    assert slots[12]["blocked_reason"] == "unreadable"

    reservation = result["reservations"][0]
    assert reservation["identity_key"] == "identity-a"
    assert reservation["code_issued"] is True
    assert reservation["code_length"] == len(SECRET_CODE)
    assert reservation["code_source"] == "allocated"
    assert reservation["desired_slot"] == 10
    assert reservation["start"] == "2026-05-01T16:00:00+00:00"
    assert reservation["end"] == "2026-05-05T11:00:00+00:00"


async def test_diagnostics_never_leak_codes_or_guest_names(
    hass: HomeAssistant,
) -> None:
    """No door code or guest-identifying text reaches the payload."""
    entry = _entry(hass)
    _install_coordinator(hass, entry)
    await _allocator(hass)

    result = await async_get_config_entry_diagnostics(hass, entry)
    serialized = json.dumps(result, cls=ExtendedJSONEncoder)

    assert SECRET_CODE not in serialized
    assert GUEST_NAME not in serialized
    assert "private-token-abc123" not in serialized
    assert result["config"]["url"] == REDACTED
    assert "actual_code" not in serialized
    assert "slot_code" not in serialized


async def test_diagnostics_handle_missing_coordinator(hass: HomeAssistant) -> None:
    """A not-yet-loaded entry still produces a serializable payload."""
    entry = _entry(hass)

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["allocator"] is None
    assert result["reconciliation"] is None
    assert result["slots"] is None
    assert result["reservations"] is None
    assert result["entry"]["entry_id"] == entry.entry_id
    json.dumps(result, cls=ExtendedJSONEncoder)
