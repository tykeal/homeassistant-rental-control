# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Determine whether an entry may run Keymaster-facing behavior."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .allocator.associations import AssociationMode
from .allocator.associations import AssociationTuple
from .const import ALLOCATOR
from .const import CONF_ASSOCIATION_GENERATION
from .const import CONF_ASSOCIATION_MODE
from .const import CONF_ASSOCIATION_TRANSACTION_ID
from .const import CONF_LOCK_ENTRY
from .const import CONF_VERIFIED_KEYMASTER_ENTRY_ID
from .const import DOMAIN
from .const import PROTECTED_REGISTRY_STORE
from .helpers import configured_lock_slug

_ASSOCIATION_FIELDS = frozenset(
    {
        CONF_ASSOCIATION_GENERATION,
        CONF_ASSOCIATION_MODE,
        CONF_ASSOCIATION_TRANSACTION_ID,
        CONF_VERIFIED_KEYMASTER_ENTRY_ID,
    }
)


def keymaster_runtime_enabled(
    config: Mapping[str, Any],
    *,
    legacy_runtime_lockname: str | None = None,
) -> bool:
    """Return whether persisted association state authorizes Keymaster behavior.

    Explicit locklessness and malformed or incomplete committed associations
    are calendar-only, even when a historical lock selector remains stored.
    """
    lockname = configured_lock_slug(config.get(CONF_LOCK_ENTRY))
    association = AssociationTuple.from_config(config)
    if association is None:
        return False

    if association.mode is AssociationMode.LEGACY_UNRESOLVED:
        # update_config may be projecting a just-submitted selector before HA
        # replaces ConfigEntry.data; only this pre-commit mode may use the
        # coordinator's already-normalized runtime selector.
        return lockname is not None or legacy_runtime_lockname is not None
    return association.mode is AssociationMode.VERIFIED and lockname is not None


def explicitly_lockless(config: Mapping[str, Any]) -> bool:
    """Return whether the committed tuple explicitly selects no Keymaster lock."""
    association = AssociationTuple.from_config(config)
    return (
        association is not None
        and association.mode is AssociationMode.EXPLICIT_LOCKLESS
    )


def _protected_registry_required(coordinator: Any) -> bool:
    """Return whether runtime requires the protected registry."""
    data = getattr(getattr(coordinator, "hass", None), "data", None)
    domain_data = data.get(DOMAIN) if isinstance(data, dict) else None
    if not isinstance(domain_data, dict):
        return False
    allocator = domain_data.get(ALLOCATOR)
    return PROTECTED_REGISTRY_STORE in domain_data or (
        getattr(allocator, "_protection_required", False) is True
    )


def protected_legacy_unresolved(coordinator: Any) -> bool:
    """Block unverified evidence when protected config is missing or unresolved."""
    config = getattr(getattr(coordinator, "config_entry", None), "data", None)
    if not _protected_registry_required(coordinator):
        return False
    if not isinstance(config, Mapping):
        return True
    association = AssociationTuple.from_config(config)
    if association is None:
        return True
    return association.mode is AssociationMode.LEGACY_UNRESOLVED and (
        configured_lock_slug(config.get(CONF_LOCK_ENTRY)) is not None
        or configured_lock_slug(getattr(coordinator, "lockname", None)) is not None
    )


def lockless_allocation_allowed(hass: Any, config_entry: Any) -> bool:
    """Allow lockless allocation only for a safe, acknowledged association."""
    config = config_entry.data
    association = AssociationTuple.from_config(config)
    if association is None:
        return False
    if not _ASSOCIATION_FIELDS.intersection(config):
        return configured_lock_slug(config.get(CONF_LOCK_ENTRY)) is None
    if association.mode is not AssociationMode.EXPLICIT_LOCKLESS:
        return False
    domain_data = hass.data.get(DOMAIN)
    store = (
        domain_data.get(PROTECTED_REGISTRY_STORE)
        if isinstance(domain_data, dict)
        else None
    )
    reader = getattr(store, "association_for_entry", None)
    try:
        return callable(reader) and reader(config_entry.entry_id) == association
    except Exception:
        return False


def coordinator_code_publication_allowed(coordinator: Any) -> bool:
    """Withhold stale codes when committed runtime authorization is absent."""
    if protected_legacy_unresolved(coordinator):
        return False
    entry = getattr(coordinator, "config_entry", None)
    config = getattr(entry, "data", None)
    if not isinstance(config, Mapping):
        return True
    association = AssociationTuple.from_config(config)
    if association is None:
        return False
    if coordinator_keymaster_runtime_enabled(coordinator):
        if (
            _protected_registry_required(coordinator)
            and association.mode is AssociationMode.VERIFIED
        ):
            from .coordinator_helpers.cycle_requests import (
                verified_observation_identity,
            )

            entry_id = getattr(entry, "entry_id", None)
            if not isinstance(entry_id, str):
                return False
            try:
                return (
                    verified_observation_identity(coordinator.hass, entry_id)
                    is not None
                )
            except Exception:
                return False
        return True
    return lockless_allocation_allowed(coordinator.hass, entry)


def coordinator_keymaster_runtime_enabled(coordinator: Any) -> bool:
    """Resolve the runtime gate for production coordinators and small test doubles."""
    enabled = getattr(coordinator, "keymaster_runtime_enabled", None)
    if isinstance(enabled, bool):
        return enabled
    config_entry = getattr(coordinator, "config_entry", None)
    config = getattr(config_entry, "data", None)
    if isinstance(config, Mapping):
        return keymaster_runtime_enabled(
            config,
            legacy_runtime_lockname=getattr(coordinator, "lockname", None),
        )
    return configured_lock_slug(getattr(coordinator, "lockname", None)) is not None
