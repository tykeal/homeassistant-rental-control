# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Disk-backed protected-registry transaction restart scenarios."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
import hashlib
import json
from pathlib import Path
import shutil
from typing import TYPE_CHECKING
from typing import Any

import pytest

from custom_components.rental_control.allocator.models import ProtectionLoadState
from custom_components.rental_control.allocator.protected_registry import (
    async_commit_protection_transaction,
)
from custom_components.rental_control.allocator.protected_registry import (
    async_recover_protection_files,
)
from custom_components.rental_control.allocator.registry_validation import (
    prepare_exact_json_bytes,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


def _envelope(transaction_id: str, generation: int, fences: list[dict[str, Any]]):
    """Build a complete typed v2 envelope for isolated disk tests."""
    return {
        "version": 2,
        "minor_version": 1,
        "key": "rental_control.code_registry",
        "data": {
            "schema_version": 1,
            "transaction_id": transaction_id,
            "generation": generation,
            "ledger": {
                "schema_version": 1,
                "updated_at": "2026-11-21T00:00:00+00:00",
                "code_ref_salt": "test-salt",
                "records": [],
                "announced_conflicts": {},
            },
            "associations": [],
            "attestations": [],
            "pending_fences": fences,
            "holds": [],
            "tombstones": [],
            "pending_config": [],
        },
    }


def _fence() -> dict[str, Any]:
    """Build the original slot fence retained across a later crash."""
    return {
        "fence_id": "f" * 32,
        "original_km_entry_id": "original-entry",
        "slot": 7,
        "rc_entry_id": "rc-entry",
        "owner_ref": "owner-ref",
        "allocation_ref": "allocation-ref",
        "created_transaction_id": "1" * 32,
        "created_generation": 1,
        "fence_type": "pending_clear",
    }


@pytest.fixture
def isolated_storage(hass: HomeAssistant, tmp_path: Path) -> Iterator[Path]:
    """Use the current real HA instance's isolated configuration storage."""
    parent = Path(hass.config.path(f"registry-transactions-{tmp_path.name}"))
    parent.mkdir(parents=True, exist_ok=True)
    storage_dir = parent / ".storage"
    storage_dir.mkdir(parents=True, exist_ok=True)
    try:
        yield storage_dir
    finally:
        shutil.rmtree(parent, ignore_errors=True)


@pytest.mark.asyncio
async def test_genesis_commit_survives_fresh_runtime_load(
    hass: HomeAssistant,
    isolated_storage: Path,
) -> None:
    """A fresh recovery call re-acks exact committed bytes from HA storage."""
    storage = isolated_storage
    registry_path = storage / "rental_control.code_registry"
    marker_path = storage / "rental_control.code_registry_protection"
    envelope = _envelope("1" * 32, 1, [])

    committed = await async_commit_protection_transaction(
        asyncio.Lock(), storage, envelope
    )
    registry_bytes = registry_path.read_bytes()
    marker_bytes = marker_path.read_bytes()
    recovered = await async_recover_protection_files(asyncio.Lock(), storage)

    assert committed.state is ProtectionLoadState.VALID_V2
    assert recovered.state is ProtectionLoadState.VALID_V2
    assert registry_path.read_bytes() == registry_bytes
    assert marker_path.read_bytes() == marker_bytes
    assert (
        hashlib.sha256(registry_bytes).hexdigest()
        == json.loads(marker_bytes)["envelope_digest"]
    )


@pytest.mark.asyncio
async def test_restart_rolls_back_later_intent_without_losing_prior_fence(
    hass: HomeAssistant,
    isolated_storage: Path,
) -> None:
    """A crash after the pending marker retains an earlier dispatched fence."""
    storage = isolated_storage
    registry_path = storage / "rental_control.code_registry"
    marker_path = storage / "rental_control.code_registry_protection"
    fence = _fence()
    committed = await async_commit_protection_transaction(
        asyncio.Lock(),
        storage,
        _envelope("1" * 32, 1, [fence]),
    )
    assert committed.state is ProtectionLoadState.VALID_V2
    previous_bytes = registry_path.read_bytes()
    previous_marker = json.loads(marker_path.read_bytes())

    intended = _envelope("2" * 32, 2, [])
    intended_bytes = prepare_exact_json_bytes(intended)
    assert intended_bytes.digest is not None
    pending_marker = {
        "schema_version": 1,
        "activation_id": previous_marker["activation_id"],
        "state": "pending",
        "prior": previous_marker,
        "target_transaction_id": "2" * 32,
        "target_generation": 2,
        "target_digest": intended_bytes.digest,
        "kind": "commit",
    }
    pending_bytes = prepare_exact_json_bytes(pending_marker)
    assert pending_bytes.data is not None
    marker_path.write_bytes(pending_bytes.data)

    # A new lock/runtime has no cached transaction state; it must compare the
    # exact disk bytes and re-ack the predecessor, never infer target effects.
    recovered = await async_recover_protection_files(asyncio.Lock(), storage)

    assert recovered.state is ProtectionLoadState.VALID_V2
    assert recovered.transaction_id == "1" * 32
    assert registry_path.read_bytes() == previous_bytes
    assert json.loads(registry_path.read_bytes())["data"]["pending_fences"] == [fence]
    finalized = json.loads(marker_path.read_bytes())
    assert finalized["state"] == "finalized"
    assert finalized["committed_transaction_id"] == "1" * 32
