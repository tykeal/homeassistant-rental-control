# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Activate protected allocator storage before production mutations."""

from __future__ import annotations

import secrets
from time import monotonic
from typing import TYPE_CHECKING
from typing import Any

from ..const import DOMAIN
from ..const import PROTECTED_REGISTRY_STORE
from ..const import STORE_CODE_REGISTRY_KEY
from .models import ProtectionLoadResult
from .models import ProtectionLoadState
from .protected_store import ProtectedRegistryStore
from .registry import AllocationRegistry
from .registry_snapshot import serialize_exclusion_snapshots
from .registry_validation import validate_v2_envelope_shape
from .store import serialize_registry_ledger

if TYPE_CHECKING:
    from .allocator import DoorCodeAllocator


async def async_activate_protected_store(allocator: DoorCodeAllocator) -> None:
    """Load, migrate and publish one acknowledged v2 snapshot or stay unknown."""
    hass = allocator.hass
    store = ProtectedRegistryStore(hass, allocator._lock)
    hass.data.setdefault(DOMAIN, {})[PROTECTED_REGISTRY_STORE] = store

    pending_registry, pending_valid = _pending_legacy_registry(allocator)
    if not await _quiesce_legacy_writer(allocator):
        _fail_activation(allocator, store, "legacy_writer_quiesce_failed")
        return
    try:
        loaded = await store.async_load()
    except Exception:
        _fail_activation(allocator, store, "protected_load_failed")
        return

    if loaded.state is ProtectionLoadState.VALID_V2:
        _activate_valid_v2(
            allocator, store, loaded.data, pending_registry, pending_valid
        )
        return

    if loaded.state is ProtectionLoadState.LEGACY_V1:
        await _activate_legacy_v1(
            allocator, store, loaded.data, pending_registry, pending_valid
        )
        return

    if loaded.state is ProtectionLoadState.ABSENT:
        await _activate_absent(allocator, store, pending_registry, pending_valid)
        return

    _set_unknown_allocator_state(allocator)


def _activate_valid_v2(
    allocator: DoorCodeAllocator,
    store: ProtectedRegistryStore,
    data: dict[str, Any] | None,
    pending_registry: AllocationRegistry | None,
    pending_valid: bool,
) -> None:
    """Load an intact v2 ledger without passing it through HA Store."""
    registry = _registry_from_ledger(allocator, data)
    if registry is None or not pending_valid:
        _fail_activation(allocator, store, "ledger_invalid")
        return
    if pending_registry is not None and not _same_registry(pending_registry, registry):
        _fail_activation(allocator, store, "legacy_writer_mismatch")
        return
    _publish_loaded_registry(allocator, registry)


async def _activate_legacy_v1(
    allocator: DoorCodeAllocator,
    store: ProtectedRegistryStore,
    legacy_data: dict[str, Any] | None,
    pending_registry: AllocationRegistry | None,
    pending_valid: bool,
) -> None:
    """Validate intact v1 state, quiesce writers and acknowledge its migration."""
    registry = _validated_legacy_registry(
        allocator, store, legacy_data, pending_registry, pending_valid
    )
    if registry is None:
        return
    try:
        retry = await store.async_load()
    except Exception:
        _fail_activation(allocator, store, "protected_load_failed")
        return
    if retry.state is not ProtectionLoadState.LEGACY_V1 or retry.data != legacy_data:
        _fail_activation(allocator, store, "legacy_snapshot_changed")
        return
    envelope = _new_v2_envelope(allocator.hass, registry)
    if envelope is None:
        _fail_activation(allocator, store, "snapshot_preflight_failed")
        return
    if not await _commit_activation(store, envelope, legacy_migration_verified=True):
        _set_unknown_allocator_state(allocator)
        return
    _publish_loaded_registry(allocator, registry)


def _validated_legacy_registry(
    allocator: DoorCodeAllocator,
    store: ProtectedRegistryStore,
    data: dict[str, Any] | None,
    pending_registry: AllocationRegistry | None,
    pending_valid: bool,
) -> AllocationRegistry | None:
    """Require the parsed v1 disk, writer cache and live allocator to agree."""
    if not isinstance(data, dict):
        _fail_activation(allocator, store, "legacy_snapshot_unavailable")
        return None
    try:
        registry = allocator._store._registry_from_payload(data)
    except Exception:
        _fail_activation(allocator, store, "legacy_invalid")
        return None
    if (
        not pending_valid
        or (
            pending_registry is not None
            and not _same_registry(pending_registry, registry)
        )
        or (
            _has_live_registry_state(allocator)
            and not _same_registry(allocator._registry, registry)
        )
    ):
        _fail_activation(allocator, store, "legacy_state_mismatch")
        return None
    return registry


async def _activate_absent(
    allocator: DoorCodeAllocator,
    store: ProtectedRegistryStore,
    pending_registry: AllocationRegistry | None,
    pending_valid: bool,
) -> None:
    """Create protected genesis only after verifying no legacy state was lost."""
    if (
        not pending_valid
        or pending_registry is not None
        or _has_live_registry_state(allocator)
    ):
        _fail_activation(allocator, store, "legacy_state_without_disk")
        return
    registry = AllocationRegistry(code_ref_salt=secrets.token_hex(8))
    envelope = _new_v2_envelope(allocator.hass, registry)
    if envelope is None:
        _fail_activation(allocator, store, "snapshot_preflight_failed")
        return
    if not await _commit_activation(store, envelope):
        _set_unknown_allocator_state(allocator)
        return
    _publish_loaded_registry(allocator, registry)
    allocator._registry_lost = True
    allocator._registry_missing = True
    allocator._pending_adoption |= allocator._seed_pending_adoption()


async def _commit_activation(
    store: ProtectedRegistryStore,
    envelope: dict[str, Any],
    *,
    legacy_migration_verified: bool = False,
) -> bool:
    """Commit one v2 genesis or migration snapshot without raising to setup."""
    try:
        result = await store.async_commit(
            envelope,
            legacy_migration_verified=legacy_migration_verified,
        )
    except Exception:
        _block_activation(store, "protected_commit_failed")
        return False
    if result.state is not ProtectionLoadState.VALID_V2:
        _block_activation(store, "protected_commit_failed")
        return False
    return True


def _fail_activation(
    allocator: DoorCodeAllocator,
    store: ProtectedRegistryStore,
    failure_code: str,
) -> None:
    """Latch sanitized failure state and keep setup non-raising."""
    _block_activation(store, failure_code)
    _set_unknown_allocator_state(allocator)


def _pending_legacy_registry(
    allocator: DoorCodeAllocator,
) -> tuple[AllocationRegistry | None, bool]:
    """Decode only this integration's already-created delayed-save payload."""
    store = allocator._store._store
    pending = getattr(store, "_data", None)
    if pending is None:
        return None, True
    if not isinstance(pending, dict):
        return None, False
    payload: Any = pending.get("data")
    data_func = pending.get("data_func")
    if callable(data_func):
        try:
            payload = data_func()
        except Exception:
            return None, False
    if not isinstance(payload, dict):
        return None, False
    if pending.get("version") != store.version or pending.get("key") != store.key:
        return None, False
    try:
        return allocator._store._registry_from_payload(payload), True
    except Exception:
        return None, False


async def _quiesce_legacy_writer(allocator: DoorCodeAllocator) -> bool:
    """Disable delayed/final HA writes and drain any already-started write."""
    try:
        legacy = allocator._store._store
        legacy.make_read_only()
        legacy._async_cleanup_delay_listener()
        legacy._async_cleanup_final_write_listener()
        async with legacy._write_lock:
            legacy._data = None
    except Exception:
        return False
    return True


def _registry_from_ledger(
    allocator: DoorCodeAllocator, data: dict[str, Any] | None
) -> AllocationRegistry | None:
    """Parse the durable v2 ledger through the established allocator schema."""
    if not isinstance(data, dict):
        return None
    ledger = data.get("ledger")
    if not isinstance(ledger, dict):
        return None
    try:
        return allocator._store._registry_from_payload(ledger)
    except Exception:
        return None


def _same_registry(left: AllocationRegistry, right: AllocationRegistry) -> bool:
    """Compare every persisted owner and alias field, not serialization time."""
    return (
        left.code_ref_salt == right.code_ref_salt
        and left.records == right.records
        and left.announced_conflicts == right.announced_conflicts
        and left.by_identity == right.by_identity
    )


def _has_live_registry_state(allocator: DoorCodeAllocator) -> bool:
    """Detect any already-published allocator state before disk migration."""
    return bool(allocator._registry.records or allocator._registry.code_ref_salt)


def _new_v2_envelope(hass: Any, registry: AllocationRegistry) -> dict[str, Any] | None:
    """Build the complete empty-safety-section v2 activation snapshot."""
    try:
        exclusions = serialize_exclusion_snapshots(
            hass.config_entries.async_entries(DOMAIN)
        )
        data = {
            "schema_version": 2,
            "transaction_id": secrets.token_hex(16),
            "generation": 1,
            "ledger": serialize_registry_ledger(registry),
            "associations": [],
            "attestations": [],
            "pending_fences": [],
            "holds": [],
            "tombstones": [],
            "pending_config": [],
            "exclusions": exclusions,
        }
        envelope = {
            "version": 2,
            "minor_version": 1,
            "key": STORE_CODE_REGISTRY_KEY,
            "data": data,
        }
        return envelope if validate_v2_envelope_shape(envelope) else None
    except Exception:
        return None


def _publish_loaded_registry(
    allocator: DoorCodeAllocator, registry: AllocationRegistry
) -> None:
    """Publish a validated registry only after the protected store is READY."""
    allocator._registry = registry
    allocator._registry_lost = False
    allocator._registry_missing = False


def _set_unknown_allocator_state(allocator: DoorCodeAllocator) -> None:
    """Keep setup non-raising while withholding access under protective-unknown."""
    allocator._registry = AllocationRegistry()
    allocator._registry_lost = True
    allocator._registry_missing = False
    allocator._pending_adoption |= allocator._seed_pending_adoption()
    if allocator._pending_adoption and allocator._gate_deadline == 0.0:
        from .allocator import _ADOPTION_GATE_WARNING_SECONDS

        allocator._gate_deadline = monotonic() + _ADOPTION_GATE_WARNING_SECONDS


def _block_activation(store: ProtectedRegistryStore, code: str) -> None:
    """Latch one fixed protective failure without exposing caller data."""
    store._enter_protective_unknown(
        ProtectionLoadResult(
            ProtectionLoadState.PROTECTIVE_UNKNOWN,
            failure_code=code,
        )
    )


__all__ = ["async_activate_protected_store"]
