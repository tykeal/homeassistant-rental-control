<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Feature Specification: Decompose Check-in Sensor

**Feature Branch**: `014-decompose-checkinsensor`
**Created**: 2026-06-26
**Status**: Draft
**Input**: User description: "Decompose
`custom_components/rental_control/sensors/checkinsensor.py` for GitHub issue
#577. This is a behavior-preserving code-health refactor that separates
state-machine decisions, restore-state reconciliation, and timer scheduling from
the Home Assistant entity shell without changing runtime behavior."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Preserve Check-in State Behavior (Priority: P1)

As a property manager, I want the check-in tracking sensor to report the same
state, attributes, and transitions after decomposition, so upgrades do not
change how automations or dashboards observe reservations.

**Why this priority**: The feature is a code-health refactor. Its most important
outcome is preserving the existing four-state check-in/check-out behavior while
making the logic easier to maintain and test.

**Independent Test**: Can be fully tested by running the existing check-in
sensor behavior tests unchanged before and after the refactor and verifying the
same observable Home Assistant states, attributes, events, and transition order.

**Acceptance Scenarios**:

1. **Given** no relevant reservation exists, **When** the coordinator updates,
   **Then** the sensor remains in `no_reservation` with the same attributes as
   before the decomposition.
2. **Given** a future reservation becomes relevant, **When** the coordinator
   updates, **Then** the sensor transitions to `awaiting_checkin` with the same
   tracked event fields and scheduled transition target as before.
3. **Given** an awaiting reservation reaches check-in time while Keymaster
   monitoring does not require a door-code unlock, **When** the relevant update
   or timer occurs, **Then** the sensor transitions to `checked_in` exactly as it
   did before.
4. **Given** a checked-in reservation reaches checkout time, **When** the
   checkout path runs, **Then** the sensor transitions to `checked_out`, emits
   the same Home Assistant event payload, and preserves the same linger behavior.
5. **Given** a checked-out stay finishes its linger period or hands off to a
   follow-on reservation, **When** the relevant timer or update runs, **Then** the
   sensor returns to `no_reservation` or `awaiting_checkin` with no observable
   behavior change.

---

### User Story 2 - Preserve Restore Reconciliation (Priority: P1)

As an existing Rental Control user, I want restored check-in state to reconcile
with current time and calendar data exactly as before, so restarts and upgrades
remain safe for active and upcoming guests.

**Why this priority**: Restore-state reconciliation is one of the oversized and
bug-prone areas. Decomposition must make it testable without changing recovery
semantics for stale, missing, or pending state.

**Independent Test**: Can be fully tested by replaying restored-state scenarios
for each sensor state and confirming the same corrected state, timer target, and
persisted data values as the current implementation.

**Acceptance Scenarios**:

1. **Given** persisted state is `checked_in` and the tracked event ended while
   Home Assistant was down, **When** the entity is restored, **Then** the sensor
   silently reconciles to `checked_out` exactly as before.
2. **Given** persisted state is `checked_in` but the tracked event is far in the
   future, **When** restore validation runs, **Then** the self-healing checkout
   behavior is preserved.
3. **Given** persisted state is `awaiting_checkin` and the start time has passed,
   **When** Keymaster monitoring does not require a physical unlock, **Then** the
   silent automatic check-in and any immediate checkout behavior are unchanged.
4. **Given** persisted state is `checked_out`, **When** linger has expired or a
   different relevant reservation is available, **Then** the same transition is
   selected as before decomposition.
5. **Given** persisted state is `no_reservation` with a pending follow-up day,
   **When** the entity is restored, **Then** the same follow-up timer and stale
   follow-up cleanup behavior are preserved.
6. **Given** persisted state contains an unknown or corrupted state value,
   **When** restore validation runs, **Then** the sensor resets through the same
   `no_reservation` safety path as before.

---

### User Story 3 - Preserve Timer Scheduling Semantics (Priority: P1)

As a maintainer, I want all automatic transition timers to keep their existing
cancellation and rescheduling behavior after decomposition, so the refactor does
not introduce duplicate callbacks, missed callbacks, or stale transitions.

**Why this priority**: Timer management is intertwined with state transitions
and restore handling. Any drift in cancellation semantics can create visible
state changes or duplicate Home Assistant events.

**Independent Test**: Can be fully tested by exercising auto-check-in,
auto-checkout, same-day turnover, different-day follow-on, cleaning-window
linger, and restored pending timer scenarios while asserting the same single
active timer and transition target behavior as before.

**Acceptance Scenarios**:

1. **Given** a scheduled transition exists, **When** the tracked reservation
   changes or the sensor leaves that state, **Then** the prior cancel handle is
   invoked once and no stale callback remains active.
2. **Given** a checked-in reservation end time changes, **When** the coordinator
   update is processed, **Then** the auto-checkout timer is cancelled and
   rescheduled to the new end time with the same target semantics as before.
3. **Given** a checked-out stay has a same-day follow-on reservation, **When**
   linger timing is computed, **Then** the transition still occurs at the same
   midpoint between checkout and the next start.
4. **Given** a checked-out stay has a different-day follow-on reservation,
   **When** linger timing is computed, **Then** the midnight transition and
   follow-up awaiting timer remain unchanged.
5. **Given** no follow-on reservation exists, **When** checkout completes,
   **Then** the cleaning-window timer is scheduled and cancelled exactly as
   before.

---

### User Story 4 - Improve Maintainability Under Aislop Limits (Priority: P2)

As a maintainer, I want the check-in sensor behavior split into focused,
independently testable units, so future fixes can be reviewed and tested without
navigating a monolithic roughly 1,700-line entity file.

**Why this priority**: Issue #577 identifies the file as the largest module in
the integration and a source of multiple bugs. Reducing size and coupling lowers
future defect risk, but only after behavior preservation is protected.

**Independent Test**: Can be fully tested by measuring the changed files and
functions against the configured complexity thresholds and by adding focused
unit coverage for extracted decision behavior without requiring a full Home
Assistant entity fixture for every case.

**Acceptance Scenarios**:

1. **Given** the decomposition is complete, **When** complexity checks are run,
   **Then** the Home Assistant entity shell file is below 400 lines, each
   function is below 80 lines, and public initializer parameter counts are no
   more than 6 unless an existing Home Assistant framework signature requires
   otherwise.
2. **Given** state-transition decisions are exercised in tests, **When** the
   inputs describe coordinator data, current time, tracked event state, and
   monitoring status, **Then** the expected transition decision can be verified
   without constructing the full entity shell.
3. **Given** restore reconciliation decisions are exercised in tests, **When**
   persisted state, current time, and current coordinator data vary, **Then** the
   expected corrected state and timer intent can be verified independently.
4. **Given** timer scheduling behavior is exercised in tests, **When** transition
   targets change, **Then** cancellation and replacement behavior can be verified
   independently from unrelated entity attributes.

---

### Edge Cases

- What happens when persisted `CheckinExtraStoredData` was written by an older
  version? It remains readable, uses the same field names and defaults, and does
  not require migration or manual state deletion.
- What happens when a timer callback fires after its state is no longer current?
  The callback is still guarded by the current sensor state and cannot cause a
  stale transition that was impossible before the refactor.
- What happens when coordinator data is temporarily missing or an update fails?
  The same state preservation, warning, and `async_write_ha_state` behavior is
  retained.
- What happens when a tracked event disappears while checked in? The existing
  fallback behavior based on stored end time and preserved state remains
  unchanged.
- What happens when a newer, more relevant event appears while awaiting check-in?
  The same tracked-event replacement behavior and checked-out event exclusion
  remain unchanged.
- What happens when Home Assistant restarts with pending auto-check-in,
  auto-checkout, linger, or follow-up timers? Restore reconciliation recreates
  the same effective transition target and cancellation handle behavior.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The refactor MUST preserve all Home Assistant observable behavior
  of the check-in tracking sensor, including native state values, entity
  attributes, event payloads, icon/device metadata, transition order, log-worthy
  safety decisions, and service override effects.
- **FR-002**: Existing check-in sensor tests MUST pass unchanged after the
  decomposition; any new tests MUST verify behavior parity rather than define new
  runtime behavior.
- **FR-003**: The sensor MUST continue to model the existing state sequence
  `no_reservation`, `awaiting_checkin`, `checked_in`, and `checked_out`,
  including all existing self-healing and fallback transitions.
- **FR-004**: State-transition decisions MUST be separable from the Home
  Assistant entity shell so they can be tested with explicit inputs and expected
  decisions while preserving the same runtime side effects when applied by the
  entity.
- **FR-005**: Restore-state reconciliation MUST be separable from the entity
  shell so persisted state, current time, coordinator data, and pending timer
  information can be tested independently while preserving the same corrected
  runtime state.
- **FR-006**: Timer scheduling decisions MUST be separable from the entity shell
  so auto-check-in, auto-checkout, checkout linger, same-day turnover,
  different-day follow-on, cleaning-window, and restored follow-up timers can be
  tested independently.
- **FR-007**: `CheckinExtraStoredData` persistence MUST remain backward
  compatible with existing stored dictionaries, field names, date parsing,
  defaults, and optional fields.
- **FR-008**: The refactor MUST preserve the cancellation semantics of Home
  Assistant scheduled-callback helpers, including `async_call_later`-style or
  `async_track_point_in_time`-style unsubscribe handles, so each transition path
  has at most one active relevant timer.
- **FR-009**: Coordinator-update processing is a hot path and MUST avoid
  delegation overhead that is measurable in normal Home Assistant operation;
  behavior-preserving decomposition MUST not introduce blocking I/O, new
  coordinator refreshes, or extra Home Assistant state writes.
- **FR-010**: The entity class MUST remain the Home Assistant-facing boundary for
  entity lifecycle hooks, coordinator subscription, state writes, event bus
  emission, and service-facing behavior.
- **FR-011**: Decomposition MUST NOT change public APIs of unrelated Rental
  Control modules or require behavioral changes outside the check-in sensor
  feature area.
- **FR-012**: The completed implementation MUST bring the decomposed check-in
  sensor files and functions below the active aislop thresholds: files below 400
  lines, functions below 80 lines, and initializer parameter lists no more than 6
  parameters where project-owned signatures can be changed.
- **FR-013**: The plan and implementation stages MUST include regression
  coverage for coordinator-update transitions, restore reconciliation,
  persistence round trips, timer cancellation and rescheduling, debug override
  clearing, and Keymaster-unlock-triggered check-in behavior.
- **FR-014**: Documentation and review notes for later stages MUST clearly state
  that this feature is behavior-preserving and must not add new check-in states,
  new automation events, new configuration options, or changed timing rules.

### Key Entities

- **Check-in Tracking Sensor**: The Home Assistant entity that exposes check-in
  state, attributes, lifecycle behavior, coordinator updates, events, and
  service-facing operations for a rental unit.
- **Check-in State Snapshot**: The current logical state plus tracked event
  summary, event start and end, slot name, check-in source, checkout source,
  checkout time, transition target, checked-out event key, follow-up start day,
  and lock name.
- **Persisted Extra Stored Data**: The serialized representation of the state
  snapshot stored through Home Assistant restore-state support and read back on
  entity startup.
- **Transition Decision**: The behavior-preserving decision produced from the
  current snapshot, coordinator data, current time, and monitoring status before
  the entity applies any Home Assistant side effects.
- **Restore Reconciliation Decision**: The behavior-preserving correction made
  after persisted data is loaded, including stale-state fixes and timer intents.
- **Scheduled Transition**: A pending automatic state change represented by a
  target time, callback purpose, and cancel handle.

## Assumptions

- This specification covers issue #577's spec stage only; planning and
  implementation stages will decide exact file layout and helper boundaries.
- The current roughly 1,700-line `checkinsensor.py`, the approximately 229-line
  `_handle_coordinator_update`, the approximately 208-line
  `_validate_restored_state`, and the oversized stored-data initializer are the
  complexity baseline to improve.
- The behavior currently encoded by existing tests and by the source file is the
  source of truth unless a later accepted issue explicitly changes behavior.
- Runtime performance expectations are parity with the current implementation in
  normal Home Assistant operation, not a new user-visible performance feature.

## Non-Goals

- Changing check-in or checkout business rules, state names, state attributes,
  event payloads, logging policy, service behavior, or timer timing.
- Adding new features, configuration options, sensors, events, or recovery
  workflows.
- Redesigning calendar parsing, Keymaster slot reconciliation, lock-code
  programming, or public APIs of other Rental Control modules.
- Prescribing exact module names, file layout, class names, or helper function
  signatures for the implementation stage.
- Closing issue #577 in this specification PR; later implementation work owns the
  runtime refactor.

## Constraints

- State persistence through `CheckinExtraStoredData` MUST remain compatible with
  data written by existing releases.
- Timer cancellation semantics for Home Assistant scheduled callbacks MUST be
  preserved, including cancellation before replacement and clearing cancel
  handles after callbacks fire.
- Coordinator-update delegation MUST remain lightweight because it runs on the
  normal refresh hot path.
- No behavior observable by Home Assistant users, automations, dashboards, or
  tests may change as part of this refactor.
- This stage is documentation-only and MUST NOT include production code changes.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of existing check-in tracking sensor tests pass unchanged
  after the implementation stage completes.
- **SC-002**: In 100% of covered regression scenarios, native state, attributes,
  event payloads, transition order, and timer targets match the pre-refactor
  behavior.
- **SC-003**: 100% of persisted `CheckinExtraStoredData` fixtures written in the
  existing dictionary shape restore successfully with the same field values,
  defaults, and stale-state corrections.
- **SC-004**: 100% of auto-check-in, auto-checkout, linger, same-day turnover,
  different-day follow-on, cleaning-window, and restored follow-up timer tests
  observe a single active relevant timer and the same cancellation behavior as
  before.
- **SC-005**: The decomposed check-in sensor entity shell file is below 400
  lines, every project-owned function in the decomposed feature area is below 80
  lines, and project-owned initializer parameter lists are no more than 6
  parameters.
- **SC-006**: Focused tests can exercise state-transition, restore
  reconciliation, and timer scheduling decisions without requiring a full Home
  Assistant entity fixture for every decision case.
- **SC-007**: Normal coordinator-update processing performs no additional Home
  Assistant state writes, coordinator refreshes, blocking I/O, or user-visible
  delays compared with the current implementation.
- **SC-008**: No production-code changes are included in this specification PR;
  the PR contains only the feature specification for the #577 decomposition
  pipeline.
