<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Feature Specification: Slot Reconciliation

**Feature Branch**: `012-slot-reconciliation`
**Created**: 2026-06-19
**Status**: Review
**Input**: User description: "Redesign Rental Control slot management so
managed Keymaster slots are reconciled on every coordinator refresh to a
single deterministic desired reservation-to-slot state. The redesign is
motivated by issue #589 and related reports #535, #546, and #521, where
full slots, stale or phantom Keymaster state, duplicates, and restart-only
recovery caused missing or wrong guest door codes."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Program Soonest Eligible Reservations (Priority: P1)

As a property manager, I want the soonest eligible reservations to have
working door codes whenever managed slots are available, so an arriving
guest is not locked out because a farther-future reservation consumed a
slot first.

**Why this priority**: Missing or wrong lock codes are the primary guest
impact reported in issues #589 and #535. The property manager needs the
next arriving guests to be represented in Keymaster before later stays.

**Independent Test**: Can be fully tested by providing more eligible
reservations than managed slots, running one refresh, and verifying the
managed slots contain only the earliest eligible reservations, subject to
active-guest protection.

**Acceptance Scenarios**:

1. **Given** all managed slots are occupied by later reservations and a
   newly received reservation starts earlier than every occupied
   reservation, **When** the next coordinator refresh completes with all
   required slot operations confirmed, **Then** the new nearer reservation
   occupies a managed slot and the farthest unprotected reservation is
   no longer assigned.
2. **Given** there are more eligible reservations than managed slots,
   **When** reconciliation chooses which reservations receive slots,
   **Then** the selected set is the soonest managed-slot count of eligible
   reservations by start time, except for currently checked-in guests who
   are protected from eviction.
3. **Given** one nearer eligible reservation is unassigned and one
   farther-future unprotected reservation occupies a managed slot,
   **When** a refresh reconciles managed slots, **Then** the farther
   reservation is removed or corrected before any later reservation is
   allowed to remain assigned ahead of the nearer reservation.

---

### User Story 2 - Protect Current Guests Mid-Stay (Priority: P1)

As a property manager, I want a currently checked-in guest's door code to
remain programmed through the stay, so reconciliation cannot evict an
active guest while they still need access.

**Why this priority**: Removing an active guest's code is a direct access
failure and can strand guests already occupying the property.

**Independent Test**: Can be fully tested by marking a reservation as
checked in, adding enough earlier-starting or newly discovered eligible
reservations to exceed slot capacity, and verifying the active guest's
slot remains assigned while remaining capacity is filled by the soonest
non-active reservations.

**Acceptance Scenarios**:

1. **Given** a guest is currently checked in and occupies a managed slot,
   **When** a refresh sees more eligible reservations than managed slots,
   **Then** the checked-in guest remains assigned even if start-time
   ordering alone would evict that reservation.
2. **Given** one or more checked-in guests are protected, **When** the
   desired slot plan is computed, **Then** protected active reservations
   count against managed-slot capacity and remaining slots are filled by
   the soonest eligible non-active reservations.
3. **Given** a checked-in guest reaches the normal end of the protected
   stay window, **When** subsequent reconciliation runs, **Then** the
   reservation follows the normal eligibility and cleanup rules rather
   than remaining protected indefinitely.

---

### User Story 3 - Self-Heal Corrupted Slot State (Priority: P1)

As a property manager, I want Rental Control to recover automatically
from duplicate, phantom, stale, or mis-assigned managed slots, so I do
not need to restart Home Assistant, reload the integration, or manually
clear all managed slots to restore correct codes.

**Why this priority**: Issues #589, #546, and #521 describe states where
normal operation could not recover after Keymaster and Rental Control
state diverged. Self-healing is the core redesign goal.

**Independent Test**: Can be fully tested by seeding managed slots with
invalid current state, running one refresh, and verifying the resulting
actual slots converge toward the desired plan without requiring restart
or manual clearing.

**Acceptance Scenarios**:

1. **Given** the same reservation is physically present in two managed
   slots, **When** reconciliation runs, **Then** at most one slot remains
   assigned to that reservation and any extra duplicate is cleared or
   corrected without assigning another reservation into it until the
   clear is confirmed.
2. **Given** a farther-future reservation is programmed while a nearer
   eligible reservation has no slot, **When** reconciliation runs, **Then**
   the slot mapping is corrected so the nearer reservation is assigned
   before the farther unprotected reservation.
3. **Given** a managed slot has a stale or phantom name-only state, such
   as a guest name with no usable code or invalid date range, **When** a
   normal refresh runs, **Then** that slot is treated as corrupted actual
   state, reconciled away from the desired plan, and reclaimed only after
   the physical clear is confirmed.
4. **Given** Home Assistant has not restarted and the integration has not
   been reloaded, **When** any refresh observes actual managed-slot state
   that differs from the desired plan, **Then** reconciliation attempts to
   correct the difference during normal operation.

---

### User Story 4 - Avoid Double-Assignment on Clear Failures (Priority: P1)

As a property manager, I want a slot to remain unavailable until its
physical clear is confirmed, so a stale code cannot stay active while the
same slot is reused for a different guest.

**Why this priority**: A failed clear can otherwise produce duplicate
reservations, capacity exhaustion, or stale access codes. Preventing
reuse until confirmation protects both access reliability and security.

**Independent Test**: Can be fully tested by forcing a physical clear to
fail, running reconciliation, and verifying the slot is still considered
occupied or blocked and no new reservation is assigned to it until a
later confirmed clear succeeds.

**Acceptance Scenarios**:

1. **Given** reconciliation needs to clear a managed slot before assigning
   another reservation, **When** the physical clear operation fails or
   cannot be confirmed, **Then** the slot remains occupied or blocked and
   is not reused for any reservation.
2. **Given** a failed clear leaves a stale reservation physically present,
   **When** subsequent refreshes run, **Then** reconciliation retries or
   continues reporting the discrepancy without assigning another
   reservation to that slot.
3. **Given** the physical clear later succeeds, **When** the next
   reconciliation evaluates the slot, **Then** the slot becomes available
   for the desired reservation plan.

---

### User Story 5 - Correct and Log Manual Tampering (Priority: P2)

As a property manager, I want Rental Control to remain authoritative for
its managed Keymaster slots while recording when it overwrites a manual
change, so accidental edits are repaired and troubleshooting has an audit
trail.

**Why this priority**: Managed slots are intended to be owned by Rental
Control. Silent manual changes can otherwise cause wrong codes or confuse
incident diagnosis.

**Independent Test**: Can be fully tested by manually changing a
Rental-Control-managed Keymaster slot, running a refresh, and verifying
the slot returns to the desired reservation mapping with a log entry that
identifies the corrected manual change.

**Acceptance Scenarios**:

1. **Given** a manager manually changes the guest name, code, or date
   range in an RC-managed Keymaster slot, **When** the next refresh runs,
   **Then** Rental Control restores that slot to the desired state.
2. **Given** Rental Control overwrites a manual edit in a managed slot,
   **When** the correction is made, **Then** the system logs that a
   managed slot was manually changed and reconciled back to the desired
   state.
3. **Given** a Keymaster slot is outside the Rental-Control-managed
   range, **When** reconciliation runs, **Then** that slot is not changed
   by this feature.

---

### User Story 6 - Survive Restarts and Noisy Feeds (Priority: P2)

As a property manager, I want reservation-to-slot identity to survive
Home Assistant restarts and short-lived calendar feed misses, so booking
platform churn does not make Rental Control forget or prematurely clear
valid guest codes.

**Why this priority**: Reports show restart can temporarily repair state,
but the redesign must preserve identity across restarts and tolerate
flaky feeds during normal operation instead of relying on restart
side-effects.

**Independent Test**: Can be fully tested by assigning reservations to
slots, restarting Home Assistant, changing volatile calendar identifiers,
and temporarily omitting a reservation from the feed for one or two
refreshes while verifying the mapping remains stable.

**Acceptance Scenarios**:

1. **Given** reservations have been assigned to managed slots, **When**
   Home Assistant restarts, **Then** Rental Control reconstructs the
   slot-to-reservation mapping from persisted reservation identity and
   continues reconciling from that mapping.
2. **Given** a booking platform changes a volatile calendar identifier for
   an otherwise same reservation, **When** reconciliation runs after the
   change, **Then** the existing slot mapping is retained rather than
   creating a duplicate or treating the reservation as unrelated.
3. **Given** an assigned reservation is missing from the calendar feed for
   the first consecutive refresh, **When** reconciliation runs, **Then**
   the slot remains assigned to that reservation.
4. **Given** the same assigned reservation is still missing for a second
   consecutive refresh, **When** reconciliation completes, **Then** the
   slot still remains assigned and the absence remains tracked.
5. **Given** the same reservation remains missing after the two tolerated
   consecutive refreshes, **When** the next reconciliation runs, **Then**
   the reservation becomes eligible for normal clearing if it is not
   otherwise protected.
6. **Given** a temporarily missing reservation reappears before the
   tolerance is exceeded, **When** reconciliation runs, **Then** its
   absence count is reset and the existing slot mapping is retained.

---

### User Story 7 - Troubleshoot Desired vs Actual State (Priority: P3)

As a property manager or maintainer, I want diagnostics that show the
planned reservation mapping and the observed Keymaster mapping, so I can
understand why a slot is programmed, blocked, clearing, or unassigned.

**Why this priority**: Diagnostics do not program codes by themselves,
but they reduce support time and make self-healing behavior auditable.

**Independent Test**: Can be fully tested by creating desired/actual
mismatches and verifying diagnostics expose enough information to compare
which reservations should be assigned, what Keymaster currently shows,
and what reconciliation is waiting to correct.

**Acceptance Scenarios**:

1. **Given** managed slots match the desired plan, **When** diagnostics
   are requested, **Then** they show each desired slot-to-reservation
   mapping and that no correction is pending.
2. **Given** a slot is blocked by an unconfirmed clear, **When**
   diagnostics are requested, **Then** they show the desired mapping, the
   actual stale state, and that the slot is unavailable until clear
   confirmation.
3. **Given** a reservation is unassigned because there are more eligible
   reservations than managed slots, **When** diagnostics are requested,
   **Then** they identify that reservation as overflow rather than lost.

---

### User Story 8 - Preserve Existing Lock-Code Semantics (Priority: P1)

As an existing Rental Control user, I want the redesign to change how
slots are reconciled without changing established feature behavior, so
upgrading fixes reliability without surprising property managers or
guests.

**Why this priority**: Slot reconciliation is safety-critical, but it
must not regress existing slot-name trimming, lock-code timing, date-based
code regeneration, PMS time handling, or check-in tracking behavior.

**Independent Test**: Can be fully tested by exercising existing user
features before and after the redesign and verifying their observable
semantics are unchanged while slot selection becomes authoritative and
self-healing.

**Acceptance Scenarios**:

1. **Given** slot-name trimming is configured, **When** reconciliation
   derives names for desired slots, **Then** the trimmed or untrimmed slot
   names match the existing feature behavior.
2. **Given** lock-code before or after buffers are configured, **When** a
   desired slot is programmed, **Then** the effective code validity window
   preserves the existing buffer semantics.
3. **Given** honor-PMS-times is enabled or disabled, **When** desired
   reservation windows are calculated, **Then** the existing PMS time
   behavior is preserved.
4. **Given** date-based code regeneration or should-update-code determines
   an existing code should change or remain stable, **When**
   reconciliation evaluates that reservation, **Then** it follows the
   existing code regeneration decision.
5. **Given** check-in tracking sensors observe guest access, **When** slot
   reconciliation changes or preserves the managed slot plan, **Then** the
   existing check-in tracking semantics continue to work for the assigned
   reservation.

---

### Edge Cases

- What happens when all managed slots are full and a nearer new
  reservation appears? The nearer reservation enters the desired plan and
  the farthest unprotected reservation becomes overflow once required
  clears and sets are confirmed.
- What happens when a farther-future reservation appears while all slots
  are already assigned to nearer reservations? The farther reservation is
  logged or reported as overflow and must not displace a nearer
  unprotected reservation.
- What happens when the same reservation appears in more than one managed
  slot? Reconciliation collapses the duplicate so the reservation maps to
  at most one slot, and any duplicate slot is not reused until its clear is
  confirmed.
- What happens when a Keymaster slot clear fails? The slot remains
  occupied or blocked, is retried or reported as discrepant on subsequent
  refreshes, and cannot be assigned to another reservation until the clear
  is confirmed.
- What happens when a managed slot has a phantom guest name but no usable
  code or dates? The slot is treated as corrupted actual state, cleared
  during normal reconciliation, and reclaimed only after clear
  confirmation.
- What happens when a manager manually edits an RC-managed slot? Rental
  Control restores the slot to the desired state and logs that it
  overwrote a manual change.
- What happens when a reservation disappears from the calendar feed
  briefly? The assigned slot is retained through two consecutive missing
  refreshes and is only eligible for clearing after the tolerance is
  exceeded.
- What happens when two reservations would overlap? Reservations are
  assumed not to overlap for whole-unit rentals; overlap resolution is out
  of scope for this feature.
- What happens when there are no eligible reservations? Managed slots are
  cleared according to the same confirmed-clear safety rule, while
  transient feed-miss tolerance still applies to previously assigned
  reservations.
- What happens when Keymaster cannot confirm current state during a
  refresh? Reconciliation must not treat unknown state as safely reusable;
  diagnostics and logs show the unresolved discrepancy until state can be
  confirmed.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: On every coordinator refresh, the system MUST compute one
  deterministic desired reservation-to-slot plan for the RC-managed
  Keymaster slots from current eligible reservations, protected active
  reservations, persisted slot mappings, feed-miss tolerance state, and
  observed actual Keymaster state.
- **FR-002**: The desired plan MUST be authoritative for RC-managed slots;
  incremental in-memory slot assignment state MUST NOT be treated as the
  source of truth when it conflicts with the desired plan or actual
  Keymaster state.
- **FR-003**: When eligible reservations exceed managed slot capacity, the
  desired plan MUST include the soonest reservations by start time up to
  managed-slot capacity, except that currently checked-in guests MUST
  remain assigned and count against capacity until their protected stay
  ends.
- **FR-004**: A farther-future unprotected reservation MUST NOT occupy an
  RC-managed slot while a nearer eligible reservation is unassigned, except
  while physical operations needed to remove the farther reservation are
  unconfirmed; that exception MUST be visible in logs or diagnostics and
  MUST NOT allow double-assignment.
- **FR-005**: Each reservation MUST map to at most one RC-managed slot in
  both the desired plan and the reconciled actual state. Duplicate actual
  assignments MUST be detected and collapsed during normal reconciliation.
- **FR-006**: Each RC-managed slot MUST map to at most one reservation in
  the desired plan, and the system MUST NOT assign a new reservation to a
  slot whose previous physical clear has not been confirmed.
- **FR-007**: A slot clear MUST be considered complete only after the
  physical Keymaster state confirms the slot is clear. If clear
  confirmation fails or is unavailable, the slot MUST remain occupied or
  blocked and unavailable for reassignment.
- **FR-008**: Reconciliation MUST compare desired managed-slot state with
  actual Keymaster state on every refresh and correct duplicates,
  phantom-occupied slots, stale assignments, missing assignments, and
  mis-assigned slots without requiring Home Assistant restart, integration
  reload, or manual clear-all.
- **FR-009**: RC-managed Keymaster slots are authoritative Rental Control
  outputs. Manual changes to an RC-managed slot MUST be overwritten by the
  next reconciliation when they conflict with the desired plan, and each
  overwrite MUST be logged as a manual or external change correction.
- **FR-010**: The system MUST persist reservation identity and slot mapping
  metadata in Home Assistant storage so mappings survive restarts and can
  distinguish the same reservation from booking-platform calendar
  identifier churn.
- **FR-011**: Persisted reservation identity MUST be based on stable
  reservation metadata sufficient to reconnect a re-delivered reservation
  to its prior slot even when volatile calendar identifiers change.
- **FR-012**: When an assigned reservation is absent from the calendar feed
  for one or two consecutive refreshes, the system MUST retain its slot and
  track the consecutive absence. After the two-refresh tolerance is
  exceeded, the reservation MAY be cleared through normal reconciliation if
  it is not otherwise protected.
- **FR-013**: When a previously absent reservation reappears before the
  tolerance is exceeded, the system MUST reset its absence count and keep
  the existing slot mapping when the reservation remains eligible.
- **FR-014**: The system SHOULD expose diagnostics that show, for each
  RC-managed slot and eligible or recently persisted reservation, the
  desired mapping, observed actual Keymaster state, pending correction or
  blocked state, and overflow reason when applicable.
- **FR-015**: Reconciliation MUST preserve existing user-facing semantics
  for slot-name trimming, lock-code before and after buffers,
  honor-PMS-times, date-based code regeneration, should-update-code
  decisions, and check-in tracking sensors.
- **FR-016**: The system MUST keep all reconciliation behavior scoped to
  RC-managed Keymaster slots and MUST NOT modify unmanaged Keymaster slots.
- **FR-017**: Reconciliation MUST log meaningful slot corrections,
  duplicate collapses, overflow decisions, blocked clear failures, phantom
  slot recovery, and manual-change overwrites for operational
  troubleshooting.
- **FR-018**: The system MUST be verifiable against each of the
  issue-reported failure families: full slots plus nearer new reservation,
  farther-future reservation displacing a nearer one, duplicate
  reservation assignments, clear-failure safety, phantom name-only slots,
  restart survival, feed-miss tolerance, and manual edit correction.

### Key Entities

- **Reservation**: A rental calendar stay eligible for door-code
  programming. Key attributes include stable reservation identity,
  guest-facing slot name, access code, start and end time, active
  check-in status, consecutive feed-miss count, and whether it is eligible,
  protected, assigned, or overflow.
- **Managed Slot**: A numbered Keymaster code slot within the
  Rental-Control-managed range. Key attributes include slot number,
  observed guest name, observed access code, observed validity window,
  clear-confirmation status, blocked or available state, and whether the
  observed state matches Rental Control's desired mapping.
- **Desired Plan**: The deterministic result of each refresh that selects
  which eligible and protected reservations should occupy managed slots,
  which reservations are overflow, which actual slots need correction, and
  which slots are blocked pending confirmed clear.
- **Slot-to-Reservation Mapping**: The persisted association between one
  reservation identity and at most one managed slot, including metadata
  needed to reconnect the reservation after restart or volatile calendar
  identifier changes.
- **Actual Keymaster State**: The observed physical or entity state of the
  managed Keymaster slots at refresh time, used to detect whether the
  desired plan has already converged or needs correction.
- **Feed-Miss Record**: The tracked consecutive refresh count for a
  persisted reservation that was previously assigned but is currently
  absent from the calendar feed.

## Assumptions

- Reservations represent whole-unit rentals and do not overlap; a clean
  total ordering by reservation start time is valid for desired-plan
  selection.
- Existing configuration and eligibility rules continue to decide which
  reservations are in scope for lock-code programming; this feature
  changes how selected reservations are reconciled to slots, not which
  calendar platforms are supported.
- Keymaster may transiently fail to clear or report slot state. In those
  cases, safety requires treating the affected slot as unavailable until
  clear state is confirmed, even if convergence of actual slot contents
  takes additional refreshes.
- The phrase "RC-managed slot" means only the contiguous configured range
  Rental Control is allowed to manage; slots outside that range remain the
  user's responsibility.
- A currently checked-in guest is identified by the existing check-in
  tracking behavior and remains protected only for the existing active
  stay window.
- The two-refresh feed-miss tolerance protects against noisy calendar
  feeds; it is not intended to preserve codes indefinitely for reservations
  permanently removed from the booking source.

## Non-Goals

- Changing booking-platform integrations, iCal parsing, or calendar feed
  retrieval behavior.
- Defining behavior for overlapping reservations, because whole-unit
  rentals are assumed not to overlap.
- Changing Keymaster's internal lock programming, reset implementation, or
  unmanaged slots.
- Redesigning slot-name extraction, slot-name trimming, lock-code buffer
  semantics, honor-PMS-times semantics, date-based code regeneration,
  should-update-code rules, or check-in tracking sensors beyond preserving
  them during reconciliation.
- Closing issue #589 in this specification stage; the implementation PR is
  responsible for resolving the runtime defect.

## Security Considerations

- Door-code slot assignment controls physical property access. Missing,
  stale, duplicate, or wrong codes can lock out valid guests or leave
  stale access active after checkout.
- Confirmed-clear safety is required because a slot that appears free only
  in memory can still contain an active physical code. Such a slot must not
  be reused until clear confirmation proves it is safe.
- Authoritative reconciliation of manual edits is intentional for managed
  slots: manual changes inside the managed range are treated as drift that
  could compromise guest access correctness and are restored with logging.
- Diagnostics and logs must aid troubleshooting without exposing more
  sensitive reservation or code information than existing Rental Control
  diagnostics expose.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For 100% of refreshes where all required physical slot
  operations are confirmed, the set of RC-managed programmed reservations
  after reconciliation equals the soonest eligible reservations up to slot
  capacity, with checked-in guests protected from eviction.
- **SC-002**: For 100% of reconciled states, no reservation is assigned to
  more than one RC-managed slot and no RC-managed slot is assigned to more
  than one reservation.
- **SC-003**: In 100% of tested duplicate, phantom, stale, and mis-assigned
  starting states, normal coordinator refreshes converge to the desired
  plan without requiring Home Assistant restart, integration reload, or
  manual clear-all, except for slots blocked by unconfirmed physical
  operations.
- **SC-004**: In 100% of overflow scenarios, no farther-future
  unprotected reservation remains assigned while a nearer eligible
  reservation is unassigned once required clears and sets are confirmed.
- **SC-005**: In 100% of active-stay overflow scenarios, a currently
  checked-in guest remains assigned until the protected stay window ends.
- **SC-006**: In 100% of failed-clear scenarios, the affected slot is not
  assigned to a different reservation until the clear is confirmed, and
  the failure is visible through logs or diagnostics.
- **SC-007**: In 100% of manual-edit scenarios inside the managed range,
  the next reconciliation restores the desired slot state and records a
  log entry for the overwritten change.
- **SC-008**: In 100% of restart scenarios with persisted mappings,
  Rental Control preserves the reservation-to-slot mapping for still
  eligible reservations despite volatile calendar identifier changes.
- **SC-009**: In 100% of transient calendar-miss scenarios, an assigned
  reservation remains assigned through two consecutive missing refreshes
  and is only eligible for clearing after that tolerance is exceeded.
- **SC-010**: When diagnostics are captured for any RC-managed slot,
  they should expose the desired reservation, actual Keymaster state,
  pending correction or blocked reason, and overflow status in a single
  diagnostic capture sufficient to diagnose the slot without additional
  log correlation.
- **SC-011**: Existing behavior for slot-name trimming, lock-code buffers,
  honor-PMS-times, date-based code regeneration, should-update-code, and
  check-in tracking remains unchanged in 100% of corresponding regression
  scenarios.
