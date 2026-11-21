# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Private protected registry storage protocol helpers."""

from __future__ import annotations

import math
from typing import Any

from ..const import CODE_REGISTRY_SCHEMA_VERSION
from .code_codec import decode_code
from .models import AllocationOrigin


def _is_v1_ledger_shape(data: dict[str, Any]) -> bool:
    """Apply the actual legacy loader's payload and record validations."""
    schema_version = data.get("schema_version")
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != CODE_REGISTRY_SCHEMA_VERSION
        or not isinstance(data.get("records"), list)
    ):
        return False
    codes: set[str] = set()
    identities: set[str] = set()
    for record in data["records"]:
        if not isinstance(record, dict):
            return False
        code_length = record.get("code_length")
        encoded_code = record.get("encoded_code")
        salt_value = record.get("encoding_salt_value")
        owners = record.get("owners")
        if (
            not isinstance(code_length, int)
            or isinstance(code_length, bool)
            or code_length < 1
            or not isinstance(encoded_code, str)
            or not encoded_code
            or record.get("encoding_salt_source") != "entry_id"
            or not isinstance(salt_value, str)
            or not salt_value
            or not isinstance(record.get("created_at"), str)
            or not record["created_at"]
            or not isinstance(record.get("updated_at"), str)
            or not record["updated_at"]
            or not isinstance(owners, list)
            or not owners
        ):
            return False
        try:
            code = decode_code(encoded_code, salt_value)
        except Exception:
            return False
        if not code.isdecimal() or len(code) != code_length or code in codes:
            return False
        codes.add(code)
        for owner in owners:
            if not isinstance(owner, dict):
                return False
            entry_id = owner.get("entry_id")
            identity_key = owner.get("identity_key")
            origin = owner.get("origin")
            lockname = owner.get("lockname")
            slot = owner.get("slot")
            if (
                not isinstance(entry_id, str)
                or not entry_id
                or not isinstance(identity_key, str)
                or not identity_key
                or not isinstance(origin, str)
                or origin not in {item.value for item in AllocationOrigin}
                or (lockname is not None and not isinstance(lockname, str))
                or (
                    slot is not None
                    and (
                        not isinstance(slot, int) or isinstance(slot, bool) or slot < 1
                    )
                )
                or ((lockname is None) != (slot is None))
                or not isinstance(owner.get("lock_observed", False), bool)
                or not isinstance(owner.get("first_seen"), str)
                or not owner["first_seen"]
                or not isinstance(owner.get("last_seen"), str)
                or not owner["last_seen"]
                or identity_key in identities
            ):
                return False
            identities.add(identity_key)
    return True


def _is_v2_ledger_shape(ledger: object) -> bool:
    """Require the complete, typed current ledger representation."""
    if not isinstance(ledger, dict) or set(ledger) != {
        "schema_version",
        "updated_at",
        "code_ref_salt",
        "records",
        "announced_conflicts",
    }:
        return False
    schema_version = ledger.get("schema_version")
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != CODE_REGISTRY_SCHEMA_VERSION
        or not isinstance(ledger.get("updated_at"), str)
        or not ledger["updated_at"]
        or not isinstance(ledger.get("code_ref_salt"), str)
        or not ledger["code_ref_salt"]
        or not isinstance(ledger.get("records"), list)
        or not _is_v1_ledger_shape(ledger)
        or not _is_announced_conflicts(ledger.get("announced_conflicts"))
    ):
        return False
    for record in ledger["records"]:
        if not isinstance(record, dict) or set(record) != {
            "encoded_code",
            "encoding_salt_source",
            "encoding_salt_value",
            "code_length",
            "created_at",
            "updated_at",
            "owners",
        }:
            return False
        if any(
            not isinstance(owner, dict)
            or set(owner)
            != {
                "entry_id",
                "identity_key",
                "origin",
                "lockname",
                "slot",
                "lock_observed",
                "first_seen",
                "last_seen",
            }
            for owner in record["owners"]
        ):
            return False
    return True


def _is_announced_conflicts(value: object) -> bool:
    """Validate the v2 copy of the conflict-notice cache."""
    if not isinstance(value, dict):
        return False
    return all(
        isinstance(code_ref, str)
        and bool(code_ref)
        and isinstance(slots, list)
        and all(
            isinstance(slot, list)
            and len(slot) == 3
            and all(isinstance(part, str) for part in slot)
            for slot in slots
        )
        for code_ref, slots in value.items()
    )


def _is_typed_json_object(item: object) -> bool:
    """Reject malformed safety records and invalid JSON values."""
    if not isinstance(item, dict) or not item:
        return False
    for key, value in item.items():
        if not isinstance(key, str) or not key:
            return False
        if key.endswith("_id") and value is not None and not isinstance(value, str):
            return False
        if (
            key in {"generation", "slot"}
            and value is not None
            and (not isinstance(value, int) or isinstance(value, bool))
        ):
            return False
        if not _is_json_value(value):
            return False
    return True


def _is_json_value(value: object) -> bool:
    """Return whether a value contains only JSON-compatible types."""
    if value is None or isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _is_json_value(item) for key, item in value.items()
        )
    return False


def _reject_json_constant(value: str) -> None:
    """Reject Python's non-standard NaN and infinity JSON extensions."""
    raise ValueError(value)
