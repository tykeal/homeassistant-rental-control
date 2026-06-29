<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Tasks: Decompose Calendar Sensor

**Input**: Design documents from `/specs/019-decompose-calsensor/`
**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅,
quickstart.md ✅

**Tests**: Included and required. This feature is a behavior-preserving
refactor of `custom_components/rental_control/sensors/calsensor.py`, so
existing sensor and integration tests remain the primary oracle. New focused
tests pin helper parity for description parsing, generated codes, attribute and
state rendering, read-only slot lookup, and compatibility seams.

**Organization**: Tasks follow the implementation order from PLAN and the
feature brief: baseline parity first, helper models and pure helpers next,
`calsensor.py` shell delegation and `_handle_coordinator_update` splitting,
`SlotAssignmentContext` parameter reduction, import and patch-seam verification,
maintainability gates, and final pytest/ruff/pre-commit acceptance. All
checkboxes remain unchecked until the implementation PR performs the work.

## Format: `- [ ] T### [P?] [Story?] Description with file path`

- **[P]**: Can run in parallel (different files, no dependency on incomplete
  tasks)
- **[Story]**: Which user story the task primarily proves (US1 through US4)
- Include exact file paths in descriptions
- Leave every checkbox unchecked until the implementation PR performs the task

## Path Conventions

- **Public shell**: `custom_components/rental_control/sensors/calsensor.py`
- **Production importer**: `custom_components/rental_control/sensor.py`
- **Extracted helper package**:
  `custom_components/rental_control/sensors/calsensor_helpers/`
- **Helper modules**:
  `custom_components/rental_control/sensors/calsensor_helpers/models.py`,
  `descriptions.py`, `codes.py`, `attributes.py`, `slots.py`, and `state.py`
- **Required calsensor compatibility surface**: `RentalControlCalSensor`,
  `get_slot_name`, `make_reservation_fingerprint`, `_handle_coordinator_update`,
  `_generate_door_code`, `_extract_email`, `_extract_phone_number`,
  `_extract_num_guests`, `_extract_last_four`, `_extract_url`,
  `_extract_booking_id`, `_extract_dynamic_attributes`,
  `_async_handle_slot_assignment`, `_event_attributes`, `_parsed_attributes`,
  `_code_generator`, `_code_length`, and `_event_number`
- **Patch-sensitive calsensor attributes**: `get_slot_name` and
  `make_reservation_fingerprint` must remain patchable at
  `custom_components.rental_control.sensors.calsensor`
- **Existing behavior-oracle tests**: `tests/unit/test_sensors.py`,
  `tests/integration/test_refresh_cycle.py`, and
  `tests/integration/test_checkin_tracking.py`
- **New focused tests**: `tests/unit/test_calsensor_descriptions.py`,
  `tests/unit/test_calsensor_codes.py`, and
  `tests/unit/test_calsensor_attributes.py`
- **Feature docs**: `specs/019-decompose-calsensor/`

## Live Module Transition Scope

Implementation changes the calendar-sensor feature only. The target split from
PLAN is:

- `custom_components/rental_control/sensors/calsensor.py` — public Home
  Assistant entity shell, class import location, module-level patch seams,
  constructor, entity properties, private wrapper methods, and the single final
  `async_write_ha_state()` per coordinator update.
- `custom_components/rental_control/sensors/calsensor_helpers/models.py` —
  internal dataclasses or equivalent typed containers for event attributes, ETA,
  parsed attributes, door-code requests, slot read contexts/results, render
  results, and `SlotAssignmentContext`.
- `custom_components/rental_control/sensors/calsensor_helpers/descriptions.py` —
  pure email, phone, guest-count, last-four, URL, booking ID, dynamic-field, and
  parsed-attribute helpers.
- `custom_components/rental_control/sensors/calsensor_helpers/codes.py` — pure
  generated door-code decisions for date-based, last-four, and static-random
  fallback behavior.
- `custom_components/rental_control/sensors/calsensor_helpers/attributes.py` —
  initial and no-reservation attributes, UID normalization, ETA snapshots, base
  event attributes, and parsed-attribute assembly.
- `custom_components/rental_control/sensors/calsensor_helpers/slots.py` —
  read-only slot name, reservation fingerprint, assignment, and code lookup using
  call-time dependency functions supplied by `calsensor.py`.
- `custom_components/rental_control/sensors/calsensor_helpers/state.py` — pure
  coordinator-update render orchestration that returns a result for the shell to
  assign.

No implementation task may add `aislop-ignore`, `aislop-ignore-file`, or an
equivalent complexity suppression to `calsensor.py` or any calendar-sensor
helper module. No helper may call Home Assistant services, mutate event
overrides, launch reconciliation, schedule async tasks, perform blocking I/O, or
write Home Assistant state.

---

## Phase 1: Setup & Baseline (Shared Infrastructure)

**Purpose**: Establish behavior, import, patch-site, caller, and complexity
baselines before moving any production code.

- [ ] T001 Run `.specify/scripts/bash/check-prerequisites.sh --json` with `SPECIFY_FEATURE=019-decompose-calsensor` from the repository root and confirm `specs/019-decompose-calsensor/` reports `research.md`, `data-model.md`, and `quickstart.md`
- [ ] T002 Inspect US1-US4, FR-001 through FR-015, edge cases, assumptions, non-goals, security considerations, and SC-001 through SC-010 in `specs/019-decompose-calsensor/spec.md`
- [ ] T003 Inspect the Project Structure, Concrete Decomposition Design, compatibility boundary, ground-truth call and patch analysis, helper split, slot-assignment reduction, and complexity notes in `specs/019-decompose-calsensor/plan.md`
- [ ] T004 Inspect all research decisions, all data-model helper entities, and quickstart parity steps in `specs/019-decompose-calsensor/research.md`, `specs/019-decompose-calsensor/data-model.md`, and `specs/019-decompose-calsensor/quickstart.md`
- [ ] T005 Inventory `RentalControlCalSensor.__init__`, `async_added_to_hass`, entity properties, `_generate_door_code`, all `_extract_*` helpers, `_handle_coordinator_update`, and `_async_handle_slot_assignment` in `custom_components/rental_control/sensors/calsensor.py`
- [ ] T006 Inventory the production `RentalControlCalSensor` import and four-argument construction path in `custom_components/rental_control/sensor.py`
- [ ] T007 Inventory direct imports, private method calls, calsensor module patches, read-only slot assertions, and the sole `_async_handle_slot_assignment` caller in `tests/unit/test_sensors.py`
- [ ] T008 Run unchanged baseline calsensor unit tests with `uv run pytest tests/unit/test_sensors.py -q` against `tests/unit/test_sensors.py`
- [ ] T009 Run unchanged baseline integration callers with `uv run pytest tests/integration/test_refresh_cycle.py tests/integration/test_checkin_tracking.py -q` against the listed integration files
- [ ] T010 Record the current line, function-length, and parameter-count baseline for `custom_components/rental_control/sensors/calsensor.py`, confirming the 537-line file, 147-line `_handle_coordinator_update`, seven-keyword-only-parameter `_async_handle_slot_assignment` plus `self`, and absence of a calsensor `aislop-ignore-file` directive

---

## Phase 2: Helper Models and Contexts (Priority: P1)

**Goal**: Introduce internal typed values that make later helper extraction
explicit while keeping the public calsensor shell unchanged.

**Independent Test**: Construct each model with representative event, parsed,
slot, code, render, and slot-assignment values and verify field values and
conversion helpers preserve current dictionary keys.

### Tests for Helper Models

- [ ] T011 [US1] Add event attribute, ETA, parsed attribute, door-code request, slot-read result, and render-result construction tests in `tests/unit/test_calsensor_attributes.py`
- [ ] T012 [US2] Add `SlotReadContext` and `SlotAssignmentContext` construction tests proving grouped slot inputs carry the existing legacy fields in `tests/unit/test_calsensor_attributes.py`

### Implementation for Helper Models

- [ ] T013 [US4] Create `custom_components/rental_control/sensors/calsensor_helpers/__init__.py` with SPDX headers, a module docstring, and internal helper-package exports as needed
- [ ] T014 [US4] Create `custom_components/rental_control/sensors/calsensor_helpers/models.py` with SPDX headers, a module docstring, `EventAttributeSnapshot`, `EtaSnapshot`, `ParsedReservationAttributes` or equivalent typed containers, and dictionary conversion helpers matching current `_event_attributes` and `_parsed_attributes` keys
- [ ] T015 [US4] Add `DoorCodeRequest`, `SlotReadContext`, `SlotReadResult`, `CalendarSensorRenderResult`, and `SlotAssignmentContext` or equivalent grouped values in `custom_components/rental_control/sensors/calsensor_helpers/models.py`
- [ ] T016 [US4] Ensure `custom_components/rental_control/sensors/calsensor_helpers/models.py` imports no runtime objects from `custom_components/rental_control/sensors/calsensor.py` and remains below 400 lines with no project-owned function over 80 lines or parameter list over six parameters
- [ ] T017 [US1] Run model validation with `uv run pytest tests/unit/test_calsensor_attributes.py -q` against `tests/unit/test_calsensor_attributes.py`

**Checkpoint**: Helper models prove the data ownership needed for FR-005,
FR-011, FR-012, SC-002, SC-006, and SC-007 before extraction begins.

---

## Phase 3: Pure Helper Extraction (Priority: P1)

**Goal**: Preserve pure parsing, code generation, attribute, slot-read, and state
rendering behavior in focused helpers before the public shell delegates to them.

**Independent Test**: Apply identical descriptions, event timestamps, UIDs,
coordinator slot state, prefixes, and code-generator settings to helper inputs
and compare outputs with the current `RentalControlCalSensor` behavior.

### Description Helpers

- [ ] T018 [P] [US1] Add description parser parity tests for email, phone, guest counts, last-four variants, URL, booking ID, dynamic fields, known-field skipping, URL skipping, slugification, and dedicated-field no-overwrite behavior in `tests/unit/test_calsensor_descriptions.py`
- [ ] T019 [US1] Create `custom_components/rental_control/sensors/calsensor_helpers/descriptions.py` with pure `extract_email`, `extract_phone_number`, `extract_num_guests`, `extract_last_four`, `extract_url`, `extract_booking_id`, `extract_dynamic_attributes`, and `build_parsed_attributes` behavior matching `custom_components/rental_control/sensors/calsensor.py`
- [ ] T020 [US1] Run description helper validation with `uv run pytest tests/unit/test_calsensor_descriptions.py tests/unit/test_sensors.py -q` against the listed test files

### Generated-Code Helpers

- [ ] T021 [P] [US1] Add generated-code parity tests for date-based truncation, zero-fill fallback, no-description date fallback, last-four only at length four, static-random UID determinism, static-random description fallback, empty UID normalization, and current global RNG behavior in `tests/unit/test_calsensor_codes.py`
- [ ] T022 [US1] Create `custom_components/rental_control/sensors/calsensor_helpers/codes.py` with pure generated-code behavior that preserves date digit ordering, `random.seed(seed)`, `randrange`, code-length handling, UID preference, and description fallback semantics
- [ ] T023 [US1] Run generated-code validation with `uv run pytest tests/unit/test_calsensor_codes.py tests/unit/test_sensors.py -q` against the listed test files

### Attribute and ETA Helpers

- [ ] T024 [US1] Add attribute parity tests for initial attributes, no-reservation attributes with and without event prefix, UID normalization, future and past ETA values, base event attributes, parsed attribute assembly, and exact exposed dictionary keys in `tests/unit/test_calsensor_attributes.py`
- [ ] T025 [US1] Create `custom_components/rental_control/sensors/calsensor_helpers/attributes.py` with initial/no-reservation builders, UID normalization, `datetime.now(start.tzinfo)` ETA calculation, base event snapshot building, and parsed attribute assembly matching current `calsensor.py`
- [ ] T026 [US1] Run attribute validation with `uv run pytest tests/unit/test_calsensor_attributes.py tests/unit/test_sensors.py -q` against the listed test files

### Read-Only Slot Helpers

- [ ] T027 [US2] Add read-only slot helper tests covering `get_slot_name` use, fingerprint skipping when `event_overrides` is absent, fingerprint skipping when slot name is `None`, assignment/code reads with the same identity key, generated-code fallback eligibility, and no calls to event-overrides mutation helpers in `tests/unit/test_calsensor_attributes.py`
- [ ] T028 [US2] Create `custom_components/rental_control/sensors/calsensor_helpers/slots.py` with slot-name calculation, reservation fingerprint preparation, coordinator `get_slot_assignment` and `get_slot_code` reads, and `SlotReadResult` output using calsensor-supplied dependency callables
- [ ] T029 [US2] Verify `custom_components/rental_control/sensors/calsensor_helpers/slots.py` does not import bound aliases for `get_slot_name` or `make_reservation_fingerprint` and does not call `async_reserve_or_get_slot`, `async_fire_set_code`, `async_fire_clear_code`, `async_fire_update_times`, or `hass.async_create_task`

### State Render Helpers

- [ ] T030 [US1] Add state render parity tests for event selection by sensor index, unsuccessful update passthrough inputs, state string formatting, slot-code fallback order, parsed attributes, no-reservation reset, and render-result dictionaries in `tests/unit/test_calsensor_attributes.py`
- [ ] T031 [US1] Create `custom_components/rental_control/sensors/calsensor_helpers/state.py` with pure render orchestration that composes attributes, descriptions, slots, and codes into a `CalendarSensorRenderResult` or equivalent without writing Home Assistant state
- [ ] T032 [US2] Verify `custom_components/rental_control/sensors/calsensor_helpers/state.py` performs no Home Assistant service calls, no coordinator refreshes, no reconciliation launches, no event-overrides mutation, no async task scheduling, and no Home Assistant state writes
- [ ] T033 [US4] Verify every file in `custom_components/rental_control/sensors/calsensor_helpers/` stays below 400 lines, every project-owned helper function stays below 80 lines, and every project-owned parameter list has no more than six parameters

**Checkpoint**: Pure helpers prove FR-005 through FR-010, FR-012 through
FR-014, SC-002 through SC-004, SC-006, SC-007, and SC-009 before shell wiring.

---

## Phase 4: calsensor Shell Delegation and Update Split (Priority: P1)

**Goal**: Slim `calsensor.py` into the Home Assistant entity shell while keeping
the class, properties, private wrappers, module patch seams, and state-write
semantics stable.

**Independent Test**: Run the current sensor tests plus focused helper tests and
compare state, attributes, parsed fields, slot values, generated codes, wrapper
return values, and `async_write_ha_state()` calls for identical coordinator data.

### Tests for Shell Delegation

- [ ] T034 [US3] Add wrapper parity tests proving `_extract_email`, `_extract_phone_number`, `_extract_num_guests`, `_extract_last_four`, `_extract_url`, `_extract_booking_id`, and `_extract_dynamic_attributes` still read from `self._event_attributes["description"]` in `tests/unit/test_sensors.py`
- [ ] T035 [US1] Add `_generate_door_code` wrapper tests proving the method uses current `_code_generator`, `_code_length`, `_event_attributes`, and last-four helper behavior in `tests/unit/test_sensors.py`
- [ ] T036 [US1] Add coordinator-update shell tests proving unsuccessful updates, successful event renders, no-reservation resets, code-generator refresh, and exactly one final `async_write_ha_state()` per invocation in `tests/unit/test_sensors.py`

### Implementation for Shell Delegation

- [ ] T037 [US3] Update `custom_components/rental_control/sensors/calsensor.py` imports to use `calsensor_helpers` modules while keeping module-level `get_slot_name` and `make_reservation_fingerprint` visible at the calsensor module path
- [ ] T038 [US3] Replace private description extractor bodies in `custom_components/rental_control/sensors/calsensor.py` with thin wrappers over `custom_components/rental_control/sensors/calsensor_helpers/descriptions.py`
- [ ] T039 [US1] Replace `_generate_door_code` in `custom_components/rental_control/sensors/calsensor.py` with a thin wrapper over `custom_components/rental_control/sensors/calsensor_helpers/codes.py` while preserving current fallback behavior and RNG side effects
- [ ] T040 [US1] Update `RentalControlCalSensor.__init__` and no-reservation reset paths in `custom_components/rental_control/sensors/calsensor.py` to use helper-built event attributes without changing initial state, `_event_attributes`, `_parsed_attributes`, or `_state` values
- [ ] T041 [US1] Split successful event rendering from `_handle_coordinator_update` in `custom_components/rental_control/sensors/calsensor.py` into short private shell helpers that refresh code settings, delegate to `state.py`, assign render results, and preserve current debug logging
- [ ] T042 [US2] Ensure the shell passes current calsensor module `get_slot_name` and `make_reservation_fingerprint` callables into slot/state helpers at coordinator-update call time in `custom_components/rental_control/sensors/calsensor.py`
- [ ] T043 [US1] Reduce `_handle_coordinator_update` in `custom_components/rental_control/sensors/calsensor.py` below 80 lines while preserving unsuccessful-update passthrough, event-index selection, no-reservation reset, and the single final `async_write_ha_state()` call
- [ ] T044 [US1] Run shell delegation validation with `uv run pytest tests/unit/test_sensors.py tests/unit/test_calsensor_descriptions.py tests/unit/test_calsensor_codes.py tests/unit/test_calsensor_attributes.py -q` against the listed test files

**Checkpoint**: Shell delegation proves FR-001 through FR-008, FR-014, SC-001
through SC-005, and SC-009 with the public entity still hosted by `calsensor.py`.

---

## Phase 5: SlotAssignmentContext Reduction (Priority: P1)

**Goal**: Bring the deprecated no-op slot-assignment shim under the parameter
threshold without reintroducing scheduling or mutation behavior.

**Independent Test**: Invoke `_async_handle_slot_assignment` with the grouped
context and verify it returns `None`, remains async, and does not call
reservation, Keymaster, event-overrides, or task-scheduling helpers.

- [ ] T045 [US2] Update `tests/unit/test_sensors.py::test_async_handle_slot_assignment_is_noop` to import and construct `SlotAssignmentContext` from `custom_components/rental_control/sensors/calsensor_helpers/models.py`
- [ ] T046 [US2] Update `RentalControlCalSensor._async_handle_slot_assignment` in `custom_components/rental_control/sensors/calsensor.py` to accept `context: SlotAssignmentContext` and keep the method as a harmless async no-op
- [ ] T047 [US2] Verify `custom_components/rental_control/sensors/calsensor.py` contains no production call to `_async_handle_slot_assignment` and no coordinator update path schedules or awaits slot-assignment work
- [ ] T048 [US2] Run no-op slot shim validation with `uv run pytest tests/unit/test_sensors.py::TestSensorReadOnly::test_async_handle_slot_assignment_is_noop -q` against `tests/unit/test_sensors.py`

**Checkpoint**: SlotAssignmentContext proves FR-004, FR-011, FR-012, SC-006,
and SC-007 without adding `*args`, `**kwargs`, or an Aislop directive.

---

## Phase 6: Import and Patch-Seam Verification (Priority: P1)

**Goal**: Preserve production imports, visible and hidden monkeypatch boundaries,
and private compatibility seams after helper delegation.

**Independent Test**: Import the calsensor module, patch calsensor module-level
slot helpers, instantiate through `sensor.py`, and verify runtime calls observe
patched objects without requiring callers to import helper modules.

### Tests for Compatibility Seams

- [ ] T049 [US3] Add calsensor import-surface tests proving `RentalControlCalSensor`, `get_slot_name`, and `make_reservation_fingerprint` remain importable from `custom_components.rental_control.sensors.calsensor` in `tests/unit/test_sensors.py`
- [ ] T050 [US3] Add private method presence tests for `_handle_coordinator_update`, `_generate_door_code`, all description extractor wrappers, `async_added_to_hass`, and `_async_handle_slot_assignment` on `RentalControlCalSensor` in `tests/unit/test_sensors.py`
- [ ] T051 [US3] Add production import-boundary tests proving `custom_components/rental_control/sensor.py` continues to import `RentalControlCalSensor` from `custom_components.rental_control.sensors.calsensor` and construct it with `hass`, `coordinator`, `sensor_name`, and `event_number` in `tests/unit/test_sensors.py`
- [ ] T052 [US3] Add patch-seam tests proving patches to `custom_components.rental_control.sensors.calsensor.get_slot_name` and `custom_components.rental_control.sensors.calsensor.make_reservation_fingerprint` intercept the coordinator-update slot path in `tests/unit/test_sensors.py`

### Implementation Verification

- [ ] T053 [US3] Verify no production caller in `custom_components/rental_control/sensor.py` or `custom_components/rental_control/sensors/**/*.py` imports `RentalControlCalSensor` from helper modules instead of `custom_components/rental_control/sensors/calsensor.py`
- [ ] T054 [US3] Verify helper imports in `custom_components/rental_control/sensors/calsensor_helpers/*.py` do not create a public API dependency or bypass the calsensor module patch seams
- [ ] T055 [US3] Run import and patch-seam validation with `uv run pytest tests/unit/test_sensors.py tests/integration/test_refresh_cycle.py tests/integration/test_checkin_tracking.py -q` against the listed files

**Checkpoint**: Compatibility verification proves FR-003, FR-004, FR-009,
FR-010, SC-001, SC-005, and SC-006 before maintainability cleanup.

---

## Phase 7: Maintainability, File Sizes, and Aislop Gates (Priority: P2)

**Goal**: Resolve active calendar-sensor complexity findings without
suppressions, catch-all modules, or behavior changes.

**Independent Test**: Measure every calendar-sensor file after final shell wiring
and run existing complexity tooling with no new Aislop directive.

### Cleanup and Complexity Gates

- [ ] T056 [US4] Confirm final implementation diff is limited to `custom_components/rental_control/sensors/calsensor.py`, `custom_components/rental_control/sensors/calsensor_helpers/*.py`, and directly required test files under `tests/unit/` or `tests/integration/`
- [ ] T057 [US4] Remove temporary extraction shims from `custom_components/rental_control/sensors/calsensor.py` and `custom_components/rental_control/sensors/calsensor_helpers/*.py`, leaving only planned wrappers, helper exports, typed context values, and focused helper functions
- [ ] T058 [US4] Confirm no new lock-code business rules, reservation parsing semantics, slot-assignment behavior, sensors, services, Store authority, Home Assistant state writes, coordinator refreshes, reconciliation launches, blocking I/O, async tasks, diagnostics fields, or user-visible delays were introduced in calendar-sensor files
- [ ] T059 [US4] Measure `custom_components/rental_control/sensors/calsensor.py` and `custom_components/rental_control/sensors/calsensor_helpers/*.py` with `wc -l` and confirm every calendar-sensor-related file is below 400 lines
- [ ] T060 [US4] Ensure every project-owned function in `custom_components/rental_control/sensors/calsensor.py` and `custom_components/rental_control/sensors/calsensor_helpers/*.py` is below 80 lines, splitting helper stages without changing behavior where needed
- [ ] T061 [US4] Ensure every project-owned parameter list in `custom_components/rental_control/sensors/calsensor.py` and `custom_components/rental_control/sensors/calsensor_helpers/*.py` has no more than six parameters unless an external framework signature requires otherwise
- [ ] T062 [US4] Verify no `aislop-ignore`, `aislop-ignore-file`, or equivalent complexity suppression was added to `custom_components/rental_control/sensors/calsensor.py` or `custom_components/rental_control/sensors/calsensor_helpers/*.py`
- [ ] T063 [US4] Run isolated complexity validation with `uv run pre-commit run aislop` and confirm file-size, function-length, and parameter-count thresholds pass for the calendar-sensor decomposition

**Checkpoint**: Maintainability proves FR-012, FR-013, FR-014, SC-007,
SC-008, and SC-009.

---

## Phase 8: Polish & Cross-Cutting Acceptance Gates

**Purpose**: Verify behavior parity, caller compatibility, quality gates,
traceability, and implementation notes before the runtime refactor is complete.

### Acceptance and Quality Gates

- [ ] T064 Run unchanged calsensor parity tests with `uv run pytest tests/unit/test_sensors.py -x -q` against `tests/unit/test_sensors.py`
- [ ] T065 Run all new focused helper tests with `uv run pytest tests/unit/test_calsensor_descriptions.py tests/unit/test_calsensor_codes.py tests/unit/test_calsensor_attributes.py -q` against the listed test files
- [ ] T066 Run integration caller parity tests with `uv run pytest tests/integration/test_refresh_cycle.py tests/integration/test_checkin_tracking.py -x -q` against the listed integration files
- [ ] T067 Run full regression tests with `uv run pytest tests/ -x -q` against `tests/`
- [ ] T068 Run linting with `uv run ruff check custom_components/ tests/` against `custom_components/` and `tests/`
- [ ] T069 Run full pre-commit validation with `uv run pre-commit run --all-files` against repository-tracked files, including reuse, yamllint, actionlint, aislop, ruff, ruff-format, mypy, interrogate, and gitlint hooks
- [ ] T070 Verify every FR-001 through FR-015 has a test, implementation, or acceptance task mapped in `specs/019-decompose-calsensor/tasks.md`
- [ ] T071 Verify every SC-001 through SC-010 has a test, implementation, or acceptance task mapped in `specs/019-decompose-calsensor/tasks.md`
- [ ] T072 Review `specs/019-decompose-calsensor/quickstart.md` and confirm the implementation PR notes list unchanged parity commands, new focused helper commands, import and patch-seam results, read-only slot safeguards, file-size measurements, final `aislop` results, full `pytest tests/ -x -q`, ruff, and pre-commit results

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup & Baseline (Phase 1)**: No dependencies.
- **Helper Models and Contexts (Phase 2)**: Depends on Setup because model shapes
  must reflect the live source and tests.
- **Pure Helper Extraction (Phase 3)**: Depends on models where grouped requests
  or render results are used. Descriptions, codes, and attribute tests can start
  after baseline; `state.py` depends on descriptions, codes, attributes, and
  slots.
- **calsensor Shell Delegation (Phase 4)**: Depends on pure helpers being present
  so shell methods can become wrappers and `_handle_coordinator_update` can be
  split below 80 lines.
- **SlotAssignmentContext Reduction (Phase 5)**: Depends on `models.py` and can be
  completed after or alongside shell delegation, but before parameter-count gates.
- **Import and Patch-Seam Verification (Phase 6)**: Depends on shell delegation
  and slot helper dependency wiring.
- **Maintainability (Phase 7)**: Depends on all extraction, wrapper wiring,
  compatibility verification, and shim cleanup.
- **Polish (Phase 8)**: Depends on all desired extraction, wrapper,
  compatibility, and cleanup phases.

### User Story Dependencies

- **US1 (P1)**: State and attribute parity starts with baseline tests and drives
  descriptions, codes, attributes, state rendering, and shell delegation.
- **US2 (P1)**: Read-only slot behavior starts with baseline tests and is proven
  by `slots.py`, `state.py`, and `SlotAssignmentContext` verification before final
  gates.
- **US3 (P1)**: Compatibility surface depends on shell delegation and completes
  with import, private seam, production importer, and patch-target tests.
- **US4 (P2)**: Maintainability follows helper extraction and shell wiring because
  file/function/parameter thresholds are meaningful only after the split.

### Within Each Story

- Focused tests are written before the corresponding helper extraction tasks and
  should fail or expose missing coverage until the extraction lands.
- `models.py` precedes helpers that need grouped requests or render results.
- `descriptions.py`, `codes.py`, `attributes.py`, and `slots.py` precede
  `state.py` render orchestration.
- `calsensor.py` keeps private wrappers and module-level patch seams throughout
  extraction; helper modules do not become production import targets.
- `SlotAssignmentContext` test caller changes happen with the signature change,
  not before the context exists.
- File-size measurement and `uv run pre-commit run aislop` happen after temporary
  shims are removed and before final full gates.
- No calendar-sensor `aislop-ignore` or equivalent directive may be added at any
  point.

---

## Parallel Opportunities

- T018 and T021 can be developed in parallel after baselines because
  description and generated-code tests live in separate files.
- Attribute and slot tests in `tests/unit/test_calsensor_attributes.py` should be
  sequenced within one working copy to avoid same-file collisions.
- T019, T022, T025, and T028 can be developed in parallel after model shapes are
  stable because they create different helper modules.
- Shell tests in `tests/unit/test_sensors.py` should be sequenced within one
  working copy because wrapper, generated-code, coordinator-update, import,
  private-method, and production-import assertions edit the same file.
- T064, T065, and T066 can run independently once implementation is complete;
  T067 through T069 are final serial quality gates.

## Parallel Example: Helper Parity After Models

```bash
Task: "Add description parser parity tests in tests/unit/test_calsensor_descriptions.py"
Task: "Add generated-code parity tests in tests/unit/test_calsensor_codes.py"
Task: "Add read-only slot helper tests in tests/unit/test_calsensor_attributes.py"
```

---

## Implementation Strategy

### MVP First (Behavior Parity and Safety)

1. Complete Phase 1 baselines.
2. Add helper models and focused tests for exact current values.
3. Extract pure helpers in the order descriptions, codes, attributes, slots, and
   state.
4. Convert `calsensor.py` to thin wrappers and split `_handle_coordinator_update`
   while preserving one state write.
5. Reduce `_async_handle_slot_assignment` with `SlotAssignmentContext` and verify
   it remains a no-op.
6. Verify import and patch seams before claiming maintainability fixes.

### Incremental Delivery

1. Build `calsensor_helpers/models.py` and keep it internal.
2. Build parser, generated-code, attribute, slot, and state helpers with focused
   parity tests.
3. Slim `calsensor.py` into the compatibility shell with wrapper methods and
   call-time patch dependencies.
4. Update only the sole no-op shim test caller for `SlotAssignmentContext`.
5. Remove temporary shims, measure every calendar-sensor file below 400 lines,
   run `aislop` with no suppression, and then run full pytest, ruff, and
   pre-commit gates.

---

## Acceptance Coverage Map

| Coverage item | Primary tasks |
|---------------|---------------|
| US1 Preserve calendar sensor state | T005, T008-T010, T011, T018-T026, T030-T044, T064-T067 |
| US2 Preserve read-only slot behavior | T007-T009, T012, T027-T029, T032, T042, T045-T048, T055, T066-T067 |
| US3 Preserve calsensor compatibility surface | T006-T007, T034-T038, T049-T055, T064-T067, T070 |
| US4 Improve maintainability under Aislop limits | T010, T013-T016, T033, T043, T056-T063, T068-T069 |
| FR-001 observable behavior unchanged | T008-T009, T024-T026, T030-T044, T064-T067 |
| FR-002 existing tests and parity tests | T008-T009, T018, T021, T024, T027, T030, T064-T067 |
| FR-003 production import retained | T006, T037, T049-T055, T070 |
| FR-004 test compatibility surface retained | T007, T034-T038, T045-T052, T055, T064 |
| FR-005 coordinator update equivalence | T005, T030-T036, T040-T044, T064-T067 |
| FR-006 ETA semantics | T024-T026, T030-T031, T064-T065 |
| FR-007 door-code generation | T021-T023, T035, T039, T064-T065 |
| FR-008 description parsing | T018-T020, T034, T038, T064-T065 |
| FR-009 read-only reconciliation integration | T027-T029, T032, T042, T047, T055, T066 |
| FR-010 fingerprint compatibility | T027-T029, T042, T052, T055, T066 |
| FR-011 slot-assignment context | T012, T015, T045-T048, T061 |
| FR-012 complexity thresholds | T010, T016, T033, T043, T059-T063, T069 |
| FR-013 no Aislop suppression | T010, T033, T062-T063, T069 |
| FR-014 no new side effects | T029, T032, T036, T047, T058, T064-T069 |
| FR-015 behavior-preserving docs | T002-T004, T058, T070-T072 |
| SC-001 existing tests green | T008-T009, T044, T055, T064, T066-T067 |
| SC-002 identical event outputs | T011, T024-T026, T030-T044, T064-T065 |
| SC-003 no-event and unsuccessful parity | T024-T026, T030-T036, T040-T044, T064 |
| SC-004 generated-code parity | T021-T023, T035, T039, T064-T065 |
| SC-005 imports and calls unchanged | T006-T007, T049-T055, T070 |
| SC-006 no-op shim and no mutation | T027-T029, T045-T048, T055, T066 |
| SC-007 file/function/parameter limits | T016, T033, T043, T059-T063, T069 |
| SC-008 no complexity directive | T010, T062-T063, T069 |
| SC-009 no added hot-path work | T029, T032, T036, T047, T058, T064-T069 |
| SC-010 docs-only tasks stage | This `tasks.md` PR only; implementation tasks start unchecked |
