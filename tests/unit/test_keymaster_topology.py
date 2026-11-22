# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Verify event payloads only corroborate an already resolved slot source."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from typing import cast

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
import pytest

from custom_components.rental_control.allocator.associations import AssociationMode
from custom_components.rental_control.const import CONF_ASSOCIATION_GENERATION
from custom_components.rental_control.const import CONF_ASSOCIATION_MODE
from custom_components.rental_control.const import CONF_ASSOCIATION_TRANSACTION_ID
from custom_components.rental_control.const import CONF_VERIFIED_KEYMASTER_ENTRY_ID
from custom_components.rental_control.const import LOCK_MANAGER
from custom_components.rental_control.keymaster_identity import CapabilityResolution
from custom_components.rental_control.keymaster_identity import CapabilityStatus
from custom_components.rental_control.keymaster_topology import VerifiedSlotSource
from custom_components.rental_control.keymaster_topology import (
    _has_unrelated_shared_source,
)
from custom_components.rental_control.keymaster_topology import _physical_lock_entity_id
from custom_components.rental_control.keymaster_topology import _raw_lockname
from custom_components.rental_control.keymaster_topology import association_is_verified
from custom_components.rental_control.keymaster_topology import (
    association_requires_verified_routing,
)
from custom_components.rental_control.keymaster_topology import (
    event_name_matches_source,
)
from custom_components.rental_control.keymaster_topology import event_slot_name_matches


def _source(entity_id: str | None = "text.guest_name") -> VerifiedSlotSource:
    """Create one source object after identity resolution for payload tests."""
    return VerifiedSlotSource(
        entry=cast(ConfigEntry[Any], SimpleNamespace()),
        lock_entity_id="lock.front_door",
        lockname="front_door",
        name_capability=CapabilityResolution(
            None,
            CapabilityStatus.READY,
            entity_id=entity_id,
        ),
        lifecycle_generation=1,
    )


def test_event_name_only_matches_nonempty_lockname_corroboration() -> None:
    """Names cannot create routing evidence and must match normalized labels."""
    source = _source()

    assert event_name_matches_source("Front Door", source)
    assert not event_name_matches_source(None, source)
    assert not event_name_matches_source("", source)
    assert not event_name_matches_source("   ", source)
    assert not event_name_matches_source("other lock", source)


@pytest.mark.parametrize("event_value", [None, ""])
async def test_empty_event_slot_name_is_non_authorizing_corroboration(
    hass,
    event_value: object,
) -> None:
    """Absent payload names are neutral; nonempty names must match live state."""
    source = _source()
    hass.states.async_set("text.guest_name", "Guest")

    assert event_slot_name_matches(hass, event_value, source)
    assert event_slot_name_matches(hass, "Guest", source)
    assert not event_slot_name_matches(hass, "Other", source)
    assert not event_slot_name_matches(hass, " ", source)
    assert not event_slot_name_matches(hass, {"name": "Guest"}, source)
    assert not event_slot_name_matches(hass, "Guest", _source(None))
    assert not event_slot_name_matches(hass, "Guest", _source("text.missing"))


def test_association_routing_rejects_partial_metadata() -> None:
    """Malformed committed metadata never falls back to legacy name routing."""
    entry = cast(ConfigEntry[Any], SimpleNamespace(entry_id="rc-entry", data={}))
    partial = cast(
        ConfigEntry[Any],
        SimpleNamespace(
            entry_id="rc-entry",
            data={CONF_VERIFIED_KEYMASTER_ENTRY_ID: "km-entry"},
        ),
    )
    verified = cast(
        ConfigEntry[Any],
        SimpleNamespace(
            entry_id="rc-entry",
            data={
                CONF_VERIFIED_KEYMASTER_ENTRY_ID: "km-entry",
                CONF_ASSOCIATION_GENERATION: 1,
                CONF_ASSOCIATION_TRANSACTION_ID: "a" * 32,
                CONF_ASSOCIATION_MODE: AssociationMode.VERIFIED.value,
            },
        ),
    )

    assert not association_requires_verified_routing(entry)
    assert association_requires_verified_routing(partial)
    assert association_requires_verified_routing(verified)
    assert association_is_verified(verified)
    assert not association_is_verified(partial)


def test_shared_physical_source_requires_exact_parent_relationship() -> None:
    """Same-lock children are related by current parent IDs, not matching names."""
    parent = cast(
        ConfigEntry[Any],
        SimpleNamespace(
            entry_id="parent",
            domain=LOCK_MANAGER,
            data={"lock_entity_id": "lock.front", "lockname": "front"},
        ),
    )
    child = cast(
        ConfigEntry[Any],
        SimpleNamespace(
            entry_id="child",
            domain=LOCK_MANAGER,
            data={
                "parent_entry_id": "parent",
                "lock_entity_id": "lock.front",
                "lockname": "front",
            },
        ),
    )
    unrelated = cast(
        ConfigEntry[Any],
        SimpleNamespace(
            entry_id="unrelated",
            domain=LOCK_MANAGER,
            data={"lock_entity_id": "lock.front", "lockname": "front"},
        ),
    )
    entries = {entry.entry_id: entry for entry in (parent, child)}
    hass = cast(
        HomeAssistant,
        SimpleNamespace(
            config_entries=SimpleNamespace(
                async_entries=lambda _domain: list(entries.values()),
                async_get_entry=lambda entry_id: entries.get(entry_id),
            )
        ),
    )

    assert not _has_unrelated_shared_source(hass, child)

    entries[unrelated.entry_id] = unrelated
    assert _has_unrelated_shared_source(hass, unrelated)
    assert _physical_lock_entity_id(child) == "lock.front"
    assert _raw_lockname(child) == "front"


@pytest.mark.parametrize(
    "lock_entity_id",
    ("front", "switch.front", "lock.", "lock.front.extra", None),
)
def test_invalid_physical_lock_ids_never_create_topology(
    lock_entity_id: object,
) -> None:
    """Only one exact lock-domain entity id can corroborate a source."""
    entry = cast(
        ConfigEntry[Any],
        SimpleNamespace(
            entry_id="entry",
            data={"lock_entity_id": lock_entity_id, "lockname": "  "},
        ),
    )

    assert _physical_lock_entity_id(entry) is None
    assert _raw_lockname(entry) is None
