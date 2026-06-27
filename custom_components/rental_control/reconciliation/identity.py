# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Identity, fingerprint, and name-form helpers."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
import hashlib
import re
from typing import TYPE_CHECKING

from .enums import FINGERPRINT_VERSION

if TYPE_CHECKING:
    from .plan_models import Reservation
    from .stateless_models import DesiredReservation

_AIRBNB_CONF_RE = re.compile(r"(?<![A-Z0-9])([A-Z][A-Z0-9]{9})(?![A-Z0-9])")


def normalize_slot_name_for_fingerprint(slot_name: str) -> str:
    """Return the stable normalized form of a slot name for fingerprinting.

    Strips leading/trailing whitespace and applies Unicode-aware
    ``casefold()`` so names that differ only in case or surrounding
    whitespace produce identical fingerprints.

    Args:
        slot_name: Unprefixed, untrimmed guest-facing slot name.

    Returns:
        Casefold-normalized, stripped slot name.
    """
    return slot_name.strip().casefold()


def _desired_name_forms(
    slot_name: str, display_slot_name: str | None = None
) -> set[str]:
    """Return normalized stable/display forms for stateless name matching."""
    forms = {normalize_slot_name_for_fingerprint(slot_name)}
    if display_slot_name:
        forms.add(normalize_slot_name_for_fingerprint(display_slot_name))
    return {form for form in forms if form}


def _slot_name_variants(name: str, *, prefix: str = "") -> set[str]:
    """Return normalized physical name variants, including prefix-stripped form."""
    stripped = name.strip()
    variants = {normalize_slot_name_for_fingerprint(stripped)}
    if prefix and stripped.startswith(prefix):
        variants.add(normalize_slot_name_for_fingerprint(stripped[len(prefix) :]))
    return {variant for variant in variants if variant}


def _names_match(
    physical_name: str | None,
    stable_slot_name: str,
    display_slot_name: str | None = None,
    *,
    prefix: str = "",
) -> bool:
    """Return whether a physical Keymaster name identifies a desired stay."""
    if not physical_name:
        return False
    physical_forms = _slot_name_variants(physical_name, prefix=prefix)
    desired_forms = _desired_name_forms(stable_slot_name, display_slot_name)
    if physical_forms & desired_forms:
        return True
    # Trim-aware matching is handled by requiring callers to provide the exact
    # display_slot_name that Rental Control would write to Keymaster.  Do not use
    # generic prefix matching here: names like "Ann" and "Anna" are distinct
    # stable identities even though one is a string prefix of the other.
    return False


def _reservation_name_key(reservation: Reservation) -> str:
    """Return the stable name grouping key for a legacy Reservation."""
    return normalize_slot_name_for_fingerprint(reservation.slot_name)


def _desired_name_key(reservation: DesiredReservation) -> str:
    """Return the stable name grouping key for a DesiredReservation."""
    return normalize_slot_name_for_fingerprint(reservation.stable_slot_name)


def _dt_to_utc_iso(dt: datetime) -> str:
    """Convert *dt* to a UTC ISO-8601 string for fingerprint computation.

    Naive datetimes are treated as UTC.  The output format is always
    ``YYYY-MM-DDTHH:MM:SS+00:00`` to ensure a single canonical
    representation regardless of the original timezone offset.

    Args:
        dt: Input datetime, timezone-aware or naive.

    Returns:
        Fixed-format UTC ISO-8601 string.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")


def make_reservation_fingerprint(
    entry_id: str,
    slot_name: str,
    start: datetime,
    end: datetime,
) -> str:
    """Compute the stable versioned identity fingerprint for a reservation.

    The fingerprint is a 64-character lowercase SHA-256 hexdigest of a
    canonical string built from:

    - :data:`FINGERPRINT_VERSION` prefix (``"v1"``),
    - *entry_id* (config-entry scope),
    - normalized, casefold-stripped *slot_name*,
    - UTC ISO-8601 *start*, and
    - UTC ISO-8601 *end*.

    The fingerprint deliberately excludes volatile calendar UIDs so that
    platform UID churn does not change the primary identity key.

    Args:
        entry_id: Config entry ID that scopes this fingerprint to one
            integration instance.
        slot_name: Unprefixed, untrimmed guest-facing slot name.  Will
            be normalized via :func:`normalize_slot_name_for_fingerprint`.
        start: Reservation start datetime (any timezone; converted to
            UTC before hashing).
        end: Reservation end datetime (any timezone; converted to UTC
            before hashing).

    Returns:
        64-character lowercase SHA-256 hexdigest string.
    """
    normalized_name = normalize_slot_name_for_fingerprint(slot_name)
    start_utc = _dt_to_utc_iso(start)
    end_utc = _dt_to_utc_iso(end)
    canonical = (
        f"{FINGERPRINT_VERSION}:{entry_id}:{normalized_name}:{start_utc}:{end_utc}"
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def extract_booking_aliases(summary: str, description: str) -> set[str]:
    """Extract booking/confirmation aliases from an event's text fields.

    Searches the combined *summary* and *description* text for known
    booking-platform confirmation codes.  Currently extracts:

    - **Airbnb** confirmation codes: one uppercase letter followed by
      nine uppercase alphanumeric characters (e.g. ``HMXXXXXXXX``).

    The extracted aliases are stored as :attr:`Reservation.booking_aliases`
    and used as secondary signals in :func:`find_reservation_rematch`.

    Args:
        summary: Raw iCal SUMMARY field value.
        description: Raw iCal DESCRIPTION field value; may be empty or
            ``None``-like (empty string is safe).

    Returns:
        Set of extracted booking identifier strings; empty when none
        are detected.
    """
    aliases: set[str] = set()
    text = f"{summary} {description or ''}"
    for m in _AIRBNB_CONF_RE.finditer(text):
        aliases.add(m.group(1))
    return aliases
