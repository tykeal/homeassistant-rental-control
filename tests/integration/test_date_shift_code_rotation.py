# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""End-to-end coverage for date-shift door-code rotation (issue #801).

A date-based door code must regenerate when a guest moves their booking
window before check-in.  These tests drive the real pipeline - reservation
building, allocator cycle resolution, desired planning, and plan
application - and assert on the code actually commanded to the lock.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from typing import cast
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

from freezegun import freeze_time
from homeassistant.util import dt as dt_util
import pytest

from custom_components.rental_control.allocator.allocator import DoorCodeAllocator
from custom_components.rental_control.allocator.models import AdoptionRequest
from custom_components.rental_control.allocator.models import AllocationRequest
from custom_components.rental_control.codegen import generate_slot_code
from custom_components.rental_control.coordinator_helpers import code_allocation
from custom_components.rental_control.coordinator_helpers import reissue
from custom_components.rental_control.coordinator_helpers.models import (
    ReservationBuildContext,
)
from custom_components.rental_control.coordinator_helpers.reservations import (
    build_reservations,
)
from custom_components.rental_control.event_overrides import EventOverrides
from custom_components.rental_control.helpers import OperationResult
from custom_components.rental_control.reconciliation import ManagedSlot
from custom_components.rental_control.reconciliation import SlotStatus
from custom_components.rental_control.reconciliation import compute_desired_plan
from custom_components.rental_control.reconciliation import make_reservation_fingerprint

_ENTRY = "entry-a"
_LOCK = "front_door"
_CODE_LENGTH = 4
_BEFORE_CHECKIN = "2024-12-01 12:00:00+00:00"
_OLD_START = datetime(2024, 12, 25, 16, tzinfo=dt_util.UTC)
_OLD_END = datetime(2024, 12, 30, 11, tzinfo=dt_util.UTC)
_NEW_START = _OLD_START - timedelta(days=1)
_NEW_END = _OLD_END
_PEER_START = datetime(2025, 1, 5, 16, tzinfo=dt_util.UTC)
_PEER_END = datetime(2025, 1, 9, 11, tzinfo=dt_util.UTC)


@pytest.fixture(autouse=True)
def _freeze_before_checkin() -> Any:
    """Run every cycle well before any reservation starts."""
    with freeze_time(_BEFORE_CHECKIN):
        yield


_OLD_CODE = generate_slot_code(
    "date_based", _CODE_LENGTH, _OLD_START, _OLD_END, "", None
)
_NEW_CODE = generate_slot_code(
    "date_based", _CODE_LENGTH, _NEW_START, _NEW_END, "", None
)
_PEER_CODE = generate_slot_code(
    "date_based", _CODE_LENGTH, _PEER_START, _PEER_END, "", None
)
_MANUAL_PIN = "8642"


@pytest.mark.parametrize(
    ("should_update_code", "manual_pin", "expected_code", "expect_rotation"),
    [
        (True, None, _NEW_CODE, True),
        (False, None, _OLD_CODE, False),
        (True, _MANUAL_PIN, _MANUAL_PIN, False),
        (False, _MANUAL_PIN, _MANUAL_PIN, False),
    ],
    ids=["update-generated", "static-generated", "update-manual", "static-manual"],
)
async def test_date_shift_rotation_commands_expected_code(
    should_update_code: bool,
    manual_pin: str | None,
    expected_code: str,
    expect_rotation: bool,
) -> None:
    """Only a date-based code with should_update_code rotates on a date shift."""
    outcome = await _run_cycle(should_update_code, manual_pin)

    assert outcome["target_code"] == expected_code
    if expect_rotation:
        assert outcome["set_codes"] == [(1, _NEW_CODE)]
        assert outcome["update_times"] == []
        assert outcome["clears"] == [1]
    else:
        assert outcome["set_codes"] == []
        assert outcome["update_times"] == [(1, expected_code)]
        assert outcome["clears"] == []


@pytest.mark.parametrize("should_update_code", [True, False])
async def test_date_shift_rotation_leaves_peer_untouched(
    should_update_code: bool,
) -> None:
    """An unaffected reservation sees no clear, set, or update in the cycle."""
    outcome = await _run_cycle(should_update_code, None)

    assert outcome["peer_code"] == _PEER_CODE
    assert 2 not in outcome["clears"]
    assert all(slot != 2 for slot, _code in outcome["set_codes"])
    assert all(slot != 2 for slot, _code in outcome["update_times"])


async def _run_cycle(
    should_update_code: bool,
    manual_pin: str | None,
    new_start: datetime = _NEW_START,
    new_end: datetime = _NEW_END,
) -> dict[str, Any]:
    """Run one full refresh cycle and capture the commands sent to the lock."""
    observed_code = manual_pin or _OLD_CODE
    old_identity = make_reservation_fingerprint(
        _ENTRY, "Target Guest", _OLD_START, _OLD_END
    )
    peer_identity = make_reservation_fingerprint(
        _ENTRY, "Peer Guest", _PEER_START, _PEER_END
    )
    allocator = _allocator()
    for identity, code, slot in (
        (old_identity, observed_code, 1),
        (peer_identity, _PEER_CODE, 2),
    ):
        await allocator.async_adopt(
            AdoptionRequest(
                entry_id=_ENTRY,
                identity_key=identity,
                code=code,
                code_length=_CODE_LENGTH,
                lockname=_LOCK,
                slot=slot,
            )
        )

    managed_slots = [
        _slot(1, observed_code, "RC Target Guest", _OLD_START, _OLD_END, old_identity),
        _slot(2, _PEER_CODE, "RC Peer Guest", _PEER_START, _PEER_END, peer_identity),
    ]
    reservations = build_reservations(
        [
            _event("Target Guest", new_start, new_end),
            _event("Peer Guest", _PEER_START, _PEER_END),
        ],
        managed_slots,
        _ctx(should_update_code),
    )
    target = next(item for item in reservations if item.slot_name == "Target Guest")
    peer = next(item for item in reservations if item.slot_name == "Peer Guest")
    # The date shift remaps the persisted mapping onto the new fingerprint.
    target.fingerprint_history.add(old_identity)

    with patch.object(code_allocation, "get_allocator", return_value=allocator):
        await code_allocation.async_resolve_codes(
            reissue.CodeResolutionRequest(
                hass=cast(Any, SimpleNamespace()),
                entry_id=_ENTRY,
                lockname=_LOCK,
                code_length=_CODE_LENGTH,
                managed_slots=managed_slots,
                reservations=reservations,
            )
        )

    plan = compute_desired_plan(
        reservations=reservations,
        managed_slots=managed_slots,
        max_events=2,
        plan_id="plan-801",
        generated_at=_OLD_START,
        entry_id=_ENTRY,
        lockname=_LOCK,
        start_slot=1,
    )
    commands = await _apply(plan, reservations, observed_code)
    commands["target"] = target
    commands["target_code"] = target.slot_code
    commands["peer_code"] = peer.slot_code
    return commands


async def _apply(
    plan: Any, reservations: list[Any], observed_code: str
) -> dict[str, Any]:
    """Apply a plan against mocked Keymaster services and record commands."""
    overrides = EventOverrides(start_slot=1, max_slots=2)
    overrides.update(1, observed_code, "RC Target Guest", _OLD_START, _OLD_END)
    overrides.update(2, _PEER_CODE, "RC Peer Guest", _PEER_START, _PEER_END)
    physical = {
        1: ["RC Target Guest", observed_code],
        2: ["RC Peer Guest", _PEER_CODE],
    }

    def _state(entity_id: str) -> Any:
        """Return the fake Keymaster text state for one slot entity."""
        for slot, (name, code) in physical.items():
            if entity_id == f"text.{_LOCK}_code_slot_{slot}_name":
                return SimpleNamespace(state=name)
            if entity_id == f"text.{_LOCK}_code_slot_{slot}_pin":
                return SimpleNamespace(state=code)
        return None

    coordinator = MagicMock()
    coordinator.lockname = _LOCK
    coordinator.hass.states.get.side_effect = _state

    async def _clear(_coordinator: Any, slot: int, **_kwargs: Any) -> OperationResult:
        """Confirm a clear so the replacement set can proceed."""
        physical[slot] = ["", ""]
        overrides.update(slot, None, None, None, None)
        return OperationResult(kind="clear", slot=slot, confirmed=True)

    with (
        patch(
            "custom_components.rental_control.event_overrides.async_fire_clear_code",
            side_effect=_clear,
        ) as clear_code,
        patch(
            "custom_components.rental_control.event_overrides.async_fire_set_code",
            new_callable=AsyncMock,
        ) as set_code,
        patch(
            "custom_components.rental_control.event_overrides.async_fire_update_times",
            new_callable=AsyncMock,
        ) as update_times,
    ):
        set_code.side_effect = lambda _c, _e, slot: OperationResult(
            kind="set", slot=slot, confirmed=True
        )
        update_times.side_effect = lambda _c, _e, slot: OperationResult(
            kind="update_times", slot=slot, confirmed=True
        )
        await overrides.async_apply_plan(
            coordinator,
            plan,
            {item.identity_key: item for item in reservations},
        )

    return {
        "clears": [call.args[1] for call in clear_code.call_args_list],
        "set_codes": [_command(call) for call in set_code.call_args_list],
        "update_times": [_command(call) for call in update_times.call_args_list],
    }


def _command(call: Any) -> tuple[int, str]:
    """Return the (slot, code) actually commanded to the lock."""
    return call.args[2], call.args[1].extra_state_attributes["slot_code"]


def _allocator() -> DoorCodeAllocator:
    """Build an allocator with persistence disabled."""
    allocator = DoorCodeAllocator(
        cast(
            Any,
            SimpleNamespace(
                data={},
                config=SimpleNamespace(config_dir="."),
                config_entries=SimpleNamespace(async_entries=lambda _domain=None: []),
            ),
        )
    )
    allocator._store = cast(Any, SimpleNamespace(async_save=lambda _registry: None))
    return allocator


def _ctx(should_update_code: bool) -> ReservationBuildContext:
    """Build a reservation context for the date-based generator."""
    return ReservationBuildContext(
        entry_id=_ENTRY,
        timezone=dt_util.UTC,
        event_prefix="RC",
        trim_names=False,
        max_name_length=20,
        code_buffer_before=0,
        code_buffer_after=0,
        should_update_code=should_update_code,
        code_generator="date_based",
        code_length=_CODE_LENGTH,
        active_windows_for_name=lambda _name: set(),
        reissue_suppression=reissue.ReissueSuppression(),
    )


def _event(name: str, start: datetime, end: datetime) -> Any:
    """Build a calendar-like event."""
    return SimpleNamespace(summary=name, description="", start=start, end=end, uid=None)


def _slot(
    number: int,
    code: str,
    name: str,
    start: datetime,
    end: datetime,
    identity: str,
) -> ManagedSlot:
    """Build one occupied managed slot."""
    return ManagedSlot(
        slot=number,
        managed=True,
        status=SlotStatus.OCCUPIED,
        actual_name=name,
        actual_code=code,
        actual_code_present=True,
        actual_start=start,
        actual_end=end,
        persisted_identity_key=identity,
    )


async def test_missing_registry_keeps_code_and_completes_recovery() -> None:
    """A lost registry keeps the observed PIN owned instead of rotating it.

    Rotation is given up rather than retried: the observed code is
    re-adopted so the physically programmed PIN cannot be handed to
    another booking, and the cycle completes registry recovery instead
    of fail-closing a previously published booking.  The pre-existing
    code is retained, matching the behaviour before this fix.
    """
    old_identity = make_reservation_fingerprint(
        _ENTRY, "Target Guest", _OLD_START, _OLD_END
    )
    allocator = _allocator()
    allocator._registry_lost = True
    allocator._registry_missing = True

    managed_slots = [
        _slot(1, _OLD_CODE, "RC Target Guest", _OLD_START, _OLD_END, old_identity),
    ]
    reservations = build_reservations(
        [_event("Target Guest", _NEW_START, _NEW_END)],
        managed_slots,
        _ctx(True),
    )
    target = reservations[0]
    target.fingerprint_history.add(old_identity)
    target.published_once = True

    with patch.object(code_allocation, "get_allocator", return_value=allocator):
        await code_allocation.async_resolve_codes(
            reissue.CodeResolutionRequest(
                hass=cast(Any, SimpleNamespace()),
                entry_id=_ENTRY,
                lockname=_LOCK,
                code_length=_CODE_LENGTH,
                managed_slots=managed_slots,
                reservations=reservations,
            )
        )

    # The still-programmed PIN is reconstructed as an owned allocation, so a
    # second, unpublished booking cannot be handed the same code.
    intruder = await allocator.async_allocate(
        AllocationRequest(
            entry_id="entry-b",
            identity_key="identity-b",
            preferred_code=_OLD_CODE,
            code_length=_CODE_LENGTH,
            lockname="back_door",
            slot=1,
        )
    )

    assert target.code_rotation_pending is False
    assert target.slot_code == _OLD_CODE
    assert target.code_source == "adopted"
    assert intruder.code is not None
    assert intruder.code != _OLD_CODE
    assert allocator._registry_lost is False

    # Recovery completed in the same cycle because the reconstructed adoption
    # satisfied the issuance gate, so no further refresh is fail-closed.  The
    # code is not rotated later: adoption bound it to the shifted identity.
    followup = build_reservations(
        [_event("Target Guest", _NEW_START, _NEW_END)],
        managed_slots,
        _ctx(True),
    )
    followup[0].fingerprint_history.add(old_identity)
    followup[0].published_once = True

    with patch.object(code_allocation, "get_allocator", return_value=allocator):
        await code_allocation.async_resolve_codes(
            reissue.CodeResolutionRequest(
                hass=cast(Any, SimpleNamespace()),
                entry_id=_ENTRY,
                lockname=_LOCK,
                code_length=_CODE_LENGTH,
                managed_slots=managed_slots,
                reservations=followup,
            )
        )

    assert followup[0].slot_code == _OLD_CODE
    assert allocator._registry_lost is False


async def test_second_entry_recovery_keeps_unowned_pin_reserved() -> None:
    """A later entry in a recovery pass still protects its unowned PIN.

    ``resolve_cycle`` clears the registry-lost flag as soon as the first
    entry completes its adoption gate, so a second, date-shifted entry
    sees a healthy allocator while its own physical code is still
    absent from the registry.  Rotation must give way to adoption there,
    otherwise a failed replacement write would leave the programmed code
    unreserved and issuable to another booking.
    """
    allocator = _allocator()
    allocator._registry_lost = True
    allocator._registry_missing = True

    # Entry A recovers first and clears the shared registry-lost flag.
    first_identity = make_reservation_fingerprint(
        "entry-first", "First Guest", _PEER_START, _PEER_END
    )
    first_slots = [
        _slot(1, _PEER_CODE, "RC First Guest", _PEER_START, _PEER_END, first_identity)
    ]
    first_reservations = build_reservations(
        [_event("First Guest", _PEER_START, _PEER_END)], first_slots, _ctx(True)
    )
    with patch.object(code_allocation, "get_allocator", return_value=allocator):
        await code_allocation.async_resolve_codes(
            reissue.CodeResolutionRequest(
                hass=cast(Any, SimpleNamespace()),
                entry_id="entry-first",
                lockname="first_door",
                code_length=_CODE_LENGTH,
                managed_slots=first_slots,
                reservations=first_reservations,
            )
        )
    assert allocator._registry_lost is False

    # Entry B is date-shifted and its programmed code is still unowned.
    old_identity = make_reservation_fingerprint(
        _ENTRY, "Target Guest", _OLD_START, _OLD_END
    )
    second_slots = [
        _slot(1, _OLD_CODE, "RC Target Guest", _OLD_START, _OLD_END, old_identity)
    ]
    second_reservations = build_reservations(
        [_event("Target Guest", _NEW_START, _NEW_END)], second_slots, _ctx(True)
    )
    target = second_reservations[0]
    target.fingerprint_history.add(old_identity)
    with patch.object(code_allocation, "get_allocator", return_value=allocator):
        await code_allocation.async_resolve_codes(
            reissue.CodeResolutionRequest(
                hass=cast(Any, SimpleNamespace()),
                entry_id=_ENTRY,
                lockname=_LOCK,
                code_length=_CODE_LENGTH,
                managed_slots=second_slots,
                reservations=second_reservations,
            )
        )

    # Simulate a failed replacement write: the old code is still programmed.
    intruder = await allocator.async_allocate(
        AllocationRequest(
            entry_id="entry-c",
            identity_key="identity-c",
            preferred_code=_OLD_CODE,
            code_length=_CODE_LENGTH,
            lockname="other_door",
            slot=9,
        )
    )

    assert target.code_rotation_pending is False
    assert target.slot_code == _OLD_CODE
    assert intruder.code is not None
    assert intruder.code != _OLD_CODE


async def test_rotation_requires_registry_owned_observed_code() -> None:
    """Rotation proceeds once the registry owns the programmed code."""
    old_identity = make_reservation_fingerprint(
        _ENTRY, "Target Guest", _OLD_START, _OLD_END
    )
    allocator = _allocator()
    await allocator.async_adopt(
        AdoptionRequest(
            entry_id=_ENTRY,
            identity_key=old_identity,
            code=_OLD_CODE,
            code_length=_CODE_LENGTH,
            lockname=_LOCK,
            slot=1,
        )
    )
    managed_slots = [
        _slot(1, _OLD_CODE, "RC Target Guest", _OLD_START, _OLD_END, old_identity)
    ]
    reservations = build_reservations(
        [_event("Target Guest", _NEW_START, _NEW_END)], managed_slots, _ctx(True)
    )
    reservations[0].fingerprint_history.add(old_identity)

    with patch.object(code_allocation, "get_allocator", return_value=allocator):
        await code_allocation.async_resolve_codes(
            reissue.CodeResolutionRequest(
                hass=cast(Any, SimpleNamespace()),
                entry_id=_ENTRY,
                lockname=_LOCK,
                code_length=_CODE_LENGTH,
                managed_slots=managed_slots,
                reservations=reservations,
            )
        )

    # The replaced code stays reserved by its stale owner while it is still
    # physically programmed, so no other booking can be issued it.
    intruder = await allocator.async_allocate(
        AllocationRequest(
            entry_id="entry-c",
            identity_key="identity-c",
            preferred_code=_OLD_CODE,
            code_length=_CODE_LENGTH,
            lockname="other_door",
            slot=9,
        )
    )

    assert reservations[0].slot_code == _NEW_CODE
    assert reservations[0].code_source == "allocated"
    assert intruder.code is not None
    assert intruder.code != _OLD_CODE


@pytest.mark.asyncio
async def test_started_stay_keeps_code_and_updates_dates_only() -> None:
    """Extending checkout mid-stay must not replace the working PIN.

    ``should_update_code`` only promises to follow date changes on future
    events, so a stay that has already begun keeps its programmed code and
    receives a dates-only update.
    """
    with freeze_time(_OLD_START + timedelta(days=1)):
        result = await _run_cycle(
            should_update_code=True,
            manual_pin=None,
            new_start=_OLD_START,
            new_end=_OLD_END + timedelta(days=2),
        )

    assert result["target"].code_rotation_pending is False
    assert result["target_code"] == _OLD_CODE
    assert result["set_codes"] == []
    assert result["clears"] == []
    assert result["update_times"] == [(1, _OLD_CODE)]
