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

#### Calendar Parsing Tests (calendar.py)

- [ ] T040 [P] [US2] Create tests/unit/test_calendar.py with SPDX header and module docstring
- [ ] T041 [US2] Add test_parse_ics_valid_airbnb to verify parsing Airbnb ICS format correctly in tests/unit/test_calendar.py
- [ ] T042 [US2] Add test_parse_ics_valid_vrbo to verify parsing VRBO ICS format correctly in tests/unit/test_calendar.py
- [ ] T043 [US2] Add test_parse_ics_generic to verify parsing generic ICS format in tests/unit/test_calendar.py
- [ ] T044 [US2] Add test_parse_ics_multiple_events to verify extracting multiple events from single calendar in tests/unit/test_calendar.py
- [ ] T045 [US2] Add test_parse_ics_empty_calendar to verify handling calendar with no events in tests/unit/test_calendar.py
- [ ] T046 [US2] Add test_parse_ics_malformed to verify error handling for malformed ICS syntax in tests/unit/test_calendar.py
- [ ] T047 [US2] Add test_parse_ics_missing_required_fields to verify handling events with missing DTSTART/DTEND in tests/unit/test_calendar.py
- [ ] T048 [US2] Add test_timezone_handling_utc to verify parsing UTC times correctly (FR-016) in tests/unit/test_calendar.py
- [ ] T049 [US2] Add test_timezone_handling_named to verify parsing events with named timezones (FR-016) in tests/unit/test_calendar.py
- [ ] T050 [US2] Add test_timezone_handling_x_wr_timezone to verify handling X-WR-TIMEZONE property (FR-016) in tests/unit/test_calendar.py
- [ ] T051 [US2] Add test_event_filtering_reserved to verify reserved events are included in tests/unit/test_calendar.py
- [ ] T052 [US2] Add test_event_filtering_blocked to verify blocked events are filtered when ignore_non_reserved is True in tests/unit/test_calendar.py
- [ ] T053 [US2] Add test_event_filtering_not_available to verify not available events are filtered appropriately in tests/unit/test_calendar.py
- [ ] T054 [US2] Add test_calendar_entity_creation to verify CalendarEntity is created with correct attributes in tests/unit/test_calendar.py
- [ ] T055 [US2] Add test_calendar_entity_get_events to verify calendar entity returns events for date range queries in tests/unit/test_calendar.py

#### Event Override Tests (event_overrides.py)

- [ ] T056 [P] [US2] Create tests/unit/test_event_overrides.py with SPDX header and module docstring
- [ ] T057 [US2] Add test_checkin_time_adjustment_allday to verify all-day events get checkin time applied in tests/unit/test_event_overrides.py
- [ ] T058 [US2] Add test_checkout_time_adjustment_allday to verify all-day events get checkout time applied in tests/unit/test_event_overrides.py
- [ ] T059 [US2] Add test_time_adjustment_timed_events to verify timed events are not adjusted in tests/unit/test_event_overrides.py
- [ ] T060 [US2] Add test_time_adjustment_timezone_aware to verify timezone is preserved in adjusted times in tests/unit/test_event_overrides.py
- [ ] T061 [US2] Add test_time_adjustment_custom_times to verify custom checkin/checkout times are applied in tests/unit/test_event_overrides.py
- [ ] T062 [US2] Add test_event_override_detection to verify override logic identifies which events to adjust in tests/unit/test_event_overrides.py

#### Utility Function Tests (util.py)

- [ ] T063 [P] [US2] Create tests/unit/test_util.py with SPDX header and module docstring
- [ ] T064 [US2] Add test_extract_guest_email_valid to verify email extraction from description in tests/unit/test_util.py
- [ ] T065 [US2] Add test_extract_guest_email_missing to verify handling description without email in tests/unit/test_util.py
- [ ] T066 [US2] Add test_extract_guest_email_invalid to verify handling malformed email addresses in tests/unit/test_util.py
- [ ] T067 [US2] Add test_extract_guest_phone_valid to verify phone number extraction from description in tests/unit/test_util.py
- [ ] T068 [US2] Add test_extract_guest_phone_missing to verify handling description without phone in tests/unit/test_util.py
- [ ] T069 [US2] Add test_extract_guest_phone_formats to verify various phone number formats are extracted in tests/unit/test_util.py
- [ ] T070 [US2] Add test_extract_guest_count_valid to verify guest count extraction from description in tests/unit/test_util.py
- [ ] T071 [US2] Add test_extract_guest_count_missing to verify handling description without guest count in tests/unit/test_util.py
- [ ] T072 [US2] Add test_extract_reservation_url_valid to verify URL extraction from description in tests/unit/test_util.py
- [ ] T073 [US2] Add test_extract_reservation_url_missing to verify handling description without URL in tests/unit/test_util.py
- [ ] T074 [US2] Add test_generate_code_date_based to verify date-based code generation produces deterministic codes in tests/unit/test_util.py
- [ ] T075 [US2] Add test_generate_code_date_based_different_dates to verify different dates produce different codes in tests/unit/test_util.py
- [ ] T076 [US2] Add test_generate_code_static_random to verify static random code generation with seed in tests/unit/test_util.py
- [ ] T077 [US2] Add test_generate_code_static_random_same_seed to verify same seed produces same code in tests/unit/test_util.py
- [ ] T078 [US2] Add test_generate_code_last_four to verify phone-based code generation using last 4 digits in tests/unit/test_util.py
- [ ] T079 [US2] Add test_generate_code_last_four_missing_phone to verify fallback when phone is missing in tests/unit/test_util.py
- [ ] T080 [US2] Add test_generate_code_length_validation to verify code length parameter is respected (4-8 digits) in tests/unit/test_util.py
- [ ] T081 [US2] Add test_should_update_code_date_changed to verify code update logic when event dates change in tests/unit/test_util.py
- [ ] T082 [US2] Add test_should_update_code_disabled to verify codes not updated when should_update_code is False in tests/unit/test_util.py

**Checkpoint**: At this point, User Story 2 should be fully functional and testable independently. Calendar parsing and event processing have comprehensive coverage.

---

## Phase 5: User Story 3 - Sensor and Entity Testing (Priority: P3)

**Goal**: Tests for sensor entities and their attributes to ensure event data is correctly exposed to Home Assistant users and automations

**Independent Test**: Mock coordinator data and verify sensor state updates, attribute mappings, entity availability, and multiple event sensors work correctly.

### Implementation for User Story 3

#### Sensor Tests (sensor.py and sensors/calsensor.py)

**Note on FR-005 Coverage**: Tasks T083-T102 provide comprehensive coverage for sensor entity creation, state updates, and attribute mapping as required by FR-005. Tests verify sensor creation (T085, T088), state updates on coordinator changes (T086-T090, T098), and all attribute mappings (T091-T097).

- [ ] T083 [P] [US3] Create tests/unit/test_sensors.py with SPDX header and module docstring
- [ ] T084 [US3] Add test_sensor_platform_setup to verify sensor platform initializes with coordinator in tests/unit/test_sensors.py
- [ ] T085 [US3] Add test_current_event_sensor_creation to verify current event sensor is created with correct entity_id in tests/unit/test_sensors.py
- [ ] T086 [US3] Add test_current_event_sensor_state to verify current event sensor shows active event in tests/unit/test_sensors.py
- [ ] T087 [US3] Add test_current_event_sensor_no_event to verify current event sensor shows unavailable when no current event in tests/unit/test_sensors.py
- [ ] T088 [US3] Add test_next_event_sensor_creation to verify next event sensor is created in tests/unit/test_sensors.py
- [ ] T089 [US3] Add test_next_event_sensor_state to verify next event sensor shows upcoming event in tests/unit/test_sensors.py
- [ ] T090 [US3] Add test_multiple_event_sensors to verify max_events creates correct number of sensors in tests/unit/test_sensors.py
- [ ] T091 [US3] Add test_sensor_attributes_guest_email to verify guest_email attribute is set correctly in tests/unit/test_sensors.py
- [ ] T092 [US3] Add test_sensor_attributes_guest_phone to verify guest_phone attribute is set correctly in tests/unit/test_sensors.py
- [ ] T093 [US3] Add test_sensor_attributes_guest_count to verify guest_count attribute is set correctly in tests/unit/test_sensors.py
- [ ] T094 [US3] Add test_sensor_attributes_reservation_url to verify reservation_url attribute is set correctly in tests/unit/test_sensors.py
- [ ] T095 [US3] Add test_sensor_attributes_door_code to verify door_code attribute is set correctly in tests/unit/test_sensors.py
- [ ] T096 [US3] Add test_sensor_attributes_start_end_times to verify start and end time attributes in tests/unit/test_sensors.py
- [ ] T097 [US3] Add test_sensor_attributes_missing_data to verify attributes handle missing data gracefully in tests/unit/test_sensors.py
- [ ] T098 [US3] Add test_sensor_update_on_coordinator_refresh to verify sensors update when coordinator data changes in tests/unit/test_sensors.py
- [ ] T099 [US3] Add test_sensor_availability to verify sensor availability tracking based on coordinator state in tests/unit/test_sensors.py
- [ ] T100 [US3] Add test_sensor_unique_id to verify each sensor has unique_id for entity registry in tests/unit/test_sensors.py
- [ ] T101 [US3] Add test_sensor_device_info to verify sensors include device_info for grouping in tests/unit/test_sensors.py
- [ ] T102 [US3] Add test_calsensor_specific_attributes to verify CalSensor-specific attributes and methods in tests/unit/test_sensors.py

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
