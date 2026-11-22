# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Opaque association references derived from acknowledged protected state."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import hmac
import json
from typing import Any

from .association_evidence import AssociationRebindHold
from .association_transactions import valid_association_sections
from .code_codec import code_ref_for
from .code_codec import decode_code
from .registry_safety_models import valid_fence_section
from .registry_safety_models import valid_tombstone_section


class OriginalEntryEvidenceState(StrEnum):
    """Typed result for immutable original-entry provenance."""

    ABSENT = "absent"
    UNIQUE = "unique"
    AMBIGUOUS = "ambiguous"
    INCOMPLETE = "incomplete"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class OriginalEntryEvidence:
    """One typed provenance result without collapsing unsafe states to absence."""

    state: OriginalEntryEvidenceState
    keymaster_entry_id: str | None = None


def original_keymaster_entry_evidence(
    data: dict[str, Any],
    rc_entry_id: str,
    references: tuple[str, ...],
    *,
    require_complete_snapshot: bool = True,
) -> OriginalEntryEvidence:
    """Classify exact source evidence; only a clean no-record result is absent."""
    validated = _validate_original_entry_snapshot(
        data,
        rc_entry_id,
        references,
        require_complete_snapshot=require_complete_snapshot,
    )
    if isinstance(validated, OriginalEntryEvidence):
        return validated
    target_references, fences, tombstones, salt = validated
    collected = _collect_original_entry_sources(
        rc_entry_id,
        target_references,
        fences,
        tombstones,
        salt,
    )
    if collected is None:
        return OriginalEntryEvidence(OriginalEntryEvidenceState.INVALID)
    known_by_reference = collected
    return _classify_original_entry_sources(
        target_references,
        known_by_reference,
    )


def _validate_original_entry_snapshot(
    data: dict[str, Any],
    rc_entry_id: str,
    references: tuple[str, ...],
    *,
    require_complete_snapshot: bool,
) -> (
    tuple[
        set[str],
        list[dict[str, Any]],
        list[dict[str, Any]],
        str,
    ]
    | OriginalEntryEvidence
):
    """Validate all sections before absence can become an attestation fallback."""
    if not references or len(set(references)) != len(references):
        return OriginalEntryEvidence(OriginalEntryEvidenceState.INVALID)
    if any(not isinstance(reference, str) or not reference for reference in references):
        return OriginalEntryEvidence(OriginalEntryEvidenceState.INVALID)
    expected = association_references_for_snapshot(data, rc_entry_id)
    if expected is None:
        return OriginalEntryEvidence(OriginalEntryEvidenceState.INVALID)
    target_references = set(references)
    expected_references = set(expected)
    if not target_references.issubset(expected_references) or (
        require_complete_snapshot and target_references != expected_references
    ):
        return OriginalEntryEvidence(OriginalEntryEvidenceState.INCOMPLETE)
    if not valid_association_sections(
        data.get("associations"),
        data.get("attestations"),
        data.get("pending_config"),
        data.get("holds"),
    ):
        return OriginalEntryEvidence(OriginalEntryEvidenceState.INVALID)

    fences = data.get("pending_fences")
    tombstones = data.get("tombstones")
    holds = data.get("holds")
    if (
        not isinstance(fences, list)
        or not isinstance(tombstones, list)
        or not isinstance(holds, list)
        or not valid_fence_section(fences)
        or not valid_tombstone_section(tombstones)
        or not _holds_are_typed(holds)
    ):
        return OriginalEntryEvidence(OriginalEntryEvidenceState.INVALID)
    salt = _code_reference_salt(data)
    if salt is None:
        return OriginalEntryEvidence(OriginalEntryEvidenceState.INVALID)
    return target_references, fences, tombstones, salt


def _holds_are_typed(holds: list[Any]) -> bool:
    """Require every retained hold to have a recognized typed shape."""
    return all(
        isinstance(hold, dict)
        and hold.get("kind") == "association_rebind"
        and AssociationRebindHold.from_record(hold) is not None
        for hold in holds
    )


def _collect_original_entry_sources(
    rc_entry_id: str,
    target_references: set[str],
    fences: list[dict[str, Any]],
    tombstones: list[dict[str, Any]],
    salt: str,
) -> dict[str, set[str]] | None:
    """Collect recognized exact-reference origins from validated safety rows."""
    known: dict[str, set[str]] = {}
    for section, records in (
        ("pending_fences", fences),
        ("tombstones", tombstones),
    ):
        for record in records:
            if record["rc_entry_id"] != rc_entry_id:
                continue
            proven = _original_proven_references(section, record, rc_entry_id, salt)
            if proven is None:
                return None
            for reference, entry_id in proven.items():
                if reference in target_references:
                    known.setdefault(reference, set()).add(entry_id)
    return known


def _classify_original_entry_sources(
    target_references: set[str],
    known_by_reference: dict[str, set[str]],
) -> OriginalEntryEvidence:
    """Distinguish a unique origin, conflict, partial proof, and clean absence."""
    if any(len(entries) > 1 for entries in known_by_reference.values()):
        return OriginalEntryEvidence(OriginalEntryEvidenceState.AMBIGUOUS)
    known_references = set(known_by_reference)
    known_entries = {
        next(iter(entries)) for entries in known_by_reference.values() if entries
    }
    if len(known_entries) > 1:
        return OriginalEntryEvidence(OriginalEntryEvidenceState.AMBIGUOUS)
    if known_references and known_references != target_references:
        return OriginalEntryEvidence(OriginalEntryEvidenceState.INCOMPLETE)
    if known_references == target_references:
        return OriginalEntryEvidence(
            OriginalEntryEvidenceState.UNIQUE,
            next(iter(known_entries)),
        )
    return OriginalEntryEvidence(OriginalEntryEvidenceState.ABSENT)


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


def ledger_allocation_references(
    data: dict[str, Any],
    rc_entry_id: str,
    identity_key: str,
) -> tuple[str, str, int] | None:
    """Return domain-separated opaque references for one exact ledger owner.

    The returned values are suitable for binding a slot fence to a current
    allocation without persisting or logging a PIN.  Ambiguous, malformed, or
    undecodable ledger rows fail closed.
    """
    ledger = data.get("ledger")
    if not isinstance(ledger, dict):
        return None
    salt = _code_reference_salt(data)
    records = ledger.get("records")
    if salt is None or not isinstance(records, list):
        return None

    match: tuple[str, str, int] | None = None
    try:
        for record in records:
            if not isinstance(record, dict) or not isinstance(
                record.get("owners"), list
            ):
                return None
            for owner in record["owners"]:
                if not isinstance(owner, dict):
                    return None
                if (
                    owner.get("entry_id") != rc_entry_id
                    or owner.get("identity_key") != identity_key
                ):
                    continue
                owner_reference = ledger_owner_reference(data, record, owner)
                if owner_reference is None:
                    return None
                encoded_code = record.get("encoded_code")
                encoding_salt = record.get("encoding_salt_value")
                if not isinstance(encoded_code, str) or not isinstance(
                    encoding_salt, str
                ):
                    return None
                code_reference = code_ref_for(
                    decode_code(encoded_code, encoding_salt), salt
                )
                slot = owner.get("slot")
                if not isinstance(slot, int) or isinstance(slot, bool) or slot < 1:
                    return None
                allocation_reference = _opaque_reference(
                    salt,
                    "allocation",
                    code_reference,
                    rc_entry_id,
                    identity_key,
                    owner.get("lockname"),
                    slot,
                )
                if match is not None:
                    return None
                match = (owner_reference, allocation_reference, slot)
    except Exception:
        return None
    return match


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
