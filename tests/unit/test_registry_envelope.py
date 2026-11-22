# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the protected registry envelope primitives."""

from __future__ import annotations

import builtins
import copy
from datetime import UTC
from datetime import datetime
import errno
import hashlib
from importlib import import_module
import os
from pathlib import Path
import stat
from typing import Any
from unittest.mock import Mock

import pytest

from custom_components.rental_control.allocator import (
    protected_registry as store_module,
)
from custom_components.rental_control.allocator import registry_validation
from custom_components.rental_control.allocator.models import ProtectionCommitToken
from custom_components.rental_control.allocator.registry_safety_models import (
    PendingSlotFence,
)
from custom_components.rental_control.allocator.registry_safety_models import (
    ResolutionTombstone,
)
from custom_components.rental_control.allocator.registry_safety_models import (
    resolve_fence_to_tombstone,
)
from custom_components.rental_control.allocator.registry_safety_models import (
    tombstone_pressure,
)
from custom_components.rental_control.allocator.registry_safety_models import (
    validate_pending_fence,
)
from custom_components.rental_control.allocator.registry_safety_models import (
    validate_resolution_tombstone,
)
from custom_components.rental_control.allocator.registry_safety_transitions import (
    validate_snapshot_transition,
)

atomicwrites = import_module("atomicwrites")


def _v2_envelope() -> dict[str, Any]:
    """Return a structurally valid, PIN-free v2 envelope."""
    return {
        "version": 2,
        "minor_version": 1,
        "key": "rental_control.code_registry",
        "data": {
            "schema_version": 1,
            "transaction_id": "a" * 32,
            "generation": 1,
            "ledger": {
                "schema_version": 1,
                "updated_at": "2026-01-01T00:00:00+00:00",
                "code_ref_salt": "salt",
                "records": [],
                "announced_conflicts": {},
            },
            "associations": [],
            "attestations": [],
            "pending_fences": [],
            "holds": [],
            "tombstones": [],
            "pending_config": [],
        },
    }


def _finalized_v2_marker() -> dict[str, Any]:
    """Return a valid committed marker for one v2 envelope."""
    return {
        "schema_version": 1,
        "activation_id": "a" * 32,
        "state": "finalized",
        "committed_version": "v2",
        "committed_generation": 1,
        "envelope_digest": "b" * 64,
        "committed_transaction_id": "c" * 32,
    }


def _pending_v2_marker() -> dict[str, Any]:
    """Return a valid pending transaction over one finalized predecessor."""
    return {
        "schema_version": 1,
        "activation_id": "a" * 32,
        "state": "pending",
        "prior": _finalized_v2_marker(),
        "target_transaction_id": "d" * 32,
        "target_generation": 2,
        "target_digest": "e" * 64,
        "kind": "commit",
    }


def _raw_present(data: bytes) -> store_module.RawFileRead:
    """Create one captured exact-byte read outcome."""
    return store_module.RawFileRead(store_module.RawFileState.PRESENT, data)


def _v1_payload() -> dict[str, Any]:
    """Return a minimal legacy ledger payload accepted by HA Store v1."""
    return {
        "schema_version": 1,
        "records": [],
    }


def _v1_payload_with_record() -> dict[str, Any]:
    """Return one valid, legacy-compatible encoded allocation record."""
    return {
        "schema_version": 1,
        "code_ref_salt": "ledger-salt",
        "records": [
            {
                "encoded_code": store_module.encode_code("2468", "entry-salt"),
                "encoding_salt_source": "entry_id",
                "encoding_salt_value": "entry-salt",
                "code_length": 4,
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "owners": [
                    {
                        "entry_id": "rc-entry",
                        "identity_key": "identity-1",
                        "origin": "preferred",
                        "lockname": "front",
                        "slot": 1,
                        "lock_observed": True,
                        "first_seen": "2026-01-01T00:00:00+00:00",
                        "last_seen": "2026-01-01T00:00:00+00:00",
                    }
                ],
            }
        ],
    }


@pytest.mark.parametrize(
    ("mode", "json_data", "expected"),
    [
        ("wb", b'{"value":"caf\xc3\xa9"}', b'{"value":"caf\xc3\xa9"}'),
        ("w", '{"value":"café"}', b'{"value":"caf\xc3\xa9"}'),
    ],
)
def test_prepare_exact_bytes_preserves_serializer_output(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    json_data: str | bytes,
    expected: bytes,
) -> None:
    """Use one serializer result and hash the exact UTF-8 bytes."""
    prepare = Mock(return_value=(mode, json_data))
    monkeypatch.setattr(registry_validation, "prepare_save_json", prepare)

    result = store_module.prepare_exact_json_bytes(_v2_envelope())

    assert result.data == expected
    assert result.digest == hashlib.sha256(expected).hexdigest()
    assert result.failure_code is None
    prepare.assert_called_once()


@pytest.mark.parametrize(
    ("mode", "json_data"),
    [
        ("wb", "text"),
        ("w", b"bytes"),
        ("unknown", b"bytes"),
    ],
)
def test_prepare_exact_bytes_rejects_mismatched_serializer_pair(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    json_data: str | bytes,
) -> None:
    """Reject malformed HA serializer mode/value pairs without leaking errors."""
    monkeypatch.setattr(
        registry_validation,
        "prepare_save_json",
        Mock(return_value=(mode, json_data)),
    )

    result = store_module.prepare_exact_json_bytes(_v2_envelope())

    assert result.data is None
    assert result.digest is None
    assert result.failure_code == "serialization_invalid"


def test_prepare_exact_bytes_sanitizes_serializer_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return a fixed error code rather than a serializer exception or chain."""
    failure = ValueError("PIN 2468 at /private/path")

    def fail(_value: object) -> tuple[str, str | bytes]:
        """Raise a synthetic sensitive error."""
        raise failure

    monkeypatch.setattr(registry_validation, "prepare_save_json", fail)

    result = store_module.prepare_exact_json_bytes(_v2_envelope())

    assert result.data is None
    assert result.digest is None
    assert result.failure_code == "serialization_failed"
    assert failure not in (result.data, result.digest, result.failure_code)


def test_prepare_exact_bytes_enforces_exact_output_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Accept an exact byte cap and reject cap plus one before any write."""
    maximum = 64
    exact = b"x" * maximum
    monkeypatch.setattr(
        registry_validation,
        "prepare_save_json",
        Mock(return_value=("wb", exact)),
    )
    accepted = store_module.prepare_exact_json_bytes(_v2_envelope(), maximum=maximum)

    monkeypatch.setattr(
        registry_validation,
        "prepare_save_json",
        Mock(return_value=("wb", exact + b"x")),
    )
    rejected = store_module.prepare_exact_json_bytes(_v2_envelope(), maximum=maximum)

    assert accepted.data == exact
    assert accepted.failure_code is None
    assert rejected.data is None
    assert rejected.digest is None
    assert rejected.failure_code == "serialized_oversize"


@pytest.mark.parametrize(
    "maximum",
    [store_module.MAX_REGISTRY_BYTES, store_module.MAX_MARKER_BYTES],
)
def test_prepare_exact_bytes_enforces_real_registry_and_marker_caps(
    monkeypatch: pytest.MonkeyPatch,
    maximum: int,
) -> None:
    """Hash exact-at-limit output and reject limit plus one for both files."""
    exact = b"x" * maximum
    monkeypatch.setattr(
        registry_validation,
        "prepare_save_json",
        Mock(return_value=("wb", exact)),
    )
    accepted = store_module.prepare_exact_json_bytes(_v2_envelope(), maximum=maximum)

    monkeypatch.setattr(
        registry_validation,
        "prepare_save_json",
        Mock(return_value=("wb", exact + b"x")),
    )
    rejected = store_module.prepare_exact_json_bytes(_v2_envelope(), maximum=maximum)

    assert accepted.data == exact
    assert accepted.digest == hashlib.sha256(exact).hexdigest()
    assert rejected.data is None
    assert rejected.failure_code == "serialized_oversize"


def test_v2_envelope_requires_every_typed_protection_section() -> None:
    """Do not treat absent or malformed safety metadata as empty state."""
    envelope = _v2_envelope()
    assert store_module.validate_v2_envelope_shape(envelope)

    for section in (
        "ledger",
        "associations",
        "attestations",
        "pending_fences",
        "holds",
        "tombstones",
        "pending_config",
    ):
        damaged = _v2_envelope()
        del damaged["data"][section]
        assert not store_module.validate_v2_envelope_shape(damaged)

    invalid_values: tuple[object, ...] = (None, {}, "invalid", 1)
    for invalid in invalid_values:
        damaged = _v2_envelope()
        damaged["data"]["pending_fences"] = invalid
        assert not store_module.validate_v2_envelope_shape(damaged)


@pytest.mark.parametrize(
    ("section", "value"),
    [
        ("schema_version", True),
        ("transaction_id", "not-an-opaque-id"),
        ("generation", 0),
        ("ledger", {"schema_version": 1}),
    ],
)
def test_v2_envelope_rejects_incomplete_core_sections(
    section: str, value: object
) -> None:
    """Require typed version, transaction and complete ledger snapshots."""
    envelope = _v2_envelope()
    envelope["data"][section] = value

    assert not store_module.validate_v2_envelope_shape(envelope)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("version", True),
        ("version", 3),
        ("minor_version", True),
        ("minor_version", 2),
        ("key", ""),
    ],
)
def test_v2_envelope_rejects_unsupported_or_boolean_versions(
    field: str, value: object
) -> None:
    """Reject unsupported wrapper values before decoding the ledger."""
    envelope = _v2_envelope()
    envelope[field] = value

    assert not store_module.validate_v2_envelope_shape(envelope)


def test_v2_envelope_rejects_boolean_generation_and_non_object_sections() -> None:
    """Keep JSON booleans from passing integer fields and section types."""
    envelope = _v2_envelope()
    envelope["data"]["generation"] = True
    assert not store_module.validate_v2_envelope_shape(envelope)

    envelope = _v2_envelope()
    envelope["data"]["attestations"] = [{"rc_entry_id": 7}]
    assert not store_module.validate_v2_envelope_shape(envelope)


def test_v2_envelope_rejects_extra_owner_fields_and_non_json_history() -> None:
    """Persisted v2 rows reject secret-shaped and non-JSON data."""
    envelope = _v2_envelope()
    ledger = _v1_payload_with_record()
    envelope["data"]["ledger"] = {
        **ledger,
        "updated_at": "2026-01-01T00:00:00+00:00",
        "announced_conflicts": {},
    }
    assert store_module.validate_v2_envelope_shape(envelope)

    envelope["data"]["ledger"]["records"][0]["owners"][0]["pin"] = "2468"
    assert not store_module.validate_v2_envelope_shape(envelope)

    envelope = _v2_envelope()
    envelope["data"]["pending_config"] = [{"generation": float("nan")}]
    assert not store_module.validate_v2_envelope_shape(envelope)

    envelope = _v2_envelope()
    envelope["data"]["ledger"]["announced_conflicts"] = {"": []}
    assert not store_module.validate_v2_envelope_shape(envelope)


def test_v2_requires_typed_original_slot_fences_and_resolution_tombstones() -> None:
    """Safety rows retain explicit original identity and transaction fences."""
    fence = PendingSlotFence(
        fence_id="f" * 32,
        original_km_entry_id="km-entry-a",
        slot=4,
        rc_entry_id="rc-entry-a",
        owner_ref="owner-ref-a",
        allocation_ref="allocation-ref-a",
        created_transaction_id="a" * 32,
        created_generation=2,
        fence_type="pending_clear",
    ).to_record()
    tombstone = ResolutionTombstone(
        tombstone_id="c" * 32,
        fence_id=fence["fence_id"],
        original_km_entry_id=fence["original_km_entry_id"],
        slot=fence["slot"],
        rc_entry_id=fence["rc_entry_id"],
        owner_ref=fence["owner_ref"],
        allocation_ref=fence["allocation_ref"],
        fence_type=fence["fence_type"],
        created_transaction_id=fence["created_transaction_id"],
        created_generation=fence["created_generation"],
        resolved_transaction_id="b" * 32,
        resolved_generation=3,
        resolution_kind="clear_confirmed",
        created_at="2026-11-21T00:00:00+00:00",
    ).to_record()
    envelope = _v2_envelope()
    envelope["data"]["transaction_id"] = "b" * 32
    envelope["data"]["generation"] = 3
    envelope["data"]["pending_fences"] = []
    envelope["data"]["tombstones"] = [tombstone]

    assert validate_pending_fence(fence)
    assert validate_resolution_tombstone(tombstone)
    assert store_module.validate_v2_envelope_shape(envelope)

    envelope["data"]["pending_fences"] = [fence]
    assert not store_module.validate_v2_envelope_shape(envelope)

    malformed_fence = dict(fence)
    malformed_fence["slot"] = True
    envelope["data"]["pending_fences"] = [malformed_fence]
    assert not store_module.validate_v2_envelope_shape(envelope)

    envelope["data"]["pending_fences"] = [{"entry_id": "km-entry-a", "slot": 4}]
    assert not store_module.validate_v2_envelope_shape(envelope)

    malformed_tombstone = dict(tombstone)
    malformed_tombstone["resolved_generation"] = 0
    envelope["data"]["pending_fences"] = []
    envelope["data"]["tombstones"] = [malformed_tombstone]
    assert not store_module.validate_v2_envelope_shape(envelope)

    mismatched_creator = dict(fence)
    mismatched_creator["created_transaction_id"] = "d" * 32
    mismatched_creator["created_generation"] = 3
    envelope["data"]["pending_fences"] = [mismatched_creator]
    envelope["data"]["tombstones"] = []
    assert not store_module.validate_v2_envelope_shape(envelope)


def test_tombstone_pressure_reports_only_count_and_age() -> None:
    """Retained tombstone pressure never exposes identity or credential data."""
    record = ResolutionTombstone(
        tombstone_id="c" * 32,
        fence_id="f" * 32,
        original_km_entry_id="private-km-entry",
        slot=7,
        rc_entry_id="private-rc-entry",
        owner_ref="private-owner",
        allocation_ref="private-allocation",
        fence_type="allocation",
        created_transaction_id="a" * 32,
        created_generation=1,
        resolved_transaction_id="b" * 32,
        resolved_generation=2,
        resolution_kind="owner_released",
        created_at="2026-11-20T23:00:00+00:00",
    ).to_record()

    pressure = tombstone_pressure(
        [record], now=datetime(2026, 11, 21, 0, 0, tzinfo=UTC)
    )

    assert pressure.count == 1
    assert pressure.oldest_age_seconds == 3600
    assert "private" not in repr(pressure)


def test_fence_resolution_retains_original_references_and_requires_new_generation() -> (
    None
):
    """Resolution adds a tombstone; it does not rewrite the original fence."""
    fence = PendingSlotFence(
        fence_id="f" * 32,
        original_km_entry_id="km-entry-a",
        slot=4,
        rc_entry_id="rc-entry-a",
        owner_ref="owner-ref-a",
        allocation_ref="allocation-ref-a",
        created_transaction_id="a" * 32,
        created_generation=2,
        fence_type="pending_clear",
    ).to_record()
    original = dict(fence)

    tombstone = resolve_fence_to_tombstone(
        fence,
        tombstone_id="c" * 32,
        resolved_transaction_id="b" * 32,
        resolved_generation=3,
        resolution_kind="clear_confirmed",
        retained_at="2026-11-21T00:00:00+00:00",
    )

    assert fence == original
    assert tombstone["fence_id"] == fence["fence_id"]
    assert tombstone["original_km_entry_id"] == fence["original_km_entry_id"]
    assert tombstone["slot"] == fence["slot"]
    assert tombstone["owner_ref"] == fence["owner_ref"]
    assert tombstone["allocation_ref"] == fence["allocation_ref"]
    assert tombstone["created_transaction_id"] == fence["created_transaction_id"]
    assert tombstone["created_generation"] == fence["created_generation"]
    with pytest.raises(ValueError, match="invalid resolution tombstone"):
        resolve_fence_to_tombstone(
            fence,
            tombstone_id="d" * 32,
            resolved_transaction_id="b" * 32,
            resolved_generation=2,
            resolution_kind="clear_confirmed",
            retained_at="2026-11-21T00:00:00+00:00",
        )


@pytest.mark.parametrize(
    ("transaction_id", "generation"),
    [("not-an-id", 1), ("a" * 32, True), ("a" * 32, 0)],
)
def test_commit_token_rejects_malformed_generation_or_transaction(
    transaction_id: str,
    generation: int,
) -> None:
    """Keep token matching from treating booleans or malformed IDs as authority."""
    with pytest.raises(ValueError, match="invalid protection commit token"):
        ProtectionCommitToken(transaction_id, generation)


def test_snapshot_transition_requires_fence_and_tombstone_continuity() -> None:
    """Prior fences persist or resolve exactly, and history is immutable."""
    fence_a = PendingSlotFence(
        fence_id="f" * 32,
        original_km_entry_id="km-entry-a",
        slot=4,
        rc_entry_id="rc-entry-a",
        owner_ref="owner-ref-a",
        allocation_ref="allocation-ref-a",
        created_transaction_id="a" * 32,
        created_generation=1,
        fence_type="pending_clear",
    ).to_record()
    fence_b = PendingSlotFence(
        fence_id="e" * 32,
        original_km_entry_id="km-entry-b",
        slot=9,
        rc_entry_id="rc-entry-b",
        owner_ref="owner-ref-b",
        allocation_ref="allocation-ref-b",
        created_transaction_id="a" * 32,
        created_generation=1,
        fence_type="allocation",
    ).to_record()
    prior: dict[str, Any] = {
        "transaction_id": "a" * 32,
        "generation": 1,
        "pending_fences": [fence_a, fence_b],
        "tombstones": [],
    }
    resolved_a = resolve_fence_to_tombstone(
        fence_a,
        tombstone_id="c" * 32,
        resolved_transaction_id="b" * 32,
        resolved_generation=2,
        resolution_kind="clear_confirmed",
        retained_at="2026-11-21T00:00:00+00:00",
    )
    target: dict[str, Any] = {
        "transaction_id": "b" * 32,
        "generation": 2,
        "pending_fences": [fence_b],
        "tombstones": [resolved_a],
    }

    assert validate_snapshot_transition(prior, target)

    stale_transaction_fence = PendingSlotFence(
        fence_id="d" * 32,
        original_km_entry_id="km-entry-c",
        slot=3,
        rc_entry_id="rc-entry-c",
        owner_ref="owner-ref-c",
        allocation_ref="allocation-ref-c",
        created_transaction_id="a" * 32,
        created_generation=2,
        fence_type="allocation",
    ).to_record()
    stale_transaction_target = copy.deepcopy(target)
    stale_transaction_target["pending_fences"].append(stale_transaction_fence)
    assert not validate_snapshot_transition(prior, stale_transaction_target)

    stale_generation_fence = dict(stale_transaction_fence)
    stale_generation_fence["created_transaction_id"] = "b" * 32
    stale_generation_fence["created_generation"] = 1
    stale_generation_target = copy.deepcopy(target)
    stale_generation_target["pending_fences"].append(stale_generation_fence)
    assert not validate_snapshot_transition(prior, stale_generation_target)

    dropped_fence = copy.deepcopy(target)
    dropped_fence["pending_fences"] = []
    assert not validate_snapshot_transition(prior, dropped_fence)

    mutated_fence = copy.deepcopy(target)
    mutated_fence["pending_fences"][0]["slot"] = 10
    assert not validate_snapshot_transition(prior, mutated_fence)

    wrong_resolver = copy.deepcopy(target)
    wrong_resolver["tombstones"][0]["resolved_transaction_id"] = "d" * 32
    assert not validate_snapshot_transition(prior, wrong_resolver)

    duplicate_active = copy.deepcopy(target)
    duplicate = dict(fence_b)
    duplicate["fence_id"] = "d" * 32
    duplicate["created_transaction_id"] = "b" * 32
    duplicate["created_generation"] = 2
    duplicate_active["pending_fences"].append(duplicate)
    assert not validate_snapshot_transition(prior, duplicate_active)

    active_and_resolved = copy.deepcopy(target)
    tombstone_b = resolve_fence_to_tombstone(
        fence_b,
        tombstone_id="d" * 32,
        resolved_transaction_id="b" * 32,
        resolved_generation=2,
        resolution_kind="owner_released",
        retained_at="2026-11-21T00:00:00+00:00",
    )
    active_and_resolved["tombstones"].append(tombstone_b)
    assert not validate_snapshot_transition(prior, active_and_resolved)

    prior_with_history = copy.deepcopy(target)
    next_snapshot = copy.deepcopy(target)
    next_snapshot["transaction_id"] = "d" * 32
    next_snapshot["generation"] = 3
    next_snapshot["tombstones"] = []
    assert not validate_snapshot_transition(prior_with_history, next_snapshot)

    mutated_history = copy.deepcopy(target)
    mutated_history["transaction_id"] = "d" * 32
    mutated_history["generation"] = 3
    mutated_history["tombstones"][0]["owner_ref"] = "other-owner"
    assert not validate_snapshot_transition(prior_with_history, mutated_history)


@pytest.mark.parametrize(
    "wrapper",
    [
        {"version": 1, "data": _v1_payload()},
        {"version": 1, "minor_version": 27, "data": _v1_payload()},
        {"version": 1, "minor_version": -2, "key": "other", "data": _v1_payload()},
    ],
)
def test_raw_classifier_accepts_ha_compatible_v1_wrappers(
    wrapper: dict[str, Any],
) -> None:
    """Honor HA v1 minor fallback and optional wrapper key without writes."""
    raw_bytes = store_module.prepare_exact_json_bytes(wrapper).data
    assert raw_bytes is not None

    result = store_module.classify_registry_file(
        store_module.RawFileRead(store_module.RawFileState.PRESENT, raw_bytes)
    )

    assert result.state == "legacy_v1"
    assert result.data == wrapper["data"]
    assert result.failure_code is None


def test_v1_classifier_validates_real_record_and_preserves_legacy_fields() -> None:
    """Accept an authentic encoded record and reject invalid record semantics."""
    payload = _v1_payload_with_record()
    raw_bytes = store_module.prepare_exact_json_bytes(
        {"version": 1, "data": payload}
    ).data
    assert raw_bytes is not None
    result = store_module.classify_registry_file(
        store_module.RawFileRead(store_module.RawFileState.PRESENT, raw_bytes)
    )

    assert result.state == "legacy_v1"
    assert result.data == payload

    invalid_cases = (
        ("code_length", True),
        ("encoded_code", "not-base64"),
        ("encoding_salt_source", "unexpected"),
    )
    for field, invalid_value in invalid_cases:
        damaged = _v1_payload_with_record()
        damaged["records"][0][field] = invalid_value
        raw = store_module.prepare_exact_json_bytes(
            {"version": 1, "data": damaged}
        ).data
        assert raw is not None
        rejected = store_module.classify_registry_file(
            store_module.RawFileRead(store_module.RawFileState.PRESENT, raw)
        )
        assert rejected.state == "protective_unknown"
        assert rejected.failure_code == "legacy_invalid"

    malformed_payloads: list[dict[str, Any]] = []
    malformed = _v1_payload_with_record()
    malformed["records"] = [None]
    malformed_payloads.append(malformed)

    malformed = _v1_payload_with_record()
    malformed["records"][0]["owners"] = [None]
    malformed_payloads.append(malformed)

    malformed = _v1_payload_with_record()
    malformed["records"][0]["owners"][0]["origin"] = "unrecognized"
    malformed_payloads.append(malformed)

    malformed = _v1_payload_with_record()
    malformed["records"][0]["owners"][0]["slot"] = True
    malformed_payloads.append(malformed)

    malformed = _v1_payload_with_record()
    malformed["records"][0]["owners"][0]["slot"] = None
    malformed_payloads.append(malformed)

    malformed = _v1_payload_with_record()
    malformed["records"][0]["owners"].append(
        copy.deepcopy(malformed["records"][0]["owners"][0])
    )
    malformed_payloads.append(malformed)

    malformed = _v1_payload_with_record()
    malformed["records"].append(copy.deepcopy(malformed["records"][0]))
    malformed_payloads.append(malformed)

    malformed = _v1_payload_with_record()
    malformed["records"][0]["encoded_code"] = store_module.encode_code(
        "ABCD", "entry-salt"
    )
    malformed_payloads.append(malformed)

    for damaged in malformed_payloads:
        raw = store_module.prepare_exact_json_bytes(
            {"version": 1, "data": damaged}
        ).data
        assert raw is not None
        rejected = store_module.classify_registry_file(
            store_module.RawFileRead(store_module.RawFileState.PRESENT, raw)
        )
        assert rejected.state == "protective_unknown"
        assert rejected.failure_code == "legacy_invalid"


@pytest.mark.parametrize(
    "raw_bytes",
    [
        b"",
        b"{}",
        b"[]",
        b"{invalid json",
        b'{"version":true,"data":{}}',
        b'{"version":1,"minor_version":true,"data":{"schema_version":1,"records":[]}}',
        b'{"version":1,"minor_version":"1","data":{"schema_version":1,"records":[]}}',
        b'{"version":1,"data":{"schema_version":2,"records":[]}}',
        b'{"version":1,"data":{"schema_version":1,"records":[]},"extra":NaN}',
    ],
)
def test_raw_classifier_keeps_present_invalid_v1_unknown(
    raw_bytes: bytes,
) -> None:
    """Never collapse present empty, malformed, or unsupported v1 to absence."""
    result = store_module.classify_registry_file(
        store_module.RawFileRead(store_module.RawFileState.PRESENT, raw_bytes)
    )

    assert result.state == "protective_unknown"
    assert result.failure_code is not None


def test_storage_models_reject_non_allowlisted_failure_text() -> None:
    """Raw exception and path text cannot become a result failure code."""
    result = store_module.RawFileRead(
        store_module.RawFileState.FAILED,
        failure_code="PIN 2468 at /private/storage",
    )

    assert result.failure_code == "operation_failed"
    assert "2468" not in repr(result)
    assert "/private" not in repr(result)


def test_raw_classifier_requires_supported_v2_protection_schema() -> None:
    """Reject an otherwise valid wrapper with an unknown safety data schema."""
    envelope = _v2_envelope()
    envelope["data"]["schema_version"] = 2
    raw_bytes = store_module.prepare_exact_json_bytes(envelope).data
    assert raw_bytes is not None

    result = store_module.classify_registry_file(
        store_module.RawFileRead(store_module.RawFileState.PRESENT, raw_bytes)
    )

    assert result.state == "protective_unknown"
    assert result.failure_code == "version_unsupported"


def test_protection_marker_requires_full_finalized_or_pending_shape() -> None:
    """Require exact, typed transaction marker versions and predecessors."""
    assert store_module.validate_protection_marker_shape(_finalized_v2_marker())
    assert store_module.validate_protection_marker_shape(_pending_v2_marker())

    genesis = {
        "schema_version": 1,
        "activation_id": "f" * 32,
        "state": "pending",
        "prior": {"state": "genesis_absent"},
        "target_transaction_id": "1" * 32,
        "target_generation": 1,
        "target_digest": "2" * 64,
        "kind": "genesis",
    }
    assert store_module.validate_protection_marker_shape(genesis)

    v1_finalized = {
        "schema_version": 1,
        "activation_id": "a" * 32,
        "state": "finalized",
        "committed_version": "v1",
        "committed_generation": 0,
        "envelope_digest": "b" * 64,
        "committed_transaction_id": None,
    }
    assert store_module.validate_protection_marker_shape(v1_finalized)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda marker: marker.update(committed_version=[]),
        lambda marker: marker.update(committed_generation=True),
        lambda marker: marker.update(envelope_digest="A" * 64),
        lambda marker: marker.update(unrecognized=True),
    ],
)
def test_protection_marker_rejects_malformed_final_state(mutate: Any) -> None:
    """Reject unsupported or malformed finalized marker data."""
    marker = _finalized_v2_marker()
    mutate(marker)

    assert not store_module.validate_protection_marker_shape(marker)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda marker: marker.update(target_generation=3),
        lambda marker: marker.update(target_transaction_id="c" * 32),
        lambda marker: marker.update(target_digest="not-a-digest"),
        lambda marker: marker.update(kind="migration"),
        lambda marker: marker["prior"].update(committed_generation=True),
    ],
)
def test_protection_marker_rejects_mismatched_transition(
    mutate: Any,
) -> None:
    """Reject malformed IDs, non-successor generations and unknown kinds."""
    marker = _pending_v2_marker()
    mutate(marker)

    assert not store_module.validate_protection_marker_shape(marker)


def test_recovery_plan_distinguishes_absence_legacy_and_evidence() -> None:
    """Only complete verified absence and intact markerless v1 remain eligible."""
    empty_scan = store_module.EvidenceScanResult(True)
    absent = store_module.plan_protection_recovery(
        store_module.RawFileRead(store_module.RawFileState.ABSENT),
        store_module.RawFileRead(store_module.RawFileState.ABSENT),
        empty_scan,
    )
    assert absent.action.value == "absent"

    legacy_bytes = store_module.prepare_exact_json_bytes(
        {"version": 1, "data": _v1_payload()}
    ).data
    assert legacy_bytes is not None
    legacy = store_module.plan_protection_recovery(
        _raw_present(legacy_bytes),
        store_module.RawFileRead(store_module.RawFileState.ABSENT),
        empty_scan,
    )
    assert legacy.action.value == "legacy_v1"

    evidence = store_module.EvidenceScanResult(
        True, matches=("rental_control.code_registry.corrupt.synthetic",)
    )
    blocked = store_module.plan_protection_recovery(
        _raw_present(legacy_bytes),
        store_module.RawFileRead(store_module.RawFileState.ABSENT),
        evidence,
    )
    assert blocked.action.value == "protective_unknown"
    assert blocked.failure_code == "partial_history"

    quarantined_v1 = store_module.plan_protection_recovery(
        _raw_present(legacy_bytes),
        store_module.RawFileRead(store_module.RawFileState.ABSENT),
        store_module.EvidenceScanResult(
            True, matches=("rental_control.code_registry.corrupt.synthetic",)
        ),
    )
    incomplete_v1_scan = store_module.plan_protection_recovery(
        _raw_present(legacy_bytes),
        store_module.RawFileRead(store_module.RawFileState.ABSENT),
        store_module.EvidenceScanResult(False, failure_code="scan_limit"),
    )
    assert quarantined_v1.action.value == "protective_unknown"
    assert quarantined_v1.failure_code == "partial_history"
    assert incomplete_v1_scan.action.value == "protective_unknown"
    assert incomplete_v1_scan.failure_code == "scan_limit"


def test_recovery_plan_requires_exact_finalized_digest_and_schema() -> None:
    """A marker authenticates exact bytes only when their versions also agree."""
    envelope = _v2_envelope()
    envelope["data"]["transaction_id"] = "c" * 32
    envelope_bytes = store_module.prepare_exact_json_bytes(envelope).data
    assert envelope_bytes is not None
    marker = _finalized_v2_marker()
    marker["envelope_digest"] = hashlib.sha256(envelope_bytes).hexdigest()
    marker_bytes = store_module.prepare_exact_json_bytes(marker).data
    assert marker_bytes is not None
    scan = store_module.EvidenceScanResult(True)

    matched = store_module.plan_protection_recovery(
        _raw_present(envelope_bytes),
        _raw_present(marker_bytes),
        scan,
    )
    assert matched.action.value == "valid_v2"

    whitespace_changed = envelope_bytes + b" "
    mismatch = store_module.plan_protection_recovery(
        _raw_present(whitespace_changed),
        _raw_present(marker_bytes),
        scan,
    )
    assert mismatch.action.value == "protective_unknown"
    assert mismatch.failure_code == "marker_mismatch"


def test_recovery_plan_distinguishes_pending_target_prior_and_absent_genesis() -> None:
    """Classify only the exact pending target, predecessor, or genesis pair."""
    scan = store_module.EvidenceScanResult(True)
    target_envelope = _v2_envelope()
    target_envelope["data"]["transaction_id"] = "d" * 32
    target_envelope["data"]["generation"] = 2
    target_bytes = store_module.prepare_exact_json_bytes(target_envelope).data
    assert target_bytes is not None
    pending = _pending_v2_marker()
    pending["target_digest"] = hashlib.sha256(target_bytes).hexdigest()
    pending_bytes = store_module.prepare_exact_json_bytes(pending).data
    assert pending_bytes is not None
    target_plan = store_module.plan_protection_recovery(
        _raw_present(target_bytes),
        _raw_present(pending_bytes),
        scan,
    )
    assert target_plan.action.value == "pending_target"

    v1_bytes = store_module.prepare_exact_json_bytes(
        {"version": 1, "data": _v1_payload()}
    ).data
    assert v1_bytes is not None
    prior = {
        "schema_version": 1,
        "activation_id": "a" * 32,
        "state": "finalized",
        "committed_version": "v1",
        "committed_generation": 0,
        "envelope_digest": hashlib.sha256(v1_bytes).hexdigest(),
        "committed_transaction_id": None,
    }
    pending_migration = {
        "schema_version": 1,
        "activation_id": "a" * 32,
        "state": "pending",
        "prior": prior,
        "target_transaction_id": "d" * 32,
        "target_generation": 1,
        "target_digest": "e" * 64,
        "kind": "v1_migration",
    }
    pending_migration_bytes = store_module.prepare_exact_json_bytes(
        pending_migration, maximum=store_module.MAX_MARKER_BYTES
    ).data
    assert pending_migration_bytes is not None
    prior_plan = store_module.plan_protection_recovery(
        _raw_present(v1_bytes),
        _raw_present(pending_migration_bytes),
        scan,
    )
    assert prior_plan.action.value == "pending_prior"

    genesis = {
        "schema_version": 1,
        "activation_id": "f" * 32,
        "state": "pending",
        "prior": {"state": "genesis_absent"},
        "target_transaction_id": "1" * 32,
        "target_generation": 1,
        "target_digest": "2" * 64,
        "kind": "genesis",
    }
    genesis_bytes = store_module.prepare_exact_json_bytes(
        genesis, maximum=store_module.MAX_MARKER_BYTES
    ).data
    assert genesis_bytes is not None
    absent_plan = store_module.plan_protection_recovery(
        store_module.RawFileRead(store_module.RawFileState.ABSENT),
        _raw_present(genesis_bytes),
        scan,
    )
    assert absent_plan.action.value == "pending_genesis_absent"


def test_v2_validator_checks_ledger_record_and_owner_types() -> None:
    """Do not accept a generic JSON object as a complete allocation ledger."""
    envelope = _v2_envelope()
    envelope["data"]["ledger"]["records"] = [{"encoded_code": "bad"}]
    assert not store_module.validate_v2_envelope_shape(envelope)

    envelope = _v2_envelope()
    envelope["data"]["associations"] = [{}]
    assert not store_module.validate_v2_envelope_shape(envelope)


def test_raw_reader_distinguishes_absence_and_exact_size_limit(
    tmp_path: Path,
) -> None:
    """Read only bounded raw bytes and accept exactly the configured limit."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    target = storage / "rental_control.code_registry"
    target.write_bytes(b"1234")

    exact = store_module.read_bounded_bytes(target, maximum=4)
    target.write_bytes(b"12345")
    oversize = store_module.read_bounded_bytes(target, maximum=4)
    target.unlink()
    absent = store_module.read_bounded_bytes(target, maximum=4)

    assert exact.state == "present"
    assert exact.data == b"1234"
    assert oversize.state == "oversize"
    assert oversize.data is None
    assert absent.state == "absent"
    assert absent.failure_code is None


@pytest.mark.parametrize(
    ("name", "maximum"),
    [
        ("rental_control.code_registry", store_module.MAX_REGISTRY_BYTES),
        ("rental_control.code_registry_protection", store_module.MAX_MARKER_BYTES),
    ],
)
def test_raw_reader_enforces_real_registry_and_marker_caps(
    tmp_path: Path,
    name: str,
    maximum: int,
) -> None:
    """Accept each actual byte cap and preserve limit-plus-one evidence."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    target = storage / name
    exact_bytes = b"x" * maximum
    target.write_bytes(exact_bytes)
    exact = store_module.read_bounded_bytes(target, maximum=maximum)
    target.write_bytes(exact_bytes + b"x")
    oversize = store_module.read_bounded_bytes(target, maximum=maximum)

    assert exact.state == "present"
    assert exact.data == exact_bytes
    assert oversize.state == "oversize"
    assert oversize.data is None
    assert target.stat().st_size == maximum + 1


@pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="requires POSIX no-follow")
def test_raw_reader_rejects_symlink_without_reading_target(tmp_path: Path) -> None:
    """Reject symlinked storage parents and final paths without following."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    secret = tmp_path / "outside"
    secret.write_bytes(b"must not be read")
    target = storage / "rental_control.code_registry"
    target.symlink_to(secret)

    result = store_module.read_bounded_bytes(target, maximum=1024)

    assert result.state == "failed"
    assert result.failure_code == "unsafe_path"
    assert secret.read_bytes() == b"must not be read"


@pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="requires POSIX no-follow")
def test_raw_reader_rejects_symlinked_storage_parent(tmp_path: Path) -> None:
    """Reject a symlinked .storage directory before opening its target."""
    real_storage = tmp_path / "real-storage"
    real_storage.mkdir()
    target = real_storage / "rental_control.code_registry"
    target.write_bytes(b"untouched")
    (tmp_path / ".storage").symlink_to(real_storage, target_is_directory=True)

    result = store_module.read_bounded_bytes(
        tmp_path / ".storage" / "rental_control.code_registry",
        maximum=1024,
    )

    assert result.state == "failed"
    assert result.failure_code == "unsafe_path"
    assert target.read_bytes() == b"untouched"


def test_raw_reader_rejects_invalid_limit_path_and_nonregular_target(
    tmp_path: Path,
) -> None:
    """Distinguish unsafe parent/name/target from verified path absence."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    target = storage / "rental_control.code_registry"

    invalid_limit = store_module.read_bounded_bytes(target, maximum=-1)
    invalid_parent = store_module.read_bounded_bytes(
        tmp_path / "missing" / "rental_control.code_registry", maximum=1
    )
    invalid_name = store_module.read_bounded_bytes(storage / "other", maximum=1)
    target.mkdir()
    nonregular = store_module.read_bounded_bytes(target, maximum=1)

    assert invalid_limit.failure_code == "invalid_limit"
    assert invalid_parent.failure_code == "unsafe_path"
    assert invalid_name.failure_code == "unsafe_path"
    assert nonregular.failure_code == "unsafe_path"


def test_raw_reader_sanitizes_open_permission_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Permission failures never become absence or expose raw exception text."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    target = storage / "rental_control.code_registry"

    def deny_open(_path: Path, _flags: int) -> int:
        """Inject a permission error containing private details."""
        raise OSError(errno.EACCES, "PIN 2468 /private/path")

    monkeypatch.setattr(store_module.os, "open", deny_open)
    result = store_module.read_bounded_bytes(target, maximum=1)

    assert result.state.value == "failed"
    assert result.failure_code == "read_failed"
    assert "2468" not in repr(result)


def test_evidence_scan_sanitizes_unavailable_and_enumeration_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unsafe path and failed enumeration remain typed sanitized outcomes."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    unavailable = store_module.scan_corrupt_evidence(tmp_path / "elsewhere")
    monkeypatch.setattr(
        store_module.os,
        "scandir",
        lambda _path: (_ for _ in ()).throw(OSError("PIN 2468 /private/path")),
    )
    failed = store_module.scan_corrupt_evidence(storage)

    assert unavailable.failure_code == "scan_unavailable"
    assert failed.failure_code == "scan_failed"
    assert "2468" not in repr(failed)


def test_atomic_writer_replaces_private_file_and_cleans_owned_temp(
    tmp_path: Path,
) -> None:
    """Use the HA runtime AtomicWriter adapter and acknowledge exact bytes."""
    if os.name != "posix":
        pytest.skip("protected writes are explicitly gated outside POSIX")
    storage = tmp_path / ".storage"
    storage.mkdir()
    target = storage / "rental_control.code_registry"
    target.write_bytes(b"old")

    result = store_module.atomic_write_bytes(
        target,
        b"new exact bytes",
        maximum=1024,
    )

    assert result.acknowledged
    assert not result.ambiguous
    assert target.read_bytes() == b"new exact bytes"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert list(storage.iterdir()) == [target]


def test_atomic_writer_refuses_oversize_before_replacing_target(
    tmp_path: Path,
) -> None:
    """Do not create a temp file or mutate an authoritative file over the cap."""
    if os.name != "posix":
        pytest.skip("protected writes are explicitly gated outside POSIX")
    storage = tmp_path / ".storage"
    storage.mkdir()
    target = storage / "rental_control.code_registry"
    target.write_bytes(b"prior")

    result = store_module.atomic_write_bytes(
        target,
        b"oversized",
        maximum=8,
    )

    assert not result.acknowledged
    assert not result.ambiguous
    assert result.failure_code == "serialized_oversize"
    assert target.read_bytes() == b"prior"
    assert list(storage.iterdir()) == [target]


def test_atomic_writer_rejects_symlink_and_missing_runtime_dependency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never follow a symlink or fall back when AtomicWriter import fails."""
    if os.name != "posix":
        pytest.skip("protected writes are explicitly gated outside POSIX")
    storage = tmp_path / ".storage"
    storage.mkdir()
    outside = tmp_path / "outside"
    outside.write_bytes(b"untouched")
    target = storage / "rental_control.code_registry"
    target.symlink_to(outside)

    symlink_result = store_module.atomic_write_bytes(
        target, b"replacement", maximum=1024
    )
    assert symlink_result.failure_code == "unsafe_path"
    assert outside.read_bytes() == b"untouched"
    target.unlink()

    original_import = builtins.__import__

    def reject_atomicwrites(name: str, *args: Any, **kwargs: Any) -> Any:
        """Simulate a missing required runtime dependency."""
        if name == "atomicwrites":
            raise ImportError("sensitive runtime path")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_atomicwrites)
    missing_dependency = store_module.atomic_write_bytes(
        target, b"replacement", maximum=1024
    )

    assert not missing_dependency.acknowledged
    assert missing_dependency.failure_code == "write_failed"
    assert not target.exists()


def test_atomic_writer_directory_fsync_failure_is_ambiguous(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A visible replacement without directory durability is not acknowledged."""
    if os.name != "posix":
        pytest.skip("protected writes are explicitly gated outside POSIX")
    storage = tmp_path / ".storage"
    storage.mkdir()
    target = storage / "rental_control.code_registry"
    target.write_bytes(b"prior")
    actual_fsync = os.fsync
    failed = False

    def fail_directory_fsync(descriptor: int) -> None:
        """Fail the first directory fsync after replacing the target."""
        nonlocal failed
        if stat.S_ISDIR(os.fstat(descriptor).st_mode) and not failed:
            failed = True
            raise OSError("PIN 2468 /private/config/.storage")
        actual_fsync(descriptor)

    monkeypatch.setattr(store_module.os, "fsync", fail_directory_fsync)
    result = store_module.atomic_write_bytes(
        target,
        b"replacement",
        maximum=1024,
    )

    assert failed
    assert not result.acknowledged
    assert result.ambiguous
    assert result.failure_code == "write_failed"
    assert target.read_bytes() == b"replacement"
    assert list(storage.iterdir()) == [target]


def test_atomic_writer_file_fsync_failure_preserves_prior_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pre-replacement file-sync failure is not acknowledged or ambiguous."""
    if os.name != "posix":
        pytest.skip("protected writes are explicitly gated outside POSIX")
    storage = tmp_path / ".storage"
    storage.mkdir()
    target = storage / "rental_control.code_registry"
    target.write_bytes(b"prior")
    actual_fsync = atomicwrites._proper_fsync
    failed = False

    def fail_file_fsync(file_descriptor: int) -> None:
        """Fail before replacement and preserve the prior target."""
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("PIN 2468 /private/config/.storage")
        actual_fsync(file_descriptor)

    monkeypatch.setattr(atomicwrites, "_proper_fsync", fail_file_fsync)
    result = store_module.atomic_write_bytes(
        target,
        b"replacement",
        maximum=1024,
    )

    assert failed
    assert not result.acknowledged
    assert not result.ambiguous
    assert result.failure_code == "write_failed"
    assert target.read_bytes() == b"prior"
    assert list(storage.iterdir()) == [target]


def test_evidence_scan_counts_unrelated_entries_and_detects_cap_overflow(
    tmp_path: Path,
) -> None:
    """Require a complete bounded scan before treating evidence as absent."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    for index in range(4_096):
        (storage / f"unrelated-{index}").touch()

    exact = store_module.scan_corrupt_evidence(storage, maximum_entries=4_096)
    assert exact.complete
    assert exact.failure_code is None
    assert exact.matches == ()

    (storage / "unrelated-overflow").touch()
    overflow = store_module.scan_corrupt_evidence(storage, maximum_entries=4_096)
    assert not overflow.complete
    assert overflow.failure_code == "scan_limit"
    assert overflow.matches == ()


def test_evidence_scan_finds_only_exact_registry_quarantine_names(
    tmp_path: Path,
) -> None:
    """Detect exact registry quarantine siblings without touching their data."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    evidence = storage / "rental_control.code_registry.corrupt.123"
    evidence.write_bytes(b"preserve these bytes")
    decoy = storage / "other.corrupt.123"
    decoy.write_bytes(b"not allocator evidence")

    result = store_module.scan_corrupt_evidence(storage)

    assert result.complete
    assert result.matches == (evidence.name,)
    assert evidence.read_bytes() == b"preserve these bytes"
    assert decoy.read_bytes() == b"not allocator evidence"


def test_owned_temp_is_partial_history_not_quarantine(tmp_path: Path) -> None:
    """A first-run directory with an abandoned owned temp is not true absence."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    temp = storage / (
        ".rental_control.code_registry.rental_control.code_registry."
        + "tmp-"
        + "a" * 32
        + "-random"
    )
    temp.touch(mode=0o600)
    evidence = store_module.scan_corrupt_evidence(storage)

    assert evidence.complete
    assert evidence.matches == ()
    assert evidence.partial_writes
    plan = store_module.plan_protection_recovery(
        store_module.RawFileRead(store_module.RawFileState.ABSENT),
        store_module.RawFileRead(store_module.RawFileState.ABSENT),
        evidence,
    )
    assert plan.action.value == "protective_unknown"
    assert plan.failure_code == "partial_history"
