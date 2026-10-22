# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Evaluation and reporting for the allocator's adoption gate.

The adoption gate decides whether an entry may issue door codes at all. A
shut gate withholds every code for the entry, so the reason it is shut has
to be visible in the log rather than inferred from missing codes.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
import logging
from typing import TYPE_CHECKING

from ..reconciliation import SlotStatus
from .reissue import ReissueSuppression

if TYPE_CHECKING:
    from ..allocator.models import AdoptionRequest
    from ..allocator.models import CycleObservation
    from ..reconciliation import ManagedSlot

_LOGGER = logging.getLogger(__name__)

_UNREADABLE_REASON = (
    "unreadable, so it may still hold a code this integration cannot see"
)
_UNMATCHED_REASON = "holds a code that matches no current reservation"

# Entries whose shut gate has already been reported, mapped to the blocking
# slot signature that was reported. Reporting only repeats when that
# signature changes, because the gate is re-evaluated on every refresh.
_reported: dict[str, tuple[tuple[int, ...], tuple[int, ...]]] = {}


@dataclass(frozen=True)
class AdoptionGateStatus:
    """The adoption gate outcome and the slots that are holding it shut."""

    complete: bool
    unreadable_slots: frozenset[int] = field(default_factory=frozenset)
    unmatched_slots: frozenset[int] = field(default_factory=frozenset)

    @property
    def blocking_slots(self) -> frozenset[int]:
        """Return every slot that is holding the gate shut."""
        return self.unreadable_slots | self.unmatched_slots

    @property
    def signature(self) -> tuple[tuple[int, ...], tuple[int, ...]]:
        """Return a comparable summary of why the gate is shut."""
        return (
            tuple(sorted(self.unreadable_slots)),
            tuple(sorted(self.unmatched_slots)),
        )


def evaluate(
    observation: CycleObservation,
    adoptions: list[AdoptionRequest],
    managed_slots: list[ManagedSlot],
    suppression: ReissueSuppression | None = None,
    *,
    excluded: frozenset[int] = frozenset(),
) -> AdoptionGateStatus:
    """Return whether all readable managed codes were accounted for."""
    suppression = suppression or ReissueSuppression()
    unreadable = frozenset(observation.unreadable_slots) - excluded
    adopted_slots = {request.slot for request in adoptions}
    readable_coded_slots = {
        slot.slot
        for slot in managed_slots
        if slot.managed and slot.status is not SlotStatus.UNKNOWN and slot.actual_code
    }
    readable_coded_slots -= suppression.slots
    readable_coded_slots -= getattr(adoptions, "suppressed_slots", set())
    unmatched = frozenset(readable_coded_slots - adopted_slots)
    return AdoptionGateStatus(
        complete=not unreadable and not unmatched,
        unreadable_slots=unreadable,
        unmatched_slots=unmatched,
    )


def report(
    entry_id: str,
    lockname: str | None,
    status: AdoptionGateStatus,
) -> None:
    """Log why an entry withholds issuance, once per distinct condition."""
    if status.complete:
        if _reported.pop(entry_id, None) is not None:
            _LOGGER.info(
                "Rental Control entry %s accounted for every managed slot on "
                "lock %s, so its adoption gate reopened. Issuance resumes "
                "once every entry's gate is open, because all entries share "
                "one door code space",
                entry_id,
                lockname or "unknown lock",
            )
        return
    if _reported.get(entry_id) == status.signature:
        return
    _reported[entry_id] = status.signature
    _LOGGER.warning(
        "Rental Control entry %s is withholding ALL door code issuance on "
        "lock %s: %s. No door codes will be created or updated for this "
        "entry until every listed slot is resolved. %s",
        entry_id,
        lockname or "unknown lock",
        _blocking_detail(status),
        _remediation(status),
    )


def forget_entry(entry_id: str) -> None:
    """Drop the reporting state for an entry that is going away."""
    _reported.pop(entry_id, None)


def reset_reporting() -> None:
    """Drop all reporting state, so the next shut gate reports again."""
    _reported.clear()


def _blocking_detail(status: AdoptionGateStatus) -> str:
    """Return the per-slot description of what is holding the gate shut."""
    details = [
        f"slot {slot} is {_UNREADABLE_REASON}"
        for slot in sorted(status.unreadable_slots)
    ]
    details.extend(
        f"slot {slot} {_UNMATCHED_REASON}" for slot in sorted(status.unmatched_slots)
    )
    return "; ".join(details)


def _remediation(status: AdoptionGateStatus) -> str:
    """Return the operator-facing recovery advice for a shut gate."""
    advice: list[str] = []
    if status.unreadable_slots:
        advice.append(
            "Restore readable Keymaster entities for the unreadable slots, "
            "reconfigure this entry's start slot or event count so the "
            "managed range no longer covers them, or call the "
            "rental_control.set_slot_exclusion action to exclude each dead "
            "slot."
        )
    if status.unmatched_slots:
        advice.append(
            "A slot holding an unmatched code usually belongs to a booking "
            "that has gone away; clear that code at the lock, or wait for "
            "Keymaster to finish clearing the slot, and issuance resumes on "
            "the next refresh."
        )
    return " ".join(advice)


__all__ = [
    "AdoptionGateStatus",
    "evaluate",
    "forget_entry",
    "report",
    "reset_reporting",
]
