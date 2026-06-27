# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Pure door-code generation helpers shared by coordinator helpers.

These functions perform no Home Assistant state reads, Store writes,
refresh requests, or service calls.  They reproduce the legacy code
generation used by the calendar sensor so coordinator-produced codes
match sensor-produced codes.
"""

from __future__ import annotations

from datetime import datetime
import random
import re


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


def extract_last_four(description: str | None) -> str | None:
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
        phone_matches = re.findall(
            r"""Phone(?: Number)?:\s+(\+?[\d\. \-\(\)]{9,})""",
            description,
        )
        if phone_matches:
            digits = str(phone_matches[0]).replace(" ", "")
            if len(digits) >= 4:
                return digits[-4:]

    return None


def generate_slot_code(
    code_generator: str,
    code_length: int,
    start: datetime,
    end: datetime,
    description: str | None,
    uid: str | None,
) -> str:
    """Generate a slot code using the configured legacy generator."""
    generator = code_generator

    if description is None and (generator != "static_random" or uid is None):
        generator = "date_based"

    code: str | None = None
    if generator == "last_four" and code_length == 4:
        code = extract_last_four(description)
    elif generator == "static_random":
        seed = uid if uid else description
        if seed:
            rng = random.Random(seed)
            max_range = int("9999".rjust(code_length, "9"))
            code = str(rng.randrange(1, max_range, code_length)).zfill(code_length)

    return (
        code if code is not None else generate_date_based_code(code_length, start, end)
    )
