# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Characterize the real Rental Control v4.0.3 HA Store downgrade boundary."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
import hashlib
import json
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING
from typing import Any
from unittest.mock import AsyncMock

from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.issue_registry import async_get as async_get_issue_registry
from homeassistant.helpers.storage import STORAGE_MANAGER
import pytest

from custom_components.rental_control.allocator.models import ProtectionLoadState
from custom_components.rental_control.allocator.protected_store import (
    ProtectedRegistryStore,
)
from custom_components.rental_control.allocator.registry_transactions import (
    async_recover_protection_files,
)
from custom_components.rental_control.const import DOMAIN

from tests.fixtures.keymaster_releases import artifact_paths
from tests.fixtures.keymaster_releases import extract_legacy_rental_control
from tests.fixtures.keymaster_releases import import_legacy_registry_store
from tests.fixtures.keymaster_releases import remove_imported_package

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


_STORE_KEY = "rental_control.code_registry"
_MODULE_NAME = "rental_control_v403_downgrade"


@pytest.fixture
def verified_artifacts() -> dict[str, Path]:
    """Require the previously acquired, hash-verified upstream fixture."""
    return artifact_paths()


@pytest.fixture
def old_store(
    hass: HomeAssistant,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    verified_artifacts: dict[str, Path],  # noqa: ARG001
) -> Iterator[ModuleType]:
    """Load actual v4.0.3 source and isolate HA Store files for this test."""
    monkeypatch.setattr(hass.config, "config_dir", str(tmp_path))
    hass.data.pop(STORAGE_MANAGER, None)
    source = extract_legacy_rental_control(tmp_path / "legacy")
    module = import_legacy_registry_store(source, _MODULE_NAME)
    try:
        yield module
    finally:
        remove_imported_package(_MODULE_NAME)


def _storage_path(hass: HomeAssistant) -> Path:
    """Return HA's actual configured storage directory."""
    return Path(hass.config.path(".storage")) / _STORE_KEY


def _v1_payload(**extra: Any) -> dict[str, Any]:
    """Return an empty valid v1 ledger, optionally with legacy extra fields."""
    return {
        "schema_version": 1,
        "updated_at": "2026-11-21T00:00:00+00:00",
        "code_ref_salt": "legacy-salt",
        "records": [],
        **extra,
    }


def _write_envelope(
    path: Path,
    *,
    version: Any = 1,
    minor_version: Any = 1,
    include_minor: bool = True,
    data: Any = None,
) -> bytes:
    """Write literal HA Store envelope bytes, including malformed variants."""
    envelope: dict[str, Any] = {"version": version, "key": _STORE_KEY}
    if include_minor:
        envelope["minor_version"] = minor_version
    envelope["data"] = _v1_payload() if data is None else data
    raw = json.dumps(envelope, separators=(",", ":")).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


def _inject_json_decode_failure(registry_store: Any, path: Path) -> None:
    """Make HA's real JSON-error branch quarantine a corrupt disk file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"{")
    try:
        json.loads("{")
    except json.JSONDecodeError as cause:
        try:
            raise HomeAssistantError("synthetic corrupt JSON") from cause
        except HomeAssistantError as error:
            registry_store._store._io.async_load = AsyncMock(side_effect=error)


async def _load_decoded_envelope(
    hass: HomeAssistant,
    old_store: ModuleType,
    envelope: dict[str, Any],
) -> Any:
    """Run HA's actual Store version loader on one decoded envelope."""
    registry_store = old_store.RegistryStore(hass)
    registry_store._store._data = envelope
    return await registry_store.async_load()


@pytest.mark.asyncio
async def test_real_v403_store_reads_empty_decoded_envelope_as_absent(
    hass: HomeAssistant,
    old_store: ModuleType,
) -> None:
    """Preserve HA's actual empty-envelope behavior."""
    registry_store = old_store.RegistryStore(hass)
    registry_store._store._io.async_load = AsyncMock(return_value={})
    absent = await registry_store.async_load()
    assert absent.registry_missing is True


@pytest.mark.asyncio
async def test_real_v403_store_defaults_omitted_minor_to_one(
    hass: HomeAssistant,
    old_store: ModuleType,
) -> None:
    """HA defaults a missing v1 envelope minor version to one."""
    legacy_default = await _load_decoded_envelope(
        hass,
        old_store,
        {"version": 1, "key": _STORE_KEY, "data": _v1_payload()},
    )
    assert legacy_default.registry_lost is False
    assert legacy_default.registry_missing is False


@pytest.mark.asyncio
async def test_real_v403_store_accepts_v1_extra_fields(
    hass: HomeAssistant,
    old_store: ModuleType,
) -> None:
    """Legacy payloads tolerate optional fields that older releases dropped."""
    exact_v1 = await _load_decoded_envelope(
        hass,
        old_store,
        {
            "version": 1,
            "minor_version": 1,
            "key": _STORE_KEY,
            "data": _v1_payload(legacy_extra_field={"retained": True}),
        },
    )
    assert exact_v1.registry_lost is False
    assert exact_v1.registry.records == {}


@pytest.mark.asyncio
async def test_real_v403_store_rejects_payload_only_schema_bump(
    hass: HomeAssistant,
    old_store: ModuleType,
) -> None:
    """An unsupported inner schema loses payload without an envelope bump."""
    result = await _load_decoded_envelope(
        hass,
        old_store,
        {
            "version": 1,
            "minor_version": 1,
            "key": _STORE_KEY,
            "data": _v1_payload(schema_version=2),
        },
    )
    assert result.registry_lost is True
    assert result.registry_missing is False


@pytest.mark.parametrize(
    "payload",
    [
        {"version": 2, "minor_version": 1, "key": _STORE_KEY},
        {"version": 2, "minor_version": 1, "key": _STORE_KEY, "data": None},
        {
            "version": 2,
            "minor_version": 1,
            "key": _STORE_KEY,
            "data": {"schema_version": "malformed"},
        },
    ],
)
@pytest.mark.asyncio
async def test_real_v403_store_rejects_v2_header_before_payload_use(
    hass: HomeAssistant,
    old_store: ModuleType,
    payload: dict[str, Any],
) -> None:
    """The integer v2 header blocks downgrade independent of inner data."""
    registry_store = old_store.RegistryStore(hass)
    registry_store._store._data = payload
    with pytest.raises(ConfigEntryNotReady) as caught:
        await registry_store.async_load()

    assert caught.value.__cause__ is not None
    assert "version" in str(caught.value.__cause__).lower()


@pytest.mark.parametrize(
    "envelope",
    [
        {"version": "one", "minor_version": 1, "key": _STORE_KEY, "data": {}},
        {"minor_version": 1, "key": _STORE_KEY, "data": {}},
    ],
)
@pytest.mark.asyncio
async def test_real_v403_store_wraps_malformed_header_errors(
    hass: HomeAssistant,
    old_store: ModuleType,
    envelope: dict[str, Any],
) -> None:
    """Malformed HA envelope indexing/comparison errors reach RC as retryable."""
    registry_store = old_store.RegistryStore(hass)
    registry_store._store._data = envelope
    with pytest.raises(ConfigEntryNotReady) as caught:
        await registry_store.async_load()

    assert caught.value.__cause__ is not None


@pytest.mark.asyncio
async def test_real_v403_quarantines_corrupt_json_then_new_reader_blocks_markerless_v1(
    hass: HomeAssistant,
    tmp_path: Path,
    old_store: ModuleType,
) -> None:
    """HA quarantine plus surviving protection marker is not a valid recovery."""
    storage = Path(hass.config.path(".storage"))
    registry_path = storage / _STORE_KEY
    marker_path = storage / "rental_control.code_registry_protection"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    old_bytes = _write_envelope(
        registry_path,
        data=_v1_payload(legacy_extra_field={"will_be_quarantined": True}),
    )
    marker = {
        "schema_version": 1,
        "activation_id": "a" * 32,
        "state": "finalized",
        "committed_version": "v1",
        "committed_generation": 0,
        "envelope_digest": hashlib.sha256(old_bytes).hexdigest(),
        "committed_transaction_id": None,
    }
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    registry_store = old_store.RegistryStore(hass)
    _inject_json_decode_failure(registry_store, registry_path)

    old_result = await registry_store.async_load()
    assert old_result.registry_missing is True
    assert not registry_path.exists()
    assert list(storage.glob(f"{_STORE_KEY}.corrupt.*"))
    assert marker_path.exists()

    result = await async_recover_protection_files(asyncio.Lock(), storage)
    assert result.state is ProtectionLoadState.PROTECTIVE_UNKNOWN
    assert result.failure_code in {"partial_history", "marker_mismatch"}

    reader = ProtectedRegistryStore(hass, asyncio.Lock(), storage_dir=storage)
    surfaced = await reader.async_load()
    assert surfaced.state is ProtectionLoadState.PROTECTIVE_UNKNOWN
    issue = async_get_issue_registry(hass).async_get_issue(
        DOMAIN, "protected_registry_unknown"
    )
    assert issue is not None
    assert issue.translation_key == "protected_registry_unknown"
    assert marker_path.exists()
    assert list(storage.glob(f"{_STORE_KEY}.corrupt.*"))


@pytest.mark.asyncio
async def test_real_v403_corrupt_json_returns_absent_without_a_marker(
    hass: HomeAssistant,
    old_store: ModuleType,
) -> None:
    """Document the old loader's fresh-default behavior when markerless."""
    path = _storage_path(hass)
    registry_store = old_store.RegistryStore(hass)
    _inject_json_decode_failure(registry_store, path)
    result = await registry_store.async_load()

    assert result.registry_missing is True
    assert not path.exists()
    assert list(path.parent.glob(f"{_STORE_KEY}.corrupt.*"))
