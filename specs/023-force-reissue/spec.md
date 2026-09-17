<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Feature Specification: Force Re-Issue of a Door Code

**Feature Branch**: `023-force-reissue`
**Created**: 2026-09-17
**Status**: Draft
**Input**: GitHub issue #735 — "No way to force re-issue of a door code for a
reservation". Once a code is on a lock, reconciliation retains it whenever it
does not match a freshly generated value, treating it as a manual override. A
**bad** code is therefore retained indefinitely, and the only remedy today is
manual intervention against Keymaster.

## Problem Statement

Reconciliation deliberately keeps any code it did not itself generate.
`_resolve_observed_code` (`coordinator_helpers/reservations.py`, line 224)
returns `(observed_code, "manual_observed")` whenever the PIN observed on the
lock does not match the code the configured generator would produce for that
booking window.

That retention is correct almost all of the time. It protects a code an
operator set by hand in Keymaster, and it shields a guest who is mid-stay from
a change to the generator or to the booking feed. It is the same instinct that
makes feature 022 adopt pre-existing codes rather than rotate them.

But retention is unconditional. It cannot distinguish "this code is
intentionally different" from "this code is wrong and must go". So a bad code
survives every reconcile cycle forever. Three conditions produce a bad code
that the system cannot currently correct:

1. **A duplicate across config entries.** Two reservations on one shared parent
   lock hold the same code. Both sides are observed, both are retained as
   `manual_observed`, and both are adopted into the shared registry as an
   adoption conflict. Improving the generator fixes future bookings and leaves
   the live conflict untouched. This condition is currently live in a
   production property running ten config entries against one shared parent
   lock with carved-out slot ranges.
2. **A disclosed code.** A code that has leaked — posted, forwarded, screen
   shotted, left in a message thread — must be rotated even though nothing
   about it is structurally wrong.
3. **A migration onto a new generation scheme.** An operator who changes the
   configured generator wants existing reservations moved onto the new scheme,
   but retention pins them to the old one for the life of the booking.

This feature adds the one thing missing from all three: an operator-invoked,
single-target escape hatch that clears the retained state for exactly one
target so that the next reconcile cycle issues a fresh code through the shared
allocator.

## Relationship to Feature 022

Feature 022 (`022-shared-code-allocator`, issue #743) introduced a single
`DoorCodeAllocator` held on shared integration data with a persisted allocation
registry, guaranteeing that no two concurrent reservations anywhere on one Home
Assistant system are issued the same code.

Feature 022 deliberately does **not** heal duplicates that already existed
before it was installed. Its FR-022 records both sides of an adoption conflict
and reports it to the operator, and rotates neither, because rotating either
side would strip a mid-stay guest of the credential they are holding. Its
FR-014 further refuses to release an allocation whose code is still programmed
on a managed lock. The result is safe, and permanent: the conflict is visible
and inert.

**This feature is the deliberate counterpart to that decision.** Feature 022
detects and reports; feature 023 is the operator-invoked remedy that heals.
The healing is not automatic precisely because feature 022 established that
the system cannot safely decide on its own which side of a conflict may lose
its code. A human decides; this feature executes that decision safely.

Feature 022's out-of-scope section names this feature as the remedy for the
live production duplicate, and its FR-022 names #735 explicitly as the owner of
correcting an existing duplicate. This specification discharges that reference.

## Context

The pieces this feature composes already exist:

- **Shared allocator and registry** (`allocator/allocator.py`,
  `allocator/registry.py`, `allocator/issuance.py`, `allocator/models.py`).
  Codes are issued through `async_allocate` / `async_resolve_cycle` and
  released through the registry. Sensors no longer generate codes of their own;
  feature 022 removed direct generation from the sensor path.
- **The release guard** (`DoorCodeAllocator._release_guard_reason`). It refuses
  to release an allocation when the record has more than one owner
  (`adoption_conflict`), when the owner names a lock for which the current
  observation set contains no coverage of that lockname and that specific slot
  in `managed_slots` (`unverifiable_lock`), or when the code still appears
  programmed (`code_still_programmed`).
- **Observation semantics** (`CycleObservation`). `SlotStatus.UNKNOWN` places a
  slot in `unreadable_slots` and means **unreadable**, not empty.
  `SlotStatus.FREE` is known-empty. The guard treats an unreadable managed slot
  as possibly still programmed.
- **Masked diagnostics** (`code_ref`, feature 022 FR-025). Raw door codes never
  reach logs, diagnostics, or notifications.
- **Existing service surface** (`services.yaml`). `checkout` and `set_state`
  target a Rental Control sensor entity; `clear_orphaned_codes` is a
  domain-level service with a `dry_run` field and an optional service response.
  This feature follows both precedents.
- **Downstream consumption.** A captive-portal service authenticates guests by
  door code, reading `slot_code`, `slot_name`, and `last_four` from calendar
  sensor attributes. A re-issue changes a credential that a guest may already
  have been given, so the observable sensor behaviour during the transition is
  part of the specification, not an implementation detail.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Heal a Live Duplicate Code (Priority: P1)

As a property manager whose shared parent lock is currently carrying the same
door code on two different reservations, I want to choose one of the two
reservations and force it onto a fresh, unique code, so that the duplicate is
resolved without disturbing the guest I chose to leave alone.

**Why this priority**: This is the condition that is live in production today
and the one feature 022 explicitly declined to fix on its own. It is also the
only one of the three motivating cases that is a security defect rather than an
operational inconvenience: a duplicate code authorises the wrong guest in the
downstream captive portal.

**Independent Test**: Create the conflict — two reservations in different
config entries holding the same code on one shared parent lock, both recorded
in the registry as an adoption conflict. Invoke the re-issue service against
one of the two reservation sensors. Confirm that reservation receives a
different code, that the other reservation's code is untouched, and that the
registry no longer records a conflict for that code.

**Acceptance Scenarios**:

1. **Given** two reservations in different config entries are recorded in the
   registry as owners of one code, **When** the operator forces a re-issue
   against one of them, **Then** that reservation is issued a new code that is
   unique across all entries, and the other reservation keeps its code
   unchanged.
2. **Given** the re-issued reservation's old code is still programmed on the
   physical lock, **When** the new code is issued, **Then** the old code is not
   yet returned to the registry as available, and is returned only once the
   release guard is satisfied.
3. **Given** the duplicate has been healed on one side, **When** the next
   reconcile cycle runs, **Then** the adoption conflict for that code is no
   longer reported to the operator.
4. **Given** the operator targets a reservation that is not part of any
   duplicate, **When** the re-issue runs, **Then** it succeeds normally; the
   service is not restricted to conflict participants.
5. **Given** two reservations colliding on one code on one shared parent lock,
   **When** the operator forces a re-issue on one side and the replacement code
   has been written and confirmed on that side's slot, **Then** the end state is
   two distinct codes, the registry record for the old code has been reduced
   from two owners to one, and no duplicate is reported for it any longer.

---

### User Story 2 - Rotate a Disclosed Code (Priority: P1)

As a property manager who has learned that a guest's door code was disclosed to
someone who should not have it, I want to force that reservation onto a new
code immediately, so the disclosed value stops opening the door.

**Why this priority**: Rotation after disclosure is time-sensitive and has no
workaround short of hand-editing Keymaster, which desynchronises the
integration from the lock. It shares every mechanism with User Story 1, so it
is delivered by the same slice.

**Independent Test**: Take a reservation holding a known code, force a
re-issue, and confirm that after the lock write is confirmed the reservation
holds a different code and the sensor publishes the new one.

**Acceptance Scenarios**:

1. **Given** an active reservation holding a disclosed code, **When** the
   operator forces a re-issue with the checked-in override, **Then** a new
   unique code is issued and written to the reservation's slot.
2. **Given** the re-issue has completed and the lock write is confirmed,
   **When** the calendar sensor updates, **Then** `slot_code` and `last_four`
   reflect the new code so the downstream captive portal authenticates on the
   new value.
3. **Given** the disclosed code is later released back to the registry, **When**
   a future allocation requests a code, **Then** that value is eligible to be
   issued again — see **Known Limitation: Released Codes Are Not Retired**.

---

### User Story 3 - Migrate a Reservation onto a New Scheme (Priority: P2)

As a property manager who has changed the configured code generator, I want to
move a specific existing reservation onto the new scheme rather than waiting
for the booking to end, so my in-flight inventory is consistent with my new
configuration.

**Why this priority**: It is a real motivating case from the issue, but it is
an operational convenience rather than a defect or a security exposure. An
operator can also simply wait for the booking to turn over.

**Independent Test**: Change a config entry's generator, force a re-issue
against one existing reservation in that entry, and confirm the resulting code
is the one the newly configured generator prefers — or a collision-resolved
code derived from that preference when the preferred value is already taken.

**Acceptance Scenarios**:

1. **Given** a reservation whose code predates a generator change, **When** the
   operator forces a re-issue, **Then** the new code is obtained through the
   shared allocator using the config entry's currently configured generator.
2. **Given** the newly preferred code is already allocated to another
   reservation, **When** the re-issue runs, **Then** a collision-resolved code
   is issued rather than a duplicate, and the operator is told the code was
   collision-resolved.
3. **Given** the operator wants to see the outcome before committing, **When**
   they invoke the service in dry-run mode, **Then** the response shows what
   would be issued and nothing is changed on the lock or in the registry.

---

### User Story 4 - Clear a Ghost Code with No Reservation (Priority: P2)

As a property manager who can see a bad code on a managed slot that no current
reservation claims, I want to target that lock and slot directly, so the code
can be corrected even though there is no sensor entity to point at.

**Why this priority**: A bad code can outlive the reservation that produced it
— a cancelled booking, a feed that dropped a reservation, or a slot left
programmed by an earlier failure. Entity targeting cannot reach these, so slot
targeting is required for the feature to cover the problem it exists to solve.
It is P2 because the common cases are reachable by entity.

**Independent Test**: Leave a managed slot programmed with a code that no
active reservation owns, invoke the service with an explicit lock and slot
number, and confirm the orphaned code is cleared from the slot and its registry
record handled under the same release guard.

**Acceptance Scenarios**:

1. **Given** a managed slot holds a code that no active reservation claims,
   **When** the operator targets that lock and slot number, **Then** the system
   acts on that slot without requiring a reservation entity.
2. **Given** the operator supplies a slot number that the target config entry
   does not manage, **When** the service is invoked, **Then** it refuses with a
   clear error and changes nothing.
3. **Given** the operator supplies both an entity target and an explicit lock
   and slot, **When** the service is invoked, **Then** it refuses with a clear
   error rather than guessing which target was meant.

---

### Edge Cases

- **Target is checked in**: re-issuing revokes a credential the guest is
  holding right now. The service refuses unless the caller explicitly opts in.
- **Old code still physically programmed**: the old code cannot be released
  yet, or another entry could be issued that same value and recreate the exact
  duplicate this feature exists to fix. The re-issue still proceeds; the
  release is deferred.
- **Lock unavailable or slot unreadable**: an unreadable slot
  (`SlotStatus.UNKNOWN`) is not an empty slot. The system must not treat it as
  proof the old code is gone.
- **Invoked twice**: a second invocation against a target already carrying a
  freshly issued code must not chain another rotation on top of the first.
- **Lockless config entry**: there is no physical write to confirm and no
  release guard coverage to wait for, so the new code publishes immediately.
- **Code space exhausted**: no unique replacement exists. The re-issue must
  fail rather than issue a duplicate, and must leave the existing code in
  place.
- **Reservation ends between invocation and the next reconcile cycle**: the
  pending forced re-issue must not resurrect or re-create a slot for a booking
  that is over.
- **Target entity is not a Rental Control reservation sensor**: refused.
- **Home Assistant restarts between invocation and the next reconcile cycle**:
  the pending re-issue is in-memory only and is silently dropped. This is
  documented behaviour rather than a fault: the operator invokes the service
  again after the restart. See **FR-013**.

## Requirements *(mandatory)*

### Functional Requirements

#### Service surface and targeting

- **FR-001**: The system MUST expose an operator-invocable service that forces
  re-issue of the door code for exactly one target. It MUST support an optional
  service response.
- **FR-002**: A call MUST identify its target in exactly one of two ways: a
  Rental Control reservation sensor entity, following the existing `checkout`
  and `set_state` targeting precedent; or an explicit managed lock together
  with an explicit slot number. Slot targeting exists because a bad code can
  survive with no reservation attached.
- **FR-003**: A call that supplies both targeting forms, or neither, MUST be
  refused with a clear error and MUST change nothing.
- **FR-004**: A call MUST act on exactly one target. The service MUST NOT
  accept or imply any bulk, wildcard, or "fix all detected conflicts" mode.
- **FR-005**: A slot-targeted call MUST refuse, with a clear error and no
  change, when the named slot is not a slot the addressed config entry manages.

#### Safety guards

- **FR-006**: The service MUST refuse to act on a target whose reservation is
  currently checked in, unless the caller passes an explicit force flag. The
  default MUST be to refuse. This guard exists because a re-issue revokes a
  credential the guest is holding at that moment.
- **FR-007**: The service MUST support a dry-run mode whose service response
  reports exactly what would happen — including the guards that would apply and
  the code that would be issued — while changing nothing on any lock and
  nothing in the registry.
- **FR-008**: When no unique replacement code can be obtained, the service MUST
  fail, MUST report why, and MUST leave the target's existing code in place.
  It MUST NOT issue a duplicate.
- **FR-009**: Repeating the same call MUST be safe. A target that is already
  carrying a re-issue that has not yet completed MUST NOT accumulate a second
  pending re-issue, and a target whose re-issue has completed MUST NOT be
  rotated again by a repeat of the original call.
- **FR-010**: When the addressed lock is unavailable, or the addressed slot's
  observed status is unreadable, the service MUST NOT treat the slot as empty.
  It MUST either defer the affected step under the existing release guard or
  refuse with a clear reason, and in no case may it conclude that the old code
  has been cleared.

#### Re-issue mechanics

- **FR-011**: The replacement code MUST be obtained through the shared door
  code allocator so that it is unique across every config entry on the system.
  The service MUST NOT call a code generator directly and MUST NOT reintroduce
  code generation into the sensor path.
- **FR-012**: The replacement code MUST be derived from the addressed config
  entry's currently configured generator, receiving that generator's preferred
  code when it is available and a collision-resolved code when it is not,
  exactly as an ordinary allocation would.
- **FR-013**: A forced re-issue MUST suppress the `manual_observed` retention
  in `_resolve_observed_code` for the addressed target for the next reconcile
  cycle only. It MUST NOT disable that protection permanently, for other
  targets, or for the same target on subsequent cycles. The suppression MUST be
  held in memory only and MUST introduce no new persisted state; a forced
  re-issue that has been accepted but not yet consumed by a reconcile cycle
  therefore lapses on a Home Assistant restart, and the operator invokes the
  service again.
- **FR-014**: The replacement code MUST be recorded in the shared registry as
  owned by the addressed reservation identity before or at the moment it is
  written to the lock, so no other entry can be issued the same value.
- **FR-015**: For a lock-backed target, the replacement code MUST be written to
  the target's managed slot through the integration's normal reconciliation
  path rather than by a separate direct write.
- **FR-016**: For a lockless config entry, which has no physical write to
  confirm, the replacement code MUST be published immediately.
- **FR-017**: A forced re-issue MUST NOT re-create or resurrect a slot for a
  reservation that has ended, been cancelled, or disappeared from the feed
  between the service call and the next reconcile cycle.

#### Disposition of the replaced code

- **FR-018**: The replaced code MUST be released back to the shared registry so
  it becomes available for future allocation. It MUST NOT be retired,
  blacklisted, or otherwise permanently withheld.
- **FR-019**: Release of the replaced code MUST pass through the existing
  allocator release guard, with one narrow exemption for forced re-issue. The
  physical-state conditions MUST apply in full and MUST NOT be exempted: the
  replaced code MUST NOT be released while the targeted owner's lock and that
  specific slot are not covered by a current observation, nor while the code
  may still be programmed. Releasing a code that is still physically on the
  lock would allow another entry to be issued that same value and would
  recreate the exact duplicate this feature exists to fix. The exemption is
  that the multiple-owner condition MUST NOT block release of the one owner
  that this forced re-issue deliberately re-homed. That condition exists to
  prevent releasing when it is ambiguous which physical code belongs to which
  owner; a forced re-issue removes that ambiguity for that one owner by
  definition, because the system has just moved it to a new code on purpose.
  Without this exemption the feature could never heal a duplicate, since
  multiple ownership of one code is precisely what a duplicate is. The
  exemption MUST be strictly limited to the targeted owner of that forced
  re-issue: it MUST NOT release any other owner of the same record, and it
  MUST NOT apply to any ordinary, non-forced release path; routine sweeps,
  config entry removal, and orphan cleanup MUST continue to apply the full
  unmodified guard.
- **FR-020**: Because the physical-state conditions still defer release — the
  replaced code will normally remain programmed until the new code overwrites
  the slot — the system MUST support and correctly represent the intermediate
  state in which the new code has been issued and recorded while the old code
  is still pending release. In that state the old code MUST remain unavailable
  to any other allocation.
- **FR-021**: A replaced code whose release remains deferred MUST be retried on
  subsequent reconcile cycles and MUST be reported to the operator with its
  retention reason, so an indefinitely stuck release is visible rather than
  silent. A deferral caused solely by the exempted multiple-owner condition
  MUST NOT be reported as stuck.

#### Observability

- **FR-022**: The service response for a real, non-dry-run execution MUST
  identify the outcome using the masked code reference and MUST NOT contain the
  raw replacement code. The operator reads the real code from the reservation
  sensor as usual.
- **FR-023**: The dry-run response MUST contain the raw code that would be
  issued. This is the single deliberate carve-out from masked reporting,
  because a preview without the code is not a useful preview. It MUST apply to
  dry-run responses only.
- **FR-024**: All logging, diagnostics, and notifications arising from a forced
  re-issue MUST use masked code references and MUST NOT contain raw door codes,
  in keeping with feature 022's FR-025.
- **FR-025**: Every forced re-issue MUST be logged with enough detail for an
  operator to reconstruct afterwards which target was acted on, who or what
  invoked it, whether the checked-in override was used, which masked code was
  replaced by which masked code, and the disposition of the replaced code. This
  operation changes a credential a guest may already hold, so it must be
  auditable.
- **FR-026**: During a lock-backed re-issue, the reservation's calendar sensor
  MUST continue to publish the last confirmed code — the old one — until the
  new code's lock write is physically confirmed, and MUST then publish the new
  code. It MUST NOT publish an unconfirmed replacement code, so the downstream
  captive portal never authenticates on a value the lock does not yet hold.
  This feature MUST NOT add, remove, or change any calendar sensor attribute;
  the attribute surface consumed downstream — `slot_code`, `slot_name`, and
  `last_four` — stays exactly as it is.

#### Scope constraints

- **FR-027**: This feature MUST NOT introduce any new operator-supplied
  configuration option, in keeping with feature 022's FR-024. The remedy is a
  service invocation, not a setting.
- **FR-028**: Re-issue MUST occur only in response to an explicit operator
  invocation. The system MUST NOT force a re-issue automatically in response to
  a detected duplicate, a disclosed-code heuristic, or a generator change.

### Key Entities

- **Force Re-Issue Request**: one operator invocation. Carries exactly one
  target — a reservation sensor entity, or a lock plus slot number — together
  with the checked-in override flag and the dry-run flag.
- **Re-Issue Target**: the resolved subject of a request. Either a reservation
  identity with its owning config entry and, if lock-backed, its lockname and
  slot; or a bare lockname and slot with no reservation attached.
- **Pending Re-Issue**: the transient state between an accepted request and the
  reconcile cycle that consumes it. It is what suppresses `manual_observed`
  retention for exactly one cycle for exactly one target, and it is cleared
  once consumed.
- **Replaced Code**: the code the target held before the re-issue. It remains
  registry-held and unavailable to other allocations until the release guard
  permits its release, after which it returns to the pool.
- **Re-Issue Outcome**: what the service reports — whether the request was
  accepted, refused, or previewed; the masked references for the replaced and
  replacement codes; whether the replacement was generator-preferred or
  collision-resolved; and the disposition or retention reason of the replaced
  code.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A live duplicate involving two reservations across two config
  entries on one shared parent lock is reduced to zero duplicates by exactly
  one service invocation against one of the two reservations, with zero change
  to the other reservation's code.
- **SC-002**: Zero forced re-issues produce a code that duplicates any code
  held by any other reservation on the system, under any generator, including
  two entries whose reservations share identical dates under the default
  `date_based` generator.
- **SC-003**: Zero replaced codes are released back to the pool while still
  programmed on a managed lock or while the lock and slot cannot be observed.
  Zero owners other than the deliberately re-homed target are released by a
  forced re-issue, and the ordinary release paths behave identically to before
  this feature.
- **SC-004**: One hundred percent of invocations against a currently checked-in
  reservation are refused when the force flag is absent, and succeed when it is
  present.
- **SC-005**: Dry-run invocations produce zero changes to any lock, any
  registry record, and any sensor state, while still reporting the code that
  would be issued.
- **SC-006**: Raw door codes appear in zero log lines, zero diagnostics
  payloads, zero notifications, and zero non-dry-run service responses
  attributable to this feature.
- **SC-007**: Invoking the same request twice produces exactly one code
  rotation.
- **SC-008**: Every forced re-issue is reconstructable from the logs alone:
  target, invoker, override use, masked replaced code, masked replacement code,
  and replaced-code disposition.
- **SC-009**: Operators need to supply zero additional configuration values to
  use the remedy.
- **SC-010**: An invocation that cannot obtain a unique replacement code leaves
  the target's existing code unchanged in one hundred percent of cases.
- **SC-011**: A bad code on a managed slot with no reservation attached is
  correctable without any manual Keymaster intervention.

## Known Limitation: Released Codes Are Not Retired

The replaced code is returned to the shared registry as available for future
allocation (FR-018). It is **not** retired or blacklisted.

The consequence is explicit and accepted: a code rotated *because it was
disclosed* can legitimately be issued again to a later reservation. User Story
2 is therefore only partly served — the disclosed value stops opening the door
for the affected reservation immediately, which is the urgent part, but the
value itself is not permanently burned.

This is a deliberate decision, not an oversight. Retiring codes would
monotonically shrink a four-digit code space that feature 022 worked to make
fully usable, and would require persisted retirement state with its own
expiry policy and its own operator remedy for exhaustion. Operators who need a
value permanently withdrawn have the existing recourse of raising the
configured code length. No retirement flag is to be added to this service.

## Assumptions

- The operator invoking the service has already decided which side of a
  duplicate should lose its code. The system does not and should not make that
  choice.
- The shared allocator and its persisted registry from feature 022 are present
  and are the sole source of issued codes. This feature adds no new code
  generation path.
- The existing release guard's physical-state conditions are a sufficient and
  correct safety condition for returning a replaced code to the pool; this
  feature reuses them unchanged rather than defining its own, and narrows only
  the multiple-owner condition, and only for the deliberately re-homed owner.
- The integration's existing reconciliation path is the correct vehicle for
  writing the replacement code to a lock, so the service does not need its own
  write path.
- A reservation's existing stable identity remains the key under which the
  replacement allocation is recorded.
- One Home Assistant system remains the uniqueness boundary, as in feature 022.

## Out of Scope

- **Bulk or batch re-issue**: no "fix all detected duplicates", no wildcard
  target, no multi-entity call. Each invocation is surgical and affects exactly
  one target. A remedy that changes many guests' credentials in one action is
  exactly the failure mode feature 022 avoided by declining to auto-heal.
- **Code retirement or blacklisting**: see **Known Limitation: Released Codes
  Are Not Retired**. No retire flag, no deny list, no cooling-off period.
- **Automatic healing**: the system never forces a re-issue on its own. Feature
  022's detection and reporting of adoption conflicts stays as it is; this
  feature does not convert it into an automatic action.
- **New configuration options**: none, per feature 022's FR-024.
- **An in-flight re-issue indicator on the sensor**: no attribute is added to
  signal that a forced re-issue is pending. The calendar sensor's attribute
  surface is unchanged by this feature, so the downstream captive-portal
  contract over `slot_code`, `slot_name`, and `last_four` is untouched.
- **Changing the default retention behaviour**: `_resolve_observed_code`
  continues to retain `manual_observed` codes for every target that has not
  been explicitly force-re-issued. This feature suppresses retention narrowly
  and temporarily; it does not weaken it.
- **Code length (#741)**: unchanged, as in feature 022.
- **A user-supplied replacement code**: the operator chooses *that* the code
  changes, not *what* it changes to. Accepting an arbitrary operator-supplied
  value would bypass the allocator's uniqueness guarantee.
- **Recovering from two Home Assistant systems driving one parent lock**: still
  outside the uniqueness boundary, as in feature 022.

## Relationship to Other Work

- **Completes #743 / feature 022**: 022's FR-022 records and reports a
  pre-existing duplicate without rotating either side, and names #735 as the
  owner of correcting it. This feature is that correction.
- **Depends on** the shared allocator, the persisted registry, and the release
  guard delivered by feature 022. It is not implementable without them, which
  is why it follows rather than precedes that work.
- **Reuses** feature 022's masked `code_ref` reporting discipline (FR-025) and
  its no-new-configuration constraint (FR-024).
- **Follows the service precedent** of `checkout` and `set_state` for entity
  targeting and of `clear_orphaned_codes` for dry-run plus service response.
