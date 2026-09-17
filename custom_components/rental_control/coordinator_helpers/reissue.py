# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""In-memory coordinator state for forced door-code re-issue."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING
from typing import Any

from ..allocator.models import AdoptionRequest
from ..allocator.models import ForcedReissueDirective
from ..allocator.models import ReissueOutcome

if TYPE_CHECKING:
    from ..reconciliation import Reservation

_PENDING_ATTR = "_pending_reissues"
_COMPLETED_ATTR = "_completed_reissues"
_MAX_COMPLETED_REISSUES = 128


@dataclass(frozen=True, slots=True)
class ReissueSuppression:
    """One-cycle targets whose observed-code retention must be suppressed."""

    identity_keys: frozenset[str] = frozenset()
    slots: frozenset[int] = frozenset()


class AdoptionRequests(list[AdoptionRequest]):
    """Adoption requests plus skipped slots accounted by suppression."""

    def __init__(self) -> None:
        """Initialize the request list and suppressed slot set."""
        super().__init__()
        self.suppressed_slots: set[int] = set()


class ReissuePhase(StrEnum):
    """Track the in-memory lifecycle of one forced re-issue request."""

    ACCEPTED = "accepted"
    ISSUED = "issued"


@dataclass(slots=True)
class PendingReissue:
    """Transient state between an accepted call and its consuming cycle."""

    target_key: str
    identity_key: str | None
    lockname: str | None
    slot: int | None
    suppress_pending: bool = True
    phase: ReissuePhase = ReissuePhase.ACCEPTED
    replaced_code_ref: str | None = None
    replacement_code_ref: str | None = None
    requested_at: str | None = None
    invoker: str | None = None
    terminal_reason: str | None = None


@dataclass(frozen=True, slots=True)
class CodeResolutionRequest:
    """Inputs needed for one coordinator allocator resolution step."""

    hass: Any
    entry_id: str
    lockname: str | None
    code_length: int
    managed_slots: list[Any]
    reservations: list[Reservation]
    suppression: ReissueSuppression = ReissueSuppression()
    forced_reissues: tuple[ForcedReissueDirective, ...] = ()


@dataclass(frozen=True, slots=True)
class CodeResolutionResult:
    """Result of one coordinator allocator resolution step."""

    observation: Any
    reissues: tuple[ReissueOutcome, ...] = ()
    consumed_target_keys: tuple[str, ...] = ()


def target_key(identity_key: str | None, lockname: str | None, slot: int | None) -> str:
    """Return the in-memory pending-state key for one re-issue target."""
    if identity_key is not None:
        return identity_key
    return f"slot:{lockname}:{slot}"


def pending_reissues(coordinator: Any) -> dict[str, PendingReissue]:
    """Return the coordinator's in-memory pending re-issues."""
    pending = getattr(coordinator, _PENDING_ATTR, None)
    if not isinstance(pending, dict):
        pending = {}
        setattr(coordinator, _PENDING_ATTR, pending)
    return pending


def completed_reissues(coordinator: Any) -> dict[str, ReissueOutcome]:
    """Return same-runtime completed re-issue fingerprints."""
    completed = getattr(coordinator, _COMPLETED_ATTR, None)
    if not isinstance(completed, dict):
        completed = {}
        setattr(coordinator, _COMPLETED_ATTR, completed)
    return completed


def coerce_code_resolution_request(
    request: CodeResolutionRequest | Any,
    entry_id: str | None,
    lockname: str | None,
    code_length: int | None,
    managed_slots: list[Any] | None,
    reservations: list[Reservation] | None,
) -> CodeResolutionRequest:
    """Normalize legacy and request-object code-resolution calls."""
    if isinstance(request, CodeResolutionRequest):
        return request
    if (
        entry_id is None
        or code_length is None
        or managed_slots is None
        or reservations is None
    ):
        msg = "legacy async_resolve_codes calls require all cycle inputs"
        raise TypeError(msg)
    return CodeResolutionRequest(
        hass=request,
        entry_id=entry_id,
        lockname=lockname,
        code_length=code_length,
        managed_slots=managed_slots,
        reservations=reservations,
    )


def current_suppression(coordinator: Any) -> ReissueSuppression:
    """Build the one-cycle retention suppression for pending targets."""
    identities: set[str] = set()
    slots: set[int] = set()
    for pending in pending_reissues(coordinator).values():
        if not pending.suppress_pending:
            continue
        if pending.identity_key is not None:
            identities.add(pending.identity_key)
        elif pending.slot is not None:
            slots.add(pending.slot)
    return ReissueSuppression(frozenset(identities), frozenset(slots))


def forced_reissue_directives(
    coordinator: Any, reservations: list[Reservation] | None = None
) -> tuple[ForcedReissueDirective, ...]:
    """Return directives that should be consumed by this refresh cycle."""
    live_slots = _live_mapped_slots(coordinator, reservations or [])
    return tuple(
        ForcedReissueDirective(
            coordinator._entry_id,
            pending.identity_key,
            pending.lockname,
            pending.slot,
        )
        for pending in pending_reissues(coordinator).values()
        if pending.suppress_pending
        and not (
            pending.identity_key is None
            and pending.slot is not None
            and pending.slot in live_slots
        )
    )


def remove_pending_ghost_targets(
    coordinator: Any,
    persisted: dict[str, Any],
    live_identity_keys: set[str],
) -> None:
    """Drop persisted ghosts for pending targets before ghost hydration."""
    identity_keys = {
        item.identity_key
        for item in pending_reissues(coordinator).values()
        if item.suppress_pending
        and item.identity_key
        and item.identity_key not in live_identity_keys
    }
    slot_targets = {
        item.slot
        for item in pending_reissues(coordinator).values()
        if item.suppress_pending
        and item.identity_key is None
        and item.lockname == coordinator.lockname
        and item.slot is not None
    }
    for identity_key in identity_keys:
        persisted.pop(identity_key, None)
    for identity_key, mapping in list(persisted.items()):
        if (
            identity_key not in live_identity_keys
            and isinstance(mapping, dict)
            and mapping.get("slot") in slot_targets
        ):
            persisted.pop(identity_key, None)


def consume_cycle_result(coordinator: Any, result: CodeResolutionResult) -> None:
    """Advance pending state after the cycle that consumed suppression."""
    pending = pending_reissues(coordinator)
    for key in result.consumed_target_keys:
        pending_item = pending.get(key)
        if pending_item is not None:
            pending_item.suppress_pending = False
    for outcome in result.reissues:
        key = target_key(outcome.identity_key, outcome.lockname, outcome.slot)
        pending_item = pending.get(key)
        if pending_item is None:
            key, pending_item = _pending_slot_match(
                pending, outcome.lockname, outcome.slot
            )
        if pending_item is None:
            continue
        pending_item.replaced_code_ref = outcome.replaced_code_ref
        pending_item.replacement_code_ref = outcome.replacement_code_ref
        pending_item.terminal_reason = outcome.retention_reason
        if outcome.disposition == "held_pending_release":
            pending_item.phase = ReissuePhase.ISSUED
            continue
        _remember_completed(coordinator, key, outcome)
        pending.pop(key, None)


def _pending_slot_match(
    pending: dict[str, PendingReissue],
    lockname: str | None,
    slot: int | None,
) -> tuple[str, PendingReissue | None]:
    """Return a pending bare-slot target matching a release outcome."""
    for key, item in pending.items():
        if (
            item.identity_key is None
            and item.lockname == lockname
            and item.slot == slot
        ):
            return key, item
    return "", None


def _remember_completed(coordinator: Any, key: str, outcome: ReissueOutcome) -> None:
    """Remember a bounded set of same-runtime completed re-issues."""
    completed = completed_reissues(coordinator)
    completed[key] = outcome
    while len(completed) > _MAX_COMPLETED_REISSUES:
        completed.pop(next(iter(completed)))


def _live_mapped_slots(coordinator: Any, reservations: list[Reservation]) -> set[int]:
    """Return pending bare slots that live reservations already occupy."""
    mappings = getattr(coordinator, "_slot_mappings", {}).get("mappings", {})
    live_keys = {reservation.identity_key for reservation in reservations}
    slots: set[int] = set()
    for identity_key, mapping in mappings.items():
        if identity_key not in live_keys or not isinstance(mapping, dict):
            continue
        slot = mapping.get("slot")
        if isinstance(slot, int):
            slots.add(slot)
    return slots
