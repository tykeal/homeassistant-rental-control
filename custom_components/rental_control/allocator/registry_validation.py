# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Private protected registry storage protocol helpers."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from homeassistant.helpers.json import prepare_save_json

from ..const import STORE_CODE_REGISTRY_KEY
from .models import PreparedJsonBytes
from .models import ProtectionLoadResult
from .models import ProtectionLoadState
from .models import RawFileRead
from .models import RawFileState
from .registry_files import MAX_REGISTRY_BYTES
from .registry_payload import _is_typed_json_object
from .registry_payload import _is_v1_ledger_shape
from .registry_payload import _is_v2_ledger_shape
from .registry_payload import _reject_json_constant
from .registry_safety_models import references_match_snapshot
from .registry_safety_models import valid_fence_section
from .registry_safety_models import valid_tombstone_section
from .registry_safety_models import validate_snapshot_structure


def prepare_exact_json_bytes(
    payload: dict[str, Any],
    *,
    maximum: int = MAX_REGISTRY_BYTES,
) -> PreparedJsonBytes:
    """Prepare once, normalize the HA result, and hash its exact bounded bytes."""
    try:
        mode, json_data = prepare_save_json(payload)
        if mode == "wb" and isinstance(json_data, bytes):
            data = json_data
        elif mode == "w" and isinstance(json_data, str):
            data = json_data.encode("utf-8")
        else:
            return PreparedJsonBytes(None, None, "serialization_invalid")
        if len(data) > maximum:
            return PreparedJsonBytes(None, None, "serialized_oversize")
        digest = hashlib.sha256(data).hexdigest()
        return PreparedJsonBytes(data, digest)
    except Exception:
        return PreparedJsonBytes(None, None, "serialization_failed")


def validate_v2_envelope_shape(payload: object) -> bool:
    """Validate the explicit v2 wrapper and all typed protection sections."""
    if not isinstance(payload, dict) or set(payload) != {
        "version",
        "minor_version",
        "key",
        "data",
    }:
        return False
    version = payload["version"]
    minor_version = payload["minor_version"]
    if (
        not isinstance(version, int)
        or isinstance(version, bool)
        or version != 2
        or not isinstance(minor_version, int)
        or isinstance(minor_version, bool)
        or minor_version != 1
        or payload["key"] != STORE_CODE_REGISTRY_KEY
    ):
        return False
    data = payload["data"]
    required = {
        "schema_version",
        "transaction_id",
        "generation",
        "ledger",
        "associations",
        "attestations",
        "pending_fences",
        "holds",
        "tombstones",
        "pending_config",
    }
    if not isinstance(data, dict) or set(data) != required:
        return False
    schema_version = data["schema_version"]
    generation = data["generation"]
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != 1
        or not isinstance(data["transaction_id"], str)
        or len(data["transaction_id"]) != 32
        or any(
            character not in "0123456789abcdef" for character in data["transaction_id"]
        )
        or not isinstance(generation, int)
        or isinstance(generation, bool)
        or generation < 1
    ):
        return False
    if not _is_v2_ledger_shape(data["ledger"]):
        return False
    for section in (
        "associations",
        "attestations",
        "holds",
        "pending_config",
    ):
        items = data[section]
        if not isinstance(items, list) or not all(
            _is_typed_json_object(item) for item in items
        ):
            return False
    if not valid_fence_section(data["pending_fences"]):
        return False
    if not valid_tombstone_section(data["tombstones"]):
        return False
    if not validate_snapshot_structure(data):
        return False
    if not references_match_snapshot(
        data["pending_fences"],
        data["tombstones"],
        transaction_id=data["transaction_id"],
        generation=generation,
    ):
        return False
    return True


def _is_opaque_id(value: object) -> bool:
    """Validate a lowercase, fixed-length transaction/activation token."""
    return (
        isinstance(value, str)
        and len(value) == 32
        and all(character in "0123456789abcdef" for character in value)
    )


def _is_sha256_digest(value: object) -> bool:
    """Validate a lowercase SHA-256 digest."""
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _is_finalized_marker(marker: object) -> bool:
    """Validate one complete finalized v1-genesis or v2 marker record."""
    required = {
        "schema_version",
        "activation_id",
        "state",
        "committed_version",
        "committed_generation",
        "envelope_digest",
        "committed_transaction_id",
    }
    if not isinstance(marker, dict) or set(marker) != required:
        return False
    version = marker["committed_version"]
    generation = marker["committed_generation"]
    transaction_id = marker["committed_transaction_id"]
    if (
        marker["schema_version"] != 1
        or isinstance(marker["schema_version"], bool)
        or not isinstance(marker["schema_version"], int)
        or marker["state"] != "finalized"
        or not _is_opaque_id(marker["activation_id"])
        or not isinstance(version, str)
        or version not in {"v1", "v2"}
        or not isinstance(generation, int)
        or isinstance(generation, bool)
        or not _is_sha256_digest(marker["envelope_digest"])
    ):
        return False
    if version == "v1":
        return generation == 0 and transaction_id is None
    return generation >= 1 and _is_opaque_id(transaction_id)


def validate_protection_marker_shape(marker: object) -> bool:
    """Validate exact finalized and pending marker structures and transitions."""
    if _is_finalized_marker(marker):
        return True
    required = {
        "schema_version",
        "activation_id",
        "state",
        "prior",
        "target_transaction_id",
        "target_generation",
        "target_digest",
        "kind",
    }
    if not isinstance(marker, dict) or set(marker) != required:
        return False
    if (
        marker["schema_version"] != 1
        or isinstance(marker["schema_version"], bool)
        or not isinstance(marker["schema_version"], int)
        or marker["state"] != "pending"
        or not _is_opaque_id(marker["activation_id"])
        or not _is_opaque_id(marker["target_transaction_id"])
        or not _is_sha256_digest(marker["target_digest"])
    ):
        return False
    generation = marker["target_generation"]
    if not isinstance(generation, int) or isinstance(generation, bool):
        return False
    prior = marker["prior"]
    if prior == {"state": "genesis_absent"}:
        return (
            marker["kind"] == "genesis"
            and generation == 1
            and marker["target_transaction_id"] != marker["activation_id"]
        )
    if not _is_finalized_marker(prior):
        return False
    if (
        prior["activation_id"] != marker["activation_id"]
        or prior["committed_transaction_id"] == marker["target_transaction_id"]
        or prior["committed_transaction_id"] == marker["activation_id"]
    ):
        return False
    if prior["committed_version"] == "v1":
        return (
            marker["kind"] == "v1_migration"
            and prior["committed_generation"] == 0
            and generation == 1
            and marker["target_transaction_id"] != marker["activation_id"]
        )
    return bool(
        marker["kind"] == "commit"
        and generation == prior["committed_generation"] + 1
        and marker["target_transaction_id"] != marker["activation_id"]
    )


def classify_registry_file(raw: RawFileRead) -> ProtectionLoadResult:
    """Classify raw registry bytes without HA load or quarantine helpers."""
    if raw.state is RawFileState.ABSENT:
        return ProtectionLoadResult(ProtectionLoadState.ABSENT)
    if raw.state is not RawFileState.PRESENT or raw.data is None:
        return ProtectionLoadResult(
            ProtectionLoadState.PROTECTIVE_UNKNOWN,
            failure_code=raw.failure_code or "read_failed",
        )
    try:
        payload = json.loads(
            raw.data.decode("utf-8"), parse_constant=_reject_json_constant
        )
    except Exception:
        return ProtectionLoadResult(
            ProtectionLoadState.PROTECTIVE_UNKNOWN,
            failure_code="decode_failed",
        )
    if not isinstance(payload, dict) or not payload:
        return ProtectionLoadResult(
            ProtectionLoadState.PROTECTIVE_UNKNOWN,
            failure_code="envelope_invalid",
        )
    version = payload.get("version")
    if not isinstance(version, int) or isinstance(version, bool):
        return ProtectionLoadResult(
            ProtectionLoadState.PROTECTIVE_UNKNOWN,
            failure_code="envelope_invalid",
        )
    if version == 1:
        minor_version = payload.get("minor_version", 1)
        data = payload.get("data")
        if (
            not isinstance(minor_version, int)
            or isinstance(minor_version, bool)
            or not isinstance(data, dict)
            or not _is_v1_ledger_shape(data)
        ):
            return ProtectionLoadResult(
                ProtectionLoadState.PROTECTIVE_UNKNOWN,
                failure_code="legacy_invalid",
            )
        return ProtectionLoadResult(ProtectionLoadState.LEGACY_V1, data=data)
    if version == 2 and validate_v2_envelope_shape(payload):
        data = payload["data"]
        return ProtectionLoadResult(
            ProtectionLoadState.VALID_V2,
            data=data,
            transaction_id=data["transaction_id"],
            generation=data["generation"],
        )
    return ProtectionLoadResult(
        ProtectionLoadState.PROTECTIVE_UNKNOWN,
        failure_code="version_unsupported",
    )


def _matches_finalized_marker_record(
    registry_result: ProtectionLoadResult,
    registry_bytes: bytes | None,
    marker: dict[str, Any],
) -> bool:
    """Compare exact disk digest and validated ledger version/transaction."""
    if registry_bytes is None or not _is_finalized_marker(marker):
        return False
    expected_version = marker["committed_version"]
    expected_state = (
        ProtectionLoadState.LEGACY_V1
        if expected_version == "v1"
        else ProtectionLoadState.VALID_V2
    )
    if registry_result.state is not expected_state:
        return False
    if hashlib.sha256(registry_bytes).hexdigest() != marker["envelope_digest"]:
        return False
    if expected_version == "v1":
        return (
            marker["committed_generation"] == 0
            and marker["committed_transaction_id"] is None
        )
    return bool(
        registry_result.generation == marker["committed_generation"]
        and registry_result.transaction_id == marker["committed_transaction_id"]
    )
