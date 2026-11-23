# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Verify local clear evidence and original-Keymaster release guards."""

from __future__ import annotations

from types import SimpleNamespace

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.const import STATE_UNKNOWN
import pytest

from custom_components.rental_control.allocator.models import AllocationOrigin
from custom_components.rental_control.allocator.models import AllocationOwner
from custom_components.rental_control.allocator.models import AllocationRecord
from custom_components.rental_control.allocator.models import CycleObservation
from custom_components.rental_control.allocator.models import ForcedReleaseExemption
from custom_components.rental_control.allocator.reissue import forced_release_hold_key
from custom_components.rental_control.allocator.release_guard import (
    release_guard_reason,
)
from custom_components.rental_control.helpers import is_cleared_keymaster_text_state
from custom_components.rental_control.helpers import is_unreadable_keymaster_text_state
from custom_components.rental_control.keymaster_services import (
    _classify_pin_after_reset,
)

_ENTRY = "clear-evidence-entry"
_KM_A = "known-keymaster-a"
_CODE = "5731"
_SLOT = 7
_NOW = "2026-11-21T00:00:00+00:00"


def _record() -> AllocationRecord:
    """Build one single-owner allocation for release guard assertions."""
    return AllocationRecord(
        code=_CODE,
        code_ref="opaque-code-reference",
        encoding_salt_value=_ENTRY,
        owners=[
            AllocationOwner(
                entry_id=_ENTRY,
                identity_key="guest-reservation",
                origin=AllocationOrigin.ADOPTED,
                lockname="lock.front",
                slot=_SLOT,
                lock_observed=True,
                first_seen=_NOW,
                last_seen=_NOW,
            )
        ],
    )


def _observation(
    *,
    entry_id: str = _ENTRY,
    keymaster_entry_id: str | None = _KM_A,
    generation: int | None = 12,
    observed_codes: dict[str, int] | None = None,
) -> CycleObservation:
    """Build a readable physical observation with explicit source identity."""
    return CycleObservation(
        entry_id=entry_id,
        lockname="lock.front",
        managed_slots=frozenset({_SLOT}),
        observed_codes=observed_codes or {},
        unreadable_slots=frozenset(),
        keymaster_entry_id=keymaster_entry_id,
        keymaster_generation=generation,
    )


@pytest.mark.parametrize(
    ("state", "unconfirmed", "lingering"),
    [
        ("", False, False),
        ("   ", False, False),
        ("None", False, False),
        (STATE_UNKNOWN, False, False),
        (STATE_UNAVAILABLE, True, False),
        ("5731", False, True),
    ],
)
def test_pin_clear_requires_readable_local_empty_state(
    state: str, unconfirmed: bool, lingering: bool
) -> None:
    """Local empty representations are accepted while unavailable is not."""
    entity_id = f"text.front_code_slot_{_SLOT}_pin"
    coordinator = SimpleNamespace(
        hass=SimpleNamespace(
            states=SimpleNamespace(
                get=lambda requested: (
                    SimpleNamespace(state=state) if requested == entity_id else None
                )
            )
        ),
        lockname="front",
    )
    deps = SimpleNamespace(
        is_unreadable_text=is_unreadable_keymaster_text_state,
        is_cleared_text=is_cleared_keymaster_text_state,
    )

    result = _classify_pin_after_reset(coordinator, _SLOT, deps)

    assert result == (unconfirmed, lingering)


def test_missing_pin_state_is_not_clear_evidence(hass) -> None:
    """An absent entity cannot confirm local clear."""
    result = _classify_pin_after_reset(
        SimpleNamespace(hass=hass, lockname="front"),
        _SLOT,
        SimpleNamespace(
            is_unreadable_text=is_unreadable_keymaster_text_state,
            is_cleared_text=is_cleared_keymaster_text_state,
        ),
    )
    assert result == (True, False)


def test_native_none_and_unknown_token_are_clear_but_unavailable_is_not() -> None:
    """Keymaster's native None publication may appear as HA ``unknown``."""
    assert is_cleared_keymaster_text_state(None)
    assert is_cleared_keymaster_text_state(STATE_UNKNOWN)
    assert not is_unreadable_keymaster_text_state(STATE_UNKNOWN)
    assert is_unreadable_keymaster_text_state(STATE_UNAVAILABLE)
    assert not is_cleared_keymaster_text_state(STATE_UNAVAILABLE)


def test_empty_observation_from_b_cannot_release_known_a_owner() -> None:
    """A same-name B observation is not evidence for the original A slot."""
    record = _record()
    reason = release_guard_reason(
        record,
        record.owners,
        [_observation(keymaster_entry_id="replacement-keymaster-b")],
        required_keymaster_identities={(_ENTRY, "guest-reservation"): (_KM_A, 12)},
    )
    assert reason == "unverifiable_original_keymaster"


def test_original_entry_and_current_generation_can_prove_only_local_absence() -> None:
    """Fresh A empty text allows allocator guard evaluation, not hardware proof."""
    record = _record()
    assert (
        release_guard_reason(
            record,
            record.owners,
            [_observation()],
            required_keymaster_identities={(_ENTRY, "guest-reservation"): (_KM_A, 12)},
        )
        is None
    )
    assert (
        release_guard_reason(
            record,
            record.owners,
            [_observation(observed_codes={_CODE: _SLOT})],
            required_keymaster_identities={(_ENTRY, "guest-reservation"): (_KM_A, 12)},
        )
        == "code_still_programmed"
    )


def test_observation_for_another_rc_entry_cannot_release_owner() -> None:
    """Protected evidence requires the original RC entry as well as Keymaster."""
    record = _record()
    reason = release_guard_reason(
        record,
        record.owners,
        [_observation(entry_id="other-rc-entry")],
        required_keymaster_identities={(_ENTRY, "guest-reservation"): (_KM_A, 12)},
    )
    assert reason == "unverifiable_original_keymaster"


def test_original_a_clear_does_not_bypass_adoption_conflict() -> None:
    """Exact A-local emptiness cannot erase a second live allocation owner."""
    record = _record()
    record.owners.append(
        AllocationOwner(
            entry_id="other-rc-entry",
            identity_key="other-reservation",
            origin=AllocationOrigin.ADOPTED,
            lockname="lock.front",
            slot=_SLOT + 1,
            lock_observed=True,
            first_seen=_NOW,
            last_seen=_NOW,
        )
    )

    reason = release_guard_reason(
        record,
        [record.owners[0]],
        [_observation()],
        required_keymaster_identities={(_ENTRY, "guest-reservation"): (_KM_A, 12)},
    )

    assert reason == "adoption_conflict"


def test_clear_evidence_releases_only_the_named_forced_hold_owner() -> None:
    """Local empty evidence cannot release a different owner with the hold."""
    hold_key = forced_release_hold_key("guest-reservation", _ENTRY, "lock.front", _SLOT)
    record = _record()
    record.owners[0].identity_key = hold_key
    record.owners.append(
        AllocationOwner(
            entry_id="other-entry",
            identity_key="other-reservation",
            origin=AllocationOrigin.ADOPTED,
            lockname="lock.front",
            slot=_SLOT + 1,
            lock_observed=True,
            first_seen=_NOW,
            last_seen=_NOW,
        )
    )

    reason = release_guard_reason(
        record,
        [record.owners[0]],
        [_observation()],
        forced_release=ForcedReleaseExemption(_CODE, _ENTRY, hold_key),
    )

    assert reason is None
    assert record.code == _CODE
    assert [owner.identity_key for owner in record.owners] == [
        hold_key,
        "other-reservation",
    ]


def test_redundant_clear_owner_can_retire_without_freeing_shared_pin() -> None:
    """A same-slot duplicate retires while the shared PIN remains occupied."""
    record = _record()
    redundant = AllocationOwner(
        entry_id=_ENTRY,
        identity_key="redundant-reservation",
        origin=AllocationOrigin.ADOPTED,
        lockname="lock.front",
        slot=_SLOT,
        lock_observed=True,
        first_seen=_NOW,
        last_seen=_NOW,
    )
    record.owners.append(redundant)

    reason = release_guard_reason(record, [record.owners[0]], [])

    assert reason is None
    record.owners.remove(record.owners[0])
    assert [owner.identity_key for owner in record.owners] == ["redundant-reservation"]
    assert record.code == _CODE
