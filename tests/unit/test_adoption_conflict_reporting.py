# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Reporting rules for shared door codes with more than one owner.

A property running one Rental Control entry per unit with the
``last_four`` generator legitimately hands one guest the same code in
two entries, because the code *is* that guest's phone digits.  These
tests pin the notification to the cases a user can act on, and pin the
notification text to the names a user recognises (issue #857).
"""

from __future__ import annotations

from types import MappingProxyType
from types import SimpleNamespace
from typing import Any
from typing import cast

import pytest

from custom_components.rental_control.allocator import adoption
from custom_components.rental_control.allocator import allocator as allocator_module
from custom_components.rental_control.allocator.allocator import DoorCodeAllocator
from custom_components.rental_control.allocator.models import AdoptionRequest

_CODE = "4321"
_OTHER_CODE = "8765"


class _Notifications:
    """Collect the notifications the allocator would have raised."""

    def __init__(self) -> None:
        """Start with no recorded notifications."""
        self.calls: list[dict[str, Any]] = []

    def __call__(self, _hass: Any, message: str, **kwargs: Any) -> None:
        """Record one notification call."""
        self.calls.append({"message": message, **kwargs})


def _hass(**generators: str | None) -> Any:
    """Build a hass whose entries use the named code generators.

    ``None`` registers no entry at all, which models an entry that has
    been deleted while its codes are still programmed in a lock. Entry
    data is a ``MappingProxyType`` exactly as Home Assistant exposes it,
    so the mapping-aware read is exercised against the real type.
    """
    entries = {
        entry_id: SimpleNamespace(
            entry_id=entry_id,
            title=f"Unit {entry_id[-1].upper()}",
            disabled_by=None,
            data=MappingProxyType(
                {"keymaster_entry_id": "front", "code_generation": generator}
            ),
            options=MappingProxyType({}),
        )
        for entry_id, generator in generators.items()
        if generator is not None
    }
    hass = SimpleNamespace()
    hass.data = {}
    hass.config = SimpleNamespace(config_dir=".")
    hass.config_entries = SimpleNamespace(
        async_entries=lambda _domain=None: list(entries.values()),
        async_get_entry=entries.get,
    )
    return hass


def _allocator(hass: Any) -> DoorCodeAllocator:
    """Build an allocator with persistence disabled for unit tests."""
    allocator = DoorCodeAllocator(hass)
    allocator._store = cast(Any, SimpleNamespace(async_save=lambda _registry: None))
    allocator._pending_adoption = set()
    return allocator


def _adopt(
    allocator: DoorCodeAllocator,
    entry_id: str,
    slot: int,
    code: str = _CODE,
    lockname: str = "front_door",
) -> None:
    """Adopt one observed code for one entry's slot."""
    adoption.adopt_unlocked(
        allocator,
        AdoptionRequest(
            entry_id=entry_id,
            identity_key=f"{entry_id}-guest",
            code=code,
            code_length=4,
            lockname=lockname,
            slot=slot,
        ),
    )


@pytest.fixture(name="notifications")
def _notifications(monkeypatch: pytest.MonkeyPatch) -> _Notifications:
    """Capture allocator notifications instead of raising them."""
    recorder = _Notifications()
    monkeypatch.setattr(allocator_module, "async_create", recorder)
    return recorder


def test_last_four_sharing_logs_without_notifying(
    notifications: _Notifications, caplog: pytest.LogCaptureFixture
) -> None:
    """A shared last_four code is the generator working, not a conflict."""
    caplog.set_level("INFO")
    allocator = _allocator(_hass(entry_a="last_four", entry_b="last_four"))
    _adopt(allocator, "entry_a", 3)
    _adopt(allocator, "entry_b", 5)

    assert notifications.calls == []
    assert "expected under the last_four generator" in caplog.text
    assert "entry_a-guest" in caplog.text


def test_date_based_sharing_still_notifies(notifications: _Notifications) -> None:
    """Two date_based entries on one code is a real allocator problem."""
    allocator = _allocator(_hass(entry_a="date_based", entry_b="date_based"))
    _adopt(allocator, "entry_a", 3)
    _adopt(allocator, "entry_b", 5)

    assert len(notifications.calls) == 1


def test_static_random_sharing_still_notifies(
    notifications: _Notifications,
) -> None:
    """Two static_random entries on one code is a real problem too."""
    allocator = _allocator(_hass(entry_a="static_random", entry_b="static_random"))
    _adopt(allocator, "entry_a", 3)
    _adopt(allocator, "entry_b", 5)

    assert len(notifications.calls) == 1


def test_mixed_generators_still_notify(notifications: _Notifications) -> None:
    """A date_based owner is not explained by the last_four generator."""
    allocator = _allocator(_hass(entry_a="last_four", entry_b="date_based"))
    _adopt(allocator, "entry_a", 3)
    _adopt(allocator, "entry_b", 5)

    assert len(notifications.calls) == 1


def test_undeterminable_generator_still_notifies(
    notifications: _Notifications,
) -> None:
    """A missing config entry must fail toward reporting, not silence."""
    allocator = _allocator(_hass(entry_a="last_four", entry_b=None))
    _adopt(allocator, "entry_a", 3)
    _adopt(allocator, "entry_b", 5)

    assert len(notifications.calls) == 1
