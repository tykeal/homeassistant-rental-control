# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Decide whether shared door-code owners are safe to release."""

from __future__ import annotations

from collections.abc import Collection

from . import reissue
from .models import AllocationOwner
from .models import AllocationRecord
from .models import CycleObservation
from .registry import slot_shadowed


def release_guard_reason(
    record: AllocationRecord,
    owners: list[AllocationOwner],
    observations: list[CycleObservation],
    *,
    refresh_observed: bool = True,
    forced_release: reissue.ForcedReleaseExemption | None = None,
    releasing: Collection[str] = (),
) -> str | None:
    """Return why owners must be retained, or None when safe to release.

    An owner whose slot another remaining booking already stands for is
    released without the conflict, lock and programmed-code checks: the
    record, and with it the code's reservation, outlives it, so those
    checks would only ever pin a stale identity to the record for good
    (#942). ``releasing`` names owners treated as already released.
    """
    gone = {owner.identity_key for owner in owners} | set(releasing)
    shadowed = {
        owner.identity_key for owner in owners if slot_shadowed(record, owner, gone)
    }
    if (
        len(record.owners) > 1
        and len(shadowed) < len(owners)
        and not reissue._conflict_exempt(record, owners, forced_release)
    ):
        return "adoption_conflict"
    for owner in owners:
        if owner.identity_key in shadowed:
            continue
        covered_observations = [
            observation
            for observation in observations
            if observation.lockname == owner.lockname
            and owner.slot in observation.managed_slots
        ]
        if owner.lockname is not None and not covered_observations:
            return "unverifiable_lock"
        if owner_still_programmed(
            record,
            owner,
            covered_observations,
            refresh_observed=refresh_observed,
        ):
            return "code_still_programmed"
    return None


def owner_still_programmed(
    record: AllocationRecord,
    owner: AllocationOwner,
    observations: list[CycleObservation],
    *,
    refresh_observed: bool = True,
) -> bool:
    """Refresh and return whether an owner may still be programmed."""
    if owner.lockname is None:
        if refresh_observed:
            owner.lock_observed = False
        return False
    covered = False
    for observation in observations:
        if observation.lockname != owner.lockname:
            continue
        covered = True
        programmed = (
            record.code in observation.observed_codes
            or owner.slot in observation.unreadable_slots
        )
        if programmed:
            if refresh_observed:
                owner.lock_observed = True
            return True
    if covered:
        if refresh_observed:
            owner.lock_observed = False
        return False
    return owner.lock_observed
