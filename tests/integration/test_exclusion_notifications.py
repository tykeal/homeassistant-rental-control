# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Notification behaviour for standing slot exclusions."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from typing import cast

import pytest

from custom_components.rental_control.allocator.allocator import DoorCodeAllocator
from custom_components.rental_control.const import ALLOCATOR
from custom_components.rental_control.const import CONF_EXCLUDED_SLOTS
from custom_components.rental_control.const import DOMAIN
from custom_components.rental_control.coordinator_helpers import code_allocation
from custom_components.rental_control.coordinator_helpers import slot_exclusions
from custom_components.rental_control.coordinator_helpers.reissue import (
    CodeResolutionRequest,
)
from custom_components.rental_control.reconciliation import ManagedSlot
from custom_components.rental_control.reconciliation import SlotStatus

from tests.fixtures.fake_hass import fake_config

_DEAD_SLOT = 11


@pytest.fixture
def notifications(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Capture the persistent notifications the cycle reconciles."""
    live: dict[str, str] = {}

    def _create(
        _hass: Any, message: str, title: str = "", notification_id: str = ""
    ) -> None:
        """Record one created notification."""
        live[notification_id] = message

    def _dismiss(_hass: Any, notification_id: str) -> None:
        """Record one dismissed notification."""
        live.pop(notification_id, None)

    monkeypatch.setattr(code_allocation, "async_create", _create)
    monkeypatch.setattr(code_allocation, "async_dismiss", _dismiss)
    monkeypatch.setattr(slot_exclusions, "async_create", _create)
    monkeypatch.setattr(slot_exclusions, "async_dismiss", _dismiss)
    return live


def _hass(stored: list[dict[str, Any]] | None) -> Any:
    """Build a hass stand-in with one entry and a loaded allocator."""
    entry = SimpleNamespace(
        entry_id="entry-a",
        data={CONF_EXCLUDED_SLOTS: stored} if stored is not None else {},
        options={},
    )

    def _update(target: Any, **kwargs: Any) -> None:
        """Apply an entry data update in place."""
        target.data = kwargs.get("data", target.data)

    hass = SimpleNamespace(
        bus=SimpleNamespace(),
        data={DOMAIN: {}},
        config=fake_config(),
        config_entries=SimpleNamespace(
            async_get_entry=lambda entry_id: entry if entry_id == "entry-a" else None,
            async_entries=lambda _domain=None: [entry],
            async_update_entry=_update,
        ),
    )
    allocator = DoorCodeAllocator(cast(Any, hass))
    allocator._store = cast(Any, SimpleNamespace(async_save=lambda _registry: None))
    hass.data[DOMAIN][ALLOCATOR] = allocator
    return hass


def _slots(dead: bool = True) -> list[ManagedSlot]:
    """Observe the managed range with one optionally dead slot."""
    return [
        ManagedSlot(slot=10, managed=True, status=SlotStatus.FREE, actual_code=None),
        ManagedSlot(
            slot=_DEAD_SLOT,
            managed=True,
            status=SlotStatus.UNKNOWN if dead else SlotStatus.FREE,
            actual_code=None,
        ),
    ]


async def _cycle(hass: Any, slots: list[ManagedSlot]) -> None:
    """Run one code-resolution cycle for the entry."""
    await code_allocation.async_resolve_codes(
        CodeResolutionRequest(
            hass=hass,
            entry_id="entry-a",
            lockname="front",
            code_length=4,
            managed_slots=slots,
            reservations=[],
        )
    )


async def test_unexcluded_dead_slot_reports_only_the_block(
    notifications: dict[str, str],
) -> None:
    """Without an exclusion only the withheld-issuance notice stands."""
    await _cycle(_hass(None), _slots())

    assert set(notifications) == {
        code_allocation.unreadable_slots_notification_id("entry-a")
    }


async def test_excluded_slot_swaps_the_notification(
    notifications: dict[str, str],
) -> None:
    """An excluded slot reports its standing risk, not a block."""
    await _cycle(_hass([{"slot": _DEAD_SLOT, "reason": "dead relay"}]), _slots())

    assert set(notifications) == {
        slot_exclusions.excluded_slots_notification_id("entry-a")
    }
    message = notifications[slot_exclusions.excluded_slots_notification_id("entry-a")]
    assert "dead relay" in message
    assert "front" in message
    assert str(_DEAD_SLOT) in message


async def test_recovery_dismisses_the_exclusion_notification(
    notifications: dict[str, str],
) -> None:
    """When the slot recovers, the exclusion and its notice both clear."""
    hass = _hass([{"slot": _DEAD_SLOT, "reason": "dead relay"}])

    await _cycle(hass, _slots())
    await _cycle(hass, _slots(dead=False))

    assert notifications == {}
    assert slot_exclusions.load(hass, "entry-a") == {}


async def test_unreadable_notice_names_only_blocking_slots(
    notifications: dict[str, str],
) -> None:
    """A second dead slot is still reported while the first is excluded."""
    slots = [
        *_slots(),
        ManagedSlot(slot=12, managed=True, status=SlotStatus.UNKNOWN, actual_code=None),
    ]

    await _cycle(_hass([{"slot": _DEAD_SLOT}]), slots)

    message = notifications[code_allocation.unreadable_slots_notification_id("entry-a")]
    assert "slot 12" in message
    assert str(_DEAD_SLOT) not in message


def test_unload_and_removal_both_dismiss_the_notification() -> None:
    """Both teardown paths clear the standing exclusion notice."""
    source = Path("custom_components/rental_control/__init__.py").read_text(
        encoding="utf-8"
    )

    assert source.count("dismiss_excluded_slots(hass, config_entry.entry_id)") == 2


def test_dismissal_is_inert_without_an_event_bus() -> None:
    """A hass stand-in without a bus is never touched."""
    slot_exclusions.dismiss_excluded_slots(SimpleNamespace(), "entry-a")
    slot_exclusions.report_excluded_slots(
        SimpleNamespace(), "entry-a", "front", {1: None}
    )


async def test_withdrawing_the_last_exclusion_dismisses_the_notice(
    notifications: dict[str, str],
) -> None:
    """Clearing the last exclusion by hand clears its notification too."""
    hass = _hass([{"slot": _DEAD_SLOT, "reason": "dead relay"}])

    await _cycle(hass, _slots())
    assert set(notifications) == {
        slot_exclusions.excluded_slots_notification_id("entry-a")
    }

    entry = hass.config_entries.async_get_entry("entry-a")
    entry.data = {}

    await _cycle(hass, _slots())

    assert set(notifications) == {
        code_allocation.unreadable_slots_notification_id("entry-a")
    }
