# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Commit orchestration for the protected registry store."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Protocol

from .models import ProtectionCommitToken
from .models import ProtectionLoadResult
from .models import ProtectionLoadState
from .models import StorageRunResult
from .models import StorageRunState
from .registry_commit import async_commit_protection_transaction
from .registry_safety_transitions import authorize_snapshot_owner_transition


@dataclass(frozen=True, slots=True)
class CommitPreconditions:
    """Keep worker-safe and event-loop-only commit checks distinct."""

    worker: Callable[[], ProtectionLoadResult | None] | None = None
    event_loop: Callable[[], ProtectionLoadResult | None] | None = None


class _CommitStore(Protocol):
    """Protected-store surface required by a single commit operation."""

    _async_lock: asyncio.Lock
    _storage_dir: Path

    @property
    def loaded_result(self) -> ProtectionLoadResult | None:
        """Return the retained protected snapshot."""
        ...

    def _commit_operation_preflight(self) -> ProtectionLoadResult | None:
        """Return a rejection if current state is not safe to commit."""
        ...

    def _begin_commit_operation(self) -> int:
        """Reserve the serialized protected-write operation."""
        ...

    def _apply_commit_completion(
        self,
        result: ProtectionLoadResult,
        *,
        operation_id: int | None = None,
    ) -> None:
        """Apply the durable operation outcome to authorization state."""
        ...

    def is_current_commit_token(self, token: ProtectionCommitToken) -> bool:
        """Check whether a token matches the currently authorized snapshot."""
        ...

    def _enter_protective_unknown(self, result: ProtectionLoadResult) -> None:
        """Latch uncertainty and block protective operations."""
        ...


class _CommitCallbacks:
    """Keep commit authorization and completion callbacks together."""

    def __init__(
        self,
        store: _CommitStore,
        envelope: dict[str, Any],
        precondition: Callable[[], ProtectionLoadResult | None] | None,
        authorize_owner_transition: Callable[[], bool] | None,
        on_acknowledged: Callable[[ProtectionLoadResult], None] | None,
    ) -> None:
        """Bind all callback state for one serialized commit."""
        self.store = store
        self.envelope = envelope
        self.precondition = precondition
        self.authorize_owner_transition = authorize_owner_transition
        self.on_acknowledged = on_acknowledged
        self.operation_id: int | None = None
        self.projection_failed = False

    def check_precondition(self) -> ProtectionLoadResult | None:
        """Recheck readiness and owner authorization under both locks."""
        rejection = self.store._commit_operation_preflight()
        if rejection is not None:
            return rejection
        if self.precondition is not None:
            try:
                rejection = self.precondition()
            except Exception:
                return _unavailable("snapshot_precondition_failed")
            if rejection is not None:
                return rejection
        target_data = self.envelope.get("data")
        if not isinstance(target_data, dict):
            return _unavailable("snapshot_precondition_failed")
        loaded = self.store.loaded_result
        if (
            loaded is not None
            and loaded.state is ProtectionLoadState.VALID_V2
            and not authorize_snapshot_owner_transition(
                loaded.data,
                target_data,
                self.authorize_owner_transition,
            )
        ):
            return _unavailable("owner_transition_unauthorized")
        return None

    def begin(self) -> None:
        """Invalidate prior generation tokens before protected I/O."""
        self.operation_id = self.store._begin_commit_operation()

    def complete(self, result: ProtectionLoadResult) -> None:
        """Apply the outcome and project acknowledged state without raw errors."""
        self.store._apply_commit_completion(result, operation_id=self.operation_id)
        if not self._has_current_acknowledgement(result):
            return
        if self.on_acknowledged is None:
            return
        try:
            self.on_acknowledged(result)
        except Exception:
            self.projection_failed = True
            self.store._enter_protective_unknown(
                ProtectionLoadResult(
                    ProtectionLoadState.PROTECTIVE_UNKNOWN,
                    data=result.data,
                    transaction_id=result.transaction_id,
                    generation=result.generation,
                    failure_code="snapshot_projection_failed",
                )
            )

    def _has_current_acknowledgement(self, result: ProtectionLoadResult) -> bool:
        """Return whether the completed write still authorizes projection."""
        if (
            result.state is not ProtectionLoadState.VALID_V2
            or result.transaction_id is None
            or result.generation is None
        ):
            return False
        return self.store.is_current_commit_token(
            ProtectionCommitToken(result.transaction_id, result.generation)
        )

    def project_result(self, result: ProtectionLoadResult) -> ProtectionLoadResult:
        """Return ambiguity if in-memory publication failed after durable ack."""
        if not self.projection_failed:
            return result
        return ProtectionLoadResult(
            ProtectionLoadState.PROTECTIVE_UNKNOWN,
            data=result.data,
            transaction_id=result.transaction_id,
            generation=result.generation,
            failure_code="snapshot_projection_failed",
        )


def _unavailable(code: str) -> ProtectionLoadResult:
    """Return a fixed-code rejection without caller-controlled details."""
    return ProtectionLoadResult(ProtectionLoadState.UNAVAILABLE, failure_code=code)


async def async_commit_protected_store(
    store: _CommitStore,
    envelope: dict[str, Any],
    *,
    legacy_migration_verified: bool,
    preconditions: CommitPreconditions,
    authorize_owner_transition: Callable[[], bool] | None,
    on_acknowledged: Callable[[ProtectionLoadResult], None] | None,
) -> ProtectionLoadResult:
    """Commit through the store's locks and apply only its current outcome."""
    rejection = store._commit_operation_preflight()
    if rejection is not None:
        store._apply_commit_completion(rejection)
        return rejection

    callbacks = _CommitCallbacks(
        store,
        envelope,
        preconditions.worker,
        authorize_owner_transition,
        on_acknowledged,
    )

    def begin_if_current() -> StorageRunResult | None:
        """Check event-loop state under both locks before worker submission."""
        if preconditions.event_loop is not None:
            try:
                rejection = preconditions.event_loop()
            except Exception:
                rejection = _unavailable("snapshot_precondition_failed")
            if rejection is not None:
                if rejection.state is ProtectionLoadState.BUSY:
                    return StorageRunResult(StorageRunState.BUSY)
                if rejection.state is ProtectionLoadState.PROTECTIVE_UNKNOWN:
                    return StorageRunResult(
                        StorageRunState.UNCERTAIN,
                        value=rejection,
                        failure_code=rejection.failure_code,
                    )
                return StorageRunResult(
                    StorageRunState.UNAVAILABLE,
                    failure_code=rejection.failure_code or "operation_failed",
                )
        callbacks.begin()
        return None

    result = await async_commit_protection_transaction(
        store._async_lock,
        store._storage_dir,
        envelope,
        legacy_migration_verified=legacy_migration_verified,
        on_start=begin_if_current,
        on_completion=callbacks.complete,
        operation_preflight=callbacks.check_precondition,
    )
    return callbacks.project_result(result)
