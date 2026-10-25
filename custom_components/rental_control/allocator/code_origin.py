# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Evidence of where one allocation owner's door code came from.

The allocator holds an ``entry_id`` and an ``identity_key``, neither of
which says anything about how the code was produced.  The configuration
does not say either: an entry set to ``last_four`` publishes date-based
codes whenever its feed carries no phone number, and says nothing while
it does so.  The only durable evidence is the phone digits the feed
yielded for that reservation, which the coordinator records on the
reservation as it builds it, so this module joins an owner back to its
live reservation and asks :func:`code_is_phone_derived`.

Every step fails toward "no evidence".  A deleted entry, an unloaded
coordinator, a reservation that is no longer in the feed, or an owner
that is a bookkeeping alias rather than a booking all answer ``False``,
so a caller choosing between silence and a report chooses the report.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..codegen import code_is_phone_derived
from ..const import COORDINATOR
from ..const import DOMAIN
from . import entry_info
from .models import AllocationOwner
from .models import AllocationRecord
from .reissue_state import observed_alias_base_identity

__all__ = [
    "forget_entry",
    "owner_code_is_phone_derived",
    "record_cycle_evidence",
    "reset_cycle_evidence",
]

_UNKNOWN = object()

#: Phone evidence for the cycle currently being resolved, per entry and
#: identity.  The coordinator's reservation cache is only refreshed once
#: a cycle has completed, so a booking adopted or allocated for the
#: first time is not in it yet while its own conflicts are reported.
#: Reading a cycle that is still in flight is what keeps a genuinely
#: phone-derived shared code from raising a conflict on its first cycle
#: and then staying raised, because conflicts are reported only as an
#: owner is added.
_cycle_evidence: dict[str, dict[str, str | None]] = {}


def record_cycle_evidence(entry_id: str, reservations: list[Any]) -> None:
    """Publish the reservations the cycle about to run is working from."""
    _cycle_evidence[entry_id] = {
        reservation.identity_key: getattr(reservation, "phone_last_four", None)
        for reservation in reservations
    }


def forget_entry(entry_id: str) -> None:
    """Drop one entry's in-flight evidence as it goes away."""
    _cycle_evidence.pop(entry_id, None)


def reset_cycle_evidence() -> None:
    """Drop every entry's in-flight evidence."""
    _cycle_evidence.clear()


def owner_code_is_phone_derived(
    hass: Any,
    owner: AllocationOwner,
    record: AllocationRecord,
) -> bool:
    """Return whether one owner's share of a code came from phone digits.

    This is the positive-evidence counterpart to
    :func:`.entry_info.uses_last_four`.  That function reports what the
    entry is configured to do; this one reports what actually happened
    for this booking, which is the only sound basis for staying silent
    about a shared code.
    """
    last_four = _phone_last_four(hass, owner.entry_id, owner.identity_key)
    if last_four is _UNKNOWN:
        return False
    return code_is_phone_derived(
        record.code,
        None,
        entry_info.code_generator(hass, owner.entry_id),
        entry_info.configured_code_length(hass, owner.entry_id),
        last_four=last_four,
    )


def _phone_last_four(hass: Any, entry_id: str, identity_key: str) -> Any:
    """Return an owner's phone digits, or ``_UNKNOWN`` when undeterminable.

    ``None`` and ``_UNKNOWN`` are different answers.  ``None`` means the
    reservation was read and carried no phone number, which is positive
    evidence that the code cannot be phone-derived.  ``_UNKNOWN`` means
    the reservation could not be read at all.
    """
    for key in _owner_identities(identity_key):
        evidence = _cycle_evidence.get(entry_id)
        if evidence is not None and key in evidence:
            return evidence[key]
    reservation = _reservation_for(hass, entry_id, identity_key)
    if reservation is None:
        return _UNKNOWN
    return getattr(reservation, "phone_last_four", None)


def _owner_identities(identity_key: str) -> tuple[str, ...]:
    """Return an owner's identity and the booking identity behind it."""
    base_identity = observed_alias_base_identity(identity_key)
    if base_identity is None:
        return (identity_key,)
    return (identity_key, base_identity)


def _reservation_for(hass: Any, entry_id: str, identity_key: str) -> Any | None:
    """Return the live reservation behind one allocation owner, if any.

    An owner may be a slot's observed-code alias rather than a booking,
    in which case the booking it was derived from is the one that holds
    the evidence, so the alias is resolved back to its base identity.
    """
    reservations = _reservations_for_entry(hass, entry_id)
    if reservations is None:
        return None
    reservation = reservations.get(identity_key)
    if reservation is not None:
        return reservation
    base_identity = observed_alias_base_identity(identity_key)
    if base_identity is None:
        return None
    return reservations.get(base_identity)


def _reservations_for_entry(hass: Any, entry_id: str) -> Mapping[str, Any] | None:
    """Return one entry's latest reservations keyed by identity."""
    data = getattr(hass, "data", None)
    if not isinstance(data, Mapping):
        return None
    domain_data = data.get(DOMAIN)
    if not isinstance(domain_data, Mapping):
        return None
    entry_data = domain_data.get(entry_id)
    if not isinstance(entry_data, Mapping):
        return None
    coordinator = entry_data.get(COORDINATOR)
    reservations = getattr(coordinator, "_latest_res_by_key", None)
    if not isinstance(reservations, Mapping):
        return None
    return reservations
