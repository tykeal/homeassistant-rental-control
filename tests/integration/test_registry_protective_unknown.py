# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""HA-facing protective-unknown status and evidence-preservation behavior."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
import hashlib
import json
from pathlib import Path
import shutil
import threading
from typing import TYPE_CHECKING
from typing import Any
from unittest.mock import Mock

from homeassistant.helpers.issue_registry import async_get as async_get_issue_registry
import pytest

from custom_components.rental_control.allocator import protected_store
from custom_components.rental_control.allocator import registry_files
from custom_components.rental_control.allocator import registry_marker_io
from custom_components.rental_control.allocator import registry_transactions
from custom_components.rental_control.allocator.models import AtomicWriteResult
from custom_components.rental_control.allocator.models import (
    ProtectionAuthorizationState,
)
from custom_components.rental_control.allocator.models import ProtectionLoadResult
from custom_components.rental_control.allocator.models import ProtectionLoadState
from custom_components.rental_control.allocator.protected_store import (
    ProtectedRegistryStore,
)
from custom_components.rental_control.const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


def _v1_wrapper() -> dict[str, Any]:
    """Return a valid, empty v1 registry wrapper."""
    return {"version": 1, "data": {"schema_version": 1, "records": []}}


def _v1_bytes() -> bytes:
    """Return exact bytes for a valid empty v1 registry."""
    return json.dumps(_v1_wrapper(), separators=(",", ":")).encode()


def _v2_envelope(
    transaction_id: str,
    generation: int,
    fences: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a complete typed v2 snapshot for storage outcome tests."""
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


def _fence(
    *,
    fence_id: str,
    km_entry: str,
    slot: int,
    rc_entry: str,
    owner_ref: str,
    allocation_ref: str,
    transaction_id: str,
    generation: int,
    fence_type: str = "allocation",
) -> dict[str, Any]:
    """Build a strict original-slot fence for storage-only tests."""
    return {
        "fence_id": fence_id,
        "original_km_entry_id": km_entry,
        "slot": slot,
        "rc_entry_id": rc_entry,
        "owner_ref": owner_ref,
        "allocation_ref": allocation_ref,
        "created_transaction_id": transaction_id,
        "created_generation": generation,
        "fence_type": fence_type,
    }


@pytest.fixture
def isolated_storage(hass: HomeAssistant, tmp_path: Path) -> Iterator[Path]:
    """Use HA's isolated real configuration storage directory."""
    parent = Path(hass.config.path(f"protected-registry-{tmp_path.name}"))
    parent.mkdir(parents=True, exist_ok=True)
    storage_dir = parent / ".storage"
    storage_dir.mkdir(parents=True, exist_ok=True)
    try:
        yield storage_dir
    finally:
        shutil.rmtree(parent, ignore_errors=True)


@pytest.mark.asyncio
async def test_corrupt_storage_persists_fixed_privacy_safe_status(
    hass: HomeAssistant,
    isolated_storage: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserve corrupt bytes and report guidance without echoing their data."""
    storage = isolated_storage
    registry = storage / "rental_control.code_registry"
    registry.write_bytes(b'{"version":1,"private":"PIN 2468 /private/path"}')
    notifications: list[str] = []

    def capture_notification(
        _hass: HomeAssistant,
        message: str,
        **_kwargs: Any,
    ) -> None:
        """Capture sanitized status text for privacy assertions."""
        notifications.append(message)

    monkeypatch.setattr(protected_store, "async_create", capture_notification)
    monkeypatch.setattr(protected_store, "async_dismiss", Mock())

    result = await ProtectedRegistryStore(
        hass, asyncio.Lock(), storage_dir=storage
    ).async_load()

    assert result.state is ProtectionLoadState.PROTECTIVE_UNKNOWN
    assert registry.read_bytes().endswith(b'/private/path"}')
    assert len(notifications) == 1
    assert "markerless v1 backup alone cannot prove completeness" in notifications[0]
    assert "no supported external restore or override" in notifications[0]
    assert "blocked indefinitely" in notifications[0]
    assert "never activated v2" in notifications[0]
    assert "2468" not in notifications[0]
    assert "/private/path" not in notifications[0]


@pytest.mark.asyncio
async def test_valid_v1_with_corrupt_sibling_is_protective_unknown(
    hass: HomeAssistant,
    isolated_storage: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A quarantine sibling blocks a markerless legacy migration candidate."""
    storage = isolated_storage
    registry = storage / "rental_control.code_registry"
    quarantine = storage / "rental_control.code_registry.corrupt.synthetic"
    registry_bytes = json.dumps(_v1_wrapper()).encode()
    registry.write_bytes(registry_bytes)
    quarantine.write_bytes(b"corrupt evidence")
    notify = Mock()
    monkeypatch.setattr(protected_store, "async_create", notify)
    monkeypatch.setattr(protected_store, "async_dismiss", Mock())

    result = await ProtectedRegistryStore(
        hass, asyncio.Lock(), storage_dir=storage
    ).async_load()

    assert result.state is ProtectionLoadState.PROTECTIVE_UNKNOWN
    assert registry.read_bytes() == registry_bytes
    assert quarantine.read_bytes() == b"corrupt evidence"
    assert notify.call_count == 1
    assert "Preserve" in notify.call_args.args[1]


@pytest.mark.asyncio
async def test_incomplete_evidence_scan_has_actionable_persistent_status(
    hass: HomeAssistant,
    isolated_storage: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 4,097th entry makes absence unprovable and status actionable."""
    storage = isolated_storage
    for index in range(4097):
        (storage / f"unrelated-{index:04d}").touch()
    notify = Mock()
    monkeypatch.setattr(protected_store, "async_create", notify)
    monkeypatch.setattr(protected_store, "async_dismiss", Mock())

    result = await ProtectedRegistryStore(
        hass, asyncio.Lock(), storage_dir=storage
    ).async_load()

    assert result.state is ProtectionLoadState.PROTECTIVE_UNKNOWN
    assert result.failure_code == "scan_limit"
    message = notify.call_args.args[1]
    assert "bounded storage evidence scan" in message
    assert "cannot establish that quarantine evidence is absent" in message
    assert "Home Assistant stopped" in message
    assert "reload Rental Control" in message
    assert "markerless v1 backup alone cannot prove completeness" not in message


@pytest.mark.asyncio
async def test_protective_unknown_issues_transition_and_clear_stale_opposite(
    hass: HomeAssistant,
    isolated_storage: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep scan-incomplete distinct from unknown and clear the stale issue."""
    monkeypatch.setattr(protected_store, "async_create", Mock())
    monkeypatch.setattr(protected_store, "async_dismiss", Mock())
    store = ProtectedRegistryStore(hass, asyncio.Lock(), storage_dir=isolated_storage)
    issue_registry = async_get_issue_registry(hass)

    store._report_unknown("scan_limit")
    assert (
        issue_registry.async_get_issue(DOMAIN, "protected_registry_scan_incomplete")
        is not None
    )
    assert issue_registry.async_get_issue(DOMAIN, "protected_registry_unknown") is None

    store._report_unknown("marker_mismatch")
    unknown_issue = issue_registry.async_get_issue(DOMAIN, "protected_registry_unknown")
    assert unknown_issue is not None
    assert unknown_issue.translation_key == "protected_registry_unknown"
    assert (
        issue_registry.async_get_issue(DOMAIN, "protected_registry_scan_incomplete")
        is None
    )

    store._dismiss_status()
    assert issue_registry.async_get_issue(DOMAIN, "protected_registry_unknown") is None


@pytest.mark.asyncio
async def test_french_protective_unknown_notification_is_localized(
    hass: HomeAssistant,
    isolated_storage: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use the French safety guidance when Home Assistant is configured in French."""
    hass.config.language = "fr"
    notify = Mock()
    monkeypatch.setattr(protected_store, "async_create", notify)
    monkeypatch.setattr(protected_store, "async_dismiss", Mock())

    ProtectedRegistryStore(
        hass, asyncio.Lock(), storage_dir=isolated_storage
    )._report_unknown("marker_mismatch")

    message = notify.call_args.args[1]
    assert "Aucune sauvegarde indépendante" in message
    assert "indéfiniment" in message
    assert "sans marqueur ne prouve pas l’exhaustivité" in message


@pytest.mark.asyncio
async def test_healthy_markerless_v1_does_not_create_protective_issue(
    hass: HomeAssistant,
    isolated_storage: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A healthy never-v2 legacy ledger remains an upgrade candidate, not an alert."""
    registry = isolated_storage / "rental_control.code_registry"
    registry.write_bytes(_v1_bytes())
    notify = Mock()
    dismissed = Mock()
    monkeypatch.setattr(protected_store, "async_create", notify)
    monkeypatch.setattr(protected_store, "async_dismiss", dismissed)

    result = await ProtectedRegistryStore(
        hass, asyncio.Lock(), storage_dir=isolated_storage
    ).async_load()

    assert result.state is ProtectionLoadState.LEGACY_V1
    assert notify.call_count == 0
    assert dismissed.call_count == 1
    assert (
        async_get_issue_registry(hass).async_get_issue(
            DOMAIN, "protected_registry_unknown"
        )
        is None
    )


@pytest.mark.asyncio
async def test_unavailable_locking_does_not_create_corruption_warning(
    hass: HomeAssistant,
    isolated_storage: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unsupported path safety is unavailable, not persistent corruption."""
    storage = isolated_storage
    notifications = Mock()
    dismissed = Mock()
    monkeypatch.setattr(protected_store, "async_create", notifications)
    monkeypatch.setattr(protected_store, "async_dismiss", dismissed)
    monkeypatch.setattr(
        "custom_components.rental_control.allocator.registry_files.os.name", "nt"
    )

    result = await ProtectedRegistryStore(
        hass, asyncio.Lock(), storage_dir=storage
    ).async_load()

    assert result.state is ProtectionLoadState.UNAVAILABLE
    assert notifications.call_count == 0
    assert dismissed.call_count == 0


@pytest.mark.asyncio
async def test_verified_absence_is_not_reported_as_protective_unknown(
    hass: HomeAssistant,
    isolated_storage: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Healthy first-run absence remains available without a corruption alert."""
    notify = Mock()
    dismissed = Mock()
    monkeypatch.setattr(protected_store, "async_create", notify)
    monkeypatch.setattr(protected_store, "async_dismiss", dismissed)

    result = await ProtectedRegistryStore(
        hass, asyncio.Lock(), storage_dir=isolated_storage
    ).async_load()

    assert result.state is ProtectionLoadState.ABSENT
    assert notify.call_count == 0
    assert dismissed.call_count == 1
    assert not (isolated_storage / "rental_control.code_registry").exists()
    assert not (isolated_storage / "rental_control.code_registry_protection").exists()


@pytest.mark.parametrize(
    ("raw", "expected_code"),
    [
        (b"{}", "envelope_invalid"),
        (b'{"version":3,"data":{}}', "version_unsupported"),
        (b"not-json", "decode_failed"),
    ],
)
@pytest.mark.asyncio
async def test_present_invalid_registry_bytes_preserve_evidence_and_report_safely(
    hass: HomeAssistant,
    isolated_storage: Path,
    monkeypatch: pytest.MonkeyPatch,
    raw: bytes,
    expected_code: str,
) -> None:
    """Present empty, unsupported and undecodable files are never absence."""
    registry = isolated_storage / "rental_control.code_registry"
    registry.write_bytes(raw)
    notify = Mock()
    monkeypatch.setattr(protected_store, "async_create", notify)
    monkeypatch.setattr(protected_store, "async_dismiss", Mock())

    result = await ProtectedRegistryStore(
        hass, asyncio.Lock(), storage_dir=isolated_storage
    ).async_load()

    assert result.state is ProtectionLoadState.PROTECTIVE_UNKNOWN
    assert result.failure_code == expected_code
    assert registry.read_bytes() == raw
    assert notify.call_count == 1
    assert raw.decode(errors="ignore") not in notify.call_args.args[1]


@pytest.mark.asyncio
async def test_registry_read_permission_failure_is_protective_unknown(
    hass: HomeAssistant,
    isolated_storage: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unreadable evidence remains in place and exposes no OS error text."""
    registry = isolated_storage / "rental_control.code_registry"
    original_bytes = _v1_bytes()
    registry.write_bytes(original_bytes)
    original_open = registry_files.os.open

    def deny_registry(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        """Inject a sensitive permission error only for the authoritative file."""
        if Path(path) == registry:
            raise PermissionError("PIN 2468 at /private/storage")
        return original_open(path, flags, *args, **kwargs)

    notify = Mock()
    monkeypatch.setattr(registry_files.os, "open", deny_registry)
    monkeypatch.setattr(protected_store, "async_create", notify)
    monkeypatch.setattr(protected_store, "async_dismiss", Mock())

    result = await ProtectedRegistryStore(
        hass, asyncio.Lock(), storage_dir=isolated_storage
    ).async_load()

    assert result.state is ProtectionLoadState.PROTECTIVE_UNKNOWN
    assert result.failure_code == "read_failed"
    assert registry.read_bytes() == original_bytes
    assert "2468" not in repr(result)
    assert "/private" not in repr(result)
    assert "2468" not in notify.call_args.args[1]


@pytest.mark.asyncio
async def test_valid_v1_with_mismatched_marker_stays_protective_unknown(
    hass: HomeAssistant,
    isolated_storage: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A surviving marker cannot authenticate different legacy bytes."""
    registry = isolated_storage / "rental_control.code_registry"
    marker = isolated_storage / "rental_control.code_registry_protection"
    raw = _v1_bytes()
    registry.write_bytes(raw)
    marker.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "activation_id": "a" * 32,
                "state": "finalized",
                "committed_version": "v1",
                "committed_generation": 0,
                "envelope_digest": hashlib.sha256(b"different").hexdigest(),
                "committed_transaction_id": None,
            }
        ),
        encoding="utf-8",
    )
    notify = Mock()
    monkeypatch.setattr(protected_store, "async_create", notify)
    monkeypatch.setattr(protected_store, "async_dismiss", Mock())

    result = await ProtectedRegistryStore(
        hass, asyncio.Lock(), storage_dir=isolated_storage
    ).async_load()

    assert result.state is ProtectionLoadState.PROTECTIVE_UNKNOWN
    assert result.failure_code == "marker_mismatch"
    assert registry.read_bytes() == raw
    assert marker.exists()
    assert notify.call_count == 1


@pytest.mark.asyncio
async def test_marker_without_registry_is_protective_unknown_not_fresh_absence(
    hass: HomeAssistant,
    isolated_storage: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An activation marker proves prior work even if the registry disappeared."""
    marker = isolated_storage / "rental_control.code_registry_protection"
    marker.write_text("{}", encoding="utf-8")
    notify = Mock()
    monkeypatch.setattr(protected_store, "async_create", notify)
    monkeypatch.setattr(protected_store, "async_dismiss", Mock())

    result = await ProtectedRegistryStore(
        hass, asyncio.Lock(), storage_dir=isolated_storage
    ).async_load()

    assert result.state is ProtectionLoadState.PROTECTIVE_UNKNOWN
    assert not (isolated_storage / "rental_control.code_registry").exists()
    assert marker.read_text(encoding="utf-8") == "{}"
    assert notify.call_count == 1


@pytest.mark.asyncio
async def test_commit_completion_applies_success_before_repeated_cancellation(
    hass: HomeAssistant,
    isolated_storage: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A drained successful commit updates loaded fences and status before cancel."""
    store = ProtectedRegistryStore(hass, asyncio.Lock(), storage_dir=isolated_storage)
    assert (await store.async_load()).state is ProtectionLoadState.ABSENT
    original_fence = _fence(
        fence_id="f" * 32,
        km_entry="km-a",
        slot=4,
        rc_entry="rc-a",
        owner_ref="owner-a",
        allocation_ref="allocation-a",
        transaction_id="1" * 32,
        generation=1,
        fence_type="pending_clear",
    )
    initial = await store.async_commit(_v2_envelope("1" * 32, 1, [original_fence]))
    assert initial.state is ProtectionLoadState.VALID_V2
    assert (await store.async_load()).state is ProtectionLoadState.VALID_V2

    notifications = Mock()
    dismissed = Mock()
    monkeypatch.setattr(protected_store, "async_create", notifications)
    monkeypatch.setattr(protected_store, "async_dismiss", dismissed)
    started = threading.Event()
    finish = threading.Event()
    original_write = registry_marker_io.atomic_write_bytes
    calls = 0

    def blocked_first_write(
        path: Path,
        data: bytes,
        *,
        maximum: int,
    ) -> AtomicWriteResult:
        """Pause the first replacement until the caller has been cancelled."""
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            if not finish.wait(timeout=5):
                return AtomicWriteResult(False, failure_code="write_failed")
        return original_write(path, data, maximum=maximum)

    monkeypatch.setattr(registry_marker_io, "atomic_write_bytes", blocked_first_write)
    next_fence = _fence(
        fence_id="e" * 32,
        km_entry="km-b",
        slot=9,
        rc_entry="rc-b",
        owner_ref="owner-b",
        allocation_ref="allocation-b",
        transaction_id="2" * 32,
        generation=2,
    )
    task = asyncio.create_task(
        store.async_commit(_v2_envelope("2" * 32, 2, [original_fence, next_fence]))
    )
    try:
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done()
        assert store.loaded_result is not None
        assert store.loaded_result.generation == 1
    finally:
        finish.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert store.last_result is not None
    assert store.last_result.state is ProtectionLoadState.VALID_V2
    assert store.last_result.generation == 2
    assert store.loaded_result is not None
    assert store.loaded_result.generation == 2
    assert store.loaded_result.data is not None
    assert store.loaded_result.data["pending_fences"] == [
        original_fence,
        next_fence,
    ]
    assert notifications.call_count == 0
    assert dismissed.call_count == 1


@pytest.mark.asyncio
async def test_failed_commit_completion_preserves_loaded_fences_before_cancel(
    hass: HomeAssistant,
    isolated_storage: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A drained failed commit freezes state but retains prior loaded fences."""
    store = ProtectedRegistryStore(hass, asyncio.Lock(), storage_dir=isolated_storage)
    assert (await store.async_load()).state is ProtectionLoadState.ABSENT
    prior_fence = _fence(
        fence_id="f" * 32,
        km_entry="km-a",
        slot=4,
        rc_entry="rc-a",
        owner_ref="owner-a",
        allocation_ref="allocation-a",
        transaction_id="3" * 32,
        generation=1,
    )
    await store.async_commit(_v2_envelope("3" * 32, 1, [prior_fence]))
    await store.async_load()
    prior_bytes = (isolated_storage / "rental_control.code_registry").read_bytes()
    notifications = Mock()
    monkeypatch.setattr(protected_store, "async_create", notifications)
    monkeypatch.setattr(protected_store, "async_dismiss", Mock())
    started = threading.Event()
    finish = threading.Event()
    calls = 0

    def fail_first_write(
        _path: Path,
        _data: bytes,
        *,
        maximum: int,
    ) -> AtomicWriteResult:
        """Fail the first writer after a deterministic cancellation window."""
        nonlocal calls
        calls += 1
        assert maximum > 0
        started.set()
        finish.wait(timeout=5)
        return AtomicWriteResult(False, failure_code="write_failed")

    monkeypatch.setattr(registry_marker_io, "atomic_write_bytes", fail_first_write)
    next_fence = _fence(
        fence_id="e" * 32,
        km_entry="km-b",
        slot=9,
        rc_entry="rc-b",
        owner_ref="owner-b",
        allocation_ref="allocation-b",
        transaction_id="4" * 32,
        generation=2,
    )
    task = asyncio.create_task(
        store.async_commit(_v2_envelope("4" * 32, 2, [prior_fence, next_fence]))
    )
    try:
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done()
        assert store.loaded_result is not None
        assert store.loaded_result.generation == 1
    finally:
        finish.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert store.last_result is not None
    assert store.last_result.state is ProtectionLoadState.PROTECTIVE_UNKNOWN
    assert store.loaded_result is not None
    assert store.loaded_result.generation == 1
    assert store.loaded_result.data is not None
    assert store.loaded_result.data["pending_fences"] == [prior_fence]
    assert (
        isolated_storage / "rental_control.code_registry"
    ).read_bytes() == prior_bytes
    assert notifications.call_count == 1
    assert "Preserve the current registry" in notifications.call_args.args[1]
    assert "rc-b" not in notifications.call_args.args[1]


@pytest.mark.asyncio
async def test_recovery_exception_after_reack_is_ambiguous_and_sanitized(
    hass: HomeAssistant,
    isolated_storage: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exception after one re-ack never degrades to no-mutation unavailable."""
    store = ProtectedRegistryStore(hass, asyncio.Lock(), storage_dir=isolated_storage)
    assert (await store.async_load()).state is ProtectionLoadState.ABSENT
    committed = await store.async_commit(_v2_envelope("5" * 32, 1, []))
    assert committed.state is ProtectionLoadState.VALID_V2
    registry = isolated_storage / "rental_control.code_registry"
    marker = isolated_storage / "rental_control.code_registry_protection"
    registry_bytes = registry.read_bytes()
    marker_bytes = marker.read_bytes()
    notifications = Mock()
    monkeypatch.setattr(protected_store, "async_create", notifications)
    monkeypatch.setattr(protected_store, "async_dismiss", Mock())
    original_reack = registry_transactions._atomic_write_acknowledged
    calls = 0

    def fail_after_first_reack(path: Path, data: bytes, maximum: int) -> bool:
        """Raise sensitive nested errors after the first re-acknowledgement."""
        nonlocal calls
        calls += 1
        if calls == 2:
            try:
                raise OSError("PIN 2468 at /private/registry")
            except OSError as cause:
                raise RuntimeError("sensitive re-ack failure") from cause
        return original_reack(path, data, maximum)

    monkeypatch.setattr(
        registry_transactions, "_atomic_write_acknowledged", fail_after_first_reack
    )

    result = await store.async_load()

    assert calls == 2
    assert result.state is ProtectionLoadState.PROTECTIVE_UNKNOWN
    assert result.failure_code == "io_failed"
    assert store.last_result == result
    assert store.loaded_result is not None
    assert store.loaded_result.generation == 1
    assert registry.read_bytes() == registry_bytes
    assert marker.read_bytes() == marker_bytes
    assert notifications.call_count == 1
    assert "2468" not in repr(result)
    assert "/private" not in repr(result)
    assert "2468" not in notifications.call_args.args[1]


@pytest.mark.asyncio
async def test_protective_unknown_gate_sticks_across_busy_and_unavailable(
    hass: HomeAssistant,
    isolated_storage: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Busy/unavailable attempts cannot authorize retained stale observations."""
    store = ProtectedRegistryStore(hass, asyncio.Lock(), storage_dir=isolated_storage)
    assert (await store.async_load()).state is ProtectionLoadState.ABSENT
    fence = _fence(
        fence_id="f" * 32,
        km_entry="km-a",
        slot=4,
        rc_entry="rc-a",
        owner_ref="owner-a",
        allocation_ref="allocation-a",
        transaction_id="6" * 32,
        generation=1,
    )
    assert (
        await store.async_commit(_v2_envelope("6" * 32, 1, [fence]))
    ).state is ProtectionLoadState.VALID_V2
    loaded_before = store.loaded_result
    assert loaded_before is not None
    registry = isolated_storage / "rental_control.code_registry"
    marker = isolated_storage / "rental_control.code_registry_protection"
    registry_bytes = registry.read_bytes()
    marker_bytes = marker.read_bytes()
    marker.write_bytes(b"known mismatch")
    notifications = Mock()
    dismissed = Mock()
    monkeypatch.setattr(protected_store, "async_create", notifications)
    monkeypatch.setattr(protected_store, "async_dismiss", dismissed)

    unknown = await store.async_load()

    assert unknown.state is ProtectionLoadState.PROTECTIVE_UNKNOWN
    assert store.authorization_state is ProtectionAuthorizationState.PROTECTIVE_UNKNOWN
    assert store.loaded_result is loaded_before
    marker.write_bytes(marker_bytes)

    async def return_busy(
        _lock: asyncio.Lock,
        _storage: Path,
        *,
        on_completion: Any,
        operation_preflight: Any,
    ) -> ProtectionLoadResult:
        """Inject a bounded busy outcome while preserving the gate callback."""
        assert operation_preflight() is None
        result = ProtectionLoadResult(ProtectionLoadState.BUSY)
        on_completion(result)
        return result

    monkeypatch.setattr(protected_store, "async_recover_protection_files", return_busy)
    busy = await store.async_load()
    assert busy.state is ProtectionLoadState.BUSY
    assert store.last_result is busy
    assert store.authorization_state is ProtectionAuthorizationState.PROTECTIVE_UNKNOWN
    assert store.loaded_result is loaded_before
    assert notifications.call_count == 1
    assert dismissed.call_count == 0

    async def return_unavailable(
        _lock: asyncio.Lock,
        _storage: Path,
        *,
        on_completion: Any,
        operation_preflight: Any,
    ) -> ProtectionLoadResult:
        """Inject an unavailable result without changing authorization."""
        assert operation_preflight() is None
        result = ProtectionLoadResult(
            ProtectionLoadState.UNAVAILABLE,
            failure_code="lock_unavailable",
        )
        on_completion(result)
        return result

    monkeypatch.setattr(
        protected_store, "async_recover_protection_files", return_unavailable
    )
    unavailable = await store.async_load()
    assert unavailable.state is ProtectionLoadState.UNAVAILABLE
    assert store.last_result is unavailable
    assert store.authorization_state is ProtectionAuthorizationState.PROTECTIVE_UNKNOWN
    assert store.loaded_result is loaded_before
    assert notifications.call_count == 1
    assert dismissed.call_count == 0

    rejected_commit = await store.async_commit(_v2_envelope("7" * 32, 2, [fence]))
    assert rejected_commit.state is ProtectionLoadState.PROTECTIVE_UNKNOWN
    assert registry.read_bytes() == registry_bytes
    assert marker.read_bytes() == marker_bytes

    monkeypatch.setattr(
        protected_store,
        "async_recover_protection_files",
        registry_transactions.async_recover_protection_files,
    )
    recovered = await store.async_load()
    assert recovered.state is ProtectionLoadState.VALID_V2
    assert store.authorization_state is ProtectionAuthorizationState.READY
    assert store.loaded_result is not None
    assert store.loaded_result.generation == 1


@pytest.mark.asyncio
async def test_known_restore_latch_blocks_matching_bytes_and_new_commits(
    hass: HomeAssistant,
    isolated_storage: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A known external restore stays blocked even with a matching pair."""
    store = ProtectedRegistryStore(hass, asyncio.Lock(), storage_dir=isolated_storage)
    unverified_commit = await store.async_commit(_v2_envelope("8" * 32, 1, []))
    assert unverified_commit.state is ProtectionLoadState.UNAVAILABLE
    assert unverified_commit.failure_code == "authorization_required"
    assert not (isolated_storage / "rental_control.code_registry").exists()

    assert (await store.async_load()).state is ProtectionLoadState.ABSENT
    committed = await store.async_commit(_v2_envelope("9" * 32, 1, []))
    assert committed.state is ProtectionLoadState.VALID_V2
    registry = isolated_storage / "rental_control.code_registry"
    marker = isolated_storage / "rental_control.code_registry_protection"
    registry_bytes = registry.read_bytes()
    marker_bytes = marker.read_bytes()
    notifications = Mock()
    monkeypatch.setattr(protected_store, "async_create", notifications)
    monkeypatch.setattr(protected_store, "async_dismiss", Mock())
    reads = Mock(side_effect=AssertionError("known restore must not read"))
    monkeypatch.setattr(registry_transactions, "read_bounded_bytes", reads)

    store.mark_external_restore_suspected()
    loaded = await store.async_load()
    commit = await store.async_commit(_v2_envelope("a" * 32, 2, []))

    assert loaded.state is ProtectionLoadState.PROTECTIVE_UNKNOWN
    assert commit.state is ProtectionLoadState.PROTECTIVE_UNKNOWN
    assert loaded.failure_code == "external_restore_suspected"
    assert store.authorization_state is ProtectionAuthorizationState.PROTECTIVE_UNKNOWN
    assert store.loaded_result is not None
    assert store.loaded_result.generation == 1
    assert reads.call_count == 0
    assert registry.read_bytes() == registry_bytes
    assert marker.read_bytes() == marker_bytes
    assert notifications.call_count >= 1


@pytest.mark.asyncio
async def test_queued_load_rechecks_restore_latch_under_storage_locks(
    hass: HomeAssistant,
    isolated_storage: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A restore signal arriving while load is queued prevents all file reads."""
    async_lock = asyncio.Lock()
    store = ProtectedRegistryStore(hass, async_lock, storage_dir=isolated_storage)
    assert (await store.async_load()).state is ProtectionLoadState.ABSENT
    committed = await store.async_commit(_v2_envelope("b" * 32, 1, []))
    assert committed.state is ProtectionLoadState.VALID_V2
    loaded_before = store.loaded_result
    assert loaded_before is not None
    notifications = Mock()
    monkeypatch.setattr(protected_store, "async_create", notifications)
    read_calls = Mock(side_effect=AssertionError("queued load must not read"))
    monkeypatch.setattr(registry_transactions, "read_bounded_bytes", read_calls)

    await async_lock.acquire()
    task = asyncio.create_task(store.async_load())
    try:
        await asyncio.sleep(0.02)
        store.mark_external_restore_suspected()
    finally:
        async_lock.release()
    result = await task

    assert result.state is ProtectionLoadState.PROTECTIVE_UNKNOWN
    assert result.failure_code == "external_restore_suspected"
    assert store.authorization_state is ProtectionAuthorizationState.PROTECTIVE_UNKNOWN
    assert store.loaded_result is loaded_before
    assert read_calls.call_count == 0
    assert notifications.call_count >= 1


@pytest.mark.asyncio
async def test_commit_completion_waits_for_current_operation_and_restore_latch(
    hass: HomeAssistant,
    isolated_storage: Path,
) -> None:
    """Only the current acknowledged operation can settle a READY store."""
    store = ProtectedRegistryStore(hass, asyncio.Lock(), storage_dir=isolated_storage)
    assert (await store.async_load()).state is ProtectionLoadState.ABSENT
    committed = await store.async_commit(_v2_envelope("c" * 32, 1, []))
    assert committed.state is ProtectionLoadState.VALID_V2
    prior = store.loaded_result
    assert prior is not None

    operation_id = store._begin_commit_operation()
    waiting = asyncio.create_task(store.async_wait_for_commit_completion())
    await asyncio.sleep(0)
    assert not waiting.done()
    store._apply_commit_completion(
        ProtectionLoadResult(ProtectionLoadState.BUSY),
        operation_id=operation_id + 1,
    )
    assert store._commit_in_flight == operation_id
    assert not waiting.done()
    store._apply_commit_completion(
        ProtectionLoadResult(ProtectionLoadState.BUSY),
        operation_id=operation_id,
    )
    assert await waiting
    assert store.authorization_state is ProtectionAuthorizationState.READY
    assert store.last_result is not None
    assert store.last_result.state is ProtectionLoadState.BUSY
    assert store.loaded_result is prior

    operation_id = store._begin_commit_operation()
    store.mark_external_restore_suspected()
    replacement = ProtectionLoadResult(
        ProtectionLoadState.VALID_V2,
        data={"schema_version": 2},
        transaction_id="d" * 32,
        generation=2,
    )
    store._apply_commit_completion(replacement, operation_id=operation_id)

    assert store.authorization_state is ProtectionAuthorizationState.PROTECTIVE_UNKNOWN
    assert store.last_result is not None
    assert store.last_result.failure_code == "external_restore_suspected"
    assert store.loaded_result is prior
    assert store._commit_in_flight is None


@pytest.mark.asyncio
async def test_status_publication_failures_never_log_storage_details(
    hass: HomeAssistant,
    isolated_storage: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Failed issue APIs keep guidance sanitized and never chain raw errors."""
    store = ProtectedRegistryStore(hass, asyncio.Lock(), storage_dir=isolated_storage)

    def fail_with_sensitive_detail(*_args: Any, **_kwargs: Any) -> None:
        """Inject a provider exception containing private storage text."""
        raise RuntimeError("PIN 2468 /private/config/.storage/registry")

    monkeypatch.setattr(
        protected_store, "async_delete_issue", fail_with_sensitive_detail
    )
    monkeypatch.setattr(protected_store, "async_create", fail_with_sensitive_detail)
    monkeypatch.setattr(
        protected_store, "async_create_issue", fail_with_sensitive_detail
    )
    store._report_unknown("scan_failed")
    store._dismiss_status()

    assert "2468" not in caplog.text
    assert "/private/config" not in caplog.text
    assert "Unable to publish protected registry status" in caplog.text
    assert "Unable to clear protected registry repair guidance" in caplog.text
