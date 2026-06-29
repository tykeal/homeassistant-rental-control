# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Focused parity tests for calendar sensor generated-code helpers."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
import random

from custom_components.rental_control.sensors.calsensor_helpers.codes import (
    generate_door_code,
)
from custom_components.rental_control.sensors.calsensor_helpers.models import (
    DoorCodeRequest,
)


def _request(
    generator: str,
    code_length: int = 4,
    uid: str | None = "uid-1",
    description: str | None = "Reservation details",
    last_four: str | None = None,
) -> DoorCodeRequest:
    """Build a representative door-code request."""
    return DoorCodeRequest(
        generator=generator,
        code_length=code_length,
        start=datetime(2025, 3, 15, 16, 0, tzinfo=timezone.utc),
        end=datetime(2025, 3, 20, 11, 0, tzinfo=timezone.utc),
        uid=uid,
        description=description,
        last_four=last_four,
    )


def test_date_based_truncation_and_zero_fill() -> None:
    """Verify date-based code ordering and length behavior."""
    assert generate_door_code(_request("date_based")) == "1520"
    assert generate_door_code(_request("date_based", code_length=6)) == "152003"


def test_last_four_only_applies_to_four_digit_codes() -> None:
    """Verify last-four generation is ignored for non-four-digit codes."""
    assert generate_door_code(_request("last_four", last_four="9876")) == "9876"
    assert (
        generate_door_code(_request("last_four", code_length=6, last_four="9876"))
        == "152003"
    )


def test_static_random_uid_determinism() -> None:
    """Verify static-random keeps UID-seeded deterministic behavior."""
    first = generate_door_code(_request("static_random", uid="same-uid"))
    second = generate_door_code(_request("static_random", uid="same-uid"))
    assert first == second
    assert len(first) == 4
    assert first.isdigit()


def test_static_random_description_fallback() -> None:
    """Verify static-random falls back to description when UID is absent."""
    code = generate_door_code(
        _request("static_random", uid=None, description="Fallback test")
    )
    random.seed("Fallback test")
    expected = str(random.randrange(1, int("9999".rjust(4, "9")), 4)).zfill(4)
    assert code == expected


def test_static_random_missing_seed_uses_date_based() -> None:
    """Verify missing UID and description fall through to date-based code."""
    assert (
        generate_door_code(_request("static_random", uid=None, description=None))
        == "1520"
    )


def test_empty_uid_falls_back_to_description_seed() -> None:
    """Verify empty UID uses the mutable description fallback as before."""
    code = generate_door_code(_request("static_random", uid="", description="Fallback"))
    random.seed("Fallback")
    expected = str(random.randrange(1, int("9999".rjust(4, "9")), 4)).zfill(4)
    assert code == expected
