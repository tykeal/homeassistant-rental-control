# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Generated door-code helpers for calendar sensors."""

from __future__ import annotations

from ...codegen import generate_date_based_code
from ...codegen import generate_slot_code
from ...codegen import generate_static_random_code
from .models import DoorCodeRequest


def _date_based_code(request: DoorCodeRequest) -> str:
    """Generate the legacy date-based door code."""
    return generate_date_based_code(request.code_length, request.start, request.end)


def _static_random_code(request: DoorCodeRequest) -> str | None:
    """Generate the legacy static-random door code when a seed exists."""
    return generate_static_random_code(
        request.code_length, request.description, request.uid
    )


def generate_door_code(request: DoorCodeRequest) -> str:
    """Generate a door code matching the legacy calendar sensor behavior."""
    return generate_slot_code(
        request.generator,
        request.code_length,
        request.start,
        request.end,
        request.description,
        request.uid,
        last_four=request.last_four,
    )
