# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the pure slot-exclusion primitives."""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.rental_control.allocator import exclusions


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "7",
        {"slot": 7},
        7,
        object(),
    ],
)
def test_parse_rejects_non_list_input(raw: Any) -> None:
    """Any persisted value that is not a list parses to an empty mapping."""
    assert exclusions.parse_excluded_slots(raw) == {}


def test_parse_drops_unplaceable_members() -> None:
    """Members the parser cannot place are dropped rather than raised."""
    raw = [
        "not-a-mapping",
        {"reason": "missing slot"},
        {"slot": "7"},
        {"slot": True},
        {"slot": 0},
        {"slot": -3},
        {"slot": 5.5},
        {"slot": 7, "reason": "lock replaced"},
    ]
    assert exclusions.parse_excluded_slots(raw) == {7: "lock replaced"}


def test_parse_keeps_member_with_non_string_reason() -> None:
    """A non-string reason is normalised to None rather than dropping it."""
    assert exclusions.parse_excluded_slots([{"slot": 4, "reason": 12}]) == {4: None}
    assert exclusions.parse_excluded_slots([{"slot": 4}]) == {4: None}


def test_parse_last_duplicate_wins() -> None:
    """A repeated slot keeps the last recorded reason."""
    raw = [{"slot": 3, "reason": "first"}, {"slot": 3, "reason": "second"}]
    assert exclusions.parse_excluded_slots(raw) == {3: "second"}


def test_serialize_emits_ascending_slots() -> None:
    """Serialisation is deterministic so an unchanged set compares equal."""
    serialized = exclusions.serialize_excluded_slots({9: None, 3: "dead", 5: "gone"})
    assert serialized == [
        {"slot": 3, "reason": "dead"},
        {"slot": 5, "reason": "gone"},
        {"slot": 9, "reason": None},
    ]
    assert exclusions.serialize_excluded_slots({5: "gone", 3: "dead", 9: None}) == (
        serialized
    )


def test_serialize_round_trips_through_the_parser() -> None:
    """A serialised mapping parses back to exactly the same mapping."""
    mapping: dict[int, str | None] = {2: "a", 7: None}
    assert (
        exclusions.parse_excluded_slots(exclusions.serialize_excluded_slots(mapping))
        == mapping
    )


def _refusal(slot: int, **overrides: Any) -> str | None:
    """Evaluate the guards with a workable default fixture."""
    kwargs: dict[str, Any] = {
        "start_slot": 1,
        "max_events": 10,
        "unreadable_slots": frozenset({7}),
        "managed_slots": frozenset(range(1, 11)),
        "claimed_slots": frozenset(),
    }
    kwargs.update(overrides)
    return exclusions.refusal_for_exclusion(slot, **kwargs)


def test_unreadable_unclaimed_in_range_slot_is_excludable() -> None:
    """The only excludable slot is an unreadable, unclaimed, managed one."""
    assert _refusal(7) is None


def test_slot_below_range_is_out_of_range() -> None:
    """A slot before start_slot is refused as out of range."""
    assert _refusal(3, start_slot=5) == exclusions.REFUSAL_SLOT_OUT_OF_RANGE


def test_slot_above_range_is_out_of_range() -> None:
    """A slot at start_slot + max_events is refused as out of range."""
    assert _refusal(11) == exclusions.REFUSAL_SLOT_OUT_OF_RANGE


def test_slot_absent_from_managed_set_is_out_of_range() -> None:
    """A slot inside the numeric range but unmanaged is out of range."""
    assert (
        _refusal(7, managed_slots=frozenset({1, 2}))
        == exclusions.REFUSAL_SLOT_OUT_OF_RANGE
    )


def test_readable_slot_is_refused() -> None:
    """A readable slot can never cause a freeze, so it cannot be excluded."""
    assert _refusal(4) == exclusions.REFUSAL_SLOT_READABLE


def test_claimed_slot_is_refused() -> None:
    """A slot claimed by this entry's owners is not causing the freeze."""
    assert _refusal(7, claimed_slots=frozenset({7})) == exclusions.REFUSAL_SLOT_CLAIMED


def test_out_of_range_is_reported_before_readable() -> None:
    """An out-of-range slot has no meaningful observed status."""
    assert (
        _refusal(99, unreadable_slots=frozenset(), claimed_slots=frozenset({99}))
        == exclusions.REFUSAL_SLOT_OUT_OF_RANGE
    )


def test_readable_is_reported_before_claimed() -> None:
    """Readability is evaluated ahead of the claim check."""
    assert _refusal(4, claimed_slots=frozenset({4})) == exclusions.REFUSAL_SLOT_READABLE


def test_guards_import_no_home_assistant() -> None:
    """The guard module stays free of Home Assistant imports."""
    from pathlib import Path

    source = Path(exclusions.__file__).read_text(encoding="utf-8")
    assert "homeassistant" not in source
