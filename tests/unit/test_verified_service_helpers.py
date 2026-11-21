# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Verify exact-address Keymaster service helpers and partial invalidation."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

from homeassistant.util import dt as dt_util
import pytest

from custom_components.rental_control import keymaster_service_helpers
from custom_components.rental_control import keymaster_services
from custom_components.rental_control.keymaster_operations import (
    VerifiedOperationIntent,
)
from custom_components.rental_control.keymaster_operations import (
    VerifiedOperationRejected,
)
from custom_components.rental_control.keymaster_service_helpers import (
    KeymasterServiceDeps,
)


class _Intent:
    """Record exact property dispatches and optionally invalidate mid-flight."""

    def __init__(self, *, invalidate_at: int | None = None) -> None:
        """Create a fresh intent with an optional dispatch invalidation point."""
        self.calls: list[tuple[str, str, dict[str, object] | None]] = []
        self.addresses_used: list[str] = []
        self.slot = 21
        self.invalidate_at = invalidate_at
        self.current = True
        self.addresses = {
            "switch.code_slots:21.enabled": "switch.renamed_enabled",
            "switch.code_slots:21.accesslimit_date_range_enabled": (
                "switch.renamed_date_enabled"
            ),
            "datetime.code_slots:21.accesslimit_date_range_start": (
                "datetime.renamed_start"
            ),
            "datetime.code_slots:21.accesslimit_date_range_end": (
                "datetime.renamed_end"
            ),
            "text.code_slots:21.pin": "text.renamed_pin",
            "text.code_slots:21.name": "text.renamed_name",
            "button.code_slots:21.reset": "button.renamed_reset",
        }

    def is_current(self) -> bool:
        """Return the guard's current event-loop authorization state."""
        return self.current

    async def async_is_current(self) -> bool:
        """Model the settled-snapshot authorization await used by consumers."""
        return self.is_current()

    def entity_id(self, property_key: str) -> str:
        """Read an exact address without treating it as dispatch authorization."""
        try:
            return self.addresses[property_key]
        except KeyError as err:
            raise VerifiedOperationRejected("capability_not_authorized") from err

    async def async_call(
        self,
        property_key: str,
        service: str,
        service_data: dict[str, object] | None = None,
    ) -> None:
        """Record the call and reject any stale follow-up."""
        self.addresses_used.append(self.entity_id(property_key))
        self.calls.append((property_key, service, service_data))
        if len(self.calls) == self.invalidate_at:
            self.current = False
            raise VerifiedOperationRejected("operation_invalidated_after_dispatch")


def _deps() -> KeymasterServiceDeps:
    """Build runtime dependencies with no real services or notifications."""

    async def sleep(_delay: float) -> None:
        """Skip retry delays in unit-level service tests."""
        return None

    def apply_buffer(start, end, _before, _after, _coordinator):
        """Keep date values unchanged for this service-helper fixture."""
        return start, end

    def ensure_datetime(value, _coordinator):
        """Return the input value without timezone conversion."""
        return value

    return KeymasterServiceDeps(
        sleep=sleep,
        track_state_change=MagicMock(return_value=lambda: None),
        confirmation_timeout=0.01,
        create_notification=MagicMock(),
        dismiss_notification=MagicMock(),
        logger=MagicMock(),
        add_call=MagicMock(),
        check_gather_results=MagicMock(),
        raise_first_gather_exception=MagicMock(),
        apply_buffer=apply_buffer,
        ensure_datetime=ensure_datetime,
        trim_name=lambda value, _limit: value,
        is_cleared_text=lambda value: value == "",
        is_unreadable_text=lambda value: value in {"unknown", "unavailable"},
    )


def _set_operation_inputs():
    """Create one synthetic set request and a legacy-name distractor."""
    coordinator = MagicMock()
    coordinator.hass.services.async_call = AsyncMock()
    coordinator.lockname = "old_name_must_not_route"
    coordinator.event_prefix = None
    coordinator.trim_names = False
    coordinator.code_buffer_before = 0
    coordinator.code_buffer_after = 0
    coordinator.max_name_length = 32
    coordinator.event_overrides.verify_slot_ownership.return_value = True
    event = SimpleNamespace(
        extra_state_attributes={
            "slot_name": "Guest",
            "slot_code": "1234",
            "start": datetime(2026, 11, 25, 16, tzinfo=dt_util.UTC),
            "end": datetime(2026, 11, 27, 11, tzinfo=dt_util.UTC),
        }
    )
    return coordinator, event


async def test_set_routes_each_step_to_exact_renamed_capability_without_fallback() -> (
    None
):
    """Verified writes use current property addresses, never lockname guesses."""
    coordinator, event = _set_operation_inputs()
    intent = _Intent()

    result = await keymaster_services.async_fire_set_code(
        coordinator,
        event,
        21,
        _deps(),
        verified_intent=cast(VerifiedOperationIntent, intent),
    )

    assert result.unconfirmed
    assert [call[0] for call in intent.calls] == [
        "switch.code_slots:21.enabled",
        "switch.code_slots:21.accesslimit_date_range_enabled",
        "datetime.code_slots:21.accesslimit_date_range_end",
        "datetime.code_slots:21.accesslimit_date_range_start",
        "text.code_slots:21.pin",
        "text.code_slots:21.name",
        "switch.code_slots:21.enabled",
    ]
    assert intent.addresses_used == [
        "switch.renamed_enabled",
        "switch.renamed_date_enabled",
        "datetime.renamed_end",
        "datetime.renamed_start",
        "text.renamed_pin",
        "text.renamed_name",
        "switch.renamed_enabled",
    ]
    assert coordinator.hass.services.async_call.await_count == 0


@pytest.mark.parametrize("invalidate_at", range(1, 8))
async def test_set_stops_after_each_inter_await_invalidation(
    invalidate_at: int,
) -> None:
    """Every dispatch boundary stops before the next stale capability."""
    coordinator, event = _set_operation_inputs()
    intent = _Intent(invalidate_at=invalidate_at)
    deps = _deps()

    result = await keymaster_services.async_fire_set_code(
        coordinator,
        event,
        21,
        deps,
        verified_intent=cast(VerifiedOperationIntent, intent),
    )

    assert result.unconfirmed
    assert len(intent.calls) == invalidate_at
    assert cast(MagicMock, deps.add_call).call_count == 0
    assert coordinator.hass.services.async_call.await_count == 0


async def test_set_wrong_slot_intent_never_dispatches() -> None:
    """An intent captured for another slot is not a routing fallback."""
    coordinator, event = _set_operation_inputs()
    intent = _Intent()
    intent.slot = 20

    result = await keymaster_services.async_fire_set_code(
        coordinator,
        event,
        21,
        _deps(),
        verified_intent=cast(VerifiedOperationIntent, intent),
    )

    assert result.unconfirmed
    assert intent.calls == []
    coordinator.hass.services.async_call.assert_not_awaited()


@pytest.mark.parametrize(
    "missing_property",
    (
        "button.code_slots:21.reset",
        "text.code_slots:21.name",
        "text.code_slots:21.pin",
    ),
)
async def test_clear_requires_same_slot_confirmation_capabilities_before_reset(
    missing_property: str,
) -> None:
    """An incomplete reset intent cannot press even an available reset button."""
    coordinator = MagicMock()
    coordinator.hass.services.async_call = AsyncMock()
    coordinator.lockname = "old_name_must_not_route"
    intent = _Intent()
    intent.addresses.pop(missing_property)

    result = await keymaster_services.async_fire_clear_code(
        coordinator,
        21,
        expected_name=None,
        deps=_deps(),
        verified_intent=cast(VerifiedOperationIntent, intent),
    )

    assert result.unconfirmed
    assert intent.calls == []
    assert coordinator.hass.services.async_call.await_count == 0


@pytest.mark.parametrize(
    "missing_property",
    (
        "switch.code_slots:21.enabled",
        "switch.code_slots:21.accesslimit_date_range_enabled",
        "datetime.code_slots:21.accesslimit_date_range_start",
        "datetime.code_slots:21.accesslimit_date_range_end",
        "text.code_slots:21.pin",
        "text.code_slots:21.name",
    ),
)
async def test_set_requires_all_same_slot_capabilities_before_dispatch(
    missing_property: str,
) -> None:
    """No partial set starts if a required write capability is unavailable."""
    coordinator, event = _set_operation_inputs()
    intent = _Intent()
    intent.addresses.pop(missing_property)

    result = await keymaster_services.async_fire_set_code(
        coordinator,
        event,
        21,
        _deps(),
        verified_intent=cast(VerifiedOperationIntent, intent),
    )

    assert result.unconfirmed
    assert intent.calls == []
    coordinator.hass.services.async_call.assert_not_awaited()


async def test_date_updates_route_to_verified_start_and_end_addresses() -> None:
    """Date-only amendment uses exact same-slot dates and preserves no code."""
    coordinator = MagicMock()
    coordinator.hass.services.async_call = AsyncMock()
    coordinator.lockname = "old_name_must_not_route"
    coordinator.code_buffer_before = 0
    coordinator.code_buffer_after = 0
    coordinator.event_overrides.verify_slot_ownership.return_value = True
    intent = _Intent()
    event = SimpleNamespace(
        extra_state_attributes={
            "slot_name": "Guest",
            "start": datetime(2026, 11, 25, 16, tzinfo=dt_util.UTC),
            "end": datetime(2026, 11, 27, 11, tzinfo=dt_util.UTC),
        }
    )

    result = await keymaster_services.async_fire_update_times(
        coordinator,
        event,
        21,
        _deps(),
        verified_intent=cast(VerifiedOperationIntent, intent),
    )

    assert result.unconfirmed
    assert [call[0] for call in intent.calls] == [
        "datetime.code_slots:21.accesslimit_date_range_end",
        "datetime.code_slots:21.accesslimit_date_range_start",
    ]
    assert intent.addresses_used == [
        "datetime.renamed_end",
        "datetime.renamed_start",
    ]
    assert coordinator.hass.services.async_call.await_count == 0


async def test_date_update_stops_after_invalidation_between_end_and_start() -> None:
    """A stale operation never sends its second datetime write."""
    coordinator = MagicMock()
    coordinator.hass.services.async_call = AsyncMock()
    coordinator.lockname = "old_name_must_not_route"
    coordinator.code_buffer_before = 0
    coordinator.code_buffer_after = 0
    coordinator.event_overrides.verify_slot_ownership.return_value = True
    intent = _Intent(invalidate_at=1)
    event = SimpleNamespace(
        extra_state_attributes={
            "slot_name": "Guest",
            "start": datetime(2026, 11, 25, 16, tzinfo=dt_util.UTC),
            "end": datetime(2026, 11, 27, 11, tzinfo=dt_util.UTC),
        }
    )

    result = await keymaster_services.async_fire_update_times(
        coordinator,
        event,
        21,
        _deps(),
        verified_intent=cast(VerifiedOperationIntent, intent),
    )

    assert result.unconfirmed
    assert [call[0] for call in intent.calls] == [
        "datetime.code_slots:21.accesslimit_date_range_end"
    ]
    assert coordinator.hass.services.async_call.await_count == 0


async def test_missing_current_protection_never_creates_verified_intent(hass) -> None:
    """A passive or absent protected store cannot authorize a hardware route."""
    from custom_components.rental_control.keymaster_operations import (
        create_verified_operation_intent,
    )

    assert (
        create_verified_operation_intent(
            hass,
            "rc-entry",
            slot=21,
            owner_identity_key="reservation",
            required_properties=("text.code_slots:21.name",),
        )
        is None
    )


async def test_active_verified_lifecycle_blocks_legacy_service_fallback(
    monkeypatch,
) -> None:
    """Verified identity mode never falls back to a constructed lock slug."""
    monkeypatch.setattr(
        keymaster_service_helpers,
        "get_keymaster_lifecycle",
        lambda *_args: object(),
    )
    coordinator, event = _set_operation_inputs()
    coordinator.config_entry.entry_id = "rc-entry"
    coordinator.hass.data = {"rental_control": {"rc-entry": {}}}

    result = await keymaster_services.async_fire_set_code(
        coordinator, event, 21, _deps()
    )

    assert result.unconfirmed
    coordinator.hass.services.async_call.assert_not_awaited()
