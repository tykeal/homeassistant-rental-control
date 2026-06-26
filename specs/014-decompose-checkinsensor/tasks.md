<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Tasks: Decompose Check-in Sensor

**Input**: Design documents from `/specs/014-decompose-checkinsensor/`
**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅,
quickstart.md ✅

**Tests**: Included and required. This feature is a behavior-preserving
refactor of the check-in tracking sensor, so existing parity tests remain the
primary oracle and new focused tests cover extracted pure decisions, restore
reconciliation, timer scheduling, and persistence compatibility.

**Organization**: Tasks are grouped by setup, foundational module boundaries,
and user stories from the specification. Implementation must keep
`custom_components/rental_control/sensors/checkinsensor.py` as the Home
Assistant entity shell while moving behavior into the internal
`custom_components/rental_control/sensors/checkin/` package.

## Format: `- [ ] T### [P?] [Story?] Description with file path`

- **[P]**: Can run in parallel (different files, no dependency on incomplete
  tasks)
- **[Story]**: Which user story the task belongs to (US1 through US4)
- Include exact file paths in descriptions

## Path Conventions

- **Entity shell**: `custom_components/rental_control/sensors/checkinsensor.py`
- **Extracted package**: `custom_components/rental_control/sensors/checkin/`
- **Unit tests**: `tests/unit/`
- **Integration tests**: `tests/integration/`
- **Feature docs**: `specs/014-decompose-checkinsensor/`

## Live Module Transition Scope

Implementation changes the live check-in tracking code only. The target module
split from PLAN is:

- `custom_components/rental_control/sensors/checkinsensor.py` — HA-facing entity
  shell, lifecycle hooks, service methods, event bus effects, coordinator
  callback, state writes, private transition-method compatibility, and stable
  compatibility re-exports.
- `custom_components/rental_control/sensors/checkin/__init__.py` — internal
  package marker and typed exports.
- `custom_components/rental_control/sensors/checkin/models.py` —
  `CheckinStateSnapshot`, `CoordinatorUpdateContext`, `TransitionDecision`,
  `RestoreReconciliationDecision`, `ScheduledTransition`, effect/log intent
  value types, and state/timer enums.
- `custom_components/rental_control/sensors/checkin/persistence.py` —
  `CheckinExtraStoredData` plus snapshot ↔ stored-dict conversion.
- `custom_components/rental_control/sensors/checkin/event_selection.py` —
  event identity, relevant/tracked/follow-on selection, and slot-name
  extraction.
- `custom_components/rental_control/sensors/checkin/transition_decisions.py` —
  coordinator-update decisions split by current check-in state.
- `custom_components/rental_control/sensors/checkin/restore_decisions.py` —
  restore reconciliation decisions split by restored state.
- `custom_components/rental_control/sensors/checkin/timers.py` —
  `CheckinTimerManager` preserving the single active
  `async_track_point_in_time()` handle semantics.
- `custom_components/rental_control/sensors/checkin/applicator.py` — ordered
  decision-application helpers that call entity-owned transition, timer, log,
  and write effects.

The real existing tests to preserve or extend are
`tests/unit/test_checkin_sensor.py`,
`tests/integration/test_checkin_tracking.py`,
`tests/unit/test_keymaster_event_diagnostics.py`,
`tests/unit/test_sensors.py`, and `tests/integration/test_full_setup.py`.
New focused tests live in `tests/unit/test_checkin_decisions.py`,
`tests/unit/test_checkin_restore.py`, `tests/unit/test_checkin_timers.py`, and
`tests/unit/test_checkin_persistence.py`.

---

## Phase 1: Setup & Baseline (Shared Infrastructure)

**Purpose**: Establish the behavior and complexity baseline before changing any
production code.

- [ ] T001 Run `.specify/scripts/bash/check-prerequisites.sh --json` with `SPECIFY_FEATURE=014-decompose-checkinsensor` from the repository root and confirm `specs/014-decompose-checkinsensor/` reports `research.md`, `data-model.md`, and `quickstart.md`
- [ ] T002 Inspect US1-US4, FR-001 through FR-014, and SC-001 through SC-008 in `specs/014-decompose-checkinsensor/spec.md`
- [ ] T003 Inspect the Project Structure and Concrete Decomposition Design in `specs/014-decompose-checkinsensor/plan.md`
- [ ] T004 Inspect R-001 through R-005, all data-model entities, and quickstart validation scenarios in `specs/014-decompose-checkinsensor/research.md`, `specs/014-decompose-checkinsensor/data-model.md`, and `specs/014-decompose-checkinsensor/quickstart.md`
- [ ] T005 Inventory live `CheckinExtraStoredData`, `_handle_coordinator_update`, event-selection helpers, transition methods, timer callbacks, `_validate_restored_state`, `async_checkout`, `async_set_state`, and `async_handle_keymaster_unlock` in `custom_components/rental_control/sensors/checkinsensor.py`
- [ ] T006 Inventory existing parity coverage for persistence, restore, timers, Keymaster unlocks, debug overrides, and lifecycle behavior in `tests/unit/test_checkin_sensor.py` and `tests/integration/test_checkin_tracking.py`
- [ ] T007 Run unchanged baseline parity tests with `uv run pytest tests/unit/test_checkin_sensor.py tests/integration/test_checkin_tracking.py tests/unit/test_keymaster_event_diagnostics.py tests/unit/test_sensors.py tests/integration/test_full_setup.py -q` against the listed test files
- [ ] T008 Record the current line, function-length, and initializer-parameter baseline for `custom_components/rental_control/sensors/checkinsensor.py` in the implementation PR notes, including that `CheckinTrackingSensor.__init__` has only `hass`, `coordinator`, and `config_entry`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Create the shared internal package, typed snapshots, persistence
compatibility layer, and event-selection helpers that all story work depends on.

**⚠️ CRITICAL**: No user-story extraction can complete until this phase is done.
Models must land before modules that import them.

### Foundational Tests

- [ ] T009 [P] Add stored-dict contract tests for exact keys, ISO datetime output, missing-field defaults, invalid datetime warnings, `checkin_lock_name`, and `next_event_start_day` in `tests/unit/test_checkin_persistence.py`
- [ ] T010 Add snapshot round-trip and legacy keyword-construction tests proving `CheckinExtraStoredData` targets the persisted-data initializer, not `CheckinTrackingSensor.__init__`, in `tests/unit/test_checkin_persistence.py`
- [ ] T011 [P] Add event-key, relevant-event, tracked-event, follow-on, checked-out-key exclusion, coordinator-order, and slot-name extraction tests in `tests/unit/test_checkin_decisions.py`

### Foundational Implementation

- [ ] T012 Add `custom_components/rental_control/sensors/checkin/__init__.py` with SPDX headers, a module docstring, and typed exports for the internal check-in helper package
- [ ] T013 Add `CheckinStateSnapshot`, `CoordinatorUpdateContext`, `DecisionEffect`, `LogIntent`, `TransitionDecision`, `RestoreReconciliationDecision`, `ScheduledTransition`, state constants, and timer-purpose value types in `custom_components/rental_control/sensors/checkin/models.py`
- [ ] T014 [P] Move `CheckinExtraStoredData` into `custom_components/rental_control/sensors/checkin/persistence.py` with `from_snapshot()`, snapshot-based construction, legacy keyword compatibility, unchanged `from_dict()`, and unchanged `as_dict()` output
- [ ] T015 Re-export `CheckinExtraStoredData` from `custom_components/rental_control/sensors/checkinsensor.py` and update `extra_restore_state_data` to use `CheckinStateSnapshot` without changing the stored-dict contract in `custom_components/rental_control/sensors/checkinsensor.py`
- [ ] T016 [P] Extract `_event_key`, `_get_relevant_event`, `_find_tracked_event`, `_find_followon_event`, and `_extract_slot_name` behavior into `custom_components/rental_control/sensors/checkin/event_selection.py` without changing event scan ordering or checked-out event exclusion
- [ ] T017 Wire `custom_components/rental_control/sensors/checkinsensor.py` to delegate event-selection calls to `custom_components/rental_control/sensors/checkin/event_selection.py` while preserving private helper compatibility for existing tests
- [ ] T018 Run foundational validation with `uv run pytest tests/unit/test_checkin_persistence.py tests/unit/test_checkin_decisions.py tests/unit/test_checkin_sensor.py -q` against the listed test files

**Checkpoint**: The internal package exists, persistence remains backward
compatible, event selection is pure and covered, and the entity shell still
constructs as `CheckinTrackingSensor(hass, coordinator, config_entry)`.

---

## Phase 3: User Story 1 - Preserve Check-in State Behavior (Priority: P1) 🎯 MVP

**Goal**: Coordinator updates produce the same four-state transitions,
attributes, event payloads, transition ordering, and write behavior after the
logic moves out of the entity shell.

**Independent Test**: Run existing check-in behavior tests unchanged and focused
coordinator-decision tests, then verify observable state, attributes, HA events,
log intents, and timer intents match the pre-refactor behavior.

### Tests for User Story 1

> **NOTE: Add focused tests for extracted decisions first. Existing parity tests
> must keep their behavior assertions unchanged.**

- [ ] T019 [US1] Add `no_reservation` and `awaiting_checkin` coordinator-decision tests for no-op writes, awaiting transitions, mutable tracked-field refresh, earlier-event replacement, cancelled-event fallback, automatic check-in with monitoring off, and monitoring-on deferral in `tests/unit/test_checkin_decisions.py`
- [ ] T020 [US1] Add `checked_in` coordinator-decision tests for tracked-event refresh, far-future self-healing checkout-then-awaiting ordering, ended-event safety checkout, changed-end auto-checkout reschedule, transient missing-event warning/debug preservation, and missing-all-tracking reset in `tests/unit/test_checkin_decisions.py`
- [ ] T021 [US1] Add `checked_out` coordinator-decision tests for same follow-on no-op/write, changed follow-on recompute, removed follow-on recompute, no-active-timer recompute, and checked-out event-key exclusion in `tests/unit/test_checkin_decisions.py`
- [ ] T022 [US1] Add shell/applicator compatibility tests that exercise `_transition_to_awaiting`, `_transition_to_checked_in`, `_transition_to_checked_out`, `_transition_to_no_reservation`, `async_checkout`, `async_set_state`, and `async_handle_keymaster_unlock` in `tests/unit/test_checkin_sensor.py`

### Implementation for User Story 1

- [ ] T023 [US1] Implement `decide_coordinator_update`, `decide_no_reservation_update`, and `decide_awaiting_update` in `custom_components/rental_control/sensors/checkin/transition_decisions.py` using `CheckinStateSnapshot` and `CoordinatorUpdateContext` only
- [ ] T024 [US1] Implement `decide_checked_in_update` and `decide_checked_out_update` in `custom_components/rental_control/sensors/checkin/transition_decisions.py`, preserving self-healing effect ordering, log levels, and write intents
- [ ] T025 [US1] Implement coordinator-update effect application in `custom_components/rental_control/sensors/checkin/applicator.py` so HA bus events, service calls, timers, `async_write_ha_state()`, and mutation remain owned by the entity shell
- [ ] T026 [US1] Replace `_handle_coordinator_update` in `custom_components/rental_control/sensors/checkinsensor.py` with a wrapper below 80 lines that logs, handles failed coordinator updates, builds the context, calls `decide_coordinator_update`, applies ordered effects, and writes state only where the old path did
- [ ] T027 [US1] Preserve state, icon, `extra_state_attributes`, device metadata, service-facing behavior, debug override clearing, Keymaster-unlock-triggered check-in, and private transition-method compatibility in `custom_components/rental_control/sensors/checkinsensor.py`
- [ ] T028 [US1] Run US1 validation with `uv run pytest tests/unit/test_checkin_decisions.py tests/unit/test_checkin_sensor.py tests/integration/test_checkin_tracking.py -q` against the listed test files

**Checkpoint**: US1 proves FR-001, FR-002, FR-003, FR-004, FR-009, FR-010,
FR-011, FR-013, FR-014, SC-001, SC-002, SC-006, and SC-007 for coordinator
updates.

---

## Phase 4: User Story 2 - Preserve Restore Reconciliation (Priority: P1)

**Goal**: Restored state reconciles with current time and coordinator data
exactly as before, including silent catch-up behavior and the post-restore
coordinator-update second pass.

**Independent Test**: Replay restored-state scenarios for every state and
unknown/corrupted state, then verify corrected state, timer target, persisted
data, silent event behavior, and HA state writes match the current source.

### Tests for User Story 2

- [ ] T029 [US2] Add restored `checked_in` and `awaiting_checkin` decision tests for ended events, far-future self-healing, valid auto-checkout reschedule, missing end handling, silent automatic check-in, immediate silent checkout, monitoring-on deferral, valid auto-check-in reschedule, and missing start cleanup in `tests/unit/test_checkin_restore.py`
- [ ] T030 [US2] Add restored `checked_out`, `no_reservation`, and unknown-state decision tests for new-event handoff, expired linger reset, linger recompute, future FR-006c follow-up timer recreation, stale follow-up cleanup, and unknown-state reset in `tests/unit/test_checkin_restore.py`
- [ ] T031 [US2] Add restore shell parity tests proving no check-in or checkout bus events fire during silent restore catch-up and `async_added_to_hass()` still runs the non-silent coordinator-update second pass when coordinator data exists in `tests/unit/test_checkin_sensor.py`

### Implementation for User Story 2

- [ ] T032 [US2] Implement `decide_restore_state`, `decide_restore_checked_in`, and `decide_restore_awaiting` in `custom_components/rental_control/sensors/checkin/restore_decisions.py` with silent effects and source-equivalent time checks
- [ ] T033 [US2] Implement `decide_restore_checked_out`, `decide_restore_no_reservation`, and `decide_restore_unknown` in `custom_components/rental_control/sensors/checkin/restore_decisions.py`, preserving warning/reset and follow-up timer semantics
- [ ] T034 [US2] Implement restore-silent effect application in `custom_components/rental_control/sensors/checkin/applicator.py`, reusing ordered effects without firing HA check-in or checkout bus events during restore reconciliation
- [ ] T035 [US2] Replace `_validate_restored_state` in `custom_components/rental_control/sensors/checkinsensor.py` with a wrapper below 80 lines and preserve `async_added_to_hass()` restored-data loading plus current-data second-pass behavior
- [ ] T036 [US2] Run US2 validation with `uv run pytest tests/unit/test_checkin_restore.py tests/unit/test_checkin_sensor.py tests/integration/test_checkin_tracking.py -q` against the listed test files

**Checkpoint**: US2 proves FR-001, FR-002, FR-005, FR-007, FR-010, FR-013,
FR-014, SC-001, SC-002, SC-003, and SC-006 for restore reconciliation.

---

## Phase 5: User Story 3 - Preserve Timer Scheduling Semantics (Priority: P1)

**Goal**: Automatic transition timers keep the current single-active-handle,
cancel-before-replace, callback-clears-handle, and stale-callback guard behavior
after timer scheduling moves behind `CheckinTimerManager`.

**Independent Test**: Exercise auto-check-in, auto-checkout, same-day turnover,
different-day follow-on, cleaning-window linger, restored follow-up timers,
debug override cancellation, manual checkout cancellation, and entity-removal
cleanup while asserting the same target times and cancellation order.

### Tests for User Story 3

- [ ] T037 [US3] Add `CheckinTimerManager` unit tests for cancel-before-replace, one active handle, callback-entry handle clearing, stale callback no-op guards, and entity-removal cleanup in `tests/unit/test_checkin_timers.py`
- [ ] T038 [US3] Add timer target parity tests for auto-check-in, auto-checkout, same-day midpoint, different-day midnight, cleaning-window linger, and FR-006c follow-up timers in `tests/unit/test_checkin_timers.py`
- [ ] T039 [US3] Add or confirm integration parity tests for time-change-driven callbacks, duplicate-callback prevention, manual checkout cancellation, debug `async_set_state` timer clearing, and restored pending timers in `tests/integration/test_checkin_tracking.py`

### Implementation for User Story 3

- [ ] T040 [US3] Implement `CheckinTimerManager` in `custom_components/rental_control/sensors/checkin/timers.py` using `async_track_point_in_time()`, one unsubscribe handle, cancel-before-replace, callback handle clearing, and `ScheduledTransition` metadata
- [ ] T041 [US3] Move auto-check-in, auto-checkout, linger-to-awaiting, linger-to-no-reservation, and no-reservation-to-awaiting scheduling through `CheckinTimerManager` while preserving state guards in `custom_components/rental_control/sensors/checkinsensor.py`
- [ ] T042 [US3] Preserve transition target, follow-up day, linger follow-on key, linger baseline, `async_will_remove_from_hass`, `async_set_state`, manual checkout, and state-exit cancellation ordering in `custom_components/rental_control/sensors/checkinsensor.py`
- [ ] T043 [US3] Run US3 validation with `uv run pytest tests/unit/test_checkin_timers.py tests/unit/test_checkin_sensor.py tests/integration/test_checkin_tracking.py -q` against the listed test files

**Checkpoint**: US3 proves FR-001, FR-002, FR-006, FR-008, FR-010, FR-013,
FR-014, SC-001, SC-002, SC-004, SC-006, and SC-007 for timer behavior.

---

## Phase 6: User Story 4 - Improve Maintainability Under Aislop Limits (Priority: P2)

**Goal**: The decomposed check-in feature is reviewable under the active aislop
thresholds while retaining only intentional compatibility surfaces.

**Independent Test**: Measure the final changed source files and functions,
project-owned initializer signatures, focused unit coverage, and final diff
scope against the plan and issue #577.

### Tests for User Story 4

- [ ] T044 [US4] Add final import-boundary tests proving `CheckinTrackingSensor` and `CheckinExtraStoredData` remain importable from `custom_components/rental_control/sensors/checkinsensor.py` while helper internals are importable from `custom_components/rental_control/sensors/checkin/` in `tests/unit/test_checkin_sensor.py`
- [ ] T045 [US4] Add or confirm focused pure-helper tests can cover coordinator decisions, restore decisions, timer scheduling, and persistence without a full Home Assistant entity fixture in `tests/unit/test_checkin_decisions.py`, `tests/unit/test_checkin_restore.py`, `tests/unit/test_checkin_timers.py`, and `tests/unit/test_checkin_persistence.py`

### Implementation for User Story 4

- [ ] T046 [US4] Shrink `custom_components/rental_control/sensors/checkinsensor.py` to the HA entity shell responsibilities from PLAN, keeping lifecycle hooks, properties, service methods, event bus effects, state writes, coordinator subscription, private transition compatibility, and intentional compatibility re-exports only
- [ ] T047 [US4] Ensure every project-owned function in `custom_components/rental_control/sensors/checkinsensor.py` and `custom_components/rental_control/sensors/checkin/*.py` is below 80 lines and split any remaining oversized helper without changing behavior
- [ ] T048 [US4] Ensure `CheckinExtraStoredData.__init__` in `custom_components/rental_control/sensors/checkin/persistence.py` uses the snapshot-plus-legacy compatibility approach and every project-owned initializer in the check-in feature has no more than six explicit parameters
- [ ] T049 [US4] Remove temporary extraction shims from `custom_components/rental_control/sensors/checkinsensor.py` and `custom_components/rental_control/sensors/checkin/*.py` after tests pass, leaving only the planned compatibility re-exports and private transition-method compatibility required by existing tests
- [ ] T050 [US4] Confirm the final implementation diff is limited to `custom_components/rental_control/sensors/checkinsensor.py`, `custom_components/rental_control/sensors/checkin/`, `tests/unit/test_checkin_sensor.py`, `tests/unit/test_checkin_decisions.py`, `tests/unit/test_checkin_restore.py`, `tests/unit/test_checkin_timers.py`, `tests/unit/test_checkin_persistence.py`, and `tests/integration/test_checkin_tracking.py`
- [ ] T051 [US4] Run US4 focused validation with `uv run pytest tests/unit/test_checkin_decisions.py tests/unit/test_checkin_restore.py tests/unit/test_checkin_timers.py tests/unit/test_checkin_persistence.py -q` against the listed test files

**Checkpoint**: US4 proves FR-002, FR-004, FR-005, FR-006, FR-007, FR-012,
FR-013, FR-014, SC-003, SC-005, SC-006, and SC-007.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Verify behavior parity, quality gates, complexity thresholds,
traceability, and no unintended production behavior changes.

### Acceptance and Quality Gates

- [ ] T052 Run unchanged existing check-in parity tests with `uv run pytest tests/unit/test_checkin_sensor.py tests/integration/test_checkin_tracking.py -q` against the listed test files
- [ ] T053 Run unchanged call-site coverage with `uv run pytest tests/unit/test_keymaster_event_diagnostics.py tests/unit/test_sensors.py tests/integration/test_full_setup.py -q` against the listed test files
- [ ] T054 Run all new focused helper tests with `uv run pytest tests/unit/test_checkin_decisions.py tests/unit/test_checkin_restore.py tests/unit/test_checkin_timers.py tests/unit/test_checkin_persistence.py -q` against the listed test files
- [ ] T055 Verify every FR-001 through FR-014 has a test, implementation, or acceptance task mapped in `specs/014-decompose-checkinsensor/tasks.md`
- [ ] T056 Verify every SC-001 through SC-008 has a test, implementation, or acceptance task mapped in `specs/014-decompose-checkinsensor/tasks.md`
- [ ] T057 Confirm no new check-in states, HA events, configuration options, service contracts, event payload fields, timing rules, or unrelated Rental Control public APIs were introduced in `custom_components/rental_control/sensors/checkinsensor.py` and `custom_components/rental_control/sensors/checkin/*.py`
- [ ] T058 Stage the implementation files and run the staged aislop hook with `uv run pre-commit run aislop`; confirm `custom_components/rental_control/sensors/checkinsensor.py` is below 400 lines, all in-scope functions are below 80 lines, and project-owned initializers have no more than six explicit parameters
- [ ] T059 Run full regression tests with `uv run pytest tests/` against `tests/`
- [ ] T060 Run full regression tests with `uv run pytest tests/ -x -q` against `tests/` for the quickstart final validation command
- [ ] T061 Run linting with `uv run ruff check custom_components/ tests/` against `custom_components/` and `tests/`
- [ ] T062 Run full pre-commit validation with `uv run pre-commit run --all-files` against repository-tracked files, including reuse, yamllint, actionlint, aislop, ruff, ruff-format, mypy, and interrogate
- [ ] T063 Review `specs/014-decompose-checkinsensor/quickstart.md` and confirm the implementation PR notes list the existing parity commands, new focused test commands, hot-path safeguards, and final validation results

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup & Baseline (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup and blocks all user stories.
- **US1 Coordinator Updates (Phase 3)**: Depends on Foundational; MVP scope.
- **US2 Restore Reconciliation (Phase 4)**: Depends on Foundational and shares
  applicator/model infrastructure with US1.
- **US3 Timer Semantics (Phase 5)**: Depends on Foundational and must integrate
  with transition/restore effects before final acceptance.
- **US4 Maintainability (Phase 6)**: Depends on US1-US3 because final line
  counts, initializer counts, and shim removal are meaningful only after behavior
  extraction is complete.
- **Polish (Phase 7)**: Depends on all desired user-story phases.

### User Story Dependencies

- **US1 (P1)**: Can start after Foundational; validates normal coordinator-update
  behavior and is the MVP behavior gate.
- **US2 (P1)**: Can start after Foundational; should reuse effect models from US1
  but remains independently testable through restore fixtures.
- **US3 (P1)**: Can start after Foundational; final wiring should follow the
  effect vocabulary used by US1 and US2.
- **US4 (P2)**: Follows US1-US3 to verify the complete decomposition against
  aislop thresholds and remove temporary shims.

### Within Each Story

- New focused tests are written before the corresponding extraction tasks and
  should fail or prove missing coverage until the extraction lands.
- `models.py` precedes `persistence.py`, `transition_decisions.py`,
  `restore_decisions.py`, `timers.py`, and `applicator.py`.
- `event_selection.py` precedes coordinator and restore decisions that need event
  identity, relevant-event, tracked-event, or follow-on lookups.
- `applicator.py` grows coordinator effects before restore-silent effects because
  restore reuses the same ordered effect vocabulary with different bus-event
  behavior.
- `CheckinTimerManager` may be developed after models but must be wired before
  final transition/restore validation.
- Temporary compatibility shims are removed only after all existing and focused
  tests pass, leaving intentional re-exports intact.

---

## Parallel Opportunities

- T009 and T011 can run in parallel because they create different focused test
  files.
- T014 and T016 can run in parallel after T013 because persistence and event
  selection touch different modules.
- US1 decision tests can be split by state group if contributors coordinate
  sequential writes to `tests/unit/test_checkin_decisions.py`.
- US2 restore tests and US3 timer tests can proceed in parallel after
  Foundational because they touch different test and source modules.
- T052, T053, and T054 can run independently once implementation is complete;
  T059 through T062 are final serial quality gates.

## Parallel Example: US2 and US3 After Foundational

```bash
Task: "Add restore decision tests in tests/unit/test_checkin_restore.py"
Task: "Implement CheckinTimerManager in custom_components/rental_control/sensors/checkin/timers.py"
Task: "Add timer target parity tests in tests/unit/test_checkin_timers.py"
```

---

## Implementation Strategy

### MVP First (US1 Only)

1. Complete Phase 1 and Phase 2.
2. Complete US1 focused tests and coordinator-update extraction.
3. Validate US1 with `tests/unit/test_checkin_decisions.py`,
   `tests/unit/test_checkin_sensor.py`, and
   `tests/integration/test_checkin_tracking.py`.
4. Stop and review behavior parity before continuing to restore and timer
   extraction.

### Incremental Delivery

1. Build snapshots, persistence compatibility, and event-selection helpers beside
   the current entity path.
2. Extract coordinator-update decisions and apply effects from the shell.
3. Extract restore reconciliation as silent decisions and preserve the
   post-restore coordinator second pass.
4. Extract the timer manager while preserving the single-handle semantics.
5. Reduce the shell to HA-facing responsibilities, remove temporary shims, and
   run aislop threshold checks.
6. Run targeted parity tests, focused helper tests, full tests, ruff, and
   pre-commit.

---

## Acceptance Coverage Map

| Coverage item | Primary tasks |
|---------------|---------------|
| US1 coordinator behavior parity | T019-T028, T052 |
| US2 restore reconciliation parity | T029-T036, T052 |
| US3 timer scheduling parity | T037-T043, T052 |
| US4 aislop maintainability | T044-T051, T058 |
| FR-001 observable behavior unchanged | T007, T022, T025-T028, T031, T036, T039, T043, T052, T057 |
| FR-002 existing tests unchanged | T006, T007, T018, T028, T036, T043, T052 |
| FR-003 four-state machine preserved | T019-T028, T052 |
| FR-004 coordinator decisions separable | T011, T019-T026, T045, T054 |
| FR-005 restore decisions separable | T029-T036, T045, T054 |
| FR-006 timer decisions separable | T037-T043, T045, T054 |
| FR-007 persistence backward compatibility | T009, T010, T014, T015, T029-T036, T054 |
| FR-008 timer cancellation semantics | T037-T043, T052, T054 |
| FR-009 hot-path safeguards | T023-T026, T057, T063 |
| FR-010 HA entity boundary | T015, T017, T025-T027, T034-T035, T041-T042, T046 |
| FR-011 no unrelated public API change | T050, T057 |
| FR-012 aislop thresholds | T008, T047-T049, T058 |
| FR-013 regression coverage | T019-T022, T029-T031, T037-T039, T052-T054 |
| FR-014 behavior-preserving notes | T057, T063 |
| SC-001 existing tests green | T052, T059, T060 |
| SC-002 scenario parity | T028, T036, T043, T052 |
| SC-003 persisted fixtures restore | T009, T010, T014, T015, T029-T036, T054 |
| SC-004 single active timer | T037-T043, T054 |
| SC-005 file/function/init limits | T047-T049, T058 |
| SC-006 focused helper coverage | T045, T054 |
| SC-007 no added hot-path work | T023-T026, T057, T063 |
| SC-008 docs-only spec stage preserved | T050, T057 |
