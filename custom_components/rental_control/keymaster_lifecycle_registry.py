# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Per-Rental-Control lifecycle registration and generation epochs."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .allocator.associations import AssociationMode
from .allocator.associations import AssociationTuple
from .const import DOMAIN
from .const import PROTECTED_REGISTRY_STORE
from .helpers import get_entry_data
from .keymaster_lifecycle_tracker import KeymasterEntityLifecycle
from .keymaster_runtime import keymaster_runtime_enabled

_GENERATION_EPOCHS_KEY = "_keymaster_lifecycle_epochs"
_KEYMASTER_LIFECYCLE = "keymaster_lifecycle"


def reserve_keymaster_generation(
    hass: HomeAssistant, rc_entry_id: str, *, minimum: int = 0
) -> int:
    """Reserve a strictly increasing per-entry identity generation."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if not isinstance(domain_data, dict):
        raise ValueError("invalid Rental Control runtime data")
    epochs = domain_data.setdefault(_GENERATION_EPOCHS_KEY, {})
    if not isinstance(epochs, dict):
        raise ValueError("invalid Keymaster generation table")
    current = epochs.get(rc_entry_id, 0)
    if (
        not isinstance(current, int)
        or isinstance(current, bool)
        or current < 0
        or not isinstance(minimum, int)
        or isinstance(minimum, bool)
        or minimum < 0
    ):
        raise ValueError("invalid Keymaster generation epoch")
    generation = max(current + 1, minimum + 1)
    epochs[rc_entry_id] = generation
    return generation


def managed_slot_property_keys(start_slot: int, count: int) -> tuple[str, ...]:
    """Return only exact baseline properties in the configured slot range."""
    if (
        isinstance(start_slot, bool)
        or not isinstance(start_slot, int)
        or start_slot < 1
        or isinstance(count, bool)
        or not isinstance(count, int)
        or count < 1
    ):
        return ()
    suffixes = (
        "text.name",
        "text.pin",
        "button.reset",
        "switch.enabled",
        "switch.accesslimit_date_range_enabled",
        "switch.override_parent",
        "switch.notifications",
        "datetime.accesslimit_date_range_start",
        "datetime.accesslimit_date_range_end",
    )
    return tuple(
        f"{suffix.split('.', maxsplit=1)[0]}.code_slots:{slot}."
        f"{suffix.split('.', maxsplit=1)[1]}"
        for slot in range(start_slot, start_slot + count)
        for suffix in suffixes
    )


def start_keymaster_lifecycle(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    coordinator: Any,
    *,
    on_resolved=None,
) -> None:
    """Track only an explicitly committed verified association, if present."""
    entry_data = get_entry_data(hass, config_entry.entry_id)
    if entry_data is None:
        return
    stop_keymaster_lifecycle(hass, config_entry.entry_id)
    if not keymaster_runtime_enabled(config_entry.data):
        return
    association = AssociationTuple.from_config(config_entry.data)
    if (
        association is None
        or association.mode is not AssociationMode.VERIFIED
        or association.verified_keymaster_entry_id is None
    ):
        return
    domain_data = hass.data.get(DOMAIN)
    if not isinstance(domain_data, dict):
        return
    store = domain_data.get(PROTECTED_REGISTRY_STORE)
    if store is None:
        return
    try:
        acknowledged_association = store.association_for_entry(config_entry.entry_id)
    except Exception:
        return
    if acknowledged_association != association:
        return
    property_keys = managed_slot_property_keys(
        coordinator.start_slot,
        coordinator.max_events,
    )
    if not property_keys:
        return
    initial_generation = reserve_keymaster_generation(
        hass,
        config_entry.entry_id,
        minimum=max(0, association.association_generation - 1),
    )
    lifecycle = KeymasterEntityLifecycle(
        hass,
        association.verified_keymaster_entry_id,
        property_keys,
        on_resolved or (lambda _generation, _resolutions: None),
        initial_generation=initial_generation,
        generation_factory=lambda: reserve_keymaster_generation(
            hass,
            config_entry.entry_id,
            minimum=association.association_generation,
        ),
    )
    lifecycle.start()
    entry_data[_KEYMASTER_LIFECYCLE] = lifecycle


def stop_keymaster_lifecycle(hass: HomeAssistant, rc_entry_id: str) -> None:
    """Idempotently stop and remove a selected-entry lifecycle manager."""
    entry_data = get_entry_data(hass, rc_entry_id)
    if entry_data is None:
        return
    lifecycle = entry_data.pop(_KEYMASTER_LIFECYCLE, None)
    if lifecycle is not None:
        lifecycle.stop()


def get_keymaster_lifecycle(
    hass: HomeAssistant, rc_entry_id: str
) -> KeymasterEntityLifecycle | None:
    """Return the lifecycle manager for one loaded Rental Control entry."""
    entry_data = get_entry_data(hass, rc_entry_id)
    if entry_data is None:
        return None
    lifecycle = entry_data.get(_KEYMASTER_LIFECYCLE)
    return lifecycle if isinstance(lifecycle, KeymasterEntityLifecycle) else None
