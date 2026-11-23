# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Exact current Keymaster entry and inherited-slot source resolution."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from .allocator.associations import AssociationMode
from .allocator.associations import AssociationTuple
from .const import CONF_ASSOCIATION_GENERATION
from .const import CONF_ASSOCIATION_MODE
from .const import CONF_ASSOCIATION_TRANSACTION_ID
from .const import CONF_VERIFIED_KEYMASTER_ENTRY_ID
from .const import DOMAIN
from .const import LOCK_MANAGER
from .const import PROTECTED_REGISTRY_STORE
from .keymaster_identity import CapabilityResolution
from .keymaster_identity import resolve_capability
from .keymaster_lifecycle import get_keymaster_lifecycle

_PARENT_ENTRY_ID = "parent_entry_id"
_LOCK_ENTITY_ID = "lock_entity_id"
_LOCKNAME = "lockname"


@dataclass(frozen=True, slots=True)
class VerifiedSlotSource:
    """Current verified source identity for one inherited or overridden slot."""

    entry: ConfigEntry
    lock_entity_id: str
    lockname: str
    name_capability: CapabilityResolution
    lifecycle_generation: int


def association_is_verified(config_entry: ConfigEntry) -> bool:
    """Return whether the current entry explicitly selects a verified identity."""
    association = AssociationTuple.from_config(config_entry.data)
    return (
        association is not None
        and association.mode is AssociationMode.VERIFIED
        and association.verified_keymaster_entry_id is not None
    )


def association_requires_verified_routing(config_entry: ConfigEntry) -> bool:
    """Prevent malformed or committed non-legacy tuples from using name routing."""
    association = AssociationTuple.from_config(config_entry.data)
    if association is not None:
        return association.mode is not AssociationMode.LEGACY_UNRESOLVED
    return any(
        key in config_entry.data
        for key in (
            CONF_VERIFIED_KEYMASTER_ENTRY_ID,
            CONF_ASSOCIATION_GENERATION,
            CONF_ASSOCIATION_TRANSACTION_ID,
            CONF_ASSOCIATION_MODE,
        )
    )


def verified_association_entry(
    hass: HomeAssistant, config_entry: ConfigEntry
) -> ConfigEntry | None:
    """Resolve only a currently acknowledged, loaded selected entry."""
    association = AssociationTuple.from_config(config_entry.data)
    if (
        association is None
        or association.mode is not AssociationMode.VERIFIED
        or association.verified_keymaster_entry_id is None
    ):
        return None
    domain_data = hass.data.get(DOMAIN)
    store = (
        domain_data.get(PROTECTED_REGISTRY_STORE)
        if isinstance(domain_data, dict)
        else None
    )
    association_reader = getattr(store, "association_for_entry", None)
    if not callable(association_reader):
        return None
    try:
        if association_reader(config_entry.entry_id) != association:
            return None
    except Exception:
        return None
    selected = hass.config_entries.async_get_entry(
        association.verified_keymaster_entry_id
    )
    if (
        selected is None
        or selected.domain != LOCK_MANAGER
        or selected.disabled_by is not None
        or selected.state is not ConfigEntryState.LOADED
        or _physical_lock_entity_id(selected) is None
        or _raw_lockname(selected) is None
    ):
        return None
    return selected


def verified_slot_source(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    slot: int,
) -> VerifiedSlotSource | None:
    """Resolve one ready name source under the selected entry's inheritance."""
    selected = verified_association_entry(hass, config_entry)
    lifecycle = get_keymaster_lifecycle(hass, config_entry.entry_id)
    if (
        selected is None
        or lifecycle is None
        or lifecycle.keymaster_entry_id != selected.entry_id
        or slot < 1
    ):
        return None
    generation = lifecycle.generation
    current = lifecycle.current
    name_key = f"text.code_slots:{slot}.name"
    selected_name = current.get(name_key)
    source = selected
    parent_id = selected.data.get(_PARENT_ENTRY_ID)
    if parent_id is not None:
        if not isinstance(parent_id, str) or not parent_id:
            return None
        parent = hass.config_entries.async_get_entry(parent_id)
        if (
            parent is None
            or parent.domain != LOCK_MANAGER
            or parent.disabled_by is not None
            or parent.state is not ConfigEntryState.LOADED
            or parent.entry_id == selected.entry_id
            or _physical_lock_entity_id(parent) is None
            or _raw_lockname(parent) is None
        ):
            return None
        override = current.get(f"switch.code_slots:{slot}.override_parent")
        if (
            override is None
            or not override.ready
            or override.identity is None
            or override.identity.keymaster_entry_id != selected.entry_id
            or override.identity.slot != slot
            or override.identity.generation != generation
            or override.boolean_state is None
        ):
            return None
        if not override.boolean_state:
            source = parent

    if source.entry_id == selected.entry_id and (
        selected_name is None
        or not selected_name.ready
        or selected_name.identity is None
        or selected_name.identity.keymaster_entry_id != selected.entry_id
        or selected_name.identity.slot != slot
        or selected_name.identity.generation != generation
    ):
        return None

    source_lock_id = _physical_lock_entity_id(source)
    source_name = _raw_lockname(source)
    if source_lock_id is None or source_name is None:
        return None
    if source.entry_id == selected.entry_id:
        if selected_name is None:
            return None
        name_capability = selected_name
    else:
        name_capability = resolve_capability(
            hass,
            source.entry_id,
            name_key,
            generation=generation,
        )
    if name_capability is None:
        return None
    if (
        not name_capability.ready
        or name_capability.identity is None
        or name_capability.entity_id is None
        or name_capability.identity.keymaster_entry_id != source.entry_id
        or name_capability.identity.slot != slot
        or name_capability.identity.generation != generation
        or _has_unrelated_shared_source(hass, source)
    ):
        return None
    return VerifiedSlotSource(
        source,
        source_lock_id,
        source_name,
        name_capability,
        generation,
    )


def verified_event_sources(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    slot: int,
) -> tuple[VerifiedSlotSource, ...]:
    """Resolve the selected lock and linked inherited-child event sources."""
    source = verified_slot_source(hass, config_entry, slot)
    selected = verified_association_entry(hass, config_entry)
    if source is None or selected is None:
        return ()

    sources = [source]
    source_entry_ids = {source.entry.entry_id}

    def inherited_child_source(
        child: ConfigEntry,
        parent_entry_id: str,
    ) -> VerifiedSlotSource | None:
        """Return a child physical source only for a live inherited slot."""
        if (
            child.entry_id == parent_entry_id
            or child.domain != LOCK_MANAGER
            or child.disabled_by is not None
            or child.state is not ConfigEntryState.LOADED
            or child.data.get(_PARENT_ENTRY_ID) != parent_entry_id
        ):
            return None
        override = resolve_capability(
            hass,
            child.entry_id,
            f"switch.code_slots:{slot}.override_parent",
            generation=source.lifecycle_generation,
        )
        if (
            not override.ready
            or override.identity is None
            or override.identity.keymaster_entry_id != child.entry_id
            or override.identity.slot != slot
            or override.identity.generation != source.lifecycle_generation
            or override.boolean_state is not False
        ):
            return None
        lock_entity_id = _physical_lock_entity_id(child)
        lockname = _raw_lockname(child)
        if (
            lock_entity_id is None
            or lockname is None
            or _has_unrelated_shared_source(hass, child)
        ):
            return None
        return VerifiedSlotSource(
            child,
            lock_entity_id,
            lockname,
            source.name_capability,
            source.lifecycle_generation,
        )

    # A selected child whose slot inherits from its parent reports events
    # against the child's own physical lock as well as the inherited source.
    if selected.entry_id != source.entry.entry_id:
        selected_source = inherited_child_source(selected, source.entry.entry_id)
        if selected_source is not None:
            sources.append(selected_source)
            source_entry_ids.add(selected.entry_id)

    # Preserve the established parent->child monitoring boundary: only direct
    # children of the selected association can add an inherited event source.
    for candidate in hass.config_entries.async_entries(LOCK_MANAGER):
        if candidate.entry_id in source_entry_ids:
            continue
        child_source = inherited_child_source(candidate, selected.entry_id)
        if child_source is not None:
            sources.append(child_source)
            source_entry_ids.add(candidate.entry_id)

    return tuple(sources)


def event_name_matches_source(event_name: object, source: VerifiedSlotSource) -> bool:
    """Use event names only as corroboration after exact identity resolution."""
    if not isinstance(event_name, str) or not event_name.strip():
        return False
    from homeassistant.util import slugify

    return slugify(event_name) == slugify(source.lockname)


def event_slot_name_matches(
    hass: HomeAssistant,
    event_slot_name: object,
    source: VerifiedSlotSource,
) -> bool:
    """Corroborate a nonempty payload slot name with the live exact state."""
    if event_slot_name in (None, ""):
        return True
    if not isinstance(event_slot_name, str) or not event_slot_name.strip():
        return False
    if source.name_capability.entity_id is None:
        return False
    state = hass.states.get(source.name_capability.entity_id)
    return state is not None and state.state == event_slot_name


def _physical_lock_entity_id(entry: ConfigEntry) -> str | None:
    """Return a syntactically valid physical lock entity ID from config."""
    value = entry.data.get(_LOCK_ENTITY_ID)
    if not isinstance(value, str) or value.count(".") != 1:
        return None
    domain, object_id = value.split(".", maxsplit=1)
    if domain != "lock" or not object_id:
        return None
    return value


def _raw_lockname(entry: ConfigEntry) -> str | None:
    """Return a nonempty Keymaster label for event corroboration only."""
    value = entry.data.get(_LOCKNAME)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _has_unrelated_shared_source(hass: HomeAssistant, source: ConfigEntry) -> bool:
    """Reject same-entity entries unless their explicit parent links relate."""
    physical_id = _physical_lock_entity_id(source)
    if physical_id is None:
        return True
    for candidate in hass.config_entries.async_entries(LOCK_MANAGER):
        if (
            candidate.entry_id == source.entry_id
            or _physical_lock_entity_id(candidate) != physical_id
        ):
            continue
        if not (
            _is_ancestor(hass, candidate.entry_id, source.entry_id)
            or _is_ancestor(hass, source.entry_id, candidate.entry_id)
        ):
            return True
    return False


def _is_ancestor(hass: HomeAssistant, possible_ancestor_id: str, entry_id: str) -> bool:
    """Follow current exact parent IDs without using lock names."""
    seen = {entry_id}
    current_id = entry_id
    while True:
        current = hass.config_entries.async_get_entry(current_id)
        parent_id = current.data.get(_PARENT_ENTRY_ID) if current else None
        if not isinstance(parent_id, str) or not parent_id or parent_id in seen:
            return False
        if parent_id == possible_ancestor_id:
            return True
        parent = hass.config_entries.async_get_entry(parent_id)
        if parent is None or parent.domain != LOCK_MANAGER:
            return False
        seen.add(parent_id)
        current_id = parent_id


__all__ = [
    "VerifiedSlotSource",
    "association_is_verified",
    "association_requires_verified_routing",
    "event_name_matches_source",
    "event_slot_name_matches",
    "verified_event_sources",
    "verified_association_entry",
    "verified_slot_source",
]
