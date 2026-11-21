# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Verify exact association tuples, transaction chains, and replay guards."""

from __future__ import annotations

import asyncio
import copy
from dataclasses import replace
import json
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntry
import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.rental_control.allocator.association_commit import (
    add_association_transaction_to_envelope,
)
from custom_components.rental_control.allocator.association_evidence import (
    AssociationAttestation,
)
from custom_components.rental_control.allocator.association_evidence import (
    AssociationRebindHold,
)
from custom_components.rental_control.allocator.association_references import (
    association_references_for_snapshot,
)
from custom_components.rental_control.allocator.association_references import (
    ledger_owner_reference,
)
from custom_components.rental_control.allocator.association_transactions import (
    AssociationReplayState,
)
from custom_components.rental_control.allocator.association_transactions import (
    AssociationTransaction,
)
from custom_components.rental_control.allocator.association_transactions import (
    association_storage_record,
)
from custom_components.rental_control.allocator.association_transactions import (
    replay_association_chain,
)
from custom_components.rental_control.allocator.association_transactions import (
    valid_association_sections,
)
from custom_components.rental_control.allocator.associations import AssociationChange
from custom_components.rental_control.allocator.associations import AssociationMode
from custom_components.rental_control.allocator.associations import AssociationTuple
from custom_components.rental_control.allocator.code_codec import encode_code
from custom_components.rental_control.allocator.models import (
    ProtectionAuthorizationState,
)
from custom_components.rental_control.allocator.models import ProtectionLoadResult
from custom_components.rental_control.allocator.models import ProtectionLoadState
from custom_components.rental_control.allocator.protected_registry import (
    async_commit_protection_transaction,
)
from custom_components.rental_control.allocator.protected_store import (
    ProtectedRegistryStore,
)
from custom_components.rental_control.allocator.registry_safety_models import (
    resolve_fence_to_tombstone,
)
from custom_components.rental_control.allocator.registry_safety_transitions import (
    validate_snapshot_transition,
)
from custom_components.rental_control.allocator.registry_validation import (
    validate_v2_envelope_shape,
)
from custom_components.rental_control.const import CONF_ASSOCIATION_GENERATION
from custom_components.rental_control.const import CONF_ASSOCIATION_MODE
from custom_components.rental_control.const import CONF_ASSOCIATION_TRANSACTION_ID
from custom_components.rental_control.const import CONF_LOCK_ENTRY
from custom_components.rental_control.const import CONF_VERIFIED_KEYMASTER_ENTRY_ID
from custom_components.rental_control.const import DOMAIN
from custom_components.rental_control.coordinator_helpers.config_update import (
    AssociationApplyState,
)
from custom_components.rental_control.coordinator_helpers.config_update import (
    async_commit_association_transaction,
)
from custom_components.rental_control.coordinator_helpers.config_update import (
    preserve_committed_association_tuple,
)
from custom_components.rental_control.coordinator_helpers.config_update import (
    replay_pending_association_transactions,
)

_TX1 = "a" * 32
_TX2 = "b" * 32
_TX3 = "c" * 32
_ATTESTATION_ID = "d" * 32
_PREVIOUS_TX = "e" * 32


def _legacy(
    *,
    present: bool = True,
    raw: str | None = "Front Door",
) -> AssociationTuple:
    """Build the exact pre-association tuple."""
    return AssociationTuple(
        present,
        raw,
        None,
        0,
        None,
        AssociationMode.LEGACY_UNRESOLVED,
    )


def _verified(
    txid: str,
    *,
    generation: int = 1,
    entry_id: str = "km-A",
    raw: str | None = "Front Door",
) -> AssociationTuple:
    """Build one committed verified tuple."""
    return AssociationTuple(
        True,
        raw,
        entry_id,
        generation,
        txid,
        AssociationMode.VERIFIED,
    )


def test_tuple_preserves_absence_none_sentinel_and_explicit_lockless() -> None:
    """Legacy raw selection bytes are not normalized during capture or replay."""
    absent = AssociationTuple.from_config({})
    explicit_none = AssociationTuple.from_config({CONF_LOCK_ENTRY: None})
    sentinel = AssociationTuple.from_config({CONF_LOCK_ENTRY: "(none)"})
    assert absent is not None
    assert explicit_none is not None
    assert sentinel is not None
    assert absent != explicit_none
    assert explicit_none != sentinel
    assert absent.legacy_lock_selection_present is False
    assert explicit_none.legacy_lock_selection_present is True
    assert sentinel.legacy_lock_selection_raw == "(none)"

    lockless = AssociationTuple(
        True,
        "(none)",
        None,
        1,
        _TX1,
        AssociationMode.EXPLICIT_LOCKLESS,
    )
    assert AssociationTuple.from_record(lockless.to_record()) == lockless
    assert lockless != sentinel


def test_committed_legacy_selector_stays_unresolved() -> None:
    """A transaction may retain legacy selection without inventing identity."""
    unresolved = AssociationTuple(
        True,
        "lock.front_door",
        None,
        1,
        _TX1,
        AssociationMode.LEGACY_UNRESOLVED,
    )

    assert AssociationTuple.from_record(unresolved.to_record()) == unresolved
    assert (
        unresolved.merge_into_config({"unrelated": True})["association_mode"]
        == AssociationMode.LEGACY_UNRESOLVED.value
    )


def test_tuple_merge_preserves_unrelated_data_and_legacy_presence() -> None:
    """Apply association fields without replacing unrelated config."""
    current = {
        CONF_LOCK_ENTRY: "Front Door",
        "unrelated_option": {"kept": True},
    }
    new = _verified(_TX1)

    merged = new.merge_into_config(current)

    assert merged["unrelated_option"] == {"kept": True}
    assert merged[CONF_LOCK_ENTRY] == "Front Door"
    assert merged[CONF_VERIFIED_KEYMASTER_ENTRY_ID] == "km-A"
    absent = _legacy(present=False, raw=None).merge_into_config(current)
    assert CONF_LOCK_ENTRY not in absent
    assert absent["unrelated_option"] == {"kept": True}


def test_options_projection_allows_legacy_selector_and_strips_tuple_injection() -> None:
    """Uncommitted legacy options stay editable; tuple metadata stays trusted."""
    absent = preserve_committed_association_tuple(
        {"unrelated": "stored"},
        {
            CONF_LOCK_ENTRY: None,
            "unrelated": "new",
            CONF_ASSOCIATION_MODE: "verified",
            CONF_VERIFIED_KEYMASTER_ENTRY_ID: "crafted-entry",
            CONF_ASSOCIATION_GENERATION: 7,
            CONF_ASSOCIATION_TRANSACTION_ID: "e" * 32,
        },
    )
    explicit_none = preserve_committed_association_tuple(
        {CONF_LOCK_ENTRY: None, "unrelated": "stored"},
        {"unrelated": "new"},
    )

    assert absent[CONF_LOCK_ENTRY] is None
    assert explicit_none[CONF_LOCK_ENTRY] is None
    assert absent["unrelated"] == explicit_none["unrelated"] == "new"
    assert CONF_ASSOCIATION_MODE not in absent
    assert CONF_VERIFIED_KEYMASTER_ENTRY_ID not in absent
    assert CONF_ASSOCIATION_GENERATION not in absent
    assert CONF_ASSOCIATION_TRANSACTION_ID not in absent


def test_options_projection_preserves_committed_tuple_without_backend() -> None:
    """Options cannot overwrite or erase a committed tuple when unavailable."""
    config_data = {
        CONF_LOCK_ENTRY: "lock.front_door",
        CONF_ASSOCIATION_MODE: "verified",
        CONF_VERIFIED_KEYMASTER_ENTRY_ID: "km-a",
        CONF_ASSOCIATION_GENERATION: 4,
        CONF_ASSOCIATION_TRANSACTION_ID: "a" * 32,
    }

    projected = preserve_committed_association_tuple(
        config_data,
        {
            CONF_LOCK_ENTRY: "lock.other",
            CONF_ASSOCIATION_MODE: "explicit_lockless",
            CONF_VERIFIED_KEYMASTER_ENTRY_ID: "km-b",
            CONF_ASSOCIATION_GENERATION: 99,
            CONF_ASSOCIATION_TRANSACTION_ID: "b" * 32,
        },
    )

    assert projected == config_data


def test_prior_tuple_chain_replays_only_exact_recorded_states() -> None:
    """Replay the latest durable tuple from an exact old-chain member only."""
    old = _legacy()
    first = AssociationChange("rc-1", old, _verified(_TX1))
    second = AssociationChange(
        "rc-1",
        first.new,
        _verified(_TX2, generation=2),
    )
    chain = (first, second)

    assert replay_association_chain(old, chain) == (
        AssociationReplayState.REPLAY_PRIOR,
        second.new,
    )
    assert replay_association_chain(first.new, chain) == (
        AssociationReplayState.REPLAY_PRIOR,
        second.new,
    )
    assert replay_association_chain(second.new, chain) == (
        AssociationReplayState.MATCHES_LATEST,
        second.new,
    )
    assert replay_association_chain(_verified(_TX3, generation=3), chain) == (
        AssociationReplayState.UNKNOWN,
        None,
    )
    assert replay_association_chain(None, chain) == (
        AssociationReplayState.UNKNOWN,
        None,
    )


def test_transaction_preserves_predecessor_and_multiple_entries() -> None:
    """One protected transaction can atomically associate multiple RC entries."""
    first_change = AssociationChange(
        "rc-1", _legacy(), _verified(_TX2, raw="Front Door")
    )
    lockless = AssociationTuple(
        True,
        "(none)",
        None,
        1,
        _TX2,
        AssociationMode.EXPLICIT_LOCKLESS,
    )
    second_change = AssociationChange(
        "rc-2", _legacy(present=True, raw="(none)"), lockless
    )
    transaction = AssociationTransaction(
        _TX2,
        2,
        _TX1,
        (first_change, second_change),
    )

    parsed = AssociationTransaction.from_record(transaction.to_record())

    assert parsed == transaction
    assert parsed is not None
    assert parsed.predecessor_transaction_id == _TX1
    assert [change.rc_entry_id for change in parsed.changes] == ["rc-1", "rc-2"]


def test_legacy_transaction_defers_evidence_to_snapshot_validation() -> None:
    """Unproved references need a typed proof or a scoped attestation."""
    change = AssociationChange(
        "rc-1", _legacy(), _verified(_TX1), ("owner-ref", "hold-ref")
    )
    try:
        AssociationTransaction(_TX1, 1, None, (change,))
    except ValueError as err:
        assert str(err) == "legacy references lack complete provenance"
    else:
        raise AssertionError("unproven legacy references were accepted")

    proof_change = replace(
        change,
        independent_original_keymaster_entry_id="km-A",
        independently_proven_references=change.affected_references,
    )
    proof_transaction = AssociationTransaction(
        _TX1,
        1,
        None,
        (proof_change,),
    )
    assert AssociationTransaction.from_record(proof_transaction.to_record()) == (
        proof_transaction
    )

    attestation = AssociationAttestation(
        _ATTESTATION_ID,
        "rc-1",
        "km-recreated",
        ("owner-ref", "hold-ref"),
        _TX1,
    )
    recreated_change = replace(
        change,
        new=_verified(_TX1, entry_id="km-recreated"),
    )
    transaction = AssociationTransaction(
        _TX1,
        1,
        None,
        (recreated_change,),
        (attestation,),
    )
    assert transaction.to_record()["attestations"] == [attestation.to_record()]


def test_legacy_migration_requires_attestation_or_exact_original_proof() -> None:
    """The protected transition accepts only complete exact prior evidence."""
    prior = _durable_v2_envelope()["data"]
    owner = {
        "entry_id": "rc-1",
        "identity_key": "owner-identity",
        "origin": "adopted",
        "lockname": "lock.front_door",
        "slot": 10,
        "lock_observed": True,
        "first_seen": "2026-11-21T00:00:00+00:00",
        "last_seen": "2026-11-21T00:00:00+00:00",
    }
    record = {
        "encoded_code": encode_code("4729", "encoding-salt"),
        "encoding_salt_source": "entry_id",
        "encoding_salt_value": "encoding-salt",
        "code_length": 4,
        "created_at": "2026-11-21T00:00:00+00:00",
        "updated_at": "2026-11-21T00:00:00+00:00",
        "owners": [owner],
    }
    prior["ledger"]["records"] = [record]
    owner_reference = ledger_owner_reference(prior, record, owner)
    assert owner_reference is not None
    prior["pending_fences"] = [
        {
            "fence_id": "f" * 32,
            "original_km_entry_id": "km-A",
            "slot": 10,
            "rc_entry_id": "rc-1",
            "owner_ref": owner_reference,
            "allocation_ref": "allocation-reference",
            "created_transaction_id": _PREVIOUS_TX,
            "created_generation": 1,
            "fence_type": "allocation",
        }
    ]
    assert association_references_for_snapshot(prior, "rc-1") is not None
    change = AssociationChange(
        "rc-1",
        _legacy(),
        _verified(_TX1),
        association_references_for_snapshot(prior, "rc-1") or (),
        "km-A",
        association_references_for_snapshot(prior, "rc-1") or (),
    )
    transaction = AssociationTransaction(
        _TX1,
        2,
        _PREVIOUS_TX,
        (change,),
    )
    target = copy.deepcopy(prior)
    target["transaction_id"] = _TX1
    target["generation"] = 2
    target["associations"] = [association_storage_record("rc-1", change.new)]
    target["pending_config"] = [transaction.to_record()]

    assert validate_snapshot_transition(prior, target)
    target_envelope = _durable_v2_envelope()
    target_envelope["data"] = target
    assert validate_v2_envelope_shape(target_envelope)

    released = copy.deepcopy(target)
    released["transaction_id"] = "3" * 32
    released["generation"] = 3
    released["ledger"]["records"] = []
    released["pending_fences"] = []
    released["tombstones"] = [
        resolve_fence_to_tombstone(
            prior["pending_fences"][0],
            tombstone_id="4" * 32,
            resolved_transaction_id="3" * 32,
            resolved_generation=3,
            resolution_kind="owner_released",
            retained_at="2026-11-21T00:01:00+00:00",
        )
    ]
    assert validate_snapshot_transition(target, released)
    released_envelope = _durable_v2_envelope()
    released_envelope["data"] = released
    assert validate_v2_envelope_shape(released_envelope)

    unknown_prior = copy.deepcopy(prior)
    unknown_prior["pending_fences"] = []
    unknown_change = replace(
        change,
        affected_references=association_references_for_snapshot(unknown_prior, "rc-1")
        or (),
        independently_proven_references=association_references_for_snapshot(
            unknown_prior, "rc-1"
        )
        or (),
    )
    unknown_transaction = AssociationTransaction(
        _TX1,
        2,
        _PREVIOUS_TX,
        (unknown_change,),
    )
    unknown_target = copy.deepcopy(unknown_prior)
    unknown_target["transaction_id"] = _TX1
    unknown_target["generation"] = 2
    unknown_target["associations"] = [
        association_storage_record("rc-1", unknown_change.new)
    ]
    unknown_target["pending_config"] = [unknown_transaction.to_record()]
    assert not validate_snapshot_transition(unknown_prior, unknown_target)


def test_shared_owner_attestations_cannot_contradict_each_other() -> None:
    """One shared legacy owner cannot be assigned to different physical locks."""
    first_change = AssociationChange(
        "rc-1",
        _legacy(),
        _verified(_TX1, entry_id="km-A"),
        ("shared-owner",),
    )
    second_change = AssociationChange(
        "rc-2",
        _legacy(),
        _verified(_TX1, entry_id="km-B"),
        ("shared-owner",),
    )
    attestations = (
        AssociationAttestation(
            _ATTESTATION_ID,
            "rc-1",
            "km-A",
            ("shared-owner",),
            _TX1,
        ),
        AssociationAttestation(
            "e" * 32,
            "rc-2",
            "km-B",
            ("shared-owner",),
            _TX1,
        ),
    )

    try:
        AssociationTransaction(
            _TX1,
            1,
            None,
            (first_change, second_change),
            attestations,
        )
    except ValueError as err:
        assert str(err) == "contradictory shared-owner attestations"
    else:
        raise AssertionError("contradictory shared-owner attestations were accepted")


def test_known_entry_reselection_requires_durable_original_entry_hold() -> None:
    """A may be deselected for new work while existing references stay with A."""
    data = _durable_v2_envelope()["data"]
    old = _verified(_PREVIOUS_TX)
    data["associations"] = [association_storage_record("rc-1", old)]
    change = AssociationChange(
        "rc-1",
        old,
        _verified(_TX1, generation=2, entry_id="km-B"),
        ("known-A-hold",),
    )
    hold = AssociationRebindHold(
        "f" * 32,
        "rc-1",
        "km-A",
        "km-B",
        ("known-A-hold",),
        _TX1,
        2,
    )
    transaction = AssociationTransaction(
        _TX1,
        2,
        _PREVIOUS_TX,
        (change,),
        retained_holds=(hold,),
    )

    result = add_association_transaction_to_envelope(
        {
            "version": 2,
            "minor_version": 1,
            "key": "rental_control.code_registry",
            "data": data,
        },
        transaction,
    )

    assert result is not None
    assert result["data"]["holds"] == [hold.to_record()]
    assert result["data"]["associations"][0]["tuple"] == change.new.to_record()


def test_known_entry_reselection_without_hold_is_rejected() -> None:
    """No transaction can remap an old A reference to B without a hold."""
    try:
        change = AssociationChange(
            "rc-1",
            _verified(_TX1),
            _verified(_TX2, generation=2, entry_id="km-B"),
            ("known-A-hold",),
        )
        AssociationTransaction(_TX2, 2, _TX1, (change,))
    except ValueError as err:
        assert str(err) == "verified work requires an original-entry hold"
    else:
        raise AssertionError("known-A work transferred to B without a hold")


def test_envelope_association_sections_are_cross_record_validated() -> None:
    """Latest association records must match their durable tuple transactions."""
    old = _legacy()
    change = AssociationChange("rc-1", old, _verified(_TX1))
    transaction = AssociationTransaction(_TX1, 1, None, (change,))
    association = association_storage_record("rc-1", change.new)

    assert valid_association_sections([association], [], [transaction.to_record()])
    wrong_latest = association_storage_record("rc-1", _verified(_TX2, generation=2))
    assert not valid_association_sections([wrong_latest], [], [transaction.to_record()])
    assert not valid_association_sections(
        [{"rc_entry_id": "rc-1", "tuple": {}}], [], []
    )

    target_transaction = AssociationTransaction(
        _TX1,
        2,
        _PREVIOUS_TX,
        (change,),
    )
    envelope = _durable_v2_envelope()
    envelope["data"]["transaction_id"] = _TX1
    envelope["data"]["generation"] = 2
    envelope["data"]["associations"] = [association]
    envelope["data"]["pending_config"] = [target_transaction.to_record()]
    assert validate_v2_envelope_shape(envelope)

    envelope["data"]["associations"] = [wrong_latest]
    assert not validate_v2_envelope_shape(envelope)


class _FakeProtectedStore:
    """Deterministic acknowledgement boundary for association transactions."""

    def __init__(
        self,
        state: ProtectionLoadState = ProtectionLoadState.VALID_V2,
        *,
        on_commit=None,
        transaction_id: str | None = None,
        generation: int | None = None,
    ) -> None:
        """Configure success, ambiguous, or pre-commit outcomes."""
        self.state = state
        self.on_commit = on_commit
        self.transaction_id = transaction_id
        self.generation = generation
        self.envelopes: list[dict[str, Any]] = []

    async def async_commit(self, envelope: dict[str, Any]) -> ProtectionLoadResult:
        """Capture a target envelope and return a sanitized outcome."""
        self.envelopes.append(envelope)
        if self.on_commit is not None:
            self.on_commit()
        data = envelope["data"]
        return ProtectionLoadResult(
            self.state,
            transaction_id=self.transaction_id or data["transaction_id"],
            generation=self.generation or data["generation"],
        )


def _rc_entry(
    hass,
    entry_id: str = "rc-1",
    *,
    selection: str | None = "Front Door",
    options: dict[str, Any] | None = None,
) -> MockConfigEntry:
    """Create one RC entry with exact legacy selection and unrelated data."""
    data: dict[str, Any] = {"unrelated": "original"}
    if selection is not None:
        data[CONF_LOCK_ENTRY] = selection
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=entry_id,
        entry_id=entry_id,
        data=data,
        options=options or {},
    )
    entry.add_to_hass(hass)
    return entry


def _transaction(
    entry_id: str = "rc-1",
    *,
    txid: str = _TX1,
    generation: int = 5,
    predecessor: str | None = _PREVIOUS_TX,
    old: AssociationTuple | None = None,
    new_entry_id: str = "km-A",
    raw: str | None = "Front Door",
    affected_references: tuple[str, ...] = (),
) -> AssociationTransaction:
    """Build one verified association transaction."""
    old_tuple = old or _legacy(raw=raw)
    new_tuple = AssociationTuple(
        True,
        raw,
        new_entry_id,
        old_tuple.association_generation + 1,
        txid,
        AssociationMode.VERIFIED,
    )
    change = AssociationChange(
        entry_id,
        old_tuple,
        new_tuple,
        affected_references,
    )
    attestations: tuple[AssociationAttestation, ...] = ()
    if affected_references and old_tuple.mode is AssociationMode.LEGACY_UNRESOLVED:
        attestations = (
            AssociationAttestation(
                _ATTESTATION_ID,
                entry_id,
                new_entry_id,
                affected_references,
                txid,
            ),
        )
    return AssociationTransaction(
        txid,
        generation,
        predecessor,
        (change,),
        attestations,
    )


def _base_envelope() -> dict[str, Any]:
    """Return a minimal preexisting v2 envelope projection."""
    return {
        "version": 2,
        "minor_version": 1,
        "key": "rental_control.code_registry",
        "data": {
            "transaction_id": _PREVIOUS_TX,
            "generation": 4,
            "associations": [],
            "attestations": [],
            "holds": [],
            "pending_config": [],
        },
    }


def _durable_v2_envelope() -> dict[str, Any]:
    """Build the complete v2 payload required by the raw store validator."""
    envelope = _base_envelope()
    envelope["data"].update(
        {
            "schema_version": 1,
            "ledger": {
                "schema_version": 1,
                "updated_at": "2026-11-21T00:00:00+00:00",
                "code_ref_salt": "test-salt",
                "records": [],
                "announced_conflicts": {},
            },
            "pending_fences": [],
            "holds": [],
            "tombstones": [],
        }
    )
    envelope["data"]["transaction_id"] = _PREVIOUS_TX
    envelope["data"]["generation"] = 1
    return envelope


@pytest.mark.parametrize(
    "state",
    [
        ProtectionLoadState.BUSY,
        ProtectionLoadState.UNAVAILABLE,
        ProtectionLoadState.PROTECTIVE_UNKNOWN,
    ],
)
async def test_unacknowledged_storage_never_updates_rc_config(hass, state) -> None:
    """Marker/envelope/finalization uncertainty cannot project a selection."""
    entry = _rc_entry(hass)
    store = _FakeProtectedStore(state)

    outcome = await async_commit_association_transaction(
        hass,
        store,
        _base_envelope(),
        _transaction(),
    )

    assert outcome is AssociationApplyState.STORAGE_NOT_ACKNOWLEDGED
    assert AssociationTuple.from_config(entry.data) == _legacy()
    assert len(store.envelopes) == 1


async def test_protected_store_commits_association_only_from_ready_v2(
    hass,
    tmp_path: Path,
) -> None:
    """The actual raw store gates and acknowledges association transactions."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    initial = _durable_v2_envelope()
    committed = await async_commit_protection_transaction(
        asyncio.Lock(),
        storage,
        initial,
    )
    assert committed.state is ProtectionLoadState.VALID_V2

    store = ProtectedRegistryStore(hass, asyncio.Lock(), storage_dir=storage)
    denied = await store.async_commit_association(
        _transaction(generation=2, predecessor=_PREVIOUS_TX)
    )
    assert denied.state is ProtectionLoadState.UNAVAILABLE

    loaded = await store.async_load()
    assert loaded.state is ProtectionLoadState.VALID_V2
    association = _transaction(generation=2, predecessor=_PREVIOUS_TX)
    result = await store.async_commit_association(association)

    assert result.state is ProtectionLoadState.VALID_V2
    assert result.transaction_id == association.transaction_id
    assert result.generation == association.generation
    assert store.association_for_entry("rc-1") == association.changes[0].new


async def test_protected_rebind_appends_hold_without_moving_original_owner(
    hass,
    tmp_path: Path,
) -> None:
    """A durable A-to-B selection keeps prior ledger claims attached to A."""
    storage = tmp_path / ".storage"
    storage.mkdir()
    initial = _durable_v2_envelope()
    old_association = _verified(_PREVIOUS_TX)
    initial["data"]["associations"] = [
        association_storage_record("rc-1", old_association)
    ]
    initial["data"]["ledger"]["records"] = [
        {
            "encoded_code": encode_code("4729", "rc-1"),
            "encoding_salt_source": "entry_id",
            "encoding_salt_value": "rc-1",
            "code_length": 4,
            "created_at": "2026-11-21T00:00:00+00:00",
            "updated_at": "2026-11-21T00:00:00+00:00",
            "owners": [
                {
                    "entry_id": "rc-1",
                    "identity_key": "existing-booking",
                    "origin": "adopted",
                    "lockname": "lock.front_door",
                    "slot": 10,
                    "lock_observed": True,
                    "first_seen": "2026-11-21T00:00:00+00:00",
                    "last_seen": "2026-11-21T00:00:00+00:00",
                }
            ],
        }
    ]
    initialized = await async_commit_protection_transaction(
        asyncio.Lock(),
        storage,
        initial,
    )
    assert initialized.state is ProtectionLoadState.VALID_V2

    store = ProtectedRegistryStore(hass, asyncio.Lock(), storage_dir=storage)
    loaded = await store.async_load()
    assert loaded.state is ProtectionLoadState.VALID_V2
    references = store.association_references_for_entry("rc-1")
    assert references
    selected = _verified(_TX1, generation=2, entry_id="km-B", raw="Back Door")
    change = AssociationChange(
        "rc-1",
        old_association,
        selected,
        references,
    )
    hold = AssociationRebindHold(
        "f" * 32,
        "rc-1",
        "km-A",
        "km-B",
        references,
        _TX1,
        2,
    )
    transaction = AssociationTransaction(
        _TX1,
        2,
        _PREVIOUS_TX,
        (change,),
        retained_holds=(hold,),
    )

    committed = await store.async_commit_association(transaction)

    assert committed.state is ProtectionLoadState.VALID_V2
    assert store.association_for_entry("rc-1") == selected
    envelope = json.loads((storage / "rental_control.code_registry").read_bytes())
    assert envelope["data"]["holds"] == [hold.to_record()]
    assert envelope["data"]["ledger"]["records"][0]["owners"][0]["entry_id"] == ("rc-1")
    assert envelope["data"]["ledger"]["records"][0]["owners"][0]["identity_key"] == (
        "existing-booking"
    )


async def test_protected_owner_references_are_opaque_and_scoped(hass, tmp_path) -> None:
    """Association audit references do not reveal PINs or other RC owners."""
    data = _durable_v2_envelope()["data"]
    data["ledger"]["records"] = [
        {
            "encoded_code": encode_code("4729", "rc-1"),
            "encoding_salt_source": "entry_id",
            "encoding_salt_value": "rc-1",
            "code_length": 4,
            "created_at": "2026-11-21T00:00:00+00:00",
            "updated_at": "2026-11-21T00:00:00+00:00",
            "owners": [
                {
                    "entry_id": "rc-1",
                    "identity_key": "sensitive-booking-reference",
                    "origin": "adopted",
                    "lockname": "lock.front_door",
                    "slot": 10,
                    "lock_observed": True,
                    "first_seen": "2026-11-21T00:00:00+00:00",
                    "last_seen": "2026-11-21T00:00:00+00:00",
                },
                {
                    "entry_id": "rc-2",
                    "identity_key": "other-owner",
                    "origin": "adopted",
                    "lockname": "lock.back_door",
                    "slot": 10,
                    "lock_observed": True,
                    "first_seen": "2026-11-21T00:00:00+00:00",
                    "last_seen": "2026-11-21T00:00:00+00:00",
                },
            ],
        }
    ]
    store = ProtectedRegistryStore(hass, asyncio.Lock(), storage_dir=tmp_path)
    store._loaded_result = ProtectionLoadResult(
        ProtectionLoadState.VALID_V2,
        transaction_id=_PREVIOUS_TX,
        generation=1,
        data=data,
    )
    store._authorization_state = ProtectionAuthorizationState.READY

    references = store.association_references_for_entry("rc-1")

    assert references is not None and len(references) == 1
    assert "4729" not in repr(references)
    assert "sensitive-booking-reference" not in repr(references)
    assert store.association_references_for_entry("missing-entry") == ()


async def test_known_original_fence_has_opaque_scoped_reference(hass, tmp_path) -> None:
    """A known original lock remains attributable without exposing its record."""
    data = _durable_v2_envelope()["data"]
    data["pending_fences"] = [
        {
            "fence_id": "f" * 32,
            "original_km_entry_id": "known-km-A",
            "slot": 10,
            "rc_entry_id": "rc-1",
            "owner_ref": "owner-ref",
            "allocation_ref": "allocation-ref",
            "created_transaction_id": _PREVIOUS_TX,
            "created_generation": 1,
            "fence_type": "allocation",
        }
    ]
    store = ProtectedRegistryStore(hass, asyncio.Lock(), storage_dir=tmp_path)
    store._loaded_result = ProtectionLoadResult(
        ProtectionLoadState.VALID_V2,
        transaction_id=_PREVIOUS_TX,
        generation=1,
        data=data,
    )
    store._authorization_state = ProtectionAuthorizationState.READY

    references = store.association_references_for_entry("rc-1")
    assert references is not None
    assert len(references) == 1
    assert "known-km-A" not in repr(references)


def test_safety_references_require_persisted_salt_and_domain_separation() -> None:
    """Opaque references vary by persisted salt and never contain door codes."""
    data: dict[str, Any] = {
        "ledger": {
            "code_ref_salt": "persisted-reference-salt",
            "records": [
                {
                    "encoded_code": encode_code("4729", "encoding-salt"),
                    "encoding_salt_value": "encoding-salt",
                    "owners": [
                        {
                            "entry_id": "rc-1",
                            "identity_key": "owner-identity",
                            "lockname": "lock.front_door",
                            "slot": 10,
                        }
                    ],
                }
            ],
        },
        "pending_fences": [{"fence_id": "f" * 32, "rc_entry_id": "rc-1", "slot": 10}],
        "holds": [],
        "tombstones": [],
    }

    references = association_references_for_snapshot(data, "rc-1")
    assert references is not None and len(references) == 2
    assert "4729" not in repr(references)

    changed_salt = copy.deepcopy(data)
    changed_salt["ledger"]["code_ref_salt"] = "other-persisted-salt"
    assert association_references_for_snapshot(changed_salt, "rc-1") != references

    changed_domain = copy.deepcopy(data)
    changed_domain["pending_fences"] = []
    changed_domain["tombstones"] = [
        {"fence_id": "f" * 32, "rc_entry_id": "rc-1", "slot": 10}
    ]
    assert association_references_for_snapshot(changed_domain, "rc-1") != references

    missing_salt = copy.deepcopy(data)
    del missing_salt["ledger"]["code_ref_salt"]
    assert association_references_for_snapshot(missing_salt, "rc-1") is None


async def test_config_projection_follows_ack_and_preserves_concurrent_options(
    hass,
) -> None:
    """Only acknowledged envelopes update data; fresh unrelated values survive."""
    entry = _rc_entry(hass, options={"unrelated_option": "before"})
    tx = _transaction()

    def concurrent_update() -> None:
        """Simulate a third-party update while protected I/O is pending."""
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, "unrelated": "after"},
            options={"unrelated_option": "after"},
        )

    store = _FakeProtectedStore(on_commit=concurrent_update)
    outcome = await async_commit_association_transaction(
        hass,
        store,
        _base_envelope(),
        tx,
    )

    assert outcome is AssociationApplyState.CONFIG_WRITE_SCHEDULED
    assert entry.data["unrelated"] == "after"
    assert entry.options["unrelated_option"] == "after"
    assert AssociationTuple.from_config(entry.data) == tx.changes[0].new
    assert store.envelopes[0]["data"]["pending_config"] == [tx.to_record()]


async def test_entry_disappearing_or_association_change_after_commit_blocks_projection(
    hass,
) -> None:
    """All entries are rechecked after commit, before any multi-entry update."""
    first = _rc_entry(hass, "rc-1")
    second = _rc_entry(hass, "rc-2")
    first_change = _transaction("rc-1", txid=_TX2).changes[0]
    second_change = _transaction("rc-2", txid=_TX2).changes[0]
    tx = AssociationTransaction(
        _TX2,
        5,
        _PREVIOUS_TX,
        (first_change, second_change),
    )

    def concurrent_association_change() -> None:
        """Change one association tuple while the protected commit is pending."""
        hass.config_entries.async_update_entry(
            second,
            data={**second.data, CONF_LOCK_ENTRY: "Other Lock"},
        )

    store = _FakeProtectedStore(on_commit=concurrent_association_change)
    outcome = await async_commit_association_transaction(
        hass,
        store,
        _base_envelope(),
        tx,
    )

    assert outcome is AssociationApplyState.CONFIG_CHANGED
    assert AssociationTuple.from_config(first.data) == first_change.old
    assert AssociationTuple.from_config(second.data) != second_change.old


async def test_config_update_failure_retains_durable_replay_chain(hass) -> None:
    """A config update exception leaves the acknowledged tuple chain available."""
    entry = _rc_entry(hass)
    tx = _transaction()

    def fail_update(_entry: ConfigEntry, **_kwargs: Any) -> None:
        """Model a synchronous HA config update failure."""
        raise OSError("sensitive path and door PIN")

    store = _FakeProtectedStore()
    outcome = await async_commit_association_transaction(
        hass,
        store,
        _base_envelope(),
        tx,
        entry_updater=fail_update,
    )

    assert outcome is AssociationApplyState.CONFIG_REPLAY_REQUIRED
    assert AssociationTuple.from_config(entry.data) == tx.changes[0].old
    assert store.envelopes[0]["data"]["pending_config"] == [tx.to_record()]


async def test_restart_replays_only_recorded_prior_tuple_and_keeps_unrelated_options(
    hass,
) -> None:
    """Exact prior replay is idempotent and preserves post-crash options."""
    entry = _rc_entry(hass, options={"new_option": True})
    tx = _transaction()

    outcome = replay_pending_association_transactions(hass, (tx,))

    assert outcome is AssociationApplyState.CONFIG_WRITE_SCHEDULED
    assert AssociationTuple.from_config(entry.data) == tx.changes[0].new
    assert entry.options == {"new_option": True}
    assert (
        replay_pending_association_transactions(hass, (tx,))
        is AssociationApplyState.CONFIG_ALREADY_CURRENT
    )

    ahead = replace(tx.changes[0].new, association_generation=9)
    hass.config_entries.async_update_entry(
        entry,
        data=ahead.merge_into_config(entry.data),
    )
    assert (
        replay_pending_association_transactions(hass, (tx,))
        is AssociationApplyState.CONFIG_CHANGED
    )
