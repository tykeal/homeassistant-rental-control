# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Verify privacy boundaries across selected-Keymaster consumers."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC
from datetime import datetime
from datetime import timedelta
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from homeassistant.components.diagnostics.const import REDACTED
import pytest

from custom_components.rental_control import listeners
from custom_components.rental_control import util
from custom_components.rental_control.coordinator_helpers import allocation_failure
from custom_components.rental_control.diagnostics import (
    async_get_config_entry_diagnostics,
)

from tests.integration.test_verified_consumer_dispatch import _CODE
from tests.integration.test_verified_consumer_dispatch import _OWNER_IDENTITY
from tests.integration.test_verified_consumer_dispatch import _RELEASES
from tests.integration.test_verified_consumer_dispatch import _SLOT
from tests.integration.test_verified_consumer_dispatch import (
    _register_recording_services,
)
from tests.integration.test_verified_consumer_dispatch import _setup_verified_consumer

_PRIVATE_GUEST = "Sensitive Guest Example"
_PRIVATE_PIN = "Synthetic PIN 9384"


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize("release", _RELEASES)
async def test_selected_identity_diagnostics_and_notifications_are_sanitized(
    hass,
    tmp_path: Path,
    release: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Redact private state while retaining actionable availability status."""
    coordinator, _store, entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage", release
    )
    pin_id = entity_ids[f"text.code_slots:{_SLOT}.pin"]
    name_id = entity_ids[f"text.code_slots:{_SLOT}.name"]
    hass.states.async_set(pin_id, _PRIVATE_PIN)
    hass.states.async_set(name_id, _PRIVATE_GUEST)
    await hass.async_block_till_done()

    notifications: list[str] = []
    monkeypatch.setattr(
        allocation_failure,
        "async_create",
        lambda _hass, message, **_kwargs: notifications.append(message),
    )
    allocation_failure.report(
        hass,
        coordinator.config_entry.entry_id,
        _PRIVATE_GUEST,
        RuntimeError(f"{_PRIVATE_GUEST}: {_PRIVATE_PIN}"),
    )
    result = await async_get_config_entry_diagnostics(hass, coordinator.config_entry)
    serialized = json.dumps(result, default=str)

    assert _PRIVATE_GUEST not in serialized
    assert _PRIVATE_PIN not in serialized
    assert _PRIVATE_GUEST not in notifications[0]
    assert _PRIVATE_PIN not in notifications[0]
    assert "RuntimeError" in notifications[0]
    assert result["allocation_failure"]["last_error"] == REDACTED
    assert result["keymaster_status"]["association"]["status"] == "verified"
    assert result["keymaster_status"]["capabilities"]["status_counts"]["ready"] > 0
    assert result["keymaster_status"]["version"]["informational_only"] is True
    assert result["keymaster_status"]["protection"]["status"] == "ready"

    hass.states.async_set(pin_id, "unavailable")
    await hass.async_block_till_done()
    unavailable = await async_get_config_entry_diagnostics(
        hass, coordinator.config_entry
    )
    counts = unavailable["keymaster_status"]["capabilities"]["status_counts"]
    assert counts["unavailable"] > 0
    assert unavailable["keymaster_status"]["capabilities"]["next_step"] == (
        "restore_keymaster_entity_availability"
    )


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_unexpected_event_fields_are_not_persisted_in_diagnostics(
    hass,
    tmp_path: Path,
) -> None:
    """Event diagnostics retain only allowlisted metadata."""
    coordinator, _store, _entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage"
    )
    coordinator.keymaster_event_diagnostics = []
    event_data = {
        "state": "unlocked",
        "code_slot_num": _SLOT,
        "entity_id": "lock.selected_physical_lock",
        "pin": _PRIVATE_PIN,
        "guest_name": _PRIVATE_GUEST,
        "state_attributes": {"pin": _PRIVATE_PIN, "name": _PRIVATE_GUEST},
    }
    context = listeners._KeymasterEventContext(
        hass=hass,
        config_entry=coordinator.config_entry,
        coordinator=coordinator,
        raw_lockname="private-lock-label",
        event_lockname="private_lock_label",
        event_data=event_data,
        code_slot_num=_SLOT,
        diagnostics_enabled=True,
    )

    listeners._record_if_enabled(context, "accepted")

    recorded = coordinator.keymaster_event_diagnostics
    assert recorded == [
        {
            "timestamp": recorded[0]["timestamp"],
            "lockname_present": True,
            "state": "unlocked",
            "code_slot_num": _SLOT,
            "disposition": "accepted",
        }
    ]
    assert _PRIVATE_PIN not in json.dumps(recorded)
    assert _PRIVATE_GUEST not in json.dumps(recorded)
    assert "private-lock-label" not in json.dumps(recorded)
    assert "selected_physical_lock" not in json.dumps(recorded)


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize("release", _RELEASES)
async def test_greedy_cleanup_exception_echo_is_sanitized(
    hass,
    tmp_path: Path,
    release: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Actual cleanup keeps its fence without logging service error text."""
    coordinator, store, entity_ids = await _setup_verified_consumer(
        hass, tmp_path / ".storage", release
    )
    events: list[dict[str, Any]] = []
    await _register_recording_services(hass, store, entity_ids, events)
    original_deps = util._keymaster_deps

    async def immediate_sleep(_delay: float) -> None:
        """Skip retry delay in this test."""
        return None

    notifications: list[str] = []

    def capture_notification(
        _hass,
        message: str,
        **_kwargs: Any,
    ) -> None:
        """Capture any cleanup notification for secret-echo assertions."""
        notifications.append(message)

    monkeypatch.setattr(
        util,
        "_keymaster_deps",
        lambda: replace(
            original_deps(),
            sleep=immediate_sleep,
            create_notification=capture_notification,
        ),
    )
    reservation = SimpleNamespace(
        identity_key=_OWNER_IDENTITY,
        slot_name=_PRIVATE_GUEST,
        slot_code=_CODE,
        start=datetime.now(UTC) - timedelta(hours=2),
        end=datetime.now(UTC) + timedelta(hours=2),
        buffered_start=datetime.now(UTC) - timedelta(hours=2),
        buffered_end=datetime.now(UTC) + timedelta(hours=2),
        display_slot_name=_PRIVATE_GUEST,
    )
    assert (
        await coordinator.event_overrides._apply_set(
            coordinator, _SLOT, reservation, "privacy-cleanup-set"
        )
    ).confirmed
    coordinator._slot_mappings = {"mappings": {_OWNER_IDENTITY: {"slot": _SLOT}}}

    async def fail_with_sensitive_provider_text(
        _service_registry, *_args, **_kwargs
    ) -> None:
        """Simulate a service exception echoing sensitive payload."""
        raise RuntimeError(f"{_PRIVATE_GUEST}: {_PRIVATE_PIN}")

    monkeypatch.setattr(
        type(hass.services), "async_call", fail_with_sensitive_provider_text
    )
    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        await coordinator.event_overrides.async_check_overrides(
            coordinator, calendar=[]
        )

    assert _PRIVATE_PIN not in caplog.text
    assert _PRIVATE_GUEST not in caplog.text
    assert "Synthetic PIN" not in caplog.text
    cleanup_records = [
        record
        for record in caplog.records
        if "service dispatch failed" in record.getMessage()
    ]
    assert len(cleanup_records) == 1
    cleanup_message = cleanup_records[0].getMessage()
    assert "Selected Keymaster entry reset capability" in cleanup_message
    assert "failure category service_operation_failed" in cleanup_message
    assert f"slot {_SLOT}" in cleanup_message
    assert "verified operation remains unconfirmed" in cleanup_message
    assert "Verify the physical slot and service availability" in cleanup_message
    assert cleanup_records[0].exc_info is None
    assert cleanup_records[0].exc_text is None
    assert "RuntimeError" not in caplog.text
    assert all(_PRIVATE_PIN not in message for message in notifications)
    assert all(_PRIVATE_GUEST not in message for message in notifications)
    loaded = store.loaded_result
    assert loaded is not None and loaded.data is not None
    assert any(
        fence["fence_type"] == "pending_clear"
        for fence in loaded.data["pending_fences"]
    )
