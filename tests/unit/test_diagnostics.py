# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the config entry diagnostics platform."""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta
from datetime import timezone as dt_timezone
import json
from types import SimpleNamespace
from typing import Any
from typing import cast

from freezegun import freeze_time
from homeassistant.components.diagnostics.const import REDACTED
from homeassistant.core import HomeAssistant
from homeassistant.helpers.json import ExtendedJSONEncoder
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rental_control import diagnostics as diagnostics_module
from custom_components.rental_control.allocator.allocator import DoorCodeAllocator
from custom_components.rental_control.allocator.candidates import candidate_codes
from custom_components.rental_control.allocator.models import AllocationRequest
from custom_components.rental_control.codegen import generate_date_based_code
from custom_components.rental_control.const import ALLOCATOR
from custom_components.rental_control.const import COORDINATOR
from custom_components.rental_control.const import DOMAIN
from custom_components.rental_control.coordinator_helpers.keymaster_observation import (
    classify_slot,
)
from custom_components.rental_control.coordinator_helpers.models import (
    KeymasterSlotSnapshot,
)
from custom_components.rental_control.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.rental_control.reconciliation import ManagedSlot
from custom_components.rental_control.reconciliation import Reservation

SECRET_CODE = "987654"
GUEST_NAME = "Jane Q. Guest"
FEED_URL = "https://calendar.example.com/private-token-abc123/ical"
BOOKING_REF = "HMABCDE123"
EVENT_UID = "uid-seed-for-static-random-987654"
FAILURE_TEXT = f"set_pin failed for {GUEST_NAME} with pin {SECRET_CODE}"


#: Tokens the fixed test salt produces for the fixture identities.  Real
#: downloads use a random salt, so a token could otherwise happen to
#: contain a literal this module asserts the absence of.
TOKEN_A = "identity-8d1859d7dc64"
TOKEN_B = "identity-34d1fbc93946"


@pytest.fixture(autouse=True)
def fixed_masker_salt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the diagnostics masker salt so tokens are deterministic."""
    monkeypatch.setattr(diagnostics_module.secrets, "token_hex", lambda _size: "0" * 32)


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
    """Classify real Keymaster snapshots, one carrying the secret code."""
    snapshots = [
        KeymasterSlotSnapshot(
            slot=10,
            name_state=f"RC {GUEST_NAME}",
            pin_state=SECRET_CODE,
            use_date_range_state="on",
            enabled_state="on",
            start_state="2026-05-01T16:00:00+00:00",
            end_state="2026-05-05T11:00:00+00:00",
        ),
        KeymasterSlotSnapshot(
            slot=11,
            name_state="",
            pin_state="",
            use_date_range_state="off",
            enabled_state="off",
        ),
        KeymasterSlotSnapshot(
            slot=12,
            name_state="unavailable",
            pin_state="unavailable",
        ),
    ]
    errors = {10: FAILURE_TEXT}
    return [
        classify_slot(snapshot, errors.get(snapshot.slot))[0] for snapshot in snapshots
    ]


def _reconciliation_snapshot() -> dict[str, Any]:
    """Build a production-shaped reconciliation snapshot with guest data."""
    return {
        "selected": {"identity-a": 10},
        "overflow": {},
        "slots": [
            {
                "slot": 10,
                "desired_identity_key": "identity-a",
                "actual_classification": "occupied",
                "action": "none",
                "last_error": FAILURE_TEXT,
            }
        ],
        "reservations": [
            {
                "identity_key": "identity-a",
                "summary": f"{GUEST_NAME} - 4 nights",
                "slot_name": GUEST_NAME,
                "uid_aliases": [EVENT_UID],
                "booking_aliases": [BOOKING_REF],
            }
        ],
        "event_overrides": {"last_slot_errors": {"10": FAILURE_TEXT}},
    }


def _install_coordinator(hass: HomeAssistant, entry: MockConfigEntry) -> Any:
    """Install a coordinator stub exposing observations and reservations."""
    coordinator = SimpleNamespace(
        lockname="front_door",
        timezone="UTC",
        latest_reconciliation_diagnostics=_reconciliation_snapshot(),
        latest_plan=SimpleNamespace(selected={"identity-a": 10}),
        event_overrides=SimpleNamespace(
            persisted_mappings={"identity-a": {"slot": 10}},
            _retry_counts={10: 2},
        ),
        _observe_managed_slots=_slots,
        _latest_res_by_key={"identity-a": _reservation()},
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {COORDINATOR: coordinator}
    return coordinator


async def _allocator(
    hass: HomeAssistant, entry_id: str = "entry-diag"
) -> DoorCodeAllocator:
    """Install an allocator holding the secret code for the entry."""
    allocator = DoorCodeAllocator(hass)
    allocator._store = cast(Any, SimpleNamespace(async_save=lambda _registry: None))
    # The entry is lock-backed, so the adoption gate holds it until a cycle
    # has observed the lock; stand in for that cycle to reach allocation.
    allocator._pending_adoption.discard(entry_id)
    allocator._adopted_locks[entry_id] = "front_door"
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
    token = result["reservations"][0]["identity_key"]
    assert token == TOKEN_A
    assert result["reconciliation"]["selected"] == {token: 10}
    assert result["config"]["code_length"] == 6
    assert result["config"]["start_slot"] == 10
    assert result["config"]["max_events"] == 3
    assert result["config"]["code_generation"] == "date_based"
    assert result["config"]["lockname"] == "front_door"
    assert result["excluded_slots"] == [{"slot": 11, "reason_present": True}]

    slots = {row["slot"]: row for row in result["slots"]}
    assert slots[10]["status"] == "occupied"
    assert slots[10]["code_present"] is True
    assert slots[10]["code_length"] == len(SECRET_CODE)
    assert slots[10]["readable"] is True
    assert slots[10]["excluded"] is False
    assert slots[11]["excluded"] is True
    assert slots[12]["readable"] is False
    assert slots[12]["blocked_reason"] == "unreadable"
    assert slots[10]["last_error_present"] is True
    assert slots[11]["last_error_present"] is False
    assert slots[10]["desired_identity_key"] == token
    assert slots[10]["persisted_identity_key"] == token
    assert slots[10]["retry_count"] == 2
    assert slots[11]["desired_identity_key"] is None
    assert slots[11]["retry_count"] == 0

    reservation = result["reservations"][0]
    assert reservation["identity_key"] == token
    assert reservation["code_issued"] is True
    assert reservation["code_length"] == len(SECRET_CODE)
    assert reservation["code_source"] == "allocated"
    assert reservation["desired_slot"] == 10
    assert reservation["window_set"] is True
    assert reservation["buffer_applied"] is False
    assert "start" not in reservation
    assert "end" not in reservation
    assert slots[10]["actual_window_set"] is True
    assert slots[10]["actual_window_matches_desired"] is True


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
    assert BOOKING_REF not in serialized
    assert EVENT_UID not in serialized
    assert "set_pin failed" not in serialized
    assert result["reconciliation"]["slots"][0]["last_error"] == REDACTED
    assert result["reconciliation"]["event_overrides"]["last_slot_errors"] == REDACTED
    assert result["reconciliation"]["reservations"][0]["uid_aliases"] == REDACTED
    assert result["reconciliation"]["reservations"][0]["booking_aliases"] == REDACTED
    assert "actual_code" not in serialized
    assert "slot_code" not in serialized
    assert "2026-05-01" not in serialized
    assert "identity-a" not in serialized


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


async def test_slot_rows_do_not_invent_identity_state(hass: HomeAssistant) -> None:
    """Without a plan or mappings, slot identities stay unknown."""
    entry = _entry(hass)
    coordinator = _install_coordinator(hass, entry)
    coordinator.latest_plan = None
    coordinator.event_overrides = None

    result = await async_get_config_entry_diagnostics(hass, entry)

    slots = {row["slot"]: row for row in result["slots"]}
    assert slots[10]["desired_identity_key"] is None
    assert slots[10]["persisted_identity_key"] is None
    assert slots[10]["retry_count"] == 0
    assert slots[10]["code_present"] is True


async def test_identity_keys_become_opaque_tokens(hass: HomeAssistant) -> None:
    """Identity keys are tokenized consistently so joins still work."""
    entry = _entry(hass)
    _install_coordinator(hass, entry)
    await _allocator(hass)

    result = await async_get_config_entry_diagnostics(hass, entry)

    token = result["reservations"][0]["identity_key"]
    assert token == TOKEN_A
    assert result["slots"][0]["desired_identity_key"] == token
    assert result["slots"][0]["persisted_identity_key"] == token
    assert result["allocator"]["records"][0]["owners"][0]["identity_key"] == token
    assert list(result["reconciliation"]["selected"]) == [token]


async def test_date_based_code_cannot_be_reconstructed(hass: HomeAssistant) -> None:
    """A genuinely date-generated code is not recoverable from the payload."""
    entry = _entry(hass)
    coordinator = _install_coordinator(hass, entry)
    reservation = coordinator._latest_res_by_key["identity-a"]
    generated = generate_date_based_code(6, reservation.start, reservation.end)
    reservation.slot_code = generated

    result = await async_get_config_entry_diagnostics(hass, entry)
    serialized = json.dumps(result, cls=ExtendedJSONEncoder)

    assert generated not in serialized
    assert reservation.start.strftime("%Y") not in serialized
    assert reservation.start.isoformat() not in serialized
    assert reservation.buffered_end.isoformat() not in serialized


async def test_collision_resolved_code_seed_is_not_exported(
    hass: HomeAssistant,
) -> None:
    """The collision-resolution seed never leaves with the payload."""
    entry = _entry(hass)
    _install_coordinator(hass, entry)
    allocator = await _allocator(hass)
    await allocator.async_allocate(
        AllocationRequest(
            entry_id="entry-diag",
            identity_key="identity-b",
            preferred_code=SECRET_CODE,
            code_length=len(SECRET_CODE),
            lockname="front_door",
            slot=11,
        )
    )
    collision_code = next(candidate_codes("identity-b", len(SECRET_CODE)))

    result = await async_get_config_entry_diagnostics(hass, entry)
    serialized = json.dumps(result, cls=ExtendedJSONEncoder)

    assert "identity-b" not in serialized
    assert TOKEN_B in serialized
    assert collision_code not in serialized
    assert len(result["allocator"]["records"]) == 2


async def test_exclusion_notes_are_redacted(hass: HomeAssistant) -> None:
    """A free-form exclusion note never reaches the payload."""
    note = f"Held for {GUEST_NAME}; temporary PIN {SECRET_CODE}"
    entry = _entry(hass)
    hass.config_entries.async_update_entry(
        entry,
        data={**entry.data, "excluded_slots": [{"slot": 11, "reason": note}]},
    )
    _install_coordinator(hass, entry)
    await _allocator(hass)

    result = await async_get_config_entry_diagnostics(hass, entry)
    serialized = json.dumps(result, cls=ExtendedJSONEncoder)

    assert note not in serialized
    assert GUEST_NAME not in serialized
    assert SECRET_CODE not in serialized
    assert result["excluded_slots"] == [{"slot": 11, "reason_present": True}]
    nested = result["allocator"]["excluded_slots"][entry.entry_id]
    assert nested == [{"slot": 11, "reason": REDACTED}]


def _one_night_coordinator(
    hass: HomeAssistant, entry: MockConfigEntry, start: datetime
) -> None:
    """Install a coordinator for a single active one-night stay."""
    end = start + timedelta(hours=19)
    reservation = Reservation(
        identity_key="identity-a",
        start=start,
        end=end,
        buffered_start=start,
        buffered_end=end,
        summary=GUEST_NAME,
        slot_name=GUEST_NAME,
        display_slot_name=f"RC {GUEST_NAME}",
        slot_code=generate_date_based_code(4, start, end),
        desired_slot=10,
        protected_active=True,
        code_source="allocated",
    )
    slot = classify_slot(
        KeymasterSlotSnapshot(
            slot=10,
            name_state=f"RC {GUEST_NAME}",
            pin_state=reservation.slot_code,
            use_date_range_state="on",
            enabled_state="on",
            start_state=start.isoformat(),
            end_state=end.isoformat(),
        ),
        None,
    )[0]
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        COORDINATOR: SimpleNamespace(
            lockname="front_door",
            timezone="UTC",
            latest_reconciliation_diagnostics={"selected": {"identity-a": 10}},
            latest_plan=SimpleNamespace(selected={"identity-a": 10}),
            event_overrides=SimpleNamespace(
                persisted_mappings={"identity-a": {"slot": 10}},
                _retry_counts={},
            ),
            _observe_managed_slots=lambda: [slot],
            _latest_res_by_key={"identity-a": reservation},
        )
    }


@freeze_time("2026-10-22T20:00:00+00:00")
async def test_active_one_night_stay_is_not_reconstructable(
    hass: HomeAssistant,
) -> None:
    """Two different active one-night stays produce the same timings."""
    october = datetime(2026, 10, 22, 16, 0, tzinfo=dt_timezone.utc)
    entry = _entry(hass)
    _one_night_coordinator(hass, entry, october)
    october_result = await async_get_config_entry_diagnostics(hass, entry)
    october_dump = json.dumps(october_result, cls=ExtendedJSONEncoder)

    _one_night_coordinator(
        hass, entry, datetime(2026, 11, 5, 16, 0, tzinfo=dt_timezone.utc)
    )
    november_result = await async_get_config_entry_diagnostics(hass, entry)

    assert generate_date_based_code(4, october, october + timedelta(hours=19)) == "2223"
    assert "2223" not in october_dump
    assert _without_identity(october_result["slots"][0]) == _without_identity(
        november_result["slots"][0]
    )
    assert _without_identity(october_result["reservations"][0]) == _without_identity(
        november_result["reservations"][0]
    )


def _without_identity(row: dict[str, Any]) -> dict[str, Any]:
    """Return a row stripped of its per-download identity tokens."""
    tokens = {"identity_key", "desired_identity_key", "persisted_identity_key"}
    return {key: value for key, value in row.items() if key not in tokens}
