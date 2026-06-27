<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Feature Specification: Decompose Reconciliation Engine

**Feature Branch**: `015-decompose-reconciliation`
**Created**: 2026-06-27
**Status**: Draft
**Input**: User description: "Decompose
`custom_components/rental_control/reconciliation.py` for GitHub issue #627.
This is a behavior-preserving code-health refactor of the 3.6.0 stateless
slot-reconciliation engine. Split the oversized safety-critical engine by
phase into focused, independently testable units without changing runtime
behavior."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Preserve Duplicate-Assignment Safety (Priority: P1)

As a property manager, I want the reconciliation engine to preserve the 3.6.0
safety guarantee that one reservation is never assigned to duplicate lock-code
slots across reservation changes, so upgrades cannot reintroduce the
production lock-code wipe and duplicate-assignment failures fixed by the
stateless redesign.

**Why this priority**: This is the headline safety guarantee of the 3.6.0
redesign. The decomposition is acceptable only if it preserves the exact
reservation identity, slot matching, update, and reset ordering semantics that
prevent duplicate active codes.

**Independent Test**: Can be fully tested by running the existing
reconciliation tests unchanged and by replaying reservation length changes,
full date shifts, code changes, same-guest rebookings, duplicate guest names,
and duplicate physical slot-name scenarios before and after decomposition with
identical plans and actions.

**Acceptance Scenarios**:

1. **Given** a reservation already occupies a managed physical slot and the
   reservation length changes, **When** reconciliation computes a plan, **Then**
   the reservation remains assigned to that same physical slot, receives the
   same in-place update action as before, and is not assigned to any second
   slot.
2. **Given** a reservation's date range shifts enough to change its generated
   code, **When** reconciliation computes and applies the required plan,
   **Then** the old code is replaced through the same physical slot sequence as
   before and no duplicate slot assignment is produced.
3. **Given** two selected reservations have the same slot-name identity, **When**
   reconciliation matches physical slots to desired reservations, **Then** the
   same start-time disambiguation selects one canonical physical slot per
   reservation and resets non-canonical duplicates exactly as before.
4. **Given** a physical slot name is trimmed or includes the configured prefix,
   **When** the desired reservation is matched, **Then** the same trim-aware and
   prefix-aware slot-name identity rules identify the reservation.
5. **Given** a changed reservation requires a code, name, or time update,
   **When** replacement would write to a slot that is not confirmed empty,
   **Then** the same confirmed-reset-before-reapply ordering prevents unsafe
   reuse.

---

### User Story 2 - Preserve Stateless Plan Behavior (Priority: P1)

As an existing Rental Control user, I want every refresh to compute the same
desired slot plan and stateless plan after decomposition, so lock programming,
manual overrides, active-guest protection, and diagnostics remain unchanged.

**Why this priority**: The engine controls physical access. Splitting the code
by phase must not change how physical Keymaster state, calendar reservations,
manual overrides, active guest status, or slot capacity produce actions.

**Independent Test**: Can be fully tested by comparing the serialized
`DesiredPlan` and `StatelessPlan` results for the same inputs before and after
decomposition and by verifying all existing reconciliation behavior tests pass
unchanged.

**Acceptance Scenarios**:

1. **Given** identical reservations, managed slots, observed slots, max-events,
   plan ID, generated-at time, and optional context, **When** the decomposed
   engine computes a plan, **Then** selected reservations, protected
   reservations, overflow reasons, per-slot planned actions, action order, and
   diagnostics are byte-for-byte equivalent to the current engine's output.
2. **Given** a currently checked-in guest occupies a managed slot, **When** the
   soonest eligible reservation set changes, **Then** active-guest protection
   and capacity accounting are unchanged.
3. **Given** a manual door code, manual time override, Honor Event Times setting,
   lock-code buffer, check-in tracking state, or read-only event sensor input
   participates in reservation construction, **When** the plan is computed,
   **Then** the resulting code, access window, and selected reservation behavior
   match the current implementation.
4. **Given** Keymaster reports an unreadable or unavailable slot state, **When**
   reconciliation evaluates that slot, **Then** the same conservative blocked or
   retry behavior is produced.
5. **Given** a physical slot is empty, stale, phantom, drifted, or duplicated,
   **When** the plan is computed, **Then** the same reset, assignment,
   update-in-place, retry, or blocked action is produced in the same order.

---

### User Story 3 - Preserve Public Reconciliation Surface (Priority: P1)

As a maintainer, I want the decomposition to keep the reconciliation API used
by the coordinator, event override manager, event sensors, and regression tests
unchanged, so the refactor can be reviewed as behavior-preserving rather than
as a coordinated runtime redesign.

**Why this priority**: Production callers already construct reservations,
fingerprints, managed slots, plans, and actions from this module. Changing that
surface would expand the blast radius beyond issue #627 and make behavior
parity harder to prove.

**Independent Test**: Can be fully tested by running existing tests unchanged
and by verifying the production import sites continue to import the same
reconciliation names without requiring caller-side behavior changes.

**Acceptance Scenarios**:

1. **Given** the coordinator imports the reconciliation surface it uses today,
   **When** the decomposed implementation is loaded, **Then** `DesiredPlan`,
   `ManagedSlot`, `Reservation`, `SlotStatus`, `compute_desired_plan`,
   `extract_booking_aliases`, `make_reservation_fingerprint`, and
   `normalize_slot_name_for_fingerprint` remain available with compatible
   behavior.
2. **Given** the event override manager imports reconciliation action and plan
   types, **When** it applies or records actions, **Then** `ActionKind`,
   `DesiredPlan`, `Reservation`, and `SlotAction` preserve the same fields,
   values, ordering expectations, and semantics.
3. **Given** event sensors compute reservation fingerprints, **When** they call
   the public fingerprint helper, **Then** the same normalized fingerprint value
   is produced for the same entry, slot name, and times.
4. **Given** existing unit and integration tests import additional public
   reconciliation models and helpers, **When** those tests run unchanged,
   **Then** the imported names remain available from the documented
   reconciliation surface or a compatibility boundary with no test rewrite.

---

### User Story 4 - Enable Phase-Level Testability (Priority: P2)

As a maintainer, I want the oversized stateless engine split into focused,
independently testable phases, so future safety fixes can target plan
computation, slot-to-reservation pairing, rematch logic, diagnostics, and action
building without navigating one monolithic 2,584-line module.

**Why this priority**: Issue #627 identifies the file as the integration's
second-largest module, with complexity currently hidden by an
`aislop-ignore-file` directive. Maintainability matters, but only after
behavior preservation is guaranteed.

**Independent Test**: Can be fully tested by measuring the decomposed files and
functions against active complexity thresholds and by adding focused regression
coverage for each extracted phase while existing end-to-end reconciliation tests
continue to pass unchanged.

**Acceptance Scenarios**:

1. **Given** the decomposition is complete, **When** complexity checks run,
   **Then** files in the reconciliation feature area are below 400 lines,
   project-owned functions are below 80 lines, and project-owned parameter lists
   have no more than 6 parameters.
2. **Given** desired-plan computation is tested independently, **When** inputs
   describe reservations, managed slots, capacity, and plan context, **Then** the
   selected/protected/overflow results and per-slot planned actions match the
   current integrated engine.
3. **Given** slot-to-reservation pairing and rematch behavior is tested
   independently, **When** physical names, prefixes, trimmed display names,
   duplicate names, start times, aliases, and persisted mappings vary, **Then**
   the same match classification and pairing decisions are produced.
4. **Given** diagnostics and fingerprint behavior is tested independently,
   **When** plans, reservations, aliases, observed state, or drift fields vary,
   **Then** diagnostics remain redacted and structurally identical to the current
   engine.
5. **Given** action-building behavior is tested independently, **When** a slot's
   desired and observed state require no-op, assign, set, update, reset, retry,
   clear, overwrite, or blocked handling, **Then** the same action kind, reason,
   preflight, and confirmed-empty requirements are produced.

---

### Edge Cases

- What happens when the persisted Store is deleted, missing, stale,
  contradictory, or deleted during a refresh? The Store remains cache-only;
  physical Keymaster state and current calendar data continue to determine
  correctness exactly as before.
- What happens when a changed reservation has a different date-based code but
  the same stable slot-name identity? The reservation is matched by slot name,
  updated in place, and never assigned to a duplicate slot.
- What happens when physical Keymaster names are trimmed, prefixed, blank,
  `unknown`, `None`, or `unavailable`? The same name normalization,
  confirmed-empty, and conservative unreadable-state rules apply.
- What happens when two reservations have the same guest or slot name? The same
  start-time ordering disambiguates the selected reservations and resets
  duplicate non-canonical physical slots.
- What happens when all candidate slots require clearing before reassignment?
  New or replacement programming waits for the same confirmed physical empty
  state before any reapply action.
- What happens when a currently checked-in guest conflicts with strict
  soonest-N ordering? The active guest remains protected and counts against
  managed capacity exactly as before.
- What happens when diagnostics include drift, alias, selected, overflow, or
  action details? The same redaction policy applies; raw slot codes remain
  excluded from persisted and diagnostic output.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The decomposition MUST preserve the 3.6.0 no-duplicate
  lock-code assignment guarantee across reservation length changes, date
  shifts, generated-code changes, same-guest rebookings, duplicate names, and
  duplicate physical slot-name matches.
- **FR-002**: Existing reconciliation unit and integration tests MUST pass
  unchanged after the implementation stage; new tests MUST verify behavior
  parity and phase-level coverage rather than introduce new runtime behavior.
- **FR-003**: `compute_desired_plan` behavior MUST remain equivalent for the
  same inputs, including selected, protected, overflow, per-slot planned state,
  action list, action order, diagnostics content, validation behavior, and
  no-op suppression.
- **FR-004**: `compute_stateless_plan` behavior MUST remain equivalent for the
  same inputs, including observed-slot classification, selected assignments,
  overflow reasons, actions, action order, matched-slot fields, and diagnostics.
- **FR-005**: Slot-name identity matching MUST preserve the current trim-aware
  and prefix-aware matching model, including exact display-name matching and the
  absence of unsafe generic prefix matching between distinct names.
- **FR-006**: Duplicate or ambiguous reservation names MUST continue to be
  disambiguated by start-time order and minimum-distance physical date matching
  so each selected reservation appears in no more than one managed slot.
- **FR-007**: Changed reservations that retain stable slot-name identity MUST be
  updated in the existing physical slot rather than allocated to a second slot.
- **FR-008**: Confirmed-reset-before-reapply ordering MUST be preserved for new
  assignments, code replacements, name replacements, stale clears, phantom
  clears, drift correction, retries, and duplicate resets.
- **FR-009**: Persisted Store data MUST remain cache-only; missing, deleted,
  stale, contradictory, or mid-run deleted Store data MUST NOT become required
  for correctness, duplicate prevention, reset decisions, or assignment safety.
- **FR-010**: Reservation rematch behavior MUST preserve the existing hierarchy
  for exact fingerprint, UID alias plus name, booking alias plus name, name plus
  exact time, conservative continuity, ambiguity, and no-match results.
- **FR-011**: Fingerprint and alias helpers MUST preserve existing normalized
  values, versioning, UTC handling, booking-code extraction, redaction
  expectations, and sensitivity boundaries.
- **FR-012**: Diagnostics MUST preserve existing keys, values, redaction,
  drift-field representation, reservation summaries, alias sorting, slot
  details, and context fields for identical plan inputs.
- **FR-013**: Action building MUST preserve action kind selection, reasons,
  blocked reasons, retry counts, last-error propagation, matched-by labels,
  preflight-read flags, and confirmed-empty requirements.
- **FR-014**: The public reconciliation surface consumed by production callers
  MUST remain source-compatible, including `ActionKind`, `DesiredPlan`,
  `ManagedSlot`, `Reservation`, `SlotAction`, `SlotStatus`,
  `compute_desired_plan`, `extract_booking_aliases`,
  `make_reservation_fingerprint`, and
  `normalize_slot_name_for_fingerprint`. Existing callers MUST be able to use
  the same import names and call patterns without behavioral changes, even if
  internal planning state is grouped differently to satisfy complexity gates.
- **FR-015**: Additional public models and helpers currently imported by tests
  MUST remain available from the reconciliation compatibility boundary,
  including `FINGERPRINT_VERSION`, `ObservedSlotStatus`, `ObservedSlot`,
  `DesiredReservation`, `StatelessPlan`, `CacheOnlyStoreRecord`, `PlannedSlot`,
  `StoredIdentity`, `StoredActual`, `SlotMapping`, `RematchKind`,
  `RematchResult`, `find_reservation_rematch`, and `compute_stateless_plan`.
- **FR-016**: The implementation MUST remove the file-level
  `aislop-ignore-file` directive for `complexity/file-too-large` and
  `complexity/function-too-long` from the reconciliation engine once the
  decomposed files and functions satisfy the active thresholds.
- **FR-017**: The completed decomposition MUST keep reconciliation files under
  400 lines, project-owned functions under 80 lines, and project-owned
  parameter lists at no more than 6 parameters unless an external framework
  signature requires otherwise. Any compatibility entry point that preserves the
  current `compute_desired_plan` caller contract MUST also pass the active
  parameter-count check, for example by accepting the existing call patterns
  while delegating to an internal request object or context model.
- **FR-018**: Decomposition MUST NOT introduce blocking I/O, additional Home
  Assistant coordinator refreshes, additional state writes, or persisted Store
  authority in any reconciliation hot path.
- **FR-019**: Planning and implementation documentation MUST state that this is
  a behavior-preserving refactor and MUST NOT define new lock-code business
  rules, new reconciliation states, new automations, new configuration options,
  or changed public caller behavior.

### Key Entities

- **Reconciliation Engine**: The stateless slot planning behavior currently
  contained in `custom_components/rental_control/reconciliation.py`. It derives
  desired physical Keymaster state from calendar reservations, observed slot
  facts, managed-slot capacity, and cache-only persisted metadata.
- **Reservation**: A normalized calendar stay eligible for lock-code planning,
  including stable slot-name identity, display name, access window, generated or
  manual code, aliases, active protection, checkout state, and lookup keys.
- **Desired Reservation**: The stateless planner's reservation representation
  used to pair current calendar intent with observed physical slots.
- **Managed or Observed Slot**: A physical Keymaster slot inside the Rental
  Control managed range, including observed name, PIN presence, date range,
  readability, classification, persisted identity, retry, and diagnostic state.
- **Desired or Stateless Plan**: The refresh-local result containing selected
  assignments, protected reservations, overflow reasons, per-slot comparisons,
  ordered actions, and diagnostics.
- **Slot Action**: A planned no-op, assign, update, reset, set, clear, retry,
  overwrite, or blocked decision with slot number, identity, reason, preflight,
  and confirmed-empty metadata.
- **Reservation Rematch Result**: The classification that reconnects current
  reservations to persisted cache records without making those records
  authoritative for correctness.
- **Cache-Only Store Record**: Persisted metadata that may support aliases,
  diagnostics, and migration but cannot determine correctness when physical
  Keymaster state and calendar data disagree.

## Assumptions

- This specification covers issue #627's spec stage only; planning and
  implementation stages will decide exact module names, file layout, helper
  boundaries, and migration mechanics.
- The current 2,584-line reconciliation module, its five functions over 80
  lines, and the 8-parameter `compute_desired_plan` signature are the
  complexity baseline to improve.
- The heavy functions identified for decomposition are `compute_desired_plan`,
  `compute_stateless_plan`, `find_reservation_rematch`,
  `_build_plan_diagnostics_snapshot`, `_build_slot_action`, and the
  `_pair_partial_*` pairing helpers.
- The existing source and tests are the behavior source of truth unless a later
  accepted issue explicitly changes reconciliation behavior.
- Reservations continue to represent whole-unit, non-overlapping rentals where
  start-time ordering is valid for duplicate-name disambiguation.
- Existing public callers are the coordinator, event override manager, event
  sensors, and regression tests that import reconciliation models, helpers, and
  planning functions.

## Non-Goals

- Changing lock-code assignment behavior, slot selection, reset ordering,
  duplicate handling, active-guest protection, manual overrides, Honor Event
  Times behavior, buffer handling, code generation, or diagnostics content.
- Adding new features, configuration options, reconciliation states, sensors,
  automations, service calls, Store authority, or recovery workflows.
- Changing the reconciliation algorithm's observable decisions or the public API
  surface consumed by production callers.
- Rewriting coordinator, event override, calendar parsing, check-in sensor, or
  event sensor behavior beyond minimal caller adjustments required to consume
  the same reconciliation API.
- Prescribing exact module names, file layout, class names, or helper function
  signatures for the plan and implementation stages.
- Closing issue #627 in this specification PR; later implementation work owns
  the runtime refactor.

## Constraints

- No duplicate lock-code assignment regression is acceptable under any
  reservation-change scenario covered by the current 3.6.0 stateless design.
- Slot-name identity matching, including trimming, configured display prefixes,
  and start-time disambiguation, MUST be preserved exactly.
- In-place updates for changed reservations MUST be preserved exactly and MUST
  not allocate second slots for the same selected reservation.
- Confirmed-reset-before-reapply ordering MUST be preserved exactly for every
  new, replacement, duplicate, stale, phantom, or drift correction path.
- Persisted Store data MUST remain cache-only; deleting the Store before or
  during reconciliation MUST not change correctness or safety outcomes.
- The final implementation MUST remove the reconciliation module's temporary
  `aislop-ignore-file complexity/file-too-large complexity/function-too-long`
  directive by satisfying the underlying thresholds.
- This specification stage is documentation-only and MUST NOT include
  production code changes.

## Security Considerations

- Reconciliation controls physical property access. Duplicate, stale, missing,
  or incorrectly ordered lock-code changes can lock out valid guests or leave
  stale access active for prior guests.
- The no-duplicate-assignment guarantee is a safety constraint, not a
  maintainability preference. It must be verified before any complexity win is
  considered successful.
- Diagnostics and Store records must continue to avoid exposing raw slot codes
  beyond what existing Rental Control behavior already exposes.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of existing reconciliation-related unit and integration
  tests pass unchanged after the implementation stage completes.
- **SC-002**: In 100% of reservation length-change, date-shift, code-change,
  same-guest rebooking, duplicate-name, and duplicate-physical-slot regression
  scenarios, each selected reservation appears in no more than one managed slot.
- **SC-003**: For identical inputs, `compute_desired_plan` and
  `compute_stateless_plan` produce byte-for-byte equivalent serialized plan
  outputs, including selected mappings, overflow, slot entries, actions, action
  ordering, reasons, and diagnostics.
- **SC-004**: In 100% of stable slot-name identity scenarios, trim-aware,
  prefix-aware, and start-time-disambiguated matching produces the same physical
  slot pairing as the current engine.
- **SC-005**: In 100% of replacement and reassignment scenarios, no new or
  replacement code is programmed until the same confirmed-empty conditions that
  exist today are satisfied.
- **SC-006**: In 100% of missing, deleted, stale, contradictory, or mid-run
  deleted Store scenarios, physical Keymaster state and current calendar data
  continue to determine correctness without manual Store repair.
- **SC-007**: The production callers that import reconciliation names continue
  to run without behavior changes, and existing tests that import additional
  public reconciliation models and helpers require no rewrite.
- **SC-008**: The decomposed reconciliation feature area contains no files of
  400 lines or more, no project-owned functions of 80 lines or more, and no
  project-owned parameter lists over 6 parameters.
- **SC-009**: The temporary `aislop-ignore-file` directive for reconciliation
  file size and function length is removed in the implementation stage, and the
  active complexity checks pass without suppressing the decomposed engine.
- **SC-010**: No production-code changes are included in this specification PR;
  the PR contains only the feature specification for the #627 decomposition
  pipeline.
