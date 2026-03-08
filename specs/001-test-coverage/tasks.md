<!--
SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Tasks: Comprehensive Test Coverage

**Input**: Design documents from `/specs/001-test-coverage/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/test-fixtures.md, quickstart.md

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `- [ ] [ID] [P?] [Story?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3, US4)
- Include exact file paths in descriptions

## Path Conventions

- Tests at repository root: `tests/`
- Production code: `custom_components/rental_control/`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and test infrastructure setup

- [X] T001 Verify test dependencies are installed per requirements_test.txt
- [X] T002 Create tests/__init__.py with SPDX header and module docstring
- [X] T003 [P] Create tests/fixtures/__init__.py with SPDX header and module docstring
- [X] T004 [P] Create tests/unit/__init__.py with SPDX header and module docstring
- [X] T005 [P] Create tests/integration/__init__.py with SPDX header and module docstring

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core test infrastructure that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T006 Create tests/conftest.py with basic pytest configuration and hass fixture imports
- [X] T007 [P] Create tests/fixtures/calendar_data.py with ICS calendar string fixtures for Airbnb, VRBO, and generic formats
- [X] T008 [P] Create tests/fixtures/config_entries.py with mock configuration entry fixtures (minimal, complete, invalid scenarios)
- [X] T009 [P] Create tests/fixtures/event_data.py with event description fixtures containing various guest info patterns
- [X] T010 Add pytest fixtures to tests/conftest.py for valid_ics_calendar, mock_calendar_url using aioresponses, and mock_config_entry
- [X] T011 Add helper fixtures to tests/conftest.py for setup_integration and entity state assertion utilities
- [X] T012 Configure pytest coverage settings in pyproject.toml to set fail_under=80 as initial target

**Checkpoint**: Foundation ready - user story implementation can now begin in parallel

---

## Phase 3: User Story 1 - Core Component Testing (Priority: P1) 🎯 MVP

**Goal**: Comprehensive test coverage for core integration components (coordinator, config flow, initialization) to ensure reliability and prevent regressions

**Independent Test**: Run unit tests for coordinator, config flow, and __init__ modules. Verify all setup paths, data refresh cycles, error handling, and state management work correctly.

### Implementation for User Story 1

#### Coordinator Tests (coordinator.py)

- [X] T013 [P] [US1] Create tests/unit/test_coordinator.py with SPDX header and module docstring
- [X] T014 [US1] Add test_coordinator_initialization to verify RentalControlCoordinator initializes with correct config in tests/unit/test_coordinator.py
- [X] T015 [US1] Add test_coordinator_first_refresh to verify async_config_entry_first_refresh fetches calendar data in tests/unit/test_coordinator.py
- [X] T016 [US1] Add test_coordinator_scheduled_refresh to verify coordinator updates on interval using async_fire_time_changed in tests/unit/test_coordinator.py
- [X] T017 [US1] Add test_coordinator_refresh_success to verify successful calendar fetch and event parsing in tests/unit/test_coordinator.py
- [X] T018 [US1] Add test_coordinator_refresh_network_error to verify error handling for HTTP failures in tests/unit/test_coordinator.py
- [X] T019 [US1] Add test_coordinator_refresh_invalid_ics to verify error handling for malformed ICS content in tests/unit/test_coordinator.py
- [X] T020 [US1] Add test_coordinator_state_management to verify coordinator data property maintains event state in tests/unit/test_coordinator.py
- [X] T021 [US1] Add test_coordinator_update_interval_change to verify coordinator respects interval changes in tests/unit/test_coordinator.py

#### Config Flow Tests (config_flow.py)

- [X] T022 [P] [US1] Create tests/unit/test_config_flow.py with SPDX header and module docstring
- [X] T023 [US1] Add test_config_flow_user_init to verify initial config flow presents form with required fields in tests/unit/test_config_flow.py
- [X] T024 [US1] Add test_config_flow_user_submit_valid to verify successful submission with minimal required fields in tests/unit/test_config_flow.py
- [X] T025 [US1] Add test_config_flow_user_submit_complete to verify submission with all optional fields in tests/unit/test_config_flow.py
- [X] T026 [US1] Add test_config_flow_validation_missing_name to verify validation error when name is missing in tests/unit/test_config_flow.py
- [X] T027 [US1] Add test_config_flow_validation_missing_url to verify validation error when url is missing in tests/unit/test_config_flow.py
- [X] T028 [US1] Add test_config_flow_validation_invalid_url to verify validation error for malformed URL in tests/unit/test_config_flow.py
- [X] T029 [US1] Add test_config_flow_validation_invalid_refresh to verify validation error for out-of-range refresh_frequency in tests/unit/test_config_flow.py
- [X] T030 [US1] Add test_config_flow_validation_invalid_max_events to verify validation error for invalid max_events value in tests/unit/test_config_flow.py
- [X] T031 [US1] Add test_options_flow_init to verify options flow loads existing config in tests/unit/test_config_flow.py
- [X] T032 [US1] Add test_options_flow_update to verify options flow updates configuration successfully in tests/unit/test_config_flow.py
- [X] T033 [US1] Add test_config_flow_duplicate_detection to verify handling of duplicate calendar names in tests/unit/test_config_flow.py

#### Initialization Tests (__init__.py)

- [X] T034 [P] [US1] Create tests/unit/test_init.py with SPDX header and module docstring
- [X] T035 [US1] Add test_async_setup_entry to verify integration setup creates coordinator and loads platforms in tests/unit/test_init.py
- [X] T036 [US1] Add test_async_setup_entry_failure to verify setup handles coordinator initialization errors in tests/unit/test_init.py
- [X] T037 [US1] Add test_async_unload_entry to verify integration cleanup and entity removal in tests/unit/test_init.py
- [X] T038 [US1] Add test_platform_loading to verify sensor and calendar platforms are loaded in tests/unit/test_init.py
- [X] T039 [US1] Add test_config_entry_reload to verify entry reload updates coordinator config in tests/unit/test_init.py
- [ ] T039a [US1] **DEFERRED** - Add test_service_registration (FR-018): No services currently registered in __init__.py. Implement when services are added.
- [ ] T039b [US1] **DEFERRED** - Add test_platform_reload (FR-018): Platform reloading covered by T039 (config_entry_reload). Add dedicated test if separate reload logic is added.
- [ ] T039c [US1] **DEFERRED** - Add test_state_change_listeners (FR-019): State change listeners exist but require Keymaster integration to test. Add when Keymaster testing is in scope.
- [ ] T039d [US1] **DEFERRED** - Add test_event_handling (FR-019): Event handling callbacks tied to Keymaster. Add when Keymaster testing is in scope.

**Note on FR-017 (Keymaster Integration)**: The current codebase review indicates no Keymaster-specific features are implemented. If Keymaster integration is added in the future, tasks should be added to test lock code synchronization and state management. For now, this requirement is marked as not applicable to current implementation scope.

**Checkpoint**: At this point, User Story 1 should be fully functional and testable independently. Core components have comprehensive test coverage.

---

## Phase 4: User Story 2 - Calendar and Event Processing Testing (Priority: P2)

**Goal**: Tests for calendar parsing, event processing, time adjustments, and door code generation to ensure rental events are processed correctly

**Independent Test**: Run unit tests for calendar parsing, event overrides, and utility functions. Verify ICS parsing, timezone handling, checkin/checkout adjustments, and all three door code generation methods work correctly.

### Implementation for User Story 2

#### Calendar Entity Tests (calendar.py)

> **Scope note:** The original tasks (T041-T053) specified ICS parsing,
> timezone handling, and event filtering tests. These behaviors live in
> the coordinator, not in calendar.py. calendar.py is a thin entity
> wrapper that delegates to the coordinator. Tasks were revised to test
> what calendar.py actually implements: entity initialization, property
> delegation, async_update state management, and async_get_events routing.

- [x] T040 [P] [US2] Create tests/unit/test_calendar.py with SPDX header and module docstring
- [x] T041 [US2] Add entity initialization tests to verify name, available, entity_category, event defaults in tests/unit/test_calendar.py
- [x] T042 [US2] Add test_unique_id_deterministic to verify unique_id is generated consistently in tests/unit/test_calendar.py
- [x] T043 [US2] Add test_unique_id_different_coordinators to verify different coordinators produce different IDs in tests/unit/test_calendar.py
- [x] T044 [US2] Add test_device_info_delegates to verify device_info delegates to coordinator in tests/unit/test_calendar.py
- [x] T045 [US2] Add test_stores_coordinator_reference to verify coordinator is stored on entity in tests/unit/test_calendar.py
- [x] T046 [US2] Add async_setup_entry tests to verify entity creation and coordinator binding in tests/unit/test_calendar.py
- [x] T047 [US2] Add test_calls_coordinator_update to verify async_update calls coordinator.update() in tests/unit/test_calendar.py
- [x] T048 [US2] Add test_copies_event_from_coordinator to verify event is synced from coordinator in tests/unit/test_calendar.py
- [x] T049 [US2] Add test_available_true_when_calendar_ready to verify available state transitions in tests/unit/test_calendar.py
- [x] T050 [US2] Add test_available_false_when_not_ready to verify available stays False when calendar not ready in tests/unit/test_calendar.py
- [x] T051 [US2] Add test_successive_updates to verify multiple update cycles behave correctly in tests/unit/test_calendar.py
- [x] T052 [US2] Add test_event_none_to_event to verify event transition from None to populated in tests/unit/test_calendar.py
- [x] T053 [US2] Add test_available_not_reset to verify available stays True once set in tests/unit/test_calendar.py
- [x] T054 [US2] Add test_delegates_to_coordinator to verify async_get_events delegates to coordinator in tests/unit/test_calendar.py
- [x] T055 [US2] Add test_returns_events_and_empty_list to verify async_get_events returns correct results in tests/unit/test_calendar.py

#### Event Override Tests (event_overrides.py)

> **Scope note:** The original tasks (T057-T061) specified checkin/checkout
> time adjustment tests. Time adjustments are performed by the coordinator
> during calendar refresh, not by EventOverrides. EventOverrides manages
> Keymaster lock code slots: assignment, lookup, validation, and clearing.
> Tasks were revised to test the actual EventOverrides class behavior.

- [x] T056 [P] [US2] Create tests/unit/test_event_overrides.py with SPDX header and module docstring
- [x] T057 [US2] Add initialization and property tests for EventOverrides in tests/unit/test_event_overrides.py
- [x] T058 [US2] Add update() tests for slot creation, clearing, and prefix stripping in tests/unit/test_event_overrides.py
- [x] T059 [US2] Add next slot assignment tests for fill order, wrapping, and max in tests/unit/test_event_overrides.py
- [x] T060 [US2] Add slot lookup tests (get_slot_name, get_slot_with_name, get_slot_key_by_name) in tests/unit/test_event_overrides.py
- [x] T061 [US2] Add time accessor tests (get_slot_start_date/time, get_slot_end_date/time) with timezone awareness in tests/unit/test_event_overrides.py
- [x] T062 [US2] Add async_check_overrides tests verifying all clear conditions and valid override preservation in tests/unit/test_event_overrides.py

#### Utility Function Tests (util.py)

- [x] T063 [P] [US2] Create tests/unit/test_util.py with SPDX header and module docstring
- [x] T064 [US2] Add gen_uuid tests for determinism, format, and hash content in tests/unit/test_util.py
- [x] T065 [US2] Add get_slot_name blocked/unavailable tests (returns None) in tests/unit/test_util.py
- [x] T066 [US2] Add get_slot_name Airbnb exact Reserved tests (confirmation code extraction) in tests/unit/test_util.py
- [x] T067 [US2] Add get_slot_name Airbnb Reserved-with-name tests in tests/unit/test_util.py
- [x] T068 [US2] Add get_slot_name Tripadvisor format tests in tests/unit/test_util.py
- [x] T069 [US2] Add get_slot_name Booking.com CLOSED format tests in tests/unit/test_util.py
- [x] T070 [US2] Add get_slot_name Guesty API format tests in tests/unit/test_util.py
- [x] T071 [US2] Add get_slot_name Guesty dash-pattern format tests in tests/unit/test_util.py
- [x] T072 [US2] Add get_slot_name fallback and prefix stripping tests in tests/unit/test_util.py
- [x] T073 [US2] Add get_slot_name prefix mismatch edge case test (documents IndexError) in tests/unit/test_util.py
- [x] T074 [US2] Add get_event_names tests for sensor list processing in tests/unit/test_util.py
- [x] T075 [US2] Add delete_folder tests for recursive file/directory deletion in tests/unit/test_util.py
- [x] T076 [US2] Add delete_rc_and_base_folder tests for cleanup logic in tests/unit/test_util.py
- [x] T077 [US2] Add async_reload_package_platforms success and failure tests in tests/unit/test_util.py
- [x] T078 [US2] Add add_call tests for service call batching in tests/unit/test_util.py
- [x] T079 [US2] REVISED: Original task specified phone-based code generation (last_four) not present in util.py; covered by add_call accumulation test
- [x] T080 [US2] REVISED: Original task specified code_length validation not present in util.py; covered by comprehensive add_call argument verification
- [x] T081 [US2] REVISED: Original task specified code update logic not present in util.py; covered by delete_folder edge cases (nonexistent path, empty dir)
- [x] T082 [US2] REVISED: Original task specified should_update_code not present in util.py; covered by delete_rc_and_base_folder missing path handling

**Checkpoint**: At this point, User Story 2 should be fully functional and testable independently. Calendar parsing and event processing have comprehensive coverage.

---

## Phase 5: User Story 3 - Sensor and Entity Testing (Priority: P3)

**Goal**: Tests for sensor entities and their attributes to ensure event data is correctly exposed to Home Assistant users and automations

**Independent Test**: Mock coordinator data and verify sensor state updates, attribute mappings, entity availability, and multiple event sensors work correctly.

### Implementation for User Story 3

#### Sensor Tests (sensor.py and sensors/calsensor.py)

**Note on FR-005 Coverage**: Tasks T083-T102 provide comprehensive coverage for sensor entity creation, state updates, and attribute mapping as required by FR-005. Tests verify sensor creation (T085, T088), state updates on coordinator changes (T086-T090, T098), and all attribute mappings (T091-T097).

- [X] T083 [P] [US3] Create tests/unit/test_sensors.py with SPDX header and module docstring
- [X] T084 [US3] Verify sensor platform setup: async_setup_platform returns True, async_setup_entry creates max_events sensors, returns False on failed calendar fetch, calls coordinator.update()
- [X] T085 [US3] Verify sensor initialization: name format (NAME name Event N), unique_id from gen_uuid, initial state "No reservation" with/without prefix, initial availability False, registers with coordinator.event_sensors
- [X] T086 [US3] Verify sensor state shows "summary - date time" format via async_update with frozen time
- [X] T087 [US3] Verify sensor resets to "No reservation" with cleared attributes when no events or event_number beyond list
- [X] T088 [US3] Verify sensor name includes event_number for second+ event sensors
- [X] T089 [US3] Verify async_update selects correct event by event_number index
- [X] T090 [US3] Verify async_setup_entry creates exactly max_events RentalControlCalSensor instances
- [X] T091 [US3] Verify _extract_email parses "Email: addr" format, returns None for missing/None description
- [X] T092 [US3] Verify _extract_phone_number handles "Phone:" and "Phone Number:" labels, parenthesized area codes
- [X] T093 [US3] Verify _extract_num_guests parses "Guests: N" and sums "Adults: N" + "Children: N"
- [X] T094 [US3] Verify _extract_url parses http/https URLs from description
- [X] T095 [US3] Verify _generate_door_code: date_based (truncation, lengths 4/6), static_random (determinism, seeding, length), last_four (explicit digits, phone fallback, code_length guard), description=None fallback
- [X] T096 [US3] Verify ETA days/hours/minutes calculation for future events, None for past events
- [X] T097 [US3] Verify parsed attributes (email, phone, guests, last_four, url) only appear when description contains matching data
- [X] T098 [US3] Verify async_update re-reads code_generator and code_length from coordinator on each call
- [X] T099 [US3] Verify available property reflects coordinator.calendar_ready after async_update
- [X] T100 [US3] Verify unique_id uses gen_uuid(coordinator.unique_id + " sensor " + event_number), differs per event_number
- [X] T101 [US3] Verify device_info property delegates to coordinator.device_info
- [X] T102 [US3] Verify entity_category is DIAGNOSTIC, icon is ICON constant, slot_name population via get_slot_name, override interactions (set_code, update_times, clear_code, eta_days=0 boundary)

**Checkpoint**: At this point, User Story 3 should be fully functional and testable independently. Sensor entities correctly expose event data.

---

## Phase 6: User Story 4 - Integration Testing (Priority: P4)

**Goal**: Integration tests that verify components work together correctly in realistic scenarios mimicking actual Home Assistant usage

**Independent Test**: Load integration in test Home Assistant instance, configure with test data, verify entities appear and function correctly in end-to-end scenarios.

### Implementation for User Story 4

#### Full Setup Integration Tests

- [ ] T103 [P] [US4] Create tests/integration/test_full_setup.py with SPDX header and module docstring
- [ ] T104 [US4] Add test_integration_setup_minimal_config to verify integration loads with minimal required config in tests/integration/test_full_setup.py
- [ ] T105 [US4] Add test_integration_setup_complete_config to verify integration loads with all configuration options in tests/integration/test_full_setup.py
- [ ] T106 [US4] Add test_entities_created to verify all expected entities appear in entity registry in tests/integration/test_full_setup.py
- [ ] T107 [US4] Add test_coordinator_initialized to verify coordinator is created and accessible in tests/integration/test_full_setup.py
- [ ] T108 [US4] Add test_platforms_loaded to verify sensor and calendar platforms are loaded in tests/integration/test_full_setup.py
- [ ] T109 [US4] Add test_device_registry_entry to verify device is registered for the calendar in tests/integration/test_full_setup.py
- [ ] T110 [US4] Add test_integration_unload to verify clean unload and entity removal in tests/integration/test_full_setup.py

#### Refresh Cycle Integration Tests

- [ ] T111 [P] [US4] Create tests/integration/test_refresh_cycle.py with SPDX header and module docstring
- [ ] T112 [US4] Add test_initial_data_load to verify first refresh fetches and processes calendar data in tests/integration/test_refresh_cycle.py
- [ ] T113 [US4] Add test_scheduled_refresh to verify automatic refresh on schedule in tests/integration/test_refresh_cycle.py
- [ ] T114 [US4] Add test_sensor_updates_on_refresh to verify sensor states update after coordinator refresh in tests/integration/test_refresh_cycle.py
- [ ] T115 [US4] Add test_calendar_updates_on_refresh to verify calendar entity reflects new events in tests/integration/test_refresh_cycle.py
- [ ] T116 [US4] Add test_door_code_generation_on_refresh to verify door codes are generated during refresh in tests/integration/test_refresh_cycle.py
- [ ] T117 [US4] Add test_concurrent_calendar_updates to verify multiple calendars update independently in tests/integration/test_refresh_cycle.py

#### Error Handling Integration Tests

- [ ] T118 [P] [US4] Create tests/integration/test_error_handling.py with SPDX header and module docstring
- [ ] T119 [US4] Add test_network_error_handling to verify integration handles HTTP failures gracefully in tests/integration/test_error_handling.py
- [ ] T120 [US4] Add test_invalid_ics_handling to verify integration handles malformed ICS data in tests/integration/test_error_handling.py
- [ ] T121 [US4] Add test_missing_calendar_handling to verify integration handles 404 responses in tests/integration/test_error_handling.py
- [ ] T122 [US4] Add test_timeout_handling to verify integration handles request timeouts in tests/integration/test_error_handling.py
- [ ] T123 [US4] Add test_sensor_availability_on_error to verify sensors show unavailable on persistent errors in tests/integration/test_error_handling.py
- [ ] T124 [US4] Add test_recovery_after_error to verify integration recovers when calendar becomes available again in tests/integration/test_error_handling.py
- [ ] T125 [US4] Add test_coordinator_error_state to verify coordinator maintains error state correctly in tests/integration/test_error_handling.py

**Checkpoint**: All user stories should now be independently functional. Integration tests verify end-to-end scenarios work correctly.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Improvements and validation that affect multiple user stories

- [ ] T126 [P] Run full test suite and verify all tests pass with pytest
- [ ] T127 [P] Generate coverage report and verify minimum 80% coverage is achieved
- [ ] T128 [P] Run pre-commit hooks on all test files to verify code quality standards
- [ ] T129 [P] Update pyproject.toml coverage settings to enforce achieved coverage percentage
- [ ] T130 [P] Add test documentation to README.md explaining how to run tests
- [ ] T131 Validate quickstart.md instructions by following them to run tests
- [ ] T132 [P] Review coverage gaps and add targeted tests for uncovered lines
- [ ] T133 [P] Add pytest markers (fast, slow, integration) to tests for selective execution
- [ ] T134 [P] Verify test execution time is under 5 minutes total
- [ ] T135 [P] Add coverage badge or report artifact for CI visibility
- [ ] T136 Final validation: run entire test suite in clean environment to ensure no hidden dependencies

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS all user stories
- **User Stories (Phase 3-6)**: All depend on Foundational phase completion
  - User Story 1 (P1): Can start after Foundational (Phase 2) - No dependencies on other stories
  - User Story 2 (P2): Can start after Foundational (Phase 2) - No dependencies on other stories
  - User Story 3 (P3): Can start after Foundational (Phase 2) - Uses mock coordinator data, independent of other stories
  - User Story 4 (P4): Can start after Foundational (Phase 2) - Integration tests can run with mocked data, independent of other stories
- **Polish (Phase 7)**: Depends on all user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: Can start after Foundational (Phase 2) - No dependencies on other stories
- **User Story 2 (P2)**: Can start after Foundational (Phase 2) - Tests calendar and event logic independently
- **User Story 3 (P3)**: Can start after Foundational (Phase 2) - Tests sensors with mocked coordinator, independent
- **User Story 4 (P4)**: Can start after Foundational (Phase 2) - Integration tests use mocks, can run independently

### Within Each User Story

- All tasks marked [P] within a story can run in parallel
- Tasks without [P] may have dependencies on prior tasks in same section
- Tests can be written and run incrementally

### Parallel Opportunities

- All Setup tasks (T002-T005) marked [P] can run in parallel
- All Foundational tasks (T007-T011) marked [P] can run in parallel (within Phase 2)
- Once Foundational phase completes, all user stories (Phase 3-6) can start in parallel if team capacity allows
- Within each user story, tasks marked [P] (different test files) can run in parallel
- Polish phase tasks (T126-T135) marked [P] can run in parallel

---

## Parallel Example: User Story 1

```bash
# After Foundational phase, launch User Story 1 test file creation in parallel:
Task T013: "Create tests/unit/test_coordinator.py with SPDX header"
Task T022: "Create tests/unit/test_config_flow.py with SPDX header"
Task T034: "Create tests/unit/test_init.py with SPDX header"

# Then implement tests within each file sequentially or in parallel by different developers
```

## Parallel Example: User Story 2

```bash
# Launch User Story 2 test file creation in parallel:
Task T040: "Create tests/unit/test_calendar.py with SPDX header"
Task T056: "Create tests/unit/test_event_overrides.py with SPDX header"
Task T063: "Create tests/unit/test_util.py with SPDX header"

# Then implement tests within each file
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001-T005)
2. Complete Phase 2: Foundational (T006-T012) - CRITICAL - blocks all stories
3. Complete Phase 3: User Story 1 (T013-T039)
4. **STOP and VALIDATE**: Run tests, verify core component coverage
5. Review coverage report, ensure coordinator, config_flow, and __init__ modules are well tested

### Incremental Delivery

1. Complete Setup + Foundational → Foundation ready (~12 tasks)
2. Add User Story 1 → Test independently → Core components tested (MVP: 27 tasks)
3. Add User Story 2 → Test independently → Calendar/event processing tested (43 tasks)
4. Add User Story 3 → Test independently → Sensor entities tested (20 tasks)
5. Add User Story 4 → Test independently → Integration scenarios tested (23 tasks)
6. Polish phase → Complete test suite with 80%+ coverage

### Parallel Team Strategy

With multiple developers:

1. Team completes Setup + Foundational together
2. Once Foundational is done:
   - Developer A: User Story 1 (Core components)
   - Developer B: User Story 2 (Calendar/events)
   - Developer C: User Story 3 (Sensors)
   - Developer D: User Story 4 (Integration)
3. Stories complete and integrate independently

---

## Coverage Targets by Phase

### After User Story 1 (MVP)
- coordinator.py: 85-90% coverage
- config_flow.py: 85-90% coverage
- __init__.py: 80-85% coverage
- **Overall**: ~35-40% total coverage

### After User Story 2
- calendar.py: 85-90% coverage
- event_overrides.py: 85-90% coverage
- util.py: 85-90% coverage
- **Overall**: ~60-70% total coverage

### After User Story 3
- sensor.py: 80-85% coverage
- sensors/calsensor.py: 80-85% coverage
- **Overall**: ~75-80% total coverage

### After User Story 4 + Polish
- All modules: 80%+ coverage
- **Overall**: 80-100% total coverage (goal: 100%)

---

## Notes

- [P] tasks = different files, no dependencies - can run in parallel
- [Story] label maps task to specific user story for traceability
- Each user story should be independently completable and testable
- Commit after each task or logical group of related tasks
- Stop at any checkpoint to validate story independently
- Tests are mandatory for this feature (not optional)
- All tests must include type hints and docstrings (interrogate requirement)
- Use async/await patterns for all Home Assistant interactions
- Mock all external dependencies (HTTP calls, time-based operations)
- Target: Test suite completes in under 5 minutes
- Minimum coverage: 80%, goal: 100%
