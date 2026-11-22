# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Allocator re-issue previews preserve held codes without mutating state."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from custom_components.rental_control.allocator import reissue_preview
from custom_components.rental_control.allocator.allocator import DoorCodeAllocator
from custom_components.rental_control.allocator.models import AllocationOrigin
from custom_components.rental_control.allocator.models import AllocationOwner
from custom_components.rental_control.allocator.models import AllocationRecord
from custom_components.rental_control.allocator.models import ReissuePreviewRequest
from custom_components.rental_control.allocator.registry import AllocationRegistry
from custom_components.rental_control.allocator.reissue_holds import (
    forced_release_hold_key,
)

_NOW = "2026-11-21T00:00:00+00:00"


def _record(
    code: str,
    entry_id: str,
    identity_key: str,
    lockname: str | None,
    slot: int | None,
    *,
    code_length: int = 4,
) -> AllocationRecord:
    """Build one valid record with one owner for a preview scenario."""
    owner = AllocationOwner(
        entry_id=entry_id,
        identity_key=identity_key,
        origin=AllocationOrigin.PREFERRED,
        lockname=lockname,
        slot=slot,
        lock_observed=True,
        first_seen=_NOW,
        last_seen=_NOW,
    )
    return AllocationRecord(
        code=code,
        code_ref=f"masked-{code}",
        encoding_salt_value=entry_id,
        code_length=code_length,
        owners=[owner],
        created_at=_NOW,
        updated_at=_NOW,
    )


def _request(
    *,
    identity_key: str | None = "reservation-a",
    preferred_code: str | None = "1234",
    observed_code: str | None = "1234",
    code_length: int = 4,
) -> ReissuePreviewRequest:
    """Build a request for the original physical slot."""
    return ReissuePreviewRequest(
        entry_id="entry-a",
        identity_key=identity_key,
        preferred_code=preferred_code,
        code_length=code_length,
        lockname="front",
        slot=7,
        observed_code=observed_code,
    )


def _allocator(hass, records: dict[str, AllocationRecord]) -> DoorCodeAllocator:
    """Build an actual allocator around the supplied immutable test ledger."""
    allocator = DoorCodeAllocator(hass)
    allocator._registry = AllocationRegistry(
        records=records,
        code_ref_salt="preview-reference-salt",
    )
    allocator._registry_lost = False
    allocator._pending_adoption.clear()
    return allocator


async def test_preview_selects_alternative_and_keeps_replaced_code_held(hass) -> None:
    """A re-issue preview excludes the prior and observed physical code."""
    allocator = _allocator(
        hass,
        {"1234": _record("1234", "entry-a", "reservation-a", "front", 7)},
    )
    before = deepcopy(allocator._registry)

    preview = reissue_preview.preview_reissue(allocator, _request())

    assert preview.reason is None
    assert preview.replacement_code is not None
    assert preview.replacement_code != "1234"
    assert preview.replacement_code_ref == allocator.code_ref(preview.replacement_code)
    assert preview.origin is AllocationOrigin.COLLISION_RESOLVED
    assert preview.replaced_code_ref == allocator.code_ref("1234")
    assert allocator._registry == before


@pytest.mark.parametrize(
    ("pending", "lost", "identity_key", "preferred_code", "reason"),
    (
        ({"entry-a"}, False, "reservation-a", "1234", "adoption_pending"),
        (set(), True, "reservation-a", "1234", "recovery_fail_closed"),
        (set(), False, None, "1234", "clear_only"),
        (set(), False, "reservation-a", None, "no_existing_allocation"),
    ),
)
async def test_preview_reports_blocked_and_clear_only_states_without_mutation(
    hass,
    pending: set[str],
    lost: bool,
    identity_key: str | None,
    preferred_code: str | None,
    reason: str,
) -> None:
    """Read-only previews preserve the ledger for every non-allocation result."""
    allocator = _allocator(
        hass,
        {"1234": _record("1234", "entry-a", "reservation-a", "front", 7)},
    )
    allocator._pending_adoption = pending
    allocator._registry_lost = lost
    before = deepcopy(allocator._registry)

    preview = reissue_preview.preview_reissue(
        allocator,
        _request(
            identity_key=identity_key,
            preferred_code=preferred_code,
        ),
    )

    assert preview.reason == reason
    assert allocator._registry == before
    if reason == "clear_only":
        assert preview.replaced_code_ref == allocator.code_ref("1234")


async def test_preview_reports_exhaustion_when_all_codes_are_owned(hass) -> None:
    """A full one-digit namespace remains unavailable rather than overwriting."""
    records = {
        str(code): _record(
            str(code),
            f"other-entry-{code}",
            f"other-owner-{code}",
            None,
            None,
            code_length=1,
        )
        for code in range(10)
    }
    allocator = _allocator(hass, records)

    preview = reissue_preview.preview_reissue(
        allocator,
        _request(
            identity_key="new-owner",
            preferred_code="0",
            observed_code=None,
            code_length=1,
        ),
    )

    assert preview.reason == "exhausted"
    assert preview.replacement_code is None
    assert preview.replaced_code_ref is None
    assert set(allocator._registry.records) == {str(code) for code in range(10)}


async def test_forced_hold_matches_base_identity_and_physical_slot(hass) -> None:
    """Held owners are found by base identity or, for clear-only, exact slot."""
    hold_key = forced_release_hold_key("reservation-a", "entry-a", "front", 7)
    allocator = _allocator(
        hass,
        {"1234": _record("1234", "entry-a", hold_key, "front", 7)},
    )

    by_identity = reissue_preview.forced_hold_matches_target(
        allocator, "entry-a", "reservation-a", "renamed-front", 99
    )
    wrong_slot = reissue_preview.forced_hold_matches_target(
        allocator, "entry-a", None, "renamed-front", 7
    )
    by_slot = reissue_preview.forced_hold_matches_target(
        allocator, "entry-a", None, "front", 7
    )
    wrong_entry = reissue_preview.forced_hold_matches_target(
        allocator, "another-entry", "reservation-a", "front", 7
    )

    assert by_identity is not None
    assert by_identity.disposition == "held_pending_release"
    assert by_identity.lockname == "front"
    assert wrong_slot is None
    assert by_slot is not None
    assert wrong_entry is None
    assert (
        reissue_preview.forced_hold_matches_target(
            allocator, "entry-a", "different-reservation", "front", 7
        )
        is None
    )


def test_preview_identity_and_lockless_observation_are_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preview evidence binds current lifecycle identity and skips lockless reads."""
    monkeypatch.setattr(
        reissue_preview.cycle_requests,
        "verified_observation_identity",
        lambda _hass, _entry_id: ("selected-keymaster", 17),
    )

    identity = reissue_preview._observation_identity(object(), "entry-a")
    slots = reissue_preview.observe_managed_slots_without_mutation(
        SimpleNamespace(lockname=None)
    )

    assert identity == {
        "keymaster_entry_id": "selected-keymaster",
        "keymaster_generation": 17,
    }
    assert slots == []


def test_preview_hydrates_only_valid_persisted_mapping_fields() -> None:
    """Persisted history is copied into preview objects without sharing state."""
    reservation = SimpleNamespace(
        identity_key="reservation-a",
        fingerprint_history=set(),
        missing_count=4,
        published_once=False,
    )
    untouched = SimpleNamespace(
        identity_key="reservation-b",
        fingerprint_history=set(),
        missing_count=3,
        published_once=False,
    )

    reissue_preview.hydrate_reservations_from_mappings(
        [reservation, untouched],
        {
            "reservation-a": {
                "fingerprint_history": ["old-fingerprint", 7],
                "published_once": True,
            },
            "reservation-b": {
                "fingerprint_history": None,
                "published_once": "yes",
            },
        },
    )

    assert reservation.fingerprint_history == {"old-fingerprint"}
    assert reservation.missing_count == 0
    assert reservation.published_once
    assert untouched.fingerprint_history == set()
    assert untouched.missing_count == 0
    assert not untouched.published_once
