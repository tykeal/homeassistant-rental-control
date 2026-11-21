# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""READY-gated association views and protected transaction commits."""

from __future__ import annotations

from typing import Any
from typing import Protocol

from ..const import STORE_CODE_REGISTRY_KEY
from .association_commit import add_association_transaction_to_envelope
from .association_references import association_references_for_snapshot
from .association_transactions import AssociationTransaction
from .associations import AssociationTuple
from .models import ProtectionAuthorizationState
from .models import ProtectionLoadResult
from .models import ProtectionLoadState


class _AssociationStore(Protocol):
    """Narrow state and commit surface used by association operations."""

    _state_lock: Any
    _loaded_result: ProtectionLoadResult | None
    _authorization_state: ProtectionAuthorizationState

    async def async_commit(self, envelope: dict[str, Any]) -> ProtectionLoadResult:
        """Commit one typed protected envelope."""


def association_for_entry(
    store: _AssociationStore, rc_entry_id: str
) -> AssociationTuple | None:
    """Return one validated association only under the current READY gate."""
    with store._state_lock:
        loaded = store._loaded_result
        if (
            store._authorization_state is not ProtectionAuthorizationState.READY
            or loaded is None
            or loaded.state is not ProtectionLoadState.VALID_V2
            or loaded.data is None
        ):
            return None
        associations = loaded.data.get("associations")
        if not isinstance(associations, list):
            return None
        matches = [
            item
            for item in associations
            if isinstance(item, dict) and item.get("rc_entry_id") == rc_entry_id
        ]
        if len(matches) != 1:
            return None
        return AssociationTuple.from_record(matches[0].get("tuple"))


def associations_by_entry(
    store: _AssociationStore,
) -> dict[str, AssociationTuple] | None:
    """Return the complete validated association map under the READY gate."""
    with store._state_lock:
        loaded = store._loaded_result
        if (
            store._authorization_state is not ProtectionAuthorizationState.READY
            or loaded is None
            or loaded.state is not ProtectionLoadState.VALID_V2
            or loaded.data is None
        ):
            return None
        records = loaded.data.get("associations")
        if not isinstance(records, list):
            return None
        associations: dict[str, AssociationTuple] = {}
        for record in records:
            if not isinstance(record, dict):
                return None
            rc_entry_id = record.get("rc_entry_id")
            association = AssociationTuple.from_record(record.get("tuple"))
            if (
                not isinstance(rc_entry_id, str)
                or not rc_entry_id
                or association is None
                or rc_entry_id in associations
            ):
                return None
            associations[rc_entry_id] = association
        return associations


def association_references_for_entry(
    store: _AssociationStore, rc_entry_id: str
) -> tuple[str, ...] | None:
    """Return salted references for attributable work in the READY snapshot."""
    with store._state_lock:
        loaded = store._loaded_result
        if (
            store._authorization_state is not ProtectionAuthorizationState.READY
            or loaded is None
            or loaded.state is not ProtectionLoadState.VALID_V2
            or loaded.data is None
            or not isinstance(rc_entry_id, str)
            or not rc_entry_id
        ):
            return None
        return association_references_for_snapshot(loaded.data, rc_entry_id)


async def async_commit_association(
    store: _AssociationStore,
    transaction: AssociationTransaction,
) -> ProtectionLoadResult:
    """Build and commit a typed association transaction from READY state."""
    with store._state_lock:
        loaded = store._loaded_result
        if (
            store._authorization_state is not ProtectionAuthorizationState.READY
            or loaded is None
            or loaded.state is not ProtectionLoadState.VALID_V2
            or loaded.data is None
        ):
            return ProtectionLoadResult(
                ProtectionLoadState.UNAVAILABLE,
                failure_code="authorization_required",
            )
        base_envelope = {
            "version": 2,
            "minor_version": 1,
            "key": STORE_CODE_REGISTRY_KEY,
            "data": loaded.data,
        }
    target_envelope = add_association_transaction_to_envelope(
        base_envelope, transaction
    )
    if target_envelope is None:
        return ProtectionLoadResult(
            ProtectionLoadState.UNAVAILABLE,
            failure_code="transaction_preflight_failed",
        )
    return await store.async_commit(target_envelope)
