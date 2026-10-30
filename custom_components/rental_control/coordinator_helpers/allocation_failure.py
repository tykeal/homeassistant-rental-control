# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Operator reporting for refresh-cycle door code failures.

A generator-only entry has no lock to fall back on, so when its
allocation step raises, every door code it publishes either stops
changing or disappears.  A lock-backed entry whose reconciliation
raises stops programming its lock.  In both cases the traceback
already reaches the log, but nothing reaches the user interface, and
the same failure then repeats on every refresh for as long as it lasts.

Each :class:`FailureKind` keeps its own state and notification, so a
lockless allocation failure and a reconciliation failure never
overwrite each other.

Reporting is throttled by the failure's signature, the way
:mod:`.adoption_gate` throttles its gate report, so a condition that
recurs every refresh cycle raises one notification rather than one per
cycle.  A long-lived failure is re-announced only when it crosses an
escalation threshold, so the count a user sees stays truthful without
becoming noise.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
import logging
from typing import Any

from homeassistant.components.persistent_notification import async_create
from homeassistant.components.persistent_notification import async_dismiss

from ..const import DOMAIN
from ..const import NAME

_LOGGER = logging.getLogger(__name__)

# Consecutive failure counts that re-announce an ongoing failure. The
# notification is otherwise written once per distinct signature.
_ESCALATIONS = (10, 100, 1000)


@dataclass(frozen=True, slots=True)
class FailureKind:
    """One kind of refresh-cycle failure with its own notification."""

    key: str
    activity: str
    title: str
    message: str = field(repr=False)


LOCKLESS = FailureKind(
    key="lockless_allocation",
    activity="Door code allocation",
    title=f"{NAME} door code allocation failed",
    message=(
        "Rental Control could not allocate door codes for {name} on "
        "{count} {cycles}. The entry generates codes without "
        "managing a lock, so its sensors keep publishing the codes from "
        "the last successful cycle, or no code at all if none has "
        "succeeded yet. New or changed bookings will not receive a code "
        "until this clears. The failure is: "
        "{signature}. Search the Home Assistant log for "
        '"Lockless allocation failed for {name}" for the full '
        "traceback, and please report it with those log lines."
    ),
)

RECONCILIATION = FailureKind(
    key="reconciliation",
    activity="Door code reconciliation",
    title=f"{NAME} door code reconciliation failed",
    message=(
        "Rental Control could not reconcile door codes for {name} with "
        "its lock on {count} {cycles}. A failed cycle may have updated "
        "only some slots, so check the codes actually on the lock. New, "
        "changed or ended bookings may not be programmed or cleared until "
        "this clears. The failure is: "
        "{signature}. Search the Home Assistant log for "
        '"Reconciliation failed for {name}" for the full traceback, and '
        "please report it with those log lines."
    ),
)

KINDS = (LOCKLESS, RECONCILIATION)


@dataclass(slots=True)
class FailureState:
    """One entry's current run of consecutive failures of one kind."""

    signature: str
    count: int = 1


_state: dict[tuple[str, str], FailureState] = {}


def signature_for(error: BaseException) -> str:
    """Return a comparable description of one failure condition."""
    return f"{type(error).__name__}: {error}"


def notification_id(entry_id: str, kind: FailureKind = LOCKLESS) -> str:
    """Return the persistent notification id for an entry's failures."""
    return f"{DOMAIN}_{kind.key}_failed_{entry_id}"


def report(
    hass: Any,
    entry_id: str,
    name: str,
    error: BaseException,
    kind: FailureKind = LOCKLESS,
) -> None:
    """Record a failed cycle and surface it once per condition.

    The streak counts refresh cycles that failed, so only a successful
    cycle may reset it. A different exception part way through an
    unbroken run is a changed condition, not a fresh run: it re-announces
    so the operator sees the new error, while the count keeps climbing
    towards the next escalation.
    """
    signature = signature_for(error)
    state = _state.get((kind.key, entry_id))
    if state is None:
        state = FailureState(signature)
        _state[(kind.key, entry_id)] = state
        announce = True
    else:
        state.count += 1
        announce = state.signature != signature or state.count in _ESCALATIONS
        state.signature = signature
    if not announce:
        return
    _notify(hass, entry_id, name, state, kind)


def resolve(hass: Any, entry_id: str, name: str, kind: FailureKind = LOCKLESS) -> None:
    """Clear an entry's failure report after a cycle succeeds."""
    state = _state.pop((kind.key, entry_id), None)
    if state is None:
        return
    _LOGGER.info(
        "%s recovered for %s after %d failed refresh %s (last failure: %s)",
        kind.activity,
        name,
        state.count,
        "cycle" if state.count == 1 else "cycles",
        state.signature,
    )
    dismiss(hass, entry_id, kind)


def has_report(entry_id: str, kind: FailureKind = LOCKLESS) -> bool:
    """Return whether an entry currently has an open failure report."""
    return (kind.key, entry_id) in _state


def forget(hass: Any, entry_id: str, kind: FailureKind) -> None:
    """Drop an open report of one kind that no longer applies.

    An entry that gains or loses a lock is reconfigured in place, so it
    stops running the cycle that would otherwise resolve the report.
    """
    if _state.pop((kind.key, entry_id), None) is not None:
        dismiss(hass, entry_id, kind)


def forget_entry(hass: Any, entry_id: str) -> None:
    """Drop and dismiss every failure report of an entry as it goes away."""
    for kind in KINDS:
        _state.pop((kind.key, entry_id), None)
        dismiss(hass, entry_id, kind)


def dismiss(hass: Any, entry_id: str, kind: FailureKind = LOCKLESS) -> None:
    """Clear an entry's failure notification of one kind."""
    if not hasattr(hass, "bus"):
        return
    async_dismiss(hass, notification_id(entry_id, kind))


def reset_reporting() -> None:
    """Drop all reporting state, so the next failure reports again."""
    _state.clear()


def diagnostics(entry_id: str, kind: FailureKind = LOCKLESS) -> dict[str, Any] | None:
    """Return an entry's failure run for the diagnostics payload."""
    state = _state.get((kind.key, entry_id))
    if state is None:
        return None
    return {
        "consecutive_failures": state.count,
        "last_error": state.signature,
    }


def _notify(
    hass: Any, entry_id: str, name: str, state: FailureState, kind: FailureKind
) -> None:
    """Write the persistent notification for an ongoing failure run."""
    if not hasattr(hass, "bus"):
        return
    cycles = "refresh cycle" if state.count == 1 else "consecutive refresh cycles"
    message = kind.message.format(
        name=name, count=state.count, cycles=cycles, signature=state.signature
    )
    async_create(
        hass,
        message,
        title=kind.title,
        notification_id=notification_id(entry_id, kind),
    )


__all__ = [
    "KINDS",
    "LOCKLESS",
    "RECONCILIATION",
    "FailureKind",
    "FailureState",
    "diagnostics",
    "dismiss",
    "forget",
    "forget_entry",
    "has_report",
    "notification_id",
    "report",
    "reset_reporting",
    "resolve",
    "signature_for",
]
