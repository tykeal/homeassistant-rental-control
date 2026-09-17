# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for forced re-issue allocator helpers."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime

from custom_components.rental_control.allocator.issuance import observed_alias_key
from custom_components.rental_control.allocator.models import AdoptionRequest
from custom_components.rental_control.allocator.reissue import forced_release_hold_key
from custom_components.rental_control.allocator.reissue import is_forced_release_hold
from custom_components.rental_control.reconciliation import make_reservation_fingerprint


def test_forced_release_hold_key_round_trips() -> None:
    """A rendered hold key is structurally recognized as a hold."""
    key = forced_release_hold_key("identity-a", "entry-a", "frontdoor", 12)

    assert key == "identity-a:reissued:entry-a:frontdoor:12"
    assert is_forced_release_hold(key)


def test_forced_release_hold_key_renders_lockless_target() -> None:
    """Lockless owners use explicit none markers for physical metadata."""
    key = forced_release_hold_key("identity-a", "entry-a", None, None)

    assert key == "identity-a:reissued:entry-a:none:none"
    assert is_forced_release_hold(key)


def test_forced_release_hold_key_is_deterministic() -> None:
    """Rendering the same target twice produces the same hold key."""
    first = forced_release_hold_key("identity-a", "entry-a", "frontdoor", 12)
    second = forced_release_hold_key("identity-a", "entry-a", "frontdoor", 12)

    assert first == second


def test_hold_detection_requires_structural_namespace() -> None:
    """Substring matches and malformed hold-like keys are rejected."""
    assert not is_forced_release_hold("identity-a-reissued-entry-a-front-12")
    assert not is_forced_release_hold("identity-a:observed:entry-a:front:12")
    assert not is_forced_release_hold("identity-a:reissued:entry-a:front")
    assert not is_forced_release_hold("identity-a:reissued:entry-a:front:12:extra")


def test_hold_key_does_not_collide_with_reservation_or_alias_keys() -> None:
    """The hold namespace is disjoint from reservation and observed aliases."""
    start = datetime(2026, 9, 17, 15, tzinfo=UTC)
    end = datetime(2026, 9, 19, 10, tzinfo=UTC)
    identity = make_reservation_fingerprint("entry-a", "Guest Name", start, end)
    hold = forced_release_hold_key(identity, "entry-a", "front-door", 12)
    alias = observed_alias_key(
        AdoptionRequest(
            entry_id="entry-a",
            identity_key=identity,
            code="1234",
            code_length=4,
            lockname="front-door",
            slot=12,
        )
    )

    assert hold != identity
    assert hold != alias
    assert not is_forced_release_hold(identity)
    assert not is_forced_release_hold(alias)
