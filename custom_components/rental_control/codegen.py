# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Pure door-code generation helpers shared across Rental Control.

These functions perform no Home Assistant state reads, Store writes,
refresh requests, or service calls.  They preserve the legacy calendar
sensor generation behavior while providing one shared implementation for
coordinator and sensor callers.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import random
import re
from typing import Final
from typing import cast

_LAST_FOUR_UNSET: Final[object] = object()


def generate_date_based_code(code_length: int, start: datetime, end: datetime) -> str:
    """Generate a date-based door code from reservation start/end times."""
    start_day = start.strftime("%d")
    start_month = start.strftime("%m")
    start_year = start.strftime("%Y")
    end_day = end.strftime("%d")
    end_month = end.strftime("%m")
    end_year = end.strftime("%Y")
    code = f"{start_day}{end_day}{start_month}{end_month}{start_year}{end_year}"
    return code[:code_length] if len(code) > code_length else code.zfill(code_length)


def extract_last_four(
    description: str | None,
    phone_extractor: Callable[[], str | None] | None = None,
) -> str | None:
    """Extract last-four phone digits from reservation text."""
    if description is None:
        return None

    explicit = re.findall(r"""\(?Last 4 Digits\)?:\s+(\d{4})(?!\d)""", description)
    if explicit:
        return str(explicit[0])

    phone_last_four = re.findall(
        r"""Phone\s*\(last\s*4\):\s*(\d{4})(?!\d)""",
        description,
        re.I,
    )
    if phone_last_four:
        return str(phone_last_four[0])

    if "Phone" in description:
        phone = (
            phone_extractor()
            if phone_extractor is not None
            else _extract_phone_number(description)
        )
        if phone:
            digits = phone.replace(" ", "")
            if len(digits) >= 4:
                return str(digits)[-4:]

    return None


def generate_static_random_code(
    code_length: int,
    description: str | None,
    uid: str | None,
) -> str | None:
    """Generate a deterministic static-random door code when a seed exists."""
    seed = uid if uid else description
    if not seed:
        return None
    rng = random.Random(seed)
    max_range = int("9999".rjust(code_length, "9"))
    return str(rng.randrange(1, max_range)).zfill(code_length)


def generate_slot_code(
    code_generator: str,
    code_length: int,
    start: datetime,
    end: datetime,
    description: str | None,
    uid: str | None,
    *,
    last_four: str | None | object = _LAST_FOUR_UNSET,
) -> str:
    """Generate a slot code using the configured legacy generator."""
    generator = code_generator

    if description is None and (generator != "static_random" or uid is None):
        generator = "date_based"

    code: str | None = None
    if generator == "last_four" and code_length == 4:
        code = (
            extract_last_four(description)
            if last_four is _LAST_FOUR_UNSET
            else cast(str | None, last_four)
        )
    elif generator == "static_random":
        code = generate_static_random_code(code_length, description, uid)

    return (
        code if code is not None else generate_date_based_code(code_length, start, end)
    )


def _extract_phone_number(description: str | None) -> str | None:
    """Extract the guest phone number from a description."""
    if description is None:
        return None
    phone_matches = re.findall(
        r"""Phone(?: Number)?:\s+(\+?[\d\. \-\(\)]{9,})""",
        description,
    )
    if phone_matches:
        return str(phone_matches[0]).strip()
    return None
