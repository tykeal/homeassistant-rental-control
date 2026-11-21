# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Verify exact Keymaster entity identities and fresh capability boundaries."""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock
from unittest.mock import patch

import attr
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_registry import RegistryEntryDisabler
from homeassistant.util import slugify
import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.rental_control.keymaster_identity import CapabilityStatus
from custom_components.rental_control.keymaster_identity import (
    build_capability_identity,
)
from custom_components.rental_control.keymaster_identity import reset_capabilities_ready
from custom_components.rental_control.keymaster_identity import resolve_capability

from tests.fixtures.keymaster_releases import artifact_paths
from tests.fixtures.keymaster_releases import import_keymaster_release
from tests.fixtures.keymaster_releases import remove_imported_package

_RELEASES = ("v0.6.1", "v0.6.5")
_PROPERTY_KEYS = (
    "text.code_slots:21.name",
    "text.code_slots:21.pin",
    "button.code_slots:21.reset",
    "switch.code_slots:21.enabled",
    "switch.code_slots:21.accesslimit_date_range_enabled",
    "datetime.code_slots:21.accesslimit_date_range_start",
    "datetime.code_slots:21.accesslimit_date_range_end",
    "switch.code_slots:21.override_parent",
    "switch.code_slots:21.notifications",
)


@pytest.fixture(scope="module")
def verified_artifacts() -> dict[str, Path]:
    """Require the fixture acquisition step; unit tests never use the network."""
    return artifact_paths()


def _loaded_keymaster_entry(
    hass,
    entry_id: str = "km-front",
    *,
    data: dict[str, Any] | None = None,
) -> MockConfigEntry:
    """Create one loaded Keymaster config entry in Home Assistant."""
    entry = MockConfigEntry(
        domain="keymaster",
        title="Front Lock",
        entry_id=entry_id,
        data=data or {},
    )
    entry.add_to_hass(hass)
    entry._async_set_state(hass, ConfigEntryState.LOADED, None)
    return entry


def _register_capability(
    hass,
    entry: MockConfigEntry,
    property_key: str,
    *,
    platform: str = "keymaster",
    domain: str | None = None,
    disabled_by: RegistryEntryDisabler | None = None,
    identity_entry_id: str | None = None,
    state: str = "ready",
) -> str:
    """Register one exact unique ID and publish its initial live state."""
    expected = build_capability_identity(
        identity_entry_id or entry.entry_id,
        property_key,
        generation=1,
    )
    assert expected is not None
    entity = er.async_get(hass).async_get_or_create(
        domain or expected.domain,
        platform,
        expected.unique_id,
        config_entry=entry,
        disabled_by=disabled_by,
    )
    hass.states.async_set(entity.entity_id, state)
    return entity.entity_id


@pytest.mark.parametrize("property_key", _PROPERTY_KEYS)
def test_identity_uses_ha_slugify_and_exact_slot_property(
    property_key: str,
) -> None:
    """Use the exact supported property, domain, KM platform, and HA slug."""
    identity = build_capability_identity("km-entry", property_key, generation=7)

    assert identity is not None
    assert identity.platform == "keymaster"
    assert identity.property_key == property_key
    assert identity.domain == property_key.split(".", maxsplit=1)[0]
    assert identity.slot == 21
    assert identity.unique_id == f"km-entry_{slugify(property_key)}"
    assert identity.generation == 7


@pytest.mark.parametrize(
    "property_key",
    [
        "button.reset_lock",
        "text.code_slots:0.pin",
        "text.code_slots:21.unknown",
        "switch.code_slots:21.reset",
        "text.code_slots:21.pin.extra",
    ],
)
def test_identity_rejects_non_capability_or_invalid_slot(property_key: str) -> None:
    """Reject whole-lock controls, unsupported properties, and invalid slots."""
    assert build_capability_identity("km-entry", property_key, generation=1) is None


@pytest.mark.parametrize(
    ("state", "attributes", "expected_status"),
    [
        (None, {}, CapabilityStatus.STATE_MISSING),
        ("ready", {"restored": True}, CapabilityStatus.RESTORED),
        ("unavailable", {}, CapabilityStatus.UNAVAILABLE),
        ("[REDACTED]", {}, CapabilityStatus.MASKED),
        ("ready", {"masked": True}, CapabilityStatus.MASKED),
        ("unknown", {}, CapabilityStatus.UNKNOWN_STATE),
    ],
)
async def test_resolution_rejects_non_live_or_masked_state(
    hass,
    state: str | None,
    attributes: dict[str, Any],
    expected_status: CapabilityStatus,
) -> None:
    """Do not treat restored, unavailable, unknown, or masked state as evidence."""
    entry = _loaded_keymaster_entry(hass)
    entity_id = _register_capability(hass, entry, "text.code_slots:21.pin")
    if state is None:
        hass.states.async_remove(entity_id)
    else:
        hass.states.async_set(entity_id, state, attributes)

    result = resolve_capability(
        hass, entry.entry_id, "text.code_slots:21.pin", generation=3
    )

    assert result.status is expected_status
    assert result.entity_id == entity_id
    assert result.generation == 3
    assert not result.ready
    assert not hasattr(result, "state_value")


async def test_resolution_requires_loaded_exact_owner_platform_and_domain(hass) -> None:
    """Wrong ownership or platform/domain distractors cannot satisfy identity."""
    selected = _loaded_keymaster_entry(hass, "km-selected")
    other = _loaded_keymaster_entry(hass, "km-other")

    wrong_owner_id = _register_capability(
        hass,
        other,
        "text.code_slots:21.name",
        identity_entry_id=selected.entry_id,
    )
    wrong_owner = resolve_capability(
        hass, selected.entry_id, "text.code_slots:21.name", generation=1
    )
    assert wrong_owner.status is CapabilityStatus.WRONG_CONFIG_ENTRY
    assert wrong_owner.entity_id == wrong_owner_id

    _register_capability(
        hass,
        selected,
        "text.code_slots:21.pin",
        platform="other_platform",
    )
    wrong_platform = resolve_capability(
        hass, selected.entry_id, "text.code_slots:21.pin", generation=1
    )
    assert wrong_platform.status is CapabilityStatus.WRONG_PLATFORM
    assert wrong_platform.entity_id is None

    _register_capability(
        hass,
        selected,
        "button.code_slots:21.reset",
        domain="sensor",
    )
    wrong_domain = resolve_capability(
        hass, selected.entry_id, "button.code_slots:21.reset", generation=1
    )
    assert wrong_domain.status is CapabilityStatus.WRONG_DOMAIN
    assert wrong_domain.entity_id is None


async def test_resolution_requires_enabled_entity_and_loaded_keymaster_entry(
    hass,
) -> None:
    """Disabled registry entries and unloaded integration entries fail closed."""
    entry = _loaded_keymaster_entry(hass)
    entity_id = _register_capability(
        hass,
        entry,
        "switch.code_slots:21.enabled",
        disabled_by=RegistryEntryDisabler.USER,
    )
    disabled = resolve_capability(
        hass, entry.entry_id, "switch.code_slots:21.enabled", generation=1
    )
    assert disabled.status is CapabilityStatus.DISABLED
    assert disabled.entity_id == entity_id

    entry._async_set_state(hass, ConfigEntryState.NOT_LOADED, None)
    unloaded = resolve_capability(
        hass, entry.entry_id, "switch.code_slots:21.enabled", generation=2
    )
    assert unloaded.status is CapabilityStatus.KEYMASTER_NOT_LOADED
    assert unloaded.entity_id is None


async def test_resolution_rejects_duplicate_exact_identity(hass, monkeypatch) -> None:
    """A duplicate registry identity is ambiguous, never first-match-wins."""
    entry = _loaded_keymaster_entry(hass)
    entity_id = _register_capability(hass, entry, "text.code_slots:21.name")
    registry = er.async_get(hass)
    registered = registry.async_get(entity_id)
    assert registered is not None
    duplicate = attr.evolve(registered, entity_id="text.duplicate_name")
    fake_registry = SimpleNamespace(
        entities={
            entity_id: registered,
            "text.duplicate_name": duplicate,
        }
    )
    monkeypatch.setattr(
        "custom_components.rental_control.keymaster_identity.er.async_get",
        lambda _hass: fake_registry,
    )

    result = resolve_capability(
        hass, entry.entry_id, "text.code_slots:21.name", generation=1
    )

    assert result.status is CapabilityStatus.AMBIGUOUS
    assert result.entity_id is None


async def test_other_slot_does_not_satisfy_requested_identity(hass) -> None:
    """A same-property entity for another slot is not a match."""
    entry = _loaded_keymaster_entry(hass)
    _register_capability(hass, entry, "text.code_slots:22.name")

    result = resolve_capability(
        hass, entry.entry_id, "text.code_slots:21.name", generation=1
    )

    assert result.status is CapabilityStatus.MISSING
    assert result.identity is not None
    assert result.identity.slot == 21


@pytest.mark.parametrize(
    ("override_state", "expected_control_status", "expected_ready"),
    [
        (None, CapabilityStatus.INHERITANCE_UNVERIFIED, False),
        ("off", CapabilityStatus.INHERITED_UNAVAILABLE, False),
        ("unavailable", CapabilityStatus.INHERITANCE_UNVERIFIED, False),
        ("on", CapabilityStatus.READY, True),
    ],
)
async def test_child_reset_requires_live_same_slot_override_and_controls(
    hass,
    override_state: str | None,
    expected_control_status: CapabilityStatus,
    expected_ready: bool,
) -> None:
    """A pressable child reset needs independent live authority and confirmations."""
    parent = _loaded_keymaster_entry(hass, "km-parent")
    child = _loaded_keymaster_entry(
        hass, "km-child", data={"parent_entry_id": parent.entry_id}
    )
    _register_capability(
        hass,
        child,
        "button.code_slots:21.reset",
        state="ready",
    )
    _register_capability(
        hass,
        child,
        "text.code_slots:21.name",
        state="ready",
    )
    _register_capability(
        hass,
        child,
        "text.code_slots:21.pin",
        state="ready",
    )
    if override_state is not None:
        _register_capability(
            hass,
            child,
            "switch.code_slots:21.override_parent",
            state=override_state,
        )

    reset = resolve_capability(
        hass, child.entry_id, "button.code_slots:21.reset", generation=4
    )
    name = resolve_capability(
        hass, child.entry_id, "text.code_slots:21.name", generation=4
    )

    assert reset.status is CapabilityStatus.READY
    assert name.status is expected_control_status
    assert (
        reset_capabilities_ready(hass, child.entry_id, 21, generation=4)
        is expected_ready
    )


@pytest.mark.parametrize("release", _RELEASES)
async def test_actual_keymaster_release_uses_same_identity_formula(
    hass,
    tmp_path: Path,
    release: str,
    verified_artifacts: dict[str, Path],  # noqa: ARG001
) -> None:
    """Both supported package entity classes use HA's exact slugification."""
    module_name = "custom_components.keymaster"
    assert module_name not in sys.modules
    import_keymaster_release(release, tmp_path / release, module_name)
    try:
        upstream_entity = import_module(f"{module_name}.entity")
        entry = MockConfigEntry(
            domain="keymaster",
            title="Front Lock",
            entry_id=f"km-{release}",
        )
        coordinator = SimpleNamespace(
            sync_get_lock_by_config_entry_id=lambda _entry_id: None
        )
        for property_key in _PROPERTY_KEYS:
            identity = build_capability_identity(
                entry.entry_id, property_key, generation=1
            )
            assert identity is not None
            description = upstream_entity.KeymasterEntityDescription(
                hass=hass,
                config_entry=entry,
                coordinator=coordinator,
                key=property_key,
                name="Synthetic property",
            )
            with patch.object(
                upstream_entity.CoordinatorEntity, "__init__", lambda *args: None
            ):
                entity = upstream_entity.KeymasterEntity(description)
            assert entity.unique_id == identity.unique_id
    finally:
        remove_imported_package(module_name)


@pytest.mark.parametrize("release", _RELEASES)
async def test_actual_child_availability_does_not_authorize_reset(
    hass,
    tmp_path: Path,
    release: str,
    verified_artifacts: dict[str, Path],  # noqa: ARG001
) -> None:
    """Baseline child reset stays available while required controls are not."""
    module_name = "custom_components.keymaster"
    import_keymaster_release(release, tmp_path / release, module_name)
    try:
        upstream_entity = import_module(f"{module_name}.entity")
        entry = MockConfigEntry(
            domain="keymaster",
            title="Child Lock",
            entry_id=f"km-child-{release}",
        )
        slot = SimpleNamespace(
            override_parent=False,
            enabled=True,
            notifications=True,
            name=None,
            pin=None,
            accesslimit_date_range_enabled=True,
            accesslimit_date_range_start=None,
            accesslimit_date_range_end=None,
            accesslimit_day_of_week={},
        )
        lock = SimpleNamespace(
            lock_name="Child Lock",
            connected=True,
            parent_name="Parent Lock",
            code_slots={21: slot},
            redact_pin_codes=False,
            redact_slot_names=False,
        )
        coordinator = SimpleNamespace(
            sync_get_lock_by_config_entry_id=lambda _entry_id: lock
        )

        def build_entity(module: Any, class_name: str, property_key: str) -> Any:
            """Instantiate an upstream platform entity without HA dispatch."""
            description_class = getattr(
                module, f"Keymaster{class_name}EntityDescription"
            )
            entity_class = getattr(module, f"Keymaster{class_name}")
            description = description_class(
                hass=hass,
                config_entry=entry,
                coordinator=coordinator,
                key=property_key,
                name="Synthetic child property",
            )
            entity = entity_class(description)
            entity.async_write_ha_state_if_changed = Mock()
            return entity

        with patch.object(
            upstream_entity.CoordinatorEntity, "__init__", lambda *args: None
        ):
            text_module = import_module(f"{module_name}.text")
            button_module = import_module(f"{module_name}.button")
            switch_module = import_module(f"{module_name}.switch")
            datetime_module = import_module(f"{module_name}.datetime")
            name = build_entity(text_module, "Text", "text.code_slots:21.name")
            pin = build_entity(text_module, "Text", "text.code_slots:21.pin")
            reset = build_entity(button_module, "Button", "button.code_slots:21.reset")
            enabled = build_entity(
                switch_module, "Switch", "switch.code_slots:21.enabled"
            )
            override = build_entity(
                switch_module, "Switch", "switch.code_slots:21.override_parent"
            )
            notifications = build_entity(
                switch_module, "Switch", "switch.code_slots:21.notifications"
            )
            date_start = build_entity(
                datetime_module,
                "DateTime",
                "datetime.code_slots:21.accesslimit_date_range_start",
            )

            for entity in (
                name,
                pin,
                reset,
                enabled,
                override,
                notifications,
                date_start,
            ):
                entity._handle_coordinator_update()

        assert reset.available
        assert not name.available
        assert not pin.available
        assert not enabled.available
        assert not date_start.available
        assert override.available
        assert notifications.available
    finally:
        remove_imported_package(module_name)
