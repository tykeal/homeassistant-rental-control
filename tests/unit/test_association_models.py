# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Validate exact persisted association and transition model boundaries."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from custom_components.rental_control.allocator.associations import AssociationChange
from custom_components.rental_control.allocator.associations import AssociationMode
from custom_components.rental_control.allocator.associations import AssociationTuple
from custom_components.rental_control.const import CONF_ASSOCIATION_GENERATION
from custom_components.rental_control.const import CONF_ASSOCIATION_MODE
from custom_components.rental_control.const import CONF_ASSOCIATION_TRANSACTION_ID
from custom_components.rental_control.const import CONF_LOCK_ENTRY
from custom_components.rental_control.const import CONF_VERIFIED_KEYMASTER_ENTRY_ID

_TX1 = "a" * 32
_TX2 = "b" * 32


def _unresolved() -> AssociationTuple:
    """Return one exact unresolved legacy configuration tuple."""
    return AssociationTuple(
        True,
        "Front Door",
        None,
        0,
        None,
        AssociationMode.LEGACY_UNRESOLVED,
    )


def _verified() -> AssociationTuple:
    """Return one exact current verified tuple."""
    return AssociationTuple(
        True,
        "Front Door",
        "km-selected",
        1,
        _TX1,
        AssociationMode.VERIFIED,
    )


def test_config_tuple_round_trip_preserves_absence_and_unrelated_options() -> None:
    """Tuple projection does not collapse absent lock selection and explicit None."""
    absent = AssociationTuple.from_config({})
    explicit_none = AssociationTuple.from_config({CONF_LOCK_ENTRY: None})
    assert absent is not None and explicit_none is not None
    assert not absent.legacy_lock_selection_present
    assert explicit_none.legacy_lock_selection_present
    assert absent.mode is explicit_none.mode is AssociationMode.LEGACY_UNRESOLVED
    absent_verified_config = {
        CONF_VERIFIED_KEYMASTER_ENTRY_ID: "km-selected",
        CONF_ASSOCIATION_GENERATION: 1,
        CONF_ASSOCIATION_TRANSACTION_ID: _TX1,
        CONF_ASSOCIATION_MODE: "verified",
    }
    absent_verified = AssociationTuple.from_config(absent_verified_config)
    assert absent_verified is not None
    assert not absent_verified.legacy_lock_selection_present

    committed = _verified()
    merged = committed.merge_into_config(
        {"unrelated_option": "keep", CONF_LOCK_ENTRY: "old"}
    )
    assert merged["unrelated_option"] == "keep"
    assert AssociationTuple.from_config(merged) == committed
    cleared = AssociationTuple(
        False,
        None,
        None,
        1,
        _TX2,
        AssociationMode.EXPLICIT_LOCKLESS,
    ).merge_into_config({CONF_LOCK_ENTRY: "old", "keep": True})
    assert CONF_LOCK_ENTRY not in cleared
    assert cleared["keep"] is True


@pytest.mark.parametrize(
    "data",
    (
        {CONF_LOCK_ENTRY: 12},
        {CONF_ASSOCIATION_GENERATION: 1},
        {
            CONF_LOCK_ENTRY: "Front Door",
            CONF_VERIFIED_KEYMASTER_ENTRY_ID: "km",
            CONF_ASSOCIATION_GENERATION: True,
            CONF_ASSOCIATION_TRANSACTION_ID: _TX1,
            CONF_ASSOCIATION_MODE: "verified",
        },
        {
            CONF_LOCK_ENTRY: "Front Door",
            CONF_VERIFIED_KEYMASTER_ENTRY_ID: "km",
            CONF_ASSOCIATION_GENERATION: 1,
            CONF_ASSOCIATION_TRANSACTION_ID: "not-an-opaque-id",
            CONF_ASSOCIATION_MODE: "verified",
        },
        {
            CONF_LOCK_ENTRY: None,
            CONF_VERIFIED_KEYMASTER_ENTRY_ID: "km",
            CONF_ASSOCIATION_GENERATION: 1,
            CONF_ASSOCIATION_TRANSACTION_ID: _TX1,
            CONF_ASSOCIATION_MODE: "unsupported",
        },
    ),
)
def test_config_parser_rejects_partial_or_contradictory_tuples(
    data: dict[str, Any],
) -> None:
    """Malformed persisted config cannot become a guessed association."""
    assert AssociationTuple.from_config(data) is None


@pytest.mark.parametrize(
    "payload",
    (
        None,
        [],
        {},
        {"legacy_lock_selection_present": True},
        {
            **_verified().to_record(),
            "unexpected": True,
        },
        {
            **_verified().to_record(),
            "association_generation": True,
        },
        {
            **_verified().to_record(),
            "verified_keymaster_entry_id": None,
        },
    ),
)
def test_record_parser_rejects_noncanonical_tuple_shapes(payload: object) -> None:
    """Protected records require all exact fields and valid tuple variants."""
    assert AssociationTuple.from_record(payload) is None


def test_association_change_parser_rejects_ambiguous_evidence_shapes() -> None:
    """A replay record must preserve exact transition and proof evidence."""
    old = _unresolved()
    new = _verified()
    valid = AssociationChange("rc-1", old, new, ("opaque-reference",))
    payload = valid.to_record()
    assert AssociationChange.from_record(payload) == valid

    for malformed in (
        None,
        {},
        {**payload, "unexpected": True},
        {**payload, "affected_references": "not-a-list"},
        {**payload, "independently_proven_references": [None]},
        {**payload, "rc_entry_id": ""},
    ):
        assert AssociationChange.from_record(malformed) is None

    invalid_proof = deepcopy(payload)
    invalid_proof["independent_original_keymaster_entry_id"] = "km-other"
    invalid_proof["independently_proven_references"] = ["opaque-reference"]
    assert AssociationChange.from_record(invalid_proof) is None


@pytest.mark.parametrize(
    ("old", "new", "references"),
    (
        (_unresolved(), _unresolved(), ()),
        (
            _unresolved(),
            AssociationTuple(
                True,
                "Front Door",
                "km-selected",
                2,
                _TX2,
                AssociationMode.VERIFIED,
            ),
            (),
        ),
        (_unresolved(), _verified(), ("same", "same")),
        (_unresolved(), _verified(), ("",)),
    ),
)
def test_association_changes_reject_nonconsecutive_or_duplicate_evidence(
    old: AssociationTuple,
    new: AssociationTuple,
    references: tuple[str, ...],
) -> None:
    """Only one consecutive tuple change with unique references is representable."""
    with pytest.raises(ValueError):
        AssociationChange("rc-1", old, new, references)


def test_independent_association_proof_is_scoped_to_the_original_entry() -> None:
    """A physical-lock assertion cannot target a different selected entry."""
    with pytest.raises(ValueError):
        AssociationChange(
            "rc-1",
            _unresolved(),
            _verified(),
            ("reference",),
            independent_original_keymaster_entry_id="km-other",
            independently_proven_references=("reference",),
        )
