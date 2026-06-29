# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Generated door-code helpers for calendar sensors."""

from __future__ import annotations

import random

from .models import DoorCodeRequest


def _date_based_code(request: DoorCodeRequest) -> str:
    """Generate the legacy date-based door code."""
    start_day = request.start.strftime("%d")
    start_month = request.start.strftime("%m")
    start_year = request.start.strftime("%Y")
    end_day = request.end.strftime("%d")
    end_month = request.end.strftime("%m")
    end_year = request.end.strftime("%Y")
    code = f"{start_day}{end_day}{start_month}{end_month}{start_year}{end_year}"
    if len(code) > request.code_length:
        return code[: request.code_length]
    return code.zfill(request.code_length)


def _static_random_code(request: DoorCodeRequest) -> str | None:
    """Generate the legacy static-random door code when a seed exists."""
    seed = request.uid if request.uid else request.description
    if not seed:
        return None
    random.seed(seed)
    max_range = int("9999".rjust(request.code_length, "9"))
    return str(random.randrange(1, max_range, request.code_length)).zfill(
        request.code_length
    )


def generate_door_code(request: DoorCodeRequest) -> str:
    """Generate a door code matching the legacy calendar sensor behavior."""
    generator = request.generator
    if request.description is None and (
        generator != "static_random" or request.uid is None
    ):
        generator = "date_based"

    ret = None
    if generator == "last_four" and request.code_length == 4:
        ret = request.last_four
    elif generator == "static_random":
        ret = _static_random_code(request)

    if ret is None:
        ret = _date_based_code(request)
    return ret
