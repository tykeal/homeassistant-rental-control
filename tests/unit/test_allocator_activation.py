# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Validate fail-closed parsing before protected activation."""

from __future__ import annotations

from typing import Any
from typing import cast

import pytest

from custom_components.rental_control.allocator import activation
from custom_components.rental_control.allocator.activation import (
    _has_live_registry_state,
)
from custom_components.rental_control.allocator.activation import _new_v2_envelope
from custom_components.rental_control.allocator.activation import (
    _pending_legacy_registry,
)
from custom_components.rental_control.allocator.activation import _quiesce_legacy_writer
from custom_components.rental_control.allocator.activation import _registry_from_ledger
from custom_components.rental_control.allocator.activation import _same_registry
from custom_components.rental_control.allocator.activation import (
    _validated_legacy_registry,
)
from custom_components.rental_control.allocator.allocator import DoorCodeAllocator
from custom_components.rental_control.allocator.models import ProtectionLoadResult
from custom_components.rental_control.allocator.models import ProtectionLoadState
from custom_components.rental_control.allocator.registry import AllocationRegistry
from custom_components.rental_control.allocator.store import serialize_registry_ledger


def _pending_store(allocator: DoorCodeAllocator, payload: object) -> Any:
    """Build the real HA Store's delayed-save cache shape."""
    store = allocator._store._store
    store._data = {
        "version": store.version,
        "key": store.key,
        "data": payload,
        "data_func": None,
    }
    return store


def test_legacy_pending_payload_requires_exact_store_metadata_and_schema(
    hass,
) -> None:
    """Only the integration's own decodable delayed payload is migration evidence."""
    allocator = DoorCodeAllocator(hass)
    store = allocator._store._store
    assert _pending_legacy_registry(allocator) == (None, True)

    store._data = cast(Any, "not-a-save")
    assert _pending_legacy_registry(allocator) == (None, False)

    payload = serialize_registry_ledger(
        AllocationRegistry(code_ref_salt="pending-legacy-salt")
    )
    pending = _pending_store(allocator, payload)
    registry, valid = _pending_legacy_registry(allocator)
    assert valid
    assert registry is not None
    assert registry.code_ref_salt == "pending-legacy-salt"

    pending._data["version"] = pending.version + 1
    assert _pending_legacy_registry(allocator) == (None, False)
    pending._data["version"] = pending.version
    pending._data["key"] = "another.integration"
    assert _pending_legacy_registry(allocator) == (None, False)
    pending._data["key"] = pending.key
    pending._data["data"] = {"unsupported": True}
    assert _pending_legacy_registry(allocator) == (None, False)
    pending._data["data"] = "not-a-ledger"
    assert _pending_legacy_registry(allocator) == (None, False)


def test_pending_data_function_is_evaluated_once_and_failure_is_unknown(hass) -> None:
    """A delayed writer callback cannot silently replace or corrupt evidence."""
    allocator = DoorCodeAllocator(hass)
    store = _pending_store(
        allocator,
        serialize_registry_ledger(
            AllocationRegistry(code_ref_salt="pending-legacy-salt")
        ),
    )
    calls = 0

    def payload_factory() -> dict[str, Any]:
        """Return the exact delayed legacy payload captured at activation."""
        nonlocal calls
        calls += 1
        return serialize_registry_ledger(
            AllocationRegistry(code_ref_salt="factory-legacy-salt")
        )

    store._data["data_func"] = payload_factory
    parsed, valid = _pending_legacy_registry(allocator)
    assert valid
    assert calls == 1
    assert parsed is not None
    assert parsed.code_ref_salt == "factory-legacy-salt"

    def fail_payload() -> dict[str, Any]:
        """Represent an exception from an existing HA delayed save."""
        raise RuntimeError("private writer failure")

    store._data["data_func"] = fail_payload
    assert _pending_legacy_registry(allocator) == (None, False)


def test_v2_ledger_parser_and_live_state_comparison_fail_closed(hass) -> None:
    """Malformed ledger snapshots and preexisting live state are detected."""
    allocator = DoorCodeAllocator(hass)
    registry = AllocationRegistry(code_ref_salt="activation-ledger-salt")
    ledger = serialize_registry_ledger(registry)

    assert _registry_from_ledger(allocator, {"ledger": ledger}) == registry
    assert _registry_from_ledger(allocator, None) is None
    assert _registry_from_ledger(allocator, {"ledger": []}) is None
    assert _has_live_registry_state(allocator) is False
    allocator._registry = AllocationRegistry(code_ref_salt="live-state")
    assert _has_live_registry_state(allocator) is True
    assert not _same_registry(registry, allocator._registry)
    assert _same_registry(
        registry, AllocationRegistry(code_ref_salt=registry.code_ref_salt)
    )


def test_legacy_and_v2_schema_parser_exceptions_fail_closed(
    hass,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unparseable on-disk payloads never become empty protected state."""
    allocator = DoorCodeAllocator(hass)
    store = _FixedActivationStore(
        ProtectionLoadResult(ProtectionLoadState.LEGACY_V1, data={"records": []})
    )

    def reject_payload(_payload: object) -> AllocationRegistry:
        """Simulate a parser rejecting malformed persisted data."""
        raise ValueError("private registry parser detail")

    monkeypatch.setattr(allocator._store, "_registry_from_payload", reject_payload)

    assert _registry_from_ledger(allocator, {"ledger": {"records": []}}) is None
    assert (
        _validated_legacy_registry(
            allocator,
            store,  # type: ignore[arg-type]
            {"records": []},
            None,
            True,
        )
        is None
    )
    assert store.unknown_result is not None
    assert store.unknown_result.failure_code == "legacy_invalid"


def test_activation_envelope_contains_complete_empty_safety_sections(hass) -> None:
    """First activation constructs a complete schema before durable commit."""
    envelope = _new_v2_envelope(
        hass, AllocationRegistry(code_ref_salt="activation-envelope-salt")
    )

    assert envelope is not None
    assert envelope["version"] == 2
    assert envelope["data"]["generation"] == 1
    assert envelope["data"]["associations"] == []
    assert envelope["data"]["attestations"] == []
    assert envelope["data"]["pending_fences"] == []
    assert envelope["data"]["holds"] == []
    assert envelope["data"]["tombstones"] == []
    assert envelope["data"]["pending_config"] == []
    assert envelope["data"]["exclusions"] == []


async def test_quiesce_failure_stops_protected_loading(
    hass,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A delayed legacy writer that cannot be drained blocks activation."""
    allocator = DoorCodeAllocator(hass)
    allocator._protection_required = True
    store = _FixedActivationStore(ProtectionLoadResult(ProtectionLoadState.ABSENT))
    load_calls = 0

    async def fail_quiesce(_allocator: DoorCodeAllocator) -> bool:
        """Report that the legacy delayed writer could not be drained."""
        return False

    async def track_load() -> ProtectionLoadResult:
        """Count store reads to prove activation stopped before loading."""
        nonlocal load_calls
        load_calls += 1
        return ProtectionLoadResult(ProtectionLoadState.ABSENT)

    store.async_load = track_load  # type: ignore[method-assign]
    monkeypatch.setattr(activation, "ProtectedRegistryStore", lambda *_args: store)
    monkeypatch.setattr(activation, "_quiesce_legacy_writer", fail_quiesce)

    await activation.async_activate_protected_store(allocator)

    assert load_calls == 0
    assert store.unknown_result is not None
    assert store.unknown_result.failure_code == "operation_failed"
    assert allocator._registry_lost


async def test_protected_load_exception_keeps_allocator_unknown(
    hass,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unavailable protected load does not continue with the legacy store."""
    allocator = DoorCodeAllocator(hass)
    allocator._protection_required = True

    class BrokenLoadStore(_FixedActivationStore):
        """Raise at the real activation load boundary."""

        async def async_load(self) -> ProtectionLoadResult:
            """Raise a private storage error for the activation test."""
            raise OSError("private load detail")

    store = BrokenLoadStore(ProtectionLoadResult(ProtectionLoadState.ABSENT))
    monkeypatch.setattr(activation, "ProtectedRegistryStore", lambda *_args: store)

    await activation.async_activate_protected_store(allocator)

    assert store.unknown_result is not None
    assert store.unknown_result.failure_code == "operation_failed"
    assert allocator._registry_lost
    assert allocator._registry_missing is False


async def test_absent_store_rejects_unverifiable_pending_legacy_state(
    hass,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No genesis snapshot can replace a pending write with untrusted contents."""
    allocator = DoorCodeAllocator(hass)
    allocator._protection_required = True
    store = _FixedActivationStore(ProtectionLoadResult(ProtectionLoadState.ABSENT))
    monkeypatch.setattr(activation, "ProtectedRegistryStore", lambda *_args: store)
    monkeypatch.setattr(
        activation,
        "_pending_legacy_registry",
        lambda _allocator: (None, False),
    )

    await activation.async_activate_protected_store(allocator)

    assert store.unknown_result is not None
    assert store.unknown_result.failure_code == "operation_failed"
    assert allocator._registry_lost
    assert allocator._registry_missing is False


async def test_absent_genesis_preflight_failure_stays_protective_unknown(
    hass,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An incomplete first snapshot cannot authorize a fresh allocation epoch."""
    allocator = DoorCodeAllocator(hass)
    allocator._protection_required = True
    store = _FixedActivationStore(ProtectionLoadResult(ProtectionLoadState.ABSENT))
    monkeypatch.setattr(activation, "ProtectedRegistryStore", lambda *_args: store)
    monkeypatch.setattr(
        activation,
        "_pending_legacy_registry",
        lambda _allocator: (None, True),
    )
    monkeypatch.setattr(activation, "_new_v2_envelope", lambda *_args: None)

    await activation.async_activate_protected_store(allocator)

    assert store.unknown_result is not None
    assert store.unknown_result.failure_code == "operation_failed"
    assert allocator._registry_lost
    assert not allocator._registry_missing


async def test_legacy_writer_quiesce_exception_is_fail_closed(
    hass,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A listener cleanup error prevents declaring delayed writers quiescent."""
    allocator = DoorCodeAllocator(hass)
    legacy = allocator._store._store

    def fail_cleanup() -> None:
        """Simulate failure while unregistering the delayed-save listener."""
        raise RuntimeError("private listener error")

    monkeypatch.setattr(legacy, "_async_cleanup_delay_listener", fail_cleanup)

    assert await _quiesce_legacy_writer(allocator) is False


def test_activation_envelope_serialization_failure_returns_no_snapshot(
    hass,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exclusion serialization failure cannot yield a partial v2 envelope."""

    def fail_exclusions(_entries: object) -> list[object]:
        """Simulate exclusion serialization failing before persistence."""
        raise RuntimeError("private serialization detail")

    monkeypatch.setattr(activation, "serialize_exclusion_snapshots", fail_exclusions)
    assert (
        _new_v2_envelope(
            hass, AllocationRegistry(code_ref_salt="activation-envelope-salt")
        )
        is None
    )


@pytest.mark.parametrize(
    (
        "retry_state",
        "retry_raises",
        "no_envelope",
        "commit_raises",
        "expected_commits",
    ),
    (
        (None, True, False, False, 0),
        (ProtectionLoadState.VALID_V2, False, False, False, 0),
        (ProtectionLoadState.LEGACY_V1, False, True, False, 0),
        (ProtectionLoadState.LEGACY_V1, False, False, True, 1),
    ),
)
async def test_legacy_activation_failure_boundaries_stay_unknown(
    hass,
    monkeypatch: pytest.MonkeyPatch,
    retry_state: ProtectionLoadState | None,
    retry_raises: bool,
    no_envelope: bool,
    commit_raises: bool,
    expected_commits: int,
) -> None:
    """Every post-parse migration failure remains non-raising and gated."""
    allocator = DoorCodeAllocator(hass)
    allocator._protection_required = True
    store = _ActivationStore(
        retry_state=retry_state,
        retry_raises=retry_raises,
        commit_raises=commit_raises,
    )
    registry = AllocationRegistry(code_ref_salt="activation-boundary-salt")
    monkeypatch.setattr(activation, "ProtectedRegistryStore", lambda *_args: store)
    monkeypatch.setattr(
        activation, "_validated_legacy_registry", lambda *_args: registry
    )
    if no_envelope:
        monkeypatch.setattr(activation, "_new_v2_envelope", lambda *_args: None)
    else:
        monkeypatch.setattr(
            activation,
            "_new_v2_envelope",
            lambda *_args: {"version": 2, "data": {}},
        )

    await activation.async_activate_protected_store(allocator)

    assert store.unknown_result is not None
    assert store.unknown_result.failure_code == "operation_failed"
    assert store.load_count == 2
    assert store.commit_count == expected_commits
    assert allocator._registry_lost
    assert not allocator._registry.records


class _ActivationStore:
    """Control only the disk result at the activation orchestration boundary."""

    def __init__(
        self,
        *,
        retry_state: ProtectionLoadState | None,
        retry_raises: bool,
        commit_raises: bool,
    ) -> None:
        """Initialize controlled load and commit outcomes for activation."""
        self.retry_state = retry_state
        self.retry_raises = retry_raises
        self.commit_raises = commit_raises
        self.load_count = 0
        self.commit_count = 0
        self.unknown_result: ProtectionLoadResult | None = None

    async def async_load(self) -> ProtectionLoadResult:
        """Return one legacy snapshot, then the injected retry result."""
        self.load_count += 1
        if self.load_count == 1:
            return ProtectionLoadResult(
                ProtectionLoadState.LEGACY_V1, data={"records": []}
            )
        if self.retry_raises:
            raise OSError("private load failure")
        assert self.retry_state is not None
        return ProtectionLoadResult(self.retry_state, data={"records": []})

    async def async_commit(
        self, _envelope: dict[str, Any], *, legacy_migration_verified: bool = False
    ) -> ProtectionLoadResult:
        """Fail the migration only through a sanitized typed boundary."""
        self.commit_count += 1
        assert legacy_migration_verified
        if self.commit_raises:
            raise OSError("private commit failure")
        return ProtectionLoadResult(ProtectionLoadState.UNAVAILABLE)

    def _enter_protective_unknown(self, result: ProtectionLoadResult) -> None:
        """Capture the fixed failure result applied by the production helper."""
        self.unknown_result = result


@pytest.mark.parametrize(
    ("loaded", "pending_registry", "pending_valid", "expected_failure"),
    (
        (
            ProtectionLoadResult(ProtectionLoadState.BUSY),
            None,
            True,
            None,
        ),
        (
            ProtectionLoadResult(ProtectionLoadState.VALID_V2, data={}),
            None,
            True,
            "operation_failed",
        ),
        (
            ProtectionLoadResult(ProtectionLoadState.VALID_V2, data={}),
            None,
            False,
            "operation_failed",
        ),
        (
            ProtectionLoadResult(ProtectionLoadState.ABSENT),
            AllocationRegistry(code_ref_salt="pending-unwritten-owner"),
            True,
            "operation_failed",
        ),
        (
            ProtectionLoadResult(ProtectionLoadState.LEGACY_V1, data=None),
            None,
            True,
            "legacy_snapshot_unavailable",
        ),
    ),
)
async def test_activation_rejects_unverified_load_and_pending_state(
    hass,
    monkeypatch: pytest.MonkeyPatch,
    loaded: ProtectionLoadResult,
    pending_registry: AllocationRegistry | None,
    pending_valid: bool,
    expected_failure: str | None,
) -> None:
    """Busy, malformed, and diskless legacy state never publishes owners."""
    allocator = DoorCodeAllocator(hass)
    allocator._protection_required = True
    store = _FixedActivationStore(loaded)
    monkeypatch.setattr(activation, "ProtectedRegistryStore", lambda *_args: store)
    monkeypatch.setattr(
        activation,
        "_pending_legacy_registry",
        lambda _allocator: (pending_registry, pending_valid),
    )

    await activation.async_activate_protected_store(allocator)

    assert not allocator._registry.records
    if expected_failure is None:
        assert store.unknown_result is None
        assert allocator._registry_lost
    else:
        assert store.unknown_result is not None
        assert store.unknown_result.failure_code == expected_failure


class _FixedActivationStore:
    """Return one typed recovery result and capture any fail-closed latch."""

    def __init__(self, loaded: ProtectionLoadResult) -> None:
        """Store the one scenario result."""
        self.loaded = loaded
        self.unknown_result: ProtectionLoadResult | None = None

    async def async_load(self) -> ProtectionLoadResult:
        """Return the exact typed recovery state."""
        return self.loaded

    def _enter_protective_unknown(self, result: ProtectionLoadResult) -> None:
        """Capture a sanitized activation failure."""
        self.unknown_result = result
