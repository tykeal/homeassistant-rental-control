# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""User-facing reports for shared door-code adoption problems.

These reports have two audiences and say different things to each.
The log is read by a maintainer chasing a bug, so it keeps the entry
id, the identity key and the code_ref.  The notification is read by
the person running the property, who knows their configuration as a
named entry, a lock and a slot number, so it says that instead and
keeps one masked code_ref as the handle tying it back to the log.
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

from homeassistant.components.persistent_notification import async_create

from ..const import DOMAIN
from ..const import NAME
from . import code_origin
from . import entry_info
from .models import AdoptionRequest
from .models import AllocationRecord

if TYPE_CHECKING:
    from .allocator import DoorCodeAllocator

_LOGGER = logging.getLogger(__name__)
_CONFLICT_NOTIFICATION_ID = f"{DOMAIN}_code_registry_conflict"
_LAST_FOUR_CODE_LENGTH = 4


def conflict_notification_id(code_ref: str) -> str:
    """Return the persistent notification id for one code's conflict.

    Each conflicting code gets its own notification. A single shared id
    let one code's report overwrite another's, so only the newest was
    ever visible and dismissing it hid every other open conflict.
    """
    return f"{_CONFLICT_NOTIFICATION_ID}_{code_ref}"


def _conflict_is_expected(
    allocator: DoorCodeAllocator,
    record: AllocationRecord,
) -> bool:
    """Return whether every owner of a shared code is explained by it.

    Under the ``last_four`` generator the code *is* the guest's last
    four phone digits, so two owners holding one code means one phone
    number, which in practice means one guest moving between units at
    the same property. That is the generator working as specified, and
    the report says "no code was rotated" because there is nothing to
    act on, so it is suppressed to an INFO log line.

    Suppression requires *every* owner's entry to use ``last_four``.
    A record shared between a ``last_four`` entry and a ``date_based``
    one is not explained by the generator: the date_based entry picked
    that code from the shared code space, which is the allocator
    problem the notification exists to surface, so the mixed case still
    notifies. An entry whose generator cannot be determined at all -
    deleted, unloaded or carrying no configuration - counts as "not
    known to be last_four", so an undeterminable owner also notifies.
    Silence is only ever chosen on positive evidence.

    Configuration alone is not that evidence.
    ``generate_slot_code`` uses the phone digits only when the
    configured code length is four, and falls back to date-based codes
    silently both when it is not and when the reservation text carries
    no phone number at all. Either way the entry still reports
    ``last_four``. The second case stopped being hypothetical when
    Booking.com dropped phone information from its feeds, and at a code
    length of four a date-based code is only ``start_day + end_day``, so
    two such entries colliding is likely - and is exactly the collision
    this report exists to raise (issue #861). Each owner must therefore
    show that its own reservation's phone digits really are the shared
    code.

    The shared code itself must also be four digits long. A phone
    derived code never is anything else, so a longer shared code was
    produced some other way and is not explained by the generator.
    """
    return (
        bool(record.owners)
        and record.code_length == _LAST_FOUR_CODE_LENGTH
        and all(
            entry_info.uses_last_four(allocator.hass, owner.entry_id)
            and code_origin.owner_code_is_phone_derived(allocator.hass, owner, record)
            for owner in record.owners
        )
    )


def report_adoption_conflict(
    allocator: DoorCodeAllocator,
    record: AllocationRecord,
) -> None:
    """Log and notify that an observed code has multiple owners."""
    owners = ", ".join(
        f"{owner.entry_id}:{owner.identity_key}@{owner.lockname}:{owner.slot}"
        for owner in record.owners
    )
    message = (
        f"Shared code registry adoption conflict for code_ref "
        f"{record.code_ref}: {owners}. No code was rotated."
    )
    if _conflict_is_expected(allocator, record):
        _LOGGER.info(
            "Shared code_ref %s has multiple owners (%s), which is expected "
            "under the last_four generator because the code is the guest's "
            "phone digits. No code was rotated and no notification was "
            "raised.",
            record.code_ref,
            owners,
        )
        return
    _LOGGER.warning(message)
    places = ", ".join(
        entry_info.describe_owner(allocator.hass, owner) for owner in record.owners
    )
    _notify(
        allocator,
        (
            f"Rental Control found one door code in use by more than one "
            f"booking: {places}. The code was left in place and nothing was "
            f"rotated. Search the Home Assistant log for code_ref "
            f"{record.code_ref} for the full details."
        ),
        title=f"{NAME} code registry conflict",
        notification_id=conflict_notification_id(record.code_ref),
    )


def report_identity_mismatch(
    allocator: DoorCodeAllocator,
    request: AdoptionRequest,
    existing: AllocationRecord,
    observed_ref: str,
) -> None:
    """Log and notify that one identity has conflicting observed code refs."""
    mismatch = (request.entry_id, request.identity_key, observed_ref)
    if mismatch in allocator._reported_identity_mismatches:
        return
    allocator._reported_identity_mismatches.add(mismatch)
    message = (
        f"Shared code registry identity mismatch for {request.entry_id}:"
        f"{request.identity_key}: registry code_ref {existing.code_ref}, "
        f"observed code_ref {observed_ref}. The observed lock code was "
        "retained and no code was rotated."
    )
    _LOGGER.warning(message)
    where = entry_info.describe_slot(
        allocator.hass, request.entry_id, request.lockname, request.slot
    )
    _notify(
        allocator,
        (
            f"Rental Control found a different door code on the lock than "
            f"the one it recorded for a booking in {where}. The code on the "
            f"lock was kept and nothing was rotated. Search the Home "
            f"Assistant log for code_ref {observed_ref} for the full "
            "details."
        ),
        title=f"{NAME} code registry identity mismatch",
        notification_id=f"{_CONFLICT_NOTIFICATION_ID}_identity",
    )


def _notify(allocator: DoorCodeAllocator, message: str, **kwargs: str) -> None:
    """Create a notification, honoring tests patched on the allocator module."""
    allocator_module = sys.modules.get(
        "custom_components.rental_control.allocator.allocator"
    )
    create = getattr(allocator_module, "async_create", async_create)
    create(allocator.hass, message, **kwargs)
