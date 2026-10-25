# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Detect published door codes the configured generator cannot explain.

A reservation that was issued a phone-derived code keeps it for as long
as the booking lives.  The allocator short-circuits on the identity
key, and the identity key does not change when the feed stops carrying
phone numbers, so the code is handed back unchanged on every refresh
and never self-corrects.  Booking.com stopped publishing phone
information in late September 2026, so every booking issued a
``last_four`` code before then is now in this state (issue #862).

The code is still valid and still unique, so the defect is not safety:
it is that the published code silently stopped meaning what the
configuration says it means, with nothing logged and nothing surfaced.
Every divergence is therefore recorded for diagnostics.  Rotation is a
narrower promise, because changing a code a guest may already hold is
its own failure, and is offered only when the operator opted into
rotation with ``should_update_code`` *and* the stay has not started.

What this cannot do is tell a stale generated code from a PIN an
operator typed into the lock by hand.  Both read as a code the
generator would not produce now, exactly as ``_resolve_observed_code``
already notes for issue #736.  An operator who does not want
hand-entered PINs replaced should leave ``should_update_code`` off.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
import logging
from typing import TYPE_CHECKING
from typing import Any

from ..allocator import entry_info
from ..allocator.models import ForcedReissueDirective
from ..codegen import code_is_phone_derived
from ..codegen import generate_slot_code
from ..const import CONF_SHOULD_UPDATE_CODE
from ..reconciliation.desired import select_eligible_reservations
from .reissue import ReissueSuppression
from .reservations import rotation_window_open

if TYPE_CHECKING:
    from ..reconciliation import ManagedSlot
    from ..reconciliation import Reservation
    from .reissue import CodeResolutionRequest

_LOGGER = logging.getLogger(__name__)

#: Why a published code is no longer what its generator would produce.
#: Only one cause is detectable today, and naming it keeps the
#: diagnostics row readable without inviting a guess at any other.
PHONE_DIGITS_UNAVAILABLE = "phone_digits_unavailable"


@dataclass(frozen=True, slots=True)
class Divergence:
    """One reservation whose code the configured generator cannot explain."""

    identity_key: str
    configured_generator: str
    effective_generator: str
    reason: str
    rotating: bool
    rotation_attempted: bool = False


@dataclass(frozen=True, slots=True)
class DivergenceQuery:
    """Everything needed to judge one entry's published codes."""

    hass: Any
    entry_id: str
    lockname: str | None
    allocator: Any
    reservations: list[Reservation]
    managed_slots: list[ManagedSlot]
    match_slot: Any
    already_reissuing: frozenset[str] = frozenset()


_state: dict[str, dict[str, Divergence]] = {}


@dataclass(frozen=True, slots=True)
class _Attempt:
    """One rotation this module has already asked an identity to make.

    ``code`` is the code the rotation was requested away from, so a
    later refresh can tell a replacement that was issued from one that
    never arrived.  ``settled`` records that the replacement did arrive
    and that the identity is finished with, which must outlive the
    replacement itself.
    """

    code: str
    settled: bool


#: Rotations already asked for, per entry and identity.  A rotation is
#: offered once: the allocator may answer it with a collision-resolved
#: alternative rather than the exact code asked for, and re-reading that
#: alternative as a fresh divergence would ask for a rotation on every
#: refresh forever.
_attempts: dict[str, dict[str, _Attempt]] = {}


def rotate_stale_codes(
    request: CodeResolutionRequest, allocator: Any, match_slot: Any
) -> CodeResolutionRequest:
    """Return one cycle's request carrying the rotations it has earned.

    A rotating identity also joins the cycle's retention suppression.
    Its slot still physically holds the code being replaced, and that
    code is about to leave the registry with the allocation this cycle
    releases.  Suppressing retention is what makes the adoption step
    reserve the observed code under an alias, so the allocator cannot
    hand the still-programmed code to another booking before the
    replacement is confirmed.
    """
    directives = directives_for(request, allocator, match_slot)
    identities = {
        directive.identity_key
        for directive in directives
        if directive.identity_key is not None
    }
    if not identities:
        return request
    return replace(
        request,
        forced_reissues=request.forced_reissues + directives,
        suppression=ReissueSuppression(
            identity_keys=request.suppression.identity_keys | identities,
            slots=request.suppression.slots,
        ),
    )


def directives_for(
    request: CodeResolutionRequest, allocator: Any, match_slot: Any
) -> tuple[ForcedReissueDirective, ...]:
    """Return the rotations one cycle's request has earned."""
    return evaluate(
        DivergenceQuery(
            hass=request.hass,
            entry_id=request.entry_id,
            lockname=request.lockname,
            allocator=allocator,
            reservations=request.reservations,
            managed_slots=request.managed_slots,
            match_slot=match_slot,
            already_reissuing=frozenset(
                directive.identity_key
                for directive in request.forced_reissues
                if directive.identity_key is not None
            ),
        )
    )


def evaluate(query: DivergenceQuery) -> tuple[ForcedReissueDirective, ...]:
    """Record one entry's divergences and request the allowed rotations.

    Returns the forced re-issue directives the cycle should carry. The
    forced re-issue path is reused deliberately: it is the one mechanism
    that frees an identity's existing allocation, holds the replaced code
    until it is no longer programmed, and rolls back when the replacement
    cannot be issued.
    """
    entry_id = query.entry_id
    found: dict[str, Divergence] = {}
    directives: list[ForcedReissueDirective] = []
    if entry_info.uses_last_four(query.hass, entry_id):
        found, directives = _scan(query)
    previous = _state.get(entry_id, {})
    for identity_key, divergence in found.items():
        if previous.get(identity_key) != divergence:
            _log(entry_id, divergence)
    if found:
        _state[entry_id] = found
    else:
        _state.pop(entry_id, None)
    return tuple(directives)


def _scan(
    query: DivergenceQuery,
) -> tuple[dict[str, Divergence], list[ForcedReissueDirective]]:
    """Return one entry's divergent reservations and their directives."""
    hass = query.hass
    entry_id = query.entry_id
    generator = entry_info.code_generator(hass, entry_id)
    code_length = entry_info.configured_code_length(hass, entry_id)
    if generator is None or code_length is None:
        return {}, []
    should_update_code = _should_update_code(hass, entry_id)
    registry = getattr(query.allocator, "_registry", None)
    code_for_identity = getattr(registry, "code_for_identity", None)
    if not callable(code_for_identity):
        return {}, []
    found: dict[str, Divergence] = {}
    directives: list[ForcedReissueDirective] = []
    attempts = _attempts.setdefault(entry_id, {})
    consumed: set[int] = set()
    eligible = {
        reservation.identity_key
        for reservation in select_eligible_reservations(query.reservations)
    }
    for reservation in query.reservations:
        # Slot matching consumes slots in list order, so every
        # reservation must be offered to the matcher in that order even
        # when it is then skipped, exactly as the rotation guard does.
        slot = query.match_slot(reservation, query.managed_slots, consumed)
        if reservation.identity_key not in eligible:
            continue
        if reservation.code_rotation_pending:
            continue
        if reservation.identity_key in query.already_reissuing:
            continue
        if reservation.missing_count:
            # A reservation absent from the feed carries no event text,
            # so its empty phone evidence says nothing about the feed
            # having dropped phone numbers.  Retention wins.
            continue
        held = code_for_identity(reservation.identity_key)
        expected = generate_slot_code(
            generator, code_length, reservation.start, reservation.end, None, None
        )
        if not _diverges(reservation, held, expected, generator, code_length):
            continue
        attempt = attempts.get(reservation.identity_key)
        if attempt is not None and attempt.settled:
            continue
        if attempt is not None and held != attempt.code:
            # The allocator answered the rotation with a code of its own
            # choosing, which collision resolution may move away from
            # ``expected``.  The booking has been re-issued under the
            # current generator, so it is settled and stays settled: a
            # dropped marker would read the replacement as stale again
            # and move a future guest's PIN on alternating refreshes.
            attempts[reservation.identity_key] = _Attempt(held, settled=True)
            continue
        rotating = (
            should_update_code
            and attempt is None
            and _stay_has_not_started(reservation, slot)
        )
        found[reservation.identity_key] = Divergence(
            identity_key=reservation.identity_key,
            configured_generator=generator,
            effective_generator="date_based",
            reason=PHONE_DIGITS_UNAVAILABLE,
            rotating=rotating,
            rotation_attempted=attempt is not None,
        )
        if rotating:
            attempts[reservation.identity_key] = _Attempt(held, settled=False)
            reservation.slot_code = expected
            reservation.code_source = "generated"
            reservation.code_provenance = "generated"
            directives.append(
                ForcedReissueDirective(
                    entry_id,
                    reservation.identity_key,
                    query.lockname if slot is not None else None,
                    slot.slot if slot is not None else None,
                )
            )
    return found, directives


def _diverges(
    reservation: Reservation,
    held: str | None,
    expected: str,
    generator: str,
    code_length: int,
) -> bool:
    """Return whether a held code is one the generator cannot produce.

    The one divergence detectable here is a feed that stopped supplying
    phone numbers, so a reservation that still has phone digits is never
    divergent: its held code is either those digits or a
    collision-resolved substitute for them, and neither is this defect.
    Where there are no digits, generation can only have produced the
    date-based code, so any other held code is unexplained.
    """
    if held is None:
        return False
    last_four = reservation.phone_last_four
    if last_four is not None:
        return False
    if code_is_phone_derived(held, None, generator, code_length, last_four=None):
        return False
    return held != expected


def _stay_has_not_started(reservation: Reservation, slot: ManagedSlot | None) -> bool:
    """Return whether rotating this stay's code is still harmless.

    A lock-backed stay has two windows that can have opened: the one
    programmed into the slot and the one the feed now asks for. An entry
    that manages no lock has only the requested window, so that window
    stands in for both.
    """
    programmed_start = (
        getattr(slot, "actual_start", None)
        if slot is not None
        else reservation.buffered_start
    )
    return rotation_window_open(programmed_start, reservation.buffered_start)


def _should_update_code(hass: Any, entry_id: str) -> bool:
    """Return whether an entry opted into door-code rotation."""
    config = entry_info.entry_config(hass, entry_id)
    if config is None:
        return False
    return bool(config.get(CONF_SHOULD_UPDATE_CODE))


def _log(entry_id: str, divergence: Divergence) -> None:
    """Announce one divergence to the log exactly once per state."""
    _LOGGER.warning(
        "Entry %s identity %s holds a door code its %s generator cannot "
        "produce, because the calendar feed no longer supplies a phone "
        "number and code generation has fallen back to %s. %s",
        entry_id,
        divergence.identity_key,
        divergence.configured_generator,
        divergence.effective_generator,
        (
            "The code is being re-issued because the stay has not started "
            "and this entry updates codes."
            if divergence.rotating
            else "The code was left in place; see the integration "
            "diagnostics for the full list."
        ),
    )


def diagnostics(entry_id: str) -> list[dict[str, Any]] | None:
    """Return an entry's divergences for the diagnostics payload."""
    found = _state.get(entry_id)
    if not found:
        return None
    return [
        {
            "identity_key": divergence.identity_key,
            "configured_generator": divergence.configured_generator,
            "effective_generator": divergence.effective_generator,
            "divergence_reason": divergence.reason,
            "rotating": divergence.rotating,
            "rotation_attempted": divergence.rotation_attempted,
        }
        for divergence in sorted(found.values(), key=lambda item: item.identity_key)
    ]


def forget_entry(entry_id: str) -> None:
    """Drop an entry's divergences as it goes away."""
    _state.pop(entry_id, None)
    _attempts.pop(entry_id, None)


def reset_reporting() -> None:
    """Drop all divergence state, so the next cycle reports again."""
    _state.clear()
    _attempts.clear()


__all__ = [
    "PHONE_DIGITS_UNAVAILABLE",
    "directives_for",
    "Divergence",
    "DivergenceQuery",
    "diagnostics",
    "rotate_stale_codes",
    "evaluate",
    "forget_entry",
    "reset_reporting",
]
