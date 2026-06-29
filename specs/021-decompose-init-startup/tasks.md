<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Tasks: Decompose Init Startup Readability

**Input**: Design documents from `/specs/021-decompose-init-startup/`
**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅,
quickstart.md ✅

**Tests**: Included and required. This feature is a behavior-preserving
refactor of `custom_components/rental_control/__init__.py`, so unchanged
`tests/unit/test_init.py` is the primary behavior oracle. Focused helper tests
may be added only to pin extracted startup-readability behavior, import seams,
watcher cleanup, and complexity gates without changing runtime semantics.

**Organization**: Tasks follow the implementation order from PLAN and the
feature brief: baseline parity first, startup-readability module extraction,
private watcher object decomposition, public arming function reduction,
`__init__.py` shell re-export wiring, import and patch-seam verification,
file-size and Aislop gates, and final pytest/ruff/pre-commit acceptance. All
checkboxes remain unchecked until the implementation PR performs the work.

## Format: `- [ ] T### [P?] [Story?] Description with file path`

- **[P]**: Can run in parallel (different files, no dependency on incomplete
  tasks)
- **[Story]**: Which user story the task primarily proves (US1 through US4)
- Include exact file paths in descriptions
- Leave every checkbox unchecked until the implementation PR performs the task

## Path Conventions

- **Entry shell**: `custom_components/rental_control/__init__.py`
- **Extracted startup-readability module**:
  `custom_components/rental_control/startup_readability.py`
- **Existing behavior oracle**: `tests/unit/test_init.py`
- **Optional focused helper tests**: `tests/unit/test_startup_readability.py`
- **Integration caller smoke tests**:
  `tests/integration/test_full_setup.py` and
  `tests/integration/test_refresh_cycle.py`
- **Required package compatibility surface**: `async_setup_entry`,
  `async_unload_entry`, `update_listener`, `async_start_listener`,
  `async_migrate_entry`, `async_register_keymaster_listener`, and
  `async_arm_startup_readability_refresh`
- **Patch-sensitive package attribute**: `async_start_listener` must remain
  patchable at `custom_components.rental_control.async_start_listener`
- **Feature docs**: `specs/021-decompose-init-startup/`

## Live Module Transition Scope

Implementation changes only the startup-readability decomposition scope. The
target split from PLAN is:

- `custom_components/rental_control/__init__.py` — Home Assistant package entry
  shell for `async_setup_entry`, `async_unload_entry`, `update_listener`,
  `async_start_listener`, existing #572 re-exports, and startup-readability
  imports/re-exports.
- `custom_components/rental_control/startup_readability.py` — startup
  readability constants, managed slot entity discovery, readable-state checks,
  startup unreadability decision, private watcher object, debounce/watchdog
  timers, one-shot refresh task, and cleanup ownership.
- `tests/unit/test_init.py` — existing setup, unload, reload, update-listener,
  migration re-export, direct arming-call, and package patch-seam oracle.
- `tests/unit/test_startup_readability.py` — optional focused parity tests for
  helper and watcher behavior when the implementation needs tighter coverage.

No implementation task may add `aislop-ignore`, `aislop-ignore-file`, or an
equivalent complexity suppression for file size, function length, or parameter
count. There is no existing Aislop directive to retain for this scope.

---

## Phase 1: Setup & Baseline (Shared Infrastructure)

**Purpose**: Establish behavior, source, compatibility, and complexity baselines
before moving any production code.

- [x] T001 Run `.specify/scripts/bash/check-prerequisites.sh --json` with `SPECIFY_FEATURE=021-decompose-init-startup` from the repository root and confirm `specs/021-decompose-init-startup/` reports `research.md`, `data-model.md`, and `quickstart.md`
- [x] T002 Inspect US1-US4, FR-001 through FR-016, edge cases, assumptions, non-goals, security considerations, and SC-001 through SC-008 in `specs/021-decompose-init-startup/spec.md`
- [x] T003 Inspect the Project Structure, Concrete Decomposition Design, public compatibility boundary, watcher object decomposition, one-shot cleanup invariants, compatibility wiring, and Aislop handling in `specs/021-decompose-init-startup/plan.md`
- [x] T004 Inspect all research decisions, all data-model entities, and quickstart parity steps in `specs/021-decompose-init-startup/research.md`, `specs/021-decompose-init-startup/data-model.md`, and `specs/021-decompose-init-startup/quickstart.md`
- [x] T005 Inventory `async_setup_entry`, `async_unload_entry`, `update_listener`, `_managed_slot_readability_entity_ids`, `_is_readable_keymaster_state`, `_all_managed_slots_readable`, `_needs_startup_readability_refresh`, `async_arm_startup_readability_refresh`, and `async_start_listener` in `custom_components/rental_control/__init__.py`
- [x] T006 Inventory visible imports, direct `async_arm_startup_readability_refresh` calls, `custom_components.rental_control.async_start_listener` patches, update-listener tests, and #572 migration/listener re-export tests in `tests/unit/test_init.py`
- [x] T007 Run unchanged baseline init parity tests with `uv run pytest tests/unit/test_init.py -q` against `tests/unit/test_init.py`
- [x] T008 Run unchanged integration caller smoke tests with `uv run pytest tests/integration/test_full_setup.py tests/integration/test_refresh_cycle.py -q` against the listed integration files
- [x] T009 Record the current line, function-length, parameter-count, import, patch-seam, and directive baseline for `custom_components/rental_control/__init__.py`, confirming the 449-line file, the 143-line `async_arm_startup_readability_refresh`, four module-level readability helpers, four arming parameters, and no `aislop-ignore` directive

---

## Phase 2: Startup-Readability Helper Extraction (Priority: P1) 🎯 MVP

**Goal**: Move managed slot discovery and readability decision helpers into a
focused module while keeping current setup ordering and behavior unchanged.

**Independent Test**: Helper tests and unchanged `tests/unit/test_init.py` prove
that lockless entries, managed entity IDs, missing states, unavailable states,
`unknown` states, and startup unreadability decisions match the current source.

### Tests for Helper Extraction

- [x] T010 [P] [US1] Add helper parity tests for no lock name, managed text/switch entity IDs, configured slot ranges, missing state, `STATE_UNAVAILABLE`, `STATE_UNKNOWN`, and normal readable states in `tests/unit/test_startup_readability.py`
- [x] T011 [P] [US1] Add startup unreadability decision tests for all-readable, partially unreadable, no-entity, and lockless cases in `tests/unit/test_startup_readability.py`
- [x] T012 [US2] Add setup-order regression coverage or assertions proving startup unreadability is captured before first refresh and arming runs after first refresh in `tests/unit/test_init.py`

### Implementation for Helper Extraction

- [x] T013 [US4] Create `custom_components/rental_control/startup_readability.py` with project SPDX headers, a module docstring, type hints, Home Assistant imports, project constants, and no import from `custom_components.rental_control.__init__`
- [x] T014 [US1] Move `_STARTUP_READABILITY_REFRESH_DELAY` and `_STARTUP_READABILITY_WATCHDOG` unchanged into `custom_components/rental_control/startup_readability.py`
- [x] T015 [US1] Move `_managed_slot_readability_entity_ids` unchanged into `custom_components/rental_control/startup_readability.py`, preserving no-lock behavior and exact `text.<lock>_code_slot_<slot>_{name,pin}` and `switch.<lock>_code_slot_<slot>_enabled` entity IDs
- [x] T016 [US1] Move `_is_readable_keymaster_state` unchanged into `custom_components/rental_control/startup_readability.py`, preserving `None` and `STATE_UNAVAILABLE` as unreadable and `STATE_UNKNOWN` as readable
- [x] T017 [US1] Move `_all_managed_slots_readable` unchanged into `custom_components/rental_control/startup_readability.py`, preserving Home Assistant state lookup semantics for every watched entity ID
- [x] T018 [US2] Move `_needs_startup_readability_refresh` into `custom_components/rental_control/startup_readability.py` and preserve the `(needs_refresh, entity_ids)` tuple consumed by `async_setup_entry`
- [x] T019 [US4] Remove startup-readability helper bodies and unused readability imports from `custom_components/rental_control/__init__.py` only after `startup_readability.py` owns the equivalent helpers
- [x] T020 [US1] Run helper parity checks with `uv run pytest tests/unit/test_startup_readability.py tests/unit/test_init.py::test_healthy_startup_does_not_arm_watcher -q` against the listed test files

**Checkpoint**: Helper extraction proves FR-001, FR-002, FR-004, FR-005,
FR-016, SC-001, SC-002, and SC-007 while the arming behavior is still
behavior-compatible.

---

## Phase 3: Watcher Object and Arming Decomposition (Priority: P1)

**Goal**: Replace nested callback state with a private watcher object, preserve
one-shot/debounce/watchdog/cancellation semantics, and reduce the public arming
function below 80 lines.

**Independent Test**: Existing startup watcher tests and focused lifecycle tests
prove missed transitions, readable transitions, debounce replacement, unload
cleanup, watchdog expiry, missing entry data, refresh errors, and task cleanup
match the current implementation.

### Tests for Watcher Lifecycle

- [x] T021 [US1] Add focused direct-call tests for `startup_slots_unreadable=True` scheduling the delayed one-shot refresh when watched entities are already readable in `tests/unit/test_startup_readability.py`
- [x] T022 [US1] Add readable-transition tests proving unreadable-to-readable changes schedule debounce, readable-to-readable changes do not reschedule, and rapid readable storms collapse to one refresh in `tests/unit/test_startup_readability.py`
- [x] T023 [US1] Add cleanup tests proving unload cancels state tracking, debounce timer, watchdog timer, pending refresh task, and cleanup references in `tests/unit/test_startup_readability.py`
- [x] T024 [US1] Add watchdog expiry tests proving expiration logs non-fatally and removes the startup watcher from `UNSUB_LISTENERS` in `tests/unit/test_startup_readability.py`
- [x] T025 [US1] Add refresh safety tests proving missing entry data skips refresh and coordinator refresh exceptions are logged without propagation in `tests/unit/test_startup_readability.py`

### Implementation for Watcher Lifecycle

- [x] T026 [US1] Add private `_StartupReadabilityWatcher` or equivalent lifecycle owner in `custom_components/rental_control/startup_readability.py` with fields for `hass`, `config_entry`, `coordinator`, `entity_ids`, `done`, `unsub_state`, `unsub_timer`, `unsub_watchdog`, and `refresh_task`
- [x] T027 [US1] Implement watcher `arm()` in `custom_components/rental_control/startup_readability.py`, subscribing state changes, starting the watchdog, appending `remove_self` to `UNSUB_LISTENERS`, scheduling initial debounce when already readable, and logging the same armed message
- [x] T028 [US1] Implement watcher cleanup methods in `custom_components/rental_control/startup_readability.py`, preserving listener-reference removal, debounce/watchdog/state cancellation order, unload self-removal, pending refresh-task cancellation, and safe missing-entry handling
- [x] T029 [US1] Implement watcher refresh methods in `custom_components/rental_control/startup_readability.py`, preserving `async_refresh_once`, `refresh_if_readable`, task name `rental_control startup readability refresh <entry_id>`, done-callback cleanup, missing-entry skip, and logged refresh exceptions
- [x] T030 [US1] Implement watcher transition and expiry methods in `custom_components/rental_control/startup_readability.py`, preserving readable new-state filtering, readable old-state storm filtering, debounce replacement, all-entity recheck, and watchdog expiration cleanup
- [x] T031 [US4] Reduce `async_arm_startup_readability_refresh` in `custom_components/rental_control/startup_readability.py` to a thin `@callback` orchestrator below 80 lines that computes need, returns on no-op, instantiates the watcher, and calls `arm()`
- [x] T032 [US1] Remove the nested `_remove_listener_reference`, `_cancel_watchers`, `_remove_self`, `_refresh_done`, `_async_refresh_once`, `_refresh_if_readable`, `_schedule_refresh`, and `_expire` closures from the arming implementation in `custom_components/rental_control/startup_readability.py`
- [x] T033 [US1] Run watcher lifecycle checks with `uv run pytest tests/unit/test_startup_readability.py tests/unit/test_init.py::test_startup_readability_watcher_unloads_cleanly tests/unit/test_init.py::test_startup_readability_watcher_handles_missed_transition -q` against the listed test files

**Checkpoint**: Watcher decomposition proves FR-006 through FR-010, FR-014,
FR-015, SC-001, SC-002, SC-005, SC-006, and SC-007 without changing startup
refresh behavior.

---

## Phase 4: Entry Shell Re-Exports and Lifecycle Contract (Priority: P1)

**Goal**: Keep `__init__.py` as the Home Assistant shell, preserve setup/unload
/update-listener order, and re-export startup readability with the existing #572
migration and keymaster-listener package names intact.

**Independent Test**: Existing init tests and integration smoke tests prove setup,
unload, reload, update-listener, package imports, and listener restart behavior
are unchanged.

### Tests for Entry Shell Contract

- [x] T034 [US2] Add or preserve tests proving `async_setup_entry` stores the coordinator, performs first refresh, arms startup readability, starts normal listeners, forwards platforms, registers the keymaster listener, adds the update listener, and cleans generated files in `tests/unit/test_init.py`
- [x] T035 [US2] Add or preserve tests proving `async_unload_entry` unloads platforms, deletes generated files, reloads package platforms, calls and clears `UNSUB_LISTENERS`, removes domain data, dismisses notification, and returns the same result in `tests/unit/test_init.py`
- [x] T036 [US2] Add or preserve tests proving `update_listener` handles present data, missing entry data before mutation, missing domain data, and entry disappearance after coordinator update in `tests/unit/test_init.py`

### Implementation for Entry Shell Contract

- [x] T037 [US2] Import `_needs_startup_readability_refresh` from `custom_components/rental_control/startup_readability.py` into `custom_components/rental_control/__init__.py` for the existing pre-first-refresh startup unreadability capture
- [x] T038 [US3] Import and re-export `async_arm_startup_readability_refresh` from `custom_components/rental_control/startup_readability.py` in `custom_components/rental_control/__init__.py` so `from custom_components.rental_control import async_arm_startup_readability_refresh` remains valid
- [x] T039 [US2] Keep `async_setup_entry` in `custom_components/rental_control/__init__.py` calling the package-level `async_arm_startup_readability_refresh` after first refresh and before `async_start_listener`, using the captured startup unreadability value
- [x] T040 [US2] Keep `async_unload_entry` unchanged in `custom_components/rental_control/__init__.py` except for imports affected by the startup-readability move
- [x] T041 [US3] Keep `update_listener` in `custom_components/rental_control/__init__.py` resolving the package-level `async_start_listener` at runtime so `custom_components.rental_control.async_start_listener` patches remain effective
- [x] T042 [US3] Keep `async_start_listener` defined in `custom_components/rental_control/__init__.py` and do not move normal Keymaster state-change tracking into `custom_components/rental_control/startup_readability.py`
- [x] T043 [US3] Preserve #572 package re-exports for `async_migrate_entry` and `async_register_keymaster_listener` in `custom_components/rental_control/__init__.py`
- [x] T044 [US2] Run entry shell checks with `uv run pytest tests/unit/test_init.py -q` against `tests/unit/test_init.py`

**Checkpoint**: Entry shell wiring proves FR-001 through FR-004, FR-011,
FR-012, FR-013, SC-001, SC-003, and SC-004 with Home Assistant entry behavior
unchanged.

---

## Phase 5: Import and Patch-Seam Verification (Priority: P1)

**Goal**: Verify visible and hidden compatibility surfaces after extraction,
including direct package imports and package-path monkeypatches.

**Independent Test**: Import smoke checks, direct arming calls, and
`update_listener` patch tests prove callers do not need to import helper modules.

### Tests for Compatibility Seams

- [x] T045 [US3] Add package import-surface tests proving `async_setup_entry`, `async_unload_entry`, `update_listener`, `async_start_listener`, `async_migrate_entry`, `async_register_keymaster_listener`, and `async_arm_startup_readability_refresh` remain importable from `custom_components.rental_control` in `tests/unit/test_init.py`
- [x] T046 [US3] Add direct-call compatibility tests proving package-imported `async_arm_startup_readability_refresh` accepts `hass`, `config_entry`, `coordinator`, and keyword-only `startup_slots_unreadable` with current behavior in `tests/unit/test_init.py`
- [x] T047 [US3] Add patch-seam tests proving patches to `custom_components.rental_control.async_start_listener` still affect `update_listener` listener restart behavior in `tests/unit/test_init.py`
- [x] T048 [US3] Add #572 compatibility tests or assertions proving package imports of `async_migrate_entry` and `async_register_keymaster_listener` remain available after startup-readability extraction in `tests/unit/test_init.py`

### Implementation Verification

- [x] T049 [US3] Verify no production caller in `custom_components/rental_control/**/*.py` imports `async_arm_startup_readability_refresh` through a helper-only path when the package path should remain the public compatibility surface
- [x] T050 [US3] Verify `custom_components/rental_control/startup_readability.py` imports Home Assistant event helpers, project constants, coordinator types, and `get_entry_data` directly without importing from `custom_components/rental_control/__init__.py`
- [x] T051 [US3] Verify `custom_components/rental_control/__init__.py` does not cache or wrap `async_start_listener` in a helper alias that would bypass package-path patches during `update_listener`
- [x] T052 [US3] Run import and patch-seam validation with `uv run pytest tests/unit/test_init.py tests/integration/test_full_setup.py tests/integration/test_refresh_cycle.py -q` against the listed files

**Checkpoint**: Compatibility verification proves FR-003, FR-011 through FR-013,
SC-003, and SC-004 before maintainability cleanup.

---

## Phase 6: Maintainability, File Sizes, and Aislop Gates (Priority: P2)

**Goal**: Resolve the remaining complexity findings without suppressions,
behavior changes, or catch-all modules.

**Independent Test**: Measure entry and startup-readability files after final
wiring and run existing complexity tooling with no Aislop directive added.

### Cleanup and Complexity Gates

- [x] T053 [US4] Confirm final implementation diff is limited to `custom_components/rental_control/__init__.py`, `custom_components/rental_control/startup_readability.py`, and directly required startup-readability tests under `tests/unit/` or `tests/integration/`
- [x] T054 [US4] Confirm no new configuration options, services, entities, diagnostics fields, storage schema, listener semantics, startup refresh semantics, lock-code business rules, Home Assistant state writes, config-entry writes, Keymaster service calls, blocking I/O, async tasks beyond the existing one-shot task, or user-visible delays were introduced in startup-readability files
- [x] T055 [US4] Measure `custom_components/rental_control/__init__.py` and `custom_components/rental_control/startup_readability.py` with `wc -l` and confirm both files are below 400 lines
- [x] T056 [US4] Ensure every project-owned function in `custom_components/rental_control/__init__.py` and `custom_components/rental_control/startup_readability.py` is below 80 lines, including `async_arm_startup_readability_refresh` and every watcher method
- [x] T057 [US4] Ensure every project-owned parameter list in `custom_components/rental_control/__init__.py` and `custom_components/rental_control/startup_readability.py` has no more than six parameters unless an external Home Assistant framework signature requires otherwise
- [x] T058 [US4] Verify no `aislop-ignore`, `aislop-ignore-file`, or equivalent complexity suppression exists in `custom_components/rental_control/__init__.py` or `custom_components/rental_control/startup_readability.py`
- [x] T059 [US4] Run isolated complexity validation with `uv run pre-commit run aislop --all-files` and confirm file-size, function-length, and parameter-count thresholds pass for the init startup-readability decomposition

**Checkpoint**: Maintainability proves FR-014, FR-015, SC-005, and SC-006 with
findings resolved rather than suppressed.

---

## Phase 7: Polish & Cross-Cutting Acceptance Gates

**Purpose**: Verify behavior parity, caller compatibility, quality gates,
traceability, and implementation notes before the runtime refactor is complete.

### Acceptance and Quality Gates

- [x] T060 Run unchanged init parity tests with `uv run pytest tests/unit/test_init.py -x -q` against `tests/unit/test_init.py`
- [x] T061 Run focused helper tests with `uv run pytest tests/unit/test_startup_readability.py -q` against `tests/unit/test_startup_readability.py` if the implementation added that file
- [x] T062 Run integration caller parity tests with `uv run pytest tests/integration/test_full_setup.py tests/integration/test_refresh_cycle.py -x -q` against the listed integration files
- [x] T063 Run full regression tests with `uv run pytest tests/ -x -q` against `tests/`
- [x] T064 Run linting with `uv run ruff check custom_components/ tests/` against `custom_components/` and `tests/`
- [x] T065 Run full pre-commit validation with `uv run pre-commit run --all-files` against repository-tracked files, including reuse, yamllint, actionlint, aislop, ruff, ruff-format, mypy, interrogate, and gitlint hooks
- [x] T066 Verify every FR-001 through FR-016 has a test, implementation, or acceptance task mapped in `specs/021-decompose-init-startup/tasks.md`
- [x] T067 Verify every SC-001 through SC-008 has a test, implementation, or acceptance task mapped in `specs/021-decompose-init-startup/tasks.md`
- [x] T068 Review `specs/021-decompose-init-startup/quickstart.md` and confirm the implementation PR notes list unchanged parity commands, focused helper commands if added, import and patch-seam results, file-size and function-length measurements, final `aislop` results, full `pytest tests/ -x -q`, ruff, and pre-commit results

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup & Baseline (Phase 1)**: No dependencies.
- **Startup-Readability Helper Extraction (Phase 2)**: Depends on baseline source
  and test inventories so helper behavior reflects live `origin/main`.
- **Watcher Object and Arming Decomposition (Phase 3)**: Depends on helper
  extraction because the watcher uses the extracted readiness helpers and module
  constants.
- **Entry Shell Re-Exports and Lifecycle Contract (Phase 4)**: Depends on the
  startup-readability module exposing the public arming function and private
  needs helper.
- **Import and Patch-Seam Verification (Phase 5)**: Depends on shell re-export
  wiring and unchanged package-level `async_start_listener` ownership.
- **Maintainability (Phase 6)**: Depends on all extraction, watcher,
  compatibility, and cleanup work being complete.
- **Polish (Phase 7)**: Depends on all desired extraction, wrapper,
  compatibility, and maintainability phases.

### User Story Dependencies

- **US1 (P1)**: Startup readability parity starts with baselines and drives
  helper extraction, watcher lifecycle tests, direct arming calls, and final
  parity gates.
- **US2 (P1)**: Entry contract parity starts with baselines and is proven by
  setup, unload, update-listener, shell wiring, and integration smoke gates.
- **US3 (P1)**: Compatibility surface depends on shell wiring and completes with
  package import, direct-call, #572 re-export, and `async_start_listener` patch
  tests.
- **US4 (P2)**: Maintainability follows helper extraction and shell wiring because
  file/function/parameter thresholds are meaningful only after the split.

### Within Each Story

- Existing `tests/unit/test_init.py` remains the oracle and must pass before and
  after implementation.
- Focused helper tests, if added, are written before the matching helper or
  watcher implementation tasks and should fail or expose missing coverage until
  extraction lands.
- `startup_readability.py` imports from Home Assistant helpers, constants,
  coordinator types, and `util.py`, never from package `__init__.py`.
- `__init__.py` keeps `async_start_listener`, `update_listener`,
  `async_setup_entry`, and `async_unload_entry` in the package shell.
- `async_setup_entry` captures startup unreadability before first refresh and
  calls the package-level arming function after first refresh.
- File-size measurement and `uv run pre-commit run aislop --all-files` happen
  after temporary shims are removed and before final full gates.
- No Aislop directive may be added at any point.

---

## Parallel Opportunities

- T010 and T011 can be developed in parallel after baselines because helper ID
  discovery and readability-decision tests cover separate helper behaviors.
- T021 through T025 can be drafted in parallel once helper extraction exists, but
  final assertions should be reconciled in one working copy because they may edit
  `tests/unit/test_startup_readability.py`.
- T013 through T018 are same-file implementation tasks and should be sequenced in
  one working copy to avoid conflicting edits to `startup_readability.py`.
- T034 through T036 can be reviewed in parallel because setup, unload, and
  update-listener tests cover different sections of `tests/unit/test_init.py`.
- T045 through T048 can be drafted in parallel as compatibility assertions, then
  reconciled before running T052.
- T060 through T062 can run independently once implementation is complete; T063
  through T065 are final serial quality gates.

## Parallel Example: Helper Parity After Baseline

```bash
Task: "Add managed entity ID helper parity tests in tests/unit/test_startup_readability.py"
Task: "Add startup unreadability decision tests in tests/unit/test_startup_readability.py"
Task: "Review setup-order assertions in tests/unit/test_init.py"
```

---

## Implementation Strategy

### MVP First (Behavior Parity and Safety)

1. Complete Phase 1 baselines against live `origin/main` source and tests.
2. Add helper parity tests and extract startup-readability constants and helpers.
3. Introduce the private watcher object while preserving exact nested-closure
   semantics.
4. Reduce `async_arm_startup_readability_refresh` below 80 lines as a thin
   orchestrator.
5. Wire `__init__.py` to import `_needs_startup_readability_refresh` and re-export
   `async_arm_startup_readability_refresh` without moving `async_start_listener`.
6. Verify package imports, direct arming calls, #572 re-exports, and package-path
   patches before claiming maintainability fixes.

### Incremental Delivery

1. Build `startup_readability.py` with helper functions and tests while keeping
   behavior unchanged.
2. Add `_StartupReadabilityWatcher` and migrate each nested callback
   responsibility to a focused method.
3. Slim `__init__.py` into the Home Assistant entry shell with package-level
   compatibility imports and no startup-readability helper bodies.
4. Remove temporary shims, measure files/functions/parameters below thresholds,
   run `aislop` with no suppression, and then run full pytest, ruff, and
   pre-commit gates.

---

## Acceptance Coverage Map

| Coverage item | Primary tasks |
|---------------|---------------|
| US1 Preserve startup readability refresh | T005-T011, T014-T018, T020-T033, T044, T052, T060-T065 |
| US2 Preserve integration entry contract | T005-T009, T012, T034-T044, T052, T060-T065 |
| US3 Preserve test and patch surfaces | T006, T038, T041-T043, T045-T052, T060-T068 |
| US4 Resolve Aislop complexity findings | T009, T013, T019, T031-T032, T053-T059, T063-T068 |
| FR-001 observable behavior unchanged | T007-T008, T020, T033-T044, T052, T060-T065 |
| FR-002 existing tests and parity tests | T007-T012, T020-T025, T033-T036, T044, T052, T060-T062 |
| FR-003 package arming import retained | T006, T038, T045-T046, T052, T060 |
| FR-004 setup arming order retained | T012, T037-T039, T044, T060 |
| FR-005 readability decision equivalent | T010-T018, T020, T060-T061 |
| FR-006 watcher lifecycle equivalent | T021-T030, T033, T060-T061 |
| FR-007 one-shot refresh guarantee | T021-T023, T027-T033, T060-T061 |
| FR-008 missed-transition behavior | T021, T029-T033, T046, T060-T061 |
| FR-009 watchdog expiry equivalent | T024, T027-T030, T033, T060-T061 |
| FR-010 refresh error handling equivalent | T025, T028-T030, T033, T060-T061 |
| FR-011 `async_start_listener` patch seam | T006, T036, T041-T042, T047, T051-T052, T060 |
| FR-012 package HA contract unchanged | T034-T048, T052, T060-T062 |
| FR-013 #572 re-exports intact | T006, T043, T048, T052, T060 |
| FR-014 complexity thresholds | T009, T031, T053-T059, T065 |
| FR-015 no Aislop suppression | T009, T058-T059, T065 |
| FR-016 behavior-preserving scope docs | T002-T004, T054, T066-T068 |
| SC-001 existing init tests green | T007, T020, T033, T044, T052, T060 |
| SC-002 startup watcher parity | T010-T033, T060-T061 |
| SC-003 HA lifecycle contract unchanged | T034-T044, T052, T060-T062 |
| SC-004 visible import and patch seams | T006, T038, T041-T048, T052, T060 |
| SC-005 file/function/parameter limits | T009, T031, T053-T059, T065 |
| SC-006 no complexity directive | T009, T058-T059, T065 |
| SC-007 no added hot-path work | T054, T060-T065 |
| SC-008 docs-only tasks stage | This `tasks.md` PR only; implementation tasks start unchecked |
