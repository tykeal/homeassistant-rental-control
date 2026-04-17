<!--
SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Tasks: Honor PMS Calendar Event Times

**Input**: Design documents from `/specs/007-honor-pms-times/`
**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅, contracts/time-resolution.md ✅, quickstart.md ✅

**Tests**: Included — the plan.md constitution check requires full test coverage (Principle I).

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story. US1 and US2 are both P1 priority and share foundational work; US3 is P2 and builds on their foundation.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **Source**: `custom_components/rental_control/`
- **Tests**: `tests/unit/`
- **Translations**: `custom_components/rental_control/translations/`

---

## Phase 1: Setup (Constants & Shared Infrastructure)

**Purpose**: Add the new constant and default that all subsequent phases depend on

- [X] T001 Add `CONF_HONOR_EVENT_TIMES = "honor_event_times"` and `DEFAULT_HONOR_EVENT_TIMES = False` to `custom_components/rental_control/const.py` after the existing `CONF_SHOULD_UPDATE_CODE` / `DEFAULT_SHOULD_UPDATE_CODE` entries (after line 95)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Config migration and translation strings that MUST be complete before user story implementation can proceed

**⚠️ CRITICAL**: No user story work can begin until this phase is complete — the config entry must have the new key and the UI must have labels

- [X] T002 Add v7→v8 migration in `custom_components/rental_control/__init__.py`: import `CONF_HONOR_EVENT_TIMES` from `.const`, add migration block after the existing v6→v7 block (after line 237) that copies `config_entry.data`, sets `data[CONF_HONOR_EVENT_TIMES] = False`, and calls `hass.config_entries.async_update_entry()` with `version=8`
- [X] T003 [P] Add `honor_event_times` key with label string to `config.step.user.data` and `options.step.init.data` sections in `custom_components/rental_control/strings.json` — use label: "Honor calendar event times from PMS instead of stored override times"
- [X] T004 [P] Add `honor_event_times` key with identical English label to `config.step.user.data` and `options.step.init.data` sections in `custom_components/rental_control/translations/en.json`
- [X] T005 [P] Add `honor_event_times` key with French translation to `config.step.user.data` and `options.step.init.data` sections in `custom_components/rental_control/translations/fr.json` — use label: "Utiliser les heures des événements du calendrier PMS au lieu des heures de remplacement enregistrées"

**Checkpoint**: Migration and translation strings complete — config entries will have the new key on load

---

## Phase 3: User Story 2 — New Configuration Option in Options Flow (Priority: P1)

**Goal**: Add the "Honor event times" boolean toggle to the integration's options flow so users can enable/disable the feature. This is implemented first because US1 (time resolution) depends on the config option being available.

**Independent Test**: Open the integration's options flow, verify the toggle appears, toggle it on/off, save, reload, and confirm the setting persists.

### Tests for User Story 2

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [X] T006 [P] [US2] Add test in `tests/unit/test_config_flow.py` that verifies `VERSION` is 8 on `RentalControlFlowHandler`
- [X] T007 [P] [US2] Add test in `tests/unit/test_config_flow.py` that verifies `honor_event_times` toggle appears in the options flow schema and defaults to `False`
- [X] T008 [P] [US2] Add test in `tests/unit/test_config_flow.py` that verifies toggling `honor_event_times` to `True` in the options flow persists the value in `config_entry.data` after `update_listener` runs
- [X] T009 [P] [US2] Add test in `tests/unit/test_init.py` that verifies v7→v8 migration sets `honor_event_times` to `False` for an existing config entry that lacks the key

### Implementation for User Story 2

- [X] T010 [US2] Add `honor_event_times` toggle to config flow schema in `custom_components/rental_control/config_flow.py`: import `CONF_HONOR_EVENT_TIMES` and `DEFAULT_HONOR_EVENT_TIMES` from `.const`, add `CONF_HONOR_EVENT_TIMES: DEFAULT_HONOR_EVENT_TIMES` to `DEFAULTS` dict (after `CONF_SHOULD_UPDATE_CODE` entry around line 77), add `vol.Optional(CONF_HONOR_EVENT_TIMES, default=_get_default(CONF_HONOR_EVENT_TIMES, DEFAULT_HONOR_EVENT_TIMES)): cv.boolean` to `_get_schema()` after the `CONF_SHOULD_UPDATE_CODE` entry (after line 287), and bump `VERSION` from 7 to 8
- [X] T011 [US2] Verify all US2 tests pass — run `uv run pytest tests/unit/test_config_flow.py tests/unit/test_init.py -v -k "honor"` from worktree root

**Checkpoint**: At this point, the "Honor event times" toggle is visible in the UI, persists across reloads, and migration handles existing entries. User Story 2 is fully functional and testable independently.

---

## Phase 4: User Story 1 — PMS Time Changes Flow Through to Lock Codes (Priority: P1) 🎯 MVP

**Goal**: When "Honor event times" is enabled, calendar-provided check-in/check-out times take precedence over stored override times for events with explicit times, causing PMS time changes to propagate to Keymaster via the existing time-update pipeline.

**Independent Test**: Enable "Honor event times," create a test calendar with a timed reservation, assign it to a Keymaster slot, change the reservation's start time, trigger a refresh, and verify the override times are updated and a time-update event fires.

### Tests for User Story 1

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [X] T012 [P] [US1] Add test in `tests/unit/test_coordinator.py` that verifies when `honor_event_times=True` and event has explicit times and override exists, `_ical_parser` builds `CalendarEvent` with calendar times (not override times)
- [X] T013 [P] [US1] Add test in `tests/unit/test_coordinator.py` that verifies when `honor_event_times=False` and override exists, `_ical_parser` builds `CalendarEvent` with override times (current behavior preserved — FR-005)
- [X] T014 [P] [US1] Add test in `tests/unit/test_coordinator.py` that verifies when `honor_event_times=True` and event has explicit times and no override exists, `_ical_parser` builds `CalendarEvent` with calendar times
- [X] T015 [P] [US1] Add test in `tests/unit/test_coordinator.py` that verifies when `honor_event_times=True` and calendar times match stored override times, no unnecessary time-update event fires (FR-007)

### Implementation for User Story 1

- [X] T016 [US1] Read and store `honor_event_times` config in `custom_components/rental_control/coordinator.py`: import `CONF_HONOR_EVENT_TIMES` from `.const`, add `self.honor_event_times: bool = bool(config.get(CONF_HONOR_EVENT_TIMES))` in `__init__()` (after line 115 near the existing `self.should_update_code` assignment), and add `self.honor_event_times = bool(config.get(CONF_HONOR_EVENT_TIMES))` in `update_config()` (after line 518 near the existing `self.should_update_code` assignment)
- [X] T017 [US1] Modify time resolution logic in `custom_components/rental_control/coordinator.py` `_ical_parser()` method (lines 607–631): replace the current override-first / try-except block with the new three-way branching: (1) if `self.honor_event_times` and `isinstance(event["DTSTART"].dt, datetime)` → use calendar times, (2) elif override exists → use override times, (3) else try calendar `.time()` with `AttributeError` fallback to defaults
- [X] T018 [US1] Verify all US1 tests pass — run `uv run pytest tests/unit/test_coordinator.py -v -k "honor"` from worktree root

**Checkpoint**: At this point, PMS time changes flow through to Keymaster when "Honor event times" is enabled. The core feature is complete. User Stories 1 AND 2 are both independently functional.

---

## Phase 5: User Story 3 — All-Day Events Fall Back to Default Times (Priority: P2)

**Goal**: When "Honor event times" is enabled, all-day events (no explicit times) correctly fall back to stored override times or configured defaults — no regression from the current behavior.

**Independent Test**: Enable "Honor event times," create an all-day calendar event, verify it uses configured default check-in/check-out times. Then assign it to a slot with override times and verify it uses override times.

### Tests for User Story 3

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [X] T019 [P] [US3] Add test in `tests/unit/test_coordinator.py` that verifies when `honor_event_times=True` and event is all-day (date-only DTSTART) and no override exists, `_ical_parser` uses configured default checkin/checkout times
- [X] T020 [P] [US3] Add test in `tests/unit/test_coordinator.py` that verifies when `honor_event_times=True` and event is all-day and override exists, `_ical_parser` uses override times (not defaults)
- [X] T021 [P] [US3] Add test in `tests/unit/test_coordinator.py` that verifies when `honor_event_times=False` and event is all-day and no override exists, `_ical_parser` uses configured default times (existing behavior)

### Implementation for User Story 3

> **NOTE**: The time resolution logic implemented in T017 already handles all-day event fallback via the three-way branching. These tests validate that the existing implementation is correct for all-day edge cases — no additional source code changes should be needed.

- [X] T022 [US3] Verify all US3 tests pass — run `uv run pytest tests/unit/test_coordinator.py -v -k "all_day or allday"` from worktree root
- [X] T023 [US3] If any US3 test fails, fix the time resolution logic in `custom_components/rental_control/coordinator.py` `_ical_parser()` to ensure all-day events (where `isinstance(event["DTSTART"].dt, datetime)` is `False`) always fall through to the override or default branches

**Checkpoint**: All user stories are now independently functional. All-day events confirmed to work correctly with honor_event_times enabled/disabled.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Full test suite validation, edge cases, and pre-commit compliance

- [X] T024 [P] Add edge case test in `tests/unit/test_coordinator.py` that verifies transitioning an event from explicit times to all-day (between refreshes) with `honor_event_times=True` falls back to defaults gracefully
- [X] T025 [P] Add edge case test in `tests/unit/test_coordinator.py` that verifies enabling `honor_event_times` mid-session (via `update_config`) causes the next refresh to use calendar times for timed events with existing overrides
- [X] T026 Run full test suite from worktree root: `uv run pytest tests/ -v --cov=custom_components/rental_control --cov-report=term-missing` — verify no regressions across all existing tests
- [X] T027 Run pre-commit hooks from worktree root: `pre-commit run --all-files` — verify ruff, mypy, interrogate (100% docstring coverage), reuse (SPDX headers), and gitlint all pass
- [X] T028 Validate quickstart.md test commands work: run `uv run pytest tests/unit/test_coordinator.py -v -k "honor"` and `uv run pytest tests/unit/test_config_flow.py -v -k "honor"` from worktree root

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 (constants must exist) — BLOCKS all user stories
- **User Story 2 (Phase 3)**: Depends on Phase 2 — config flow needs migration + strings
- **User Story 1 (Phase 4)**: Depends on Phase 3 — coordinator needs the config option to be in config_entry.data
- **User Story 3 (Phase 5)**: Depends on Phase 4 — tests validate the same logic implemented in T017
- **Polish (Phase 6)**: Depends on all user stories being complete

### User Story Dependencies

- **User Story 2 (P1 — config)**: Can start after Foundational (Phase 2). No dependencies on other stories. Implemented first because it provides the config plumbing.
- **User Story 1 (P1 — core logic)**: Depends on US2 completion — the coordinator reads `honor_event_times` from config, which must be in the schema and migrated first.
- **User Story 3 (P2 — all-day fallback)**: Depends on US1 — validates that the time resolution logic from T017 correctly handles all-day events. No new source code expected.

### Within Each User Story

- Tests MUST be written and FAIL before implementation
- Config plumbing before core logic
- Core implementation before integration validation
- Story complete before moving to next priority

### Parallel Opportunities

- **Phase 2**: T003, T004, T005 (translation files) can all run in parallel
- **Phase 3**: T006, T007, T008, T009 (US2 tests) can all run in parallel
- **Phase 4**: T012, T013, T014, T015 (US1 tests) can all run in parallel
- **Phase 5**: T019, T020, T021 (US3 tests) can all run in parallel
- **Phase 6**: T024, T025 (edge case tests) can run in parallel

---

## Parallel Example: User Story 1 Tests

```bash
# Launch all US1 test tasks together (all write to different test functions in the same file):
Task T012: "Test honor=True + timed event + override → calendar times"
Task T013: "Test honor=False + override → override times (FR-005)"
Task T014: "Test honor=True + timed event + no override → calendar times"
Task T015: "Test honor=True + times match → no update event (FR-007)"
```

---

## Implementation Strategy

### MVP First (User Stories 1 + 2)

1. Complete Phase 1: Setup (T001 — single constant addition)
2. Complete Phase 2: Foundational (T002–T005 — migration + strings)
3. Complete Phase 3: User Story 2 (T006–T011 — config toggle)
4. Complete Phase 4: User Story 1 (T012–T018 — core time resolution)
5. **STOP and VALIDATE**: Test US1 + US2 independently
6. This delivers the full core feature — PMS times flow through when enabled

### Incremental Delivery

1. Setup + Foundational → Config infrastructure ready
2. Add User Story 2 → Config toggle available in UI → Validate independently
3. Add User Story 1 → Core feature works → Validate independently (MVP!)
4. Add User Story 3 → All-day event behavior confirmed → Validate independently
5. Polish → Full test suite passes, pre-commit clean

### Atomic Commit Strategy (from quickstart.md)

Each phase maps to one atomic commit:
1. `const.py` — Add constants
2. `config_flow.py` — Add toggle + bump VERSION
3. `__init__.py` — Add v7→v8 migration
4. `coordinator.py` — Read config + modify time resolution
5. `strings.json` + `translations/` — Add UI strings
6. `tests/` — Add all tests

---

## Notes

- [P] tasks = different files or independent test functions, no dependencies
- [Story] label maps task to specific user story for traceability
- US2 (config) is implemented before US1 (logic) despite both being P1, because US1 depends on the config plumbing from US2
- US3 tests validate the same code path implemented in US1's T017 — no new source code is expected for US3
- The downstream time-update pipeline (event_overrides.py → calsensor.py → util.py) is UNCHANGED per research.md findings
- All file paths are relative to worktree root: `/home/tykeal/repos/personal/homeassistant/worktrees/007-honor-pms-times/`
