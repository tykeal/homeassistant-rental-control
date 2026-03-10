<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Tasks: Code Health Improvement

**Input**: Design documents from `/specs/002-code-health/`
**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md),
[research.md](research.md), [quickstart.md](quickstart.md)

**Tests**: Test tasks are included (Phase 7) per spec FR-022–FR-024.
Tests are placed last so they cover the improved code paths
(see [plan.md](plan.md) rationale).

**Organization**: Tasks are grouped by user story. US1 and US2
(both P1) are separate phases. US3 (P2, test coverage) is
intentionally last per plan rationale. US4–US6 (P3) follow the
plan's phase ordering.

## Format: `- [ ] T### [P?] [Story?] Description`

- **T###**: Sequential task ID (T001, T002, etc.)
- **[P]**: Can run in parallel (different files, no deps)
- **[Story]**: Which user story (US1–US6) from spec.md
- Include exact file paths in descriptions

## Path Conventions

```text
custom_components/rental_control/   # Source code
tests/unit/                         # Unit tests
tests/integration/                  # Integration tests
```

---

## Phase 1: Setup

**Purpose**: Verify baseline before making any changes

- [ ] T001 Verify baseline: run `uv run pytest tests/ -x -q` (all
  tests pass) and `uv run ruff check custom_components/ tests/`
  (zero findings) to confirm a clean starting state

---

## Phase 2: US1 — Integration Survives Calendar Failures (P1) 🎯 MVP

**Goal**: Calendar provider outages, malformed data, and timeouts
no longer crash the integration. The most recently known-good
calendar data is preserved on failure.

**Independent Test**: Simulate calendar fetch errors (timeout,
malformed response, HTTP error) and verify the integration remains
available with its previous calendar state.

### Implementation for User Story 1

- [ ] T002 [US1] Wrap the entire `_refresh_calendar` method body
  in a try/except in
  `custom_components/rental_control/coordinator.py`. Catch
  `asyncio.TimeoutError` (log warning), `aiohttp.ClientError`
  (log warning with error detail), and `Exception` (log exception
  traceback). On any error, return early preserving existing
  `self.calendar` data. See research.md R1 and quickstart.md key
  patterns for the exact exception hierarchy.
  **FR**: FR-001, FR-002, FR-003
- [ ] T003 [US1] Add `return_exceptions=True` to all
  `asyncio.gather` call sites and add result-checking logic that
  re-raises `asyncio.CancelledError` and logs other exceptions.
  Files:
  `custom_components/rental_control/coordinator.py` (sensor update
  gather in `_refresh_calendar`),
  `custom_components/rental_control/util.py`
  (`async_fire_set_code` and `handle_state_change`),
  `custom_components/rental_control/event_overrides.py`
  (`async_check_overrides`).
  Note: `__init__.py` `async_unload_entry` gather is addressed
  separately in T016 (FR-017). See research.md R2 and quickstart.md
  gather pattern for the `BaseException` / `CancelledError` idiom.
  **FR**: FR-004
- [ ] T004 [US1] Remove the conditional that skips miss tracking
  when `len(calendar) > 1` in
  `custom_components/rental_control/coordinator.py`. Apply the
  same `max_misses` tracking logic regardless of calendar event
  count so that multi-event calendars track empty refreshes
  identically to single-event calendars. See research.md R3.
  **FR**: FR-005
- [ ] T005 [US1] Simplify the `overrides_loaded` readiness tracking
  in `custom_components/rental_control/coordinator.py`. Ensure
  there is a single clear code path for determining when the
  coordinator is ready to process events, regardless of whether a
  lock manager is configured. The current code sets
  `overrides_loaded = True` only when `lockname is None` —
  review and make the readiness determination explicit. See
  research.md R4.
  **FR**: FR-006

**Checkpoint**: After T002–T005, the integration should survive
calendar failures gracefully. Verify with
`uv run pytest tests/ -x -q`.

---

## Phase 3: US2 — Lock Slot Changes Are Resilient (P1)

**Goal**: Bug fixes in the event processing pipeline that affect
lock slot operations. These remove dead code and fix incorrect
patterns that could mask errors.

**Independent Test**: Verify the unreachable handler is gone, event
logging shows actual data, and null-checking uses idiomatic
patterns.

### Implementation for User Story 2

- [ ] T006 [US2] Remove the unreachable `KeyError` exception
  handler around calendar event description retrieval (lines
  521–525) in
  `custom_components/rental_control/coordinator.py`. The
  underlying `.get()` method call can never raise `KeyError`,
  making this handler dead code. See code review §2.2.
  **FR**: FR-007
- [ ] T007 [US2] Fix debug logging to log the actual `cal_event`
  instance instead of the `CalendarEvent` class name (line 535)
  in `custom_components/rental_control/coordinator.py`. Change
  the log argument from the class reference to the event variable.
  See code review §2.3.
  **FR**: FR-008
- [ ] T008 [US2] Replace `isinstance(x, type(None))` with
  `x is None` for the non-reserved event filtering check
  (line 418) in
  `custom_components/rental_control/coordinator.py`. See code
  review §2.4.
  **FR**: FR-009

**Checkpoint**: After T006–T008, all P1 bug fixes are complete.
Verify with `uv run pytest tests/ -x -q`.

---

## Phase 4: US4 — Logging Performance Improvement (P3)

**Goal**: All log statements use deferred formatting so string
interpolation is skipped when the log level is inactive.

**Independent Test**: Run
`grep -rn "f['\"]" custom_components/rental_control/ | grep '_LOGGER\.'`
and confirm zero output (no f-string log calls remain).

### Implementation for User Story 4

- [ ] T009 [US4] Convert all f-string log calls to `%s`-style
  deferred formatting across four files:
  `custom_components/rental_control/__init__.py`,
  `custom_components/rental_control/coordinator.py`,
  `custom_components/rental_control/event_overrides.py`,
  `custom_components/rental_control/util.py`.
  Replace patterns like `_LOGGER.debug(f"message {var}")` with
  `_LOGGER.debug("message %s", var)`. See research.md R5 and
  quickstart.md lazy logging pattern.
  **FR**: FR-010

**Checkpoint**: After T009, verify zero f-string logging with the
grep command above.

---

## Phase 5: US5 — Modernized and Cleaned-Up Codebase (P3)

**Goal**: Remove dead code, stale comments, deprecated patterns,
and legacy idioms. Replace with current Python and HA conventions.

**Independent Test**: Verify all deprecated patterns, dead code,
and stale comments from the code review have been addressed.
Run `grep -rn 'from typing import' custom_components/rental_control/ | grep -Ev 'TYPE_CHECKING|Any|Final'`
and confirm zero output.

### Implementation for User Story 5

- [ ] T010 [US5] Replace legacy `typing` module imports (`Dict`,
  `List`, `Optional`, `Union`) with built-in generic equivalents
  (`dict`, `list`, `X | None`, `X | Y`) in:
  `custom_components/rental_control/config_flow.py`,
  `custom_components/rental_control/coordinator.py`,
  `custom_components/rental_control/event_overrides.py`,
  `custom_components/rental_control/util.py`.
  Remove the corresponding `from typing import` lines (keep
  `TYPE_CHECKING`, `Any`, `Final` if used). This task touches
  all four files and must complete before the remaining US5 tasks.
  **FR**: FR-011
- [ ] T011 [P] [US5] Remove the unused `Any` import and its
  associated `# noqa` suppression comment in
  `custom_components/rental_control/util.py`. See code review
  §5.1.
  **FR**: FR-012
- [ ] T012 [P] [US5] Remove the stale "temporary call" comment
  (line 356) in
  `custom_components/rental_control/coordinator.py`. See code
  review §5.2.
  **FR**: FR-013
- [ ] T013 [US5] Remove all inert `# pylint: disable=` directive
  comments across all source files in
  `custom_components/rental_control/`. The project uses ruff,
  not pylint — these directives have no effect. See code review
  §5.3.
  **FR**: FR-014
- [ ] T014 [P] [US5] Remove the empty `CONFIG_SCHEMA` variable
  and the synchronous `setup()` function in
  `custom_components/rental_control/__init__.py`. The integration
  is config-flow only (declared in `manifest.json`), so these
  are unnecessary. See research.md R6.
  **FR**: FR-015
- [ ] T015 [P] [US5] Remove the legacy
  `@config_entries.HANDLERS.register(DOMAIN)` decorator from the
  config flow class in
  `custom_components/rental_control/config_flow.py`. The manifest
  `config_flow: true` key handles registration. See research.md
  R6.
  **FR**: FR-016
- [ ] T016 [US5] Replace the `asyncio.gather` loop of individual
  `async_forward_entry_unload()` calls with a single
  `hass.config_entries.async_unload_platforms(entry, PLATFORMS)`
  call in `custom_components/rental_control/__init__.py`. See
  research.md R6 for the correct API signature.
  **FR**: FR-017
- [ ] T017 [US5] Convert all `os.path` calls to `pathlib.Path`
  operations in `custom_components/rental_control/util.py`. The
  test suite already uses `pathlib` via `tmp_path` fixtures. See
  research.md R7.
  **FR**: FR-018
- [ ] T018 [P] [US5] Remove the commented-out function parameter
  (line 135) in
  `custom_components/rental_control/config_flow.py`. See code
  review §5.5.
  **FR**: FR-019
- [ ] T019 [P] [US5] Fix the "EventOVerrides" docstring typo
  (capitalization error) in
  `custom_components/rental_control/event_overrides.py`. See
  code review §5.6.
  **FR**: FR-020

**Checkpoint**: After T010–T019, all modernization items from the
code review should be addressed. Verify with
`uv run pytest tests/ -x -q` and the legacy typing grep above.

---

## Phase 6: US6 — Configuration Consistency (P3)

**Goal**: The miss-tracking threshold is handled consistently as a
pure internal constant, not a hybrid config/constant.

**Independent Test**: Verify `CONF_MAX_MISSES` is referenced only
as an internal constant (not read from config entry data).

### Implementation for User Story 6

- [ ] T020 [US6] Make `CONF_MAX_MISSES` a pure internal constant.
  In `custom_components/rental_control/const.py`, ensure it is
  defined as a plain constant. In
  `custom_components/rental_control/coordinator.py`, remove any
  `self.config_entry.data.get(CONF_MAX_MISSES, ...)` pattern and
  reference the constant directly. See research.md R8.
  **FR**: FR-021

**Checkpoint**: After T020, verify the miss threshold is purely
constant-driven with `uv run pytest tests/ -x -q`.

---

## Phase 7: US3 — Improved Test Coverage (P2)

**Goal**: Add automated test coverage for the three identified
coverage gaps: lock slot management, calendar error scenarios, and
slot bootstrapping.

**Independent Test**: Run
`uv run pytest tests/ --cov=custom_components.rental_control --cov-report=term-missing -q`
and verify util.py ≥85% (up from 77%) and coordinator.py ≥85%
(up from 81%).

### Implementation for User Story 3

- [ ] T021 [P] [US3] Add lock slot management function tests in
  `tests/unit/test_util.py`. Test `async_fire_set_code`,
  `async_fire_clear_code`, `async_fire_update_times`, and
  `handle_state_change` using mocked Keymaster service calls.
  Cover both success and failure paths (service call raises, one
  of multiple gather coroutines fails). See research.md R9 item 1.
  **FR**: FR-022
- [ ] T022 [US3] Add calendar error scenario tests in
  `tests/unit/test_coordinator.py` and
  `tests/integration/test_error_handling.py`. Test timeout,
  malformed ical data, timezone conversion failure, and non-200
  HTTP responses using aioresponses. Verify the coordinator
  preserves previous calendar state on each failure. See
  research.md R9 item 2.
  **FR**: FR-023
- [ ] T023 [US3] Add slot bootstrapping path tests in
  `tests/unit/test_coordinator.py`. Test the Keymaster entity
  discovery and slot initialization during coordinator startup.
  Cover the case where Keymaster entities are not yet available
  (still loading). See research.md R9 item 3.
  **FR**: FR-024

**Checkpoint**: After T021–T023, run coverage report and verify
targets. Overall coverage must remain ≥85% (pyproject.toml
`fail_under`).

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Final verification that all success criteria are met

- [ ] T024 Run final verification per
  [quickstart.md](quickstart.md) Final Verification section:
  full test suite with coverage, f-string grep check, legacy
  typing import grep check
- [ ] T025 Validate all success criteria from spec.md:
  SC-001 (zero regressions), SC-002 (all tests pass),
  SC-003 (zero f-string logging), SC-004 (coverage targets met),
  SC-005 (calendar errors handled gracefully),
  SC-006 (all in-scope review items addressed),
  SC-007 (pre-commit pipeline passes cleanly)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — verify baseline first
- **US1 (Phase 2)**: Depends on Setup — calendar error handling
- **US2 (Phase 3)**: Depends on Setup — can start after Phase 1,
  independent of US1 (different code locations in coordinator.py)
- **US4 (Phase 4)**: Depends on US1 + US2 — logging changes should
  apply after bug fixes to avoid reformatting fixed code
- **US5 (Phase 5)**: Depends on US4 — modernization after logging
  to avoid touching the same lines twice
- **US6 (Phase 6)**: Depends on Setup — independent, but ordered
  after US5 to batch coordinator.py changes
- **US3 (Phase 7)**: Depends on all previous phases — tests cover
  the improved code paths
- **Polish (Phase 8)**: Depends on all user stories

### User Story Dependencies

- **US1 (P1)**: Can start after Setup — no other story deps
- **US2 (P1)**: Can start after Setup — independent of US1
- **US4 (P3)**: Should follow US1+US2 (avoid reformatting)
- **US5 (P3)**: Should follow US4 (avoid touching same lines)
- **US6 (P3)**: Independent — ordered after US5 for convenience
- **US3 (P2)**: Must follow all code changes (tests cover final
  code)

### Within Each User Story

- Tasks within US1 are sequential (all touch coordinator.py;
  T002 establishes the try/except structure that T003–T005 build on)
- Tasks within US2 are sequential (all touch coordinator.py)
- US4 is a single task
- US5: T010 must go first (touches all 4 files); after T010,
  tasks marked [P] can run in parallel (different files)
- US6 is a single task
- US3: T021 can run in parallel with T022/T023 (different file);
  T022 and T023 are sequential (both touch test_coordinator.py)

### Parallel Opportunities

After T010 (US5 typing modernization) completes, these five tasks
can run in parallel since they each touch a different file:

```text
T011 [P] — util.py (unused import)
T012 [P] — coordinator.py (stale comment)
T014 [P] — __init__.py (CONFIG_SCHEMA removal)
T015 [P] — config_flow.py (HANDLERS removal)
T019 [P] — event_overrides.py (docstring typo)
```

In US3, T021 (test_util.py) can run in parallel with T022/T023
(test_coordinator.py + test_error_handling.py).

---

## Implementation Strategy

### MVP First (US1 Only)

1. Complete Phase 1: Setup (verify baseline)
2. Complete Phase 2: US1 (calendar failure resilience)
3. **STOP and VALIDATE**: Test US1 independently — simulate
   calendar errors and verify graceful handling
4. The integration now survives the most common real-world fault

### Incremental Delivery

1. Setup → verify baseline
2. US1 → calendar resilience → verify → commit (MVP!)
3. US2 → lock slot bug fixes → verify → commit
4. US4 → lazy logging → verify → commit
5. US5 → code modernization → verify → commit (11 atomic commits)
6. US6 → config consistency → verify → commit
7. US3 → test coverage → verify → commit
8. Polish → final verification → done

### Atomic Commit Strategy

Each task maps to one atomic commit following the project
constitution (Conventional Commits, capitalized types, ≤50 char
subject, DCO sign-off, Co-authored-by trailer). See quickstart.md
Implementation Order tables for the exact commit type and scope
for each task.

---

## Notes

- [P] tasks = different files, no dependencies on incomplete tasks
- [Story] label maps task to specific user story for traceability
- Each user story should be independently completable and testable
- Commit after each task (atomic commits per constitution)
- Run `uv run pytest tests/ -x -q` after every commit
- Pre-commit hooks enforce ruff, mypy, interrogate, reuse — DO NOT
  use `--no-verify`
- All code changes modify existing files — no new source or test
  files are created
