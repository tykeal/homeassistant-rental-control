# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for early-checkout expiry retry behavior."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from typing import cast

from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
import pytest

from custom_components.rental_control import util
from custom_components.rental_control.const import CHECKIN_STATE_CHECKED_IN
from custom_components.rental_control.const import CHECKIN_STATE_CHECKED_OUT
from custom_components.rental_control.const import EARLY_CHECKOUT_EXPIRY_SWITCH
from custom_components.rental_control.sensors import checkinsensor
from custom_components.rental_control.sensors.checkin import checkout_expiry
from custom_components.rental_control.sensors.checkin import checkout_expiry_guards
from custom_components.rental_control.sensors.checkin import checkout_expiry_retry
from custom_components.rental_control.sensors.checkin import runtime
from custom_components.rental_control.sensors.checkin.models import CheckinStateSnapshot
from custom_components.rental_control.sensors.checkin.persistence import (
    CheckinExtraStoredData,
)


class _Overrides:
    """Minimal current event-slot mapping for the retry guard."""

    def __init__(self, slot_name: str, start: datetime, end: datetime) -> None:
        """Create a single occupied slot."""
        self.ready = True
        self.overrides = {
            10: {
                "slot_name": slot_name,
                "start_time": start,
                "end_time": end,
                "slot_code": "123456",
            }
        }

    def get_slot_key_by_name(self, slot_name: str) -> int:
        """Return the matching managed slot, if any."""
        return 10 if self.overrides[10]["slot_name"] == slot_name else 0

    def get_slot_name(self, slot: int) -> str:
        """Return the occupant name for the requested slot."""
        if slot not in self.overrides:
            return ""
        return cast(str, self.overrides[slot]["slot_name"])


class _Notifications:
    """Capture persistent notification create/dismiss calls."""

    def __init__(self) -> None:
        """Initialize call histories."""
        self.created: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.dismissed: list[str] = []

    def create(self, *args: Any, **kwargs: Any) -> None:
        """Record notification creation."""
        self.created.append((args, kwargs))

    def dismiss(self, _hass: Any, notification_id: str) -> None:
        """Record notification dismissal."""
        self.dismissed.append(notification_id)


def _entity(hass: Any, start: datetime, original_end: datetime) -> SimpleNamespace:
    """Build the small entity/coordinator shape used by runtime helpers."""
    overrides = _Overrides("Guest", start, original_end)
    coordinator = SimpleNamespace(
        event_overrides=overrides,
        start_slot=10,
        max_events=1,
        lockname="front_door",
    )
    entity = SimpleNamespace(
        _hass=hass,
        _config_entry=SimpleNamespace(entry_id="entry-964"),
        coordinator=coordinator,
        unique_id="checkin-entity",
        entity_id="sensor.rental_checkin",
        _state=CHECKIN_STATE_CHECKED_IN,
        _tracked_event_start=start,
        _tracked_event_end=original_end,
        _tracked_event_slot_name="Guest",
        _tracked_event_summary="Reserved - Guest",
        _early_expiry_original_end=None,
        _early_expiry_new_end=None,
        _early_expiry_retry_attempt=0,
        _early_expiry_retry_slot=None,
        _early_expiry_retry_handle=None,
        _early_expiry_retry_task=None,
        _early_expiry_retry_generation=0,
        async_write_ha_state=lambda: None,
    )
    return entity


def _patch_checkout_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[_Notifications, list[dict[str, Any]]]:
    """Patch routing and timer dependencies to capture retry scheduling."""
    notices = _Notifications()
    timers: list[dict[str, Any]] = []

    def track(_hass: Any, callback: Any, when: datetime) -> Any:
        """Record one scheduled callback and return its cancellation handle."""
        timer = {"callback": callback, "when": when, "cancelled": False}
        timers.append(timer)
        return lambda: timer.__setitem__("cancelled", True)

    monkeypatch.setattr(
        checkout_expiry, "association_requires_verified_routing", lambda *_: False
    )
    monkeypatch.setattr(
        checkout_expiry_guards,
        "association_requires_verified_routing",
        lambda *_: False,
    )
    monkeypatch.setattr(
        checkout_expiry_retry,
        "association_requires_verified_routing",
        lambda *_: False,
    )
    monkeypatch.setattr(checkout_expiry_retry, "async_create", notices.create)
    monkeypatch.setattr(checkout_expiry_retry, "async_dismiss", notices.dismiss)
    monkeypatch.setattr(checkout_expiry_retry, "async_track_point_in_time", track)
    monkeypatch.setattr(
        checkout_expiry,
        "get_entry_data",
        lambda *_args: {EARLY_CHECKOUT_EXPIRY_SWITCH: SimpleNamespace(is_on=True)},
    )
    return notices, timers


def test_notification_is_skipped_without_home_assistant_bus(hass: Any) -> None:
    """Avoid creating persistent notifications before HA is initialized."""
    now = dt_util.now()
    entity = _entity(hass, now - timedelta(days=1), now + timedelta(hours=4))
    entity._hass = SimpleNamespace()

    checkout_expiry_retry._publish_early_expiry_notice(entity)


def test_permanent_removal_dismisses_entry_scoped_notice(
    hass: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Entry removal clears the persistent retry notice by config entry."""
    dismissed: list[str] = []
    monkeypatch.setattr(
        checkout_expiry_retry,
        "async_dismiss",
        lambda _hass, notification_id: dismissed.append(notification_id),
    )

    checkout_expiry_retry.dismiss_early_expiry_notice(hass, "entry-964")

    assert dismissed == ["rental_control_early_checkout_expiry_entry-964"]


@pytest.mark.parametrize("dispatched", [False, True])
async def test_failed_early_expiry_notifies_and_retries(
    hass: Any, monkeypatch: pytest.MonkeyPatch, dispatched: bool
) -> None:
    """Failures remain visible and preserve uncertain dispatched writes."""
    notices, timers = _patch_checkout_runtime(monkeypatch)
    now = dt_util.now()
    start = now - timedelta(days=1)
    old_end = now + timedelta(hours=4)
    entity = _entity(hass, start, old_end)
    attempts: list[datetime] = []

    async def fail_update(
        _entity: Any,
        new_end: datetime,
        *,
        identity_end: datetime | None = None,
        on_dispatch: Any = None,
    ) -> bool:
        """Report an unconfirmed update, optionally after dispatch."""
        assert identity_end == old_end
        attempts.append(new_end)
        if dispatched and on_dispatch is not None:
            on_dispatch()
        return False

    monkeypatch.setattr(checkout_expiry, "async_update_lock_code_expiry", fail_update)
    await runtime._maybe_shorten_lock_expiry(entity, now)

    expected_end = runtime.compute_early_expiry_time(now, old_end)
    assert entity._tracked_event_end == (expected_end if dispatched else old_end)
    assert entity._early_expiry_original_end == old_end
    assert len(notices.created) == 1
    notice = notices.created[0][0][1]
    assert "could not confirm" in notice
    assert "123456" not in notice
    assert len(timers) == 1
    assert timers[0]["when"] - now <= timedelta(minutes=5, seconds=1)
    entity._state = CHECKIN_STATE_CHECKED_OUT

    async def confirm_update(
        _entity: Any,
        _new_end: datetime,
        *,
        identity_end: datetime | None = None,
        on_dispatch: Any = None,
    ) -> bool:
        """Confirm the next request."""
        assert identity_end == old_end
        attempts.append(_new_end)
        return True

    monkeypatch.setattr(
        checkout_expiry, "async_update_lock_code_expiry", confirm_update
    )
    timers[0]["callback"](timers[0]["when"])
    retry_task = entity._early_expiry_retry_task
    assert retry_task is not None
    await retry_task

    assert attempts == [expected_end, expected_end]
    assert entity._early_expiry_original_end is None
    assert notices.dismissed
    assert entity._tracked_event_end == expected_end


async def test_confirmed_early_expiry_never_notifies(
    hass: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A confirmed shortening leaves no standing notice or retry."""
    notices, timers = _patch_checkout_runtime(monkeypatch)
    now = dt_util.now()
    old_end = now + timedelta(hours=4)
    entity = _entity(hass, now - timedelta(days=1), old_end)

    async def confirmed(*_args: Any, **_kwargs: Any) -> bool:
        """Confirm the shortening."""
        return True

    monkeypatch.setattr(checkout_expiry, "async_update_lock_code_expiry", confirmed)
    await runtime._maybe_shorten_lock_expiry(entity, now)

    assert entity._tracked_event_end == runtime.compute_early_expiry_time(now, old_end)
    assert not notices.created
    assert notices.dismissed
    assert not timers


async def test_retry_clears_when_slot_is_replaced(
    hass: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A retry never proceeds after its original slot is replaced."""
    notices, timers = _patch_checkout_runtime(monkeypatch)
    now = dt_util.now()
    start = now - timedelta(days=1)
    old_end = now + timedelta(hours=4)
    entity = _entity(hass, start, old_end)

    async def failed(*_args: Any, **_kwargs: Any) -> bool:
        """Leave the first shortening unconfirmed."""
        return False

    monkeypatch.setattr(checkout_expiry, "async_update_lock_code_expiry", failed)
    await runtime._maybe_shorten_lock_expiry(entity, now)
    entity._state = CHECKIN_STATE_CHECKED_OUT
    entity.coordinator.event_overrides.overrides[10]["end_time"] = old_end + timedelta(
        days=1
    )
    runtime.resume_early_expiry_retry(entity)

    assert not entity._early_expiry_original_end
    assert timers[0]["cancelled"]
    assert notices.dismissed


async def test_stale_reservation_does_not_publish_retry_notice(
    hass: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Known replacement data clears pending work without flashing a notice."""
    notices, timers = _patch_checkout_runtime(monkeypatch)
    now = dt_util.now()
    old_end = now + timedelta(hours=4)
    entity = _entity(hass, now - timedelta(days=1), old_end)

    async def replaced(*_args: Any, **_kwargs: Any) -> bool:
        """Replace the slot before reporting an unconfirmed update."""
        entity.coordinator.event_overrides.overrides[10]["end_time"] = (
            old_end + timedelta(days=1)
        )
        return False

    monkeypatch.setattr(checkout_expiry, "async_update_lock_code_expiry", replaced)
    await runtime._maybe_shorten_lock_expiry(entity, now)

    assert not notices.created
    assert not timers
    assert entity._early_expiry_original_end is None


async def test_legacy_same_name_slot_replacement_fails_date_identity(
    hass: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Calendar membership cannot validate a reused legacy slot by name alone."""
    _patch_checkout_runtime(monkeypatch)
    now = dt_util.now()
    start = now - timedelta(days=1)
    original_end = now + timedelta(hours=4)
    new_end = runtime.compute_early_expiry_time(now, original_end)
    entity = _entity(hass, start, original_end)
    entity._early_expiry_original_end = original_end
    entity._early_expiry_new_end = new_end
    entity._early_expiry_reservation_start = start
    entity._early_expiry_reservation_slot_name = "Guest"
    entity._early_expiry_retry_slot = 10
    entity.coordinator.code_buffer_before = 15
    entity.coordinator.code_buffer_after = 30
    entity.coordinator.event_prefix = ""
    entity.coordinator.bookings_only_feed = False
    entity.coordinator.last_update_success = True
    original_event = SimpleNamespace(
        summary="Guest",
        description=None,
        start=start,
        end=original_end,
        uid=None,
    )
    entity.coordinator.data = [original_event]

    entity.coordinator.event_overrides.overrides[10].update(
        start_time=start - timedelta(minutes=15),
        end_time=original_end + timedelta(minutes=30),
    )
    assert checkout_expiry_guards._legacy_calendar_event_matches(
        entity, original_end, start, "Guest"
    )
    assert checkout_expiry_guards._legacy_reservation_is_current(
        entity, 10, original_end, new_end
    )

    replacement_start = start + timedelta(days=2)
    replacement_end = original_end + timedelta(days=3)
    entity.coordinator.event_overrides.overrides[10].update(
        start_time=replacement_start - timedelta(minutes=15),
        end_time=replacement_end + timedelta(minutes=30),
    )

    assert checkout_expiry_guards._legacy_calendar_event_matches(
        entity, original_end, start, "Guest"
    )
    assert not checkout_expiry_guards._legacy_reservation_is_current(
        entity, 10, original_end, new_end
    )
    assert not checkout_expiry_guards._retry_reservation_is_current(entity)


async def test_verified_duplicate_guest_name_uses_fingerprint_slot(
    hass: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verified routing must retain its fingerprint-resolved slot."""
    _patch_checkout_runtime(monkeypatch)
    monkeypatch.setattr(
        checkout_expiry_guards, "association_requires_verified_routing", lambda *_: True
    )
    now = dt_util.now()
    start = now - timedelta(days=1)
    original_end = now + timedelta(hours=4)
    new_end = runtime.compute_early_expiry_time(now, original_end)
    entity = _entity(hass, start, original_end)
    entity._early_expiry_original_end = original_end
    entity._early_expiry_new_end = new_end
    entity._early_expiry_reservation_start = start
    entity._early_expiry_reservation_slot_name = "Guest"
    entity._early_expiry_retry_slot = 11

    class DuplicateNameOverrides(_Overrides):
        """Represent two verified reservations with the same display name."""

        def __init__(self) -> None:
            """Set up duplicate display names across distinct slots."""
            self.ready = True
            self.overrides = {
                10: {"slot_name": "Guest"},
                11: {"slot_name": "Guest"},
            }

        def get_slot_key_by_name(self, slot_name: str) -> int:
            """Return the first name match, not necessarily the verified slot."""
            return 10 if slot_name == "Guest" else 0

        def get_slot_name(self, slot: int) -> str:
            """Return the selected slot occupant."""
            return cast(str, self.overrides.get(slot, {}).get("slot_name", ""))

    overrides = DuplicateNameOverrides()
    entity.coordinator.event_overrides = overrides
    entity.coordinator.start_slot = 10
    entity.coordinator.max_events = 2
    identity = original_end.isoformat()
    monkeypatch.setattr(
        checkout_expiry_guards,
        "_reservation_fingerprint",
        lambda _entry, _name, _start, end: end.isoformat(),
    )
    entity.coordinator.get_slot_assignment = lambda key: 11 if key == identity else None

    assert (
        checkout_expiry_guards._retry_slot(
            entity, original_end, start, "Guest", new_end
        )
        == 11
    )
    assert checkout_expiry_guards._retry_reservation_is_current(entity)


async def test_failed_retry_keeps_notice_and_uses_backoff(
    hass: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unconfirmed retries preserve old tracking and back off."""
    notices, timers = _patch_checkout_runtime(monkeypatch)
    now = dt_util.now()
    old_end = now + timedelta(hours=4)
    entity = _entity(hass, now - timedelta(days=1), old_end)

    async def failed(*_args: Any, **_kwargs: Any) -> bool:
        """Leave the retry unconfirmed."""
        return False

    monkeypatch.setattr(checkout_expiry, "async_update_lock_code_expiry", failed)
    await runtime._maybe_shorten_lock_expiry(entity, now)
    entity._state = CHECKIN_STATE_CHECKED_OUT
    generation = entity._early_expiry_retry_generation

    await checkout_expiry_retry._async_run_early_expiry_retry(
        entity, generation, now + timedelta(minutes=5)
    )

    assert entity._tracked_event_end == old_end
    assert entity._early_expiry_retry_attempt == 1
    assert len(notices.created) == 1
    assert len(timers) == 2
    assert timers[1]["when"] - now >= timedelta(minutes=15)


async def test_initial_checkout_request_survives_coordinator_refresh(
    hass: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refreshes must preserve retry metadata while checkout is awaiting confirmation."""
    notices, timers = _patch_checkout_runtime(monkeypatch)
    now = dt_util.now()
    old_end = now + timedelta(hours=4)
    entity = _entity(hass, now - timedelta(days=1), old_end)
    started = asyncio.Event()
    finish = asyncio.Event()

    async def fail_after_refresh(*_args: Any, **_kwargs: Any) -> bool:
        """Hold the initial request until after the coordinator refresh."""
        started.set()
        await finish.wait()
        return False

    monkeypatch.setattr(
        checkout_expiry, "async_update_lock_code_expiry", fail_after_refresh
    )
    request = asyncio.create_task(runtime._maybe_shorten_lock_expiry(entity, now))
    await started.wait()

    checkout_expiry_retry.resume_early_expiry_retry(entity)

    assert entity._early_expiry_retry_initial_pending
    assert entity._early_expiry_original_end == old_end
    assert not notices.created
    assert not timers

    finish.set()
    await request

    assert not entity._early_expiry_retry_initial_pending
    assert entity._early_expiry_original_end == old_end
    assert notices.created
    assert len(timers) == 1


async def test_interrupted_initial_request_resumes_after_restore(
    hass: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A restored in-flight marker must not block retry recovery."""
    notices, timers = _patch_checkout_runtime(monkeypatch)
    now = dt_util.now()
    start = now - timedelta(days=1)
    original_end = now + timedelta(hours=4)
    new_end = runtime.compute_early_expiry_time(now, original_end)
    snapshot = CheckinStateSnapshot(
        state=CHECKIN_STATE_CHECKED_OUT,
        tracked_event_start=start,
        tracked_event_end=original_end,
        tracked_event_slot_name="Guest",
        early_expiry_original_end=original_end,
        early_expiry_new_end=new_end,
        early_expiry_reservation_start=start,
        early_expiry_reservation_slot_name="Guest",
        early_expiry_retry_initial_pending=True,
    )
    stored = CheckinExtraStoredData.from_snapshot(snapshot)
    restored = CheckinExtraStoredData.from_dict(stored.as_dict()).snapshot
    entity = _entity(hass, start, original_end)
    entity._state = CHECKIN_STATE_CHECKED_OUT
    entity._early_expiry_original_end = restored.early_expiry_original_end
    entity._early_expiry_new_end = restored.early_expiry_new_end
    entity._early_expiry_reservation_start = restored.early_expiry_reservation_start
    entity._early_expiry_reservation_slot_name = (
        restored.early_expiry_reservation_slot_name
    )
    entity._early_expiry_retry_initial_pending = (
        restored.early_expiry_retry_initial_pending
    )
    entity.coordinator.event_overrides = None

    checkout_expiry_retry.resume_early_expiry_retry(entity)

    assert not entity._early_expiry_retry_initial_pending
    assert len(notices.created) == 1
    assert len(timers) == 1


async def test_retry_exception_keeps_notice_and_reschedules(
    hass: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retry exceptions leave checkout complete and keep retrying."""
    notices, timers = _patch_checkout_runtime(monkeypatch)
    now = dt_util.now()
    old_end = now + timedelta(hours=4)
    entity = _entity(hass, now - timedelta(days=1), old_end)

    async def initially_failed(*_args: Any, **_kwargs: Any) -> bool:
        """Create pending retry state."""
        return False

    monkeypatch.setattr(
        checkout_expiry, "async_update_lock_code_expiry", initially_failed
    )
    await runtime._maybe_shorten_lock_expiry(entity, now)
    entity._state = CHECKIN_STATE_CHECKED_OUT

    async def raises(*_args: Any, **_kwargs: Any) -> bool:
        """Simulate an unexpected transport or service failure."""
        raise HomeAssistantError("transport unavailable")

    monkeypatch.setattr(checkout_expiry, "async_update_lock_code_expiry", raises)
    await checkout_expiry_retry._async_run_early_expiry_retry(
        entity, entity._early_expiry_retry_generation, timers[0]["when"]
    )

    assert entity._tracked_event_end == old_end
    assert entity._early_expiry_original_end == old_end
    assert entity._early_expiry_retry_attempt == 1
    assert len(notices.created) == 1
    assert len(timers) == 2


@pytest.mark.parametrize("verified", [False, True])
async def test_retry_resolves_slot_that_was_unavailable_at_checkout(
    hass: Any, monkeypatch: pytest.MonkeyPatch, verified: bool
) -> None:
    """A missing legacy or verified slot is resolved before retry."""
    _notices, timers = _patch_checkout_runtime(monkeypatch)
    now = dt_util.now()
    old_end = now + timedelta(hours=4)
    entity = _entity(hass, now - timedelta(days=1), old_end)
    overrides = entity.coordinator.event_overrides
    available = False
    if verified:
        monkeypatch.setattr(
            checkout_expiry, "association_requires_verified_routing", lambda *_: True
        )
        monkeypatch.setattr(
            checkout_expiry_guards,
            "association_requires_verified_routing",
            lambda *_: True,
        )
        monkeypatch.setattr(
            checkout_expiry_retry,
            "association_requires_verified_routing",
            lambda *_: True,
        )
        entity.coordinator.get_slot_assignment = lambda _identity: (
            10 if available else None
        )
    else:
        overrides.get_slot_key_by_name = lambda _name: 0

    async def failed(*_args: Any, **_kwargs: Any) -> bool:
        """Leave the first request unconfirmed."""
        return False

    monkeypatch.setattr(checkout_expiry, "async_update_lock_code_expiry", failed)
    await runtime._maybe_shorten_lock_expiry(entity, now)
    assert entity._early_expiry_retry_slot is None
    assert timers[0]["when"] < old_end

    entity._state = CHECKIN_STATE_CHECKED_OUT
    available = True
    if not verified:
        overrides.get_slot_key_by_name = lambda _name: 10
    resolved: list[int] = []

    async def confirmed(
        _entity: Any,
        _new_end: datetime,
        *,
        identity_end: datetime | None = None,
        on_dispatch: Any = None,
    ) -> bool:
        """Confirm the retry using the newly available slot."""
        assert identity_end == old_end
        resolved.append(entity._early_expiry_retry_slot)
        return True

    monkeypatch.setattr(checkout_expiry, "async_update_lock_code_expiry", confirmed)
    await checkout_expiry_retry._async_run_early_expiry_retry(
        entity, entity._early_expiry_retry_generation, timers[0]["when"]
    )

    assert resolved == [10]
    assert entity._early_expiry_original_end is None


async def test_empty_legacy_calendar_data_keeps_retry_pending(
    hass: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Transiently empty calendar results are unknown, not reservation loss."""
    _notices, timers = _patch_checkout_runtime(monkeypatch)
    now = dt_util.now()
    old_end = now + timedelta(hours=4)
    entity = _entity(hass, now - timedelta(days=1), old_end)
    entity.coordinator.data = []
    entity.coordinator.last_update_success = True
    entity._find_tracked_event = lambda: None
    attempts = 0

    async def failed(*_args: Any, **_kwargs: Any) -> bool:
        """Leave the initial update unconfirmed."""
        nonlocal attempts
        attempts += 1
        return False

    monkeypatch.setattr(checkout_expiry, "async_update_lock_code_expiry", failed)
    await runtime._maybe_shorten_lock_expiry(entity, now)
    entity._state = CHECKIN_STATE_CHECKED_OUT

    await checkout_expiry_retry._async_run_early_expiry_retry(
        entity, entity._early_expiry_retry_generation, timers[0]["when"]
    )

    assert entity._early_expiry_original_end == old_end
    assert attempts == 1
    assert len(timers) == 2
    assert entity._early_expiry_retry_attempt == 1


async def test_successful_retry_clears_state_after_generation_changes(
    hass: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An in-flight confirmed retry clears its notice despite a refresh race."""
    notices, timers = _patch_checkout_runtime(monkeypatch)
    now = dt_util.now()
    old_end = now + timedelta(hours=4)
    entity = _entity(hass, now - timedelta(days=1), old_end)

    async def initially_failed(*_args: Any, **_kwargs: Any) -> bool:
        """Create pending retry state."""
        return False

    monkeypatch.setattr(
        checkout_expiry, "async_update_lock_code_expiry", initially_failed
    )
    await runtime._maybe_shorten_lock_expiry(entity, now)
    entity._state = CHECKIN_STATE_CHECKED_OUT
    started = asyncio.Event()
    finish = asyncio.Event()

    async def confirm_after_refresh(*_args: Any, **_kwargs: Any) -> bool:
        """Wait for the simulated coordinator refresh, then confirm."""
        started.set()
        await finish.wait()
        return True

    monkeypatch.setattr(
        checkout_expiry, "async_update_lock_code_expiry", confirm_after_refresh
    )
    generation = entity._early_expiry_retry_generation
    entity._early_expiry_retry_task = asyncio.create_task(
        checkout_expiry_retry._async_run_early_expiry_retry(
            entity, generation, timers[0]["when"]
        )
    )
    await started.wait()

    checkout_expiry_retry.resume_early_expiry_retry(entity)
    assert entity._early_expiry_retry_generation == generation
    assert len(notices.created) == 1
    assert len(timers) == 1
    entity._early_expiry_retry_generation += 1
    finish.set()
    await entity._early_expiry_retry_task

    assert entity._early_expiry_original_end is None
    assert notices.dismissed
    assert entity._early_expiry_retry_handle is None


async def test_replacement_during_dispatched_retry_defers_cleanup(
    hass: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dispatched retry finishes without touching a replacement reservation."""
    notices, timers = _patch_checkout_runtime(monkeypatch)
    now = dt_util.now()
    old_end = now + timedelta(hours=4)
    entity = _entity(hass, now - timedelta(days=1), old_end)

    async def initially_failed(*_args: Any, **_kwargs: Any) -> bool:
        """Create pending retry state."""
        return False

    monkeypatch.setattr(
        checkout_expiry, "async_update_lock_code_expiry", initially_failed
    )
    await runtime._maybe_shorten_lock_expiry(entity, now)
    entity._state = CHECKIN_STATE_CHECKED_OUT
    started = asyncio.Event()
    finish = asyncio.Event()

    async def confirm_after_dispatch(
        _entity: Any,
        _new_end: datetime,
        *,
        on_dispatch: Any = None,
        **_kwargs: Any,
    ) -> bool:
        """Hold an already-dispatched write while the slot is replaced."""
        if on_dispatch is not None:
            on_dispatch()
        started.set()
        await finish.wait()
        return True

    monkeypatch.setattr(
        checkout_expiry, "async_update_lock_code_expiry", confirm_after_dispatch
    )
    generation = entity._early_expiry_retry_generation
    task = asyncio.create_task(
        checkout_expiry_retry._async_run_early_expiry_retry(
            entity, generation, timers[0]["when"]
        )
    )
    entity._early_expiry_retry_task = task
    await started.wait()

    replacement_start = now + timedelta(days=1)
    replacement_end = now + timedelta(days=2)
    entity._tracked_event_start = replacement_start
    entity._tracked_event_end = replacement_end
    entity._tracked_event_slot_name = "Replacement"
    entity.coordinator.event_overrides.overrides[10].update(
        slot_name="Replacement",
        start_time=replacement_start,
        end_time=replacement_end,
    )
    checkout_expiry_retry.resume_early_expiry_retry(entity)

    assert not task.cancelled()
    assert entity._early_expiry_retry_clear_when_done
    assert entity._early_expiry_original_end == old_end
    assert not notices.dismissed
    finish.set()
    await task

    assert entity._tracked_event_start == replacement_start
    assert entity._tracked_event_end == replacement_end
    assert entity._tracked_event_slot_name == "Replacement"
    assert entity._early_expiry_original_end is None
    assert notices.dismissed
    assert entity._early_expiry_retry_task is None


async def test_cancel_dispatched_retry_preserves_pending_notice(
    hass: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unloading cannot cancel dispatched work or dismiss its pending notice."""
    notices, timers = _patch_checkout_runtime(monkeypatch)
    now = dt_util.now()
    old_end = now + timedelta(hours=4)
    entity = _entity(hass, now - timedelta(days=1), old_end)

    async def initially_failed(*_args: Any, **_kwargs: Any) -> bool:
        """Create pending retry state."""
        return False

    monkeypatch.setattr(
        checkout_expiry, "async_update_lock_code_expiry", initially_failed
    )
    await runtime._maybe_shorten_lock_expiry(entity, now)
    entity._state = CHECKIN_STATE_CHECKED_OUT
    started = asyncio.Event()
    finish = asyncio.Event()

    async def fail_after_dispatch(
        _entity: Any,
        _new_end: datetime,
        *,
        on_dispatch: Any = None,
        **_kwargs: Any,
    ) -> bool:
        """Hold dispatched work, then report that it remains unconfirmed."""
        if on_dispatch is not None:
            on_dispatch()
        started.set()
        await finish.wait()
        return False

    monkeypatch.setattr(
        checkout_expiry, "async_update_lock_code_expiry", fail_after_dispatch
    )
    task = asyncio.create_task(
        checkout_expiry_retry._async_run_early_expiry_retry(
            entity, entity._early_expiry_retry_generation, timers[0]["when"]
        )
    )
    entity._early_expiry_retry_task = task
    await started.wait()

    pending_original_end = entity._early_expiry_original_end
    pending_new_end = entity._early_expiry_new_end
    checkout_expiry_retry.cancel_early_expiry_retry(entity, dismiss=False)

    assert not task.cancelled()
    assert entity._early_expiry_original_end == pending_original_end
    assert entity._early_expiry_new_end == pending_new_end
    assert not notices.dismissed
    assert timers[0]["cancelled"]

    finish.set()
    await task

    assert entity._early_expiry_original_end == pending_original_end
    assert entity._early_expiry_new_end == pending_new_end
    assert not notices.dismissed
    assert entity._early_expiry_retry_task is None


async def test_confirmed_retry_after_removal_does_not_write_entity_state(
    hass: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dispatched confirmation can finish without writing a removed entity."""
    notices, timers = _patch_checkout_runtime(monkeypatch)
    now = dt_util.now()
    old_end = now + timedelta(hours=4)
    entity = _entity(hass, now - timedelta(days=1), old_end)
    writes: list[None] = []
    entity.async_write_ha_state = lambda: writes.append(None)

    async def initially_failed(*_args: Any, **_kwargs: Any) -> bool:
        """Create pending retry state."""
        return False

    monkeypatch.setattr(
        checkout_expiry, "async_update_lock_code_expiry", initially_failed
    )
    await runtime._maybe_shorten_lock_expiry(entity, now)
    entity._state = CHECKIN_STATE_CHECKED_OUT
    started = asyncio.Event()
    finish = asyncio.Event()

    async def confirm_after_dispatch(
        _entity: Any,
        _new_end: datetime,
        *,
        on_dispatch: Any = None,
        **_kwargs: Any,
    ) -> bool:
        """Keep a dispatched confirmation in flight through removal."""
        if on_dispatch is not None:
            on_dispatch()
        started.set()
        await finish.wait()
        return True

    monkeypatch.setattr(
        checkout_expiry, "async_update_lock_code_expiry", confirm_after_dispatch
    )
    task = asyncio.create_task(
        checkout_expiry_retry._async_run_early_expiry_retry(
            entity, entity._early_expiry_retry_generation, timers[0]["when"]
        )
    )
    entity._early_expiry_retry_task = task
    await started.wait()

    checkout_expiry_retry.cancel_early_expiry_retry(
        entity, dismiss=False, entity_removed=True
    )
    assert entity._early_expiry_retry_entity_removed
    assert not task.cancelled()
    finish.set()
    await task

    assert not writes
    assert entity._early_expiry_original_end is None
    assert notices.dismissed
    assert entity._early_expiry_retry_task is None


async def test_retry_clear_flag_does_not_leak_across_reload(
    hass: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A later reload preserves pending metadata after an earlier deferred clear."""
    notices, timers = _patch_checkout_runtime(monkeypatch)
    now = dt_util.now()
    first_end = now + timedelta(hours=4)
    entity = _entity(hass, now - timedelta(days=1), first_end)

    async def fail_without_dispatch(*_args: Any, **_kwargs: Any) -> bool:
        """Create pending retry state."""
        return False

    monkeypatch.setattr(
        checkout_expiry, "async_update_lock_code_expiry", fail_without_dispatch
    )
    await runtime._maybe_shorten_lock_expiry(entity, now)
    entity._state = CHECKIN_STATE_CHECKED_OUT
    first_started = asyncio.Event()
    first_finish = asyncio.Event()

    async def confirm_first(
        _entity: Any,
        _new_end: datetime,
        *,
        on_dispatch: Any = None,
        **_kwargs: Any,
    ) -> bool:
        """Hold the first dispatched retry until its reservation is replaced."""
        if on_dispatch is not None:
            on_dispatch()
        first_started.set()
        await first_finish.wait()
        return True

    monkeypatch.setattr(checkout_expiry, "async_update_lock_code_expiry", confirm_first)
    first_generation = entity._early_expiry_retry_generation
    first_task = asyncio.create_task(
        checkout_expiry_retry._async_run_early_expiry_retry(
            entity, first_generation, timers[0]["when"]
        )
    )
    entity._early_expiry_retry_task = first_task
    await first_started.wait()

    replacement_start = now + timedelta(days=1)
    replacement_end = now + timedelta(days=2)
    entity._tracked_event_start = replacement_start
    entity._tracked_event_end = replacement_end
    entity._tracked_event_slot_name = "Replacement"
    entity.coordinator.event_overrides.overrides[10].update(
        slot_name="Replacement",
        start_time=replacement_start,
        end_time=replacement_end,
    )
    checkout_expiry_retry.resume_early_expiry_retry(entity)
    assert entity._early_expiry_retry_clear_when_done

    first_finish.set()
    await first_task
    assert entity._early_expiry_original_end is None
    assert entity._early_expiry_retry_clear_when_done is False
    first_dismissal_count = len(notices.dismissed)

    entity._state = CHECKIN_STATE_CHECKED_IN
    second_end = replacement_end

    async def fail_second_checkout(*_args: Any, **_kwargs: Any) -> bool:
        """Start the second cycle with an unconfirmed update."""
        return False

    monkeypatch.setattr(
        checkout_expiry, "async_update_lock_code_expiry", fail_second_checkout
    )
    await runtime._maybe_shorten_lock_expiry(entity, replacement_start)
    second_new_end = entity._early_expiry_new_end
    assert entity._early_expiry_retry_clear_when_done is False
    assert entity._early_expiry_original_end == second_end

    entity._state = CHECKIN_STATE_CHECKED_OUT
    second_started = asyncio.Event()
    second_finish = asyncio.Event()

    async def fail_second_retry_after_dispatch(
        _entity: Any,
        _new_end: datetime,
        *,
        on_dispatch: Any = None,
        **_kwargs: Any,
    ) -> bool:
        """Keep the second retry pending across an entity reload."""
        if on_dispatch is not None:
            on_dispatch()
        second_started.set()
        await second_finish.wait()
        return False

    monkeypatch.setattr(
        checkout_expiry,
        "async_update_lock_code_expiry",
        fail_second_retry_after_dispatch,
    )
    second_generation = entity._early_expiry_retry_generation
    second_task = asyncio.create_task(
        checkout_expiry_retry._async_run_early_expiry_retry(
            entity, second_generation, timers[1]["when"]
        )
    )
    entity._early_expiry_retry_task = second_task
    await second_started.wait()
    checkout_expiry_retry.cancel_early_expiry_retry(entity, dismiss=False)
    assert not second_task.cancelled()
    second_finish.set()
    await second_task

    assert entity._early_expiry_retry_clear_when_done is False
    assert entity._early_expiry_original_end == second_end
    assert entity._early_expiry_new_end == second_new_end
    assert len(notices.dismissed) == first_dismissal_count

    snapshot = CheckinStateSnapshot(
        state=CHECKIN_STATE_CHECKED_OUT,
        tracked_event_summary="Reserved - Replacement",
        tracked_event_start=replacement_start,
        tracked_event_end=entity._tracked_event_end,
        tracked_event_slot_name="Replacement",
        early_expiry_original_end=entity._early_expiry_original_end,
        early_expiry_new_end=entity._early_expiry_new_end,
        early_expiry_retry_attempt=entity._early_expiry_retry_attempt,
        early_expiry_retry_slot=entity._early_expiry_retry_slot,
    )
    restored = CheckinExtraStoredData.from_dict(
        CheckinExtraStoredData.from_snapshot(snapshot).as_dict()
    ).snapshot
    reloaded = _entity(hass, replacement_start, second_end)
    reloaded._tracked_event_summary = "Reserved - Replacement"
    reloaded._tracked_event_slot_name = "Replacement"
    reloaded._tracked_event_end = entity._tracked_event_end
    reloaded._state = CHECKIN_STATE_CHECKED_OUT
    reloaded.coordinator.event_overrides.overrides[10].update(
        slot_name="Replacement",
        start_time=replacement_start,
        end_time=second_end,
    )
    for field in restored.__dataclass_fields__:
        setattr(reloaded, f"_{field}", getattr(restored, field))

    checkout_expiry_retry.resume_early_expiry_retry(reloaded)

    assert reloaded._early_expiry_original_end == second_end
    assert reloaded._early_expiry_new_end == second_new_end
    assert not getattr(reloaded, "_early_expiry_retry_clear_when_done", False)
    assert len(timers) == 3
    assert len(notices.dismissed) == first_dismissal_count
    assert len(notices.created) == 3

    retry_calls: list[tuple[datetime, datetime | None]] = []

    async def confirm_restored_retry(
        _entity: Any,
        new_end: datetime,
        *,
        identity_end: datetime | None = None,
        on_dispatch: Any = None,
    ) -> bool:
        """Confirm the retry restored from persisted state."""
        retry_calls.append((new_end, identity_end))
        if on_dispatch is not None:
            on_dispatch()
        return True

    monkeypatch.setattr(
        checkout_expiry, "async_update_lock_code_expiry", confirm_restored_retry
    )
    timers[-1]["callback"](now)
    restored_task = reloaded._early_expiry_retry_task
    assert restored_task is not None
    await restored_task

    assert retry_calls == [(second_new_end, second_end)]
    assert reloaded._early_expiry_original_end is None


async def test_legacy_readback_failure_retries_until_datetime_matches(
    hass: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Service success without read-back remains pending until it matches."""
    notices, timers = _patch_checkout_runtime(monkeypatch)
    now = dt_util.now()
    old_end = now + timedelta(hours=4)
    entity = _entity(hass, now - timedelta(days=1), old_end)
    entity.coordinator.keymaster_runtime_enabled = True
    monkeypatch.setattr(
        checkout_expiry, "protected_registry_active", lambda *_args: False
    )
    service_calls: list[tuple[Any, ...]] = []
    readback_results = [False, True]
    readbacks: list[tuple[str, datetime]] = []

    async def service_call(*args: Any) -> None:
        """Record a successful datetime service call."""
        service_calls.append(args)

    monkeypatch.setattr(
        checkinsensor,
        "add_call",
        lambda *_args, **_kwargs: [service_call()],
    )

    async def wait_for_datetime(
        _hass: Any, entity_id: str, expected: datetime, _timeout: float
    ) -> bool:
        """Simulate a delayed Keymaster datetime state update."""
        readbacks.append((entity_id, expected))
        return readback_results.pop(0)

    monkeypatch.setattr(
        checkout_expiry, "_async_wait_for_expected_datetime", wait_for_datetime
    )
    await runtime._maybe_shorten_lock_expiry(entity, now)

    assert len(service_calls) == 1
    assert len(readbacks) == 1
    assert entity._early_expiry_original_end == old_end
    assert notices.created

    entity._state = CHECKIN_STATE_CHECKED_OUT
    await checkout_expiry_retry._async_run_early_expiry_retry(
        entity, entity._early_expiry_retry_generation, timers[0]["when"]
    )

    assert len(service_calls) == 2
    assert len(readbacks) == 2
    assert readbacks[0][0] == "datetime.front_door_code_slot_10_date_range_end"
    assert readbacks[0] == readbacks[1]
    assert entity._early_expiry_original_end is None
    assert notices.dismissed


async def test_legacy_datetime_readback_uses_real_home_assistant_state(
    hass: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real datetime service and HA state confirm the legacy retry."""
    notices, timers = _patch_checkout_runtime(monkeypatch)
    now = dt_util.now()
    old_end = now + timedelta(hours=4)
    new_end = runtime.compute_early_expiry_time(now, old_end)
    entity = _entity(hass, now - timedelta(days=1), old_end)
    entity._state = CHECKIN_STATE_CHECKED_OUT
    entity._tracked_event_end = new_end
    entity._early_expiry_original_end = old_end
    entity._early_expiry_new_end = new_end
    entity._early_expiry_retry_attempt = 1
    entity._early_expiry_retry_slot = 10
    entity.coordinator.keymaster_runtime_enabled = True
    entity_id = checkout_expiry._legacy_expiry_entity_id("front_door", 10)
    hass.states.async_set(entity_id, old_end.isoformat())
    monkeypatch.setattr(checkout_expiry, "protected_registry_active", lambda *_: False)
    calls = 0
    delayed_updates: list[asyncio.Task[None]] = []
    deps = util._keymaster_deps()
    listened_entities: list[str] = []

    def track_state_change_thread_safely(
        _hass: Any, entity_ids: list[str], callback: Any
    ) -> Any:
        """Marshal HA state events onto the waiter's event loop."""
        listened_entities.extend(entity_ids)
        loop = asyncio.get_running_loop()

        def handle_event(event: Any) -> None:
            """Invoke the waiter callback on its owning loop."""
            loop.call_soon_threadsafe(callback, event)

        # HA's test harness may dispatch state callbacks from another loop.
        return deps.track_state_change(_hass, entity_ids, handle_event)

    monkeypatch.setattr(
        util,
        "_keymaster_deps",
        lambda: replace(deps, track_state_change=track_state_change_thread_safely),
    )
    checkout_expiry_retry._publish_early_expiry_notice(entity)

    async def set_datetime(call: Any) -> None:
        """Schedule a delayed HA state update for the dispatched entity."""
        nonlocal calls
        calls += 1
        target = call.data["entity_id"]
        assert target == entity_id

        async def update_state() -> None:
            """Set the target state after read-back has started."""
            await asyncio.sleep(0.01)
            hass.states.async_set(target, new_end.isoformat())

        delayed_updates.append(asyncio.create_task(update_state()))

    hass.services.async_register("datetime", "set_value", set_datetime)
    try:
        await checkout_expiry_retry._async_run_early_expiry_retry(
            entity, entity._early_expiry_retry_generation, now
        )
    finally:
        if delayed_updates:
            await asyncio.gather(*delayed_updates, return_exceptions=True)
        hass.services.async_remove("datetime", "set_value")

    assert calls == 1
    assert listened_entities == [entity_id]
    assert hass.states.get(entity_id).state == new_end.isoformat()
    assert entity._early_expiry_original_end is None
    assert notices.dismissed
    assert not timers


async def test_legacy_datetime_readback_timeout_keeps_retry_pending(
    hass: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dispatched update without state read-back stays pending for retry."""
    notices, timers = _patch_checkout_runtime(monkeypatch)
    now = dt_util.now()
    old_end = now + timedelta(hours=4)
    entity = _entity(hass, now - timedelta(days=1), old_end)
    entity.coordinator.keymaster_runtime_enabled = True
    entity_id = checkout_expiry._legacy_expiry_entity_id("front_door", 10)
    hass.states.async_set(entity_id, old_end.isoformat())
    monkeypatch.setattr(
        checkout_expiry, "protected_registry_active", lambda *_args: False
    )
    monkeypatch.setattr(checkout_expiry, "_SET_CODE_CONFIRMATION_TIMEOUT", 0.01)
    deps = util._keymaster_deps()
    monkeypatch.setattr(
        util,
        "_keymaster_deps",
        lambda: replace(deps, confirmation_timeout=0.01),
    )
    calls: list[str] = []
    dispatches: list[bool] = []

    async def no_state_update(call: Any) -> None:
        """Acknowledge service dispatch without changing the HA state."""
        calls.append(call.data["entity_id"])

    hass.services.async_register("datetime", "set_value", no_state_update)
    update = checkout_expiry.async_update_lock_code_expiry

    async def observe_dispatch(*args: Any, **kwargs: Any) -> bool:
        """Record dispatch while executing the real legacy update."""
        on_dispatch = kwargs.get("on_dispatch")

        def mark_dispatched() -> None:
            """Record and forward the real dispatch callback."""
            dispatches.append(True)
            if callable(on_dispatch):
                on_dispatch()

        kwargs["on_dispatch"] = mark_dispatched
        return await update(*args, **kwargs)

    monkeypatch.setattr(
        checkout_expiry, "async_update_lock_code_expiry", observe_dispatch
    )
    try:
        await runtime._maybe_shorten_lock_expiry(entity, now)
    finally:
        hass.services.async_remove("datetime", "set_value")

    assert calls == [entity_id]
    assert dispatches == [True]
    assert entity._early_expiry_original_end == old_end
    assert notices.created
    assert not notices.dismissed
    assert len(timers) == 1


async def test_resume_retry_waits_for_ready_reservation_data(
    hass: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Restored retry state waits for coordinator data and expires safely."""
    notices, timers = _patch_checkout_runtime(monkeypatch)
    now = dt_util.now()
    old_end = now + timedelta(hours=4)
    entity = _entity(hass, now - timedelta(days=1), old_end)
    snapshot = CheckinStateSnapshot(
        state=CHECKIN_STATE_CHECKED_OUT,
        tracked_event_summary="Reserved - Guest",
        tracked_event_start=entity._tracked_event_start,
        tracked_event_end=old_end,
        tracked_event_slot_name="Guest",
        early_expiry_original_end=old_end,
        early_expiry_new_end=now + timedelta(hours=1),
        early_expiry_retry_attempt=1,
        early_expiry_retry_slot=None,
    )
    serialized = CheckinExtraStoredData.from_snapshot(snapshot).as_dict()
    restored = CheckinExtraStoredData.from_dict(serialized).snapshot
    for field in restored.__dataclass_fields__:
        setattr(entity, f"_{field}", getattr(restored, field))
    entity.coordinator.event_overrides.ready = False

    checkout_expiry_retry.resume_early_expiry_retry(entity)
    assert notices.created
    assert len(timers) == 1

    entity.coordinator.event_overrides.ready = True
    checkout_expiry_retry.resume_early_expiry_retry(entity)
    assert len(notices.created) == 1
    assert len(timers) == 1

    entity._early_expiry_original_end = now - timedelta(seconds=1)
    checkout_expiry_retry.resume_early_expiry_retry(entity)
    assert entity._early_expiry_original_end is None
    assert timers[0]["cancelled"]
    assert notices.dismissed


async def test_retry_survives_linger_expiry_and_restore(
    hass: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An expired display linger does not discard an active reservation retry."""
    notices, timers = _patch_checkout_runtime(monkeypatch)
    now = dt_util.now()
    start = now - timedelta(days=1)
    original_end = now + timedelta(hours=4)
    new_end = runtime.compute_early_expiry_time(now, original_end)
    entity = _entity(hass, start, original_end)
    entity._cancel_timer = lambda: None
    entity.coordinator.event_prefix = ""
    entity.coordinator.bookings_only_feed = False
    entity.coordinator.last_update_success = True
    entity.coordinator.data = [
        SimpleNamespace(
            summary="Guest",
            description=None,
            start=start,
            end=original_end,
            uid=None,
        )
    ]

    async def fail_initial(*_args: Any, **_kwargs: Any) -> bool:
        """Create a pending, undispatched expiry retry."""
        return False

    monkeypatch.setattr(checkout_expiry, "async_update_lock_code_expiry", fail_initial)
    await runtime._maybe_shorten_lock_expiry(entity, now)
    entity._state = CHECKIN_STATE_CHECKED_OUT
    runtime.transition_to_no_reservation(entity)

    assert entity._state == "no_reservation"
    assert entity._tracked_event_start is None
    assert entity._tracked_event_slot_name is None
    assert entity._early_expiry_original_end == original_end
    assert not notices.dismissed
    snapshot = CheckinStateSnapshot(
        state=entity._state,
        early_expiry_original_end=entity._early_expiry_original_end,
        early_expiry_new_end=entity._early_expiry_new_end,
        early_expiry_retry_attempt=entity._early_expiry_retry_attempt,
        early_expiry_retry_slot=entity._early_expiry_retry_slot,
        early_expiry_reservation_start=entity._early_expiry_reservation_start,
        early_expiry_reservation_slot_name=entity._early_expiry_reservation_slot_name,
        early_expiry_retry_was_dispatched=entity._early_expiry_retry_was_dispatched,
    )
    restored = CheckinExtraStoredData.from_dict(
        CheckinExtraStoredData.from_snapshot(snapshot).as_dict()
    ).snapshot

    checkout_expiry_retry.cancel_early_expiry_retry(entity, dismiss=False)
    reloaded = _entity(hass, start, original_end)
    reloaded.coordinator.event_prefix = ""
    reloaded.coordinator.bookings_only_feed = False
    reloaded.coordinator.last_update_success = True
    reloaded.coordinator.data = entity.coordinator.data
    for field in restored.__dataclass_fields__:
        setattr(reloaded, f"_{field}", getattr(restored, field))

    checkout_expiry_retry.resume_early_expiry_retry(reloaded)

    assert len(timers) == 2
    assert timers[0]["cancelled"]
    retry_calls: list[tuple[datetime, datetime | None]] = []

    async def confirm_retry(
        _entity: Any,
        expected_end: datetime,
        *,
        identity_end: datetime | None = None,
        on_dispatch: Any = None,
    ) -> bool:
        """Confirm the pending expiry against its stored reservation identity."""
        retry_calls.append((expected_end, identity_end))
        if on_dispatch is not None:
            on_dispatch()
        return True

    monkeypatch.setattr(checkout_expiry, "async_update_lock_code_expiry", confirm_retry)
    timers[-1]["callback"](now)
    retry_task = reloaded._early_expiry_retry_task
    assert retry_task is not None
    await retry_task

    assert retry_calls == [(new_end, original_end)]
    assert reloaded._early_expiry_original_end is None
    assert notices.dismissed


async def test_resume_retry_schedules_while_overrides_are_unavailable(
    hass: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Persisted retries remain timed while coordinator overrides are absent."""
    notices, timers = _patch_checkout_runtime(monkeypatch)
    now = dt_util.now()
    old_end = now + timedelta(hours=4)
    entity = _entity(hass, now - timedelta(days=1), old_end)
    entity._state = CHECKIN_STATE_CHECKED_OUT
    entity._early_expiry_original_end = old_end
    entity._early_expiry_new_end = now + timedelta(hours=1)
    entity.coordinator.event_overrides = None

    checkout_expiry_retry.resume_early_expiry_retry(entity)

    assert notices.created
    assert len(timers) == 1
    assert timers[0]["when"] < old_end


async def test_cancelled_retry_callback_ignores_stale_generation(
    hass: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cancelled timer callback cannot restart retry work."""
    _notices, timers = _patch_checkout_runtime(monkeypatch)
    now = dt_util.now()
    old_end = now + timedelta(hours=4)
    entity = _entity(hass, now - timedelta(days=1), old_end)

    async def failed(*_args: Any, **_kwargs: Any) -> bool:
        """Leave the first shortening pending."""
        return False

    monkeypatch.setattr(checkout_expiry, "async_update_lock_code_expiry", failed)
    await runtime._maybe_shorten_lock_expiry(entity, now)
    checkout_expiry_retry.cancel_early_expiry_retry(entity, dismiss=False)

    timers[0]["callback"](timers[0]["when"])
    assert entity._early_expiry_retry_task is None


async def test_entity_removal_cancels_active_retry_task(hass: Any) -> None:
    """Unloading an entity cancels an already running retry task."""
    now = dt_util.now()
    entity = _entity(hass, now - timedelta(days=1), now + timedelta(hours=4))
    task = asyncio.create_task(asyncio.Event().wait())
    entity._early_expiry_retry_task = task

    checkout_expiry_retry.cancel_early_expiry_retry(entity, dismiss=False)

    with pytest.raises(asyncio.CancelledError):
        await task
    assert entity._early_expiry_retry_task is None


async def test_retry_stops_at_original_reservation_end(
    hass: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The original end is the final expiry boundary for pending retries."""
    notices, timers = _patch_checkout_runtime(monkeypatch)
    now = dt_util.now()
    old_end = now + timedelta(hours=4)
    entity = _entity(hass, now - timedelta(days=1), old_end)
    calls = 0

    async def failed(*_args: Any, **_kwargs: Any) -> bool:
        """Leave the shortening unconfirmed."""
        nonlocal calls
        calls += 1
        return False

    monkeypatch.setattr(checkout_expiry, "async_update_lock_code_expiry", failed)
    await runtime._maybe_shorten_lock_expiry(entity, now)
    entity._state = CHECKIN_STATE_CHECKED_OUT
    generation = entity._early_expiry_retry_generation
    await checkout_expiry_retry._async_run_early_expiry_retry(
        entity, generation, old_end
    )

    assert calls == 1
    assert not entity._early_expiry_original_end
    assert timers[0]["cancelled"]
    assert notices.dismissed


async def test_removal_cancels_retry_without_losing_restore_state(
    hass: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Entity removal cancels callbacks but preserves pending retry metadata."""
    notices, timers = _patch_checkout_runtime(monkeypatch)
    now = dt_util.now()
    old_end = now + timedelta(hours=4)
    entity = _entity(hass, now - timedelta(days=1), old_end)

    async def failed(*_args: Any, **_kwargs: Any) -> bool:
        """Leave the shortening pending."""
        return False

    monkeypatch.setattr(checkout_expiry, "async_update_lock_code_expiry", failed)
    await runtime._maybe_shorten_lock_expiry(entity, now)
    retry_end = entity._early_expiry_original_end

    runtime.cancel_early_expiry_retry(entity, dismiss=False)
    timers[0]["callback"](timers[0]["when"])
    await hass.async_block_till_done()

    assert timers[0]["cancelled"]
    assert entity._early_expiry_original_end == retry_end
    assert not notices.dismissed
    assert not entity._early_expiry_retry_task


async def test_checkout_transitions_when_expiry_update_fails(
    hass: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed shortening does not prevent the checked-out transition."""
    notices, timers = _patch_checkout_runtime(monkeypatch)
    now = dt_util.now()
    old_end = now + timedelta(hours=4)
    entity = _entity(hass, now - timedelta(days=1), old_end)
    entity._transition_to_checked_out = lambda **_kwargs: setattr(
        entity, "_state", CHECKIN_STATE_CHECKED_OUT
    )
    monkeypatch.setattr(
        checkout_expiry,
        "get_entry_data",
        lambda *_args: {EARLY_CHECKOUT_EXPIRY_SWITCH: SimpleNamespace(is_on=True)},
    )

    async def failed(*_args: Any, **_kwargs: Any) -> bool:
        """Fail without dispatch."""
        return False

    monkeypatch.setattr(checkout_expiry, "async_update_lock_code_expiry", failed)
    await runtime.async_checkout(entity, force=True)

    assert entity._state == CHECKIN_STATE_CHECKED_OUT
    assert entity._tracked_event_end == old_end
    assert notices.created
    assert timers


async def test_legacy_service_exception_is_not_success(
    hass: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit legacy service exception is reported as an unsuccessful update."""
    now = dt_util.now()
    old_end = now + timedelta(hours=4)
    start = now - timedelta(days=1)
    entity = _entity(hass, start, old_end)
    dispatched = False

    async def service_failure() -> None:
        """Emulate a failing Home Assistant service call."""
        raise HomeAssistantError("service failed")

    def add_failing_call(
        _hass: Any,
        coroutines: list[Any],
        *_args: Any,
        **_kwargs: Any,
    ) -> list[Any]:
        """Append the explicit failing request to gather."""
        coroutines.append(service_failure())
        return coroutines

    monkeypatch.setattr(
        checkout_expiry, "coordinator_keymaster_runtime_enabled", lambda *_: True
    )
    monkeypatch.setattr(checkout_expiry, "protected_registry_active", lambda *_: False)
    monkeypatch.setattr(
        checkout_expiry, "association_requires_verified_routing", lambda *_: False
    )
    monkeypatch.setattr(
        checkout_expiry,
        "import_module",
        lambda *_: SimpleNamespace(add_call=add_failing_call),
    )

    def mark_dispatched() -> None:
        """Record dispatch before service invocation."""
        nonlocal dispatched
        dispatched = True

    result = await checkout_expiry.async_update_lock_code_expiry(
        entity,
        runtime.compute_early_expiry_time(now, old_end),
        identity_end=old_end,
        on_dispatch=mark_dispatched,
    )

    assert dispatched
    assert not result
