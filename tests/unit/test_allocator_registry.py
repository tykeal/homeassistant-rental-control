# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the pure allocation registry."""

from __future__ import annotations

import pytest

from custom_components.rental_control.allocator.models import AllocationOrigin
from custom_components.rental_control.allocator.models import AllocationOwner
from custom_components.rental_control.allocator.models import AllocationRecord
from custom_components.rental_control.allocator.models import AllocationRequest
from custom_components.rental_control.allocator.registry import AllocationRegistry

_NOW = "2026-09-16T12:00:00+00:00"


def _request(identity: str, preferred: str = "1234") -> AllocationRequest:
    """Build an allocation request for registry tests."""
    return AllocationRequest(
        entry_id="entry-a",
        identity_key=identity,
        preferred_code=preferred,
        code_length=len(preferred),
        lockname="front",
        slot=1,
    )


def test_allocate_lookup_and_release() -> None:
    """A registry can allocate, look up, and release an owner."""
    registry = AllocationRegistry(code_ref_salt="salt")
    record = registry.allocate(
        _request("identity-a"), "1234", AllocationOrigin.PREFERRED, "ref", _NOW
    )

    assert record.code == "1234"
    assert registry.code_for_identity("identity-a") == "1234"
    assert registry.release("identity-a") is True
    assert registry.code_for_identity("identity-a") is None
    assert registry.records == {}


def test_idempotent_re_request_returns_existing_record() -> None:
    """A known identity keeps its existing code without mutation."""
    registry = AllocationRegistry(code_ref_salt="salt")
    first = registry.allocate(
        _request("identity-a"), "1234", AllocationOrigin.PREFERRED, "ref", _NOW
    )
    second = registry.allocate(
        _request("identity-a", "5678"),
        "5678",
        AllocationOrigin.COLLISION_RESOLVED,
        "newref",
        _NOW,
    )

    assert second is first
    assert registry.code_for_identity("identity-a") == "1234"
    assert first.owners[0].origin is AllocationOrigin.PREFERRED


def test_conflict_recording_makes_code_unavailable() -> None:
    """A code with two owners is retained but cannot be reissued."""
    registry = AllocationRegistry(code_ref_salt="salt")
    registry.allocate(
        _request("identity-a"), "1234", AllocationOrigin.PREFERRED, "ref", _NOW
    )
    registry.add_owner(
        "1234",
        4,
        AllocationOwner(
            entry_id="entry-b",
            identity_key="identity-b",
            origin=AllocationOrigin.ADOPTED,
            lockname="front",
            slot=2,
            lock_observed=True,
            first_seen=_NOW,
            last_seen=_NOW,
        ),
        "ref",
        _NOW,
    )

    assert len(registry.conflicts()) == 1
    assert not registry.is_available("1234", 4, "identity-c")


def test_code_length_filtering_blocks_other_lengths() -> None:
    """Records are only available to requests of their stored length."""
    registry = AllocationRegistry(code_ref_salt="salt")
    registry.allocate(
        _request("identity-a"), "1234", AllocationOrigin.PREFERRED, "ref", _NOW
    )

    assert not registry.is_available("1234", 4, "identity-b")
    with pytest.raises(ValueError, match="code must be decimal"):
        registry.is_available("1234", 6, "identity-b")


def test_rejects_malformed_record_inputs() -> None:
    """Malformed codes and duplicate identities are rejected."""
    with pytest.raises(ValueError, match="code must be decimal"):
        AllocationRecord(
            code="12ab",
            code_ref="ref",
            encoding_salt_value="entry-a",
            code_length=4,
            owners=[
                AllocationOwner(
                    entry_id="entry-a",
                    identity_key="identity-a",
                    origin=AllocationOrigin.PREFERRED,
                )
            ],
        )
    with pytest.raises(ValueError, match="multiple records"):
        AllocationRegistry(
            records={
                "1234": AllocationRecord(
                    code="1234",
                    code_ref="ref-a",
                    encoding_salt_value="entry-a",
                    code_length=4,
                    owners=[
                        AllocationOwner(
                            entry_id="entry-a",
                            identity_key="identity-a",
                            origin=AllocationOrigin.PREFERRED,
                        )
                    ],
                ),
                "5678": AllocationRecord(
                    code="5678",
                    code_ref="ref-b",
                    encoding_salt_value="entry-b",
                    code_length=4,
                    owners=[
                        AllocationOwner(
                            entry_id="entry-b",
                            identity_key="identity-a",
                            origin=AllocationOrigin.ADOPTED,
                        )
                    ],
                ),
            }
        )


def test_rejects_duplicate_identity_in_same_record() -> None:
    """A single record cannot contain the same owner identity twice."""
    with pytest.raises(ValueError, match="multiple records"):
        AllocationRegistry(
            records={
                "1234": AllocationRecord(
                    code="1234",
                    code_ref="ref",
                    encoding_salt_value="entry-a",
                    code_length=4,
                    owners=[
                        AllocationOwner(
                            entry_id="entry-a",
                            identity_key="identity-a",
                            origin=AllocationOrigin.PREFERRED,
                        ),
                        AllocationOwner(
                            entry_id="entry-a",
                            identity_key="identity-a",
                            origin=AllocationOrigin.ADOPTED,
                        ),
                    ],
                )
            }
        )


@pytest.mark.parametrize(
    ("lockname", "slot"),
    [
        ("front", None),
        (None, 1),
        ("front", True),
    ],
)
def test_rejects_invalid_physical_owner_pair(
    lockname: str | None, slot: int | None
) -> None:
    """Physical owner metadata must be complete and non-boolean."""
    with pytest.raises(ValueError, match="slot|lockname"):
        AllocationOwner(
            entry_id="entry-a",
            identity_key="identity-a",
            origin=AllocationOrigin.ADOPTED,
            lockname=lockname,
            slot=slot,
        )


@pytest.mark.parametrize("code_length", [True, 1.0, 0])
def test_rejects_invalid_code_lengths(code_length: object) -> None:
    """Records and registry APIs accept only positive integer lengths."""
    with pytest.raises(ValueError, match="code_length"):
        AllocationRecord(
            code="1",
            code_ref="ref",
            encoding_salt_value="entry-a",
            code_length=code_length,  # type: ignore[arg-type]
            owners=[
                AllocationOwner(
                    entry_id="entry-a",
                    identity_key="identity-a",
                    origin=AllocationOrigin.PREFERRED,
                )
            ],
        )

    with pytest.raises(ValueError, match="code_length"):
        AllocationRegistry().is_available(
            "1",
            code_length,  # type: ignore[arg-type]
            "identity-a",
        )
