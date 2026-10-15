# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the pure slot-exclusion primitives."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.rental_control.allocator import exclusions
from custom_components.rental_control.allocator.models import CycleObservation
from custom_components.rental_control.const import CONF_EXCLUDED_SLOTS
from custom_components.rental_control.coordinator_helpers import slot_exclusions


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


def _unexpected_update(*args: Any, **kwargs: Any) -> None:
    """Fail if an entry rebuild is attempted."""
    raise AssertionError("config entry was rebuilt")


def _hass(entries: list[Any]) -> Any:
    """Build a Home Assistant stand-in exposing config entries."""
    by_id = {entry.entry_id: entry for entry in entries}
    return SimpleNamespace(
        config_entries=SimpleNamespace(
            async_get_entry=by_id.get,
            async_entries=lambda _domain=None: list(entries),
        )
    )


def _entry(entry_id: str, data: dict[str, Any]) -> Any:
    """Build a config-entry stand-in with the given data mapping."""
    return SimpleNamespace(entry_id=entry_id, data=data, options={})


def test_load_returns_empty_for_a_missing_entry() -> None:
    """An unknown entry id yields no exclusions rather than raising."""
    assert slot_exclusions.load(_hass([]), "entry-a") == {}


def test_load_returns_empty_when_the_key_is_absent() -> None:
    """An entry that never excluded a slot yields an empty mapping."""
    hass = _hass([_entry("entry-a", {"name": "Entry A"})])

    assert slot_exclusions.load(hass, "entry-a") == {}


def test_load_tolerates_a_malformed_stored_value() -> None:
    """A corrupt persisted value can never stop an entry from setting up."""
    hass = _hass([_entry("entry-a", {CONF_EXCLUDED_SLOTS: "not-a-list"})])

    assert slot_exclusions.load(hass, "entry-a") == {}


def test_load_parses_a_stored_exclusion() -> None:
    """A well-formed persisted value round-trips into the mapping."""
    hass = _hass(
        [
            _entry(
                "entry-a",
                {CONF_EXCLUDED_SLOTS: [{"slot": 5, "reason": "dead relay"}]},
            )
        ]
    )

    assert slot_exclusions.load(hass, "entry-a") == {5: "dead relay"}


def test_load_scopes_to_the_requested_entry() -> None:
    """One entry's exclusions never leak into another entry's gate."""
    hass = _hass(
        [
            _entry("entry-a", {CONF_EXCLUDED_SLOTS: [{"slot": 5}]}),
            _entry("entry-b", {CONF_EXCLUDED_SLOTS: [{"slot": 9}]}),
        ]
    )

    assert slot_exclusions.load(hass, "entry-b") == {9: None}


def _observation(
    *,
    managed: frozenset[int] = frozenset({1, 2, 3}),
    unreadable: frozenset[int] = frozenset({2}),
) -> CycleObservation:
    """Build the observation re-validation is derived from."""
    return CycleObservation(
        entry_id="entry-a",
        lockname="front",
        managed_slots=managed,
        observed_codes={},
        unreadable_slots=unreadable,
    )


def test_revalidate_keeps_a_still_unreadable_slot() -> None:
    """An exclusion survives while its slot stays dead and managed."""
    kept, cleared = slot_exclusions.revalidate({2: "dead relay"}, _observation())

    assert kept == {2: "dead relay"}
    assert cleared == {}


def test_revalidate_clears_a_slot_that_became_readable() -> None:
    """Recovery of the slot retires the exclusion within the same cycle."""
    kept, cleared = slot_exclusions.revalidate(
        {2: "dead relay"}, _observation(unreadable=frozenset())
    )

    assert kept == {}
    assert cleared == {2: "dead relay"}


def test_revalidate_clears_a_slot_outside_the_managed_range() -> None:
    """Narrowing the managed range retires exclusions it no longer covers."""
    kept, cleared = slot_exclusions.revalidate(
        {2: None}, _observation(managed=frozenset({1}), unreadable=frozenset({2}))
    )

    assert kept == {}
    assert cleared == {2: None}


def test_persist_writes_a_new_exclusion() -> None:
    """A changed set is written into the entry's data, not its options."""
    entry = _entry("entry-a", {"name": "Entry A"})
    hass = _hass([entry])
    updates: list[dict[str, Any]] = []
    hass.config_entries.async_update_entry = lambda _entry, **kwargs: updates.append(
        kwargs
    )

    assert slot_exclusions.persist_excluded_slots(hass, "entry-a", {2: "dead"}) is True
    assert updates == [
        {
            "data": {
                "name": "Entry A",
                CONF_EXCLUDED_SLOTS: [{"slot": 2, "reason": "dead"}],
            }
        }
    ]


def test_persist_is_a_no_op_when_unchanged() -> None:
    """An unchanged set never rewrites the entry."""
    entry = _entry("entry-a", {CONF_EXCLUDED_SLOTS: [{"slot": 2, "reason": "dead"}]})
    hass = _hass([entry])
    updates: list[dict[str, Any]] = []
    hass.config_entries.async_update_entry = lambda _entry, **kwargs: updates.append(
        kwargs
    )

    assert slot_exclusions.persist_excluded_slots(hass, "entry-a", {2: "dead"}) is False
    assert updates == []


def test_persist_removes_the_key_when_the_set_empties() -> None:
    """Clearing the last exclusion removes the key rather than storing []."""
    entry = _entry("entry-a", {CONF_EXCLUDED_SLOTS: [{"slot": 2}], "name": "Entry A"})
    hass = _hass([entry])
    updates: list[dict[str, Any]] = []
    hass.config_entries.async_update_entry = lambda _entry, **kwargs: updates.append(
        kwargs
    )

    assert slot_exclusions.persist_excluded_slots(hass, "entry-a", {}) is True
    assert updates == [{"data": {"name": "Entry A"}}]


def test_persist_tolerates_a_missing_entry() -> None:
    """Persisting for an unloaded entry reports no write instead of raising."""
    assert (
        slot_exclusions.persist_excluded_slots(_hass([]), "entry-a", {2: None}) is False
    )


async def test_persisting_to_data_does_not_reload_the_entry() -> None:
    """Writing data while options are empty leaves update_listener inert."""
    from custom_components.rental_control import update_listener

    entry = _entry("entry-a", {CONF_EXCLUDED_SLOTS: [{"slot": 2}]})
    hass = _hass([entry])
    hass.config_entries.async_update_entry = _unexpected_update

    await update_listener(hass, entry)
