# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Reservation rematch dataclasses and enums."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from enum import Enum


class RematchKind(str, Enum):
    """Classification of a reservation identity rematch result.

    Values follow the six-rule matching hierarchy from R-002 in
    ``specs/012-slot-reconciliation/research.md``.  Lower-numbered rules
    take precedence: :func:`find_reservation_rematch` returns the *first*
    matching rule for each candidate reservation.
    """

    EXACT = "exact"
    """Rule 1: primary fingerprint found unchanged in persisted mappings."""

    UID_ALIAS = "uid_alias"
    """Rule 2: a volatile UID alias overlaps; name also matches.

    :attr:`RematchResult.date_shifted` is always ``True`` when this
    kind is returned, because if the dates had not changed the primary
    fingerprint would have matched under rule 1 instead.
    """

    BOOKING_ALIAS = "booking_alias"
    """Rule 3: a booking/confirmation alias overlaps; name also matches."""

    NAME_TIME = "name_time"
    """Rule 4: normalized name plus exact UTC start/end match.

    Triggered when no alias evidence is available but the persisted
    identity dict's ``slot_name``, ``start``, and ``end`` match the
    incoming reservation exactly.  Acts as a migration safety net.
    """

    CONTINUITY = "continuity"
    """Rule 5: conservative continuity rematch.

    Exactly one persisted mapping is compatible based on fingerprint
    history, booking aliases, or normalized name with actual-slot
    evidence, and no other current reservation competes for it.
    """

    AMBIGUOUS = "ambiguous"
    """Two or more candidates are equally compatible; no rematch is made.

    Can arise from rule 2 (multiple UID alias matches), rule 3 (multiple
    booking alias matches), or rule 5 (multiple continuity-compatible
    mappings).  :attr:`RematchResult.ambiguous_keys` lists all
    compatible candidates for diagnostic capture.
    """

    NO_MATCH = "no_match"
    """No compatible persisted mapping was found under any rule."""


@dataclass(slots=True)
class RematchResult:
    """Result of a reservation identity rematch lookup.

    Produced by :func:`find_reservation_rematch` and consumed by the
    coordinator's identity-resolution step to decide whether and how
    to update the persisted slot mapping.

    Attributes:
        kind: Classification of the match found.
        matched_identity_key: Primary identity key of the persisted
            mapping that matched.  ``None`` for ``AMBIGUOUS`` and
            ``NO_MATCH`` results.
        date_shifted: ``True`` when the match was established via a UID
            alias but the incoming reservation's dates differ from the
            persisted fingerprint.  Always ``False`` for non-UID-alias
            matches.  When ``True`` and ``should_update_code`` is also
            ``True`` in the coordinator config, the coordinator should
            regenerate the access code alongside the date update.
        ambiguous_keys: Identity keys of all compatible candidates when
            *kind* is ``AMBIGUOUS``; empty otherwise.
    """

    kind: RematchKind
    matched_identity_key: str | None
    date_shifted: bool = False
    ambiguous_keys: list[str] = field(default_factory=list)
