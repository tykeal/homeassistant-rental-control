# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for allocator orphan cleanup services."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from typing import cast

from homeassistant.core import HomeAssistant
import pytest

from custom_components.rental_control.allocator import services as services_module
from custom_components.rental_control.allocator.allocator import DoorCodeAllocator
from custom_components.rental_control.allocator.models import AdoptionRequest
from custom_components.rental_control.allocator.models import AllocationRequest
from custom_components.rental_control.allocator.models import CycleObservation
from custom_components.rental_control.allocator.models import OrphanCleanupReport
from custom_components.rental_control.allocator.models import OrphanOutcome
from custom_components.rental_control.allocator.services import (
    SERVICE_CLEAR_ORPHANED_CODES,
)
from custom_components.rental_control.allocator.services import (
    register_allocator_services,
)
from custom_components.rental_control.const import ALLOCATOR
from custom_components.rental_control.const import DOMAIN


async def test_service_registers_once(hass: HomeAssistant) -> None:
    """The cleanup service is registered only once for the domain."""
    register_allocator_services(hass)
    register_allocator_services(hass)

    assert hass.services.has_service(DOMAIN, SERVICE_CLEAR_ORPHANED_CODES)


async def test_service_dry_run_changes_nothing(
    hass: HomeAssistant,
) -> None:
    """A dry-run cleanup reports releasable owners without mutating state."""
    allocator = _allocator(hass)
    await _allocate(allocator, "orphan-entry", "identity-a", "2468", None, None)
    hass.data.setdefault(DOMAIN, {})[ALLOCATOR] = allocator
    register_allocator_services(hass)

    response = await hass.services.async_call(
        DOMAIN,
        SERVICE_CLEAR_ORPHANED_CODES,
        {"dry_run": True},
        blocking=True,
        return_response=True,
    )

    assert response["dry_run"] is True
    assert response["cleared"] == [
        {
            "code_ref": allocator.code_ref("2468"),
            "entry_id": "orphan-entry",
            "identity_key": "identity-a",
        }
    ]
    assert allocator._registry.code_for_identity("identity-a") == "2468"


async def test_service_dry_run_preserves_observed_state(
    hass: HomeAssistant,
) -> None:
    """A dry-run cleanup does not mutate lock observation metadata."""
    allocator = _allocator(hass)
    await _allocate(allocator, "orphan-entry", "identity-a", "2468", "front", 1)
    owner = allocator._registry.records["2468"].owners[0]
    owner.lock_observed = True

    report = await allocator.async_clear_orphans(
        set(),
        [
            CycleObservation(
                entry_id="live",
                lockname="front",
                managed_slots=frozenset({1}),
                observed_codes={},
                unreadable_slots=frozenset(),
            )
        ],
        dry_run=True,
    )

    assert [outcome.identity_key for outcome in report.cleared] == ["identity-a"]
    assert owner.lock_observed is True
    assert allocator._registry.code_for_identity("identity-a") == "2468"


@pytest.mark.parametrize(
    ("observation", "reason"),
    [
        (
            CycleObservation(
                entry_id="live",
                lockname="front",
                managed_slots=frozenset({1}),
                observed_codes={"1357": 1},
                unreadable_slots=frozenset(),
            ),
            "code_still_programmed",
        ),
        (
            CycleObservation(
                entry_id="live",
                lockname="front",
                managed_slots=frozenset({1}),
                observed_codes={},
                unreadable_slots=frozenset({1}),
            ),
            "code_still_programmed",
        ),
    ],
)
async def test_service_retains_still_programmed_orphans(
    hass: HomeAssistant,
    observation: CycleObservation,
    reason: str,
) -> None:
    """Observed and unreadable slots both retain orphaned allocations."""
    allocator = _allocator(hass)
    await _allocate(allocator, "orphan-entry", "identity-a", "1357", "front", 1)

    report = await allocator.async_clear_orphans(set(), [observation])

    assert _reasons(report) == [reason]
    assert allocator._registry.code_for_identity("identity-a") == "1357"


def test_report_dismisses_stale_notification(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clean cleanup report dismisses any previous orphan warning."""
    dismissed = []
    created = []
    monkeypatch.setattr(
        services_module,
        "async_dismiss",
        lambda _hass, notification_id: dismissed.append(notification_id),
    )
    monkeypatch.setattr(
        services_module,
        "async_create",
        lambda *_args, **_kwargs: created.append(_args),
    )

    services_module.report_orphan_cleanup(hass, OrphanCleanupReport(dry_run=False))

    assert dismissed == [services_module._ORPHAN_NOTIFICATION_ID]
    assert created == []


def test_report_dry_run_suppresses_notification(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dry-run cleanup reports do not mutate persistent notifications."""
    created = []
    dismissed = []
    monkeypatch.setattr(
        services_module,
        "async_create",
        lambda *_args, **_kwargs: created.append(_args),
    )
    monkeypatch.setattr(
        services_module,
        "async_dismiss",
        lambda _hass, notification_id: dismissed.append(notification_id),
    )

    services_module.report_orphan_cleanup(
        hass,
        OrphanCleanupReport(
            dry_run=True,
            retained=[
                OrphanOutcome(
                    code_ref="abc12345",
                    entry_id="orphan",
                    identity_key="identity",
                    reason="unverifiable_lock",
                )
            ],
        ),
    )

    assert created == []
    assert dismissed == []


async def test_service_retains_unverifiable_and_conflict_orphans(
    hass: HomeAssistant,
) -> None:
    """Unobserved locks and conflict records are retained with reasons."""
    allocator = _allocator(hass)
    await _allocate(allocator, "orphan-entry", "identity-a", "1357", "front", 1)
    await allocator.async_adopt(
        AdoptionRequest(
            entry_id="other-orphan",
            identity_key="identity-b",
            code="2468",
            code_length=4,
            lockname="front",
            slot=2,
        )
    )
    await allocator.async_adopt(
        AdoptionRequest(
            entry_id="third-orphan",
            identity_key="identity-c",
            code="2468",
            code_length=4,
            lockname="front",
            slot=3,
        )
    )

    report = await allocator.async_clear_orphans(set(), [])

    assert sorted(_reasons(report)) == [
        "adoption_conflict",
        "adoption_conflict",
        "unverifiable_lock",
    ]
    assert allocator._registry.code_for_identity("identity-a") == "1357"
    assert allocator._registry.code_for_identity("identity-b") == "2468"


async def test_service_releases_lockless_and_is_idempotent(
    hass: HomeAssistant,
) -> None:
    """Lockless orphans are cleared immediately and later calls no-op."""
    allocator = _allocator(hass)
    await _allocate(allocator, "orphan-entry", "identity-a", "1357", None, None)

    first = await allocator.async_clear_orphans(set(), [])
    second = await allocator.async_clear_orphans(set(), [])

    assert [outcome.code_ref for outcome in first.cleared] == [
        allocator.code_ref("1357")
    ]
    assert first.retained == []
    assert second.cleared == []
    assert second.retained == []
    assert allocator._registry.records == {}


async def test_orphan_report_and_logs_do_not_expose_codes(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Cleanup reports and log records expose code refs, never raw codes."""
    allocator = _allocator(hass)
    await _allocate(allocator, "orphan-entry", "identity-a", "1357", None, None)
    caplog.set_level(logging.INFO)

    report = await allocator.async_clear_orphans(set(), [])

    for outcome in report.cleared + report.retained:
        assert outcome.code_ref == allocator.code_ref("1357")
        assert outcome.entry_id != "1357"
        assert outcome.identity_key != "1357"
        assert outcome.reason != "1357"
    for record in caplog.records:
        assert record.msg != "1357"
        assert "1357" not in tuple(str(arg) for arg in record.args)


def _allocator(hass: HomeAssistant) -> DoorCodeAllocator:
    """Build an allocator with persistence disabled."""
    allocator = DoorCodeAllocator(hass)
    allocator._store = cast(Any, SimpleNamespace(async_save=lambda _registry: None))
    return allocator


async def _allocate(
    allocator: DoorCodeAllocator,
    entry_id: str,
    identity_key: str,
    code: str,
    lockname: str | None,
    slot: int | None,
) -> None:
    """Allocate one code for service tests."""
    await allocator.async_allocate(
        AllocationRequest(
            entry_id=entry_id,
            identity_key=identity_key,
            preferred_code=code,
            code_length=len(code),
            lockname=lockname,
            slot=slot,
        )
    )


def _reasons(report: OrphanCleanupReport) -> list[str]:
    """Return cleanup retention reasons."""
    return [outcome.reason or "" for outcome in report.retained]
