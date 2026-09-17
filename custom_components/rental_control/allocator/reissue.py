# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Pure helpers for forced door-code re-issue allocator state."""

from __future__ import annotations

from .models import AllocationOwner
from .models import AllocationRecord
from .models import ForcedReleaseExemption


def forced_release_hold_key(
    identity_key: str, entry_id: str, lockname: str | None, slot: int | None
) -> str:
    """Return the reserved hold identity for one forced-release target."""
    lock_part = lockname if lockname is not None else "none"
    slot_part = str(slot) if slot is not None else "none"
    return f"{identity_key}:reissued:{entry_id}:{lock_part}:{slot_part}"


def is_forced_release_hold(key: str) -> bool:
    """Return whether an identity key is in the forced-release hold namespace."""
    parts = key.split(":")
    return len(parts) == 5 and parts[1] == "reissued"


def _conflict_exempt(
    record: AllocationRecord,
    owners: list[AllocationOwner],
    forced_release: ForcedReleaseExemption | None,
) -> bool:
    """Return whether a forced-release exemption matches one hold owner."""
    if forced_release is None or len(owners) != 1:
        return False
    owner = owners[0]
    return (
        owner.identity_key == forced_release.identity_key
        and owner.entry_id == forced_release.entry_id
        and record.code == forced_release.code
        and is_forced_release_hold(owner.identity_key)
    )
