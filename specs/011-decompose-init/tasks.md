<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Tasks: Decompose Integration Entry Module

**Input**: Design documents from `/specs/011-decompose-init/`
**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, quickstart.md ✅

**Tests**: Included — this is a pure refactor. The existing test suite is the
behavioral baseline and acceptance gate. Test changes are limited to confirming
package-level import stability for moved public entry points.

**Organization**: Tasks are grouped by setup, foundational decomposition work,
and user stories. The migration extraction and listener extraction are separate
atomic work streams, with behavior preserved by existing tests.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3, US4)
- Include exact file paths in descriptions

## Path Conventions

- **Source**: `custom_components/rental_control/` at repository root
- **Tests**: `tests/unit/`, `tests/integration/` at repository root
- **Spec**: `specs/011-decompose-init/` at repository root

---

## Phase 1: Setup & Baseline (Shared Infrastructure)

**Purpose**: Establish the issue #572 baseline before changing tests or code

- [X] T001 Run `.specify/scripts/bash/check-prerequisites.sh --json` with `SPECIFY_FEATURE=011-decompose-init` from the repository root and confirm `specs/011-decompose-init/` plus `research.md` and `quickstart.md` are reported
- [X] T002 Inventory the live migration, listener, and package-level import symbols in `custom_components/rental_control/__init__.py`, `tests/unit/test_init.py`, `tests/unit/test_keymaster_event_diagnostics.py`, and `tests/unit/test_checkin_sensor.py`
- [X] T003 Run the full unchanged baseline test suite with `uv run pytest tests/` against `tests/`
- [X] T004 Confirm the planned refactor scope is limited to `custom_components/rental_control/__init__.py`, `custom_components/rental_control/migrations.py`, `custom_components/rental_control/listeners.py`, and import-stability coverage in `tests/unit/test_init.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Create the new module boundaries, move the in-scope public entry
points, preserve package-level re-exports, and avoid circular imports.

**⚠️ CRITICAL**: No user story validation can complete until this phase is done.

- [X] T005 [P] Add `custom_components/rental_control/migrations.py` with SPDX headers, a module docstring, type hints, local `_LOGGER = logging.getLogger(__name__)`, and direct imports from Home Assistant and `custom_components/rental_control/const.py`
- [X] T006 [P] Add `custom_components/rental_control/listeners.py` with SPDX headers, a module docstring, type hints, local `_LOGGER = logging.getLogger(__name__)`, and direct imports from Home Assistant, `custom_components/rental_control/const.py`, and `custom_components/rental_control/util.py`
- [X] T007 Move `async_migrate_entry` from `custom_components/rental_control/__init__.py` into `custom_components/rental_control/migrations.py` without changing supported-version range, defaults, removals, update-entry calls, logging, or return values
- [X] T008 Split the moved migration flow in `custom_components/rental_control/migrations.py` into private per-version helpers for v3→v4, v4→v5, v5→v6, v6→v7, v7→v8, v8→v9, and v9→v10 while keeping each function at or below 80 lines
- [X] T009 Re-export `async_migrate_entry` from `custom_components/rental_control/__init__.py` via `from .migrations import async_migrate_entry` and remove migration-only imports from `custom_components/rental_control/__init__.py`
- [X] T010 Move `async_register_keymaster_listener` from `custom_components/rental_control/__init__.py` into `custom_components/rental_control/listeners.py` without changing event-bus registration, unsubscribe tracking, monitored-lock checks, diagnostics, or forwarding behavior
- [X] T011 Split the moved listener flow in `custom_components/rental_control/listeners.py` into private helpers for `_handle_keymaster_event`, diagnostic recording, check-in sensor refresh, monitoring checks, and unlock forwarding while keeping each function at or below 80 lines
- [X] T012 Re-export `async_register_keymaster_listener` from `custom_components/rental_control/__init__.py` via `from .listeners import async_register_keymaster_listener` and remove listener-only imports from `custom_components/rental_control/__init__.py`
- [X] T013 Confirm `custom_components/rental_control/migrations.py` and `custom_components/rental_control/listeners.py` import no symbols from `custom_components/rental_control/__init__.py` or `custom_components.rental_control`

**Checkpoint**: New modules own the detailed migration and keymaster listener
logic. `__init__.py` still owns setup, unload, update-listener, and
`async_start_listener()` orchestration plus package-level re-exports.

---

## Phase 3: User Story 1 — Preserve Existing Integration Behavior (Priority: P1) 🎯 MVP

**Goal**: Existing setup, unload, migration, update-listener, package-listener,
and keymaster-listener behavior stays externally identical.

**Independent Test**: Run the existing behavior tests unchanged and confirm public
entry points remain importable from `custom_components.rental_control`.

### Tests for User Story 1

> **NOTE: Keep existing behavior assertions unchanged. Add only import-stability
> coverage if the current tests do not already assert the moved public names.**

- [X] T014 [US1] Add or confirm package-level import-stability assertions for `async_migrate_entry` and `async_register_keymaster_listener` in `tests/unit/test_init.py`
- [X] T015 [P] [US1] Run existing integration setup, unload, update-listener, and migration tests in `tests/unit/test_init.py`
- [X] T016 [P] [US1] Run existing keymaster diagnostics behavior tests in `tests/unit/test_keymaster_event_diagnostics.py`
- [X] T017 [P] [US1] Run existing keymaster forwarding and rejection behavior tests in `tests/unit/test_checkin_sensor.py`

### Implementation for User Story 1

- [X] T018 [US1] Preserve `async_setup_entry`, `async_unload_entry`, `update_listener`, and `async_start_listener` behavior in `custom_components/rental_control/__init__.py` while using the package-level `async_register_keymaster_listener` re-export
- [X] T019 [US1] Preserve Home Assistant and test-suite public imports for `async_migrate_entry` and `async_register_keymaster_listener` from `custom_components/rental_control/__init__.py`

**Checkpoint**: The MVP behavior surface is unchanged and package-level imports
continue to work for Home Assistant, project modules, and tests.

---

## Phase 4: User Story 2 — Review Migration Logic Independently (Priority: P1)

**Goal**: Migration responsibilities live in `migrations.py` and each version
transition can be reviewed without setup, unload, or listener code.

**Independent Test**: Exercise every supported migration path and the unsupported
old-version path with unchanged expectations.

### Tests for User Story 2

- [X] T020 [US2] Confirm existing unsupported-version migration coverage remains unchanged in `tests/unit/test_init.py`
- [X] T021 [US2] Confirm existing v3→v10, v6→v10, v7→v10, v8→v10, and v9→v10 migration assertions remain unchanged in `tests/unit/test_init.py`

### Implementation for User Story 2

- [X] T022 [US2] Preserve the unsupported version `< 3` safe-failure path and error logging in `custom_components/rental_control/migrations.py`
- [X] T023 [US2] Preserve per-version data mutations, defaults, removals, `unique_id`, `version`, and final success behavior in `custom_components/rental_control/migrations.py`
- [X] T024 [US2] Run targeted migration validation with `uv run pytest tests/unit/test_init.py -k migrate` against `tests/unit/test_init.py`

**Checkpoint**: Migration behavior is reviewable in one module and remains
behaviorally identical across every existing migration test.

---

## Phase 5: User Story 3 — Review Keymaster Listener Logic Independently (Priority: P2)

**Goal**: Keymaster listener registration, event filtering, diagnostics, and
unlock forwarding live in `listeners.py` and can be reviewed independently.

**Independent Test**: Register the listener, replay accepted and rejected event
cases, and verify unchanged listener storage, diagnostics, monitoring checks, and
check-in forwarding.

### Tests for User Story 3

- [X] T025 [P] [US3] Confirm existing diagnostics acceptance and rejection assertions remain unchanged in `tests/unit/test_keymaster_event_diagnostics.py`
- [X] T026 [P] [US3] Confirm existing keymaster forwarding, monitoring switch, entry-data, and slot-range assertions remain unchanged in `tests/unit/test_checkin_sensor.py`

### Implementation for User Story 3

- [X] T027 [US3] Preserve keymaster event hot-path ordering in `custom_components/rental_control/listeners.py`: slugify lock name, return early for unmonitored locks, then evaluate state, slot, range, entry data, check-in sensor, monitoring switch, diagnostics, and forwarding
- [X] T028 [US3] Preserve diagnostic ring-buffer entries, timestamps, dispositions, and check-in sensor state refresh behavior in `custom_components/rental_control/listeners.py`
- [X] T029 [US3] Preserve listener lifecycle behavior by appending the keymaster bus unsubscribe callback to `UNSUB_LISTENERS` in `custom_components/rental_control/listeners.py`
- [X] T030 [US3] Run targeted listener validation with `uv run pytest tests/unit/test_keymaster_event_diagnostics.py tests/unit/test_checkin_sensor.py -k keymaster` against `tests/unit/test_keymaster_event_diagnostics.py` and `tests/unit/test_checkin_sensor.py`

**Checkpoint**: Listener behavior is reviewable in one module and remains
behaviorally identical across existing diagnostics and forwarding tests.

---

## Phase 6: User Story 4 — Bound the Refactor to the Reported Complexity Issue (Priority: P3)

**Goal**: The decomposition addresses only the issue-reported file-size and
function-length complexity warnings without unrelated behavior cleanup.

**Independent Test**: Review the final diff and complexity results for the
in-scope files only.

### Tests for User Story 4

- [X] T031 [US4] Confirm no existing behavior assertions were rewritten in `tests/unit/test_init.py`, `tests/unit/test_keymaster_event_diagnostics.py`, or `tests/unit/test_checkin_sensor.py` beyond import-stability coverage

### Implementation for User Story 4

- [X] T032 [US4] Verify `async_start_listener` stays in `custom_components/rental_control/__init__.py` and is not moved into `custom_components/rental_control/listeners.py`
- [X] T033 [US4] Verify `custom_components/rental_control/__init__.py` contains setup, unload, update-listener, package-listener startup, and re-export orchestration only, with no detailed migration or keymaster event-handler bodies remaining
- [X] T034 [US4] Confirm the final implementation diff is limited to `custom_components/rental_control/__init__.py`, `custom_components/rental_control/migrations.py`, `custom_components/rental_control/listeners.py`, and `tests/unit/test_init.py`

**Checkpoint**: Scope remains bounded to issue #572 and unrelated functionality or
complexity warnings are not changed.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Final validation across all user stories and quality gates

- [X] T035 Run the full unchanged acceptance suite with `uv run pytest tests/` against `tests/`
- [X] T036 Run ruff validation for `custom_components/rental_control/__init__.py`, `custom_components/rental_control/migrations.py`, `custom_components/rental_control/listeners.py`, `tests/unit/test_init.py`, `tests/unit/test_keymaster_event_diagnostics.py`, and `tests/unit/test_checkin_sensor.py`
- [X] T037 Run mypy validation through the existing pre-commit hook for `custom_components/rental_control/__init__.py`, `custom_components/rental_control/migrations.py`, `custom_components/rental_control/listeners.py`, and `tests/unit/test_init.py`
- [X] T038 Run interrogate validation through the existing pre-commit hook for `custom_components/rental_control/__init__.py`, `custom_components/rental_control/migrations.py`, and `custom_components/rental_control/listeners.py`
- [X] T039 Verify `custom_components/rental_control/__init__.py` has fewer than 400 lines and every function in `custom_components/rental_control/__init__.py`, `custom_components/rental_control/migrations.py`, and `custom_components/rental_control/listeners.py` is at or below 80 lines using the AST check from `specs/011-decompose-init/quickstart.md`
- [X] T040 Run the staged aislop scan and confirm `custom_components/rental_control/__init__.py` no longer reports `complexity/file-too-large` and no in-scope function in `custom_components/rental_control/__init__.py`, `custom_components/rental_control/migrations.py`, or `custom_components/rental_control/listeners.py` reports `complexity/function-too-long`
- [X] T041 Run the public import script from `specs/011-decompose-init/quickstart.md` and confirm `async_migrate_entry` and `async_register_keymaster_listener` are callable from `custom_components/rental_control/__init__.py`
- [X] T042 Run full pre-commit validation for `custom_components/rental_control/`, `tests/unit/test_init.py`, and `specs/011-decompose-init/tasks.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Setup — BLOCKS user-story validation
- **US1 (Phase 3)**: Depends on Foundational — validates public behavior and imports
- **US2 (Phase 4)**: Depends on migration extraction tasks T005, T007, T008, and T009
- **US3 (Phase 5)**: Depends on listener extraction tasks T006, T010, T011, and T012
- **US4 (Phase 6)**: Depends on US1, US2, and US3 for final scope review
- **Polish (Phase 7)**: Depends on all desired user stories being complete

### User Story Dependencies

- **US1 (P1)**: Can start after Foundational; MVP behavior gate for the refactor
- **US2 (P1)**: Can start after migration extraction; independent of US3
- **US3 (P2)**: Can start after listener extraction; independent of US2 after Foundational
- **US4 (P3)**: Depends on US1 + US2 + US3 to audit final scope and thresholds

### Within Each User Story

- Import-stability coverage must be added or confirmed before relying on moved
  package-level functions
- Existing behavior assertions must remain unchanged unless only import-boundary
  coverage is added
- Migration helper split must complete before migration validation
- Listener helper split must complete before listener validation
- Story complete before moving to the next lower priority

### Parallel Opportunities

- T005 and T006 can run in parallel because they create different new modules
- T015, T016, and T017 can run in parallel after package-level re-exports exist
- US2 migration validation and US3 listener validation can proceed in parallel
  after their respective extraction tasks complete
- T025 and T026 can run in parallel because they validate different test files
- T036, T037, and T038 can run independently after implementation is complete

---

## Parallel Example: Migration and Listener Work

```bash
# After setup, create the two independent module scaffolds:
Task: "Add migrations.py scaffold" # T005
Task: "Add listeners.py scaffold" # T006

# After Foundational extraction, validate independent behavior areas:
Task: "Run migration tests in tests/unit/test_init.py" # T024
Task: "Run listener tests in diagnostics/check-in files" # T030
```

---

## Implementation Strategy

### MVP First (US1 + US2)

1. Complete Phase 1: Setup and baseline full-suite test run
2. Complete Phase 2: New module boundaries, moved functions, helper splits, and
   package-level re-exports
3. Complete Phase 3: Public behavior and import-stability validation
4. Complete Phase 4: Migration reviewability and unchanged migration validation
5. **STOP and VALIDATE**: Run T024 plus the relevant US1 tests before continuing

### Incremental Delivery

1. Setup + Foundational → module boundaries and package imports are stable
2. US1 → existing behavior and package-level import contract are preserved
3. US2 → migration logic is independently reviewable and behaviorally identical
4. US3 → keymaster listener logic is independently reviewable and unchanged
5. US4 → scope remains limited to issue #572
6. Polish → full test suite, ruff, mypy, interrogate, thresholds, aislop, quickstart,
   and pre-commit validation

### Atomic Commit Sequence

1. `Test(init): cover public entry imports` — T014 only
2. `Refactor(migrations): extract entry migrations` — T005, T007, T008, T009,
   T020, T021, T022, T023, and T024
3. `Refactor(listeners): extract keymaster listener` — T006, T010, T011, T012,
   T025, T026, T027, T028, T029, and T030
4. `Refactor(init): trim entry orchestration` — T013, T018, T019, T031, T032,
   T033, and T034
5. `Chore: validate init decomposition` — T035, T036, T037, T038, T039, T040,
   T041, and T042 if validation changes are required
6. `Docs(tasks): Mark spec 011 complete` — `specs/011-decompose-init/tasks.md`
   checkbox updates only, committed separately from implementation changes

---

## Notes

- This is a pure refactor; do not change user-facing configuration, entity state,
  service behavior, migration semantics, listener semantics, diagnostics, or
  unrelated complexity warnings.
- `async_start_listener()` must stay in `custom_components/rental_control/__init__.py`.
- `custom_components/rental_control/migrations.py` and
  `custom_components/rental_control/listeners.py` must define their own `_LOGGER` and
  must never import from `custom_components/rental_control/__init__.py`.
- Package-level imports from `custom_components.rental_control` must continue to
  expose `async_migrate_entry` and `async_register_keymaster_listener`.
- The primary acceptance signal is that `uv run pytest tests/` passes with existing
  behavior assertions unchanged.
