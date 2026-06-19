<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Tasks: Slot Reconciliation

**Input**: Design documents from `/specs/012-slot-reconciliation/`
**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅,
quickstart.md ✅

**Tests**: Included and required. This feature changes safety-relevant door-code
slot management, so every invariant, success criterion, and reported regression
family has explicit unit or integration test tasks before implementation tasks.

**Organization**: Tasks are grouped by user story after shared setup and
foundational work. The redesign replaces per-`event_N` greedy slot mutation with
coordinator-owned reconciliation; `event_N` sensors become read-only views of the
latest reconciled plan.

## Format: `- [ ] T### [P?] [Story?] Description with file path`

- **[P]**: Can run in parallel (different files, no dependency on incomplete
  tasks)
- **[Story]**: Which user story this task belongs to (US1 through US8)
- Include exact file paths in descriptions

## Path Conventions

- **Source**: `custom_components/rental_control/` at repository root
- **Tests**: `tests/unit/`, `tests/integration/`, and `tests/fixtures/`
- **Feature docs**: `specs/012-slot-reconciliation/`

## Live Module Transition Scope

The implementation must change the live slot-management modules called out by
PLAN and issue #589: `custom_components/rental_control/coordinator.py`,
`custom_components/rental_control/event_overrides.py`,
`custom_components/rental_control/sensors/calsensor.py`,
`custom_components/rental_control/util.py`,
`custom_components/rental_control/sensor.py`,
`custom_components/rental_control/const.py`, and check-in protection state in
`custom_components/rental_control/sensors/checkinsensor.py`. The planned new
internal module is `custom_components/rental_control/reconciliation.py`.

---

## Phase 1: Setup & Baseline (Shared Infrastructure)

**Purpose**: Establish the merged artifact and existing-test baseline before
changing reconciliation tests or code.

- [ ] T001 Inspect the 18 functional requirements, 8 user stories, and 11 success criteria in specs/012-slot-reconciliation/spec.md
- [ ] T002 Inspect architecture decisions R-001 through R-008 in specs/012-slot-reconciliation/research.md
- [ ] T003 Inspect Reservation, ManagedSlot, DesiredPlan, Store mapping, and lifecycle state rules in specs/012-slot-reconciliation/data-model.md
- [ ] T004 Inspect validation scenarios and expected targeted commands in specs/012-slot-reconciliation/quickstart.md
- [ ] T005 Inventory current greedy slot mutation paths in custom_components/rental_control/event_overrides.py and custom_components/rental_control/sensors/calsensor.py
- [ ] T006 Run the baseline targeted pytest command for tests/unit/test_event_overrides.py, tests/unit/test_sensors.py, tests/unit/test_util.py, tests/unit/test_coordinator.py, tests/unit/test_checkin_sensor.py, tests/integration/test_refresh_cycle.py, and tests/integration/test_slot_concurrency.py

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Add shared typed models, Store schema, identity helpers, observed
actual-state helpers, and coordinator wiring scaffolding that every story needs.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

### Foundational Tests

- [ ] T007 [P] Add Reservation, ManagedSlot, DesiredPlan, PlannedSlot, SlotAction, SlotStatus, and validation tests in tests/unit/test_slot_reconciliation.py
- [ ] T008 Add stable reservation identity fingerprint, UID alias, booking alias, fingerprint-history, and ambiguous-rematch tests in tests/unit/test_slot_reconciliation.py
- [ ] T009 Add persisted Store schema v1 serialization, deserialization, no-raw-PIN, duplicate-slot rejection, and pending-fence validation tests in tests/unit/test_event_overrides.py
- [ ] T010 Add first-upgrade migration tests for adopting populated Keymaster slots without wiping working slots in tests/unit/test_coordinator.py
- [ ] T011 Add first-upgrade migration tests for prefixed or trimmed names, buffered dates, phantom name-only slots, ambiguous populated slots, and pending-clear restart fences in tests/unit/test_coordinator.py
- [ ] T012 [P] Add operation-result model tests for confirmed, unconfirmed, and failed Keymaster clear or set verification in tests/unit/test_util.py
- [ ] T013 [P] Add shared fixture builders for reservations, managed slots, persisted mappings, and actual Keymaster snapshots in tests/fixtures/event_data.py

### Foundational Implementation

- [ ] T014 Create typed dataclasses and enums for Reservation, ManagedSlot, DesiredPlan, PlannedSlot, SlotAction, SlotStatus, and Store mapping records in custom_components/rental_control/reconciliation.py
- [ ] T015 Implement stable reservation identity fingerprinting, UID alias normalization, booking alias extraction hooks, fingerprint history, and conservative rematch helpers in custom_components/rental_control/reconciliation.py
- [ ] T016 Add Store key, schema version, slot status, diagnostics, operation-token, and overflow constants in custom_components/rental_control/const.py
- [ ] T017 Implement HA Store load, save, schema v1 migration, first-upgrade adoption, and no-raw-PIN persistence helpers in custom_components/rental_control/coordinator.py
- [ ] T018 Add persisted mapping ownership, pending-clear fence, duplicate-mapping rejection, and actual-state cache fields to EventOverrides in custom_components/rental_control/event_overrides.py
- [ ] T019 Add observed Keymaster state collection and classification for free, occupied, phantom, partial_reset, unknown, and unmanaged slots in custom_components/rental_control/event_overrides.py
- [ ] T020 Change async_fire_clear_code(), async_fire_set_code(), and async_fire_update_times() to return explicit verified operation results in custom_components/rental_control/util.py
- [ ] T021 Add coordinator hooks to load Store during setup, pass normalized reservations into reconciliation, and persist changed mappings after each refresh in custom_components/rental_control/coordinator.py
- [ ] T022 Add read-only reconciliation state accessors used by event sensors and check-in protection consumers in custom_components/rental_control/coordinator.py
- [ ] T023 Run foundational pytest validation for tests/unit/test_slot_reconciliation.py, tests/unit/test_event_overrides.py, tests/unit/test_util.py, and tests/unit/test_coordinator.py

**Checkpoint**: Foundation ready — typed models, Store schema, identity matching,
actual-state classification, operation results, and coordinator scaffolding are
available for all user stories.

---

## Phase 3: User Story 1 — Program Soonest Eligible Reservations (Priority: P1) 🎯 MVP

**Goal**: The soonest eligible reservations occupy managed slots whenever
capacity and confirmed physical operations allow it.

**Independent Test**: Feed more eligible reservations than managed slots, run one
refresh, and verify the selected set is the earliest `max_events` by start time,
except for protected checked-in guests handled in US2.

### Tests for User Story 1

> **NOTE: Write these tests FIRST and ensure they fail until the soonest-N
> planner and coordinator reconciliation are implemented.**

- [ ] T024 [P] [US1] Add pure soonest-N overflow tests with max_events=3 and five eligible reservations in tests/unit/test_slot_reconciliation.py
- [ ] T025 [US1] Add no-farther-before-nearer, equal-start identity tie-breaker, churn-minimization, and overflow-rank diagnostic tests in tests/unit/test_slot_reconciliation.py
- [ ] T026 [US1] Add #535 nearer-not-programmed-when-full regression scenario with successful clear/set confirmations in tests/integration/test_refresh_cycle.py
- [ ] T027 [US1] Add #546 farther-evicts-nearer regression scenario proving the farther unprotected reservation becomes overflow in tests/integration/test_refresh_cycle.py
- [ ] T028 [P] [US1] Add coordinator refresh tests proving every refresh computes one DesiredPlan before publishing data in tests/unit/test_coordinator.py
- [ ] T029 [P] [US1] Add EventOverrides desired-plan application tests for set, update_times, noop, and overflow actions in tests/unit/test_event_overrides.py

### Implementation for User Story 1

- [ ] T030 [US1] Implement deterministic desired-plan selection by start time, identity tie-breaker, persisted-slot retention, and overflow reasons in custom_components/rental_control/reconciliation.py
- [ ] T031 [US1] Replace coordinator update_event_overrides() greedy per-event assignment with one refresh-level desired-plan computation in custom_components/rental_control/coordinator.py
- [ ] T032 [US1] Add EventOverrides apply-plan support for set, update_times, noop, overflow, and confirmed post-operation persistence in custom_components/rental_control/event_overrides.py
- [ ] T033 [US1] Ensure the coordinator publishes reconciled slot assignments and overflow status for sensors after apply-plan completes in custom_components/rental_control/coordinator.py
- [ ] T034 [US1] Preserve existing calendar event parsing and max_events filtering while passing normalized reservations to reconciliation in custom_components/rental_control/coordinator.py
- [ ] T035 [US1] Run US1 validation for tests/unit/test_slot_reconciliation.py, tests/unit/test_coordinator.py, tests/unit/test_event_overrides.py, and tests/integration/test_refresh_cycle.py

**Checkpoint**: US1 independently proves SC-001 and SC-004 for confirmed physical
operations and covers the #535 and #546 regression families.

---

## Phase 4: User Story 2 — Protect Current Guests Mid-Stay (Priority: P1)

**Goal**: A currently checked-in guest remains assigned through the active stay
window and counts against capacity.

**Independent Test**: Mark a reservation checked in, exceed slot capacity with
earlier or newly discovered reservations, and verify the active guest remains
assigned while remaining capacity is filled by soonest non-active reservations.

### Tests for User Story 2

- [ ] T036 [P] [US2] Add active checked-in guest selected-before-overflow and retained-current-slot tests in tests/unit/test_slot_reconciliation.py
- [ ] T037 [US2] Add protected guests count against managed-slot capacity and protection-expiry tests in tests/unit/test_slot_reconciliation.py
- [ ] T038 [US2] Add check-in tracking active-reservation surface tests for awaiting, checked_in, checked_out, linger, and no_reservation states in tests/unit/test_checkin_sensor.py
- [ ] T039 [US2] Add active guest never evicted mid-stay overflow integration scenario in tests/integration/test_refresh_cycle.py
- [ ] T040 [US2] Add check-in tracking preservation scenario for slot reconciliation changes in tests/integration/test_checkin_tracking.py

### Implementation for User Story 2

- [ ] T041 [US2] Expose active checked-in reservation identity, protected stay window, and checkout state to the coordinator in custom_components/rental_control/sensors/checkinsensor.py
- [ ] T042 [US2] Include protected active reservations and protection expiry in desired-plan computation in custom_components/rental_control/reconciliation.py
- [ ] T043 [US2] Wire check-in protection data into the coordinator reconciliation inputs in custom_components/rental_control/coordinator.py
- [ ] T044 [US2] Run US2 validation for tests/unit/test_slot_reconciliation.py, tests/unit/test_checkin_sensor.py, tests/integration/test_refresh_cycle.py, and tests/integration/test_checkin_tracking.py

**Checkpoint**: US2 independently proves SC-005 and keeps active guest protection
compatible with the existing check-in sensor lifecycle.

---

## Phase 5: User Story 3 — Self-Heal Corrupted Slot State (Priority: P1)

**Goal**: Normal coordinator refreshes recover from duplicate, phantom, stale,
and mis-assigned actual Keymaster state without restart, reload, or manual
clear-all.

**Independent Test**: Seed corrupted actual managed slots, run one or two normal
refresh cycles, and verify convergence to the desired plan unless a slot remains
blocked by unconfirmed physical operations.

### Tests for User Story 3

- [ ] T045 [P] [US3] Add duplicate actual assignment canonical-slot and non-canonical pending-clear tests in tests/unit/test_event_overrides.py
- [ ] T046 [P] [US3] Add no reservation in two desired slots and no slot with two reservations invariant tests in tests/unit/test_slot_reconciliation.py
- [ ] T047 [US3] Add #589 triple-assignment duplicate-collapse regression scenario converging in one or two refreshes in tests/integration/test_refresh_cycle.py
- [ ] T048 [US3] Add #521 phantom name-only slot regression scenario with pending-clear then reuse after confirmed clear in tests/integration/test_refresh_cycle.py
- [ ] T049 [US3] Add stale expired assignment self-heal scenario converging without restart or reload in tests/integration/test_refresh_cycle.py
- [ ] T050 [US3] Add stale mis-assigned slot self-heal scenario replacing the wrong reservation with the desired one in tests/integration/test_refresh_cycle.py
- [ ] T051 [US3] Add nearer-not-programmed-when-full corrupt starting state regression for #535 in tests/integration/test_refresh_cycle.py
- [ ] T052 [US3] Add farther-evicts-nearer corrupt starting state regression for #546 in tests/integration/test_refresh_cycle.py
- [ ] T053 [US3] Add caplog coverage for duplicate collapse, overflow decision, phantom recovery, stale correction, and mis-assignment correction in tests/unit/test_event_overrides.py
- [ ] T054 [US3] Add unmanaged-slot ignored tests proving slots outside the RC-managed range are never changed in tests/unit/test_event_overrides.py

### Implementation for User Story 3

- [ ] T055 [US3] Implement duplicate actual assignment detection, canonical slot selection, and non-canonical pending-clear actions in custom_components/rental_control/reconciliation.py
- [ ] T056 [US3] Implement phantom, stale, partial-reset, and mis-assigned actual-state diff generation in custom_components/rental_control/reconciliation.py
- [ ] T057 [US3] Apply duplicate, phantom, stale, and mis-assigned corrections under the EventOverrides single lock in custom_components/rental_control/event_overrides.py
- [ ] T058 [US3] Log duplicate collapse, overflow decisions, phantom recovery, stale correction, and mis-assignment correction with redacted code data in custom_components/rental_control/event_overrides.py
- [ ] T059 [US3] Run US3 validation for tests/unit/test_slot_reconciliation.py, tests/unit/test_event_overrides.py, and tests/integration/test_refresh_cycle.py

**Checkpoint**: US3 independently proves SC-002 and SC-003, including the #589,
#521, #535, and #546 corrupt-state regression families.

---

## Phase 6: User Story 4 — Avoid Double-Assignment on Clear Failures (Priority: P1)

**Goal**: A slot remains occupied, pending-clear, or blocked until physical
Keymaster state confirms that it is safe to reuse.

**Independent Test**: Force a clear to fail or remain unconfirmed, run
reconciliation, and verify no different reservation receives the slot until a
later confirmed clear succeeds.

### Tests for User Story 4

- [ ] T060 [P] [US4] Add async_fire_clear_code() confirmed, unconfirmed, lingering-name, lingering-PIN, and service-failure tests in tests/unit/test_util.py
- [ ] T061 [P] [US4] Add pending-clear fenced-token lifecycle, stale-token rejection, retry_clear, and later-confirmed-free tests in tests/unit/test_event_overrides.py
- [ ] T062 [P] [US4] Add desired-plan tests excluding pending-clear, blocked, and unknown slots from assignment capacity in tests/unit/test_slot_reconciliation.py
- [ ] T063 [US4] Add clear failure slot-not-reused and no double-assignment integration scenario in tests/integration/test_refresh_cycle.py
- [ ] T064 [US4] Add callback re-entrancy fencing scenario where callbacks cannot launch reconciliation during a pending operation in tests/integration/test_slot_concurrency.py

### Implementation for User Story 4

- [ ] T065 [US4] Implement verified clear, set, update_times, and redacted result objects for Keymaster service helpers in custom_components/rental_control/util.py
- [ ] T066 [US4] Persist fenced operation tokens before service calls and verify matching tokens before clearing fences in custom_components/rental_control/event_overrides.py
- [ ] T067 [US4] Keep pending-clear, blocked, and unknown slots unavailable to desired-plan assignment until confirmed free in custom_components/rental_control/reconciliation.py
- [ ] T068 [US4] Serialize reconciliation and state-change callbacks through the existing single EventOverrides lock without re-entrant reconciliation in custom_components/rental_control/event_overrides.py
- [ ] T069 [US4] Run US4 validation for tests/unit/test_util.py, tests/unit/test_event_overrides.py, tests/unit/test_slot_reconciliation.py, tests/integration/test_refresh_cycle.py, and tests/integration/test_slot_concurrency.py

**Checkpoint**: US4 independently proves SC-006 and the confirmed-clear safety
model.

---

## Phase 7: User Story 5 — Correct and Log Manual Tampering (Priority: P2)

**Goal**: RC-managed slots remain authoritative while manual or external edits
inside the managed range are corrected and logged for troubleshooting.

**Independent Test**: Manually drift a managed slot's name, code, or date range,
run one refresh, and verify the desired state is restored with a log entry.

### Tests for User Story 5

- [ ] T070 [P] [US5] Add manual managed-slot name, code, start date, end date, and date-range switch drift tests in tests/unit/test_event_overrides.py
- [ ] T071 [P] [US5] Add manual drift overwrite action tests preserving desired reservation identity and redacting PIN data in tests/unit/test_slot_reconciliation.py
- [ ] T072 [US5] Add manual edit corrected plus caplog assertion integration scenario in tests/integration/test_refresh_cycle.py
- [ ] T073 [US5] Add unmanaged manual edit ignored integration scenario in tests/integration/test_refresh_cycle.py

### Implementation for User Story 5

- [ ] T074 [US5] Detect manual or external drift by comparing desired mappings with observed actual managed-slot state in custom_components/rental_control/event_overrides.py
- [ ] T075 [US5] Generate overwrite_manual_change actions for managed-slot drift and ignore unmanaged slots in custom_components/rental_control/reconciliation.py
- [ ] T076 [US5] Log manual or external overwrite details with slot number, field names, identity, and redacted code fields in custom_components/rental_control/event_overrides.py
- [ ] T077 [US5] Run US5 validation for tests/unit/test_event_overrides.py, tests/unit/test_slot_reconciliation.py, and tests/integration/test_refresh_cycle.py

**Checkpoint**: US5 independently proves SC-007 and the manual-edit parts of
FR-009, FR-016, and FR-017.

---

## Phase 8: User Story 6 — Survive Restarts and Noisy Feeds (Priority: P2)

**Goal**: Reservation-to-slot identity survives Home Assistant restarts,
volatile calendar identifier churn, and one or two consecutive feed misses.

**Independent Test**: Assign reservations, persist mappings, simulate restart and
UID churn, then omit a reservation for one, two, and three refreshes while
checking continuity and clear eligibility.

### Tests for User Story 6

- [ ] T078 [P] [US6] Add exact stable-fingerprint restart mapping preservation tests in tests/unit/test_event_overrides.py
- [ ] T079 [P] [US6] Add UID-changed but stable name/start/end mapping retention tests in tests/unit/test_slot_reconciliation.py
- [ ] T080 [US6] Add UID-match date-shift mapping update and should-update-code interaction tests in tests/unit/test_slot_reconciliation.py
- [ ] T081 [US6] Add conservative continuity rematch tests for fingerprint history, booking aliases, normalized name, non-overlap ordering, and actual-slot continuity in tests/unit/test_slot_reconciliation.py
- [ ] T082 [US6] Add ambiguous continuity rematch diagnostics tests where two candidates remain compatible in tests/unit/test_slot_reconciliation.py
- [ ] T083 [US6] Add restart with persisted Store mapping and UID churn integration scenario in tests/integration/test_refresh_cycle.py
- [ ] T084 [US6] Add first-upgrade migration does-not-wipe-working-slots integration scenario in tests/integration/test_refresh_cycle.py
- [ ] T085 [US6] Add two-cycle transient miss tolerance and third-miss clearable integration scenario in tests/integration/test_refresh_cycle.py
- [ ] T086 [US6] Add reappearing-before-third-miss resets missing_count and retains mapping integration scenario in tests/integration/test_refresh_cycle.py

### Implementation for User Story 6

- [ ] T087 [US6] Rehydrate persisted mappings, identity aliases, missing_count, pending fences, and last observed actual state during coordinator setup in custom_components/rental_control/coordinator.py
- [ ] T088 [US6] Update persisted mappings after identity alias changes, date shifts, continuity rematches, set confirmations, clear confirmations, and overflow decisions in custom_components/rental_control/coordinator.py
- [ ] T089 [US6] Implement feed-miss lifecycle retaining assigned reservations through missing_count 1 and 2 then making them clearable on missing_count 3 in custom_components/rental_control/reconciliation.py
- [ ] T090 [US6] Run US6 validation for tests/unit/test_slot_reconciliation.py, tests/unit/test_event_overrides.py, tests/unit/test_coordinator.py, and tests/integration/test_refresh_cycle.py

**Checkpoint**: US6 independently proves SC-008 and SC-009, including first-upgrade
migration safety and booking-platform identity churn.

---

## Phase 9: User Story 7 — Troubleshoot Desired vs Actual State (Priority: P3)

**Goal**: Diagnostics expose desired mappings, actual Keymaster state, pending or
blocked corrections, and overflow reasons in one capture.

**Independent Test**: Create matched, overflow, manual-drift, and pending-clear
states, capture diagnostics, and verify each managed slot has enough data to
diagnose without log correlation.

### Tests for User Story 7

- [ ] T091 [P] [US7] Add diagnostics snapshot tests for matched slots, pending corrections, blocked clear reasons, retry count, and last error in tests/unit/test_event_overrides.py
- [ ] T092 [P] [US7] Add per-reservation diagnostics tests for selected, protected, overflow, missing_count, assigned slot, and identity aliases in tests/unit/test_slot_reconciliation.py
- [ ] T093 [US7] Add diagnostics desired-vs-actual completeness integration scenario with matched slot, overflow reservation, manual drift, and pending clear in tests/integration/test_refresh_cycle.py
- [ ] T094 [P] [US7] Add existing diagnostics redaction compatibility tests for slot codes and reservation metadata in tests/unit/test_keymaster_event_diagnostics.py

### Implementation for User Story 7

- [ ] T095 [US7] Build per-refresh DesiredPlan diagnostics snapshots with plan_id, timestamps, desired state, actual classification, pending action, blocked reason, and overflow details in custom_components/rental_control/reconciliation.py
- [ ] T096 [US7] Store and expose the latest diagnostics snapshot from EventOverrides for Home Assistant diagnostics collection in custom_components/rental_control/event_overrides.py
- [ ] T097 [US7] Wire coordinator diagnostics access to the latest reconciliation snapshot without exposing raw PIN values in custom_components/rental_control/coordinator.py
- [ ] T098 [US7] Run US7 validation for tests/unit/test_event_overrides.py, tests/unit/test_slot_reconciliation.py, tests/unit/test_keymaster_event_diagnostics.py, and tests/integration/test_refresh_cycle.py

**Checkpoint**: US7 independently proves SC-010 and diagnostics support for
SC-006 and FR-017.

---

## Phase 10: User Story 8 — Preserve Existing Lock-Code Semantics (Priority: P1)

**Goal**: Reconciliation changes slot ownership mechanics without changing slot
name, buffer, PMS time, code regeneration, event sensor, or check-in semantics.

**Independent Test**: Exercise existing behavior before and after the redesign
and verify observable semantics stay unchanged while event sensors become
read-only views of coordinator reconciliation state.

### Tests for User Story 8

- [ ] T099 [P] [US8] Add sensor read-only tests proving RentalControlCalSensor no longer calls async_reserve_or_get_slot(), async_fire_set_code(), async_fire_clear_code(), or async_fire_update_times() in tests/unit/test_sensors.py
- [ ] T100 [US8] Add event_N attribute regression tests for slot_number, slot_code, summary, start, end, ETA, UID, slot name, and no-reservation state in tests/unit/test_sensors.py
- [ ] T101 [P] [US8] Add slot-name trimming and prefix preservation regression tests for Keymaster display names in tests/unit/test_event_overrides.py
- [ ] T102 [P] [US8] Add lock-code before-buffer and after-buffer regression tests in tests/unit/test_util.py
- [ ] T103 [P] [US8] Add honor-PMS-times regression tests for timed events, description times, override fallback, and configured defaults in tests/unit/test_coordinator.py
- [ ] T104 [US8] Add date-based code regeneration and should_update_code regression tests for date shifts, stable codes, regenerated codes, and update_times-only paths in tests/unit/test_sensors.py
- [ ] T105 [P] [US8] Add check-in tracking state machine regression tests for assigned reservation changes and preserved checkout behavior in tests/unit/test_checkin_sensor.py
- [ ] T106 [US8] Add end-to-end preserved semantics scenario covering slot names, buffers, honor-PMS-times, code regeneration, and check-in tracking in tests/integration/test_refresh_cycle.py

### Implementation for User Story 8

- [ ] T107 [US8] Convert RentalControlCalSensor._handle_coordinator_update() to read slot_number and slot_code from coordinator reconciliation state only in custom_components/rental_control/sensors/calsensor.py
- [ ] T108 [US8] Remove slot mutation side effects from RentalControlCalSensor._async_handle_slot_assignment() while preserving existing public attributes in custom_components/rental_control/sensors/calsensor.py
- [ ] T109 [US8] Keep sensor platform setup and entity naming unchanged while routing event_N sensors to reconciliation state in custom_components/rental_control/sensor.py
- [ ] T110 [US8] Preserve slot-name trimming, prefix handling, and legacy EventOverrides read APIs for backward-compatible sensor reads in custom_components/rental_control/event_overrides.py
- [ ] T111 [US8] Preserve lock-code buffer, date range, PMS time, and code regeneration semantics in custom_components/rental_control/util.py
- [ ] T112 [US8] Preserve check-in tracking transitions and checkout behavior while using reconciliation assignments for active protection in custom_components/rental_control/sensors/checkinsensor.py
- [ ] T113 [US8] Run US8 validation for tests/unit/test_sensors.py, tests/unit/test_event_overrides.py, tests/unit/test_util.py, tests/unit/test_coordinator.py, tests/unit/test_checkin_sensor.py, and tests/integration/test_refresh_cycle.py

**Checkpoint**: US8 independently proves SC-011 and completes the event sensor
read-only transition required by the redesign.

---

## Phase 11: Polish & Cross-Cutting Concerns

**Purpose**: Retire superseded paths, validate full behavior, and prepare the
implementation PR without mixing code and task-list completion commits.

- [ ] T114 Remove or retire EventOverrides._next_slot, async_reserve_or_get_slot() authoritative behavior, and async_check_overrides() cleanup policy after replacements are covered in custom_components/rental_control/event_overrides.py
- [ ] T115 Remove obsolete per-event slot assignment scheduling and mutation fallback code from RentalControlCalSensor in custom_components/rental_control/sensors/calsensor.py
- [ ] T116 Confirm backward-compatible helper APIs used by sensors, diagnostics, and tests still exist or have migration shims in custom_components/rental_control/event_overrides.py
- [ ] T117 Run targeted feature tests from specs/012-slot-reconciliation/quickstart.md for tests/unit/test_slot_reconciliation.py, tests/unit/test_event_overrides.py, tests/unit/test_util.py, tests/integration/test_refresh_cycle.py, and tests/integration/test_slot_concurrency.py
- [ ] T118 Run full test suite with coverage using `uv run pytest tests/ -x -q` for tests/
- [ ] T119 Run ruff validation with `uv run ruff check custom_components/ tests/` for custom_components/rental_control/ and tests/
- [ ] T120 Run mypy and interrogate through `uv run pre-commit run mypy interrogate --all-files` for custom_components/rental_control/ and tests/
- [ ] T121 Run full pre-commit validation including reuse, yamllint, actionlint, aislop, gitlint, ruff, ruff-format, mypy, and interrogate for custom_components/rental_control/, tests/, and specs/012-slot-reconciliation/tasks.md
- [ ] T122 Validate manual quickstart scenarios for overflow, active protection, manual drift, clear failure, restart, and UID churn against specs/012-slot-reconciliation/quickstart.md
- [ ] T123 Commit only implementation code and tests in the atomic sequence below, then commit tasks.md status updates separately in specs/012-slot-reconciliation/tasks.md

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies; establishes artifact and test baseline
- **Foundational (Phase 2)**: Depends on Setup; BLOCKS all user stories
- **US1 (Phase 3)**: Depends on Foundational desired-plan and Store scaffolding
- **US2 (Phase 4)**: Depends on Foundational and can run with US1 tests once
  protection inputs exist; implementation depends on the plan selector from US1
- **US3 (Phase 5)**: Depends on Foundational and US1 apply-plan paths
- **US4 (Phase 6)**: Depends on Foundational operation-result models and must be
  complete before any cleanup that would reuse slots
- **US5 (Phase 7)**: Depends on US1 desired-vs-actual comparison and US4
  operation fencing
- **US6 (Phase 8)**: Depends on Foundational Store scaffolding and US1 selected
  mappings; feed-miss clear behavior depends on US4 confirmed-clear safety
- **US7 (Phase 9)**: Depends on US1 through US6 state and action metadata
- **US8 (Phase 10)**: Depends on US1 coordinator-owned assignments and should be
  completed before Polish cleanup removes superseded sensor mutation paths
- **Polish (Phase 11)**: Depends on all user stories being complete

### User Story Dependencies

- **US1 (P1)**: MVP starts after Foundational; proves soonest-N selection
- **US2 (P1)**: Extends US1 selection with active-guest protection
- **US3 (P1)**: Extends US1/US4 mechanics for corrupt actual-state self-heal
- **US4 (P1)**: Safety gate for any slot reuse; required before merge
- **US8 (P1)**: Compatibility gate; required before merge
- **US5 (P2)**: Builds on desired-vs-actual comparison and confirmed operations
- **US6 (P2)**: Builds on Store, identity, desired-plan, and confirmed clears
- **US7 (P3)**: Builds on all action and status metadata

### Within Each User Story

- Tests MUST be written first and must fail or protect existing behavior before
  implementation tasks make them pass
- Pure reconciliation model tasks precede coordinator and EventOverrides wiring
- Store persistence and fenced operation tokens precede physical service calls
- Sensor read-only conversion precedes removal of superseded greedy paths
- Story validation runs before moving to the next lower-priority story

### Parallel Opportunities

- T007, T012, and T013 can proceed in parallel after T006; T008 follows T007 in the same file
- T024, T028, and T029 can proceed in parallel after T023; T025 follows T024 in the same file
- T036 can proceed in parallel with T038 after T035; T037 follows T036 in the same file
- T045 and T046 can proceed in parallel after T035; T053 and T054 follow other EventOverrides tests in the same file
- T060, T061, and T062 can proceed in parallel after T023
- T070 and T071 can proceed in parallel after T069
- T078 and T079 can proceed in parallel after T023; T080 through T082 follow
  T079 in the same file
- T091, T092, and T094 can proceed in parallel after T090
- T099, T101, T102, T103, and T105 can proceed in parallel after T098;
  T100 and T104 follow T099 in the same file

---

## Parallel Example: User Story 3

```bash
# After US1 and foundational operation helpers are in place, split corrupt-state
# tests across unit and integration files:
Task: "Duplicate actual assignment tests in tests/unit/test_event_overrides.py" # T045
Task: "No duplicate desired mapping tests in tests/unit/test_slot_reconciliation.py" # T046
Task: "#589/#521/#535/#546 convergence scenarios in tests/integration/test_refresh_cycle.py" # T047-T052

# Then implement model and state-owner pieces in order:
Task: "Duplicate and phantom diff generation in custom_components/rental_control/reconciliation.py" # T055-T056
Task: "Apply corrections under the EventOverrides lock in custom_components/rental_control/event_overrides.py" # T057-T058
```

---

## Implementation Strategy

### MVP First (P1 Safety Stories)

1. Complete Setup and Foundational phases (T001-T023)
2. Complete US1 soonest-N programming (T024-T035)
3. Complete US2 active-guest protection (T036-T044)
4. Complete US3 corrupt-state self-heal (T045-T059)
5. Complete US4 confirmed-clear safety (T060-T069)
6. Complete US8 compatibility and sensor read-only transition (T099-T113)
7. **STOP and VALIDATE**: Run T117-T121 before any implementation PR merge

### Incremental Delivery

1. Data structures, Store, and identity helpers make pure tests pass
2. Desired-plan computation selects the authoritative reservation set
3. Confirmed-clear apply-diff safely changes physical Keymaster state
4. Coordinator publishes one reconciled plan and sensors become read-only
5. Persistence, noisy-feed tolerance, diagnostics, and manual-drift logging add
   supportability without weakening safety gates
6. Cleanup removes only superseded greedy paths after compatibility tests pass

### Atomic Commit Sequence

1. `Feat(core): Add reconciliation data models` — T007, T014
2. `Feat(store): Add slot mapping persistence` — T009-T011, T016-T018, T021
3. `Feat(identity): Add reservation rematching` — T008, T015, T078-T082
4. `Feat(plan): Compute desired slot plans` — T024-T030, T036-T037, T042
5. `Feat(reconcile): Apply confirmed slot diffs` — T029, T032, T060-T069
6. `Feat(coordinator): Own slot reconciliation` — T028, T031, T033-T035, T043
7. `Feat(sensors): Make event sensors read-only` — T099-T100, T107-T109
8. `Feat(diagnostics): Expose slot plan state` — T091-T098
9. `Feat(drift): Log managed slot corrections` — T070-T077
10. `Test(reconcile): Cover corrupt states` — T045-T054, T047-T052
11. `Test(reconcile): Cover persistence feeds` — T083-T090
12. `Test(reconcile): Preserve existing behavior` — T101-T106, T110-T113
13. `Refactor(slots): Retire greedy slot paths` — T114-T116
14. `Chore(validate): Run slot reconciliation gates` — T117-T122
15. `Docs(tasks): Mark spec 012 tasks complete` — T123 only, separate from code

---

## Traceability Matrix

| Acceptance gate | Test tasks |
|-----------------|------------|
| SC-001 soonest N by start time | T024, T026, T035 |
| SC-002 no reservation in two slots | T046, T047, T059 |
| SC-003 corrupt-state self-heal | T045, T047-T052, T059 |
| SC-004 no farther before nearer | T025, T027, T051, T052 |
| SC-005 active guest protected | T036-T040, T044 |
| SC-006 failed clear blocks reuse | T060-T064, T069 |
| SC-007 manual edit corrected/logged | T070-T073, T077 |
| SC-008 restart with persisted mapping | T078-T084, T090 |
| SC-009 two-cycle miss tolerance | T085, T086, T089, T090 |
| SC-010 diagnostics desired vs actual | T091-T094, T098 |
| SC-011 existing semantics preserved | T099-T106, T113 |
| #589 triple assignment | T047 |
| #535 nearer not programmed when full | T026, T051 |
| #546 farther evicts nearer | T027, T052 |
| #521 phantom name-only slot | T048 |

---

## Notes

- Tasks marked [P] use different files or test scopes and do not depend on
  another incomplete task in the same file
- Reconciliation is scoped only to RC-managed Keymaster slots; unmanaged slots
  must not be modified
- Pending-clear, blocked, and unknown slots are never reused until confirmed
  free by observed physical Keymaster state
- Raw PIN values must not be written to HA Store, diagnostics, or logs
- Existing tests for slot-name trimming, buffers, honor-PMS-times,
  should-update-code, code regeneration, and check-in tracking must stay green
- Do not close issue #589 from tasks or planning commits; the implementation PR
  owns runtime closure
