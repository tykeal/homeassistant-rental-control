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

from collections.abc import Sequence
import logging
import sys
from typing import TYPE_CHECKING

from homeassistant.components.persistent_notification import async_create

from ..const import NAME
from . import code_origin
from . import conflict_notices
from . import entry_info
from .conflict_notices import CONFLICT_NOTIFICATION_PREFIX
from .conflict_notices import conflict_notification_id
from .models import AdoptionRequest
from .models import AllocationOwner
from .models import AllocationRecord
from .registry import SlotKey
from .registry import issuance_owners
from .registry import physical_slots

if TYPE_CHECKING:
    from .allocator import DoorCodeAllocator

_LOGGER = logging.getLogger(__name__)
_CONFLICT_NOTIFICATION_ID = CONFLICT_NOTIFICATION_PREFIX
_LAST_FOUR_CODE_LENGTH = 4

__all__ = [
    "ConflictVerdict",
    "conflict_notification_id",
    "rejudge_conflicts",
    "report_adoption_conflict",
    "report_identity_mismatch",
]

#: The physical slots a shared code was last judged for, and whether
#: that judgement found the sharing explained.
ConflictVerdict = tuple[frozenset[SlotKey], bool]


def _conflict_is_expected(
    allocator: DoorCodeAllocator,
    record: AllocationRecord,
    owners: Sequence[AllocationOwner] | None = None,
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

    ``owners`` narrows the judgement to the given owners and defaults to
    every owner of the record.
    """
    judged = record.owners if owners is None else owners
    return (
        bool(judged)
        and record.code_length == _LAST_FOUR_CODE_LENGTH
        and all(
            entry_info.uses_last_four(allocator.hass, owner.entry_id)
            and code_origin.owner_code_is_phone_derived(allocator.hass, owner, record)
            for owner in judged
        )
    )


def _verdict(owners: Sequence[AllocationOwner], explained: bool) -> ConflictVerdict:
    """Return the comparable verdict for one judgement of a shared code."""
    return (physical_slots(owners), explained)


def _evidence_settled(
    allocator: DoorCodeAllocator, owners: Sequence[AllocationOwner]
) -> bool:
    """Return whether every owner's entry can vouch for its code now.

    An enabled entry that has not yet run a cycle since it was loaded
    has published no phone evidence, so judging it would read as
    "unexplained" and raise a false report during startup. Its first
    cycle judges the record instead. An entry that is deleted or
    disabled never publishes evidence, and is judged as it stands.
    """
    for owner in owners:
        if code_origin.has_cycle_evidence(owner.entry_id):
            continue
        entry = entry_info.config_entry(allocator.hass, owner.entry_id)
        if entry is not None and getattr(entry, "disabled_by", None) is None:
            return False
    return True


def rejudge_conflicts(allocator: DoorCodeAllocator, entry_id: str) -> None:
    """Re-judge the shared codes this entry holds, once per cycle.

    Reporting a conflict only as an owner joins left two holes (#870):
    a conflict silenced at formation under an earlier, wrong rule was
    never reconsidered, and one whose explanation later decayed - say,
    a feed that stopped carrying phone numbers - was never reported.

    Each shared code is therefore judged again on every cycle of an
    entry holding it, after that cycle's evidence is published. The
    last verdict is remembered per ``code_ref`` and only a changed
    verdict is announced, while the announcement itself is persisted,
    so a conflict is reported once rather than on every refresh or
    restart. A code no longer in conflict has its notification
    withdrawn. Observed aliases and forced-release holds are not
    issuance owners and take no part.
    """
    verdicts: dict[str, ConflictVerdict] = allocator._conflict_verdicts
    conflicts = {record.code_ref: record for record in allocator._registry.conflicts()}
    for code_ref in set(verdicts) - set(conflicts):
        del verdicts[code_ref]
    conflict_notices.withdraw_resolved(allocator, conflicts)
    for code_ref, record in conflicts.items():
        owners = issuance_owners(record)
        if not any(owner.entry_id == entry_id for owner in owners):
            continue
        if not _evidence_settled(allocator, owners):
            continue
        explained = _conflict_is_expected(allocator, record, owners)
        verdict = _verdict(owners, explained)
        if verdicts.get(code_ref) == verdict:
            continue
        verdicts[code_ref] = verdict
        _announce_conflict(allocator, record, explained, owners)


def report_adoption_conflict(
    allocator: DoorCodeAllocator,
    record: AllocationRecord,
) -> None:
    """Log and notify that an observed code has multiple owners.

    Owners on one physical slot are one code in one place, so a record
    whose owners collapse to a single slot is no conflict at all (#942).
    Between bookings, only issuance owners are judged and the verdict is
    remembered for re-judgement, so an alias or hold without phone
    evidence cannot make otherwise explained sharing look unexplained.
    A code shared by one booking's slot and an alias or hold on another
    slot is still the same code presented by two slots of a lock, so it
    is announced as it forms, judged over every owner, but is never
    re-judged.
    """
    owners = issuance_owners(record)
    if len(physical_slots(owners)) < 2:
        if len(physical_slots(record.owners)) < 2:
            return
        explained = _conflict_is_expected(allocator, record)
        _announce_conflict(allocator, record, explained, record.owners, track=False)
        return
    explained = _conflict_is_expected(allocator, record, owners)
    allocator._conflict_verdicts[record.code_ref] = _verdict(owners, explained)
    _announce_conflict(allocator, record, explained, owners)


def _announce_conflict(
    allocator: DoorCodeAllocator,
    record: AllocationRecord,
    explained: bool,
    owners: Sequence[AllocationOwner],
    *,
    track: bool = True,
) -> None:
    """Log a shared code, and notify unless its sharing is explained.

    A tracked conflict is announced once per set of physical slots and
    withdrawn once it is explained or resolved; an untracked one, an
    alias or hold beside a booking, is announced as it forms.
    """
    logged = ", ".join(
        f"{owner.entry_id}:{owner.identity_key}@{owner.lockname}:{owner.slot}"
        for owner in record.owners
    )
    if explained:
        if track:
            conflict_notices.withdraw(allocator, record.code_ref)
        _LOGGER.info(
            "Shared code_ref %s has multiple owners (%s), which is expected "
            "under the last_four generator because the code is the guest's "
            "phone digits. No code was rotated and no notification was "
            "raised.",
            record.code_ref,
            logged,
        )
        return
    if track and not conflict_notices.claim(
        allocator, record.code_ref, physical_slots(owners)
    ):
        _LOGGER.debug(
            "Shared code_ref %s conflict (%s) was already reported",
            record.code_ref,
            logged,
        )
        return
    _LOGGER.warning(
        "Shared code registry adoption conflict for code_ref %s: %s. "
        "No code was rotated.",
        record.code_ref,
        logged,
    )
    places = "\n".join(
        f"- {line}" for line in conflict_notices.describe_slots(allocator, owners)
    )
    _notify(
        allocator,
        (
            f"Rental Control found one door code in use by more than one "
            f"booking:\n\n{places}\n\nThe code was left in place and "
            f"nothing was rotated. Search the Home Assistant log for "
            f"code_ref {record.code_ref} for the full details."
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
