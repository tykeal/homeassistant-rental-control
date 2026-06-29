# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Patch-boundary tests for util, event_overrides, and coordinator wrappers."""

from __future__ import annotations

from unittest.mock import AsyncMock
from unittest.mock import MagicMock

from custom_components.rental_control import coordinator as coordinator_module
from custom_components.rental_control import event_overrides
from custom_components.rental_control import util
from custom_components.rental_control.helpers import OperationResult


async def test_event_overrides_set_wrapper_calls_util_at_runtime(
    monkeypatch,
) -> None:
    """Unpatched event_overrides set wrapper observes util-level patches."""
    patched = AsyncMock(
        return_value=OperationResult(kind="set", slot=10, confirmed=True)
    )
    monkeypatch.setattr(util, "async_fire_set_code", patched)

    result = await event_overrides.async_fire_set_code("coordinator", "event", 10)

    assert result.confirmed is True
    patched.assert_awaited_once_with("coordinator", "event", 10)


async def test_event_overrides_clear_wrapper_calls_util_at_runtime(
    monkeypatch,
) -> None:
    """Unpatched event_overrides clear wrapper observes util-level patches."""
    patched = AsyncMock(
        return_value=OperationResult(kind="clear", slot=10, confirmed=True)
    )
    monkeypatch.setattr(util, "async_fire_clear_code", patched)

    result = await event_overrides.async_fire_clear_code("coordinator", 10)

    assert result.confirmed is True
    patched.assert_awaited_once_with("coordinator", 10)


async def test_event_overrides_update_wrapper_calls_util_at_runtime(
    monkeypatch,
) -> None:
    """Unpatched event_overrides update wrapper observes util-level patches."""
    patched = AsyncMock(
        return_value=OperationResult(kind="update_times", slot=10, confirmed=True)
    )
    monkeypatch.setattr(util, "async_fire_update_times", patched)

    result = await event_overrides.async_fire_update_times("coordinator", "event", 10)

    assert result.confirmed is True
    patched.assert_awaited_once_with("coordinator", "event", 10)


def test_event_overrides_identity_wrapper_calls_util_at_runtime(monkeypatch) -> None:
    """Unpatched event_overrides identity wrapper observes util-level patches."""
    patched = MagicMock(return_value=[])
    monkeypatch.setattr(util, "get_event_identities", patched)

    assert event_overrides.get_event_identities("coordinator") == []
    patched.assert_called_once_with("coordinator")


async def test_coordinator_clear_wrapper_calls_util_at_runtime(monkeypatch) -> None:
    """Unpatched coordinator clear wrapper observes util-level patches."""
    patched = AsyncMock(
        return_value=OperationResult(kind="clear", slot=10, confirmed=True)
    )
    monkeypatch.setattr(util, "async_fire_clear_code", patched)

    result = await coordinator_module.async_fire_clear_code("coordinator", 10)

    assert result.confirmed is True
    patched.assert_awaited_once_with("coordinator", 10, None)


def test_coordinator_add_call_wrapper_calls_util_at_runtime(monkeypatch) -> None:
    """Coordinator add_call remains a visible util-delegating patch target."""
    patched = MagicMock(return_value=["call"])
    monkeypatch.setattr(util, "add_call", patched)

    assert coordinator_module.add_call(
        "hass", [], "domain", "service", "target", {}
    ) == ["call"]
    patched.assert_called_once_with("hass", [], "domain", "service", "target", {})
