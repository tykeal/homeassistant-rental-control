<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Feature Specification: Stateless Slot Reconciliation

**Feature Branch**: `013-stateless-reconciliation`
**Created**: 2026-06-23
**Status**: Draft
**Input**: User description: "Redesign Rental Control slot reconciliation for
issue #607 so every refresh derives truth from physical Keymaster slot state
and the current calendar, while persisted slot mappings become cache-only and
cannot wedge correctness. The redesign must prevent duplicate slot assignment
when reservations change by matching physical slots to reservations by stable
slot name identity, not by dates or generated code."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Prevent Duplicates Across Reservation Changes (Priority: P1)

As a property manager, I want a reservation whose dates, length, or generated
code changes to remain in the same physical Keymaster slot, so the same guest
never receives or leaves behind a second active slot assignment.

**Why this priority**: This is the load-bearing requirement for returning to a
stateless model. Persisted authoritative mappings were originally introduced to
prevent duplicate slots after reservation changes; the redesign must preserve
that safety without making persisted data authoritative.

**Independent Test**: Can be fully tested by programming a reservation, changing
its dates or length so its expected code or validity window changes, running a
normal refresh, and verifying exactly one managed slot contains that
reservation by name after reconciliation.

**Acceptance Scenarios**:

1. **Given** a reservation already occupies a managed slot and the stay length
   increases, **When** the next refresh compares current calendar data with
   physical Keymaster slots, **Then** the existing slot is updated in place and
   no second slot is assigned to that reservation.
2. **Given** a reservation already occupies a managed slot and the stay length
   decreases, **When** reconciliation runs, **Then** the same physical slot is
   retained with the shortened validity window and no duplicate slot exists.
3. **Given** a reservation's full date range shifts and the deterministic code
   for the reservation changes, **When** reconciliation runs, **Then** the slot
   matching the reservation name is updated in place with the new dates and
   code, the old code is replaced only after the same slot is confirmed safe
   for reprogramming, and no additional slot is allocated.
4. **Given** a same-guest rebooking or back-to-back stay presents the same
   guest identifier across adjacent reservations, **When** reconciliation
   derives the desired physical state, **Then** the reservation identity is
   matched by trim-aware slot name and start-time order so the guest is not
   duplicated across slots.
5. **Given** two concurrent upcoming reservations have duplicate guest names,
   **When** reconciliation matches physical slots to the ordered desired set,
   **Then** the reservations are disambiguated by start-time order and each
   selected reservation appears in exactly one managed slot.
6. **Given** a managed slot already has the correct reservation name, code, and
   validity window, **When** reconciliation runs, **Then** no lock programming
   change is performed for that slot.

---

### User Story 2 - Reconcile From Physical Truth Every Refresh (Priority: P1)

As a property manager, I want Rental Control to re-derive correct slot contents
from Keymaster and the calendar on every refresh, so stale saved state, deleted
saved state, first upgrades, and entity-readiness timing cannot wedge guest
codes.

**Why this priority**: The authoritative persisted-state design introduced
stuck pending-clear fences, stale-store recovery failures, adoption timing
problems, and cases where codes never unwedge. Stateless reconciliation removes
those failure modes by refusing to let persisted data be required for
correctness.

**Independent Test**: Can be fully tested by starting with missing, stale, or
contradictory persisted slot-mapping data, running normal refresh cycles, and
verifying physical slot contents plus the calendar determine the resulting
managed slots.

**Acceptance Scenarios**:

1. **Given** persisted slot mappings are absent because the store was deleted
   or this is the first upgrade, **When** a refresh observes existing coded
   Keymaster slots, **Then** matching slots are recognized by slot name and
   reconciled in place rather than wiped or requiring manual store repair.
2. **Given** persisted slot mappings contradict readable physical Keymaster
   state, **When** reconciliation runs, **Then** physical Keymaster state and
   the current calendar determine which slots stay, reset, or update.
3. **Given** a Keymaster entity is not readable during one refresh, **When**
   reconciliation evaluates the slot, **Then** the slot is treated
   conservatively for that cycle and is re-evaluated normally on a later
   refresh without relying on saved state to be correct.
4. **Given** the persisted cache contains stale pending-clear information for a
   slot that is now physically empty, **When** the next refresh confirms the
   physical slot is empty, **Then** the slot is available for normal assignment.
5. **Given** no calendar reservation belongs in a physical managed slot,
   **When** reconciliation runs, **Then** that slot is reset and freed only
   after the empty physical state is confirmed.

---

### User Story 3 - Program the Soonest Eligible Reservations (Priority: P1)

As a property manager, I want managed Keymaster slots to hold the soonest
eligible reservations, so arriving guests receive access before farther-future
stays consume limited slot capacity.

**Why this priority**: The system manages a limited number of lock slots for
whole-unit, non-overlapping reservations. The correct business outcome is a
clean start-time ordered set of soonest reservations, while protecting active
guests.

**Independent Test**: Can be fully tested by providing more eligible
reservations than managed slots, including changed and unchanged physical slot
state, and verifying the selected physical slot set equals the soonest-N
calendar reservations once required resets and writes are confirmed.

**Acceptance Scenarios**:

1. **Given** eligible reservations exceed managed slot capacity, **When**
   reconciliation computes the should-be-in-Keymaster set, **Then** the set
   contains the soonest reservations by start time up to slot capacity.
2. **Given** a reservation drops out of the soonest-N set, **When**
   reconciliation runs, **Then** its slot is reset and freed, and the newly
   eligible reservation is programmed after a confirmed reset or into an
   already empty slot.
3. **Given** all managed slots are full of farther-future reservations and a
   nearer reservation appears, **When** required physical operations complete,
   **Then** the nearer reservation is represented in a managed slot and the
   farthest unprotected reservation is no longer assigned.
4. **Given** a farther-future reservation is not selected because capacity is
   full, **When** reconciliation completes, **Then** it remains unprogrammed
   until it enters the soonest-N set.

---

### User Story 4 - Reuse Slots Only After Confirmed Reset (Priority: P1)

As a property manager, I want a managed slot to receive a new or replacement
PIN only after Rental Control has confirmed the physical PIN and name are
empty, so stale access is not left active while the same slot is reprogrammed.

**Why this priority**: Lock-code management controls physical property access.
A slot that still holds a PIN must not be reused, even when the desired plan has
a new reservation waiting.

**Independent Test**: Can be fully tested by forcing reset confirmation to lag
or fail, running refresh cycles, and verifying no different reservation is
programmed into that physical slot until empty state is confirmed.

**Acceptance Scenarios**:

1. **Given** reconciliation must replace the PIN, name, or reservation in a
   physical slot, **When** the slot still physically holds a PIN or non-empty
   name, **Then** no replacement code is applied to that slot.
2. **Given** a reset command completes but Keymaster reports lingering name or
   PIN state, **When** the next assignment opportunity is evaluated, **Then**
   the slot remains unavailable and is retried or reported rather than reused.
3. **Given** Keymaster later reports both name and PIN as empty, blank, or
   `unknown`, **When** reconciliation runs, **Then** the slot is considered
   physically free and may receive an unassigned should-be reservation.
4. **Given** Keymaster reports `unavailable` for slot text state, **When**
   reconciliation runs, **Then** the slot is not assumed empty or occupied for
   reassignment safety and is re-evaluated on a later refresh.
5. **Given** the same reservation remains matched to the same physical slot but
   its generated PIN must change, **When** reconciliation replaces the old PIN,
   **Then** the reservation retains ownership of that slot, no second slot is
   allocated, and the replacement PIN is applied only after the old physical
   slot contents are confirmed empty.

---

### User Story 5 - Preserve Existing Guest Access Semantics (Priority: P1)

As an existing Rental Control user, I want the redesign to change only how
managed slots reconcile, so upgrades preserve manual overrides, active guest
protection, code timing, sensors, and display behavior that property managers
already rely on.

**Why this priority**: The redesign fixes reliability and safety problems, but
must not change established user-visible lock-code semantics unrelated to the
source-of-truth model.

**Independent Test**: Can be fully tested by exercising each preserved behavior
before and after reconciliation changes and verifying the same observable slot
names, times, codes, and sensors while stateless reconciliation corrects slot
contents.

**Acceptance Scenarios**:

1. **Given** manual check-in or checkout time-of-day overrides exist and Honor
   Event Times is off, **When** the desired reservation state is derived,
   **Then** the manual time overrides determine the programmed access window.
2. **Given** Honor Event Times is on, **When** a calendar event contains usable
   event times, **Then** those event times are honored instead of built-in
   check-in and checkout defaults.
3. **Given** a manual door code override exists for a reservation, **When** the
   reservation is reconciled, **Then** the manual code is preserved rather than
   replaced by a generated code.
4. **Given** a guest is currently checked in, **When** the soonest-N set changes
   while the stay is active, **Then** the active guest is never evicted mid-stay
   and still counts against managed slot capacity.
5. **Given** slot-name trimming, lock-code buffers, deterministic code
   generation, check-in tracking sensors, and read-only event sensors are in
   use, **When** reconciliation runs, **Then** their existing observable
   behavior is preserved.
6. **Given** Honor Event Times is on and non-zero lock-code buffers are
   configured, **When** a PMS calendar event changes its check-in or checkout
   time, **Then** a physical slot already matching the previously buffered
   window is treated as system-managed and updated to the new calendar time
   after applying the configured before/after buffer; only a physical time that
   deviates from the buffered expected window is treated as a manual override.

---

### User Story 6 - Self-Heal Physical Empty and Drifted Slots (Priority: P2)

As a maintainer, I want each refresh to correct drift between physical slots
and the desired calendar state, so operators do not need restarts, reloads, or
manual store deletion to recover from stale, phantom, or manually edited slots.

**Why this priority**: The redesign should reduce support burden and eliminate
manual recovery workflows while still preserving safety gates around physical
clear confirmation.

**Independent Test**: Can be fully tested by seeding physical slots with empty,
stale, phantom, duplicate, or manually changed state and verifying normal
refreshes converge to the desired state without using persisted data as truth.

**Acceptance Scenarios**:

1. **Given** a physical managed slot has both name and PIN empty, blank,
   `unknown`, or `None`, **When** reconciliation runs, **Then** the
   slot is treated as physically free regardless of cached status.
2. **Given** a physical managed slot contains a name or PIN that matches no
   should-be reservation, **When** reconciliation runs, **Then** the slot is
   reset and only reused after confirmed empty state.
3. **Given** the same should-be reservation appears in more than one physical
   managed slot, **When** reconciliation runs, **Then** at most one slot remains
   assigned and the duplicate physical slot is reset through the confirmed-clear
   safety path.
4. **Given** a manager manually changes a managed slot's name, PIN, or dates
   away from the desired reservation state, **When** reconciliation runs,
   **Then** Rental Control restores the desired state or resets the slot and
   records that managed slot drift was corrected.

---

### Edge Cases

- What happens when a changed reservation's code no longer matches the physical
  slot because date-based generation changed? The slot is matched by stable
  trim-aware name identity, updated in place, and not duplicated.
- What happens when two desired reservations have the same display name? They
  are paired with physical slots by start-time order under the whole-unit
  non-overlap assumption; each selected reservation may appear in only one
  slot.
- What happens when a physical slot name is trimmed or prefixed relative to the
  calendar-derived name? Matching remains trim-aware and prefix-aware so the
  existing physical name can identify the reservation.
- What happens when saved slot-mapping data is missing, stale, or contradictory?
  The saved data may help with aliases or diagnostics, but physical Keymaster
  state and the current calendar determine correctness.
- What happens when Keymaster entities are not loaded yet? The affected slot is
  handled conservatively during that refresh and retried on later refreshes;
  correctness does not depend on the timing of entity readiness.
- What happens when all selected slots require clearing before new reservations
  can be programmed? New programming waits for confirmed physical empty state;
  no slot is double-programmed.
- What happens when there are no eligible reservations? Managed physical slots
  that hold non-active reservations are reset and remain empty once confirmed.
- What happens when active-guest protection conflicts with strict soonest-N
  ordering? The active guest remains assigned and counts against capacity; the
  remaining capacity is filled by the soonest non-active reservations.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: On every refresh, the system MUST treat current physical
  Keymaster managed-slot state and the current calendar-derived reservation set
  as the only authoritative inputs for correctness.
- **FR-002**: Persisted slot-mapping data MUST be cache-only; missing, stale,
  delayed, or contradictory persisted data MUST NOT be required for correct
  slot selection, duplicate prevention, reset decisions, or assignment safety.
- **FR-003**: Each refresh MUST derive an ordered should-be-in-Keymaster set
  from the soonest eligible whole-unit reservations by start time, limited by
  managed-slot capacity and active-guest protection.
- **FR-004**: The system MUST compare physical managed slots with the
  should-be-in-Keymaster set by stable reservation identity based on trim-aware
  slot name matching before considering code or date equality.
- **FR-005**: A changed reservation whose physical slot name matches its stable
  reservation identity MUST be updated in the same physical slot when its code,
  start time, or end time differs, rather than receiving another slot; any
  replacement PIN programming MUST still satisfy the confirmed-empty safety
  rule for that same physical slot.
- **FR-006**: Matching MUST account for configured slot-name prefixes and
  trimming so a shortened or prefixed Keymaster name can still identify the
  calendar reservation it represents.
- **FR-007**: When duplicate or ambiguous reservation names exist in the same
  refresh, the system MUST disambiguate them by start-time order and MUST leave
  each selected reservation in exactly one managed slot.
- **FR-008**: A managed physical slot holding a reservation that is not in the
  should-be-in-Keymaster set MUST be reset unless that reservation is protected
  as an active checked-in guest.
- **FR-009**: A should-be reservation that is not physically present in a
  managed slot MUST be assigned only to a physical slot that is confirmed empty
  or has just become confirmed empty.
- **FR-010**: A managed slot that still physically holds any non-empty PIN or
  non-empty name MUST NOT receive any new or replacement PIN until a reset is
  confirmed by physical empty state.
- **FR-011**: The system MUST recognize a physical managed slot as empty only
  when both name and PIN are empty, blank, `unknown`, or `None`, using
  case-insensitive comparison for text states.
- **FR-012**: The system MUST treat `unavailable` Keymaster text state
  conservatively as not readable and MUST NOT assume the slot is safely empty.
- **FR-013**: An already-correct physical managed slot, with matching name,
  code, and validity window for its should-be reservation, MUST be a no-op to
  avoid needless lock churn.
- **FR-014**: Manual check-in and checkout time-of-day overrides MUST continue
  to override built-in times unless Honor Event Times is enabled.
- **FR-015**: Manual door code overrides MUST continue to be preserved when
  deriving the should-be reservation code.
- **FR-016**: A currently checked-in guest MUST NOT be evicted from a managed
  slot during the active stay window and MUST count against managed-slot
  capacity.
- **FR-017**: Existing behavior for slot-name trimming, lock-code before and
  after buffers, Honor Event Times, deterministic code generation,
  should-update-code behavior, check-in tracking sensors, and read-only
  `event_N` sensors MUST be preserved.
- **FR-022**: Manual time override detection MUST compare physical Keymaster
  times against the expected calendar/default time after applying configured
  lock-code before and after buffers. A physical slot time equal to the
  buffered expected check-in or checkout time MUST be treated as
  system-managed, not a manual/local override; with Honor Event Times enabled,
  calendar check-in or checkout changes MUST update the slot to the newly
  buffered time, while a true deviation from the buffered expected time MUST
  remain a preserved manual override.
- **FR-018**: The system MUST correct stale, phantom, duplicate, manually
  drifted, and physically empty managed-slot states during normal refresh
  cycles without requiring Home Assistant restart, integration reload, manual
  clear-all, or manual persisted-store deletion.
- **FR-019**: The system MUST keep all reconciliation behavior scoped to
  Rental-Control-managed Keymaster slots and MUST NOT alter unmanaged slots.
- **FR-020**: Operational logs or diagnostics MUST make visible when a slot is
  reset, blocked by unconfirmed physical state, updated in place after a
  reservation change, matched by stable name, corrected after manual drift, or
  skipped because it is outside the should-be set.
- **FR-021**: The implementation stage MUST include acceptance coverage for
  reservation length increase, length decrease, full date shift with code
  change, same-guest rebooking or back-to-back stays, duplicate guest names,
  soonest-N dropout and replacement, cold start or deleted store,
  confirmed-reset-before-reapply, physical-empty self-heal, manual overrides,
  and active-guest protection.

### Key Entities

- **Reservation**: A calendar stay eligible for lock-code programming. Key
  attributes include stable slot-name identity, display name, start and end
  times, generated or manual code, active check-in status, and whether it is in
  the current should-be-in-Keymaster set.
- **Physical Managed Slot**: A Keymaster slot inside the configured Rental
  Control managed range. Key attributes include observed name, observed PIN
  presence, observed validity window, readability, empty status, and whether it
  matches a should-be reservation.
- **Should-Be-in-Keymaster Set**: The ordered set of reservations that should
  physically occupy managed slots after the refresh, selected from the soonest
  eligible reservations while preserving active guests.
- **Stable Slot Name Identity**: The reservation identity used to match a
  physical slot to a desired reservation across date and code changes. It is
  based on guest or reservation slot name and accounts for configured prefixing
  and trimming.
- **Persisted Slot-Mapping Cache**: Saved metadata that may accelerate aliasing,
  diagnostics, or migration but is never authoritative for correctness,
  duplicate prevention, or assignment safety.
- **Confirmed Empty State**: The physical condition where both observed slot
  name and PIN are empty, blank, `unknown`, or `None`, allowing a slot to be
  safely reused.

## Assumptions

- Reservations represent whole-unit rentals and do not overlap, so start-time
  ordering is a safe total order for soonest-N selection and duplicate-name
  disambiguation.
- Slot names carry the stable guest or reservation identifier that survives
  length changes, date shifts, and code regeneration.
- Existing calendar parsing and reservation eligibility rules continue to
  decide which reservations are eligible; this feature changes slot
  reconciliation, not calendar platform support.
- The persisted slot-mapping cache may still exist for non-authoritative alias
  and diagnostic use, but deleting it must be a safe recovery-neutral action.
- Keymaster can temporarily report unreadable or delayed state, and safety
  requires waiting for later refreshes rather than assuming such slots are
  empty.

## Non-Goals

- Changing calendar parsing, booking-platform integrations, or reservation
  eligibility policy outside the slot reconciliation model.
- Defining support for overlapping whole-unit reservations beyond the stated
  duplicate-name start-time disambiguation.
- Removing existing user configuration options for code generation, name
  trimming, buffers, Honor Event Times, manual overrides, or check-in tracking.
- Making persisted slot mappings authoritative again for recovery, allocation,
  or pending-operation fences.
- Closing issue #607 in this specification PR; the implementation PR owns the
  runtime fix.

## Security Considerations

- Door-code assignment controls physical property access. Duplicate,
  stale, missing, or wrong codes can lock out valid guests or leave stale access
  active for prior guests.
- Confirmed-reset-before-reapply is mandatory because a slot that is only
  logically free may still hold an active physical PIN.
- Persisted data must not be allowed to wedge or override physical truth,
  because stale cache contents can otherwise preserve unsafe access or prevent
  valid guest access.
- Logs and diagnostics should explain reconciliation decisions without exposing
  more sensitive code material than existing Rental Control diagnostics expose.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In 100% of reservation length increase, length decrease, full
  date-shift, and same-guest rebooking scenarios, the changed reservation is
  present in at most one managed slot and updates the matching physical slot in
  place when its stable slot name is already present.
- **SC-002**: In 100% of full date-shift scenarios using date-based code
  generation, the old generated code is replaced in the same physical slot and
  no second slot is assigned to the reservation.
- **SC-003**: In 100% of duplicate guest-name scenarios with non-overlapping
  reservations, start-time ordering assigns each selected reservation to
  exactly one managed slot.
- **SC-004**: In 100% of refreshes where all required physical operations are
  confirmed, the set of programmed managed slots equals the should-be set of
  reservations, with active-guest protection and managed-slot capacity already
  reflected in that should-be set.
- **SC-005**: In 100% of missing, deleted, stale, or contradictory persisted
  cache scenarios, normal refresh cycles reconcile correctly from physical
  Keymaster state and the calendar without manual cache repair.
- **SC-006**: In 100% of replacement scenarios, a managed slot is not reused
  for any new or changed PIN until both its physical name and PIN are confirmed
  empty.
- **SC-007**: In 100% of physical-empty self-heal scenarios, a slot whose name
  and PIN are empty, blank, `unknown`, or `None` is treated as free regardless
  of cached status; a slot with `unavailable` text state is not treated as free.
- **SC-008**: In 100% of manual override regression scenarios, manual
  check-in/out times and manual door codes produce the same programmed access
  window and code as before the redesign.
- **SC-009**: In 100% of active-guest overflow scenarios, a currently checked-in
  guest remains programmed through the active stay window and is not evicted by
  a nearer reservation.
- **SC-010**: Existing behavior for slot-name trimming, lock-code buffers,
  Honor Event Times, deterministic code generation, should-update-code,
  check-in tracking sensors, and read-only `event_N` sensors remains unchanged
  in 100% of corresponding regression scenarios.
- **SC-011**: In 100% of stale, phantom, duplicate, drifted, or physically empty
  starting states where Keymaster becomes readable and required physical
  operations are confirmed, the refresh that observes those confirmations
  leaves managed slots equal to the should-be physical state without Home
  Assistant restart, integration reload, manual clear-all, or manual
  persisted-store deletion.
