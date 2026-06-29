# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Description parsing helpers for Rental Control calendar sensors."""

from __future__ import annotations

from collections.abc import Callable
import re

from .models import ParsedReservationAttributes

KNOWN_FIELDS: frozenset[str] = frozenset(
    {
        "email",
        "last 4 digits",
        "phone",
        "phone number",
        "phone (last 4)",
        "guests",
        "adults",
        "children",
        "booking id",
    }
)


def extract_email(description: str | None) -> str | None:
    """Extract the first guest email from a description."""
    if description is None:
        return None
    ret = re.compile(r"""Email:\s+(\S+@\S+)""").findall(description)
    if ret:
        return str(ret[0])
    return None


def extract_phone_number(description: str | None) -> str | None:
    """Extract the guest phone number from a description."""
    if description is None:
        return None
    ret = re.compile(r"""Phone(?: Number)?:\s+(\+?[\d\. \-\(\)]{9,})""").findall(
        description
    )
    if ret:
        return str(ret[0]).strip()
    return None


def extract_num_guests(description: str | None) -> str | None:
    """Extract the number of guests from a description."""
    if description is None:
        return None
    ret = re.compile(r"""Guests:\s+(\d+)$""", re.M).findall(description)
    if ret:
        return str(ret[0])
    if "Adults" in description:
        guests = 0
        ret = re.compile(r"""Adults:\s+(\d+)$""", re.M).findall(description)
        if ret:
            guests = int(ret[0])
        ret = re.compile(r"""Children:\s+(\d+)$""", re.M).findall(description)
        if ret:
            guests += int(ret[0])
        if guests > 0:
            return str(guests)
    return None


def extract_last_four(
    description: str | None,
    phone_extractor: Callable[[], str | None] | None = None,
) -> str | None:
    """Extract the last four phone digits from a description."""
    if description is None:
        return None
    ret = re.compile(r"""\(?Last 4 Digits\)?:\s+(\d{4})(?!\d)""").findall(description)
    if ret:
        return str(ret[0])
    ret = re.compile(r"""Phone\s*\(last\s*4\):\s*(\d{4})(?!\d)""", re.I).findall(
        description
    )
    if ret:
        return str(ret[0])
    if "Phone" in description:
        phone = (
            phone_extractor()
            if phone_extractor is not None
            else extract_phone_number(description)
        )
        if phone:
            phone = phone.replace(" ", "")
            if len(phone) >= 4:
                return str(phone)[-4:]
    return None


def extract_url(description: str | None) -> str | None:
    """Extract a reservation URL from a description."""
    if description is None:
        return None
    ret = re.compile(r"""(https?://.*$)""", re.M).findall(description)
    if ret:
        return str(ret[0])
    return None


def extract_booking_id(description: str | None) -> str | None:
    """Extract a booking ID from a description."""
    if description is None:
        return None
    ret = re.compile(r"""Booking ID:\s*(.+)$""", re.M).findall(description)
    if ret:
        for match in ret:
            booking_id = str(match).strip()
            if booking_id:
                return booking_id
    return None


def extract_dynamic_attributes(
    description: str | None,
    known_fields: frozenset[str] = KNOWN_FIELDS,
) -> dict[str, str]:
    """Extract unrecognized ``Field: Value`` description lines."""
    if not description:
        return {}
    result: dict[str, str] = {}
    line_re = re.compile(r"^([^:\n]+?):\s+(.+)$", re.MULTILINE)
    for match in line_re.finditer(description):
        field = match.group(1).strip()
        value = match.group(2).strip()
        if field.lower() in known_fields:
            continue
        if field.lower().startswith("http"):
            continue
        key = re.sub(r"[^a-z0-9]+", "_", field.lower()).strip("_")
        if key and value:
            result[key] = value
    return result


def build_parsed_attributes(description: str | None) -> dict[str, str]:
    """Build parsed reservation attributes from a description."""
    return ParsedReservationAttributes(
        last_four=extract_last_four(description),
        number_of_guests=extract_num_guests(description),
        guest_email=extract_email(description),
        phone_number=extract_phone_number(description),
        reservation_url=extract_url(description),
        booking_id=extract_booking_id(description),
        dynamic=extract_dynamic_attributes(description),
    ).as_dict()
