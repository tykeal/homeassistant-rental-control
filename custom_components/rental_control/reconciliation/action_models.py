# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Action dataclasses for reconciliation."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field

from .enums import ActionKind


@dataclass(slots=True)
class SlotAction:
    """A single planned reconciliation action for one managed Keymaster slot.

    Instances are collected in :attr:`DesiredPlan.actions` and applied
    in order by the apply-plan phase.  Each instance couples an
    :class:`ActionKind` with the target slot and optional identity
    context so that the apply loop does not need to re-derive
    per-slot context.

    Attributes:
        kind: Type of action to perform.
        slot: Physical Keymaster slot number to act on.
        identity_key: Identity of the reservation being set or cleared;
            ``None`` for clears where no reservation is being assigned.
        reason: Optional human-readable explanation, e.g. overflow
            cause or drift description.
    """

    kind: ActionKind
    slot: int
    identity_key: str | None = None
    reason: str | None = None
    desired_id: str | None = None
    matched_by: str | None = None
    requires_confirmed_empty: bool = False
    sequence: list[str] = field(default_factory=list)
    preflight_read: bool = False
    blocked_reason: str | None = None
