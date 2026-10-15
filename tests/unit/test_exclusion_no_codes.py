# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""No slot-exclusion surface may ever disclose a door code."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from typing import cast

import pytest

from custom_components.rental_control.allocator.allocator import DoorCodeAllocator
from custom_components.rental_control.allocator.diagnostics import allocator_diagnostics
from custom_components.rental_control.allocator.models import AllocationOrigin
from custom_components.rental_control.allocator.models import AllocationOwner
from custom_components.rental_control.allocator.models import AllocationRecord
from custom_components.rental_control.allocator.models import CycleObservation
from custom_components.rental_control.const import CONF_EXCLUDED_SLOTS
from custom_components.rental_control.coordinator_helpers import code_allocation
from custom_components.rental_control.coordinator_helpers import slot_exclusions

_CODE = "8642"
_DEAD_SLOT = 11
_NOW = "2026-10-15T12:00:00+00:00"


def _hass() -> Any:
    """Build a hass stand-in whose entry excludes a slot by its code."""
    entry = SimpleNamespace(
        entry_id="entry-a",
        data={CONF_EXCLUDED_SLOTS: [{"slot": _DEAD_SLOT, "reason": "dead relay"}]},
        options={},
    )
    return SimpleNamespace(
        bus=SimpleNamespace(),
        data={},
        config=SimpleNamespace(config_dir="."),
        config_entries=SimpleNamespace(
            async_get_entry=lambda entry_id: entry if entry_id == "entry-a" else None,
            async_entries=lambda _domain=None: [entry],
        ),
    )


def test_diagnostics_report_slots_without_codes() -> None:
    """The diagnostics payload names slots and reasons, never codes."""
    hass = _hass()
    allocator = DoorCodeAllocator(cast(Any, hass))
    allocator._store = cast(Any, SimpleNamespace(async_save=lambda _registry: None))
    allocator._registry.records[_CODE] = AllocationRecord(
        code=_CODE,
        code_ref="ref-exclusion",
        encoding_salt_value="entry-a",
        owners=[
            AllocationOwner(
                entry_id="entry-a",
                identity_key="identity-a",
                origin=AllocationOrigin.ADOPTED,
                lockname="front",
                slot=_DEAD_SLOT,
                lock_observed=True,
                first_seen=_NOW,
                last_seen=_NOW,
            )
        ],
        code_length=4,
    )
    allocator._registry.rebuild_index()

    diagnostics = allocator_diagnostics(allocator)

    assert diagnostics["excluded_slots"] == {
        "entry-a": [{"slot": _DEAD_SLOT, "reason": "dead relay"}]
    }
    assert _CODE not in str(diagnostics)


def test_notification_quotes_only_the_operator_reason() -> None:
    """The notification repeats the operator's note and nothing else."""
    messages: list[str] = []
    hass = SimpleNamespace(bus=SimpleNamespace())

    original_dismiss = slot_exclusions.async_dismiss
    original = slot_exclusions.async_create
    try:
        slot_exclusions.async_dismiss = (  # type: ignore[assignment]
            lambda *_args, **_kwargs: None
        )
        slot_exclusions.async_create = (  # type: ignore[assignment]
            lambda _hass, message, title="", notification_id="": messages.append(
                message
            )
        )
        slot_exclusions.report_excluded_slots(
            hass, "entry-a", "front", {_DEAD_SLOT: "dead relay"}
        )
    finally:
        slot_exclusions.async_create = original  # type: ignore[assignment]
        slot_exclusions.async_dismiss = (  # type: ignore[assignment]
            original_dismiss
        )

    assert messages
    assert "dead relay" in messages[0]
    assert _CODE not in messages[0]


def test_revalidation_logs_name_slots_not_codes(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Clearing an exclusion logs the slot and cause, never a code."""
    hass = _hass()
    observation = CycleObservation(
        entry_id="entry-a",
        lockname="front",
        managed_slots=frozenset({10, _DEAD_SLOT}),
        observed_codes={_CODE: _DEAD_SLOT},
        unreadable_slots=frozenset(),
    )
    hass.config_entries.async_update_entry = lambda _entry, **_kwargs: None
    monkeypatch.setattr(
        slot_exclusions, "async_dismiss", lambda *_args, **_kwargs: None
    )

    with caplog.at_level(logging.INFO):
        kept = code_allocation._revalidated_exclusions(
            hass, "entry-a", "front", observation
        )

    assert kept == {}
    assert any("readable again" in record.getMessage() for record in caplog.records)
    assert all(_CODE not in record.getMessage() for record in caplog.records)
