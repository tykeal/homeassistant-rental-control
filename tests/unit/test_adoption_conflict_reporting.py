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
from custom_components.rental_control.allocator import code_origin
from custom_components.rental_control.allocator.allocator import DoorCodeAllocator
from custom_components.rental_control.allocator.models import AdoptionRequest
from custom_components.rental_control.const import COORDINATOR
from custom_components.rental_control.const import DOMAIN

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


def _hass(
    code_length: int = 4,
    phone_last_four: str | None = _CODE,
    *,
    with_reservations: bool = True,
    **generators: str | None,
) -> Any:
    """Build a hass whose entries use the named code generators.

    ``None`` registers no entry at all, which models an entry that has
    been deleted while its codes are still programmed in a lock. Entry
    data is a ``MappingProxyType`` exactly as Home Assistant exposes it,
    so the mapping-aware read is exercised against the real type.

    Each entry also gets a loaded coordinator whose reservations report
    ``phone_last_four`` for every identity ``_adopt`` can mint, because
    that is the only evidence a ``last_four`` code really came from a
    phone number. ``with_reservations=False`` leaves the coordinator
    out, which models an entry whose codes outlive its loaded state.
    """
    entries = {
        entry_id: SimpleNamespace(
            entry_id=entry_id,
            title=f"Unit {entry_id[-1].upper()}",
            disabled_by=None,
            data=MappingProxyType(
                {
                    "keymaster_entry_id": "front",
                    "code_generation": generator,
                    "code_length": code_length,
                }
            ),
            options=MappingProxyType({}),
        )
        for entry_id, generator in generators.items()
        if generator is not None
    }
    hass = SimpleNamespace()
    hass.data = {
        DOMAIN: {
            entry_id: {
                COORDINATOR: SimpleNamespace(
                    _latest_res_by_key={
                        f"{entry_id}-guest-{slot}": SimpleNamespace(
                            phone_last_four=phone_last_four
                        )
                        for slot in range(1, 11)
                    }
                )
            }
            for entry_id in generators
        }
        if with_reservations
        else {}
    }
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
    """Adopt one observed code for one entry's slot.

    The identity is derived from the slot so that one entry adopting
    two codes reads as two bookings rather than as one booking whose
    code changed, which is a different report entirely.
    """
    adoption.adopt_unlocked(
        allocator,
        AdoptionRequest(
            entry_id=entry_id,
            identity_key=f"{entry_id}-guest-{slot}",
            code=code,
            code_length=len(code),
            lockname=lockname,
            slot=slot,
        ),
    )


@pytest.fixture(autouse=True)
def _clean_cycle_evidence() -> Any:
    """Keep one test's in-flight evidence out of the next one."""
    code_origin.reset_cycle_evidence()
    yield
    code_origin.reset_cycle_evidence()


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
    assert "entry_a-guest-3" in caplog.text


def test_phoneless_last_four_sharing_still_notifies(
    notifications: _Notifications,
) -> None:
    """A last_four entry with no phone digits publishes date-based codes.

    Booking.com stopped supplying phone information, so both entries
    silently fell back to ``generate_date_based_code``. At a code length
    of four that code is only ``start_day + end_day``, so the collision
    is real and must be reported rather than explained away by a
    generator that never ran (issue #861).
    """
    allocator = _allocator(
        _hass(phone_last_four=None, entry_a="last_four", entry_b="last_four")
    )
    _adopt(allocator, "entry_a", 3)
    _adopt(allocator, "entry_b", 5)

    assert len(notifications.calls) == 1


def test_last_four_sharing_notifies_when_digits_differ(
    notifications: _Notifications,
) -> None:
    """Phone digits that are not the shared code explain nothing."""
    allocator = _allocator(
        _hass(phone_last_four="1111", entry_a="last_four", entry_b="last_four")
    )
    _adopt(allocator, "entry_a", 3)
    _adopt(allocator, "entry_b", 5)

    assert len(notifications.calls) == 1


def test_last_four_sharing_notifies_without_a_reservation(
    notifications: _Notifications,
) -> None:
    """An owner whose reservation cannot be read is undeterminable."""
    allocator = _allocator(
        _hass(with_reservations=False, entry_a="last_four", entry_b="last_four")
    )
    _adopt(allocator, "entry_a", 3)
    _adopt(allocator, "entry_b", 5)

    assert len(notifications.calls) == 1


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


def test_conflict_notification_id_is_per_code_ref(
    notifications: _Notifications,
) -> None:
    """Two conflicting codes must not overwrite one another's report."""
    allocator = _allocator(_hass(entry_a="date_based", entry_b="date_based"))
    _adopt(allocator, "entry_a", 3)
    _adopt(allocator, "entry_b", 5)
    _adopt(allocator, "entry_a", 4, code=_OTHER_CODE)
    _adopt(allocator, "entry_b", 6, code=_OTHER_CODE)

    ids = [call["notification_id"] for call in notifications.calls]
    assert all("conflict" in notification_id for notification_id in ids)
    assert len(ids) == 2
    assert len(set(ids)) == 2


def test_conflict_notification_names_lock_and_slot(
    notifications: _Notifications, caplog: pytest.LogCaptureFixture
) -> None:
    """The notification carries names; the log keeps the identifiers."""
    caplog.set_level("INFO")
    allocator = _allocator(_hass(entry_a="date_based", entry_b="date_based"))
    _adopt(allocator, "entry_a", 3)
    _adopt(allocator, "entry_b", 5)

    message = notifications.calls[0]["message"]
    assert "Unit A" in message
    assert "Unit B" in message
    assert "front_door" in message
    assert "slot 3" in message
    assert "slot 5" in message
    assert "entry_a-guest-3" not in message
    assert "entry_a:" not in message
    assert "entry_a-guest-3" in caplog.text


def test_long_last_four_sharing_still_notifies(
    notifications: _Notifications,
) -> None:
    """A six digit entry is date based however its generator is set.

    ``generate_slot_code`` uses the guest's phone digits only when the
    configured code length is four and silently falls back to date
    based codes otherwise, so a shared code between two six digit
    entries is a genuine collision and must still be reported.
    """
    allocator = _allocator(
        _hass(code_length=6, entry_a="last_four", entry_b="last_four")
    )
    _adopt(allocator, "entry_a", 3, code="432100")
    _adopt(allocator, "entry_b", 5, code="432100")

    assert len(notifications.calls) == 1


def test_in_flight_cycle_evidence_is_enough_to_stay_silent(
    notifications: _Notifications,
) -> None:
    """A booking's first cycle has evidence before the cache catches up.

    The coordinator's reservation cache is only refreshed once a cycle
    has completed, but conflicts are reported as owners are added, which
    is during the cycle.  Without reading the cycle in flight a genuinely
    phone-derived shared code would notify on its first cycle and stay
    notified, because nothing reports it again.
    """
    hass = _hass(with_reservations=False, entry_a="last_four", entry_b="last_four")
    for entry_id in ("entry_a", "entry_b"):
        code_origin.record_cycle_evidence(
            entry_id,
            [
                SimpleNamespace(
                    identity_key=f"{entry_id}-guest-{slot}", phone_last_four=_CODE
                )
                for slot in range(1, 11)
            ],
        )
    allocator = _allocator(hass)
    _adopt(allocator, "entry_a", 3)
    _adopt(allocator, "entry_b", 5)

    assert notifications.calls == []


def test_forgetting_an_entry_drops_its_cycle_evidence(
    notifications: _Notifications,
) -> None:
    """An unloaded entry stops vouching for codes it no longer owns."""
    hass = _hass(with_reservations=False, entry_a="last_four", entry_b="last_four")
    for entry_id in ("entry_a", "entry_b"):
        code_origin.record_cycle_evidence(
            entry_id,
            [
                SimpleNamespace(
                    identity_key=f"{entry_id}-guest-{slot}", phone_last_four=_CODE
                )
                for slot in range(1, 11)
            ],
        )
    code_origin.forget_entry("entry_b")
    allocator = _allocator(hass)
    _adopt(allocator, "entry_a", 3)
    _adopt(allocator, "entry_b", 5)

    assert len(notifications.calls) == 1
