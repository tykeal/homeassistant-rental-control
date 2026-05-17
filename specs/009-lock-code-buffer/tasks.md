# Tasks: Lock Code Buffer Times

**Input**: Design documents from `/specs/009-lock-code-buffer/`
**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅, contracts/ ✅, quickstart.md ✅

**Tests**: Included — plan.md specifies ~8 test files added/modified; existing test files confirmed in `tests/unit/` and `tests/integration/`.

**Organization**: Tasks grouped by user story. US1 (pre-buffer) and US2 (post-buffer) share implementation files since both buffers are applied in the same code paths; US2 phase focuses on additional test coverage for the after-buffer specifically.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3, US4)
- Include exact file paths in descriptions

## Path Conventions

- **Source**: `custom_components/rental_control/` at repository root
- **Tests**: `tests/unit/`, `tests/integration/` at repository root

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Add constants and UI strings that all subsequent phases depend on

- [ ] T001 Add CONF_CODE_BUFFER_BEFORE, CONF_CODE_BUFFER_AFTER, DEFAULT_CODE_BUFFER_BEFORE, DEFAULT_CODE_BUFFER_AFTER constants to custom_components/rental_control/const.py
- [ ] T002 [P] Add UI labels and descriptions for code_buffer_before and code_buffer_after fields to custom_components/rental_control/strings.json

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Migration, VERSION bump, and coordinator properties that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [ ] T003 Add v9→v10 migration block that adds CONF_CODE_BUFFER_BEFORE=0 and CONF_CODE_BUFFER_AFTER=0 to custom_components/rental_control/__init__.py
- [ ] T004 [P] Bump RentalControlFlowHandler.VERSION from 9 to 10 in custom_components/rental_control/config_flow.py
- [ ] T005 Add code_buffer_before and code_buffer_after int properties to __init__ and update_config methods in custom_components/rental_control/coordinator.py

**Checkpoint**: Foundation ready — constants defined, migration handles upgrade, coordinator exposes buffer values

---

## Phase 3: User Story 4 — Seamless Upgrade for Existing Users (Priority: P1)

**Goal**: Existing users upgrading from config v9 experience zero change in lock code timing; both buffer values default to 0

**Independent Test**: Upgrade from config version 9 to 10 and verify both buffer values default to 0 and all lock code timing remains identical to pre-upgrade behavior

### Tests for User Story 4

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation tasks in later phases confirm behavior**

- [ ] T006 [US4] Add v9→v10 migration test verifying both buffer fields are added with default 0 in tests/unit/test_init.py
- [ ] T007 [P] [US4] Add coordinator initialization test verifying code_buffer_before=0 and code_buffer_after=0 from migrated config in tests/unit/test_coordinator.py

**Checkpoint**: Migration correctness verified — upgrading users get default 0 buffers with no behavior change

---

## Phase 4: User Story 1 — Configure Pre-Buffer for Early Guest Arrival (Priority: P1) 🎯 MVP

**Goal**: Property managers can set a before-buffer so lock codes activate N minutes before check-in time

**Independent Test**: Configure a before-buffer value and verify lock code validity start is shifted earlier by the configured minutes

### Tests for User Story 1

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [ ] T008 [P] [US1] Add config flow test verifying buffer fields appear when lock entry is configured and are hidden when no lock entry in tests/unit/test_config_flow.py
- [ ] T009 [P] [US1] Add config flow validation test verifying negative buffer values are rejected in tests/unit/test_config_flow.py
- [ ] T010 [P] [US1] Add unit test verifying async_fire_set_code sends buffered date_range_start (start - before_buffer) to Keymaster in tests/unit/test_util.py
- [ ] T011 [P] [US1] Add unit test verifying async_fire_update_times sends buffered date_range_start (start - before_buffer) to Keymaster in tests/unit/test_util.py

### Implementation for User Story 1

- [ ] T012 [US1] Add before-buffer and after-buffer fields to _get_schema conditional on CONF_LOCK_ENTRY being configured in custom_components/rental_control/config_flow.py
- [ ] T013 [US1] Apply buffer offsets to date_range_start and date_range_end in async_fire_set_code in custom_components/rental_control/util.py
- [ ] T014 [US1] Apply buffer offsets to date_range_start and date_range_end in async_fire_update_times in custom_components/rental_control/util.py

**Checkpoint**: Pre-buffer fully functional — lock codes activate early by configured minutes; config flow shows buffer fields when lock is configured

---

## Phase 5: User Story 2 — Configure Post-Buffer for Late Checkout Grace (Priority: P2)

**Goal**: Property managers can set an after-buffer so lock codes remain active N minutes after checkout time

**Independent Test**: Configure an after-buffer value and verify lock code validity end is extended by the configured minutes

> **Note**: Implementation was delivered in Phase 4 (T012–T014) since both buffers share the same code paths. This phase adds after-buffer-specific test coverage.

### Tests for User Story 2

- [ ] T015 [US2] Add unit test verifying async_fire_set_code sends buffered date_range_end (end + after_buffer) to Keymaster in tests/unit/test_util.py
- [ ] T016 [P] [US2] Add unit test verifying async_fire_update_times sends buffered date_range_end (end + after_buffer) to Keymaster in tests/unit/test_util.py
- [ ] T017 [P] [US2] Add unit test verifying zero-buffer defaults produce unbuffered date ranges identical to pre-feature behavior in tests/unit/test_util.py

**Checkpoint**: Post-buffer verified — lock codes stay active past checkout by configured minutes; zero-buffer preserves existing behavior

---

## Phase 6: User Story 3 — Adjust Buffer Settings for Active Reservations (Priority: P3)

**Goal**: Changing buffer values in options flow updates active lock code slots on the next coordinator refresh cycle (lazy update)

**Independent Test**: Change buffer values while an active reservation exists and verify lock code date ranges update on next coordinator refresh

### Tests for User Story 3

- [ ] T018 [US3] Add coordinator test verifying update_config picks up changed buffer values from config entry in tests/unit/test_coordinator.py
- [ ] T019 [US3] Add integration test verifying buffer change propagates to active lock code slots on refresh cycle in tests/integration/test_refresh_cycle.py

**Checkpoint**: All user stories independently functional — buffers configurable, applicable to new and active reservations, upgrade-safe

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Final validation across all stories

- [ ] T020 [P] Add combined before+after buffer test verifying both offsets applied simultaneously in tests/unit/test_util.py
- [ ] T021 Run full test suite via `uv run pytest tests/ -v`
- [ ] T022 Run pre-commit hooks validation (ruff, mypy, interrogate, reuse-tool, gitlint)
- [ ] T023 Validate against quickstart.md acceptance scenarios
- [ ] T024 [P] Add negative test verifying calsensor event attributes, checkinsensor tracked_event_start/end, and event_overrides use unbuffered times when buffers are configured in tests/unit/test_util.py (FR-005)
- [ ] T025 Add integration test verifying check-in sensor transitions to checked_in when lock code is used during before-buffer window (before unbuffered event start) with keymaster monitoring enabled in tests/integration/test_refresh_cycle.py (FR-011, SC-007)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 — BLOCKS all user stories
- **US4 (Phase 3)**: Depends on Phase 2 — tests migration correctness
- **US1 (Phase 4)**: Depends on Phase 2 — core buffer implementation
- **US2 (Phase 5)**: Depends on Phase 4 — leverages shared implementation, adds after-buffer tests
- **US3 (Phase 6)**: Depends on Phase 4 — tests lazy update of active reservations
- **Polish (Phase 7)**: Depends on all user story phases

### User Story Dependencies

- **US4 (P1)**: Can start after Foundational (Phase 2) — no dependencies on other stories
- **US1 (P1)**: Can start after Foundational (Phase 2) — no dependencies on other stories; can run in parallel with US4
- **US2 (P2)**: Depends on US1 implementation (Phase 4) since both buffers share code paths
- **US3 (P3)**: Depends on US1 implementation (Phase 4) for buffer application code to exist

### Within Each User Story

- Tests MUST be written and FAIL before implementation
- Implementation follows atomic commit discipline (one logical change per commit)
- Story complete before moving to next priority

### Parallel Opportunities

- T001 and T002 can run in parallel (different files)
- T003, T004, and T005 — T004 can run in parallel with T003 (different files)
- T006 and T007 can run in parallel (different test files)
- T008, T009, T010, T011 can all run in parallel (all are test-writing tasks in different test areas)
- T015, T016, T017 — T016 and T017 can run in parallel with T015
- US4 (Phase 3) and US1 tests (Phase 4 tests) can start in parallel after Foundational phase

---

## Parallel Example: User Story 1

```bash
# Launch all US1 tests in parallel (write tests first):
Task: "Config flow buffer field visibility test in tests/unit/test_config_flow.py"  # T008
Task: "Config flow negative value rejection test in tests/unit/test_config_flow.py" # T009
Task: "Before-buffer async_fire_set_code test in tests/unit/test_util.py"           # T010
Task: "Before-buffer async_fire_update_times test in tests/unit/test_util.py"       # T011

# Then implement sequentially (same files have dependencies):
Task: "Add buffer fields to config flow schema"     # T012
Task: "Apply buffer offsets in async_fire_set_code"  # T013
Task: "Apply buffer offsets in async_fire_update_times" # T014
```

---

## Implementation Strategy

### MVP First (US4 + US1)

1. Complete Phase 1: Setup (constants + strings)
2. Complete Phase 2: Foundational (migration + VERSION + coordinator)
3. Complete Phase 3: US4 tests (verify upgrade safety)
4. Complete Phase 4: US1 tests → implementation (core buffer feature)
5. **STOP and VALIDATE**: Run `uv run pytest tests/ -v` — both buffers work, upgrade is safe
6. This delivers full buffer functionality as MVP

### Incremental Delivery

1. Setup + Foundational → Foundation ready
2. US4 (migration tests) → Upgrade safety verified
3. US1 (pre-buffer + implementation) → Core feature working (MVP!)
4. US2 (after-buffer tests) → Post-buffer coverage complete
5. US3 (lazy update tests) → Runtime adjustment verified
6. Polish → Full validation pass

### Atomic Commit Sequence (from quickstart.md)

1. `Feat: add CONF_CODE_BUFFER constants to const.py` (T001)
2. `Feat: add strings.json labels for buffer fields` (T002)
3. `Feat: add v9-to-v10 config migration for buffer fields` (T003, T004)
4. `Feat: add buffer properties to coordinator` (T005)
5. `Feat: add buffer fields to config flow schema` (T012)
6. `Feat: apply buffer offsets in Keymaster service calls` (T013, T014)
7. `Test: add unit tests for buffer functionality` (T006–T011, T015–T020)

---

## Notes

- [P] tasks = different files, no dependencies on incomplete tasks
- [Story] label maps task to specific user story for traceability
- US1 and US2 share implementation (same code paths for before/after buffer); US2 phase is test-focused
- Both buffer fields are always added together (same schema block, same function calls) per contracts/internal-api.md
- All source changes are modifications to existing files (~6 files); no new source files created
- Test files are modifications to existing test files + potentially new integration test file
- Commit after each task or logical group following atomic commit discipline
