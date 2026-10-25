# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Decide whether a calendar feed publishes only confirmed bookings.

``get_slot_name`` discards any event summarised ``Not available`` or
``Blocked`` because Airbnb, Google Calendar and several PMS feeds use
exactly those words for owner stays and maintenance blocks, which must
never receive a guest door code.

Booking.com's iCal export now titles *every* event ``CLOSED - Not
available``: it no longer publishes guest names, so a real reservation
and a manually blocked date are byte-for-byte identical apart from
their dates (issue #830).  Nothing in the feed can tell them apart, so
the filter cannot simply be relaxed.  Only the operator knows, because
Booking.com's extranet offers a choice between exporting booked dates
only and exporting booked plus closed dates.

That answer is recorded per entry as ``bookings_only_feed``.  Entries
created before the option existed carry no value at all, so the host of
the calendar URL supplies the default: a Booking.com feed is assumed to
be bookings-only, which is what repairs #830 without user action, and
every other host defaults off.  Anything unparseable is off, because
issuing codes for genuine blocks is the worse failure.

Home Assistant exposes ``ConfigEntry.data`` as a read-only
``MappingProxyType`` rather than a ``dict``: testing for ``dict`` made
every real entry look empty and silently disabled a safety mechanism in
production (issue #850), so every read here accepts any
:class:`~collections.abc.Mapping`.

An anonymous reservation still needs a slot name.  It is derived from
the VEVENT ``UID`` (``Booking A3F9K2``) rather than from the stay,
because a name derived from the dates changes when the booking is
amended, and both slot-matching layers are name-based: the amended stay
would be treated as a new reservation and the guest's door code would
change mid-stay (issue #864).  A feed that publishes no usable ``UID``
falls back to the date-derived name.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import hashlib
from typing import Any
from urllib.parse import urlparse

from homeassistant.const import CONF_URL

from .const import CONF_BOOKINGS_ONLY_FEED
from .helpers import get_slot_name
from .helpers import normalize_uid

# aislop-ignore-file ai-slop/hallucinated-import -- Provided by Home Assistant runtime.

__all__ = [
    "anonymous_slot_name",
    "feed_is_bookings_only",
    "host_defaults_bookings_only",
    "resolve_slot_name",
    "synthesized_slot_name",
    "uid_slot_name",
    "uid_slot_token",
]

_BOOKINGS_ONLY_DOMAIN = "booking.com"

_TRUTHY = frozenset({"true", "yes", "on", "1"})
_FALSEY = frozenset({"false", "no", "off", "0"})

# Crockford base32: uppercase, with the ambiguous I, L, O and U removed.
_CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_TOKEN_LENGTH = 6


def host_defaults_bookings_only(url: Any) -> bool:
    """Return whether a calendar URL's host defaults to bookings-only.

    Only the hostname is examined, case-insensitively, and only
    ``booking.com`` or one of its subdomains matches.  A substring test
    against the whole URL would also fire on a path or query parameter
    that merely mentions ``booking.com``, which says nothing about who
    publishes the feed.
    """
    if not isinstance(url, str) or not url.strip():
        return False
    try:
        hostname = urlparse(url.strip()).hostname
    except ValueError:
        return False
    if not hostname:
        return False
    hostname = hostname.lower().rstrip(".")
    return hostname == _BOOKINGS_ONLY_DOMAIN or hostname.endswith(
        f".{_BOOKINGS_ONLY_DOMAIN}"
    )


def _stored_choice(value: Any) -> bool | None:
    """Return an operator's stored choice, or None when there is none."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _TRUTHY:
            return True
        if normalized in _FALSEY:
            return False
    return None


def feed_is_bookings_only(config: Any) -> bool:
    """Return whether an entry's calendar contains only bookings.

    An explicitly stored choice always wins, including an explicit
    ``False`` on a Booking.com URL, because an operator who exports
    closed dates must be able to keep them filtered.  Only a missing or
    uninterpretable value falls back to the host-derived default.
    """
    if not isinstance(config, Mapping):
        return False
    stored = _stored_choice(config.get(CONF_BOOKINGS_ONLY_FEED))
    if stored is not None:
        return stored
    return host_defaults_bookings_only(config.get(CONF_URL))


def synthesized_slot_name(start: datetime, end: datetime) -> str:
    """Return a date-derived slot name for an anonymous reservation.

    This is the fallback for a feed that publishes no usable ``UID``.
    It is unstable by construction: amending a stay renames it, which
    costs the guest their slot and their code (issue #864).  Prefer
    :func:`uid_slot_name` whenever a ``UID`` exists.

    The year is part of the name because it is hashed into the
    reservation fingerprint: two stays covering the same day and month
    in different years must not collapse onto one key.
    """
    return f"Booking {start:%d %b} - {end:%d %b %Y}"


def uid_slot_token(uid: Any) -> str | None:
    """Return a short stable token for a VEVENT UID, or None.

    The token must be identical in every process and across every Home
    Assistant restart: it names a lock slot, and a changed name means a
    new slot and a new PIN mid-stay.  That is why this hashes with
    :mod:`hashlib` and **never** with the builtin ``hash()``, which is
    salted per process via ``PYTHONHASHSEED`` and would therefore churn
    the name on every restart.

    Hashing also keeps the upstream reservation number off the lock.
    """
    normalized = normalize_uid(str(uid)) if uid is not None else None
    if not normalized:
        return None
    digest = hashlib.sha256(normalized.encode("utf-8")).digest()
    value = int.from_bytes(digest[: (_TOKEN_LENGTH * 5 + 7) // 8], "big")
    characters = []
    for _ in range(_TOKEN_LENGTH):
        characters.append(_CROCKFORD_ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(characters)


def uid_slot_name(uid: Any) -> str | None:
    """Return a UID-derived slot name, or None without a usable UID.

    The name depends on nothing but the ``UID``, so it survives an
    upstream amendment of the stay dates and both matching layers keep
    recognising the reservation with no change to either matcher.
    """
    token = uid_slot_token(uid)
    return f"Booking {token}" if token else None


def anonymous_slot_name(uid: Any, start: datetime, end: datetime) -> str:
    """Return the slot name for an anonymous reservation.

    Every consumer must name an anonymous stay through this one
    function: the name is hashed into the reservation fingerprint, so
    two call sites that disagree would split one reservation's identity
    and churn its code.
    """
    return uid_slot_name(uid) or synthesized_slot_name(start, end)


def resolve_slot_name(
    summary: Any,
    description: Any,
    event_prefix: str,
    start: datetime,
    end: datetime,
    bookings_only_feed: bool,
    uid: Any = None,
) -> str | None:
    """Return an event's slot name, synthesizing one when permitted.

    Every consumer of a slot name must resolve it the same way.  The
    name is hashed into the reservation fingerprint that the calendar
    sensors and the check-in sensor look codes up by, so a caller still
    reading ``get_slot_name`` alone would see ``None`` for an anonymous
    booking and publish no code for a slot the coordinator did program.
    """
    slot_name = get_slot_name(summary, description or "", event_prefix or "")
    if slot_name or not bookings_only_feed:
        return slot_name
    return anonymous_slot_name(uid, start, end)
