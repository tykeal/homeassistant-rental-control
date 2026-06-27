# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Reconciliation constants and enums."""

from __future__ import annotations

from enum import Enum

FINGERPRINT_VERSION = "v1"
"""Version tag for the primary reservation fingerprint scheme.

If the fingerprint algorithm changes in a future release this tag
must be bumped (e.g. to ``"v2"``) so that v1 and v2 fingerprints
occupy separate namespaces and old persisted keys can be preserved
in :attr:`Reservation.fingerprint_history` for conservative rematch.
"""


class SlotStatus(str, Enum):
    """Physical/logical state of a managed Keymaster slot.

    Values map 1-to-1 to the string literals stored in the HA Store so
    that serialisation is a no-op cast.

    Lifecycle transitions::

        FREE  ──set──►  OCCUPIED  ──desired gone──►  PENDING_CLEAR
                                                        │         │
                                             confirmed  │  failed │
                                                        ▼         ▼
                                                      FREE     BLOCKED
                                                        ▲         │
                                                        └─confirmed┘
        *  ──►  PHANTOM : name present, PIN absent/slot disabled
        *  ──►  UNKNOWN : actual entity state unreadable
    """

    FREE = "free"
    OCCUPIED = "occupied"
    PENDING_CLEAR = "pending_clear"
    BLOCKED = "blocked"
    PHANTOM = "phantom"
    UNKNOWN = "unknown"


class ObservedSlotStatus(str, Enum):
    """Stateless physical classification for an observed Keymaster slot."""

    EMPTY = "empty"
    OCCUPIED = "occupied"
    PHANTOM = "phantom"
    UNKNOWN = "unknown"


class ActionKind(str, Enum):
    """Reconciliation action type for one managed Keymaster slot.

    Used by :class:`SlotAction` and :class:`PlannedSlot` to label the
    physical operation (or absence of operation) that the planner
    determined for a slot during a single refresh cycle.
    """

    NOOP = "noop"
    """Actual already matches desired; no Keymaster service call needed."""

    ASSIGN = "assign"
    """Stateless action: write a desired reservation to a confirmed-empty slot."""

    UPDATE_IN_PLACE = "update_in_place"
    """Stateless action: update an existing name-matched physical slot."""

    RESET = "reset"
    """Stateless action: clear a stale, duplicate, or phantom physical slot."""

    SET = "set"
    """Slot is confirmed free; write name, code, and date range."""

    UPDATE_TIMES = "update_times"
    """Same reservation and code; only buffered date-range changed."""

    CLEAR = "clear"
    """Slot contains stale, duplicate, phantom, or expired state."""

    RETRY_CLEAR = "retry_clear"
    """Prior clear attempt unconfirmed; increment retry count and re-clear."""

    OVERWRITE_MANUAL_CHANGE = "overwrite_manual_change"
    """Actual state drifted from desired; restore desired and log drift."""

    BLOCKED = "blocked"
    """Actual state unknown or clear unconfirmed; no assignment allowed."""
