<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Tasks: Explicit Entry Data Guards

**Input**: Design documents from `/specs/010-explicit-entry-guards/`
**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, quickstart.md ✅

**Tests**: Included — the constitution and spec require loaded-entry
regression coverage plus missing-domain and missing-entry safety coverage for
the helper and all six issue-reported call sites.

**Organization**: Tasks are grouped by user story. The shared helper is a
foundational blocker. US1 and US2 are both P1 and share the same call-site
implementation, so the MVP is complete only after both phases pass.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3, US4)
- Include exact file paths in descriptions

## Path Conventions

- **Source**: `custom_components/rental_control/` at repository root
- **Tests**: `tests/unit/`, `tests/integration/` at repository root

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish the issue #571 baseline before changing tests or code

- [ ] T001 Inventory the six chained entry-data lookups with `rg` in custom_components/rental_control/__init__.py, custom_components/rental_control/sensors/checkinsensor.py, and custom_components/rental_control/switch.py
- [ ] T002 Run the baseline targeted pytest command for tests/unit/test_util.py, tests/unit/test_init.py, tests/unit/test_keymaster_event_diagnostics.py, tests/unit/test_checkin_sensor.py, tests/unit/test_switch.py, and tests/integration/test_full_setup.py

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Add the shared explicit entry-data helper used by every story

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [ ] T003 Add get_entry_data unit tests for present entry data, missing hass.data[DOMAIN], missing hass.data[DOMAIN][entry_id], and no throwaway mutation in tests/unit/test_util.py
- [ ] T004 Implement get_entry_data(hass, entry_id) -> dict[str, Any] | None with a docstring and type hints in custom_components/rental_control/util.py

**Checkpoint**: Foundation ready — helper returns the existing entry dict when
present and None for both missing-domain and missing-entry paths.

---

## Phase 3: User Story 1 — Preserve Loaded Entry Behavior (Priority: P1) 🎯 MVP

**Goal**: Normally loaded entries keep existing configuration update, listener
refresh, event forwarding, monitoring, early-expiry, and switch-registration
behavior.

**Independent Test**: With hass.data[DOMAIN][entry_id] populated, exercise each
reported operation and verify the same externally observable behavior as before.

### Tests for User Story 1

> **NOTE: Write these tests FIRST, ensure they FAIL or protect current behavior
> before implementation tasks preserve the loaded-entry path.**

- [ ] T005 [P] [US1] Add update_listener present-data regression tests for config update and listener refresh behavior in tests/unit/test_init.py
- [ ] T006 [P] [US1] Add keymaster event present-data accepted-forwarding regression coverage in tests/unit/test_keymaster_event_diagnostics.py
- [ ] T007 [P] [US1] Add monitoring-switch present-data tests proving switch is_on and is_off states win over the lockname fallback in tests/unit/test_checkin_sensor.py
- [ ] T008 [US1] Add async_checkout present-data early-expiry regression test proving an enabled early-expiry switch shortens expiry in tests/unit/test_checkin_sensor.py
- [ ] T009 [P] [US1] Add KeymasterMonitoringSwitch present-data registration regression test in tests/unit/test_switch.py

### Implementation for User Story 1

- [ ] T010 [US1] Replace both update_listener entry-data lookups with get_entry_data while preserving present-data config update and listener refresh behavior in custom_components/rental_control/__init__.py
- [ ] T011 [US1] Replace the _handle_keymaster_event entry-data lookup with get_entry_data while preserving accepted unlock forwarding when components are present in custom_components/rental_control/__init__.py
- [ ] T012 [US1] Replace _is_keymaster_monitoring_enabled and async_checkout entry-data lookups with get_entry_data while preserving present switch and early-expiry behavior in custom_components/rental_control/sensors/checkinsensor.py
- [ ] T013 [US1] Replace KeymasterMonitoringSwitch.async_added_to_hass entry-data lookup with get_entry_data while preserving state restore, debug logging, and successful registration in custom_components/rental_control/switch.py

**Checkpoint**: Loaded-entry behavior is covered for all six call sites. The MVP
is not complete until US2 missing-data tests also pass.

---

## Phase 4: User Story 2 — Short-Circuit Missing Entry Data Safely (Priority: P1)

**Goal**: Missing integration domain data or missing entry data is explicit,
safe, and does not create or mutate phantom entry state.

**Independent Test**: Remove hass.data[DOMAIN], then remove only
hass.data[DOMAIN][entry_id], and invoke every affected operation without
unhandled exceptions or throwaway state mutation.

### Tests for User Story 2

- [ ] T014 [US2] Add update_listener missing-domain and missing-entry tests for first-lookup early return and second-lookup listener-refresh early return in tests/unit/test_init.py
- [ ] T015 [P] [US2] Add keymaster event missing-domain and missing-entry rejection tests verifying no forwarding and no accepted disposition in tests/unit/test_keymaster_event_diagnostics.py
- [ ] T016 [P] [US2] Add _is_keymaster_monitoring_enabled missing-domain and missing-entry tests verifying fallback to self.coordinator.lockname is not None in tests/unit/test_checkin_sensor.py
- [ ] T017 [US2] Add async_checkout missing-domain and missing-entry tests verifying checkout continues without early expiry in tests/unit/test_checkin_sensor.py
- [ ] T018 [P] [US2] Add KeymasterMonitoringSwitch missing-domain and missing-entry tests verifying no throwaway dict mutation while restore/logging behavior remains safe in tests/unit/test_switch.py

### Implementation for User Story 2

- [ ] T019 [US2] Ensure update_listener returns before config mutation when entry data is absent before update and before listener refresh when data vanishes after update in custom_components/rental_control/__init__.py
- [ ] T020 [US2] Ensure _handle_keymaster_event rejects missing entry data before CHECKIN_SENSOR or KEYMASTER_MONITORING_SWITCH lookups and never records accepted forwarding in custom_components/rental_control/__init__.py
- [ ] T021 [US2] Ensure _is_keymaster_monitoring_enabled falls back to self.coordinator.lockname is not None and async_checkout skips early expiry then continues checkout when get_entry_data returns None in custom_components/rental_control/sensors/checkinsensor.py
- [ ] T022 [US2] Ensure KeymasterMonitoringSwitch.async_added_to_hass returns after state restore and debug logging, but without mutating entry state, when get_entry_data returns None in custom_components/rental_control/switch.py

**Checkpoint**: Missing-domain and missing-entry behavior is explicit and safe
for all six issue-reported paths. MVP is complete with US1 + US2.

---

## Phase 5: User Story 3 — Keep Supporting Component Absence Predictable (Priority: P2)

**Goal**: A present entry that temporarily lacks a supporting component keeps the
existing local non-action or fallback behavior.

**Independent Test**: Populate hass.data[DOMAIN][entry_id] but omit one
supporting component at a time and verify the documented behavior.

### Tests for User Story 3

- [ ] T023 [P] [US3] Add present-entry missing-CHECKIN_SENSOR and missing-KEYMASTER_MONITORING_SWITCH event rejection tests in tests/unit/test_keymaster_event_diagnostics.py
- [ ] T024 [US3] Add present-entry missing KEYMASTER_MONITORING_SWITCH fallback tests for both configured and unconfigured lockname values in tests/unit/test_checkin_sensor.py
- [ ] T025 [US3] Add present-entry missing EARLY_CHECKOUT_EXPIRY_SWITCH checkout test verifying skip-and-continue behavior in tests/unit/test_checkin_sensor.py

### Implementation for User Story 3

- [ ] T026 [US3] Keep supporting component lookups local with existing .get(...) fallback behavior after get_entry_data succeeds in custom_components/rental_control/__init__.py and custom_components/rental_control/sensors/checkinsensor.py

**Checkpoint**: Missing components inside present entry data remain distinct
from missing entry data and preserve their existing outcomes.

---

## Phase 6: User Story 4 — Bound Refactor Scope (Priority: P3)

**Goal**: Limit changes to the six issue-reported entry-data access paths plus
the shared helper and targeted tests.

**Independent Test**: Review the diff and chained-get inventory to verify each
reported path is explicit and unrelated behavior is unchanged.

### Tests for User Story 4

- [ ] T027 [P] [US4] Add or extend loaded-entry smoke coverage for normal setup and unload in tests/integration/test_full_setup.py only if unit tests do not already cover a changed behavior

### Implementation for User Story 4

- [ ] T028 [US4] Confirm no user-facing configuration, entity state, service, migration, or unrelated data-access changes were introduced outside custom_components/rental_control/util.py, custom_components/rental_control/__init__.py, custom_components/rental_control/sensors/checkinsensor.py, and custom_components/rental_control/switch.py
- [ ] T029 [US4] Run the quickstart chained-get search and confirm the six issue-reported paths no longer use hass.data.get(DOMAIN, {}) defaults in custom_components/rental_control/__init__.py, custom_components/rental_control/sensors/checkinsensor.py, and custom_components/rental_control/switch.py

**Checkpoint**: Scope remains bounded to issue #571 and all six reported paths
have explicit domain and entry guards.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Final validation across all stories

- [ ] T030 Run targeted pytest validation for tests/unit/test_util.py, tests/unit/test_init.py, tests/unit/test_keymaster_event_diagnostics.py, tests/unit/test_checkin_sensor.py, tests/unit/test_switch.py, and tests/integration/test_full_setup.py
- [ ] T031 Run ruff validation for custom_components/rental_control/util.py, custom_components/rental_control/__init__.py, custom_components/rental_control/sensors/checkinsensor.py, custom_components/rental_control/switch.py, tests/unit/test_util.py, tests/unit/test_init.py, tests/unit/test_keymaster_event_diagnostics.py, tests/unit/test_checkin_sensor.py, and tests/unit/test_switch.py
- [ ] T032 Run full test suite via `uv run pytest tests/ -v` covering tests/unit/ and tests/integration/
- [ ] T033 Run pre-commit hook validation for custom_components/rental_control/, tests/, and specs/010-explicit-entry-guards/tasks.md
- [ ] T034 Validate the implementation against specs/010-explicit-entry-guards/quickstart.md acceptance checks

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Setup — BLOCKS all user stories
- **US1 (Phase 3)**: Depends on Foundational — loaded-entry regression coverage
- **US2 (Phase 4)**: Depends on Foundational and shares implementation with US1
- **US3 (Phase 5)**: Depends on US1 + US2 so missing components stay local
- **US4 (Phase 6)**: Depends on all implementation phases for scope review
- **Polish (Phase 7)**: Depends on all desired user stories being complete

### User Story Dependencies

- **US1 (P1)**: Can start after Foundational; MVP requires US2 too
- **US2 (P1)**: Can start after Foundational; MVP requires US1 too
- **US3 (P2)**: Depends on US1 + US2 implementation preserving local fallbacks
- **US4 (P3)**: Depends on US1 + US2 + US3 to audit final scope

### Within Each User Story

- Tests MUST be written before implementation tasks for that story
- Source changes should stay in the four planned implementation files
- Supporting component absence must not be collapsed into missing entry-data logic
- Story complete before moving to the next lower priority

### Parallel Opportunities

- T005, T006, T007, and T009 can run in parallel after T004
- T015, T016, and T018 can run in parallel after T004
- T023 can run in parallel with T024 or T025 if test file edits are coordinated
- US1 and US2 test-writing can proceed in parallel after the helper contract exists
- T030 and T031 are independent validation commands after implementation is complete

---

## Parallel Example: User Story 2

```bash
# Launch missing-data tests in different files after T004:
Task: "Keymaster event missing-domain and missing-entry tests in tests/unit/test_keymaster_event_diagnostics.py" # T015
Task: "Monitoring missing-domain and missing-entry tests in tests/unit/test_checkin_sensor.py" # T016
Task: "Switch registration missing-domain and missing-entry tests in tests/unit/test_switch.py" # T018

# Then implement per source file:
Task: "__init__.py missing-data guards" # T019-T020
Task: "checkinsensor.py missing-data fallbacks" # T021
Task: "switch.py missing-data guard" # T022
```

---

## Implementation Strategy

### MVP First (US1 + US2)

1. Complete Phase 1: Setup baseline inventory and tests
2. Complete Phase 2: Foundational helper tests and helper implementation
3. Complete Phase 3: US1 loaded-entry tests and source updates
4. Complete Phase 4: US2 missing-data tests and source updates
5. **STOP and VALIDATE**: Run T030 and confirm all six call sites preserve loaded
   behavior and handle missing domain or entry data safely

### Incremental Delivery

1. Setup + Foundational → helper contract ready
2. US1 + US2 → complete P1 MVP for issue #571
3. US3 → component-absence behavior remains predictable
4. US4 → scope remains limited to the reported quality issue
5. Polish → targeted tests, ruff, full tests, pre-commit, quickstart validation

### Atomic Commit Sequence

1. `Test: add entry-data helper coverage` (T003)
2. `Feat: add explicit entry-data helper` (T004)
3. `Test: cover loaded entry-data paths` (T005-T009)
4. `Refactor: use entry-data helper for loaded paths` (T010-T013)
5. `Test: cover missing entry-data paths` (T014-T018)
6. `Refactor: enforce missing-data fallbacks` (T019-T022)
7. `Test: cover supporting component absence` (T023-T025)
8. `Refactor: preserve component fallback scope` (T026)
9. `Chore: validate explicit entry guards` (T027-T034)

---

## Notes

- [P] tasks = different files and no dependency on another incomplete task
- [Story] label maps tasks back to the feature specification for traceability
- Missing domain data and missing entry data must both be tested separately
- `_is_keymaster_monitoring_enabled()` missing data falls back to
  `self.coordinator.lockname is not None`; it must not blanket-return False
- `async_checkout()` missing data skips early expiry and continues checkout
- `KeymasterMonitoringSwitch.async_added_to_hass()` must not mutate a throwaway
  dict when entry data is missing
- Do not implement unrelated shared-data cleanup beyond issue #571
