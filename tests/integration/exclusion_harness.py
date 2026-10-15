# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Shared fixtures for the slot-exclusion end-to-end scenarios.

The harness deliberately gives every reservation a fresh identity with no
existing registry allocation, so a cycle that issues a code must have passed
the allocator gate rather than reusing an adopted one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from typing import cast

from homeassistant.util import dt as dt_util
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
from custom_components.rental_control.reconciliation import Reservation
from custom_components.rental_control.reconciliation import SlotStatus

DEAD_SLOT = 11
START = datetime(2026, 9, 17, 16, tzinfo=dt_util.UTC)


@dataclass(frozen=True, slots=True)
class EntrySpec:
    """One configured Rental Control entry in a harness world."""

    entry_id: str
    lockname: str
    start_slot: int
    max_events: int = 2


class World:
    """A hass stand-in holding several entries and one shared allocator."""

    def __init__(self, specs: list[EntrySpec], monkeypatch: pytest.MonkeyPatch) -> None:
        """Build the entries, the hass double, and the singleton allocator."""
        self.specs = {spec.entry_id: spec for spec in specs}
        self.notifications = _capture_notifications(monkeypatch)
        self.entries = [
            SimpleNamespace(
                entry_id=spec.entry_id,
                data={
                    "keymaster_entry_id": f"km-{spec.lockname}",
                    "lockname": spec.lockname,
                    "start_slot": spec.start_slot,
                    "max_events": spec.max_events,
                },
                options={},
                title=spec.entry_id,
            )
            for spec in specs
        ]
        self.hass = cast(
            Any,
            SimpleNamespace(
                bus=SimpleNamespace(),
                data={DOMAIN: {}},
                config=SimpleNamespace(config_dir="."),
                config_entries=SimpleNamespace(
                    async_get_entry=self._get_entry,
                    async_entries=lambda _domain=None: list(self.entries),
                    async_update_entry=self._update_entry,
                ),
            ),
        )
        self.allocator = DoorCodeAllocator(self.hass)
        self.allocator._store = cast(
            Any, SimpleNamespace(async_save=lambda _registry: None)
        )
        self.hass.data[DOMAIN][ALLOCATOR] = self.allocator

    def _get_entry(self, entry_id: str) -> Any:
        """Return one entry by id, or None when it is not loaded."""
        for entry in self.entries:
            if entry.entry_id == entry_id:
                return entry
        return None

    def _update_entry(self, entry: Any, **kwargs: Any) -> None:
        """Apply a config-entry update the way Home Assistant would."""
        if "data" in kwargs:
            entry.data = dict(kwargs["data"])
        if "options" in kwargs:
            entry.options = dict(kwargs["options"])

    def entry(self, entry_id: str) -> Any:
        """Return the entry object for *entry_id*."""
        return self._get_entry(entry_id)

    def exclusions(self, entry_id: str) -> Any:
        """Return the raw stored exclusion payload for *entry_id*."""
        return dict(self.entry(entry_id).data).get(CONF_EXCLUDED_SLOTS)

    def exclude(self, entry_id: str, slot: int, reason: str | None = None) -> None:
        """Record a standing exclusion the way the service would."""
        entry = self.entry(entry_id)
        data = dict(entry.data)
        stored = list(data.get(CONF_EXCLUDED_SLOTS) or [])
        stored.append({"slot": slot, "reason": reason})
        data[CONF_EXCLUDED_SLOTS] = stored
        self._update_entry(entry, data=data)

    def save_options(self, entry_id: str) -> None:
        """Mimic an options-flow save rebuilding data from options."""
        entry = self.entry(entry_id)
        options = dict(entry.options) or {
            key: value
            for key, value in entry.data.items()
            if key != CONF_EXCLUDED_SLOTS
        }
        self._update_entry(entry, data=options, options={})

    def restart(self) -> None:
        """Rebuild the allocator the way a Home Assistant restart does.

        The registry survives because it is persisted, but the adoption gate
        is re-seeded from the configured entries, so every lock-backed entry
        is pending again until it completes a clean cycle.
        """
        registry = self.allocator._registry
        self.allocator = DoorCodeAllocator(self.hass)
        self.allocator._registry = registry
        self.allocator._store = cast(
            Any, SimpleNamespace(async_save=lambda _registry: None)
        )
        self.hass.data[DOMAIN][ALLOCATOR] = self.allocator

    async def cycle(
        self,
        entry_id: str,
        slots: list[ManagedSlot],
        reservations: list[Reservation],
        **kwargs: Any,
    ) -> Any:
        """Run one code-resolution cycle for *entry_id*."""
        spec = self.specs[entry_id]
        return await code_allocation.async_resolve_codes(
            CodeResolutionRequest(
                hass=self.hass,
                entry_id=entry_id,
                lockname=spec.lockname,
                code_length=4,
                managed_slots=slots,
                reservations=reservations,
                **kwargs,
            )
        )


def _capture_notifications(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Redirect persistent notifications into a dict keyed by id."""
    live: dict[str, str] = {}

    def _create(
        _hass: Any, message: str, title: str = "", notification_id: str = ""
    ) -> None:
        """Record one created notification."""
        live[notification_id] = message

    def _dismiss(_hass: Any, notification_id: str) -> None:
        """Forget one dismissed notification."""
        live.pop(notification_id, None)

    for module in (code_allocation, slot_exclusions):
        monkeypatch.setattr(module, "async_create", _create)
        monkeypatch.setattr(module, "async_dismiss", _dismiss)
    return live


def single_entry_world(monkeypatch: pytest.MonkeyPatch, start_slot: int = 10) -> World:
    """Build a one-entry world whose range holds the dead slot."""
    return World([EntrySpec("entry-a", "front", start_slot, max_events=2)], monkeypatch)


def three_entry_world(monkeypatch: pytest.MonkeyPatch) -> World:
    """Build three entries on three locks, each with its own slot range."""
    return World(
        [
            EntrySpec("entry-a", "front", 10),
            EntrySpec("entry-b", "side", 20),
            EntrySpec("entry-c", "rear", 30),
        ],
        monkeypatch,
    )


def free_slot(slot: int) -> ManagedSlot:
    """Build a confirmed-empty managed slot."""
    return ManagedSlot(
        slot=slot,
        managed=True,
        status=SlotStatus.FREE,
        actual_name=None,
        actual_code=None,
        actual_code_present=False,
    )


def unknown_slot(slot: int) -> ManagedSlot:
    """Build a managed slot whose physical state cannot be read."""
    return ManagedSlot(
        slot=slot,
        managed=True,
        status=SlotStatus.UNKNOWN,
        actual_name=None,
        actual_code=None,
        actual_code_present=None,
    )


def occupied_slot(slot: int, code: str, name: str = "RC Test Guest") -> ManagedSlot:
    """Build a readable managed slot holding *code*."""
    return ManagedSlot(
        slot=slot,
        managed=True,
        status=SlotStatus.OCCUPIED,
        actual_name=name,
        actual_code=code,
        actual_code_present=True,
        actual_start=START,
        actual_end=START + timedelta(days=3),
    )


def reservation(identity_key: str, code: str | None = "1234") -> Reservation:
    """Build an eligible reservation with no prior registry allocation.

    The preferred *code* stands in for the calendar generator's output; the
    allocator still has to clear its gate before anything is issued.
    """
    return Reservation(
        identity_key=identity_key,
        start=START,
        end=START + timedelta(days=3),
        buffered_start=START,
        buffered_end=START + timedelta(days=3),
        summary="Reserved: Test Guest",
        slot_name="Test Guest",
        display_slot_name="RC Test Guest",
        slot_code=code,
    )
