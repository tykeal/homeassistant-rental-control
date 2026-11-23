# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Test the fail-closed Keymaster runtime association gate."""

from types import SimpleNamespace

import pytest

from custom_components.rental_control.allocator.associations import AssociationMode
from custom_components.rental_control.allocator.associations import AssociationTuple
from custom_components.rental_control.const import CONF_LOCK_ENTRY
from custom_components.rental_control.const import DOMAIN
from custom_components.rental_control.const import PROTECTED_REGISTRY_STORE
from custom_components.rental_control.keymaster_runtime import (
    coordinator_code_publication_allowed,
)
from custom_components.rental_control.keymaster_runtime import keymaster_runtime_enabled
from custom_components.rental_control.keymaster_runtime import (
    lockless_allocation_allowed,
)
from custom_components.rental_control.keymaster_runtime import (
    protected_legacy_unresolved,
)


def _association(mode: AssociationMode, selector: str | None) -> dict[str, object]:
    """Build persisted data for one committed association mode."""
    association = AssociationTuple(
        legacy_lock_selection_present=True,
        legacy_lock_selection_raw=selector,
        verified_keymaster_entry_id=(
            "keymaster-entry" if mode is AssociationMode.VERIFIED else None
        ),
        association_generation=1,
        association_transaction_id="a" * 32,
        mode=mode,
    )
    return association.merge_into_config({CONF_LOCK_ENTRY: selector})


def test_explicit_lockless_retained_selector_is_calendar_only() -> None:
    """An old selector cannot reactivate hardware for explicit locklessness."""
    assert not keymaster_runtime_enabled(
        _association(AssociationMode.EXPLICIT_LOCKLESS, "Front Door")
    )


def test_verified_runtime_uses_verified_association_not_selector_identity() -> None:
    """A differing but present legacy selector is not the physical target."""
    assert keymaster_runtime_enabled(
        _association(AssociationMode.VERIFIED, "Old accounting name")
    )


def test_verified_without_selector_fails_closed() -> None:
    """A persisted verified association without its selector is non-operational."""
    assert not keymaster_runtime_enabled(_association(AssociationMode.VERIFIED, None))


def test_malformed_association_metadata_fails_closed() -> None:
    """Unparseable committed metadata cannot fall back to a legacy selector."""
    assert not keymaster_runtime_enabled(
        {
            CONF_LOCK_ENTRY: "Front Door",
            "association_mode": "not-a-mode",
            "association_generation": 1,
        }
    )


@pytest.mark.parametrize("selector", [123, ["front"], {"lock": "front"}])
def test_malformed_legacy_selector_is_neither_a_lock_nor_lockless(
    selector: object,
) -> None:
    """Malformed legacy evidence cannot authorize hardware, issuance or display."""
    config = {CONF_LOCK_ENTRY: selector}
    entry = SimpleNamespace(data=config, entry_id="rc-entry")
    hass = SimpleNamespace(data={DOMAIN: {PROTECTED_REGISTRY_STORE: object()}})
    coordinator = SimpleNamespace(
        config_entry=entry,
        lockname="stale_previous_lock",
        keymaster_runtime_enabled=True,
        hass=hass,
    )
    assert not keymaster_runtime_enabled(
        config, legacy_runtime_lockname="stale_previous_lock"
    )
    assert protected_legacy_unresolved(coordinator)
    assert not lockless_allocation_allowed(hass, entry)
    assert not coordinator_code_publication_allowed(coordinator)


@pytest.mark.parametrize("selector", [None, "", "   ", "(none)"])
def test_valid_legacy_lockless_selectors_still_allow_allocation(
    selector: str | None,
) -> None:
    """Supported no-lock representations remain distinct from malformed values."""
    entry = SimpleNamespace(data={CONF_LOCK_ENTRY: selector}, entry_id="rc-entry")
    hass = SimpleNamespace(data={DOMAIN: {PROTECTED_REGISTRY_STORE: object()}})
    assert lockless_allocation_allowed(hass, entry)


def test_lockless_allocation_requires_acknowledged_committed_association() -> None:
    """Calendar-only entries allocate only under the matching durable tuple."""
    config = _association(AssociationMode.EXPLICIT_LOCKLESS, "old lock")
    entry = SimpleNamespace(data=config, entry_id="rc-entry")
    current = AssociationTuple.from_config(config)
    store = SimpleNamespace(association_for_entry=lambda _: current)
    hass = SimpleNamespace(data={DOMAIN: {PROTECTED_REGISTRY_STORE: store}})
    assert lockless_allocation_allowed(hass, entry)
    store.association_for_entry = lambda _: None
    assert not lockless_allocation_allowed(hass, entry)
    entry.data = {}
    assert lockless_allocation_allowed(hass, entry)


def test_lockless_association_lookup_failure_denies_allocation() -> None:
    """Lockless issuance fails closed if the protected association lookup fails."""
    config = _association(AssociationMode.EXPLICIT_LOCKLESS, "old lock")
    entry = SimpleNamespace(data=config, entry_id="rc-entry")

    def fail_lookup(_entry_id: str) -> None:
        """Model a protected store that cannot read the association."""
        raise OSError("association lookup unavailable")

    hass = SimpleNamespace(
        data={
            DOMAIN: {
                PROTECTED_REGISTRY_STORE: SimpleNamespace(
                    association_for_entry=fail_lookup
                )
            }
        }
    )

    assert not lockless_allocation_allowed(hass, entry)


def test_missing_config_blocks_protected_runtime_and_code_publication() -> None:
    """Malformed protected runtime cannot fall back to a constructed lock name."""
    coordinator = SimpleNamespace(
        config_entry=SimpleNamespace(data=None),
        lockname="front_door",
        hass=SimpleNamespace(data={DOMAIN: {PROTECTED_REGISTRY_STORE: object()}}),
    )
    assert protected_legacy_unresolved(coordinator)
    assert not coordinator_code_publication_allowed(coordinator)
    coordinator.hass.data[DOMAIN].clear()
    assert not protected_legacy_unresolved(coordinator)
    assert coordinator_code_publication_allowed(coordinator)
