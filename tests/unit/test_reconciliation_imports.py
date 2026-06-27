# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Compatibility tests for the reconciliation package root."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
import importlib

import pytest

import custom_components.rental_control.reconciliation as root
from custom_components.rental_control.reconciliation.desired import DesiredPlanRequest

_OWNER_SYMBOLS = {
    "ActionKind": "enums",
    "CacheOnlyStoreRecord": "store_models",
    "DesiredPlan": "plan_models",
    "DesiredReservation": "stateless_models",
    "FINGERPRINT_VERSION": "enums",
    "ManagedSlot": "plan_models",
    "ObservedSlot": "stateless_models",
    "ObservedSlotStatus": "enums",
    "PlannedSlot": "plan_models",
    "RematchKind": "rematch_models",
    "RematchResult": "rematch_models",
    "Reservation": "plan_models",
    "SlotAction": "action_models",
    "SlotMapping": "store_models",
    "SlotStatus": "enums",
    "StatelessPlan": "stateless_models",
    "StoredActual": "store_models",
    "StoredIdentity": "store_models",
    "compute_desired_plan": "desired",
    "compute_stateless_plan": "stateless",
    "extract_booking_aliases": "identity",
    "find_reservation_rematch": "rematch",
    "make_reservation_fingerprint": "identity",
    "normalize_slot_name_for_fingerprint": "identity",
}


def test_root_reexports_full_compatibility_surface() -> None:
    """Root symbols are the exact objects from their owner modules."""
    for symbol, module_name in _OWNER_SYMBOLS.items():
        module = importlib.import_module(
            f"custom_components.rental_control.reconciliation.{module_name}"
        )
        assert getattr(root, symbol) is getattr(module, symbol)


def test_compute_desired_plan_legacy_and_request_calls_match() -> None:
    """Legacy calls and request-object calls produce identical empty plans."""
    generated_at = datetime(2026, 7, 1, tzinfo=timezone.utc)
    legacy = root.compute_desired_plan(
        [],
        [],
        2,
        "plan-a",
        generated_at,
        entry_id="entry",
        lockname="front",
        start_slot=1,
    )
    request = DesiredPlanRequest(
        reservations=[],
        managed_slots=[],
        max_events=2,
        plan_id="plan-a",
        generated_at=generated_at,
        entry_id="entry",
        lockname="front",
        start_slot=1,
    )
    direct = root.compute_desired_plan(request)
    assert legacy == direct
    assert legacy.diagnostics["entry_id"] == "entry"
    assert legacy.diagnostics["lockname"] == "front"
    assert legacy.diagnostics["start_slot"] == 1


def test_compute_desired_plan_rejects_bad_context() -> None:
    """Unknown context keywords fail loudly instead of being ignored."""
    with pytest.raises(TypeError, match="Unknown compute_desired_plan context"):
        root.compute_desired_plan(
            [], [], 1, "plan", datetime.now(timezone.utc), bogus=True
        )


def test_production_callers_import_without_rewrites() -> None:
    """Production modules still import reconciliation symbols from the root."""
    assert importlib.import_module("custom_components.rental_control.coordinator")
    assert importlib.import_module("custom_components.rental_control.event_overrides")
    assert importlib.import_module("custom_components.rental_control.sensors.calsensor")
