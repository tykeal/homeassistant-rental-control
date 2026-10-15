# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Target resolution for the slot-exclusion service."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.rental_control.const import CONF_EXCLUDED_SLOTS
from custom_components.rental_control.const import COORDINATOR
from custom_components.rental_control.const import DOMAIN
from custom_components.rental_control.coordinator_helpers import exclusion_targets


def _coordinator(lockname: str | None, start: int, count: int) -> SimpleNamespace:
    """Build a coordinator stand-in with a managed slot range."""
    return SimpleNamespace(lockname=lockname, start_slot=start, max_events=count)


def _hass(
    entries: dict[str, SimpleNamespace], data: dict[str, Any] | None = None
) -> Any:
    """Build a Home Assistant stand-in exposing coordinators and entries."""
    config_entries = [
        SimpleNamespace(
            entry_id=entry_id,
            data=(data or {}).get(entry_id, {}),
            options={},
        )
        for entry_id in entries
    ]
    by_id = {entry.entry_id: entry for entry in config_entries}
    return SimpleNamespace(
        data={
            DOMAIN: {
                entry_id: {COORDINATOR: coordinator}
                for entry_id, coordinator in entries.items()
            }
        },
        config_entries=SimpleNamespace(
            async_get_entry=by_id.get,
            async_entries=lambda _domain=None: config_entries,
        ),
    )


def _resolve(hass: Any, **kwargs: Any) -> tuple[Any, str]:
    """Resolve an exclusion target with sensible defaults."""
    kwargs.setdefault("entity_id", None)
    kwargs.setdefault("lockname", None)
    kwargs.setdefault("excluded", True)
    return exclusion_targets.resolve_exclusion_target(hass, **kwargs)


def test_both_target_forms_are_ambiguous() -> None:
    """An entity and a lock name together identify nothing."""
    hass = _hass({"entry-a": _coordinator("front", 1, 5)})

    target, refusal = _resolve(hass, entity_id="sensor.guest", lockname="front", slot=2)

    assert target is None
    assert refusal == exclusion_targets.REFUSAL_AMBIGUOUS_TARGET


def test_no_target_form_is_refused() -> None:
    """A call naming neither an entity nor a lock is refused."""
    target, refusal = _resolve(_hass({}), slot=2)

    assert target is None
    assert refusal == exclusion_targets.REFUSAL_MISSING_TARGET


def test_unknown_lock_is_refused() -> None:
    """A lock no loaded entry manages cannot be targeted."""
    hass = _hass({"entry-a": _coordinator("front", 1, 5)})

    target, refusal = _resolve(hass, lockname="back", slot=2)

    assert target is None
    assert refusal == exclusion_targets.REFUSAL_UNKNOWN_LOCK


def test_lock_resolves_to_its_single_entry() -> None:
    """One entry on a lock resolves without further narrowing."""
    hass = _hass({"entry-a": _coordinator("front", 1, 5)})

    target, refusal = _resolve(hass, lockname="front", slot=2)

    assert refusal == ""
    assert target is not None
    assert (target.entry_id, target.lockname, target.slot) == ("entry-a", "front", 2)


def test_disjoint_ranges_resolve_by_slot() -> None:
    """Two entries sharing a lock are separated by their managed ranges."""
    hass = _hass(
        {
            "entry-a": _coordinator("front", 1, 5),
            "entry-b": _coordinator("front", 6, 5),
        }
    )

    target, refusal = _resolve(hass, lockname="front", slot=7)

    assert refusal == ""
    assert target is not None
    assert target.entry_id == "entry-b"


def test_overlapping_ranges_are_ambiguous() -> None:
    """Two entries genuinely claiming one slot cannot be disambiguated."""
    hass = _hass(
        {
            "entry-a": _coordinator("front", 1, 10),
            "entry-b": _coordinator("front", 6, 5),
        }
    )

    target, refusal = _resolve(hass, lockname="front", slot=7)

    assert target is None
    assert refusal == exclusion_targets.REFUSAL_AMBIGUOUS_LOCK


def test_setting_out_of_range_still_resolves_for_the_guard() -> None:
    """An out-of-range slot resolves so the guard can name the refusal."""
    hass = _hass({"entry-a": _coordinator("front", 1, 5)})

    target, refusal = _resolve(hass, lockname="front", slot=99)

    assert refusal == ""
    assert target is not None
    assert target.entry_id == "entry-a"


def test_clearing_resolves_through_the_persisted_set() -> None:
    """A slot that left every managed range is cleared through its owner."""
    hass = _hass(
        {
            "entry-a": _coordinator("front", 1, 5),
            "entry-b": _coordinator("front", 6, 5),
        },
        {"entry-b": {CONF_EXCLUDED_SLOTS: [{"slot": 99}]}},
    )

    target, refusal = _resolve(hass, lockname="front", slot=99, excluded=False)

    assert refusal == ""
    assert target is not None
    assert target.entry_id == "entry-b"


def test_clearing_an_unowned_slot_is_ambiguous_on_a_shared_lock() -> None:
    """Clearing never guesses between two entries sharing one lock."""
    hass = _hass(
        {
            "entry-a": _coordinator("front", 1, 5),
            "entry-b": _coordinator("front", 6, 5),
        }
    )

    target, refusal = _resolve(hass, lockname="front", slot=99, excluded=False)

    assert target is None
    assert refusal == exclusion_targets.REFUSAL_AMBIGUOUS_LOCK


@pytest.mark.parametrize("entity_id", ["switch.guest", "sensor.missing"])
def test_unknown_entities_are_refused(entity_id: str) -> None:
    """A non-sensor or unknown entity cannot identify an entry."""
    hass = _hass({"entry-a": _coordinator("front", 1, 5)})
    hass.states = SimpleNamespace(get=lambda _entity_id: None)

    target, refusal = _resolve(hass, entity_id=entity_id, slot=2)

    assert target is None
    assert refusal == exclusion_targets.REFUSAL_UNKNOWN_ENTITY
