# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Privacy-safe error reporting for cache-only coordinator storage."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.rental_control.coordinator_helpers import coordinator_store_shell

_PRIVATE = "Guest Example PIN 9384 entry-private"


class _BrokenStore:
    """Storage double that raises an error containing private test data."""

    async def async_load(self) -> Any:
        """Raise while loading cached slot metadata."""
        raise OSError(_PRIVATE)

    async def async_save(self, _data: Any) -> None:
        """Raise while saving cached slot metadata."""
        raise OSError(_PRIVATE)


class _Coordinator(coordinator_store_shell.CoordinatorStoreMixin):
    """Minimal coordinator shell for exercising its cache methods."""

    hass = SimpleNamespace()
    _entry_id = "entry-private"
    lockname = "private-door"
    start_slot = 10
    max_events = 4
    event_prefix = ""
    trim_names = False
    max_name_length = 32

    def __init__(self) -> None:
        """Initialize the cache-bearing fields used by the mixin."""
        self._store: _BrokenStore | None = None
        self._slot_mappings: dict[str, Any] = {}


async def test_cache_load_error_does_not_log_private_data(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Load errors are reported without exception text or entry identifiers."""
    monkeypatch.setattr(
        coordinator_store_shell,
        "_coordinator_module",
        lambda: SimpleNamespace(Store=lambda *_args, **_kwargs: _BrokenStore()),
    )
    coordinator = _Coordinator()

    await coordinator._async_load_slot_store_impl()

    assert "Ignoring unreadable Rental Control slot cache" in caplog.text
    assert _PRIVATE not in caplog.text
    assert "entry-private" not in caplog.text
    assert "private-door" not in caplog.text


async def test_cache_save_error_does_not_log_private_data(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Save errors are reported without exception text or entry identifiers."""
    coordinator = _Coordinator()
    coordinator._store = _BrokenStore()

    await coordinator._async_save_slot_store_impl()

    assert "Failed to save Rental Control slot cache" in caplog.text
    assert _PRIVATE not in caplog.text
    assert "entry-private" not in caplog.text
    assert "private-door" not in caplog.text
