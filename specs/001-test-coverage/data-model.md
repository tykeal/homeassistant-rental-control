<!--
SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Data Model: Test Coverage Implementation

**Feature**: 001-test-coverage
**Date**: 2025-11-25
**Phase**: 1 - Design

## Overview

This document defines the test structure, test entities, fixtures, and data relationships for the comprehensive test coverage implementation. It serves as the blueprint for organizing test code, test data, and test execution.

---

## Test Structure Hierarchy

```text
Tests
├── Unit Tests (test individual modules in isolation)
│   ├── Coordinator Tests
│   ├── Config Flow Tests
│   ├── Initialization Tests
│   ├── Calendar Parsing Tests
│   ├── Event Override Tests
│   ├── Utility Function Tests
│   └── Sensor Tests
├── Integration Tests (test component interactions)
│   ├── Full Setup Tests
│   ├── Refresh Cycle Tests
│   └── Error Handling Tests
└── Fixtures (test data and utilities)
    ├── Calendar Data Fixtures
    ├── Config Entry Fixtures
    ├── Event Data Fixtures
    └── Mock Object Fixtures
```

---

## Core Test Entities

### 1. Test Case

**Purpose**: Individual test function that validates specific behavior

**Attributes**:
- `name`: Test function name (test_*)
- `module`: Test file containing the test
- `category`: unit, integration, or fixture test
- `target_coverage`: Which production module(s) this test covers
- `description`: Docstring explaining test purpose
- `fixtures_used`: List of pytest fixtures required
- `assertions`: Expected outcomes verified by test

**Lifecycle**:
1. Setup: Fixtures loaded, mocks configured
2. Execution: Test logic runs
3. Assertion: Validate expected behavior
4. Teardown: Cleanup (handled by pytest)

**Validation Rules**:
- Must have docstring (interrogate requirement)
- Must use async def for HA code
- Must include type hints
- Must be independent (no test order dependencies)

---

### 2. Test Fixture

**Purpose**: Reusable test data or mock objects injected into tests

**Attributes**:
- `name`: Fixture function name
- `scope`: function, module, session (pytest scopes)
- `provides`: What data/mock the fixture returns
- `dependencies`: Other fixtures this fixture requires
- `description`: Docstring explaining fixture purpose

**Categories**:

#### A. Data Fixtures
Provide test data (ICS calendars, event descriptions, configurations)

**Examples**:
- `valid_airbnb_ics`: ICS calendar string from Airbnb format
- `malformed_ics`: ICS with syntax errors for error testing
- `event_with_guest_info`: Event description containing email/phone
- `config_entry_full`: Complete configuration with all options

#### B. Mock Fixtures
Provide mock Home Assistant objects and external dependencies

**Examples**:
- `hass`: Home Assistant instance (from pytest-homeassistant-custom-component)
- `mock_calendar_response`: aioresponses mock for calendar URL
- `mock_coordinator`: Mocked RentalControlCoordinator
- `mock_config_entry`: MockConfigEntry for integration testing

#### C. Utility Fixtures
Provide helper functions for common test operations

**Examples**:
- `setup_integration`: Helper to fully initialize integration
- `advance_time`: Helper to manipulate HA time for testing
- `assert_entity_state`: Helper to verify entity state

---

### 3. Mock Object

**Purpose**: Simulated Home Assistant or external dependency for isolated testing

**Attributes**:
- `target`: What is being mocked (aiohttp, coordinator, entity)
- `behaviors`: What responses/behaviors the mock provides
- `state`: Mock's internal state (for stateful mocks)
- `call_tracking`: Record of calls made to mock for verification

**Mock Types**:

#### A. HTTP Response Mocks
Mock calendar URL requests using aioresponses

**Behaviors**:
- Return valid ICS content
- Return malformed content
- Raise network exceptions
- Return HTTP error codes

#### B. Coordinator Mocks
Mock RentalControlCoordinator for sensor testing

**Behaviors**:
- Provide event data to sensors
- Simulate update cycles
- Simulate error states

#### C. Home Assistant Core Mocks
Mock HA services and registries

**Behaviors**:
- Entity registry responses
- Device registry responses
- State changes
- Event firing

---

### 4. Test Scenario

**Purpose**: Collection of related tests validating a complete user story or feature area

**Attributes**:
- `name`: Scenario description (e.g., "Config Flow Setup")
- `user_story`: Which spec user story this validates
- `test_cases`: List of test functions in scenario
- `coverage_target`: Functional requirements covered
- `prerequisites`: Fixtures or setup required

**Scenarios**:

#### Scenario 1: Core Component Testing
- Tests: coordinator initialization, data refresh, error handling, state management
- Coverage: FR-001 (coordinator functionality)
- User Story: User Story 1

#### Scenario 2: Configuration Flow Testing
- Tests: initial setup, reconfiguration, validation, error cases
- Coverage: FR-002 (configuration flow)
- User Story: User Story 1

#### Scenario 3: Calendar Parsing Testing
- Tests: ICS parsing, event extraction, attribute parsing, timezone handling
- Coverage: FR-003, FR-016 (calendar parsing, timezones)
- User Story: User Story 2

#### Scenario 4: Door Code Generation Testing
- Tests: all three generation methods, edge cases, update conditions
- Coverage: FR-004, FR-009 (code generation, code updates)
- User Story: User Story 2

#### Scenario 5: Sensor Entity Testing
- Tests: sensor creation, state updates, attribute mapping
- Coverage: FR-005, FR-010 (sensor entities, guest attributes)
- User Story: User Story 3

#### Scenario 6: Integration Testing
- Tests: full setup, end-to-end flows, error recovery
- Coverage: FR-011, FR-012, FR-015 (error scenarios, HA integration, CI)
- User Story: User Story 4

---

## Test Data Models

### ICS Calendar Fixture Model

**Structure**:
```python
@dataclass
class ICSFixture:
    name: str                    # Fixture identifier
    source_platform: str         # e.g., "airbnb", "vrbo", "generic"
    ics_content: str            # Raw ICS string
    expected_events: int        # Number of events expected
    has_timezones: bool         # Whether ICS includes timezone
    event_descriptions: list[str]  # Sample descriptions from events
    edge_case: str | None       # What edge case this tests (if any)
```

**Examples**:
- Airbnb standard format (2 events, UTC times, guest info in description)
- VRBO format (different property naming, local timezone)
- Malformed ICS (missing END:VCALENDAR)
- Events without descriptions
- Overlapping events

---

### Event Data Fixture Model

**Structure**:
```python
@dataclass
class EventFixture:
    name: str                    # Fixture identifier
    summary: str                 # Event title
    description: str             # Event description (contains guest info)
    start: datetime             # Start datetime
    end: datetime               # End datetime
    has_email: bool             # Description contains email
    has_phone: bool             # Description contains phone
    has_guest_count: bool       # Description contains guest count
    has_reservation_url: bool   # Description contains reservation link
    expected_extractions: dict  # Expected parsed attributes
```

**Examples**:
- Complete guest info (email, phone, count, URL all present)
- Partial info (only email)
- No guest info (blocked event)
- Malformed guest info (email format invalid)

---

### Config Entry Fixture Model

**Structure**:
```python
@dataclass
class ConfigEntryFixture:
    name: str                    # Fixture identifier
    entry_id: str               # Config entry ID
    domain: str                 # Always "rental_control"
    data: dict                  # Configuration data
    options: dict               # Options flow data
    is_valid: bool              # Whether this config is valid
    validation_errors: list[str]  # Expected errors (if invalid)
```

**Configuration Fields** (from spec and code analysis):
- `name`: Calendar name
- `url`: ICS calendar URL
- `verify_ssl`: SSL verification boolean
- `refresh_frequency`: Minutes between refreshes (2-1440)
- `max_events`: Number of event sensors (1-10)
- `days`: Days ahead to fetch (1-365)
- `checkin`: Check-in time (HH:MM)
- `checkout`: Check-out time (HH:MM)
- `code_generation`: Method (date_based, static_random, last_four)
- `code_length`: Code length (4-8 digits)
- `start_slot`: Keymaster starting slot number
- `event_prefix`: Event name prefix
- `timezone`: Calendar timezone
- `ignore_non_reserved`: Filter non-reserved events
- `keymaster_entry_id`: Linked Keymaster entry
- `generate_package`: Auto-generate package files
- `packages_path`: Path for package files
- `should_update_code`: Update codes on date changes

**Examples**:
- Minimal valid config (only required fields)
- Complete config (all fields with non-default values)
- Invalid config (missing required field)
- Invalid config (out-of-range values)

---

## Test Relationships

### Coverage Mapping

```text
Production Module → Test Module → Test Scenarios → Functional Requirements

coordinator.py → test_coordinator.py
    ├── Scenario: Initialization → FR-001
    ├── Scenario: Data Refresh → FR-001
    ├── Scenario: Error Handling → FR-001, FR-011
    └── Scenario: State Management → FR-001

config_flow.py → test_config_flow.py
    ├── Scenario: Initial Setup → FR-002
    ├── Scenario: Reconfiguration → FR-002
    ├── Scenario: Validation → FR-002
    └── Scenario: Error Cases → FR-002, FR-011

__init__.py → test_init.py
    ├── Scenario: Component Setup → FR-012
    ├── Scenario: Platform Loading → FR-012
    ├── Scenario: Service Registration → FR-018
    └── Scenario: Cleanup → FR-012

calendar.py → test_calendar.py
    ├── Scenario: ICS Parsing → FR-003
    ├── Scenario: Event Extraction → FR-003
    ├── Scenario: Timezone Handling → FR-003, FR-016
    └── Scenario: Filtering → FR-008

event_overrides.py → test_event_overrides.py
    ├── Scenario: Time Adjustments → FR-007
    └── Scenario: Override Logic → FR-007

util.py → test_util.py
    ├── Scenario: Helper Functions → (supporting)
    └── Scenario: Code Generation → FR-004, FR-009, FR-010

sensor.py, sensors/ → test_sensors.py
    ├── Scenario: Sensor Creation → FR-005
    ├── Scenario: State Updates → FR-005
    ├── Scenario: Attributes → FR-005, FR-010
    └── Scenario: Multiple Calendars → FR-020

integration/ → test_full_setup.py, test_refresh_cycle.py, test_error_handling.py
    ├── Scenario: End-to-End Setup → FR-012, FR-015
    ├── Scenario: Data Flow → FR-001, FR-005
    ├── Scenario: Error Recovery → FR-011
    └── Scenario: Multiple Calendars → FR-020
```

---

## Test Execution Model

### Test Phase Sequence

```text
Phase 1: Fixture Loading
    ├── Load conftest.py fixtures
    ├── Initialize Home Assistant test instance
    └── Prepare mock objects

Phase 2: Test Setup (per test)
    ├── Inject fixtures into test function
    ├── Configure mocks for test scenario
    └── Set up initial state

Phase 3: Test Execution
    ├── Run test logic
    ├── Exercise production code
    └── Track interactions

Phase 4: Assertion
    ├── Verify expected outcomes
    ├── Check mock call patterns
    └── Validate state changes

Phase 5: Teardown
    ├── Clean up test state
    ├── Reset mocks
    └── Release resources
```

---

## Coverage Requirements

### Minimum Coverage Targets (per module)

| Module | Target Coverage | Priority | Notes |
|--------|----------------|----------|-------|
| coordinator.py | 95% | P1 | Core logic, critical for reliability |
| config_flow.py | 90% | P1 | User-facing, must handle validation |
| __init__.py | 85% | P1 | Integration setup, platform loading |
| calendar.py | 90% | P2 | Calendar parsing, complex logic |
| event_overrides.py | 85% | P2 | Time adjustments |
| util.py | 90% | P2 | Code generation algorithms |
| sensor.py | 85% | P3 | Entity creation and updates |
| sensors/calsensor.py | 85% | P3 | Calendar sensor specifics |
| const.py | N/A | N/A | Constants only, no logic to test |

**Overall Target**: Minimum 80%, goal 100%

---

## Validation Rules

### Test Quality Standards

1. **Independence**: Tests must not depend on execution order
2. **Determinism**: Tests must produce same results every run
3. **Speed**: Unit tests < 1s each, integration tests < 10s each
4. **Clarity**: Test names and docstrings clearly state what is tested
5. **Completeness**: Each test covers one logical behavior
6. **Type Safety**: All test code includes type hints
7. **Documentation**: All test utilities have docstrings

### Fixture Quality Standards

1. **Reusability**: Fixtures used by multiple tests
2. **Simplicity**: Fixtures provide minimal necessary data
3. **Clarity**: Fixture names clearly indicate what they provide
4. **Documentation**: Fixtures have docstrings explaining purpose
5. **Scope**: Appropriate scope (function/module/session) for efficiency

---

## State Transitions

### Test Lifecycle States

```text
[Not Run] → [Setup] → [Executing] → [Passed/Failed/Skipped]
                ↓
           [Teardown]
```

### Mock States

```text
[Unconfigured] → [Configured] → [Active] → [Verified] → [Reset]
```

### Integration Test States

```text
[Clean HA Instance]
    ↓
[Config Entry Added]
    ↓
[Integration Setup Called]
    ↓
[Entities Created]
    ↓
[Data Updated]
    ↓
[Assertions Verified]
    ↓
[Integration Unloaded]
```

---

## Next Steps

With the data model defined, proceed to:
1. Create API contracts (test fixture schemas) in `/contracts/`
2. Generate `quickstart.md` for developer guidance
3. Update agent context files

---

**Data Model Complete**: Test structure, entities, and relationships fully defined.
