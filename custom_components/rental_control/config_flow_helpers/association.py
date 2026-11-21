# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Fail-closed config-flow handling for verified Keymaster associations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from homeassistant.config_entries import ConfigEntry

from ..allocator.association_references import has_known_original_safety_record
from ..allocator.association_transactions import AssociationTransaction
from ..allocator.associations import AssociationAttestation
from ..allocator.associations import AssociationChange
from ..allocator.associations import AssociationMode
from ..allocator.associations import AssociationRebindHold
from ..allocator.associations import AssociationTuple
from ..allocator.models import ProtectionAuthorizationState
from ..allocator.models import ProtectionLoadResult
from ..allocator.models import ProtectionLoadState
from ..const import ASSOCIATION_CHOICE_LOCKLESS
from ..const import ASSOCIATION_CHOICE_NO_CHANGE
from ..const import CONF_ASSOCIATION_ATTEST_SAME_LOCK
from ..const import CONF_ASSOCIATION_ENTRY_CHOICE
from ..const import CONF_LOCK_ENTRY
from ..const import DOMAIN
from ..const import LOCK_ENTRY_NONE
from ..const import LOCK_MANAGER
from ..const import PROTECTED_REGISTRY_STORE
from ..const import STORE_CODE_REGISTRY_KEY
from ..coordinator_helpers.config_update import AssociationApplyState
from ..coordinator_helpers.config_update import async_commit_association_transaction
from ..coordinator_helpers.config_update import replay_pending_association_transactions
from .schemas import lock_entry_convert


@dataclass(frozen=True, slots=True)
class AssociationFlowResult:
    """Association transition outcome and sanitized form errors."""

    user_input: dict[str, Any]
    errors: dict[str, str]


@dataclass(frozen=True, slots=True)
class _AssociationIntent:
    """Validated options-flow intent before reading or mutating storage."""

    rc_entry_id: str
    form_input: dict[str, Any]
    submitted: dict[str, Any]
    old: AssociationTuple
    mode: AssociationMode
    keymaster_entry_id: str | None
    legacy_present: bool
    legacy_raw: str | None
    attest_same_lock: bool

    @property
    def changes_tuple(self) -> bool:
        """Return whether the committed association tuple changes."""
        return (
            self.mode is not self.old.mode
            or self.keymaster_entry_id != self.old.verified_keymaster_entry_id
            or (self.legacy_present, self.legacy_raw)
            != (
                self.old.legacy_lock_selection_present,
                self.old.legacy_lock_selection_raw,
            )
        )


async def apply_association_selection(
    flow: Any, user_input: dict[str, Any]
) -> AssociationFlowResult:
    """Acknowledge association state before exposing its options projection."""
    intent_or_result = _build_intent(flow, user_input)
    if isinstance(intent_or_result, AssociationFlowResult):
        return intent_or_result
    intent = intent_or_result
    if not intent.changes_tuple:
        protected = _ready_protected_store(flow)
        if protected is not None:
            _protected_store, loaded = protected
            if not isinstance(loaded.data, dict):
                return _association_error(user_input, "association_unavailable")
            replay_state = _replay_pending_projection(flow, loaded)
            if replay_state not in {
                None,
                AssociationApplyState.CONFIG_ALREADY_CURRENT,
            }:
                return _association_error(user_input, "association_replay_required")
        return AssociationFlowResult(intent.submitted, {})

    protected = _ready_protected_store(flow)
    if protected is None:
        return _association_error(user_input, "association_unavailable")
    protected_store, loaded = protected
    if not isinstance(loaded.data, dict):
        return _association_error(user_input, "association_unavailable")
    replay_state = _replay_pending_projection(flow, loaded)
    if replay_state not in {
        None,
        AssociationApplyState.CONFIG_ALREADY_CURRENT,
    }:
        return _association_error(user_input, "association_replay_required")

    reference_reader = getattr(
        protected_store, "association_references_for_entry", None
    )
    if not callable(reference_reader):
        return _association_error(user_input, "association_unavailable")
    affected_references = reference_reader(intent.rc_entry_id)
    if affected_references is None:
        return _association_error(user_input, "association_unavailable")
    if intent.old.mode is not AssociationMode.VERIFIED:
        known_original = has_known_original_safety_record(
            loaded.data, intent.rc_entry_id
        )
        if known_original is not False:
            return _association_error(user_input, "association_conflict")

    transaction = _build_transaction(intent, loaded, tuple(affected_references))
    if isinstance(transaction, AssociationFlowResult):
        return transaction
    base_envelope = {
        "version": 2,
        "minor_version": 1,
        "key": STORE_CODE_REGISTRY_KEY,
        "data": loaded.data,
    }
    outcome = await async_commit_association_transaction(
        flow.hass,
        protected_store,
        base_envelope,
        transaction,
    )
    if outcome in {
        AssociationApplyState.CONFIG_CHANGED,
        AssociationApplyState.CONFIG_REPLAY_REQUIRED,
    }:
        return _association_error(user_input, "association_replay_required")
    if outcome is not AssociationApplyState.CONFIG_WRITE_SCHEDULED:
        return _association_error(user_input, "association_unavailable")
    return AssociationFlowResult(intent.submitted, {})


def _replay_pending_projection(
    flow: Any, loaded: ProtectionLoadResult
) -> AssociationApplyState | None:
    """Replay only the exact durable transaction chain, if one is present."""
    if not isinstance(loaded.data, dict):
        return AssociationApplyState.CONFIG_REPLAY_REQUIRED
    pending = loaded.data.get("pending_config")
    if not isinstance(pending, list):
        return AssociationApplyState.CONFIG_REPLAY_REQUIRED
    transactions: list[AssociationTransaction] = []
    for record in pending:
        transaction = AssociationTransaction.from_record(record)
        if transaction is None:
            return AssociationApplyState.CONFIG_REPLAY_REQUIRED
        transactions.append(transaction)
    if not transactions:
        return None
    return replay_pending_association_transactions(flow.hass, tuple(transactions))


def _build_intent(
    flow: Any, user_input: dict[str, Any]
) -> _AssociationIntent | AssociationFlowResult:
    """Validate form values and association transition policy."""
    submitted = dict(user_input)
    attest_same_lock = submitted.pop(CONF_ASSOCIATION_ATTEST_SAME_LOCK, False)
    selection = submitted.pop(
        CONF_ASSOCIATION_ENTRY_CHOICE, ASSOCIATION_CHOICE_NO_CHANGE
    )
    if not isinstance(selection, str) or not isinstance(attest_same_lock, bool):
        return _association_error(user_input, "invalid_association")

    entry: ConfigEntry = flow.config_entry
    old = AssociationTuple.from_config(entry.data)
    if old is None:
        return _association_error(user_input, "association_unavailable")
    legacy_selection = _submitted_legacy_selection(flow, user_input, old)
    if legacy_selection is None:
        return _association_error(user_input, "invalid_association")
    legacy_present, legacy_raw = legacy_selection
    target = _requested_target(flow, selection, old, user_input)
    if isinstance(target, AssociationFlowResult):
        return target
    mode, keymaster_entry_id = target
    intent = _AssociationIntent(
        entry.entry_id,
        user_input,
        submitted,
        old,
        mode,
        keymaster_entry_id,
        legacy_present,
        legacy_raw,
        attest_same_lock,
    )

    if not intent.changes_tuple:
        if attest_same_lock:
            return _field_error(
                user_input,
                CONF_ASSOCIATION_ATTEST_SAME_LOCK,
                "attestation_unused",
            )
        return intent
    return intent


def _requested_target(
    flow: Any,
    selection: str,
    old: AssociationTuple,
    user_input: dict[str, Any],
) -> tuple[AssociationMode, str | None] | AssociationFlowResult:
    """Resolve the selected ID against current Keymaster config entries."""
    if selection == ASSOCIATION_CHOICE_NO_CHANGE:
        return old.mode, old.verified_keymaster_entry_id
    if selection == ASSOCIATION_CHOICE_LOCKLESS:
        return AssociationMode.EXPLICIT_LOCKLESS, None
    if (
        old.mode is AssociationMode.VERIFIED
        and selection == old.verified_keymaster_entry_id
    ):
        # A missing or disabled current association must not block unrelated
        # options; only a newly selected candidate is validated here.
        return old.mode, old.verified_keymaster_entry_id
    candidate = flow.hass.config_entries.async_get_entry(selection)
    if (
        candidate is None
        or candidate.domain != LOCK_MANAGER
        or candidate.disabled_by is not None
    ):
        return _field_error(
            user_input,
            CONF_ASSOCIATION_ENTRY_CHOICE,
            "invalid_candidate",
        )
    return AssociationMode.VERIFIED, selection


def _ready_protected_store(
    flow: Any,
) -> tuple[Any, ProtectionLoadResult] | None:
    """Return only the current READY v2 store snapshot."""
    domain_data = flow.hass.data.get(DOMAIN)
    store = (
        domain_data.get(PROTECTED_REGISTRY_STORE)
        if isinstance(domain_data, dict)
        else None
    )
    loaded = getattr(store, "loaded_result", None)
    if (
        store is None
        or getattr(store, "authorization_state", None)
        is not ProtectionAuthorizationState.READY
        or loaded is None
        or loaded.state is not ProtectionLoadState.VALID_V2
        or not isinstance(loaded.transaction_id, str)
        or not isinstance(loaded.generation, int)
        or not isinstance(loaded.data, dict)
    ):
        return None
    return store, loaded


def _build_transaction(
    intent: _AssociationIntent,
    loaded: ProtectionLoadResult,
    affected_references: tuple[str, ...],
) -> AssociationTransaction | AssociationFlowResult:
    """Create one typed transition, requiring scoped historical attestation."""
    if loaded.generation is None or loaded.transaction_id is None:
        return _association_error(intent.form_input, "association_unavailable")
    validation_error = _validate_association_intent(intent, affected_references)
    if validation_error is not None:
        return validation_error
    return _create_association_transaction(intent, loaded, affected_references)


def _validate_association_intent(
    intent: _AssociationIntent,
    affected_references: tuple[str, ...],
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
    intent: _AssociationIntent,
    loaded: ProtectionLoadResult,
    affected_references: tuple[str, ...],
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


def _submitted_legacy_selection(
    flow: Any,
    user_input: dict[str, Any],
    current: AssociationTuple,
) -> tuple[bool, str | None] | None:
    """Convert the legacy title selector without inferring Keymaster identity."""
    if CONF_LOCK_ENTRY not in user_input:
        return (
            current.legacy_lock_selection_present,
            current.legacy_lock_selection_raw,
        )
    selected = user_input[CONF_LOCK_ENTRY]
    if selected is None or selected == LOCK_ENTRY_NONE:
        if not current.legacy_lock_selection_present and selected == LOCK_ENTRY_NONE:
            return False, None
        return True, None
    if not isinstance(selected, str):
        return None
    return True, lock_entry_convert(flow.hass, selected, True)


def _association_error(
    user_input: dict[str, Any],
    error: str,
    *,
    field: str = CONF_ASSOCIATION_ENTRY_CHOICE,
) -> AssociationFlowResult:
    """Return a sanitized error while retaining the submitted form values."""
    return _field_error(user_input, field, error)


def _field_error(
    user_input: dict[str, Any], field: str, error: str
) -> AssociationFlowResult:
    """Build one single-field validation result."""
    return AssociationFlowResult(user_input, {field: error})
