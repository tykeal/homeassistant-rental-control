# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""The re-issue preview honours exclusions without ever changing them."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.rental_control.allocator import reissue_preview
from custom_components.rental_control.allocator.models import CycleObservation
from custom_components.rental_control.const import CONF_EXCLUDED_SLOTS
from custom_components.rental_control.coordinator_helpers import reissue
from custom_components.rental_control.coordinator_helpers import slot_exclusions

_DEAD_SLOT = 11


def _capture(seen: list[frozenset[int]]) -> Any:
    """Return an adoption-check stand-in recording the excluded set."""

    def _adoption_complete(*_args: Any, excluded: frozenset[int] = frozenset()) -> bool:
        """Record the excluded set the preview forwarded."""
        seen.append(excluded)
        return True

    return _adoption_complete


@pytest.fixture(autouse=True)
def _isolate_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the coordinator-facing collaborators with inert stubs."""
    monkeypatch.setattr(
        reissue_preview, "observe_managed_slots_without_mutation", lambda _c: []
    )
    monkeypatch.setattr(
        reissue_preview, "build_preview_request", lambda *_a, **_k: "request"
    )
    monkeypatch.setattr(
        reissue_preview.code_allocation,
        "build_cycle_observation",
        lambda entry_id, lockname, _slots: CycleObservation(
            entry_id=entry_id,
            lockname=lockname,
            managed_slots=frozenset({10, _DEAD_SLOT}),
            observed_codes={},
            unreadable_slots=frozenset({_DEAD_SLOT}),
        ),
    )
    monkeypatch.setattr(
        reissue_preview, "_adoption_would_complete", lambda *_a, **_k: True
    )


def _coordinator(excluded: list[dict[str, Any]]) -> SimpleNamespace:
    """Build a coordinator stand-in whose entry carries the exclusions."""
    entry = SimpleNamespace(
        entry_id="entry-a", data={CONF_EXCLUDED_SLOTS: excluded}, options={}
    )
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_get_entry=lambda entry_id: entry if entry_id == "entry-a" else None,
            async_entries=lambda _domain=None: [entry],
            async_update_entry=_unexpected,
        )
    )
    return SimpleNamespace(hass=hass, code_length=4)


def _unexpected(*args: Any, **kwargs: Any) -> None:
    """Fail if the preview path tries to persist anything."""
    raise AssertionError("the preview path must not write config entry data")


def _allocator() -> SimpleNamespace:
    """Build an allocator stand-in that passes the pre-observation gates."""
    return SimpleNamespace(
        _pending_adoption=set(),
        _registry_lost=False,
        _registry=SimpleNamespace(records={}),
        async_preview_reissue=_preview_ok,
    )


async def _preview_ok(_request: Any) -> str:
    """Stand in for a successful allocator preview."""
    return "preview"


def _target() -> reissue.ReissueTarget:
    """Build a re-issue target for the readable slot."""
    return reissue.ReissueTarget(
        entry_id="entry-a",
        identity_key=None,
        lockname="front",
        slot=10,
        target_key="front:10",
        checked_in=False,
    )


async def test_preview_refuses_while_the_dead_slot_is_not_excluded() -> None:
    """Without an exclusion the preview still reports unaccounted slots."""
    _, preview = await reissue_preview.preview_request_and_result(
        _allocator(), _coordinator([]), _target()
    )

    assert preview.reason == "unaccounted_slots"


async def test_preview_proceeds_once_the_dead_slot_is_excluded() -> None:
    """An exclusion releases the preview gate exactly as it does issuance."""
    _, preview = await reissue_preview.preview_request_and_result(
        _allocator(), _coordinator([{"slot": _DEAD_SLOT}]), _target()
    )

    assert preview == "preview"


async def test_preview_never_revalidates_or_notifies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The preview reads exclusions only; the cycle owns their lifecycle."""
    calls: list[str] = []

    def _record(name: str) -> Any:
        """Record a lifecycle call the preview must never make."""

        def _call(*_args: Any, **_kwargs: Any) -> Any:
            """Record this lifecycle call and return an inert result."""
            calls.append(name)
            return ({}, {}) if name == "revalidate" else False

        return _call

    monkeypatch.setattr(slot_exclusions, "revalidate", _record("revalidate"))
    monkeypatch.setattr(slot_exclusions, "persist_excluded_slots", _record("persist"))

    await reissue_preview.preview_request_and_result(
        _allocator(), _coordinator([{"slot": _DEAD_SLOT}]), _target()
    )

    assert calls == []


async def test_preview_forwards_exclusions_to_the_adoption_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fourth call site forwards the set instead of its empty default."""
    monkeypatch.undo()
    seen: list[frozenset[int]] = []
    monkeypatch.setattr(
        reissue_preview,
        "observe_managed_slots_without_mutation",
        lambda _c: [],
    )
    monkeypatch.setattr(
        reissue_preview, "build_preview_request", lambda *_a, **_k: "request"
    )
    monkeypatch.setattr(
        reissue_preview.code_allocation,
        "build_cycle_observation",
        lambda entry_id, lockname, _slots: CycleObservation(
            entry_id=entry_id,
            lockname=lockname,
            managed_slots=frozenset({10, _DEAD_SLOT}),
            observed_codes={},
            unreadable_slots=frozenset({_DEAD_SLOT}),
        ),
    )
    monkeypatch.setattr(
        reissue_preview.code_allocation, "build_adoption_requests", lambda *_a: []
    )
    monkeypatch.setattr(reissue_preview, "preview_reservations", lambda *_a: [])
    monkeypatch.setattr(
        reissue_preview.code_allocation,
        "_adoption_complete",
        _capture(seen),
    )

    await reissue_preview.preview_request_and_result(
        _allocator(), _coordinator([{"slot": _DEAD_SLOT}]), _target()
    )

    assert seen == [frozenset({_DEAD_SLOT})]
