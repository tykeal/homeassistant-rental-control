# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the shared allocation registry Store wrapper."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from homeassistant.exceptions import ConfigEntryNotReady
import pytest

from custom_components.rental_control.allocator import store as store_module
from custom_components.rental_control.allocator.models import AllocationOrigin
from custom_components.rental_control.allocator.models import AllocationOwner
from custom_components.rental_control.allocator.models import AllocationRecord
from custom_components.rental_control.allocator.registry import AllocationRegistry
from custom_components.rental_control.allocator.store import RegistryStore
from custom_components.rental_control.allocator.store import code_ref_for
from custom_components.rental_control.allocator.store import decode_code
from custom_components.rental_control.allocator.store import encode_code
from custom_components.rental_control.const import CODE_REGISTRY_SCHEMA_VERSION

_NOW = "2026-09-16T12:00:00+00:00"


class FakeStore:
    """Minimal Home Assistant Store test double."""

    payload: Any = None
    load_error: Exception | None = None
    last_instance: FakeStore | None = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Record constructor arguments for compatibility."""
        self.args = args
        self.kwargs = kwargs
        self.delayed_payload: dict[str, Any] | None = None
        self.delayed_delay: int | None = None
        type(self).last_instance = self

    async def async_load(self) -> Any:
        """Return the configured payload or raise the configured error."""
        if self.load_error is not None:
            raise self.load_error
        return self.payload

    def async_delay_save(self, data_func: Any, delay: int) -> None:
        """Record delayed-save input without touching storage."""
        self.delayed_payload = data_func()
        self.delayed_delay = delay


@pytest.fixture(autouse=True)
def patch_store(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Patch Home Assistant storage and notifications."""
    notifications: list[str] = []
    monkeypatch.setattr(store_module, "Store", FakeStore)
    monkeypatch.setattr(
        store_module,
        "async_create",
        lambda _hass, message, **_kwargs: notifications.append(message),
    )
    FakeStore.payload = None
    FakeStore.load_error = None
    FakeStore.last_instance = None
    return notifications


def _registry() -> AllocationRegistry:
    """Build a registry containing a leading-zero code."""
    owner = AllocationOwner(
        entry_id="entry-alpha",
        identity_key="identity-alpha",
        origin=AllocationOrigin.PREFERRED,
        lockname="front",
        slot=3,
        lock_observed=True,
        first_seen=_NOW,
        last_seen=_NOW,
    )
    record = AllocationRecord(
        code="0042",
        code_ref="ref",
        encoding_salt_value="entry-alpha",
        code_length=4,
        owners=[owner],
        created_at=_NOW,
        updated_at=_NOW,
    )
    return AllocationRegistry(records={"0042": record}, code_ref_salt="ref-salt")


def test_encode_decode_round_trip_with_leading_zeroes() -> None:
    """Encoding is reversible and preserves leading zeroes."""
    encoded = encode_code("0042", "entry-alpha")

    assert encoded != "0042"
    assert decode_code(encoded, "entry-alpha") == "0042"


def test_serialized_code_fields_do_not_store_plaintext() -> None:
    """Persisted registry fields never store the plain door code."""
    wrapper = RegistryStore(SimpleNamespace())
    payload = wrapper._payload_from_registry(_registry())
    record = payload["records"][0]

    assert "code" not in record
    assert record["encoded_code"] != "0042"
    assert "0042" not in record["encoded_code"]
    _assert_plaintext_absent(record, "0042", skip_keys={"encoded_code"})
    assert decode_code(record["encoded_code"], record["encoding_salt_value"]) == "0042"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (None, "absent"),
        ({"schema_version": 99, "records": []}, "schema_version"),
        ({"schema_version": True, "records": []}, "schema_version"),
        (
            {
                "schema_version": CODE_REGISTRY_SCHEMA_VERSION,
                "code_ref_salt": "ref-salt",
                "records": [{"encoded_code": "not base64"}],
            },
            "payload",
        ),
    ],
)
async def test_missing_and_corrupt_payloads_yield_empty_registry(
    caplog: pytest.LogCaptureFixture,
    patch_store: list[str],
    payload: dict[str, Any] | None,
    message: str,
) -> None:
    """Missing and corrupt payloads fail closed to an empty registry."""
    FakeStore.payload = payload
    wrapper = RegistryStore(SimpleNamespace())

    result = await wrapper.async_load()

    assert result.registry.records == {}
    assert result.registry_lost is True
    assert patch_store
    assert message in caplog.text


async def test_duplicate_decoded_codes_are_rejected() -> None:
    """Two encoded records for the same plain code corrupt the payload."""
    FakeStore.payload = {
        "schema_version": CODE_REGISTRY_SCHEMA_VERSION,
        "code_ref_salt": "ref-salt",
        "records": [
            _record_payload("0042", "entry-alpha", "identity-alpha"),
            _record_payload("0042", "entry-beta", "identity-beta"),
        ],
    }
    wrapper = RegistryStore(SimpleNamespace())

    result = await wrapper.async_load()

    assert result.registry.records == {}
    assert result.registry_lost is True


async def test_duplicate_identity_keys_are_rejected() -> None:
    """One identity in two decoded records corrupts the payload."""
    FakeStore.payload = {
        "schema_version": CODE_REGISTRY_SCHEMA_VERSION,
        "code_ref_salt": "ref-salt",
        "records": [
            _record_payload("0042", "entry-alpha", "identity-alpha"),
            _record_payload("0043", "entry-beta", "identity-alpha"),
        ],
    }
    wrapper = RegistryStore(SimpleNamespace())

    result = await wrapper.async_load()

    assert result.registry.records == {}
    assert result.registry_lost is True


async def test_valid_payload_loads_registry_round_trip() -> None:
    """A saved populated registry payload restores records and owners."""
    wrapper = RegistryStore(SimpleNamespace())
    FakeStore.payload = wrapper._payload_from_registry(_registry())

    result = await wrapper.async_load()

    record = result.registry.records["0042"]
    owner = record.owners[0]
    assert result.registry_lost is False
    assert result.registry.code_ref_salt == "ref-salt"
    assert result.registry.code_for_identity("identity-alpha") == "0042"
    assert record.code == "0042"
    assert record.code_ref == code_ref_for("0042", "ref-salt")
    assert record.encoding_salt_value == "entry-alpha"
    assert owner.entry_id == "entry-alpha"
    assert owner.identity_key == "identity-alpha"
    assert owner.origin is AllocationOrigin.PREFERRED
    assert owner.lockname == "front"
    assert owner.slot == 3
    assert owner.lock_observed is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("code_length", True),
        ("slot", True),
        ("slot", "3"),
        ("lockname", None),
        ("lock_observed", "false"),
    ],
)
async def test_malformed_typed_fields_are_rejected(field: str, value: Any) -> None:
    """Boolean-like corrupt fields do not load as valid registry data."""
    record = _record_payload("0042", "entry-alpha", "identity-alpha")
    if field == "code_length":
        record[field] = value
    else:
        record["owners"][0][field] = value
    FakeStore.payload = {
        "schema_version": CODE_REGISTRY_SCHEMA_VERSION,
        "code_ref_salt": "ref-salt",
        "records": [record],
    }
    wrapper = RegistryStore(SimpleNamespace())

    result = await wrapper.async_load()

    assert result.registry.records == {}
    assert result.registry_lost is True


async def test_storage_io_failure_reports_entry_not_ready() -> None:
    """Home Assistant storage I/O failures do not discard the registry."""
    FakeStore.load_error = OSError("unreadable")
    wrapper = RegistryStore(SimpleNamespace())

    with pytest.raises(ConfigEntryNotReady):
        await wrapper.async_load()


def test_save_uses_delayed_store_write() -> None:
    """Registry saves are coalesced through async_delay_save."""
    wrapper = RegistryStore(SimpleNamespace())

    wrapper.async_save(_registry())

    assert FakeStore.last_instance is not None
    assert FakeStore.last_instance.delayed_delay == 1
    assert FakeStore.last_instance.delayed_payload is not None
    assert (
        FakeStore.last_instance.delayed_payload["records"][0]["encoded_code"] != "0042"
    )


def _record_payload(code: str, entry_id: str, identity_key: str) -> dict[str, Any]:
    """Build one valid persisted record payload."""
    return {
        "encoded_code": encode_code(code, entry_id),
        "encoding_salt_source": "entry_id",
        "encoding_salt_value": entry_id,
        "code_length": len(code),
        "created_at": _NOW,
        "updated_at": _NOW,
        "owners": [
            {
                "entry_id": entry_id,
                "identity_key": identity_key,
                "origin": "preferred",
                "lockname": "front",
                "slot": 1,
                "lock_observed": True,
                "first_seen": _NOW,
                "last_seen": _NOW,
            }
        ],
    }


def _assert_plaintext_absent(
    value: Any, plaintext: str, skip_keys: set[str] | None = None
) -> None:
    """Assert that a serialized field tree omits a plain door code."""
    if skip_keys is None:
        skip_keys = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key not in skip_keys:
                _assert_plaintext_absent(child, plaintext, skip_keys)
    elif isinstance(value, list):
        for child in value:
            _assert_plaintext_absent(child, plaintext, skip_keys)
    elif isinstance(value, str):
        assert plaintext not in value
