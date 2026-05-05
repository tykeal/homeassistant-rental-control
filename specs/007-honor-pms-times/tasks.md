<!--
SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Tasks: Extract Check-in/Check-out Times from Event Description

**Input**: Design documents from `/specs/007-honor-pms-times/`
**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅, contracts/time-resolution.md ✅, quickstart.md ✅

**Tests**: Included — the plan.md constitution check requires full test coverage (Principle I), and the spec explicitly lists unit tests and integration tests as in-scope.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story. US1 (extract times) and US2 (preserve priority) are both P1; US3 (partial/missing) and US4 (multiple formats) are P2.

**Foundation**: This task list builds upon the already-completed `honor_event_times` config plumbing (config flow, migration, coordinator attribute). The description parser is a new module that integrates into the existing time resolution chain.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3, US4)
- Include exact file paths in descriptions

## Path Conventions

- **Source**: `custom_components/rental_control/`
- **Tests**: `tests/unit/`

---

## Phase 1: Setup (New Module Scaffold)

**Purpose**: Create the description parser module file with SPDX headers, imports, and compiled regex constants — no logic yet

- [X] T001 Create `custom_components/rental_control/description_parser.py` with SPDX license header (`Apache-2.0`), `"""Parse check-in/check-out times from iCal event descriptions."""` module docstring, `from __future__ import annotations` import, `import re` and `from datetime import time` imports, and the two compiled regex constants `_CHECKIN_PATTERN` and `_CHECKOUT_PATTERN` per data-model.md patterns (no functions yet)

---

## Phase 2: Foundational (Internal Helper)

**Purpose**: Implement the shared `_parse_time_match` helper that ALL user stories depend on for time conversion

**⚠️ CRITICAL**: No user story work can begin until this phase is complete — all extraction functions depend on this helper

- [X] T002 Implement `_parse_time_match(hour_str: str, minute_str: str | None, ampm: str | None) -> time | None` in `custom_components/rental_control/description_parser.py`: convert regex capture groups to a validated `datetime.time` object following the 12-hour conversion table and validation rules from data-model.md (reject hour > 23 without AM/PM, reject hour < 1 or > 12 with AM/PM, reject minute > 59, handle 12 AM → 0 and 12 PM → 12)

**Checkpoint**: Foundation ready — the time conversion helper is available for all extraction functions

---

## Phase 3: User Story 1 — Extract Times from All-Day Event Descriptions (Priority: P1) 🎯 MVP

**Goal**: Parse check-in/check-out times from all-day event descriptions (e.g., "Checkin time: 16\nCheckout time: 11") and use them to set the calendar entity's start/end times when `honor_event_times` is enabled.

**Independent Test**: Create a calendar with an all-day event containing "Checkin time: 16\nCheckout time: 11" in the description, enable `honor_event_times`, and verify the resulting calendar entity shows 16:00 start and 11:00 end times.

### Tests for User Story 1

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [X] T003 [P] [US1] Add test in `tests/unit/test_description_parser.py` that verifies `extract_checkin_time("Checkin time: 16\nCheckout time: 11")` returns `time(16, 0)`
- [X] T004 [P] [US1] Add test in `tests/unit/test_description_parser.py` that verifies `extract_checkout_time("Checkin time: 16\nCheckout time: 11")` returns `time(11, 0)`
- [X] T005 [P] [US1] Add test in `tests/unit/test_description_parser.py` that verifies `extract_checkin_time("Check-in time: 16:30")` returns `time(16, 30)`
- [X] T006 [P] [US1] Add test in `tests/unit/test_description_parser.py` that verifies `extract_checkout_time("Check-out time: 11:30")` returns `time(11, 30)`
- [X] T007 [P] [US1] Add test in `tests/unit/test_description_parser.py` that verifies `extract_checkin_time("Check-in: 4 PM")` returns `time(16, 0)` and `extract_checkout_time("Checkout: 11 AM")` returns `time(11, 0)`
- [X] T008 [P] [US1] Add integration test in `tests/unit/test_coordinator.py` that verifies when `honor_event_times=True` and event is all-day with "Checkin time: 16\nCheckout time: 11" in DESCRIPTION, `_ical_parser` builds `CalendarEvent` with start time 16:00 and end time 11:00

### Implementation for User Story 1

- [X] T009 [US1] Implement `extract_checkin_time(description: str) -> time | None` in `custom_components/rental_control/description_parser.py`: use `_CHECKIN_PATTERN.search(description)` and pass match groups to `_parse_time_match()`; return None if no match
- [X] T010 [US1] Implement `extract_checkout_time(description: str) -> time | None` in `custom_components/rental_control/description_parser.py`: use `_CHECKOUT_PATTERN.search(description)` and pass match groups to `_parse_time_match()`; return None if no match
- [X] T011 [US1] Integrate description parser into coordinator time resolution in `custom_components/rental_control/coordinator.py`: import `extract_checkin_time` and `extract_checkout_time` from `.description_parser`, add new `elif self.honor_event_times and not has_explicit_times:` branch after line 624 (after the existing `has_explicit_times` block) that extracts description from `str(event.get("DESCRIPTION", ""))`, calls both extraction functions, and resolves checkin/checkout per the partial extraction logic in data-model.md (use extracted value if not None, otherwise fall through to override or default)
- [X] T012 [US1] Verify all US1 tests pass — run `uv run pytest tests/unit/test_description_parser.py tests/unit/test_coordinator.py -v -k "checkin_time or checkout_time or description"` from repository root

**Checkpoint**: At this point, User Story 1 is fully functional — all-day events with description times produce correct calendar entity times.

---

## Phase 4: User Story 2 — Preserve Existing Time Resolution Priority (Priority: P1)

**Goal**: Ensure the new description parsing does NOT interfere with existing behavior: timed events still use explicit times, overrides still take precedence when `honor_event_times` is disabled, and the feature is entirely gated by the toggle.

**Independent Test**: Verify that timed events (with datetime DTSTART/DTEND) still use their explicit times regardless of description content, and that `honor_event_times=False` completely skips description parsing.

### Tests for User Story 2

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [X] T013 [P] [US2] Add test in `tests/unit/test_coordinator.py` that verifies when `honor_event_times=True` and event has explicit datetime DTSTART/DTEND AND description contains "Checkin time: 9\nCheckout time: 18", `_ical_parser` uses the explicit event times (not description times) — validates FR-009 Priority 1 > Priority 2
- [X] T014 [P] [US2] Add test in `tests/unit/test_coordinator.py` that verifies when `honor_event_times=False` and event is all-day with description times, `_ical_parser` uses configured defaults (description parsing is skipped entirely per FR-012)
- [X] T015 [P] [US2] Add test in `tests/unit/test_coordinator.py` that verifies when `honor_event_times=True` and event is all-day with description times AND an override exists, `_ical_parser` uses description-extracted times (Priority 2 beats Priority 3 override)

### Implementation for User Story 2

> **NOTE**: The time resolution logic implemented in T011 already handles these priority cases via the branching structure. These tests validate that the existing implementation correctly preserves priorities — no additional source code changes should be needed.

- [X] T016 [US2] Verify all US2 tests pass — run `uv run pytest tests/unit/test_coordinator.py -v -k "priority or preserve or honor_false"` from repository root
- [X] T017 [US2] If any US2 test fails, fix the time resolution branching in `custom_components/rental_control/coordinator.py` `_ical_parser()` to ensure: (a) explicit datetime events always use event times when honor=True, (b) description times are never parsed when honor=False, (c) description times beat overrides for all-day events when honor=True

**Checkpoint**: At this point, User Stories 1 AND 2 are both independently functional. Existing behavior is confirmed preserved.

---

## Phase 5: User Story 3 — Graceful Handling of Missing or Partial Description Times (Priority: P2)

**Goal**: When description times are absent or only partially present, the system gracefully falls through to defaults without errors.

**Independent Test**: Process events with no description, empty description, only check-in time in description, or only check-out time — all should produce valid calendar entities without errors.

### Tests for User Story 3

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [X] T018 [P] [US3] Add test in `tests/unit/test_description_parser.py` that verifies `extract_checkin_time("")` returns `None` and `extract_checkout_time("")` returns `None`
- [X] T019 [P] [US3] Add test in `tests/unit/test_description_parser.py` that verifies `extract_checkin_time("Guest phone: +1234567890\nNotes: no times here")` returns `None`
- [X] T020 [P] [US3] Add test in `tests/unit/test_description_parser.py` that verifies partial extraction: `extract_checkin_time("Checkin time: 16\nGuest: John")` returns `time(16, 0)` and `extract_checkout_time("Checkin time: 16\nGuest: John")` returns `None`
- [X] T021 [P] [US3] Add test in `tests/unit/test_description_parser.py` that verifies partial extraction: `extract_checkout_time("Checkout time: 11\nGuest: Jane")` returns `time(11, 0)` and `extract_checkin_time("Checkout time: 11\nGuest: Jane")` returns `None`
- [X] T022 [P] [US3] Add integration test in `tests/unit/test_coordinator.py` that verifies when `honor_event_times=True` and event is all-day with only "Checkin time: 14" in description (no checkout), `_ical_parser` uses extracted check-in time (14:00) and configured default checkout time
- [X] T023 [P] [US3] Add integration test in `tests/unit/test_coordinator.py` that verifies when `honor_event_times=True` and event is all-day with only "Checkout time: 10" in description (no checkin), `_ical_parser` uses configured default checkin time and extracted checkout time (10:00)
- [X] T024 [P] [US3] Add integration test in `tests/unit/test_coordinator.py` that verifies when `honor_event_times=True` and event is all-day with no description times and no override, `_ical_parser` uses configured default checkin/checkout times

### Implementation for User Story 3

> **NOTE**: The parser functions already return `None` for missing patterns (T009, T010), and the coordinator integration (T011) already handles partial extraction with fallthrough. These tests validate the existing implementation.

- [X] T025 [US3] Verify all US3 tests pass — run `uv run pytest tests/unit/test_description_parser.py tests/unit/test_coordinator.py -v -k "partial or missing or empty or none"` from repository root
- [X] T026 [US3] If any US3 test fails, fix either the parser in `custom_components/rental_control/description_parser.py` (ensure None is returned for no-match) or the coordinator fallthrough logic in `custom_components/rental_control/coordinator.py` (ensure None values fall through to override/default per data-model.md)

**Checkpoint**: Partial and missing description times handled gracefully. All user stories 1-3 are independently functional.

---

## Phase 6: User Story 4 — Support Multiple Time Formats (Priority: P2)

**Goal**: Parser recognizes integer hours, HH:MM format, and 12-hour AM/PM format including edge cases (12 AM = midnight, 12 PM = noon).

**Independent Test**: Create events with various time formats ("16", "16:30", "4 PM", "12 AM", "12 PM") in descriptions and verify correct extraction for each.

### Tests for User Story 4

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [X] T027 [P] [US4] Add test in `tests/unit/test_description_parser.py` that verifies integer hour parsing: `extract_checkin_time("Checkin time: 0")` returns `time(0, 0)`, `extract_checkin_time("Checkin time: 23")` returns `time(23, 0)`
- [X] T028 [P] [US4] Add test in `tests/unit/test_description_parser.py` that verifies HH:MM parsing: `extract_checkin_time("Checkin time: 16:30")` returns `time(16, 30)`, `extract_checkout_time("Checkout time: 00:00")` returns `time(0, 0)`, `extract_checkout_time("Checkout time: 23:59")` returns `time(23, 59)`
- [X] T029 [P] [US4] Add test in `tests/unit/test_description_parser.py` that verifies 12-hour AM/PM: `extract_checkin_time("Checkin time: 4 PM")` returns `time(16, 0)`, `extract_checkout_time("Checkout time: 11 AM")` returns `time(11, 0)`
- [X] T030 [P] [US4] Add test in `tests/unit/test_description_parser.py` that verifies 12-hour edge cases: `extract_checkin_time("Checkin time: 12 AM")` returns `time(0, 0)`, `extract_checkin_time("Checkin time: 12 PM")` returns `time(12, 0)`
- [X] T031 [P] [US4] Add test in `tests/unit/test_description_parser.py` that verifies invalid values return None: `extract_checkin_time("Checkin time: 25")` returns `None`, `extract_checkin_time("Checkin time: 16:75")` returns `None`, `extract_checkin_time("Checkin time: 13 AM")` returns `None`, `extract_checkin_time("Checkin time: 0 PM")` returns `None`
- [X] T032 [P] [US4] Add test in `tests/unit/test_description_parser.py` that verifies case-insensitivity: `extract_checkin_time("CHECKIN TIME: 14")` returns `time(14, 0)`, `extract_checkout_time("CHECKOUT: 11")` returns `time(11, 0)`
- [X] T033 [P] [US4] Add test in `tests/unit/test_description_parser.py` that verifies all label variants: "Checkin time:", "Check-in time:", "Check-in:", "Checkout time:", "Check-out time:", "Check-out:", "Checkout:" all produce correct results
- [X] T034 [P] [US4] Add test in `tests/unit/test_description_parser.py` that verifies first-match-wins: `extract_checkin_time("Checkin time: 14\nCheckin time: 16")` returns `time(14, 0)` (first match used)

### Implementation for User Story 4

> **NOTE**: The regex patterns (_CHECKIN_PATTERN, _CHECKOUT_PATTERN) and `_parse_time_match()` helper implemented in T001 and T002 already handle all these formats. These tests validate comprehensive format coverage.

- [X] T035 [US4] Verify all US4 tests pass — run `uv run pytest tests/unit/test_description_parser.py -v` from repository root
- [X] T036 [US4] If any US4 test fails, fix the regex patterns or `_parse_time_match()` logic in `custom_components/rental_control/description_parser.py` to handle the failing format case per data-model.md validation rules and 12-hour conversion table

**Checkpoint**: All time formats fully supported. All four user stories are independently functional.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Full test suite validation, edge cases, and pre-commit compliance

- [X] T037 [P] Add edge case test in `tests/unit/test_coordinator.py` that verifies when event DESCRIPTION is `None` (no DESCRIPTION field in iCal), coordinator handles gracefully without error (the `str(event.get("DESCRIPTION", ""))` pattern produces empty string)
- [X] T038 [P] Add edge case test in `tests/unit/test_coordinator.py` that verifies when event transitions from timed to all-day between refreshes with `honor_event_times=True`, the description parser is correctly invoked on the now all-day event
- [X] T039 [P] Add edge case test in `tests/unit/test_coordinator.py` that verifies when `honor_event_times=True` and all-day event has description times AND override exists, the description times win (Priority 2 > Priority 3) and the override is updated via the existing time-update pipeline
- [X] T040 Run full test suite: `uv run pytest tests/ -v --cov=custom_components/rental_control --cov-report=term-missing` — verify no regressions across all existing tests and coverage meets requirements
- [X] T041 Run pre-commit hooks: `pre-commit run --all-files` — verify ruff, mypy, interrogate (100% docstring coverage on new module), reuse (SPDX headers), and all other hooks pass
- [X] T042 Validate quickstart.md test commands work: run `uv run pytest tests/unit/test_description_parser.py -v` and `uv run pytest tests/unit/test_coordinator.py -v -k "description or honor"` from repository root

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 (module must exist with regex constants) — BLOCKS all user stories
- **User Story 1 (Phase 3)**: Depends on Phase 2 — extraction functions use the helper
- **User Story 2 (Phase 4)**: Depends on Phase 3 — validates that US1 implementation preserves existing priorities
- **User Story 3 (Phase 5)**: Depends on Phase 3 — validates partial extraction behavior of US1 implementation
- **User Story 4 (Phase 6)**: Depends on Phase 2 — validates format handling in the foundational helper
- **Polish (Phase 7)**: Depends on all user stories being complete

### User Story Dependencies

- **User Story 1 (P1 — core extraction)**: Can start after Foundational (Phase 2). This is the MVP — all other stories validate aspects of US1's implementation.
- **User Story 2 (P1 — preserve priority)**: Can start after US1 (Phase 3). Tests validate US1's coordinator integration preserves existing priority chain.
- **User Story 3 (P2 — partial/missing)**: Can start after US1 (Phase 3). Tests validate US1's None-handling and fallthrough behavior.
- **User Story 4 (P2 — format support)**: Can start after Foundational (Phase 2). Tests validate the regex patterns and `_parse_time_match()` helper. **Can run in parallel with US2 and US3.**

### Within Each User Story

- Tests MUST be written and FAIL before implementation
- Parser module before coordinator integration
- Core implementation before integration validation
- Story complete before moving to next priority

### Parallel Opportunities

- **Phase 3**: T003–T008 (US1 tests) can all run in parallel — all are independent test functions
- **Phase 4**: T013, T014, T015 (US2 tests) can all run in parallel
- **Phase 5**: T018–T024 (US3 tests) can all run in parallel
- **Phase 6**: T027–T034 (US4 tests) can all run in parallel
- **Phase 7**: T037, T038, T039 (edge case tests) can run in parallel
- **Cross-phase**: Phase 5 (US3) and Phase 6 (US4) can execute in parallel once Phase 3 (US1) is complete

---

## Parallel Example: User Story 1

```bash
# Launch all US1 unit tests together (all write to independent test functions):
Task T003: "Test extract_checkin_time returns time(16, 0) for 'Checkin time: 16'"
Task T004: "Test extract_checkout_time returns time(11, 0) for 'Checkout time: 11'"
Task T005: "Test extract_checkin_time returns time(16, 30) for HH:MM format"
Task T006: "Test extract_checkout_time returns time(11, 30) for HH:MM format"
Task T007: "Test 12-hour AM/PM format extraction"
Task T008: "Integration test: coordinator uses description times for all-day event"

# After tests exist and fail, launch implementation (sequential within story):
Task T009: "Implement extract_checkin_time"
Task T010: "Implement extract_checkout_time"
Task T011: "Integrate into coordinator"
```

---

## Parallel Example: US3 + US4 After US1

```bash
# Once US1 (Phase 3) is complete, US3 and US4 tests can launch in parallel:

# US3 tests (partial/missing handling):
Task T018–T024: All partial extraction tests

# US4 tests (format coverage) — runs in parallel with US3:
Task T027–T034: All format validation tests
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001 — module scaffold)
2. Complete Phase 2: Foundational (T002 — `_parse_time_match` helper)
3. Complete Phase 3: User Story 1 (T003–T012 — core extraction + coordinator integration)
4. **STOP and VALIDATE**: Test US1 independently — all-day events with description times produce correct calendar entity times
5. This delivers the core value: Hostaway-style calendars work without manual overrides

### Incremental Delivery

1. Setup + Foundational → Parser module scaffold ready
2. Add User Story 1 → Core extraction works → Validate independently (MVP!)
3. Add User Story 2 → Existing behavior confirmed preserved → Validate independently
4. Add User Stories 3 + 4 (parallel) → Edge cases + format coverage confirmed → Validate independently
5. Polish → Full test suite passes, pre-commit clean

### Atomic Commit Strategy (from quickstart.md)

Each phase maps to one atomic commit:
1. `Feat: Add description parser for event time extraction` — T001, T002, T009, T010
2. `Test: Add unit tests for description time parser` — T003–T007, T018–T021, T027–T034
3. `Feat: Integrate description time extraction into time resolution` — T011
4. `Test: Add coordinator integration tests for description extraction` — T008, T013–T015, T022–T024, T037–T039

---

## Notes

- [P] tasks = different files or independent test functions, no dependencies
- [Story] label maps task to specific user story for traceability
- This feature builds on the already-completed `honor_event_times` config toggle (previous tasks.md, all marked [X])
- The `description_parser.py` module is independently testable without HA mocking (pure functions)
- The coordinator integration modifies existing time resolution branching (lines 621–646 of coordinator.py)
- All downstream pipeline code (event_overrides.py, calsensor.py, util.py) is UNCHANGED per research.md
- All file paths are relative to repository root: `/home/tykeal/repos/personal/homeassistant/rental-control/`
