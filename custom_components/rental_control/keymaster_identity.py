# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Dormant identity and capability checks for supported Keymaster entities."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from types import MappingProxyType
from typing import Any
from typing import Final
from typing import Mapping

from homeassistant.config_entries import ConfigEntry
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify

_KEYMASTER_DOMAIN: Final = "keymaster"
_PARENT_ENTRY_ID: Final = "parent_entry_id"
_MASKED_STATES: Final = frozenset({"[redacted]", "masked", "****"})
_PROPERTY_SUFFIXES: Final = {
    "text": frozenset({"name", "pin"}),
    "button": frozenset({"reset"}),
    "switch": frozenset(
        {
            "accesslimit_date_range_enabled",
            "enabled",
            "notifications",
            "override_parent",
        }
    ),
    "datetime": frozenset(
        {"accesslimit_date_range_start", "accesslimit_date_range_end"}
    ),
}
_SLOT_PROPERTY: Final = re.compile(
    r"^(?P<domain>text|button|switch|datetime)\.code_slots:"
    r"(?P<slot>[1-9][0-9]*)\.(?P<property>[a-z0-9_]+)$"
)


class CapabilityStatus(StrEnum):
    """Sanitized capability or live-state outcome."""

    READY = "ready"
    INVALID_IDENTITY = "invalid_identity"
    KEYMASTER_NOT_FOUND = "keymaster_not_found"
    KEYMASTER_NOT_LOADED = "keymaster_not_loaded"
    MISSING = "missing"
    AMBIGUOUS = "ambiguous"
    WRONG_DOMAIN = "wrong_domain"
    WRONG_PLATFORM = "wrong_platform"
    WRONG_CONFIG_ENTRY = "wrong_config_entry"
    DISABLED = "disabled"
    STATE_MISSING = "state_missing"
    RESTORED = "restored"
    UNAVAILABLE = "unavailable"
    UNKNOWN_STATE = "unknown_state"
    MASKED = "masked"
    INHERITANCE_UNVERIFIED = "inheritance_unverified"
    INHERITED_UNAVAILABLE = "inherited_unavailable"


@dataclass(frozen=True, slots=True)
class CapabilityIdentity:
    """Exact immutable registry identity for one supported slot property."""

    keymaster_entry_id: str
    platform: str
    domain: str
    property_key: str
    slot: int
    unique_id: str
    generation: int


@dataclass(frozen=True, slots=True)
class CapabilityResolution:
    """A sanitized resolution result; never retain the entity's raw state."""

    identity: CapabilityIdentity | None
    status: CapabilityStatus
    entity_id: str | None = None
    registry_entry_id: str | None = None
    boolean_state: bool | None = None

    @property
    def ready(self) -> bool:
        """Return whether the exact property has a fresh usable live state."""
        return self.status is CapabilityStatus.READY

    @property
    def generation(self) -> int | None:
        """Return the mapping generation carried by this result."""
        return self.identity.generation if self.identity is not None else None


@dataclass(frozen=True, slots=True)
class CapabilityRegistryIndex:
    """Single-pass immutable index preserving duplicate registry matches."""

    by_unique_id: Mapping[str, tuple[Any, ...]]

    @classmethod
    def build(cls, hass: HomeAssistant) -> CapabilityRegistryIndex:
        """Index all registry entries once without discarding duplicates."""
        mutable: dict[str, list[Any]] = {}
        for item in er.async_get(hass).entities.values():
            mutable.setdefault(item.unique_id, []).append(item)
        return cls(
            MappingProxyType(
                {unique_id: tuple(entries) for unique_id, entries in mutable.items()}
            )
        )


def build_capability_identity(
    keymaster_entry_id: object,
    property_key: object,
    *,
    generation: int,
) -> CapabilityIdentity | None:
    """Derive an exact supported unique ID with Home Assistant slugification."""
    if (
        not isinstance(keymaster_entry_id, str)
        or not keymaster_entry_id
        or not isinstance(property_key, str)
        or isinstance(generation, bool)
        or not isinstance(generation, int)
        or generation < 1
    ):
        return None
    match = _SLOT_PROPERTY.fullmatch(property_key)
    if match is None:
        return None
    domain = match["domain"]
    property_name = match["property"]
    if property_name not in _PROPERTY_SUFFIXES[domain]:
        return None
    return CapabilityIdentity(
        keymaster_entry_id=keymaster_entry_id,
        platform=_KEYMASTER_DOMAIN,
        domain=domain,
        property_key=property_key,
        slot=int(match["slot"]),
        unique_id=f"{keymaster_entry_id}_{slugify(property_key)}",
        generation=generation,
    )


def resolve_capability(
    hass: HomeAssistant,
    keymaster_entry_id: object,
    property_key: object,
    *,
    generation: int,
    registry_index: CapabilityRegistryIndex | None = None,
) -> CapabilityResolution:
    """Resolve an exact owned, enabled property and classify only safe state."""
    identity = build_capability_identity(
        keymaster_entry_id, property_key, generation=generation
    )
    if identity is None:
        return CapabilityResolution(None, CapabilityStatus.INVALID_IDENTITY)
    return resolve_capability_identity(
        hass,
        identity,
        registry_index=registry_index,
    )


def resolve_capability_identity(
    hass: HomeAssistant,
    identity: CapabilityIdentity,
    *,
    registry_index: CapabilityRegistryIndex | None = None,
) -> CapabilityResolution:
    """Resolve a precomputed identity against one optional shared index."""
    entry = hass.config_entries.async_get_entry(identity.keymaster_entry_id)
    if entry is None or entry.domain != _KEYMASTER_DOMAIN:
        return CapabilityResolution(identity, CapabilityStatus.KEYMASTER_NOT_FOUND)
    if entry.state is not ConfigEntryState.LOADED:
        return CapabilityResolution(identity, CapabilityStatus.KEYMASTER_NOT_LOADED)
    index = registry_index or CapabilityRegistryIndex.build(hass)
    return _resolve_capability(
        hass,
        entry,
        identity,
        check_inheritance=True,
        registry_index=index,
    )


def _resolve_capability(
    hass: HomeAssistant,
    entry: ConfigEntry,
    identity: CapabilityIdentity,
    *,
    check_inheritance: bool,
    registry_index: CapabilityRegistryIndex,
) -> CapabilityResolution:
    """Resolve registry ownership and current state without retaining values."""
    unique_matches = registry_index.by_unique_id.get(identity.unique_id, ())
    if not unique_matches:
        return CapabilityResolution(identity, CapabilityStatus.MISSING)
    domain_matches = [item for item in unique_matches if item.domain == identity.domain]
    if not domain_matches:
        return CapabilityResolution(identity, CapabilityStatus.WRONG_DOMAIN)
    platform_matches = [
        item for item in domain_matches if item.platform == identity.platform
    ]
    if not platform_matches:
        return CapabilityResolution(identity, CapabilityStatus.WRONG_PLATFORM)
    if len(platform_matches) != 1:
        return CapabilityResolution(identity, CapabilityStatus.AMBIGUOUS)
    registry_entry = platform_matches[0]
    if registry_entry.config_entry_id != entry.entry_id:
        return CapabilityResolution(
            identity,
            CapabilityStatus.WRONG_CONFIG_ENTRY,
            registry_entry.entity_id,
            registry_entry.config_entry_id,
        )
    if registry_entry.disabled_by is not None:
        return CapabilityResolution(
            identity,
            CapabilityStatus.DISABLED,
            registry_entry.entity_id,
            registry_entry.config_entry_id,
        )
    state = hass.states.get(registry_entry.entity_id)
    if state is None:
        return CapabilityResolution(
            identity,
            CapabilityStatus.STATE_MISSING,
            registry_entry.entity_id,
            registry_entry.config_entry_id,
        )
    if state.attributes.get("restored") is True:
        status = CapabilityStatus.RESTORED
    elif state.state == "unavailable":
        status = CapabilityStatus.UNAVAILABLE
    elif (
        state.attributes.get("masked") is True
        or state.state.casefold() in _MASKED_STATES
    ):
        status = CapabilityStatus.MASKED
    elif state.state == "unknown":
        status = CapabilityStatus.UNKNOWN_STATE
    else:
        status = CapabilityStatus.READY
    boolean_state = (
        state.state == "on"
        if identity.domain == "switch" and state.state in {"on", "off"}
        else None
    )
    result = CapabilityResolution(
        identity,
        status,
        registry_entry.entity_id,
        registry_entry.config_entry_id,
        boolean_state,
    )
    if result.ready and check_inheritance and _requires_parent_override(identity):
        inherited_status = _child_inheritance_status(
            hass, entry, identity, registry_index
        )
        if inherited_status is not CapabilityStatus.READY:
            return CapabilityResolution(
                identity,
                inherited_status,
                registry_entry.entity_id,
                registry_entry.config_entry_id,
            )
    return result


def _requires_parent_override(identity: CapabilityIdentity) -> bool:
    """Return whether Keymaster gates this child property's availability."""
    if identity.domain == "button":
        return False
    return not identity.property_key.endswith((".override_parent", ".notifications"))


def _child_inheritance_status(
    hass: HomeAssistant,
    entry: ConfigEntry,
    identity: CapabilityIdentity,
    registry_index: CapabilityRegistryIndex,
) -> CapabilityStatus:
    """Require a loaded parent and live same-slot override for inherited controls."""
    parent_entry_id = entry.data.get(_PARENT_ENTRY_ID)
    if parent_entry_id is None:
        return CapabilityStatus.READY
    if not isinstance(parent_entry_id, str) or not parent_entry_id:
        return CapabilityStatus.INHERITANCE_UNVERIFIED
    parent = hass.config_entries.async_get_entry(parent_entry_id)
    if (
        parent is None
        or parent.domain != _KEYMASTER_DOMAIN
        or parent.state is not ConfigEntryState.LOADED
        or parent.entry_id == entry.entry_id
    ):
        return CapabilityStatus.INHERITANCE_UNVERIFIED
    override_key = f"switch.code_slots:{identity.slot}.override_parent"
    override_identity = build_capability_identity(
        entry.entry_id,
        override_key,
        generation=identity.generation,
    )
    if override_identity is None:
        return CapabilityStatus.INHERITANCE_UNVERIFIED
    override = _resolve_capability(
        hass,
        entry,
        override_identity,
        check_inheritance=False,
        registry_index=registry_index,
    )
    if not override.ready or override.boolean_state is None:
        return CapabilityStatus.INHERITANCE_UNVERIFIED
    if not override.boolean_state:
        return CapabilityStatus.INHERITED_UNAVAILABLE
    return CapabilityStatus.READY


def reset_capabilities_ready(
    hass: HomeAssistant,
    keymaster_entry_id: str,
    slot: int,
    *,
    generation: int,
) -> bool:
    """Check reset plus same-slot name/PIN confirmation controls only.

    This is a capability preflight, not storage authorization or a command to
    press the button. Callers must also supply a current protective commit token
    and their own serialized point-in-time dispatch boundary.
    """
    if isinstance(slot, bool) or not isinstance(slot, int) or slot < 1:
        return False
    registry_index = CapabilityRegistryIndex.build(hass)
    entry = hass.config_entries.async_get_entry(keymaster_entry_id)
    name_identity = build_capability_identity(
        keymaster_entry_id,
        f"text.code_slots:{slot}.name",
        generation=generation,
    )
    if (
        entry is None
        or entry.domain != _KEYMASTER_DOMAIN
        or name_identity is None
        or _child_inheritance_status(hass, entry, name_identity, registry_index)
        is not CapabilityStatus.READY
    ):
        return False
    properties = (
        f"button.code_slots:{slot}.reset",
        f"text.code_slots:{slot}.name",
        f"text.code_slots:{slot}.pin",
    )
    return all(
        resolve_capability(
            hass,
            keymaster_entry_id,
            property_key,
            generation=generation,
            registry_index=registry_index,
        ).ready
        for property_key in properties
    )
