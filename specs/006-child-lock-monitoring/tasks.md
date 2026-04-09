<!--
SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Tasks: Child Lock Monitoring for Keymaster Parent/Child Lock Setups

**Input**: Design documents from `/specs/006-child-lock-monitoring/`
**Prerequisites**: plan.md, spec.md, data-model.md, contracts/events.md, research.md, quickstart.md

**Tests**: Included with production code in the same commit per project convention (Principle II: Atomic Commit Discipline). Test-only tasks appear only when no production code changes are required (e.g., US2 verification).

**Organization**: Tasks are grouped by user story. Each phase maps to one PR with atomic commits using CAPITALIZED conventional commit types.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **Production code**: `custom_components/rental_control/`
- **Unit tests**: `tests/unit/`
- **Integration tests**: `tests/integration/`

---

## Phase 1: Setup

**Purpose**: No setup needed — existing project, existing files, no new dependencies or constants required. `LOCK_MANAGER` ("keymaster") already exists in `const.py`.

*This phase is intentionally empty. All modifications target existing files.*

---

## Phase 2: Foundational — Coordinator Child Lock Discovery

**Purpose**: Add child lock discovery infrastructure and monitored lockname set to the coordinator. This is the shared foundation required by all user stories.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [ ] T001 Add `_parent_entry_id` field with keymaster config entry lookup and `_child_locknames` field with `_discover_child_locks()` method integrated into `_async_update_data()`, with unit tests in `custom_components/rental_control/coordinator.py` and `tests/unit/test_coordinator.py`
- [ ] T002 Add `monitored_locknames` property returning `frozenset[str]` of parent + child locknames, with unit tests in `custom_components/rental_control/coordinator.py` and `tests/unit/test_coordinator.py`

**Checkpoint**: Coordinator discovers child locks on each refresh and exposes the monitored lockname set. Run: `uv run pytest tests/unit/test_coordinator.py -v -k "child_lock or monitored_locknames or parent_entry"` — all new tests pass.

---

## Phase 3: User Story 1 — Child Lock Unlock Triggers Check-in (Priority: P1) 🎯 MVP

**Goal**: Unlock events from child locks trigger check-in detection identically to parent lock events, with lock identity attribution in event data and sensor attributes (FR-001, FR-002, FR-009).

**Independent Test**: Configure rental-control with a keymaster parent lock that has child locks. Simulate unlock events from both parent and child locks; verify the checkin sensor transitions to `checked_in` in both cases with correct `lock_name` in the `rental_control_checkin` event payload and `extra_state_attributes`.

### Implementation for User Story 1

- [ ] T003 [US1] Add `lock_name: str = ""` parameter to `async_handle_keymaster_unlock()`, `_checkin_lock_name` field, include `lock_name` in `_transition_to_checked_in()` event payload, `extra_state_attributes`, and `CheckinExtraStoredData` persistence, and clear on state resets, with unit tests in `custom_components/rental_control/sensors/checkinsensor.py` and `tests/unit/test_checkin_sensor.py`
- [ ] T004 [US1] Update `_handle_keymaster_event` to use `coordinator.monitored_locknames` set matching instead of single lockname equality, capture `event_lockname` and pass as `lock_name` to sensor, with unit tests in `custom_components/rental_control/__init__.py` and `tests/unit/test_init.py`
- [ ] T005 [P] [US1] Add integration test for child lock unlock triggering full check-in lifecycle in `tests/integration/test_checkin_tracking.py`
- [ ] T006 [P] [US1] Add integration test for simultaneous parent and child unlock resulting in single check-in (dedup via state machine) in `tests/integration/test_checkin_tracking.py`

**Checkpoint**: Child lock unlocks trigger check-in with lock identity attribution. Run: `uv run pytest tests/ -v -k "child_lock or lock_name or monitored_locknames"` — all tests pass.

---

## Phase 4: User Story 2 — Unified Monitoring Switch (Priority: P2)

**Goal**: Verify that the existing keymaster monitoring switch controls parent AND all child lock event processing as a single unit (FR-004).

**Independent Test**: Toggle the monitoring switch and verify that unlock events from both parent and child locks are either all processed or all ignored based on the switch state.

**Note**: Per research R-007, no production code changes are needed. The monitoring switch check at `__init__.py` line 379 runs *before* lockname matching, so it already gates all events regardless of origin. This phase contains verification tests only.

- [ ] T007 [US2] Add verification test that monitoring switch enabled processes child lock unlock events in `tests/unit/test_checkin_sensor.py`
- [ ] T008 [US2] Add verification test that monitoring switch disabled ignores child lock unlock events in `tests/unit/test_checkin_sensor.py`

**Checkpoint**: Monitoring switch correctly controls all lock event processing. Run: `uv run pytest tests/unit/test_checkin_sensor.py -v -k "monitoring and child"` — all tests pass.

---

## Phase 5: User Story 3 — Dynamic Child Lock Discovery (Priority: P3)

**Goal**: Verify that rental-control automatically detects child lock additions and removals without restart or reconfiguration (FR-005, FR-006).

**Independent Test**: Add/remove child lock keymaster config entries and verify the coordinator's `monitored_locknames` set updates on the next refresh cycle, and that the event listener immediately reflects the change.

**Note**: Production code for discovery is implemented in Phase 2 (Foundational). This phase contains integration-level lifecycle tests that exercise the full system pipeline from discovery through event processing.

- [ ] T009 [P] [US3] Add integration test for new child lock discovered and events processed after coordinator refresh in `tests/integration/test_checkin_tracking.py`
- [ ] T010 [P] [US3] Add integration test for removed child lock events ignored after coordinator refresh in `tests/integration/test_checkin_tracking.py`

**Checkpoint**: Dynamic discovery verified end-to-end. Run: `uv run pytest tests/integration/test_checkin_tracking.py -v -k "dynamic_child"` — all tests pass.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final validation across all stories and pre-commit compliance.

- [ ] T011 [P] Run full test suite to confirm ≥552 tests pass with no regressions: `uv run pytest tests/ -v`
- [ ] T012 [P] Run pre-commit hooks on all modified files: `pre-commit run --all-files`
- [ ] T013 Run quickstart.md validation commands to confirm feature works end-to-end

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: Empty — no work needed.
- **Foundational (Phase 2)**: No dependencies — can start immediately. **BLOCKS all user stories.**
- **US1 (Phase 3)**: Depends on Phase 2 completion. Delivers MVP.
- **US2 (Phase 4)**: Depends on Phase 3 completion (needs child lock event pipeline to verify switch behavior).
- **US3 (Phase 5)**: Depends on Phase 3 completion (needs full pipeline to verify end-to-end discovery lifecycle).
- **Polish (Phase 6)**: Depends on all previous phases.

### Task Dependencies Within Phases

**Phase 2** (sequential — both modify `coordinator.py`):

```text
T001 → T002
```

**Phase 3** (sequential pipeline, then parallel integration tests):

```text
T003 → T004 → T005 [P] T006
```

**Phase 4** (parallel — independent test scenarios):

```text
T007 [P] T008
```

**Phase 5** (parallel — independent test scenarios):

```text
T009 [P] T010
```

**Phase 6** (partially parallel):

```text
T011 [P] T012 → T013
```

### User Story Dependencies

- **US1 (P1)**: Depends on Foundational (Phase 2). No dependency on other stories.
- **US2 (P2)**: Depends on US1 (Phase 3). Needs child lock event pipeline in place to test switch behavior.
- **US3 (P3)**: Depends on US1 (Phase 3). Needs full pipeline to test discovery lifecycle. Production code is in Phase 2.

### Parallel Opportunities

- **T005 and T006**: Independent integration test scenarios (child lock lifecycle vs. dedup), same file but additive.
- **T007 and T008**: Independent monitoring switch verification tests (enabled vs. disabled).
- **T009 and T010**: Independent dynamic discovery tests (add vs. remove).
- **T011 and T012**: Full test suite run and pre-commit checks are independent validation steps.

---

## Parallel Example: User Story 1 Integration Tests

```bash
# After T004 completes, launch both integration tests in parallel:
Task T005: "Integration test: child lock unlock triggers full check-in lifecycle"
Task T006: "Integration test: simultaneous parent+child unlock results in single check-in"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 2: Foundational (coordinator discovery) — 2 commits
2. Complete Phase 3: User Story 1 (sensor + listener + integration) — 4 commits
3. **STOP and VALIDATE**: Run `uv run pytest tests/ -v` — confirm ≥552 tests pass
4. MVP delivers: child lock unlocks trigger check-in with lock identity attribution

### Incremental Delivery

1. Phase 2 → Coordinator discovers child locks and exposes monitored set
2. Phase 3 → Child lock unlocks trigger check-in (MVP! ✅)
3. Phase 4 → Monitoring switch verified for child lock scenarios
4. Phase 5 → Dynamic discovery verified end-to-end
5. Phase 6 → Final validation and compliance
6. Each phase is a separate PR with atomic commits

### Commit Convention

All commits use CAPITALIZED conventional commit types:

- `Feat: add child lock discovery to coordinator`
- `Feat: add monitored_locknames property to coordinator`
- `Feat: add lock_name tracking to check-in sensor`
- `Feat: update event listener for child lock matching`
- `Test: add child lock check-in integration tests`
- `Test: verify monitoring switch controls child lock events`
- `Test: verify dynamic child lock discovery lifecycle`

---

## Notes

- [P] tasks = different files/functions, no dependencies — safe to execute simultaneously
- [US*] label maps task to specific user story from spec.md for traceability
- Each task = one atomic commit containing production code + its tests (per Principle II)
- Each phase = one PR (per project convention)
- No new files in production code — all modifications to existing files
- No new constants needed — `LOCK_MANAGER` ("keymaster") already exists in `const.py`
- No config flow changes — still configure single parent lock
- Monitoring switch requires no changes (R-007) — already gates all events before lockname matching
- Total modification targets: 3 production files + 4 test files
- Existing test count: 552 — all must continue passing
