# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Fail-closed association views over the protected snapshot."""

from __future__ import annotations

import threading
from typing import Any

import pytest

from custom_components.rental_control.allocator.association_transactions import (
    AssociationTransaction,
)
from custom_components.rental_control.allocator.association_transactions import (
    association_storage_record,
)
from custom_components.rental_control.allocator.associations import AssociationChange
from custom_components.rental_control.allocator.associations import AssociationMode
from custom_components.rental_control.allocator.associations import AssociationTuple
from custom_components.rental_control.allocator.models import (
    ProtectionAuthorizationState,
)
from custom_components.rental_control.allocator.models import ProtectionLoadResult
from custom_components.rental_control.allocator.models import ProtectionLoadState
from custom_components.rental_control.allocator.protected_associations import (
    association_for_entry,
)
from custom_components.rental_control.allocator.protected_associations import (
    association_references_for_entry,
)
from custom_components.rental_control.allocator.protected_associations import (
    associations_by_entry,
)
from custom_components.rental_control.allocator.protected_associations import (
    async_commit_association,
)

_TX1 = "a" * 32
_TX2 = "b" * 32


def _lockless() -> AssociationTuple:
    """Build a committed explicit-lockless tuple."""
    return AssociationTuple(
        True,
        None,
        None,
        1,
        _TX1,
        AssociationMode.EXPLICIT_LOCKLESS,
    )


def _data() -> dict[str, Any]:
    """Build a complete empty-safety snapshot for association projection."""
    return {
        "transaction_id": _TX1,
        "generation": 1,
        "associations": [association_storage_record("rc-1", _lockless())],
        "attestations": [],
        "pending_config": [],
        "holds": [],
        "ledger": {"code_ref_salt": "salt", "records": []},
        "pending_fences": [],
        "tombstones": [],
    }


class _Store:
    """Minimal protocol implementation recording typed association commits."""

    def __init__(
        self,
        *,
        state: ProtectionAuthorizationState = ProtectionAuthorizationState.READY,
        loaded: ProtectionLoadResult | None = None,
    ) -> None:
        """Initialize an in-memory protected-store protocol test double."""
        self._state_lock = threading.RLock()
        self._authorization_state = state
        self._loaded_result: ProtectionLoadResult | None = (
            loaded or ProtectionLoadResult(ProtectionLoadState.VALID_V2, data=_data())
        )
        self.committed: dict[str, Any] | None = None

    async def async_commit(self, envelope: dict[str, Any]) -> ProtectionLoadResult:
        """Record a committed envelope and return its acknowledged snapshot."""
        self.committed = envelope
        return ProtectionLoadResult(
            ProtectionLoadState.VALID_V2,
            data=envelope["data"],
        )


def test_ready_association_reads_require_one_valid_current_tuple() -> None:
    """Read APIs return only unique tuples from a READY v2 snapshot."""
    store = _Store()
    assert association_for_entry(store, "rc-1") == _lockless()
    assert associations_by_entry(store) == {"rc-1": _lockless()}
    assert association_references_for_entry(store, "rc-1") == ()

    duplicate = _Store()
    assert duplicate._loaded_result is not None
    assert duplicate._loaded_result.data is not None
    duplicate._loaded_result.data["associations"].append(
        association_storage_record("rc-1", _lockless())
    )
    assert association_for_entry(duplicate, "rc-1") is None
    assert associations_by_entry(duplicate) is None

    malformed = _Store()
    assert malformed._loaded_result is not None
    assert malformed._loaded_result.data is not None
    malformed._loaded_result.data["associations"] = [{"rc_entry_id": "rc-1"}]
    assert associations_by_entry(malformed) is None


def test_association_reads_and_commits_are_blocked_outside_ready_snapshot() -> None:
    """Loaded bytes alone cannot authorize an association operation."""
    store = _Store(state=ProtectionAuthorizationState.PROTECTIVE_UNKNOWN)
    assert association_for_entry(store, "rc-1") is None
    assert associations_by_entry(store) is None
    assert association_references_for_entry(store, "rc-1") is None


@pytest.mark.asyncio
async def test_association_commit_requires_ready_chain_and_appends_exactly() -> None:
    """A valid typed change commits; invalid predecessor metadata is refused."""
    store = _Store()
    old = _lockless()
    new = AssociationTuple(
        True,
        "Front Door",
        "km-selected",
        2,
        _TX2,
        AssociationMode.VERIFIED,
    )
    transaction = AssociationTransaction(
        _TX2,
        2,
        _TX1,
        (AssociationChange("rc-1", old, new),),
    )
    result = await async_commit_association(store, transaction)
    assert result.state is ProtectionLoadState.VALID_V2
    assert store.committed is not None
    assert store.committed["data"]["generation"] == 2
    assert store.committed["data"]["associations"] == [
        association_storage_record("rc-1", new)
    ]

    invalid_tx = "c" * 32
    invalid_tuple = AssociationTuple(
        True,
        "Front Door",
        "km-selected",
        2,
        invalid_tx,
        AssociationMode.VERIFIED,
    )
    invalid = AssociationTransaction(
        invalid_tx,
        2,
        "d" * 32,
        (AssociationChange("rc-1", old, invalid_tuple),),
    )
    refused = await async_commit_association(store, invalid)
    assert refused.state is ProtectionLoadState.UNAVAILABLE
    assert refused.failure_code == "transaction_preflight_failed"

    blocked = _Store(state=ProtectionAuthorizationState.UNVERIFIED)
    denied = await async_commit_association(blocked, transaction)
    assert denied.state is ProtectionLoadState.UNAVAILABLE
    assert denied.failure_code == "authorization_required"
    assert blocked.committed is None
