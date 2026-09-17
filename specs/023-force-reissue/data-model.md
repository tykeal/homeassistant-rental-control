<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Data Model: Force Re-Issue of a Door Code

Entities introduced or changed by this feature. The service and allocator API
shapes are in
[contracts/force-reissue-service.md](contracts/force-reissue-service.md); this
document defines semantics, states, and validation rules.

**No persisted schema changes.** The feature 022 registry store
(`rental_control.code_registry`, version 1) is used exactly as it is. Nothing
here adds a field to `AllocationRecord` or `AllocationOwner`, and nothing bumps
a store version.

## New entities

### ReissueRequest

**Owner module**: `custom_components.rental_control.allocator.reissue_service`

One operator invocation, parsed from the `ServiceCall` data. Pure value object.

**Fields**:

- `entity_id: str | None` — a single Rental Control reservation sensor.
- `lockname: str | None` / `slot: int | None` — the explicit slot form.
- `force: bool` — the checked-in override (FR-006). Default `False`.
- `dry_run: bool` — preview only (FR-007). Default `False`.

**Validation rules**:

- Exactly one targeting form. `entity_id` set XOR (`lockname` **and** `slot`
  set). Both, neither, or a half-supplied slot form is refused with a clear
  error and changes nothing (FR-003).
- `entity_id` is a single entity id. A list, an `area_id`, a `device_id`, or
  `all` is refused (FR-004).
- `slot` is a positive integer.
- There is no `retire`, `code`, or bulk field, and none may be added.

### ReissueTarget

**Owner module**: `custom_components.rental_control.coordinator_helpers.reissue`

The resolved subject of a request.

**Fields**: `entry_id`, `identity_key: str | None`, `lockname: str | None`,
`slot: int | None`, `target_key: str`, `checked_in: bool`,
`observed_code: str | None`.

`target_key` is `identity_key` for the entity form and a live-reservation slot
form, and `f"slot:{lockname}:{slot}"` for a bare ghost slot. It is the key under
which the pending re-issue is held.

**Validation rules**:

- The owning config entry must be loaded and must have a coordinator in
  `hass.data[DOMAIN][entry_id][COORDINATOR]`.
- Entity form: the entity must be a `RentalControlCalSensor` currently carrying
  an event; the identity is
  `make_reservation_fingerprint(entry_id, slot_name, start, end)`, the same
  derivation `sensors/calsensor_helpers/slots.py:read_slot` uses. Any other
  entity, including the check-in tracking sensor, is refused.
- Slot form: `slot` must lie in
  `range(coordinator.start_slot, coordinator.start_slot + coordinator.max_events)`
  for exactly one loaded entry whose `lockname` matches. Outside every range is
  refused (FR-005); inside more than one is refused as ambiguous.
- For lock-backed targets, the slot's current observation must not be
  `SlotStatus.UNKNOWN`, and the slot must be covered by a current observation,
  or the request is refused (FR-010). Lockless entity targets skip this check
  because they have no physical slot to observe. `SlotStatus.FREE` is
  known-empty and is **not** unreadable.
- `checked_in` is read from the entry's `CHECKIN_SENSOR` state
  (`CHECKIN_STATE_CHECKED_IN`) matched to this target. When `checked_in` is
  `True` and `force` is `False`, the request is refused (FR-006).

### PendingReissue

**Owner module**: `custom_components.rental_control.coordinator_helpers.reissue`

The transient state between an accepted request and the cycle that consumes it.
Held in a plain `dict[str, PendingReissue]` attribute on the coordinator,
**in memory only**. It is never written to any store and is gone after a Home
Assistant restart (FR-013).

**Fields**: `target_key`, `identity_key: str | None`, `lockname: str | None`,
`slot: int | None`, `suppress_pending: bool`, `phase: ReissuePhase`,
`replaced_code_ref: str | None`, `replacement_code_ref: str | None`,
`requested_at`, `invoker: str | None`, `terminal_reason: str | None`.

**State transitions**:

```text
(absent) ──accepted──► ACCEPTED (suppress_pending=True)
ACCEPTED ──cycle consumes suppression, new code allocated──► ISSUED
                                          (suppress_pending=False)
ISSUED ──hold released by the guard──► (absent)
ACCEPTED ──target gone from feed (FR-017)──► (absent)
ACCEPTED ──allocation exhausted, rollback complete──► (absent)
ACCEPTED ──bare ghost slot cleared or nothing to clear──► (absent)
ACCEPTED/ISSUED ──Home Assistant restart──► (absent, hold persists)
```

**Validation rules**:

- `suppress_pending` is `True` for exactly one reconcile cycle. The cycle that
  builds reservations with it clears it after a terminal outcome: successful
  allocation, explicit rollback after exhaustion, or a clear-only ghost result.
  It is never re-armed without a new invocation (FR-013).
- At most one `PendingReissue` per `target_key`. A repeat invocation while one
  exists returns the existing outcome and performs no second rotation (FR-009).
- A completed target fingerprint prevents same-runtime repeat calls from
  rotating again after the hold has been released. That fingerprint is not
  persisted; after hold release plus a Home Assistant restart, an identical
  service call is a new operator decision.
- A `PendingReissue` never causes a slot to be created for a reservation that no
  longer exists. Pending identities are excluded before persisted ghost
  hydration, so a disappeared booking is dropped instead of reconstructed as a
  codeless ghost allocation candidate (FR-017).
- If the allocator cannot obtain a unique replacement after a hold was staged,
  the same locked cycle restores the original owner and removes the hold before
  the pending record is cleared. Exhaustion therefore leaves the existing
  allocation and physical code in place (FR-008).

### ReissueSuppression

**Owner module**: `custom_components.rental_control.coordinator_helpers.reissue`

Frozen, pure value object carried on `ReservationBuildContext` for one cycle.

**Fields**: `identity_keys: frozenset[str]`, `slots: frozenset[int]`. The empty
value is the default, so every existing construction site of
`ReservationBuildContext` is unaffected.

**Validation rules**:

- Consulted by `_resolve_observed_code`,
  `checkin_protection.build_protected_reservation`, and
  `code_allocation.build_adoption_requests` only. Identity-backed re-issues are
  matched by `identity_key`; the slot set is used only for identity-less bare
  slot targets. That prevents a new reservation that later occupies the old
  physical slot from inheriting another target's suppression.
- Suppression changes which `(code, code_source)` pair is returned. It never
  changes matching, eligibility, dates, or names.
- Every non-suppressed reservation in the same cycle keeps full `manual_observed`
  retention.

### ForcedReissueDirective

**Owner module**: `custom_components.rental_control.allocator.models`

One cycle's instruction to re-home one owner. Frozen, slotted.

**Fields**: `entry_id`, `identity_key: str | None`, `lockname: str | None`,
`slot: int | None`.

**Validation rules**:

- Applied before adoption, rekey, and allocation, inside the single existing
  lock hold of `issuance.resolve_cycle`.
- Applies to at most one owner: the one owning the record the target identity
  currently owns, or the one whose `lockname` and `slot` match for a bare slot
  target. Never to a whole record and never to another owner of the same record.
- An identity-backed directive whose target owns nothing produces
  `no_existing_allocation` and is otherwise a no-op; allocation still proceeds
  for that reservation identity. A bare slot directive with no matching owner is
  terminal and clears the pending record, because there is no reservation
  identity for which a replacement could be allocated.

### Forced-release hold identity

**Owner module**: `custom_components.rental_control.allocator.reissue`

Not a class — a reserved identity-key namespace, in the same style as the
existing `issuance.observed_alias_key`:

```text
f"{identity_key}:reissued:{entry_id}:{lockname}:{slot}"
```

with `lockname` and `slot` rendered as `none` for a lockless owner.
`is_forced_release_hold(key)` parses the key structurally by splitting on `:`,
requiring the expected arity and `parts[1] == "reissued"` rather than accepting
an arbitrary substring match.

**Validation rules**:

- Deterministic, so a second directive for the same target finds the hold
  already present and refuses rather than chaining (FR-009).
- Can never collide with a reservation fingerprint (which contains no `:` marker
  of this form) or with an `:observed:` alias.
- A hold owner preserves the re-homed owner's `entry_id`, `origin`, `lockname`,
  `slot`, `lock_observed`, and timestamps verbatim, so every physical guard
  condition evaluates exactly as it did before the re-home.
- A hold owner is skipped by `_sweep_unlocked`, so the ordinary sweep never
  evaluates or reports it.
- A hold owner is **not** skipped by `async_mark_entry_removed` or
  ordinary `async_clear_orphans`; those apply the full unmodified guard to it,
  which is the conservative direction.

### Forced hold reclamation through `clear_orphaned_codes`

The existing `clear_orphaned_codes` service gains a fail-closed operator
override for permanently stuck forced-release holds on loaded entries. A hold
can be stuck precisely because the physical guard can never be satisfied, for
example when the lock or slot is permanently unreadable. Requiring the
unmodified physical conditions before reclamation would therefore make the
remedy a no-op for the only case it exists to fix.

**Validation rules**:

- By default, `clear_orphaned_codes` behaves exactly as it does today: owners
  whose `entry_id` belongs to a loaded config entry are not orphan candidates.
- With the explicit override flag, only hold-namespace owners on live entries
  may be considered. Ordinary live owners are never eligible through this path.
- The override is an operator assertion that the lock/slot named by the hold is
  genuinely gone. It does not call or weaken `_release_guard_reason`, and it
  does not alter `ForcedReleaseExemption`.
- `dry_run` reports every candidate before acting. Each candidate includes the
  masked `code_ref`, entry id, lock/slot when present, and the retention reason
  that made the hold visible as stuck. No report contains a raw code.
- Acting on a candidate releases only the hold identity. It never releases
  another owner of the same record and never changes any ordinary orphan path.

### ForcedReleaseExemption

**Owner module**: `custom_components.rental_control.allocator.models`

Frozen, slotted. Names the single owner one forced re-issue deliberately
re-homed.

**Fields**: `code: str`, `entry_id: str`, `identity_key: str`.

**Validation rules** (all six must hold or the full guard applies):

1. the exemption is not `None`;
2. the `owners` list under evaluation has exactly one element;
3. `owners[0].identity_key == identity_key`;
4. `owners[0].entry_id == entry_id`;
5. `record.code == code`;
6. `is_forced_release_hold(owners[0].identity_key)`.

It disarms **only** the `adoption_conflict` condition. `unverifiable_lock` and
`code_still_programmed` are evaluated unchanged for the targeted owner, and no
condition is disarmed for any other owner.

It is constructed in exactly one place, `reissue.release_forced_holds`, from the
owner it is about to evaluate.

### ReissuePreview

**Owner module**: `custom_components.rental_control.allocator.models`

Result of `async_preview_reissue`. Mutates nothing.

**Fields**: `replacement_code: str | None`, `replacement_code_ref: str | None`,
`origin: AllocationOrigin | None`, `reason: str | None`,
`replaced_code_ref: str | None`.

`replacement_code` is the single deliberate raw-code carve-out (FR-023) and may
appear only in a dry-run service response for a target that would receive a
replacement. It is `None` for a clear-only bare slot target.

### ReissueOutcome

**Owner module**: `custom_components.rental_control.allocator.models`

What one directive and its subsequent hold releases report.

**Fields**: `entry_id`, `identity_key: str | None`, `lockname: str | None`,
`slot: int | None`, `replaced_code_ref: str | None`,
`replacement_code_ref: str | None`, `origin: AllocationOrigin | None`,
`disposition: str`, `retention_reason: str | None`.

`disposition` is one of `held_pending_release`, `released`,
`no_existing_allocation`, or `failed`. `retention_reason` is one of
`unverifiable_lock` or `code_still_programmed` — never `adoption_conflict`,
which is exempted and therefore cannot be a retention reason for a hold
(FR-021). No field carries a code in any form.

## Changed entities

### CycleRequest (`allocator/models.py`)

Gains `forced_reissues: tuple[ForcedReissueDirective, ...] = ()`. The default
keeps every existing construction site and test valid.

### CycleResult (`allocator/models.py`)

Gains `reissues: tuple[ReissueOutcome, ...] = ()` covering both directives
applied this cycle and hold releases attempted this cycle.

### DoorCodeAllocator (`allocator/allocator.py`)

- `_release_guard_reason` gains the keyword-only
  `forced_release: ForcedReleaseExemption | None = None`. Its three existing
  callers pass nothing and are otherwise unchanged.
- `_sweep_unlocked` skips hold-namespace owners.
- New `async_preview_reissue(request) -> ReissuePreview`, read-only under the
  lock, with no `_store.async_save`.

### AllocationRegistry / AllocationRecord / AllocationOwner

**Unchanged.** `release(identity_key)` keeps its existing behaviour of removing
one owner and deleting the record only when no owners remain, which is exactly
what reduces a two-owner duplicate record to one owner.

### ReservationBuildContext (`coordinator_helpers/models.py`)

Gains `reissue_suppression: ReissueSuppression = ReissueSuppression()`.

### Reservation (`reconciliation/plan_models.py`)

**Unchanged.** A forced re-issue produces the ordinary `code_source` values
already defined by feature 022 (`generated` from the builder, then `allocated`
or `collision_resolved` after the allocation step). No new `code_source` value
is introduced.

## Relationships

```text
ServiceCall(force_reissue)
   │
   ├─ ReissueRequest ──► ReissueTarget ──► PendingReissue (in memory, coordinator)
   │                                            │
   │                                            ├──► ReissueSuppression
   │                                            │      └─► _resolve_observed_code
   │                                            │      └─► build_protected_reservation
   │                                            │      └─► build_adoption_requests (skip)
   │                                            │
   │                                            └──► ForcedReissueDirective
   │                                                       │
   └─ dry_run ──► async_preview_reissue ──► ReissuePreview  │
                                                            ▼
                                      issuance.resolve_cycle (one lock hold)
                                         ├─ apply_forced_reissues → hold owner
                                         ├─ adopt / rekey / allocate → new code
                                         ├─ sweep (skips holds)
                                         └─ release_forced_holds
                                                  │ builds
                                                  ▼
                                      ForcedReleaseExemption (one owner only)
                                                  │ passed to
                                                  ▼
                                      _release_guard_reason
                                       ├─ adoption_conflict   (exempted, this owner)
                                       ├─ unverifiable_lock   (IN FORCE)
                                       └─ code_still_programmed (IN FORCE)
                                                  │ on None
                                                  ▼
                                      AllocationRegistry.release(hold_key)
```

One target, one owner, one exemption, one release. Every arrow above is
single-valued by construction; none of them accepts a collection of targets.
