# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Opaque association references derived from acknowledged protected state."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from .association_evidence import AssociationRebindHold
from .code_codec import code_ref_for
from .code_codec import decode_code


def association_references_for_snapshot(
    data: dict[str, Any], rc_entry_id: str
) -> tuple[str, ...] | None:
    """Return all provably attributable references or fail closed."""
    code_ref_salt = _code_reference_salt(data)
    if code_ref_salt is None:
        return None
    ledger_references = _ledger_owner_references(data, rc_entry_id)
    if ledger_references is None:
        return None
    retained_references = _retained_claim_references(data, rc_entry_id)
    if retained_references is None:
        return None
    safety_references = _safety_record_references(data, rc_entry_id, code_ref_salt)
    if safety_references is None:
        return None
    return tuple(sorted((ledger_references | safety_references) - retained_references))


def has_known_original_safety_record(
    data: dict[str, Any], rc_entry_id: str
) -> bool | None:
    """Report scoped safety records whose immutable source is a known KM entry."""
    code_ref_salt = _code_reference_salt(data)
    retained_references = _retained_claim_references(data, rc_entry_id)
    if code_ref_salt is None or retained_references is None:
        return None
    for section in ("pending_fences", "holds", "tombstones"):
        records = data.get(section)
        if not isinstance(records, list):
            return None
        for record in records:
            if not isinstance(record, dict):
                return None
            if (
                record.get("rc_entry_id") == rc_entry_id
                and record.get("original_km_entry_id") is not None
                and record.get("kind") != "association_rebind"
            ):
                reference = _opaque_reference(
                    code_ref_salt,
                    f"safety:{section}",
                    json.dumps(
                        record,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                )
                if reference not in retained_references:
                    return True
            if "rc_entry_id" not in record and section == "holds":
                return None
    return False


def original_keymaster_entry_for_references(
    data: dict[str, Any],
    rc_entry_id: str,
    references: tuple[str, ...],
    *,
    require_complete_snapshot: bool = True,
) -> str | None:
    """Return one independently proven original entry for every exact reference."""
    expected = association_references_for_snapshot(data, rc_entry_id)
    if (
        not references
        or expected is None
        or len(set(references)) != len(references)
        or (require_complete_snapshot and set(expected) != set(references))
    ):
        return None
    salt = _code_reference_salt(data)
    if salt is None:
        return None
    proven: dict[str, set[str]] = {}
    for section in ("pending_fences", "holds", "tombstones"):
        records = data.get(section)
        if not isinstance(records, list):
            return None
        for record in records:
            if not isinstance(record, dict):
                return None
            record_references = _original_proven_references(
                section,
                record,
                rc_entry_id,
                salt,
            )
            if record_references is None:
                return None
            for reference, original in record_references.items():
                if reference in references:
                    proven.setdefault(reference, set()).add(original)
    if set(proven) != set(references) or any(
        len(entries) != 1 for entries in proven.values()
    ):
        return None
    original_entries = {next(iter(entries)) for entries in proven.values()}
    return next(iter(original_entries)) if len(original_entries) == 1 else None


def _original_proven_references(
    section: str,
    record: dict[str, Any],
    rc_entry_id: str,
    salt: str,
) -> dict[str, str] | None:
    """Map one validated safety record's stable opaque refs to its original."""
    if section == "holds" and record.get("kind") == "association_rebind":
        return None if AssociationRebindHold.from_record(record) is None else {}
    if record.get("rc_entry_id") != rc_entry_id:
        return {}
    original = record.get("original_km_entry_id")
    if not isinstance(original, str) or not original:
        return {}
    serialized = json.dumps(
        record,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    references = {_opaque_reference(salt, f"safety:{section}", serialized): original}
    if section == "tombstones":
        original_fence_reference = _original_fence_reference(record, salt)
        if original_fence_reference is not None:
            references[original_fence_reference] = original
    owner_reference = record.get("owner_ref")
    if isinstance(owner_reference, str):
        references[owner_reference] = original
    return references


def _original_fence_reference(record: dict[str, Any], salt: str) -> str | None:
    """Recreate the immutable source-fence digest retained in its tombstone."""
    original_fields = (
        "fence_id",
        "original_km_entry_id",
        "slot",
        "rc_entry_id",
        "owner_ref",
        "allocation_ref",
        "created_transaction_id",
        "created_generation",
        "fence_type",
    )
    if not all(field in record for field in original_fields):
        return None
    original_fence = {key: record[key] for key in original_fields}
    encoded = json.dumps(
        original_fence,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _opaque_reference(salt, "safety:pending_fences", encoded)


def _code_reference_salt(data: dict[str, Any]) -> str | None:
    """Read the persisted ledger salt required for every opaque reference."""
    ledger = data.get("ledger")
    if not isinstance(ledger, dict):
        return None
    salt = ledger.get("code_ref_salt")
    return salt if isinstance(salt, str) and salt else None


def _ledger_owner_references(data: dict[str, Any], rc_entry_id: str) -> set[str] | None:
    """Digest ledger owners using the existing salted code-reference scheme."""
    ledger = data.get("ledger")
    if not isinstance(ledger, dict):
        return None
    code_ref_salt = ledger.get("code_ref_salt")
    records = ledger.get("records")
    if not isinstance(code_ref_salt, str) or not code_ref_salt:
        return None
    if not isinstance(records, list):
        return None

    references: set[str] = set()
    try:
        for record in records:
            if not isinstance(record, dict):
                return None
            owners = record.get("owners")
            encoded_code = record.get("encoded_code")
            encoding_salt = record.get("encoding_salt_value")
            if (
                not isinstance(owners, list)
                or not isinstance(encoded_code, str)
                or not isinstance(encoding_salt, str)
            ):
                return None
            code_ref = _code_reference_for_entry(
                owners,
                rc_entry_id,
                encoded_code,
                encoding_salt,
                code_ref_salt,
            )
            if code_ref is None:
                continue
            for owner in owners:
                if not isinstance(owner, dict) or owner.get("entry_id") != rc_entry_id:
                    continue
                identity_key = owner.get("identity_key")
                if not isinstance(identity_key, str) or not identity_key:
                    return None
                references.add(
                    _opaque_reference(
                        code_ref_salt,
                        "owner",
                        code_ref,
                        rc_entry_id,
                        identity_key,
                        owner.get("lockname"),
                        owner.get("slot"),
                    )
                )
    except Exception:
        return None
    return references


def _code_reference_for_entry(
    owners: list[Any],
    rc_entry_id: str,
    encoded_code: str,
    encoding_salt: str,
    code_ref_salt: str,
) -> str | None:
    """Compute one masked code reference only when this entry owns it."""
    if not any(
        isinstance(owner, dict) and owner.get("entry_id") == rc_entry_id
        for owner in owners
    ):
        return None
    return code_ref_for(decode_code(encoded_code, encoding_salt), code_ref_salt)


def ledger_owner_reference(
    data: dict[str, Any],
    record: dict[str, Any],
    owner: dict[str, Any],
) -> str | None:
    """Return one PIN-free identity for an exact serialized ledger owner."""
    code_ref_salt = _code_reference_salt(data)
    if (
        code_ref_salt is None
        or not isinstance(record.get("encoded_code"), str)
        or not isinstance(record.get("encoding_salt_value"), str)
        or not isinstance(owner.get("entry_id"), str)
        or not isinstance(owner.get("identity_key"), str)
    ):
        return None
    try:
        code_ref = code_ref_for(
            decode_code(record["encoded_code"], record["encoding_salt_value"]),
            code_ref_salt,
        )
    except Exception:
        return None
    return _opaque_reference(
        code_ref_salt,
        "owner",
        code_ref,
        owner["entry_id"],
        owner["identity_key"],
        owner.get("lockname"),
        owner.get("slot"),
    )


def _safety_record_references(
    data: dict[str, Any], rc_entry_id: str, code_ref_salt: str
) -> set[str] | None:
    """Salt and domain-separate scoped fences, holds, and tombstones."""
    references: set[str] = set()
    for section in ("pending_fences", "holds", "tombstones"):
        records = data.get(section)
        if not isinstance(records, list):
            return None
        for record in records:
            if not isinstance(record, dict):
                return None
            if section == "holds" and record.get("kind") == "association_rebind":
                if AssociationRebindHold.from_record(record) is None:
                    return None
                continue
            if record.get("rc_entry_id") == rc_entry_id:
                references.add(
                    _opaque_reference(
                        code_ref_salt,
                        f"safety:{section}",
                        json.dumps(
                            record,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    )
                )
            elif "rc_entry_id" not in record and section == "holds":
                # An unscoped hold may cover this entry; do not guess.
                return None
    return references


def _retained_claim_references(
    data: dict[str, Any], rc_entry_id: str
) -> set[str] | None:
    """Return refs already durably retained under their original KM identity."""
    records = data.get("holds")
    if not isinstance(records, list):
        return None
    references: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            return None
        if record.get("kind") != "association_rebind":
            continue
        hold = AssociationRebindHold.from_record(record)
        if hold is None:
            return None
        if hold.rc_entry_id == rc_entry_id:
            if references.intersection(hold.claim_references):
                return None
            references.update(hold.claim_references)
    return references


def _opaque_reference(code_ref_salt: str, domain: str, *parts: object) -> str:
    """Create a persisted-salt, domain-separated opaque reference."""
    encoded = "\0".join(str(part) for part in parts).encode("utf-8")
    message = b"rental-control:association-reference:v1\0"
    message += domain.encode("utf-8") + b"\0" + encoded
    return hmac.new(code_ref_salt.encode("utf-8"), message, hashlib.sha256).hexdigest()
