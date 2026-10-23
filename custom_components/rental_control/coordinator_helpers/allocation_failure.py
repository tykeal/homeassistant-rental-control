# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Operator reporting for lockless allocation failures.

A generator-only entry has no lock to fall back on, so when its
allocation step raises, every door code it publishes either stops
changing or disappears.  The traceback already reaches the log, but
nothing reaches the user interface, and the same failure then repeats
on every refresh for as long as it lasts.

Reporting is throttled by the failure's signature, the way
:mod:`.adoption_gate` throttles its gate report, so a condition that
recurs every refresh cycle raises one notification rather than one per
cycle.  A long-lived failure is re-announced only when it crosses an
escalation threshold, so the count a user sees stays truthful without
becoming noise.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.components.persistent_notification import async_create
from homeassistant.components.persistent_notification import async_dismiss

from ..const import DOMAIN
from ..const import NAME

_LOGGER = logging.getLogger(__name__)

_NOTIFICATION_ID = f"{DOMAIN}_lockless_allocation_failed"

# Consecutive failure counts that re-announce an ongoing failure. The
# notification is otherwise written once per distinct signature.
_ESCALATIONS = (10, 100, 1000)


@dataclass(slots=True)
class FailureState:
    """One entry's current run of consecutive allocation failures."""

    signature: str
    count: int = 1


_state: dict[str, FailureState] = {}


def signature_for(error: BaseException) -> str:
    """Return a comparable description of one failure condition."""
    return f"{type(error).__name__}: {error}"


def notification_id(entry_id: str) -> str:
    """Return the persistent notification id for an entry's failures."""
    return f"{_NOTIFICATION_ID}_{entry_id}"


def report(hass: Any, entry_id: str, name: str, error: BaseException) -> None:
    """Record a failed lockless cycle and surface it once per condition."""
    signature = signature_for(error)
    state = _state.get(entry_id)
    if state is None or state.signature != signature:
        state = FailureState(signature)
        _state[entry_id] = state
        announce = True
    else:
        state.count += 1
        announce = state.count in _ESCALATIONS
    if not announce:
        return
    _notify(hass, entry_id, name, state)


def resolve(hass: Any, entry_id: str, name: str) -> None:
    """Clear an entry's failure report after a cycle succeeds."""
    state = _state.pop(entry_id, None)
    if state is None:
        return
    _LOGGER.info(
        "Door code allocation recovered for %s after %d failed refresh "
        "%s (last failure: %s)",
        name,
        state.count,
        "cycle" if state.count == 1 else "cycles",
        state.signature,
    )
    dismiss(hass, entry_id)


def forget_entry(hass: Any, entry_id: str) -> None:
    """Drop and dismiss an entry's failure report as it goes away."""
    _state.pop(entry_id, None)
    dismiss(hass, entry_id)


def dismiss(hass: Any, entry_id: str) -> None:
    """Clear an entry's allocation-failure notification."""
    if not hasattr(hass, "bus"):
        return
    async_dismiss(hass, notification_id(entry_id))


def reset_reporting() -> None:
    """Drop all reporting state, so the next failure reports again."""
    _state.clear()


def diagnostics(entry_id: str) -> dict[str, Any] | None:
    """Return an entry's failure run for the diagnostics payload."""
    state = _state.get(entry_id)
    if state is None:
        return None
    return {
        "consecutive_failures": state.count,
        "last_error": state.signature,
    }


def _notify(hass: Any, entry_id: str, name: str, state: FailureState) -> None:
    """Write the persistent notification for an ongoing failure run."""
    if not hasattr(hass, "bus"):
        return
    cycles = "refresh cycle" if state.count == 1 else "consecutive refresh cycles"
    message = (
        f"Rental Control could not allocate door codes for {name} on "
        f"{state.count} {cycles}. The entry generates codes without "
        "managing a lock, so its sensors keep publishing the codes from "
        "the last successful cycle, or no code at all if none has "
        "succeeded yet. New or changed bookings will not receive a code "
        "until this clears. The failure is: "
        f"{state.signature}. Search the Home Assistant log for "
        f'"Lockless allocation failed for {name}" for the full '
        "traceback, and please report it with those log lines."
    )
    async_create(
        hass,
        message,
        title=f"{NAME} door code allocation failed",
        notification_id=notification_id(entry_id),
    )


__all__ = [
    "FailureState",
    "diagnostics",
    "dismiss",
    "forget_entry",
    "notification_id",
    "report",
    "reset_reporting",
    "resolve",
    "signature_for",
]
