<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Phase 0 Research: Force Re-Issue of a Door Code

Every decision below was taken against live source on `main` at `a9b82f9`,
after feature 022 was merged. File and line references were read, not recalled.

## 1. How does a target stop owning its old code?

**Decision**: Re-home the target's single owner onto a reserved
*forced-release hold* identity on the same `AllocationRecord`, then allocate
normally for the real identity.

**Rationale**: `AllocationRegistry` keys `by_identity` one-code-per-identity,
and `add_owner` raises `ValueError("identity_key already owns a different
code")` if that is violated. `allocate()` returns the existing record for a
known identity before doing anything else, so while the identity still owns the
old code, asking for a new one is a guaranteed no-op. The identity must
therefore be freed first. Meanwhile FR-020 requires the old code to remain
unavailable to every other allocation, and the only thing that makes a code
unavailable in this system is an owner on its record
(`AllocationRegistry.is_available`). A hold owner satisfies both at once.

**Alternatives considered**:

- *Release the old code immediately and reserve it in memory.* Rejected: the
  reservation would evaporate on restart, and a still-programmed code could then
  be issued to another entry, recreating the exact duplicate this feature
  exists to fix.
- *Add a `pending_release` flag to `AllocationOwner`.* Rejected: it changes the
  persisted schema for something a reserved identity namespace already
  expresses, and the tree already uses exactly that technique in
  `issuance.observed_alias_key`.
- *Allow two codes per identity.* Rejected: it breaks the registry's central
  invariant and every consumer of `by_identity`.

## 2. Reuse the observed-alias owner as the hold?

**Decision**: No. Use a distinct `:reissued:` namespace, and collapse any
pre-existing observed alias for the same entry, lock, and slot into it.

**Rationale**: `adoption._release_moved_observed_alias` releases an alias
**directly**, bypassing `_release_guard_reason` entirely, as soon as the slot is
observed holding a different code. That is defensible for an alias, but FR-019
requires the replaced code's release to pass *through* the guard, and FR-021
requires each deferral to be retried and reported. Overloading the alias would
put the feature's central safety property on a code path designed to skip it.

**Alternatives considered**: reusing the alias verbatim (rejected, above);
leaving alias and hold as two independent owners of the same record (rejected —
it inflates owner counts, muddies conflict reporting, and makes "one owner, one
physical slot" untrue).

## 3. What shape should the exemption take?

**Decision**: A frozen `ForcedReleaseExemption(code, entry_id, identity_key)`
passed as a keyword-only parameter defaulting to `None`, honoured only for a
single owner in the hold namespace whose three fields all match.

**Rationale**: The failure mode to design against is not a malicious caller but
an ordinary one — a later contributor who adds a `force=True` argument for an
unrelated reason and disarms the guard on a live code. A boolean invites that; a
mandatory, purpose-named value object that must be constructed from the very
owner being evaluated does not. Keyword-only with a `None` default means the
three ordinary callers (`_sweep_unlocked`, `async_mark_entry_removed`,
`async_clear_orphans`) are textually unchanged and provably keep the full guard.

**Alternatives considered**:

- *A boolean `allow_conflict` parameter.* Rejected: trivially enabled by
  accident, carries no scope, and cannot be verified against the owner.
- *A separate `_forced_release_guard_reason` function.* Rejected: two guards
  drift. The physical conditions are the part that must never diverge, so there
  must be exactly one implementation of them.
- *A set of exempt identity keys on the allocator.* Rejected: ambient state that
  outlives a single decision is exactly how an exemption leaks onto the wrong
  owner.

## 4. Which retention sites must be suppressed?

**Decision**: Three — `_resolve_observed_code`,
`checkin_protection.build_protected_reservation`, and the target's
`AdoptionRequest` — all for one target for one cycle.

**Rationale**: The spec names `_resolve_observed_code`
(`coordinator_helpers/reservations.py:224`) because that is where the problem is
described. Reading the tree shows two more places where the same "keep what is
on the lock" decision is made: `build_protected_reservation`
(`coordinator_helpers/checkin_protection.py:49`) sets
`code_source="manual_observed"` for a synthesized checked-in guest, and
`build_adoption_requests` (`coordinator_helpers/code_allocation.py`) re-adopts
the observed physical code onto the reservation identity, after which
`allocate_request` returns it idempotently. Suppressing only the first would
leave the feature silently ineffective for a checked-in ghost and ineffective
outright for every lock-backed target.

**Alternatives considered**: suppressing only `_resolve_observed_code`
(rejected, above); making adoption skip any reservation whose code the registry
does not know (rejected — that is a behaviour change for every entry, and
feature 022 exists precisely to adopt those).

## 5. Does skipping one adoption break anything?

**Decision**: Yes, and `_adoption_complete` must exclude suppressed slots from
`readable_coded_slots`.

**Rationale**: `_adoption_complete`
(`coordinator_helpers/code_allocation.py:151`) returns
`readable_coded_slots <= adopted_slots`. A skipped adoption leaves a readable
coded slot unadopted, so the predicate goes `False`, `async_resolve_codes`
passes `allocations=[]`, and no code is issued to anyone in that entry that
cycle — including the target. The re-issue would appear to do nothing. The
suppressed slot is accounted for by the hold, so excluding it is both correct
and necessary. `issuance.unaccounted_slots` needs no change: a hold owner keeps
its `lockname` and `slot`, so its slot is already counted as claimed.

## 6. Entity service or domain service?

**Decision**: One domain service, `rental_control.force_reissue`, with an
`entity_id` *field* using an entity selector restricted to
`integration: rental_control, domain: sensor`.

**Rationale**: FR-002 requires two alternative targeting forms and FR-003
requires refusing a call that supplies both or neither. A Home Assistant entity
platform service with a `target:` block can express neither condition: it cannot
carry "a lock and a slot instead", and it cannot be invoked with no target at
all so that the refusal can happen. `clear_orphaned_codes` already establishes
the domain-service-plus-`dry_run`-plus-response pattern in this integration, and
the entity selector preserves the operator-facing feel of `checkout` and
`set_state`.

**Alternatives considered**: two services, one per targeting form (rejected — it
makes "exactly one target" a convention rather than a validated rule, and
doubles the surface); an entity service plus a separate slot service (same
objection).

## 7. Does the service write the code itself?

**Decision**: No. It records a pending re-issue and drives the existing refresh.

**Rationale**: FR-015 requires the write to go through the ordinary
reconciliation path, and reading `reconciliation/actions.py` shows it already
does the right thing unaided: once the reservation carries the new code and the
slot still reads the old one, `classify_matched_desired_slot` computes
`code_drift` and returns `OVERWRITE_MANUAL_CHANGE`. A dedicated write path would
duplicate confirmation, retry, and store-sync logic that already exists and is
already tested.

## 8. Does the sensor need changes for FR-026?

**Decision**: No. `RentalControlCoordinator.get_slot_code`
(`coordinator.py:200-216`) already implements it.

**Rationale**: For a lock-backed entry it returns the *observed* code until
`observed_code == res.slot_code`, and returns `None` when
`observed_slot != selected_slot`. That is "publish the last confirmed code until
the new write is confirmed", already keyed by slot so a stale code cannot leak
across a slot reassignment. For a lockless entry (`event_overrides is None`) it
returns `res.slot_code` immediately, which is FR-016. No attribute is added,
removed, or changed, so the downstream captive-portal contract over
`slot_code`, `slot_name`, and `last_four` is untouched.

## 9. How is a lock-unavailable or unreadable target handled? (FR-010)

**Decision**: Refuse at invocation when the addressed slot's current
observation is `SlotStatus.UNKNOWN` or no observation covers it; defer under the
existing guard for every cycle after acceptance.

**Rationale**: FR-010 permits either, and the two are right in different places.
Before acceptance the operator is present and can retry, so a clear refusal is
better than a silently queued operation. After acceptance there is no operator
to tell, so deferral plus per-cycle reporting (FR-021) is better. In neither
case is an unreadable slot treated as empty: `SlotStatus.UNKNOWN` is produced at
two sites in `coordinator_helpers/keymaster_observation.py` — once with
`blocked_reason="unreadable"`, once when `name_state` or `pin_state` is `None`
with no `blocked_reason` — so the only correct test is
`status is SlotStatus.UNKNOWN`. `SlotStatus.FREE` is known-empty and is not
unreadable.

## 10. What makes a repeat invocation safe? (FR-009)

**Decision**: The lifecycle of the replaced code is the idempotency key. A
pending re-issue, or an outstanding hold for the same entry, lock, and slot,
makes a repeat a reporting no-op.

**Rationale**: The hazard is double submission — a retried script, a
double-clicked dashboard button — not an operator deciding weeks later to rotate
again, which the spec explicitly allows. Tying idempotency to the replaced
code's disposition covers the hazard exactly, needs no timer, and additionally
covers the case where the in-memory pending record was lost to a restart while
the durable hold survived.

## 11. Dry run without a second selection implementation

**Decision**: Extract the candidate-selection body of
`issuance.allocate_request` into a pure `select_code(...)` helper and call it
from both the real path and `async_preview_reissue`.

**Rationale**: A preview that can disagree with the real allocation is worse
than no preview. Sharing the selection keeps FR-007's "reports exactly what
would happen" true by construction. The preview takes the allocator lock,
mutates nothing, and never calls `_store.async_save`.

## 12. Why does the dry run alone show a raw code?

**Decision**: Implement FR-023 literally — `replacement_code` exists on the
dry-run response and on no other response, log line, notification, or
diagnostics payload.

**Rationale**: The spec settles this as a deliberate, single carve-out. The
implementation risk is leakage by accident, so the response builders for the two
modes are separate functions rather than one function with a conditional
`pop()`, and the test asserts absence field-by-field rather than by
substring-searching a serialized payload.

## Resolved, with no open questions

No clarification markers remain. The spec carries 28 functional
requirements and zero clarification markers, and every planning question raised
above was answerable from live source.
