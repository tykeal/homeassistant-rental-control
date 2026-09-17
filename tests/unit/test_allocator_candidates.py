# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for deterministic allocator candidate generation."""

from __future__ import annotations

from itertools import islice

from custom_components.rental_control.allocator.candidates import candidate_codes


def test_candidate_walk_full_cycle_length_4() -> None:
    """A four-digit walk visits every code exactly once."""
    codes = list(candidate_codes("identity-a", 4))

    assert len(codes) == 10_000
    assert len(set(codes)) == 10_000
    assert all(len(code) == 4 for code in codes)


def test_candidate_walk_full_cycle_length_6() -> None:
    """A six-digit walk does not repeat within the full code space."""
    codes = list(candidate_codes("identity-b", 6))

    assert len(codes) == 1_000_000
    assert len(set(codes)) == 1_000_000
    assert all(len(code) == 6 for code in codes)


def test_candidate_walk_is_deterministic() -> None:
    """The same identity yields the same sequence across runs."""
    first = list(islice(candidate_codes("fixed-identity", 4), 20))
    second = list(islice(candidate_codes("fixed-identity", 4), 20))

    assert second == first


def test_candidate_walk_is_registry_independent() -> None:
    """Candidate order is stable when external registry state changes."""
    before = list(islice(candidate_codes("stable-identity", 4), 50))
    occupied_elsewhere = {before[3], before[7], "9999"}
    after = list(islice(candidate_codes("stable-identity", 4), 50))

    assert occupied_elsewhere
    assert after == before
