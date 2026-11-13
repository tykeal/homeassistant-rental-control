# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Pure in-memory registry for shared door-code allocations."""

from __future__ import annotations

from collections.abc import Collection
from collections.abc import Iterable
from dataclasses import dataclass
from dataclasses import field

from .models import AllocationOrigin
from .models import AllocationOwner
from .models import AllocationRecord
from .models import AllocationRequest


def is_observed_alias(identity_key: str) -> bool:
    """Return whether an identity key is a slot's observed-code alias.

    An alias records that a lock slot physically presents a code; it is
    not a reservation the code was issued to. It keeps the code out of
    issuance while the lock still presents it, but it is never an
    issuance owner, so it neither forms nor blocks a shared-code
    conflict.
    """
    parts = identity_key.split(":")
    return len(parts) == 5 and parts[1] == "observed"


def _is_physical_only(identity_key: str) -> bool:
    """Return whether an owner only marks a code still present on a lock.

    Observed aliases and forced-release holds both keep a code reserved
    while a lock slot presents it, but neither is a guest the code is
    issued to: a hold's booking has already moved to a replacement code.
    """
    parts = identity_key.split(":")
    return len(parts) == 5 and parts[1] in ("observed", "reissued")


def issuance_owners(record: AllocationRecord) -> list[AllocationOwner]:
    """Return the owners a record's code was actually issued to."""
    return [
        owner for owner in record.owners if not _is_physical_only(owner.identity_key)
    ]


#: One physical presence of a code: ``(entry_id, lockname, slot)``.
#: A lockless owner programs nothing, so its identity stands in for
#: the lock and slot and keeps two lockless bookings distinct.
SlotKey = tuple[str, str, str]


def physical_slot(owner: AllocationOwner) -> SlotKey:
    """Return the physical presence an owner's code stands for.

    A lock slot holds one code at a time, so every owner naming the
    same entry, lock and slot stands for the same code in the same
    place, however many booking identities have adopted it there.
    """
    if owner.lockname is not None and owner.slot is not None:
        return (owner.entry_id, owner.lockname, str(owner.slot))
    return (owner.entry_id, "", owner.identity_key)


def physical_slots(owners: Iterable[AllocationOwner]) -> frozenset[SlotKey]:
    """Return the distinct physical presences of the given owners."""
    return frozenset(physical_slot(owner) for owner in owners)


def conflict_slots(record: AllocationRecord) -> frozenset[SlotKey]:
    """Return the distinct physical slots a record's code was issued to."""
    return physical_slots(issuance_owners(record))


def slot_shadowed(
    record: AllocationRecord,
    owner: AllocationOwner,
    releasing: Collection[str],
) -> bool:
    """Return whether another booking already stands for this owner's slot.

    When a booking's fingerprint changes, or a repeat guest is handed
    the same code on the same slot, the new identity adopts the code
    already programmed there and the old identity is left behind on the
    same record. The remaining identity keeps the code reserved and
    accounts for it being programmed, so the stale one can go even
    though the lock still presents the code. Owners named in
    ``releasing`` are treated as already gone, and only bookings on a
    real lock slot qualify: observed aliases, forced-release holds and
    lockless owners never shadow nor are shadowed.
    """
    if _is_physical_only(owner.identity_key) or owner.lockname is None:
        return False
    if owner.slot is None:
        return False
    slot = physical_slot(owner)
    return any(
        other.identity_key != owner.identity_key
        and other.identity_key not in releasing
        and physical_slot(other) == slot
        for other in issuance_owners(record)
    )


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
    announced_conflicts: dict[str, frozenset[SlotKey]] = field(default_factory=dict)
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
        """Return records issued to more than one physical slot.

        Several identities adopted on one slot are one code in one
        place, not a code shared between bookings.
        """
        return [
            record
            for record in self.records.values()
            if len(conflict_slots(record)) > 1
        ]
