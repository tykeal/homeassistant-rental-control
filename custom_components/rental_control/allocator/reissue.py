# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Pure helpers for forced door-code re-issue allocator state."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
import logging
from typing import TYPE_CHECKING

from . import reissue_state
from .models import AdoptionRequest
from .models import AllocationOwner
from .models import AllocationRecord
from .models import AllocationResult
from .models import CycleRequest
from .models import ForcedReissueDirective
from .models import ForcedReleaseExemption
from .models import ReissueOutcome
from .reissue_holds import forced_hold_matches_slot as forced_hold_matches_slot
from .reissue_holds import forced_hold_retention_reason as forced_hold_retention_reason
from .reissue_holds import forced_release_hold_key as forced_release_hold_key
from .reissue_holds import is_forced_release_hold as is_forced_release_hold
from .reissue_holds import merge_release_outcomes as merge_release_outcomes
from .reissue_holds import release_forced_holds as release_forced_holds

if TYPE_CHECKING:
    from .allocator import DoorCodeAllocator

StagedReissue = reissue_state.StagedReissue
_LOGGER = logging.getLogger(__name__)
_FAILURE_REASONS = {
    "exhausted",
    "adoption_pending",
    "unaccounted_slots",
    "recovery_fail_closed",
}


def apply_forced_reissues(
    allocator: DoorCodeAllocator, request: CycleRequest
) -> tuple[list[ReissueOutcome], list[StagedReissue]]:
    """Re-home forced re-issue targets onto guarded release holds."""
    outcomes: list[ReissueOutcome] = []
    staged: list[StagedReissue] = []
    for directive in request.forced_reissues:
        existing, _owner = reissue_state.existing_hold_for_directive(
            allocator, directive, is_forced_release_hold, forced_release_hold_key
        )
        if existing is not None:
            outcomes.append(
                _outcome(
                    directive,
                    replaced_code_ref=existing.code_ref,
                    disposition="held_pending_release",
                )
            )
            continue
        record, owner = reissue_state.select_reissue_owner(
            allocator, directive, request.rekeys
        )
        if record is None or owner is None:
            outcomes.append(
                _outcome(
                    directive,
                    replaced_code_ref=None,
                    disposition="no_existing_allocation",
                )
            )
            continue
        outcomes.append(_stage_directive(allocator, directive, record, owner, staged))
    return outcomes, staged


def fail_closed_forced_reissues(request: CycleRequest) -> list[ReissueOutcome]:
    """Report directives that cannot safely mutate a lost registry."""
    return [
        _outcome(
            directive,
            replaced_code_ref=None,
            disposition="failed",
            retention_reason="recovery_fail_closed",
        )
        for directive in request.forced_reissues
    ]


def finalize_forced_reissue_outcomes(
    allocator: DoorCodeAllocator,
    outcomes: list[ReissueOutcome],
    staged: list[StagedReissue],
    allocated: dict[str, AllocationResult],
) -> list[ReissueOutcome]:
    """Attach replacement refs to successful staged re-issue outcomes."""
    finalized: list[ReissueOutcome] = []
    for outcome in outcomes:
        stage = reissue_state.stage_for_outcome(staged, outcome)
        if stage is None or stage.allocation_identity_key is None:
            finalized.append(outcome)
            continue
        result = allocated.get(stage.allocation_identity_key)
        if outcome.disposition != "held_pending_release" or result is None:
            finalized.append(outcome)
            continue
        replacement_ref = allocator.code_ref(result.code) if result.code else None
        if result.code is not None:
            reissue_state.discard_removed_records(allocator, stage)
            _LOGGER.info(
                "Forced re-issue replacement entry=%s identity=%s "
                "lock=%s slot=%s replacement_code_ref=%s origin=%s",
                outcome.entry_id,
                outcome.identity_key,
                outcome.lockname,
                outcome.slot,
                replacement_ref,
                result.origin,
            )
        finalized.append(
            ReissueOutcome(
                outcome.entry_id,
                outcome.identity_key,
                outcome.lockname,
                outcome.slot,
                outcome.replaced_code_ref,
                replacement_ref,
                result.origin,
                outcome.disposition,
                outcome.retention_reason,
            )
        )
    return finalized


def rollback_blocked_reissues(
    allocator: DoorCodeAllocator,
    request: CycleRequest,
    staged: list[StagedReissue],
    allocated: dict[str, AllocationResult],
) -> list[ReissueOutcome]:
    """Restore staged owners whose target did not receive a replacement."""
    allocation_keys = {allocation.identity_key for allocation in request.allocations}
    rolled_back: list[ReissueOutcome] = []
    for stage in staged:
        if stage.allocation_identity_key is None:
            continue
        result = allocated.get(stage.allocation_identity_key)
        if result is not None and result.code is not None:
            continue
        reason = result.reason if result is not None else "no_allocation_request"
        if (
            reason not in _FAILURE_REASONS
            and stage.allocation_identity_key in allocation_keys
        ):
            continue
        reissue_state.restore_staged_owner(allocator, stage)
        restored = allocator._registry.record_for_identity(
            stage.allocation_identity_key
        )
        if restored is not None:
            owner = next(
                owner
                for owner in restored.owners
                if owner.identity_key == stage.allocation_identity_key
            )
            allocated[stage.allocation_identity_key] = AllocationResult(
                code=restored.code,
                origin=owner.origin,
                reason=reason,
            )
        _LOGGER.info(
            "Forced re-issue replaced-code disposition entry=%s identity=%s "
            "lock=%s slot=%s replaced_code_ref=%s disposition=failed "
            "retention_reason=%s",
            stage.entry_id,
            stage.identity_key,
            stage.lockname,
            stage.slot,
            stage.replaced_code_ref,
            reason,
        )
        rolled_back.append(
            ReissueOutcome(
                stage.entry_id,
                stage.identity_key,
                stage.lockname,
                stage.slot,
                stage.replaced_code_ref,
                None,
                None,
                "failed",
                reason,
            )
        )
    return rolled_back


def adoption_matches_forced_reissue(
    adoption: AdoptionRequest, request: CycleRequest
) -> bool:
    """Return whether an adoption would reclaim a forced re-issue target."""
    return any(
        (
            directive.identity_key is not None
            and adoption.identity_key == directive.identity_key
        )
        or (
            directive.identity_key is None
            and adoption.entry_id == directive.entry_id
            and adoption.lockname == directive.lockname
            and adoption.slot == directive.slot
        )
        for directive in request.forced_reissues
    )


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


def _stage_directive(
    allocator: DoorCodeAllocator,
    directive: ForcedReissueDirective,
    record: AllocationRecord,
    owner: AllocationOwner,
    staged: list[StagedReissue],
) -> ReissueOutcome:
    """Rename one selected owner to a forced-release hold."""
    original_identity = owner.identity_key
    hold_source = (
        directive.identity_key
        or reissue_state.observed_alias_base_identity(original_identity)
        or original_identity
    )
    hold_key = forced_release_hold_key(
        hold_source, directive.entry_id, directive.lockname, directive.slot
    )
    removed = reissue_state.remove_stale_target_owner(allocator, directive, owner)
    allocator._registry.by_identity.pop(original_identity, None)
    owner.identity_key = hold_key
    record.updated_at = datetime.now(UTC).isoformat()
    allocator._registry.by_identity[hold_key] = record.code
    staged.append(
        StagedReissue(
            directive.entry_id,
            directive.identity_key,
            directive.identity_key,
            original_identity,
            hold_key,
            directive.lockname,
            directive.slot,
            record.code_ref,
            tuple(removed),
        )
    )
    return _outcome(
        directive,
        replaced_code_ref=record.code_ref,
        disposition="held_pending_release",
    )


def _outcome(
    directive: ForcedReissueDirective,
    *,
    replaced_code_ref: str | None,
    disposition: str,
    retention_reason: str | None = None,
) -> ReissueOutcome:
    """Build a directive outcome row."""
    return ReissueOutcome(
        directive.entry_id,
        directive.identity_key,
        directive.lockname,
        directive.slot,
        replaced_code_ref,
        None,
        None,
        disposition,
        retention_reason,
    )
