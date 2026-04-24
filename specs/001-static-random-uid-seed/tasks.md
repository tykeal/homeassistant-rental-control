# Tasks: Static Random Seed from iCal UID

**Input**: Design documents from `/specs/001-static-random-uid-seed/`
**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅, quickstart.md ✅

**Tests**: Included — plan.md requires "All changes will have unit tests; coverage target ≥85% maintained" and quickstart.md defines explicit test matrix.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **Project type**: Single project — Home Assistant custom component
- **Source**: `custom_components/rental_control/`
- **Tests**: `tests/unit/`

---

## Phase 1: Setup

**Purpose**: Verify existing project structure and test infrastructure; no new files are created by this feature.

- [ ] T001 Verify development environment: run `uv sync --locked` and `uv run pytest tests/unit/test_sensors.py -x -q` to confirm existing tests pass before making changes

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Expose iCal UID in sensor event attributes — required by US1 (seed source), US2 (fallback check), and US3 (attribute exposure). Maps to Commit 1 from quickstart.md.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [ ] T002 Add `"uid": None` to the `_event_attributes` dict initialization in `custom_components/rental_control/sensors/calsensor.py` `__init__` (after the `"end"` key, before `"eta_days"`)
- [ ] T003 Populate `uid` from event in the coordinator update handler: add `self._event_attributes["uid"] = event.uid if hasattr(event, "uid") else None` after the `description` assignment at line ~375 in `custom_components/rental_control/sensors/calsensor.py`
- [ ] T004 Add `"uid": None` to the no-events reset dict in `custom_components/rental_control/sensors/calsensor.py` `_handle_coordinator_update` else block (after `"end": None`, before `"eta_days": None`, around line ~481)

**Checkpoint**: UID is now stored in `_event_attributes` and cleared on reset — ready for user story implementation.

---

## Phase 3: User Story 1 — Stable Door Codes Across Description Changes (Priority: P1) 🎯 MVP

**Goal**: Generated door codes are seeded from the iCal UID instead of the event description, so codes remain stable when descriptions change.

**Independent Test**: Create a sensor with a known UID, generate a door code, change the description, verify the code remains identical.

### Tests for User Story 1

> **NOTE: Write these tests FIRST in `tests/unit/test_sensors.py` inside class `TestGenerateDoorCodeStaticRandom`, ensure they FAIL before implementation**

- [ ] T005 [P] [US1] Write test `test_static_random_uid_seeded_deterministic`: create sensor with `uid="abc-123"` and a description, call `_generate_door_code()` twice, assert same 4-digit code both times in `tests/unit/test_sensors.py`
- [ ] T006 [P] [US1] Write test `test_static_random_uid_stable_across_description_change`: create sensor with `uid="abc-123"`, generate code, change description to a different string, generate code again, assert codes are identical in `tests/unit/test_sensors.py`
- [ ] T007 [P] [US1] Write test `test_static_random_different_uids_produce_different_codes`: create sensor with `uid="uid-A"`, generate code, change to `uid="uid-B"`, generate code, assert codes differ (deterministic distinct values) in `tests/unit/test_sensors.py`
- [ ] T008 [P] [US1] Write test `test_static_random_uid_respects_code_length`: create sensor with `code_length=6` and `uid="test-uid"`, generate code, assert `len(code) == 6` and `code.isdigit()` in `tests/unit/test_sensors.py`

### Implementation for User Story 1

- [ ] T009 [US1] Modify `_generate_door_code()` in `custom_components/rental_control/sensors/calsensor.py`: in the `elif generator == "static_random"` block (~line 278-282), change `random.seed(self._event_attributes["description"])` to `seed = self._event_attributes.get("uid") or self._event_attributes["description"]` then `random.seed(seed)` — prefer UID (immutable) over description (mutable)

**Checkpoint**: US1 is complete — UID-seeded door codes are stable across description changes. Run `uv run pytest tests/unit/test_sensors.py::TestGenerateDoorCodeStaticRandom -v` to validate.

---

## Phase 4: User Story 2 — Graceful Fallback for Calendars Without UIDs (Priority: P2)

**Goal**: When UID is `None`, the generator falls back to description-based seeding (legacy behavior). When both UID and description are `None`, it falls back to date-based generation.

**Independent Test**: Create a sensor with `uid=None` and a valid description, generate a door code, verify it matches legacy description-seeded behavior.

### Tests for User Story 2

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [ ] T010 [P] [US2] Write test `test_static_random_uid_none_falls_back_to_description`: create sensor with `uid=None` and `description="Fallback test"`, generate code, then verify code matches what `random.seed("Fallback test")` would produce in `tests/unit/test_sensors.py`
- [ ] T011 [P] [US2] Write test `test_static_random_uid_and_description_none_falls_back_to_date_based`: create sensor with `uid=None`, `description=None`, `start` and `end` set, generate code, assert code matches date_based output (e.g., `"1520"` for start=2025-03-15, end=2025-03-20) in `tests/unit/test_sensors.py`

### Implementation for User Story 2

- [ ] T012 [US2] Modify the description-is-None guard in `_generate_door_code()` (~line 254-260) in `custom_components/rental_control/sensors/calsensor.py`: change from unconditionally setting `generator = "date_based"` when description is None, to only doing so when `generator != "static_random" or self._event_attributes.get("uid") is None` — this allows static_random to proceed with UID even when description is None
- [ ] T013 [US2] Update existing test `test_date_based_fallback_when_description_none` in `tests/unit/test_sensors.py` class `TestGenerateDoorCodeDateBased`: ensure the test explicitly sets `uid=None` (or confirms the attribute is None) so the fallback to date_based is correctly exercised under the new guard logic

**Checkpoint**: US2 is complete — fallback chain UID → description → date_based works correctly. Run `uv run pytest tests/unit/test_sensors.py -k "static_random or date_based" -v` to validate.

---

## Phase 5: User Story 3 — UID Available as Event Attribute (Priority: P3)

**Goal**: The iCal UID is visible as a sensor state attribute for automation and diagnostic use.

**Independent Test**: Load a calendar event with a known UID and verify the UID appears in the sensor's `extra_state_attributes`.

### Tests for User Story 3

- [ ] T014 [P] [US3] Write test `test_uid_exposed_as_event_attribute`: create sensor, set `_event_attributes["uid"] = "abc-123"`, access `extra_state_attributes`, assert `attrs["uid"] == "abc-123"` in `tests/unit/test_sensors.py`
- [ ] T015 [P] [US3] Write test `test_uid_none_when_event_has_no_uid`: create sensor, confirm `_event_attributes["uid"]` defaults to `None`, verify `extra_state_attributes["uid"] is None` in `tests/unit/test_sensors.py`

### Implementation for User Story 3

> Implementation was completed in Phase 2 (Foundational) — T002, T003, T004 already add, populate, and reset the `uid` attribute. No additional production code changes needed.

**Checkpoint**: US3 is complete — UID is visible in sensor attributes. Run `uv run pytest tests/unit/test_sensors.py -k "uid_exposed or uid_none" -v` to validate.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Documentation, test cleanup, and final validation across all stories.

- [ ] T016 Update existing `test_static_random_produces_code` and `test_static_random_deterministic` tests in `tests/unit/test_sensors.py` to explicitly set `uid` in test sensors (either a UID value to test UID-seeded path, or `None` to test description-seeded path) for clarity and future-proofing
- [ ] T017 [P] Document the breaking change for static_random seed rotation in the repository (release notes or README update): explain one-time code rotation on upgrade, affected users (static_random only), and mitigation (re-program active lock codes)
- [ ] T018 Run full test suite `uv run pytest tests/ -v --tb=short` and verify ≥85% coverage target is maintained
- [ ] T019 Run quickstart.md validation: manually verify all 7 acceptance scenarios from spec.md pass against the implementation
- [ ] T020 Run pre-commit hooks (`pre-commit run --all-files`) to confirm ruff, ruff-format, mypy, interrogate, reuse-tool, and gitlint all pass

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — confirm environment works
- **Foundational (Phase 2)**: Depends on Phase 1 — BLOCKS all user stories
- **US1 (Phase 3)**: Depends on Foundational (Phase 2) — core seed change
- **US2 (Phase 4)**: Depends on Foundational (Phase 2) — can run in parallel with US1
- **US3 (Phase 5)**: Depends on Foundational (Phase 2) — can run in parallel with US1 and US2
- **Polish (Phase 6)**: Depends on all user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: Requires Phase 2 complete (UID in attributes). No dependency on other stories.
- **User Story 2 (P2)**: Requires Phase 2 complete. Independent of US1 — different code path (fallback vs. happy path). Can run in parallel with US1.
- **User Story 3 (P3)**: Requires Phase 2 complete (implementation done in foundational). Independent of US1/US2. Can run in parallel.

### Within Each User Story

- Tests MUST be written and FAIL before implementation
- Implementation tasks in dependency order within each story
- Story complete before moving to Polish phase

### Parallel Opportunities

- T005, T006, T007, T008 (US1 tests) can all run in parallel
- T010, T011 (US2 tests) can all run in parallel
- T014, T015 (US3 tests) can all run in parallel
- US1, US2, and US3 can proceed in parallel after Phase 2 completes (all modify different sections of the same file — coordinate commits)
- T017 (docs) can run in parallel with T016 (test updates)

---

## Parallel Example: User Story 1

```text
# Launch all tests for US1 together (write-first, expect failures):
Task T005: "test_static_random_uid_seeded_deterministic in tests/unit/test_sensors.py"
Task T006: "test_static_random_uid_stable_across_description_change in tests/unit/test_sensors.py"
Task T007: "test_static_random_different_uids_produce_different_codes in tests/unit/test_sensors.py"
Task T008: "test_static_random_uid_respects_code_length in tests/unit/test_sensors.py"

# Then implement (single task, since it's one code block):
Task T009: "Modify _generate_door_code() seed in custom_components/rental_control/sensors/calsensor.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup — verify environment
2. Complete Phase 2: Foundational — expose UID in attributes (T002–T004)
3. Complete Phase 3: US1 — UID-seeded door codes (T005–T009)
4. **STOP and VALIDATE**: Run `uv run pytest tests/unit/test_sensors.py::TestGenerateDoorCodeStaticRandom -v`
5. Commit: `feat: expose iCal UID as sensor event attribute` (Phase 2 changes)
6. Commit: `feat: seed static_random door code from iCal UID` (Phase 3 changes)

### Incremental Delivery

1. Phase 1 + Phase 2 → Foundation ready (UID in attributes)
2. Add US1 → Test independently → Commit (MVP! Codes are now stable)
3. Add US2 → Test independently → Commit (Fallback chain complete)
4. Add US3 → Test independently → Commit (Attribute verified for automation use)
5. Phase 6 → Polish, docs, final validation → Commit (Breaking change documented)

### Commit Strategy (from quickstart.md)

1. **Commit 1** — `feat: expose iCal UID as sensor event attribute` (Phase 2 + US3 tests)
2. **Commit 2** — `feat: seed static_random door code from iCal UID` (US1 + US2 implementation and tests)
3. **Commit 3** — `docs: document breaking change for static_random seed` (Phase 6 docs)

---

## Notes

- **BREAKING CHANGE**: Existing `static_random` users will experience a one-time code rotation on upgrade
- [P] tasks = different files or non-overlapping sections, no dependencies
- [Story] label maps task to specific user story for traceability
- All production changes are in a single file: `custom_components/rental_control/sensors/calsensor.py`
- All test changes are in a single file: `tests/unit/test_sensors.py`
- Scope: ~20 lines of production code, ~80 lines of test code
- Pre-commit hooks must pass on every commit: ruff, ruff-format, mypy, interrogate, reuse-tool, gitlint
