# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Build guarded association transition records for config-flow updates."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from ..allocator.association_evidence import AssociationAttestation
from ..allocator.association_evidence import AssociationRebindHold
from ..allocator.association_transactions import AssociationTransaction
from ..allocator.associations import AssociationChange
from ..allocator.associations import AssociationMode
from ..allocator.associations import AssociationTuple
from ..allocator.models import ProtectionLoadResult
from ..const import CONF_ASSOCIATION_ATTEST_SAME_LOCK
from ..const import CONF_ASSOCIATION_ENTRY_CHOICE
from ..const import LOCK_ENTRY_NONE
from .association_flow_models import AssociationFlowResult
from .association_flow_models import AssociationIntent


def build_association_transaction(
    intent: AssociationIntent,
    loaded: ProtectionLoadResult,
    affected_references: tuple[str, ...],
    *,
    independently_proven_original: str | None,
) -> AssociationTransaction | AssociationFlowResult:
    """Create one typed transition, requiring scoped historical attestation."""
    if loaded.generation is None or loaded.transaction_id is None:
        return _association_error(intent.form_input, "association_unavailable")
    validation_error = _validate_association_intent(
        intent,
        affected_references,
        independently_proven_original=independently_proven_original,
    )
    if validation_error is not None:
        return validation_error
    return _create_association_transaction(
        intent,
        loaded,
        affected_references,
        independently_proven_original=independently_proven_original,
    )


def _validate_association_intent(
    intent: AssociationIntent,
    affected_references: tuple[str, ...],
    *,
    independently_proven_original: str | None,
) -> AssociationFlowResult | None:
    """Validate how selected history may be attached to a Keymaster entry."""
    if (
        intent.old.mode is AssociationMode.EXPLICIT_LOCKLESS
        and intent.mode is AssociationMode.VERIFIED
        and affected_references
    ):
        return _association_error(
            intent.form_input,
            "association_conflict",
            field=CONF_ASSOCIATION_ENTRY_CHOICE,
        )
    if (
        intent.old.mode is AssociationMode.LEGACY_UNRESOLVED
        and intent.mode is AssociationMode.VERIFIED
        and affected_references
    ):
        if intent.old.legacy_lock_selection_raw in {None, LOCK_ENTRY_NONE}:
            return _association_error(
                intent.form_input,
                "association_conflict",
                field=CONF_ASSOCIATION_ENTRY_CHOICE,
            )
        if independently_proven_original == intent.keymaster_entry_id:
            if intent.attest_same_lock:
                return _association_error(
                    intent.form_input,
                    "attestation_unused",
                    field=CONF_ASSOCIATION_ATTEST_SAME_LOCK,
                )
            return None
        if not intent.attest_same_lock:
            return _association_error(
                intent.form_input,
                "attestation_required",
                field=CONF_ASSOCIATION_ATTEST_SAME_LOCK,
            )
    elif intent.attest_same_lock:
        return _association_error(
            intent.form_input,
            "attestation_unused",
            field=CONF_ASSOCIATION_ATTEST_SAME_LOCK,
        )
    return None


def _create_association_transaction(
    intent: AssociationIntent,
    loaded: ProtectionLoadResult,
    affected_references: tuple[str, ...],
    *,
    independently_proven_original: str | None,
) -> AssociationTransaction:
    """Build tuple, attestation, and retained-hold records for one commit."""
    assert loaded.generation is not None
    assert loaded.transaction_id is not None
    transaction_generation = loaded.generation + 1
    transaction_id = uuid4().hex
    new_tuple = AssociationTuple(
        intent.legacy_present,
        intent.legacy_raw,
        intent.keymaster_entry_id,
        intent.old.association_generation + 1,
        transaction_id,
        intent.mode,
    )
    change = AssociationChange(
        intent.rc_entry_id,
        intent.old,
        new_tuple,
        affected_references,
        independently_proven_original
        if (
            independently_proven_original is not None
            and intent.old.mode is AssociationMode.LEGACY_UNRESOLVED
            and independently_proven_original == intent.keymaster_entry_id
        )
        else None,
        affected_references
        if (
            independently_proven_original is not None
            and intent.old.mode is AssociationMode.LEGACY_UNRESOLVED
            and independently_proven_original == intent.keymaster_entry_id
        )
        else (),
    )
    retained_holds: tuple[AssociationRebindHold, ...] = ()
    if (
        intent.old.mode is AssociationMode.VERIFIED
        and intent.old.verified_keymaster_entry_id is not None
        and intent.keymaster_entry_id != intent.old.verified_keymaster_entry_id
        and affected_references
    ):
        retained_holds = (
            AssociationRebindHold(
                uuid4().hex,
                intent.rc_entry_id,
                intent.old.verified_keymaster_entry_id,
                intent.keymaster_entry_id,
                affected_references,
                transaction_id,
                transaction_generation,
            ),
        )
    attestations: tuple[AssociationAttestation, ...] = ()
    if (
        intent.attest_same_lock
        and intent.old.mode is AssociationMode.LEGACY_UNRESOLVED
        and intent.mode is AssociationMode.VERIFIED
        and affected_references
        and intent.keymaster_entry_id is not None
    ):
        attestations = (
            AssociationAttestation(
                uuid4().hex,
                intent.rc_entry_id,
                intent.keymaster_entry_id,
                affected_references,
                transaction_id,
            ),
        )
    return AssociationTransaction(
        transaction_id,
        transaction_generation,
        loaded.transaction_id,
        (change,),
        attestations,
        retained_holds,
    )


def _association_error(
    user_input: dict[str, Any],
    error: str,
    *,
    field: str = CONF_ASSOCIATION_ENTRY_CHOICE,
) -> AssociationFlowResult:
    """Return one sanitized transaction validation error."""
    return AssociationFlowResult(user_input, {field: error})
