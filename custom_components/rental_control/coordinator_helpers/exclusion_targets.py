# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Target resolution for operator slot exclusions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.helpers import entity_registry as er

from ..const import COORDINATOR
from ..const import DOMAIN
from . import slot_exclusions
from .reissue_targets import entry_id_for_entity_attributes

REFUSAL_AMBIGUOUS_TARGET = "ambiguous_target"
REFUSAL_MISSING_TARGET = "missing_target"
REFUSAL_UNKNOWN_ENTITY = "unknown_entity"
REFUSAL_UNKNOWN_LOCK = "unknown_lock"
REFUSAL_AMBIGUOUS_LOCK = "ambiguous_lock"


@dataclass(frozen=True, slots=True)
class ExclusionTarget:
    """The single config entry an exclusion request addresses."""

    entry_id: str
    lockname: str | None
    slot: int
    coordinator: Any


def resolve_exclusion_target(
    hass: Any,
    *,
    entity_id: str | None,
    lockname: str | None,
    slot: int,
    excluded: bool,
) -> tuple[ExclusionTarget | None, str]:
    """Resolve one exclusion request to exactly one loaded config entry."""
    if entity_id is not None and lockname is not None:
        return None, REFUSAL_AMBIGUOUS_TARGET
    if entity_id is None and lockname is None:
        return None, REFUSAL_MISSING_TARGET
    if entity_id is not None:
        return _resolve_entity(hass, entity_id, slot)
    return _resolve_lock(hass, str(lockname), slot, excluded)


def _resolve_entity(
    hass: Any, entity_id: str, slot: int
) -> tuple[ExclusionTarget | None, str]:
    """Resolve a Rental Control sensor entity to its owning entry."""
    if entity_id.split(".", 1)[0] != SENSOR_DOMAIN:
        return None, REFUSAL_UNKNOWN_ENTITY
    state = hass.states.get(entity_id)
    if state is None:
        return None, REFUSAL_UNKNOWN_ENTITY
    registry_entry = er.async_get(hass).async_get(entity_id)
    entry_id = getattr(registry_entry, "config_entry_id", None)
    if not isinstance(entry_id, str):
        entry_id = entry_id_for_entity_attributes(hass, state.attributes)
    if not isinstance(entry_id, str):
        return None, REFUSAL_UNKNOWN_ENTITY
    coordinator = _coordinators(hass).get(entry_id)
    if coordinator is None:
        return None, REFUSAL_UNKNOWN_ENTITY
    return (
        ExclusionTarget(
            entry_id, getattr(coordinator, "lockname", None), slot, coordinator
        ),
        "",
    )


def _resolve_lock(
    hass: Any, lockname: str, slot: int, excluded: bool
) -> tuple[ExclusionTarget | None, str]:
    """Resolve a lock name and slot to one entry, narrowing by slot range.

    Several entries may share one Keymaster lock across disjoint slot
    ranges, so the lock alone does not identify an entry. When clearing,
    an exclusion may outlive the range that justified it, so the
    persisted set identifies its owner instead of guessing.
    """
    lock_matches = [
        (entry_id, coordinator)
        for entry_id, coordinator in _coordinators(hass).items()
        if getattr(coordinator, "lockname", None) == lockname
    ]
    if not lock_matches:
        return None, REFUSAL_UNKNOWN_LOCK
    matches = [candidate for candidate in lock_matches if _manages(candidate[1], slot)]
    if not matches and not excluded:
        matches = [
            candidate
            for candidate in lock_matches
            if slot in slot_exclusions.load(hass, candidate[0])
        ] or lock_matches
    if not matches:
        matches = lock_matches
    if len(matches) > 1:
        return None, REFUSAL_AMBIGUOUS_LOCK
    entry_id, coordinator = matches[0]
    return ExclusionTarget(entry_id, lockname, slot, coordinator), ""


def _manages(coordinator: Any, slot: int) -> bool:
    """Return whether a coordinator's managed range contains a slot."""
    start = getattr(coordinator, "start_slot", None)
    count = getattr(coordinator, "max_events", None)
    if not isinstance(start, int) or not isinstance(count, int):
        return False
    return start <= slot < start + count


def _coordinators(hass: Any) -> dict[str, Any]:
    """Return the loaded coordinators keyed by config entry id."""
    found: dict[str, Any] = {}
    for entry_id, entry_data in (hass.data.get(DOMAIN, {}) or {}).items():
        if not isinstance(entry_id, str) or not isinstance(entry_data, dict):
            continue
        coordinator = entry_data.get(COORDINATOR)
        if coordinator is not None:
            found[entry_id] = coordinator
    return found


__all__ = ["ExclusionTarget", "resolve_exclusion_target"]
