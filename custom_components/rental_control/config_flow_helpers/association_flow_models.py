# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Typed form and transaction intents for Keymaster association flow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..allocator.associations import AssociationMode
from ..allocator.associations import AssociationTuple


@dataclass(frozen=True, slots=True)
class AssociationFlowResult:
    """Association transition outcome and sanitized form errors."""

    user_input: dict[str, Any]
    errors: dict[str, str]


@dataclass(frozen=True, slots=True)
class AssociationIntent:
    """Validated options-flow intent before reading or mutating storage."""

    rc_entry_id: str
    form_input: dict[str, Any]
    submitted: dict[str, Any]
    old: AssociationTuple
    mode: AssociationMode
    keymaster_entry_id: str | None
    legacy_present: bool
    legacy_raw: str | None
    attest_same_lock: bool

    @property
    def changes_tuple(self) -> bool:
        """Return whether the committed association tuple changes."""
        return (
            self.mode is not self.old.mode
            or self.keymaster_entry_id != self.old.verified_keymaster_entry_id
            or (self.legacy_present, self.legacy_raw)
            != (
                self.old.legacy_lock_selection_present,
                self.old.legacy_lock_selection_raw,
            )
        )
