# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Pure in-memory registry for shared door-code allocations."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field

from .models import AllocationOrigin
from .models import AllocationOwner
from .models import AllocationRecord
from .models import AllocationRequest


def _validate_code(code: str, code_length: int) -> None:
    """Raise when a code is not decimal digits of the requested length."""
    if not isinstance(code_length, int) or isinstance(code_length, bool):
        raise ValueError("code_length must be a positive integer")
    if code_length < 1:
        raise ValueError("code_length must be positive")
    if not code.isdecimal() or len(code) != code_length:
        raise ValueError("code must be decimal digits matching code_length")


@dataclass(slots=True)
class AllocationRegistry:
    """Plain-code allocation registry with a derived identity index."""

    records: dict[str, AllocationRecord] = field(default_factory=dict)
    code_ref_salt: str = ""
    by_identity: dict[str, str] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        """Validate records and rebuild the derived identity index."""
        self.rebuild_index()

    def rebuild_index(self) -> None:
        """Rebuild and validate the identity-to-code index."""
        by_identity: dict[str, str] = {}
        for code, record in self.records.items():
            if code != record.code:
                raise ValueError("record key must match record code")
            _validate_code(record.code, record.code_length)
            for owner in record.owners:
                existing = by_identity.get(owner.identity_key)
                if existing is not None:
                    raise ValueError("identity_key owned by multiple records")
                by_identity[owner.identity_key] = code
        self.by_identity = by_identity

    def code_for_identity(self, identity_key: str) -> str | None:
        """Return the code currently owned by an identity, if any."""
        return self.by_identity.get(identity_key)

    def record_for_identity(self, identity_key: str) -> AllocationRecord | None:
        """Return the allocation record owned by an identity, if any."""
        code = self.code_for_identity(identity_key)
        return self.records.get(code) if code is not None else None

    def is_available(
        self, code: str, code_length: int, identity_key: str | None = None
    ) -> bool:
        """Return whether a code can be issued to the requested identity."""
        _validate_code(code, code_length)
        record = self.records.get(code)
        if record is None:
            return True
        if record.code_length != code_length or len(record.owners) != 1:
            return False
        return (
            identity_key is not None and record.owners[0].identity_key == identity_key
        )

    def add_owner(
        self,
        code: str,
        code_length: int,
        owner: AllocationOwner,
        code_ref: str,
        now: str,
    ) -> AllocationRecord:
        """Add an owner to a code, recording conflicts on the same code."""
        _validate_code(code, code_length)
        owned_code = self.by_identity.get(owner.identity_key)
        if owned_code is not None and owned_code != code:
            raise ValueError("identity_key already owns a different code")
        record = self.records.get(code)
        if record is None:
            record = AllocationRecord(
                code=code,
                code_ref=code_ref,
                encoding_salt_source="entry_id",
                encoding_salt_value=owner.entry_id,
                code_length=code_length,
                owners=[owner],
                created_at=now,
                updated_at=now,
            )
            self.records[code] = record
            self.by_identity[owner.identity_key] = code
            return record
        if record.code_length != code_length:
            raise ValueError("existing record code_length does not match")
        for existing in record.owners:
            if existing.identity_key == owner.identity_key:
                existing.entry_id = owner.entry_id
                existing.origin = owner.origin
                existing.lockname = owner.lockname
                existing.slot = owner.slot
                existing.lock_observed = owner.lock_observed
                existing.last_seen = owner.last_seen or now
                record.updated_at = now
                return record
        record.owners.append(owner)
        record.updated_at = now
        self.by_identity[owner.identity_key] = code
        return record

    def allocate(
        self,
        request: AllocationRequest,
        code: str,
        origin: AllocationOrigin,
        code_ref: str,
        now: str,
    ) -> AllocationRecord:
        """Record an issued code or return the existing idempotent owner."""
        existing = self.record_for_identity(request.identity_key)
        if existing is not None:
            return existing
        if not self.is_available(code, request.code_length, request.identity_key):
            raise ValueError("code is not available")
        owner = AllocationOwner(
            entry_id=request.entry_id,
            identity_key=request.identity_key,
            origin=origin,
            lockname=request.lockname,
            slot=request.slot,
            lock_observed=False,
            first_seen=now,
            last_seen=now,
        )
        return self.add_owner(code, request.code_length, owner, code_ref, now)

    def release(self, identity_key: str) -> bool:
        """Remove an identity owner and delete empty records."""
        code = self.by_identity.get(identity_key)
        if code is None:
            return False
        record = self.records[code]
        record.owners = [
            owner for owner in record.owners if owner.identity_key != identity_key
        ]
        self.by_identity.pop(identity_key, None)
        if not record.owners:
            del self.records[code]
        return True

    def conflicts(self) -> list[AllocationRecord]:
        """Return records with more than one owner."""
        return [record for record in self.records.values() if len(record.owners) > 1]
