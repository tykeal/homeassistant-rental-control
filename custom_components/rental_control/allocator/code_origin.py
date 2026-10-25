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

__all__ = ["owner_code_is_phone_derived"]


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
    reservation = _reservation_for(hass, owner.entry_id, owner.identity_key)
    if reservation is None:
        return False
    return code_is_phone_derived(
        record.code,
        None,
        entry_info.code_generator(hass, owner.entry_id),
        entry_info.configured_code_length(hass, owner.entry_id),
        last_four=getattr(reservation, "phone_last_four", None),
    )


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
