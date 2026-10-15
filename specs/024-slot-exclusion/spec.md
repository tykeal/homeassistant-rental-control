<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Feature Specification: Slot-Exclusion Recovery Control

**Feature Branch**: `024-slot-exclusion`
**Created**: 2026-10-15
**Status**: Draft
**Input**: GitHub issue #802 — "Permanently unavailable slot freezes issuance
entry-wide". A single managed Keymaster slot that is permanently unreadable
holds door-code issuance closed. The gate is deliberate and correct, but the
operator has no remedy available inside Home Assistant: the only fix is to
repair or remove the slot in Keymaster itself.

## Problem Statement

A managed slot whose Keymaster entity never returns a readable value freezes
door-code issuance. There are two distinct freeze regimes, and the second is
considerably worse than the one #802 describes.

### Regime 1 — entry-wide refusal

`unaccounted_slots()` (`allocator/issuance.py`, lines 221-235) returns every
unreadable slot in the cycle observation that is not claimed by one of this
entry's registry owners:

```python
def unaccounted_slots(
    allocator: DoorCodeAllocator, observation: CycleObservation
) -> frozenset[int]:
    """Return unreadable slots not claimed by this entry's registry owners."""
    ...
    return frozenset(observation.unreadable_slots - claimed)
```

When that set is non-empty, `issuance_allowed` is false for the cycle and every
allocation request for the entry is refused at `allocator/issuance.py` lines
76-77 with `reason="unaccounted_slots"`. The refusal is entry-wide rather than
scoped to the offending slot. Healthy, readable slots belonging to other
reservations stop receiving codes for as long as the one dead slot remains
unreadable.

### Regime 2 — system-wide freeze at startup

`_adoption_complete()` (`coordinator_helpers/code_allocation.py`, lines
187-205) returns `False` unconditionally whenever any managed slot is
unreadable:

```python
    if observation.unreadable_slots:
        return False
```

An entry whose adoption never completes is never discarded from
`_pending_adoption` (`allocator/issuance.py`, lines 158-163). That set does not
live on the entry. It lives on the **singleton** `DoorCodeAllocator`, whose
docstring states its scope plainly — "System-wide allocator state shared by all
Rental Control entries" (`allocator/allocator.py`, line 60). While that set is
non-empty, `allocate_request` refuses **every** entry's allocation at
`allocator/issuance.py` lines 78-79 with `reason="adoption_pending"`.

The consequence is that one dead slot at one property stops codes at an
unrelated property, on a different lock, belonging to a different config entry.
The affected entry itself does not surface that refusal, because an entry whose
adoption has not completed submits no allocation requests at all
(`coordinator_helpers/code_allocation.py`, lines 86-95); it simply issues
nothing. The visible `adoption_pending` refusals land on the healthy entries.
Because adoption runs on every start, the regime-2 freeze re-establishes itself
on every Home Assistant restart for as long as the slot stays dead.

### Why the gate is correct and must stay

An unreadable slot may be holding a code this integration cannot see. Issuing
into that uncertainty risks allocating a value that collides with the hidden
code, or writing over a working guest credential. The globality of
`_pending_adoption` is equally deliberate: feature 022 made the door-code space
a shared, system-wide resource, and an entry that has not finished accounting
for what is already on its locks cannot safely contribute to, or draw from,
that shared space.

**This feature does not weaken the gate and does not scope it per entry.** It
changes only what the allocator is allowed to *observe*, so that the gate
evaluates against the set of slots the operator still considers to be under
management.

### Why this is tractable: the planner already degrades per slot

Reconciliation already handles an unreadable slot gracefully and in isolation.
`_classify_slot` (`reconciliation/desired.py`, lines 257-258) maps an UNKNOWN
slot to `ActionKind.BLOCKED` with reason `"unreadable"`, so the planner never
writes to it and never offers it as a free slot for a new reservation:

```python
    if ms.status is SlotStatus.UNKNOWN:
        return ActionKind.BLOCKED, ms.blocked_reason or "unreadable", None
```

The entry-wide and system-wide freezes are therefore **purely allocator-gate
artefacts**, not a planner limitation. The planner's behaviour toward the dead
slot is already exactly what is wanted. Only the allocator's view needs to
change, and the integration has a single chokepoint for producing that view:
`build_cycle_observation` (`coordinator_helpers/code_allocation.py`, lines
208-227) is the one place `unreadable_slots` is computed, and
`_adoption_complete` is the one place it gates adoption. The observation has
other readers, notably the code-release evidence path, so the filtering must be
applied at the gate rather than baked into the observation for everyone; that
distinction is required by FR-016.

`reissue_preview.preview_request_and_result`
(`allocator/reissue_preview.py`, lines 90-117) routes through the same
observation builder, so `force_reissue` from feature 023 is unblocked by the
same exclusion. It is not unblocked for free, however: before consulting the
shared helpers it independently rejects any non-empty raw unreadable set at
lines 111-112, so that third check has to be made exclusion-aware as well.
FR-016 and FR-019 require it.

## Context

A slot is unreadable when its Keymaster entity state cannot be interpreted.
`ObservedSlot.__post_init__` (`reconciliation/stateless_models.py`, lines
50-77) sets `readable = False` and classifies the slot as
`ObservedSlotStatus.UNKNOWN` when the name or PIN text is missing or
unreadable, and `SlotStatus.UNKNOWN` carries the same meaning in the planner's
vocabulary — "actual entity state unreadable"
(`reconciliation/enums.py`, lines 19-45).

Transient unreadability is normal and self-healing: a slot unreadable this
cycle is simply re-evaluated next cycle, which is the whole point of the
stateless model established by #607. The problem case is a slot that is
unreadable *permanently* — a removed lock, a decommissioned slot, a Keymaster
entity that never returns. From the integration's point of view the two are
indistinguishable, so only an operator can tell them apart. That is why the
remedy is an explicit operator action and never an automatic one.

The feature follows the service precedent set by `force_reissue`
(`allocator/reissue_service.py`): a single narrowly targeted operator
invocation, dual targeting by entity or by lock plus slot, dry-run support, an
optional service response, and registration from `register_allocator_services`
(`allocator/services.py`, lines 39-57).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Unfreeze a Property After a Slot Dies (Priority: P1)

An operator's lock loses one slot permanently — the hardware was replaced and
slot 7 no longer reports anything Keymaster can read. Every reservation at that
property stops receiving a door code, and the diagnostics report refusal reason
`unaccounted_slots`. The operator confirms in Keymaster that slot 7 is genuinely
gone, then marks slot 7 excluded from management. On the next refresh cycle the
allocator stops counting slot 7 as unaccounted, issuance resumes for the
remaining slots, and arriving guests get codes again.

**Why this priority**: This is the reported defect. Without it the operator has
no in-Home-Assistant remedy at all and guests cannot get in.

**Independent Test**: Drive one entry whose managed range contains one UNKNOWN
slot, confirm every allocation is refused, exclude that slot, and confirm
allocations succeed for every remaining slot while the excluded slot is never
written to.

**Acceptance Scenarios**:

1. **Given** one entry with one permanently UNKNOWN managed slot and refusals
   reported as `unaccounted_slots`, **When** the operator excludes that slot,
   **Then** the next cycle issues codes to the remaining reservations and
   reports no `unaccounted_slots` refusal.
2. **Given** that same excluded slot, **When** reconciliation plans the cycle,
   **Then** the excluded slot is never written to, never cleared, and never
   offered as a free slot for a new reservation.
3. **Given** the operator wants to see the effect first, **When** they invoke
   the control in dry-run mode, **Then** the response reports the set the
   exclusion would produce and nothing is persisted or changed.

---

### User Story 2 - Unfreeze Unrelated Properties After One Dies (Priority: P1)

An operator runs three properties as three config entries on three separate
locks. One slot at property A becomes permanently unreadable. After the next
Home Assistant restart, properties B and C — which are entirely healthy — also
stop issuing codes for new reservations, reporting `adoption_pending`. Property
A issues nothing at all, because an entry whose adoption has not completed
submits no allocation requests in the first place and so never reaches that
refusal. The operator excludes the dead slot at property A. Property A's
adoption then completes, the shared `_pending_adoption` set empties, and B and
C resume issuing immediately.

**Why this priority**: This is the more damaging regime and the one most likely
to be misdiagnosed, because the symptom appears at a property that has nothing
wrong with it.

**Independent Test**: Load three entries, make one slot on entry A UNKNOWN,
restart, and give each entry a new reservation that has no existing registry
allocation — so that existing-code reuse cannot mask the gate. Confirm B and C
are refused with `adoption_pending` and that A requests nothing, exclude the
one slot on A, and confirm all three then issue.

**Acceptance Scenarios**:

1. **Given** three loaded entries of which one has an UNKNOWN managed slot,
   **When** adoption runs at startup and each entry has a new reservation with
   no existing allocation, **Then** the two healthy entries are refused with
   `adoption_pending` and the affected entry submits no allocation request at
   all. No entry issues a code.
2. **Given** that state, **When** the operator excludes the offending slot on
   its own entry, **Then** that entry's adoption completes, the shared pending
   set empties, and all three entries issue codes on the next cycle.
3. **Given** the exclusion has been recorded, **When** Home Assistant restarts,
   **Then** the system does not re-freeze and no further operator action is
   required.

---

### User Story 3 - Automatic Recovery When the Slot Comes Back (Priority: P1)

A slot the operator excluded during a prolonged Keymaster outage starts
reporting a readable state again. Without the operator doing anything, the
exclusion is dropped on the next cycle, the slot returns to normal fail-closed
management, and the operator is told it happened.

**Why this priority**: This is the property that makes a persisted exclusion
acceptable at all. An exclusion that outlives the condition justifying it is
precisely the permanent wedge this repository has already shipped once and
reverted.

**Independent Test**: Exclude an UNKNOWN slot, make it readable, run one cycle,
and confirm the exclusion is gone from the persisted set, the notification
reflects the smaller set, and the slot participates in normal management again.

**Acceptance Scenarios**:

1. **Given** an excluded slot that becomes readable, **When** the next cycle
   runs, **Then** the exclusion is removed automatically without any operator
   action.
2. **Given** that auto-clear, **When** the operator looks at Home Assistant,
   **Then** the change is visible in the persistent notification and in
   diagnostics, and is recorded in the log.
3. **Given** the exclusion has auto-cleared and the slot becomes unreadable
   again later, **When** the next cycle runs, **Then** the entry freezes
   fail-closed again and the operator must make the exclusion decision afresh.

---

### User Story 4 - Standing Visibility of an Accepted Risk (Priority: P2)

An operator who excluded a slot months ago, or who inherited the system from
somebody who did, can see at a glance that a slot is excluded and understand
what risk is being carried. A persistent notification stands for as long as any
exclusion is in effect and disappears the moment the last one clears.

**Why this priority**: An exclusion is an accepted risk, not a fix. A risk that
becomes invisible stops being an accepted risk and becomes a forgotten one.

**Independent Test**: Exclude a slot and confirm a per-entry notification
appears naming the slots and the risk; clear the last exclusion and confirm the
notification is dismissed.

**Acceptance Scenarios**:

1. **Given** at least one excluded slot on an entry, **When** any cycle
   completes, **Then** a per-entry persistent notification exists naming the
   excluded slots and stating the residual collision risk.
2. **Given** the last exclusion on an entry clears, **When** the next cycle
   completes, **Then** the notification for that entry is dismissed.
3. **Given** any set of exclusions, **When** diagnostics are downloaded,
   **Then** the excluded set is present for support triage.

---

### User Story 5 - Reversing an Exclusion Deliberately (Priority: P2)

An operator who excluded the wrong slot, or who has repaired the lock, removes
the exclusion explicitly rather than waiting for the slot to become readable.
The slot returns to fail-closed management immediately.

**Why this priority**: Any operator-set flag must be operator-clearable.
Waiting on the auto-clear is not acceptable when the operator already knows the
exclusion was a mistake.

**Independent Test**: Exclude a slot, invoke the control with the excluded flag
set false, and confirm the slot returns to the gate immediately.

**Acceptance Scenarios**:

1. **Given** an excluded slot, **When** the operator clears the exclusion,
   **Then** the slot is counted by the gate again on the next cycle.
2. **Given** a slot that is not excluded, **When** the operator clears an
   exclusion for it, **Then** the call succeeds, reports that nothing changed,
   and writes nothing.

---

### Edge Cases

- **An orphaned registry owner holds the excluded slot.** A code owned by an
  entry that has since been removed may still be recorded against the excluded
  slot. The claim-check in FR-010 is scoped to the addressed entry and cannot
  see such an owner, so exclusion must never be read as evidence that the slot
  is unprogrammed. Orphan cleanup continues to see the slot as unreadable and
  continues to decline to release that code without proof of absence (FR-016,
  FR-018).
- **An excluded dead slot coexists with an unaccounted readable slot.**
  Adoption stays pending and the system stays frozen. Exclusion removes only
  the unreadability condition; the requirement that readable coded slots be
  accounted for is untouched, and the operator must resolve that separately.
- **The slot is readable.** The control refuses. A readable slot can never
  cause either freeze, so there is no legitimate reason to exclude one, and
  refusing is the structural guard that prevents a live guest's slot from being
  excluded.
- **The slot is claimed by this entry's registry owners.** The control refuses.
  A claimed slot is already subtracted from the unaccounted set
  (`allocator/issuance.py`, lines 227-235) and so is not causing the freeze.
- **The slot is outside the entry's managed range.** The control refuses. A
  slot outside `start_slot .. start_slot + max_events - 1` is not managed by
  this entry and excluding it would mean nothing.
- **The slot is already excluded.** The call succeeds and reports that nothing
  changed. Repeating a call is always safe.
- **Two entries manage overlapping slot numbers on one parent lock.** An
  exclusion belongs to one config entry and affects only that entry's
  observation. It does not suppress another entry's gate for the same slot
  number, and that other entry stays fail-closed on its own terms.
- **The operator reconfigures the entry through the options flow.** All
  exclusions for that entry are dropped and the entry returns to fail-closed.
  This is a consequence of how the entry's stored data is rebuilt, and it fails
  in the safe direction.
- **The operator excludes every managed slot.** The gate is satisfied, but
  reconciliation has no usable slot to write to, so reservations are reported
  as having no slot available. The freeze is replaced by ordinary exhaustion,
  not by silent success.
- **An excluded slot becomes readable and is holding an unknown code.** The
  exclusion auto-clears, the slot re-enters the gate, and if that code is not
  accounted for the entry fails closed again. The system does not quietly adopt
  a code it never issued because of an exclusion that used to exist.
- **The lock is unavailable entirely.** The control still refuses to exclude a
  slot it cannot observe as UNKNOWN only when the observation genuinely does
  not classify it as UNKNOWN; a slot on a wholly unavailable lock classifies as
  UNKNOWN and is therefore excludable. The operator carries the judgement about
  whether the outage is permanent.
- **An exclusion is recorded while the entry is mid-reload.** The exclusion is
  recorded against the entry's stored data and takes effect on the first cycle
  that observes it. No cycle sees a partially applied exclusion set.

## Requirements *(mandatory)*

### Functional Requirements

#### Control surface and targeting

- **FR-001**: The system MUST expose an operator-invocable service,
  `rental_control.set_slot_exclusion`, that sets or clears the exclusion of
  exactly one managed slot on exactly one config entry. It MUST support an
  optional service response, matching the `force_reissue` and
  `clear_orphaned_codes` precedent.
- **FR-002**: A call MUST identify its target entry in one of the two ways
  established by `force_reissue`: a Rental Control reservation sensor entity,
  or an explicit lock name. The slot number MUST always be supplied
  explicitly; it is never inferred from the entity.
- **FR-003**: A call that supplies both targeting forms, or neither, MUST be
  refused with a clear error and MUST change nothing.
- **FR-004**: A call MUST act on exactly one slot. The service MUST NOT accept
  or imply any bulk, wildcard, range, or "exclude everything unreadable" mode.
  Each excluded slot MUST be a separate, deliberate operator decision.
- **FR-005**: The service MUST accept an `excluded` flag defaulting to true, so
  that the same service both sets and clears an exclusion.
- **FR-006**: The service MUST accept an optional free-text `reason` recorded
  with the exclusion for operator recall. The reason MUST NOT affect any
  decision the system makes.
- **FR-007**: The service MUST support a dry-run mode whose response reports
  the refusal that would apply, or the excluded set that would result, while
  persisting nothing and changing nothing.
- **FR-008**: The service response MUST report the addressed entry, the
  addressed lock, the resulting excluded set, whether anything changed, and the
  refusal reason when refused. It MUST NOT contain any door code.

#### Safety guards — no override

- **FR-009**: The service MUST refuse to exclude a slot whose current observed
  status is not UNKNOWN, with refusal reason `slot_readable`. There MUST be no
  force flag, no override, and no configuration option that relaxes this guard.
  A readable slot cannot cause either freeze regime, so excluding one can only
  ever be a mistake or an attack on a live guest's credential.
- **FR-010**: The service MUST refuse to exclude a slot claimed by any registry
  owner belonging to the addressed entry, with refusal reason `slot_claimed`.
- **FR-011**: The service MUST refuse to exclude a slot outside the addressed
  entry's managed range of `start_slot` through
  `start_slot + max_events - 1`, with refusal reason `slot_out_of_range`.
- **FR-012**: Clearing an exclusion MUST NOT be subject to FR-009, FR-010, or
  FR-011. Returning a slot to the gate is always safe and MUST always be
  permitted, including for a slot that has since left the managed range.
- **FR-013**: Every refusal MUST be total: nothing is persisted, no
  notification is created or dismissed, and no cycle behaviour changes.

#### Effect on the allocator gate

- **FR-014**: For the purpose of the issuance gate, an excluded slot MUST NOT
  count as an unreadable slot. It therefore MUST NOT contribute to
  `unaccounted_slots` and MUST NOT by itself hold `issuance_allowed` false.
- **FR-015**: An excluded slot MUST NOT, by virtue of being unreadable, prevent
  the entry's adoption from completing. Exclusion MUST NOT relax any other
  adoption condition: an entry that still has a readable coded slot not
  accounted for by an adoption request MUST remain pending regardless of how
  many slots are excluded. Recovery of unrelated entries follows only once the
  shared pending-adoption set is empty, which requires every pending entry to
  complete adoption on its own terms.
- **FR-016**: Exclusion MUST be scoped to the allocator's issuance gate. That
  scope comprises the unaccounted-slot computation, the unreadability condition
  of adoption completion, and the re-issue preview's own independent rejection
  of any non-empty unreadable set (`allocator/reissue_preview.py`, lines
  111-112), which is a third gate check and not a consumer of the other two.
  Exclusion MUST NOT reach any other consumer of the cycle observation.
  In particular, the raw unreadable-slot evidence MUST remain intact for the
  code-release and still-programmed checks in
  `DoorCodeAllocator._owner_may_be_programmed` (`allocator/allocator.py`, lines
  370-394) and for the orphan cleanup that feeds it observations
  (`allocator/services.py`, lines 98-113). Exclusion MUST NOT alter the gate's
  logic, MUST NOT relax its fail-closed default for any non-excluded slot, and
  MUST NOT scope the shared pending-adoption set per entry. The shared code
  space remains system-wide.
- **FR-017**: An excluded slot MUST remain unwritable. Reconciliation MUST
  continue to treat it as blocked, MUST NOT clear it, and MUST NOT offer it as
  a free slot for any reservation. Exclusion removes a slot from the gate's
  accounting; it does not return it to the usable pool.
- **FR-018**: An excluded slot MUST NOT contribute any observed code to the
  cycle, and MUST NOT thereby be treated as proven empty. Exclusion is an
  admission that the slot's contents are unknown, not a claim that it is empty.
  No code may be released on the strength of an exclusion: a registry owner
  whose slot is excluded MUST NOT be judged unprogrammed, and MUST NOT have its
  code returned to the pool, because the exclusion is exactly the statement
  that the slot could not be read. This holds for owners belonging to any
  config entry, including owners orphaned by a removed entry, which the
  claim-check in FR-010 does not and cannot cover.
- **FR-019**: The re-issue preview path MUST apply exclusions on exactly the
  same terms as ordinary issuance, so that `force_reissue` is unblocked by an
  exclusion. This requires treating the preview's own unreadable-set rejection
  as part of the gate scope under FR-016; reusing the shared helpers alone does
  not satisfy this requirement.

#### Re-validation and auto-clear

- **FR-020**: On every refresh cycle, the system MUST re-validate every
  exclusion against live observed state. An excluded slot that is observed with
  any status other than UNKNOWN MUST have its exclusion removed automatically,
  and the slot MUST immediately re-enter normal fail-closed management.
- **FR-021**: Auto-clear MUST NOT require, wait for, or be defeasible by any
  operator action. An exclusion MUST be capable of persisting only while the
  condition that justified it persists.
- **FR-022**: Every auto-clear MUST be reported to the operator through the
  log and through the visibility surfaces in FR-025 and FR-026.
- **FR-023**: An exclusion recorded for a slot that is no longer within the
  entry's managed range MUST be dropped on the next cycle. A narrowed managed
  range MUST NOT leave orphaned exclusions behind.
- **FR-024**: Re-validation MUST be driven by the same per-cycle observation
  the gate consumes, so that the set the gate uses and the set that is
  re-validated can never diverge.

#### Operator-visible surfaces

- **FR-025**: While an entry has at least one excluded slot, the system MUST
  maintain a per-entry persistent notification identifying the entry, listing
  the excluded slots, and stating the residual risk in FR-030. The notification
  MUST be reconciled on every cycle and MUST be dismissed when the entry's
  excluded set becomes empty, following the reconcile-and-dismiss pattern
  already used by `report_forced_hold_deferrals`
  (`allocator/services.py`, lines 164-225).
- **FR-026**: Allocator diagnostics
  (`allocator/diagnostics.py`, lines 18-58) MUST report the excluded slots for
  each entry, so that a support request can distinguish "frozen" from
  "deliberately excluded" without further questions.
- **FR-027**: Setting an exclusion, clearing one, and auto-clearing one MUST
  each be logged at a level the operator will see, naming the entry, the lock,
  the slot, and the cause.
- **FR-028**: No surface introduced by this feature — response, notification,
  diagnostics, or log — may contain a raw door code, consistent with feature
  022's masking discipline.

#### Durability

- **FR-029**: Exclusions MUST survive a Home Assistant restart. The startup
  freeze regime means a runtime-only exclusion would be erased by exactly the
  event that re-establishes the freeze, leaving the operator re-excluding every
  dead slot on every restart.
- **FR-030**: The operator MUST be told, at the point of exclusion and for as
  long as it stands, that an excluded slot may still hold a code the
  integration cannot see, and that a newly issued code could therefore collide
  with it. Exclusion is the operator accepting that risk for that slot.
- **FR-031**: The persisted exclusion set MUST NOT be load-bearing for
  correctness. It MUST only ever widen what can be issued, it MUST be
  re-validated against live observed state on every cycle, and it MUST
  self-clear. The system MUST NOT make any decision from the persisted set that
  it could not make from the live observation plus the set.
- **FR-032**: A persisted exclusion for a slot the system can no longer place —
  an unknown slot number, a removed lock, a changed managed range — MUST be
  ignored and dropped rather than treated as an error that blocks the cycle.
  Loading exclusions MUST never prevent an entry from setting up.

#### Scope constraints

- **FR-033**: Exclusion MUST occur only in response to an explicit operator
  invocation. The system MUST NOT exclude a slot automatically, MUST NOT
  propose one, and MUST NOT expire or time out the gate on its own.
- **FR-034**: This feature MUST NOT introduce any operator-supplied
  configuration option in the config or options flow, in keeping with feature
  022's FR-024 and feature 023's FR-027. The remedy is a service invocation.
- **FR-035**: The service MUST be declared in `services.yaml` with field
  selectors, and its name, description, and every field MUST be translated in
  `strings.json` and in each shipped translation, matching the coverage the
  existing services already have.

### Key Entities

- **Slot Exclusion**: one operator decision that one managed slot on one config
  entry is to be left out of the allocator's unreadable-slot accounting. It
  carries the slot number and an optional operator reason. It is scoped to one
  config entry; it is not global and not per lock.
- **Excluded Slot Set**: the set of exclusions recorded for one config entry.
  It is persisted with the entry, re-validated every cycle, and is the only new
  persisted state this feature introduces.
- **Exclusion Request**: one service invocation. It carries exactly one target
  entry, exactly one slot, the set-or-clear flag, the optional reason, and the
  dry-run flag.
- **Exclusion Outcome**: what the service reports — the entry, the lock, the
  resulting excluded set, whether anything changed, and the refusal reason when
  refused.
- **Cycle Observation**: the allocator's existing per-cycle view of physical
  slot state. This feature changes what that view contains for excluded slots
  and nothing else about it.

## Durability: Why Persisting This Is Acceptable

This repository has expensive scar tissue around persisted slot state, and this
section exists so that nobody has to rediscover it.

Version 3.5.0 shipped a slot-reconciliation redesign in which persisted state
was **authoritative**. At real-world scale, first-upgrade adoption failed to
rematch existing slots, **mass-cleared working lock codes**, and then
**permanently wedged** — every slot stuck in `pending_clear`, reporting
"selected but no free managed slot available", and surviving every restart
because the wedge itself was what had been persisted. Releases are immutable,
so the entire redesign was reverted in commit `0622057` ("Revert: Restore 3.4.6
slot management behavior") to restore the proven 3.4.6 behaviour for 3.5.1.
Issue #607 then re-specified reconciliation as stateless, with the physical
Keymaster state plus the calendar as the source of truth on every refresh cycle
and the persisted Store demoted to **cache-only, never load-bearing**.

The rule that came out of that is: *the integration does not persist state it
derived about the world, because when that derivation is wrong the persistence
makes it permanent.*

A slot exclusion is a different category of thing. It is **operator intent**,
not derived observation — the same category as `start_slot` and `max_events`,
which are persisted with the config entry today and have never caused this
class of failure. The distinction is what makes durability acceptable here, and
it holds only because of three properties that MUST be preserved:

1. **It is never load-bearing for correctness.** An exclusion only ever widens
   what can be issued. It can never cause a code to be cleared, can never mark
   a slot usable, and can never cause the system to believe a slot is empty.
   The 3.5.0 failure was persisted state driving destructive writes; an
   exclusion drives no write at all.
2. **It is re-validated every cycle against live truth.** Live observation
   always wins. The persisted set is checked against it on every cycle and
   corrected, which is the stateless discipline of #607 applied to operator
   intent rather than abandoned for it.
3. **It self-clears.** An exclusion cannot outlive the condition that justified
   it. This is the direct structural answer to the 3.5.0 permanent wedge: there
   is no state here that can become permanent, because the only thing keeping
   it alive is the continued unreadability of the slot.

If any future change would make the exclusion set load-bearing, skip
re-validation, or allow an exclusion to persist past the condition, that change
is reintroducing the 3.5.0 failure mode and must be rejected.

### Where it is stored, and the options-flow interaction

The exclusion set is stored in the config entry's `data` as a new
`excluded_slots` key, written through the normal config-entry update path. It
is not stored in `options`, and it is not stored in the reconciliation Store.

`update_listener` (`__init__.py`, lines 252-278) rebuilds the entry on every
options-flow save:

```python
    new_data = config_entry.options.copy()
    ...
    hass.config_entries.async_update_entry(
        entry=config_entry,
        ...
        data=new_data,
        options={},
    )
```

`data` is replaced wholesale from `options`, and `options` is then cleared.
Two consequences follow, both verified against this code and both intended:

- The key must live in `data`. A key written to `options` would be promoted
  into `data` and then erased from `options` on the next save, giving the
  exclusion a confusing dual residence for no benefit.
- An exclusion in `data` survives restarts, which is the point of FR-029, but
  **does not survive an options-flow save**. An operator who reconfigures the
  entry drops every exclusion and returns to fail-closed. That is the safe
  direction, it is cheap to redo, and it means a reconfiguration can never
  silently carry a stale accepted risk forward. It is documented here and in
  Edge Cases so it is expected behaviour rather than a surprise.

## Residual Risk: An Excluded Slot May Still Hold a Code

Excluding a slot does not make it safe. It makes it *ignored*.

The gate exists because an unreadable slot may hold a code this integration
cannot see. Excluding the slot does not reveal that code and does not remove
it. It states that the operator has judged the slot permanently gone and
accepts that the allocator will now issue codes without accounting for whatever
that slot might contain. If the slot is in fact still live and still holding a
code, a newly issued code can collide with it — two credentials opening one
lock, one of them invisible to Home Assistant.

This risk is the entire content of the operator's decision, so it must be
stated where the decision is made and wherever the decision's consequences
persist: in this specification, in the service documentation, and in the
standing notification required by FR-025. It MUST NOT be softened, and it MUST
NOT be buried in a log line the operator will never read.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: One entry with exactly one permanently unreadable managed slot
  issues codes to one hundred percent of its remaining reservations after
  exactly one service invocation, with zero changes made in Keymaster.
- **SC-002**: Three loaded entries, one of which has an unreadable managed
  slot, all resume issuing after exactly one service invocation against the one
  affected entry, and continue issuing across a restart with zero further
  operator action.
- **SC-003**: An exclusion whose slot becomes readable is cleared within one
  refresh cycle in one hundred percent of cases, with zero operator actions
  required, and the slot is subject to the full fail-closed gate on that same
  cycle.
- **SC-004**: Zero excluded slots are ever written to, cleared, or offered as a
  free slot for a reservation.
- **SC-005**: One hundred percent of attempts to exclude a readable slot, a
  claimed slot, or an out-of-range slot are refused, and zero of them change
  any persisted state. There is zero configuration or flag by which an operator
  can defeat those refusals.
- **SC-006**: Zero non-excluded unreadable slots stop causing a fail-closed
  freeze. The gate's behaviour is unchanged for every slot that is not
  explicitly excluded.
- **SC-007**: Dry-run invocations produce zero persisted changes, zero
  notification changes, and zero cycle behaviour changes, while still reporting
  the outcome that would result.
- **SC-008**: For every entry with a non-empty excluded set there is exactly
  one standing persistent notification, and for every entry with an empty
  excluded set there are zero, at the end of every cycle.
- **SC-009**: Raw door codes appear in zero service responses, zero
  notifications, zero diagnostics payloads, and zero log lines attributable to
  this feature.
- **SC-010**: An entry whose persisted exclusion set references slots that no
  longer exist sets up successfully in one hundred percent of cases, with the
  unplaceable entries dropped rather than raised as errors.
- **SC-011**: `force_reissue` succeeds on a non-excluded slot of an entry whose
  only unreadable slot is excluded, in one hundred percent of cases.
- **SC-012**: The end-to-end scenario — freeze observed, slot excluded,
  issuance resumes, slot becomes readable again, exclusion auto-clears, normal
  fail-closed behaviour restored — completes with zero manual intervention
  after the single exclusion call.
- **SC-013**: Operators need to supply zero new configuration values and
  perform zero Keymaster operations to use the remedy.
- **SC-014**: Zero codes are released from the registry on the strength of an
  exclusion. An orphaned owner recorded against an excluded slot is released in
  zero cases, including when the exclusion was set by a different config entry.
- **SC-015**: An entry with an excluded dead slot and one unaccounted readable
  coded slot remains pending adoption in one hundred percent of cases, and
  unrelated entries remain blocked until that entry completes adoption on its
  own terms.

## Alternatives Considered and Rejected

- **A Home Assistant repairs flow.** Attractive, because a repair issue is
  exactly the right shape for "the system has detected a problem and is asking
  you to confirm a remedy", and it would put the prompt in front of the
  operator rather than waiting for them to find a service. Rejected for now
  because it adds a platform dependency, needs its own `RepairsFlow`
  implementation and tests, and requires strings in every shipped translation —
  a disproportionate amount of surface for what is, at the decision point, a
  single boolean. It is deferred rather than refused: a repairs flow can later
  wrap this same service without changing any of its semantics, and this spec
  is deliberately written so that it can.
- **A config-entry options-flow field.** Rejected on two grounds. It violates
  FR-034 and the no-new-configuration constraint inherited from features 022
  and 023, and it mis-frames the action: a per-slot emergency recovery step
  taken once and auto-cleared later is not a setting, and presenting it beside
  `start_slot` invites operators to treat it as one.
- **Runtime-only, in-memory exclusion.** Rejected because it is useless in the
  regime that matters most. The system-wide freeze re-establishes itself at
  every startup via `_adoption_complete`, so a runtime-only exclusion would be
  erased by precisely the event that recreates the problem, and the operator
  would re-exclude every dead slot at every property after every restart.
- **Auto-expiring the gate after a timeout.** Rejected outright. A slot that
  has been unreadable for an hour is not thereby proven empty, and issuing into
  it because a timer elapsed abandons fail-closed behaviour silently and
  without anybody deciding to. The risk in this feature is acceptable only
  because an operator knowingly accepts it for one named slot.
- **Scoping `_pending_adoption` per entry.** Rejected because it breaks the
  shared-code-space invariant deliberately established by feature 022. The
  allocator is system-wide precisely because one parent lock can be driven by
  several entries; letting an entry that has not finished accounting for its
  locks issue from the shared pool is the collision this design exists to
  prevent. The globality is not the bug.
- **Automatically excluding any slot unreadable for N consecutive cycles.**
  Rejected as auto-expiry wearing a different hat, and prohibited by FR-033.
  Only an operator can distinguish a decommissioned slot from a long outage.
- **Making exclusion return the slot to the usable pool.** Rejected. The slot's
  contents remain unknown, so writing to it could clobber an invisible
  credential. Exclusion removes the slot from the gate's accounting only; the
  planner's existing `BLOCKED, "unreadable"` treatment stays exactly as it is.

## Assumptions

- The reconciliation planner's existing per-slot degradation is correct and
  sufficient: an UNKNOWN slot is already blocked and already never offered as
  free, so this feature does not need to change planning behaviour at all.
- `build_cycle_observation` is the sole producer of the allocator's unreadable
  slot set for an entry. `_adoption_complete` and the re-issue preview's own
  unreadable-set rejection are the gating consumers of it, alongside
  `unaccounted_slots`. Those three checks are the whole gate scope. The
  observation has other readers that are not part of the gate: notably
  `_owner_may_be_programmed` and the orphan cleanup that supplies it
  observations read the same unreadable-slot set as release evidence, which is
  why FR-016 requires the filtering to be gate-only rather than applied to the
  observation for all readers.
- `SlotStatus.UNKNOWN` is a faithful signal of "this slot's state could not be
  read", so refusing to exclude anything else is a sound guard.
- The shared allocator, the persisted registry, and the system-wide uniqueness
  boundary from feature 022 remain in force and are not modified by this
  feature.
- An operator who invokes this service has already confirmed in Keymaster that
  the slot is genuinely gone. The system cannot verify that and does not try.
- One Home Assistant system remains the uniqueness boundary, as in features 022
  and 023.

## Out of Scope

- **Changing, relaxing, or per-entry scoping the fail-closed gate.** The gate
  stays exactly as it is for every slot that is not explicitly excluded, and
  `_pending_adoption` stays system-wide.
- **Bulk exclusion.** No wildcard, no range, no "exclude all unreadable slots"
  action. Each exclusion is one deliberate decision about one slot.
- **A force or override flag.** The three refusal guards have no escape hatch
  by design. A readable or claimed or out-of-range slot cannot be excluded at
  all.
- **A repairs flow.** Deferred, as recorded above. This feature ships the
  service; a repair issue may later wrap it.
- **New configuration or options-flow fields.** None, per FR-034.
- **Recovering or reading the contents of an excluded slot.** The system does
  not attempt to discover what an unreadable slot holds. That is the residual
  risk, and it is accepted rather than solved.
- **Repairing Keymaster.** Removing the dead slot from Keymaster remains the
  real fix. This feature makes the integration survivable until that happens.
- **Changing the sensor attribute surface.** No attribute is added to advertise
  that a slot is excluded, so the downstream captive-portal contract over
  `slot_code`, `slot_name`, and `last_four` is untouched.
- **Code length (#741)**: unchanged, as in features 022 and 023.

## Relationship to Other Work

- **Resolves #802**, which reports the entry-wide freeze and explicitly frames
  it as an operability gap rather than a correctness bug. This spec additionally
  documents the system-wide startup regime, which #802 does not cover and which
  is the more damaging of the two.
- **Depends on** the shared allocator and registry from feature 022 (#743) and
  does not alter its uniqueness guarantee or its system-wide scope.
- **Follows the service precedent of feature 023** (#735): dual entity-or-lock
  targeting, dry-run plus optional service response, registration from
  `register_allocator_services`, and a single-target-only discipline.
- **Unblocks `force_reissue`**, because the re-issue preview path consumes the
  same cycle observation. Its own independent unreadable-set rejection is
  included in the gate scope so that the unblocking is complete rather than
  partial.
- **Honours #607's stateless redesign** and the `0622057` revert by keeping the
  only new persisted state non-authoritative, re-validated every cycle, and
  self-clearing.
