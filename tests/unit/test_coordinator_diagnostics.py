# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Tests for the pure diagnostics coordinator helpers."""

from __future__ import annotations

from custom_components.rental_control.coordinator_helpers import diagnostics


def test_scrub_codes_removes_code_keys_recursively() -> None:
    """Code-bearing keys are stripped from nested structures."""
    payload = {
        "slot_code": "1234",
        "nested": {"slot_code": "5678", "name": "Guest"},
        "items": [{"slot_code": "0000", "ok": True}],
    }
    scrubbed = diagnostics.scrub_codes(payload)
    assert "slot_code" not in scrubbed
    assert "slot_code" not in scrubbed["nested"]
    assert scrubbed["nested"]["name"] == "Guest"
    assert scrubbed["items"][0] == {"ok": True}


def test_build_reconciliation_diagnostics_empty_without_plan() -> None:
    """No plan and no overrides snapshot yields an empty dict."""
    assert diagnostics.build_reconciliation_diagnostics(None, None) == {}


def test_build_reconciliation_diagnostics_includes_overrides() -> None:
    """An overrides snapshot is embedded and scrubbed."""
    result = diagnostics.build_reconciliation_diagnostics(
        None, {"slot_code": "1234", "count": 2}
    )
    assert "slot_code" not in result["event_overrides"]
    assert result["event_overrides"]["count"] == 2
