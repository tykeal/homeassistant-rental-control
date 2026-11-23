# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Native-empty Keymaster text is narrowly scoped to verified operations."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest

from custom_components.rental_control.keymaster_identity import CapabilityIdentity
from custom_components.rental_control.keymaster_identity import CapabilityResolution
from custom_components.rental_control.keymaster_identity import CapabilityStatus
from custom_components.rental_control.keymaster_operation_models import (
    _SET_REQUIRED_PROPERTIES,
)
from custom_components.rental_control.keymaster_operation_models import (
    _AuthorizationInputs,
)
from custom_components.rental_control.keymaster_operation_validation import (
    _matching_fence,
)
from custom_components.rental_control.keymaster_operation_validation import (
    _native_empty_text_capability,
)
from custom_components.rental_control.keymaster_operation_validation import (
    _native_empty_text_policy,
)

_REQUIRED_SET = tuple(sorted(key.format(slot=7) for key in _SET_REQUIRED_PROPERTIES))


def _fence() -> dict[str, object]:
    """Build one valid exact allocation fence for lookup tests."""
    return {
        "fence_id": "f" * 32,
        "original_km_entry_id": "km-a",
        "slot": 7,
        "rc_entry_id": "rc-a",
        "owner_ref": "owner-a",
        "allocation_ref": "allocation-a",
        "created_transaction_id": "c" * 32,
        "created_generation": 3,
        "fence_type": "allocation",
    }


@pytest.mark.parametrize(
    ("fence_type", "properties", "requested", "expected"),
    (
        ("pending_clear", (), False, True),
        ("allocation", (), False, False),
        ("allocation", _REQUIRED_SET, True, True),
        ("allocation", _REQUIRED_SET[:-1], True, None),
        ("pending_set", _REQUIRED_SET, True, None),
    ),
)
def test_native_empty_policy_is_limited_to_exact_clear_or_set(
    fence_type: str,
    properties: tuple[str, ...],
    requested: bool,
    expected: bool | None,
) -> None:
    """Only a matching fence and the complete set capability suite permit it."""
    assert _native_empty_text_policy(fence_type, 7, properties, requested) is expected


@pytest.mark.parametrize(
    ("domain", "property_key", "slot", "entry_id", "generation", "allowed"),
    (
        ("text", "text.code_slots:7.name", 7, "km-a", 3, True),
        ("text", "text.code_slots:8.name", 8, "km-a", 3, False),
        ("switch", "switch.code_slots:7.enabled", 7, "km-a", 3, False),
        ("text", "text.code_slots:7.pin", 7, "km-b", 3, False),
        ("text", "text.code_slots:7.name", 7, "km-a", 4, False),
    ),
)
def test_native_empty_capability_requires_exact_live_identity(
    domain: str,
    property_key: str,
    slot: int,
    entry_id: str,
    generation: int,
    allowed: bool,
) -> None:
    """Unknown text is not generalized to placeholders or other slot addresses."""
    resolution = CapabilityResolution(
        CapabilityIdentity(
            keymaster_entry_id=entry_id,
            platform="keymaster",
            domain=domain,
            property_key=property_key,
            slot=slot,
            unique_id="exact-unique-id",
            generation=generation,
        ),
        CapabilityStatus.UNKNOWN_STATE,
        entity_id=f"{domain}.code_slots:{slot}.name",
    )

    assert (
        _native_empty_text_capability(
            resolution,
            slot=7,
            keymaster_entry_id="km-a",
            generation=3,
            allow_native_empty_text=True,
        )
        is allowed
    )
    assert not _native_empty_text_capability(
        resolution,
        slot=7,
        keymaster_entry_id="km-a",
        generation=3,
        allow_native_empty_text=False,
    )


@pytest.mark.parametrize("duplicate", [False, True])
def test_operation_fence_lookup_requires_one_exact_valid_record(
    duplicate: bool,
) -> None:
    """Missing, malformed, mismatched, or duplicate fences cannot authorize."""
    fence = _fence()
    fences: list[object] = [fence]
    if duplicate:
        fences.append(dict(fence))
    inputs = cast(
        _AuthorizationInputs,
        SimpleNamespace(
            loaded=SimpleNamespace(data={"pending_fences": fences}),
            keymaster_entry=SimpleNamespace(entry_id="km-a"),
        ),
    )

    match = _matching_fence(
        inputs,
        "rc-a",
        7,
        "owner-a",
        "allocation-a",
        "allocation",
    )
    wrong_slot = _matching_fence(
        inputs,
        "rc-a",
        8,
        "owner-a",
        "allocation-a",
        "allocation",
    )

    assert (match is None) is duplicate
    assert wrong_slot is None

    malformed = dict(fence)
    malformed["slot"] = True
    inputs.loaded.data["pending_fences"] = [malformed]
    assert (
        _matching_fence(
            inputs,
            "rc-a",
            7,
            "owner-a",
            "allocation-a",
            "allocation",
        )
        is None
    )
