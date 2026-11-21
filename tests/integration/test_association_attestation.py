# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Exercise explicit Keymaster association and legacy attestation choices."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from homeassistant.config_entries import ConfigEntryDisabler
from pytest_homeassistant.common import MockConfigEntry

from custom_components.rental_control.allocator.association_references import (
    association_references_for_snapshot,
)
from custom_components.rental_control.allocator.association_references import (
    ledger_owner_reference,
)
from custom_components.rental_control.allocator.association_transactions import (
    association_storage_record,
)
from custom_components.rental_control.allocator.associations import AssociationMode
from custom_components.rental_control.allocator.associations import AssociationTuple
from custom_components.rental_control.allocator.code_codec import encode_code
from custom_components.rental_control.allocator.models import (
    ProtectionAuthorizationState,
)
from custom_components.rental_control.allocator.models import ProtectionLoadResult
from custom_components.rental_control.allocator.models import ProtectionLoadState
from custom_components.rental_control.config_flow_helpers.association import (
    apply_association_selection,
)
from custom_components.rental_control.const import ASSOCIATION_CHOICE_LOCKLESS
from custom_components.rental_control.const import CONF_ASSOCIATION_ATTEST_SAME_LOCK
from custom_components.rental_control.const import CONF_ASSOCIATION_ENTRY_CHOICE
from custom_components.rental_control.const import CONF_ASSOCIATION_GENERATION
from custom_components.rental_control.const import CONF_ASSOCIATION_MODE
from custom_components.rental_control.const import CONF_ASSOCIATION_TRANSACTION_ID
from custom_components.rental_control.const import CONF_LOCK_ENTRY
from custom_components.rental_control.const import CONF_VERIFIED_KEYMASTER_ENTRY_ID
from custom_components.rental_control.const import DOMAIN
from custom_components.rental_control.const import LOCK_MANAGER
from custom_components.rental_control.const import PROTECTED_REGISTRY_STORE

_TX = "a" * 32


class _ProtectedStore:
    """Capture config-flow commits at the protected acknowledgement boundary."""

    def __init__(
        self,
        references: tuple[str, ...] = ("owner-reference",),
    ) -> None:
        """Create a valid synthetic current v2 snapshot."""
        self.authorization_state = ProtectionAuthorizationState.READY
        self.references = references
        self.envelopes: list[dict[str, Any]] = []
        self.loaded_result = ProtectionLoadResult(
            ProtectionLoadState.VALID_V2,
            transaction_id=_TX,
            generation=1,
            data=_v2_data(),
        )

    def association_references_for_entry(self, _entry_id: str) -> tuple[str, ...]:
        """Return synthetic opaque references, never reservation data."""
        return self.references

    async def async_commit(self, envelope: dict[str, Any]) -> ProtectionLoadResult:
        """Acknowledge the exact target envelope and capture its audit record."""
        self.envelopes.append(envelope)
        data = envelope["data"]
        result = ProtectionLoadResult(
            ProtectionLoadState.VALID_V2,
            transaction_id=data["transaction_id"],
            generation=data["generation"],
            data=data,
        )
        self.loaded_result = result
        return result


def _v2_data() -> dict[str, Any]:
    """Build the complete empty v2 safety envelope used by flow tests."""
    return {
        "schema_version": 1,
        "transaction_id": _TX,
        "generation": 1,
        "ledger": {
            "schema_version": 1,
            "updated_at": "2026-11-21T00:00:00+00:00",
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
    }


def _entry(hass, *, mode: AssociationMode = AssociationMode.LEGACY_UNRESOLVED):
    """Add a Rental Control entry with a legacy Keymaster selector."""
    data: dict[str, Any] = {CONF_LOCK_ENTRY: "lock.front_door"}
    if mode is AssociationMode.VERIFIED:
        data.update(
            {
                CONF_VERIFIED_KEYMASTER_ENTRY_ID: "km-a",
                CONF_ASSOCIATION_GENERATION: 1,
                CONF_ASSOCIATION_TRANSACTION_ID: "b" * 32,
                CONF_ASSOCIATION_MODE: mode.value,
            }
        )
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Rental",
        data=data,
        entry_id="rc-entry",
    )
    entry.add_to_hass(hass)
    return entry


def _candidate(hass, entry_id: str = "km-a", *, disabled: bool = False) -> None:
    """Add a real Keymaster-domain config entry candidate."""
    MockConfigEntry(
        domain=LOCK_MANAGER,
        title="Front Door",
        data={"lockname": "lock.front_door"},
        entry_id=entry_id,
        disabled_by=ConfigEntryDisabler.USER if disabled else None,
    ).add_to_hass(hass)


def _flow(hass, entry):
    """Provide the minimal flow surface consumed by the handler."""
    store = _ProtectedStore()
    association = AssociationTuple.from_config(entry.data)
    if association is not None and association.mode is AssociationMode.VERIFIED:
        store.loaded_result.data["associations"] = [
            association_storage_record(entry.entry_id, association)
        ]
    hass.data.setdefault(DOMAIN, {})[PROTECTED_REGISTRY_STORE] = store
    return SimpleNamespace(hass=hass, config_entry=entry), store


async def test_selection_with_legacy_work_requires_separate_attestation(hass) -> None:
    """A selected entry alone cannot claim existing unresolved allocations."""
    entry = _entry(hass)
    _candidate(hass)
    flow, store = _flow(hass, entry)

    result = await apply_association_selection(
        flow,
        {
            CONF_ASSOCIATION_ENTRY_CHOICE: "km-a",
            CONF_ASSOCIATION_ATTEST_SAME_LOCK: False,
            CONF_LOCK_ENTRY: "Front Door",
        },
    )

    assert result.errors == {CONF_ASSOCIATION_ATTEST_SAME_LOCK: "attestation_required"}
    assert not store.envelopes
    association = AssociationTuple.from_config(entry.data)
    assert association is not None
    assert association.mode is AssociationMode.LEGACY_UNRESOLVED


async def test_known_original_safety_evidence_cannot_be_legacy_attested_to_b(
    hass,
) -> None:
    """A scoped known-A fence is not reclassified as unresolved work for B."""
    entry = _entry(hass)
    _candidate(hass, "km-b")
    flow, store = _flow(hass, entry)
    store.loaded_result.data["pending_fences"] = [
        {
            "fence_id": "f" * 32,
            "original_km_entry_id": "km-a",
            "slot": 10,
            "rc_entry_id": entry.entry_id,
            "owner_ref": "owner-reference",
            "allocation_ref": "allocation-reference",
            "created_transaction_id": _TX,
            "created_generation": 1,
            "fence_type": "allocation",
        }
    ]

    result = await apply_association_selection(
        flow,
        {
            CONF_ASSOCIATION_ENTRY_CHOICE: "km-b",
            CONF_ASSOCIATION_ATTEST_SAME_LOCK: True,
            CONF_LOCK_ENTRY: "Front Door",
        },
    )

    assert result.errors == {CONF_ASSOCIATION_ENTRY_CHOICE: "association_conflict"}
    assert not store.envelopes


async def test_independent_original_entry_evidence_needs_no_attestation(hass) -> None:
    """A prior fence proves the same original entry without operator assertion."""
    entry = _entry(hass)
    _candidate(hass, "km-a")
    flow, store = _flow(hass, entry)
    owner = {
        "entry_id": entry.entry_id,
        "identity_key": "owner-identity",
        "lockname": "lock.front_door",
        "slot": 10,
    }
    ledger_record = {
        "encoded_code": encode_code("4729", "encoding-salt"),
        "encoding_salt_value": "encoding-salt",
        "owners": [owner],
    }
    store.loaded_result.data["ledger"]["records"] = [ledger_record]
    owner_reference = ledger_owner_reference(
        store.loaded_result.data, ledger_record, owner
    )
    assert owner_reference is not None
    store.loaded_result.data["pending_fences"] = [
        {
            "fence_id": "e" * 32,
            "original_km_entry_id": "km-a",
            "slot": 10,
            "rc_entry_id": entry.entry_id,
            "owner_ref": owner_reference,
            "allocation_ref": "allocation-reference",
            "created_transaction_id": _TX,
            "created_generation": 1,
            "fence_type": "allocation",
        }
    ]
    references = association_references_for_snapshot(
        store.loaded_result.data, entry.entry_id
    )
    assert references is not None
    store.references = references

    result = await apply_association_selection(
        flow,
        {
            CONF_ASSOCIATION_ENTRY_CHOICE: "km-a",
            CONF_ASSOCIATION_ATTEST_SAME_LOCK: False,
            CONF_LOCK_ENTRY: "Front Door",
        },
    )

    assert not result.errors
    assert store.envelopes
    data = store.envelopes[0]["data"]
    assert data["attestations"] == []
    change = data["pending_config"][-1]["changes"][0]
    assert change["independent_original_keymaster_entry_id"] == "km-a"
    assert change["independently_proven_references"] == list(references)


async def test_explicit_same_lock_attestation_is_committed_before_projection(
    hass,
) -> None:
    """A recreated entry needs an explicit same-lock claim and durable record."""
    entry = _entry(hass)
    _candidate(hass, "km-recreated")
    flow, store = _flow(hass, entry)

    result = await apply_association_selection(
        flow,
        {
            CONF_ASSOCIATION_ENTRY_CHOICE: "km-recreated",
            CONF_ASSOCIATION_ATTEST_SAME_LOCK: True,
            CONF_LOCK_ENTRY: "Front Door",
            "unrelated_option": "preserved",
        },
    )

    assert not result.errors
    assert result.user_input == {
        CONF_LOCK_ENTRY: "Front Door",
        "unrelated_option": "preserved",
    }
    association = AssociationTuple.from_config(entry.data)
    assert association is not None
    assert association.mode is AssociationMode.VERIFIED
    assert association.verified_keymaster_entry_id == "km-recreated"
    assert association.legacy_lock_selection_raw == "lock.front_door"
    assert (
        store.envelopes[0]["data"]["attestations"][0]["selected_keymaster_entry_id"]
        == "km-recreated"
    )
    assert store.envelopes[0]["data"]["pending_config"][0]["changes"][0][
        "affected_references"
    ] == ["owner-reference"]


async def test_selection_without_references_needs_no_attestation(hass) -> None:
    """An empty protected owner set does not require a historical assertion."""
    entry = _entry(hass)
    _candidate(hass)
    flow, store = _flow(hass, entry)
    store.references = ()

    result = await apply_association_selection(
        flow,
        {
            CONF_ASSOCIATION_ENTRY_CHOICE: "km-a",
            CONF_ASSOCIATION_ATTEST_SAME_LOCK: False,
            CONF_LOCK_ENTRY: "Front Door",
        },
    )

    assert not result.errors
    association = AssociationTuple.from_config(entry.data)
    assert association is not None
    assert association.mode is AssociationMode.VERIFIED
    assert store.envelopes[0]["data"]["attestations"] == []


async def test_lockless_selection_remains_distinct_and_never_attests(hass) -> None:
    """Explicit locklessness is a committed mode, not legacy '(none)'."""
    entry = _entry(hass)
    flow, store = _flow(hass, entry)

    result = await apply_association_selection(
        flow,
        {
            CONF_ASSOCIATION_ENTRY_CHOICE: ASSOCIATION_CHOICE_LOCKLESS,
            CONF_ASSOCIATION_ATTEST_SAME_LOCK: False,
            CONF_LOCK_ENTRY: "Front Door",
        },
    )

    assert not result.errors
    association = AssociationTuple.from_config(entry.data)
    assert association is not None
    assert association.mode is AssociationMode.EXPLICIT_LOCKLESS
    assert association.verified_keymaster_entry_id is None
    assert store.envelopes[0]["data"]["attestations"] == []


async def test_known_association_reselects_with_old_work_held_to_original(hass) -> None:
    """Selecting B changes future association but keeps existing work with A."""
    entry = _entry(hass, mode=AssociationMode.VERIFIED)
    _candidate(hass, "km-a")
    _candidate(hass, "km-b")
    flow, store = _flow(hass, entry)

    result = await apply_association_selection(
        flow,
        {
            CONF_ASSOCIATION_ENTRY_CHOICE: "km-b",
            CONF_ASSOCIATION_ATTEST_SAME_LOCK: False,
            CONF_LOCK_ENTRY: "Front Door",
        },
    )

    assert not result.errors
    assert entry.data[CONF_VERIFIED_KEYMASTER_ENTRY_ID] == "km-b"
    holds = store.envelopes[0]["data"]["holds"]
    assert len(holds) == 1
    assert holds[0]["original_km_entry_id"] == "km-a"
    assert holds[0]["selected_keymaster_entry_id"] == "km-b"
    assert holds[0]["claim_references"] == ["owner-reference"]
    assert store.envelopes[0]["data"]["attestations"] == []


async def test_failed_config_projection_replays_idempotently_on_retry(
    hass, monkeypatch
) -> None:
    """Acked state remains pending and retry projects it without a second commit."""
    entry = _entry(hass)
    _candidate(hass)
    flow, store = _flow(hass, entry)
    user_input = {
        CONF_ASSOCIATION_ENTRY_CHOICE: "km-a",
        CONF_ASSOCIATION_ATTEST_SAME_LOCK: True,
        CONF_LOCK_ENTRY: "Front Door",
    }
    original_updater = hass.config_entries.async_update_entry
    fail_updates = True

    def update_entry(target, **kwargs):
        """Fail once so the transaction replay can prove idempotent."""
        nonlocal fail_updates
        if fail_updates:
            fail_updates = False
            raise RuntimeError("sensitive config projection failure")
        return original_updater(target, **kwargs)

    monkeypatch.setattr(hass.config_entries, "async_update_entry", update_entry)
    first = await apply_association_selection(flow, user_input)
    assert first.errors == {
        CONF_ASSOCIATION_ENTRY_CHOICE: "association_replay_required"
    }
    assert len(store.envelopes) == 1
    association = AssociationTuple.from_config(entry.data)
    assert association is not None
    assert association.mode is AssociationMode.LEGACY_UNRESOLVED

    second = await apply_association_selection(flow, user_input)
    assert second.errors == {
        CONF_ASSOCIATION_ENTRY_CHOICE: "association_replay_required"
    }
    assert len(store.envelopes) == 1
    association = AssociationTuple.from_config(entry.data)
    assert association is not None
    assert association.mode is AssociationMode.VERIFIED

    third = await apply_association_selection(
        flow,
        {
            **user_input,
            CONF_ASSOCIATION_ATTEST_SAME_LOCK: False,
        },
    )
    assert not third.errors
    assert len(store.envelopes) == 1


async def test_missing_and_disabled_candidates_are_rejected(hass) -> None:
    """Candidate values are revalidated against live Keymaster entries."""
    entry = _entry(hass)
    _candidate(hass, "km-disabled", disabled=True)
    flow, store = _flow(hass, entry)

    for candidate in ("missing", "km-disabled"):
        result = await apply_association_selection(
            flow,
            {
                CONF_ASSOCIATION_ENTRY_CHOICE: candidate,
                CONF_ASSOCIATION_ATTEST_SAME_LOCK: False,
                CONF_LOCK_ENTRY: "Front Door",
            },
        )
        assert result.errors[CONF_ASSOCIATION_ENTRY_CHOICE] == "invalid_candidate"

    assert not store.envelopes


async def test_no_change_keeps_legacy_unresolved_and_does_not_commit(hass) -> None:
    """A blank choice is not an implicit migration or lockless assertion."""
    entry = _entry(hass)
    _candidate(hass)
    flow, store = _flow(hass, entry)

    result = await apply_association_selection(
        flow,
        {
            CONF_ASSOCIATION_ENTRY_CHOICE: "",
            CONF_ASSOCIATION_ATTEST_SAME_LOCK: False,
            CONF_LOCK_ENTRY: "Front Door",
        },
    )

    assert not result.errors
    association = AssociationTuple.from_config(entry.data)
    assert association is not None
    assert association.mode is AssociationMode.LEGACY_UNRESOLVED
    assert not store.envelopes


async def test_existing_disabled_association_does_not_block_unrelated_options(
    hass,
) -> None:
    """An unchanged disabled entry is not mistaken for a new selection."""
    entry = _entry(hass, mode=AssociationMode.VERIFIED)
    _candidate(hass, "km-a", disabled=True)
    flow, store = _flow(hass, entry)

    result = await apply_association_selection(
        flow,
        {
            CONF_ASSOCIATION_ENTRY_CHOICE: "km-a",
            CONF_ASSOCIATION_ATTEST_SAME_LOCK: False,
            CONF_LOCK_ENTRY: "Front Door",
            "unrelated_option": "changed",
        },
    )

    assert not result.errors
    assert result.user_input["unrelated_option"] == "changed"
    assert not store.envelopes
