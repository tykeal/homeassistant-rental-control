<!--
SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Research: Comprehensive Test Coverage

**Feature**: 001-test-coverage
**Date**: 2025-11-25
**Phase**: 0 - Research & Analysis

## Overview

This document consolidates research findings for implementing comprehensive test coverage for the Rental Control Home Assistant integration. All technical unknowns from the implementation plan have been resolved through analysis of existing patterns, documentation, and best practices.

---

## Research Areas

### 1. Home Assistant Testing Framework Patterns

**Decision**: Use pytest-homeassistant-custom-component as the primary testing framework

**Rationale**:
- Already included in requirements_test.txt
- Provides Home Assistant-specific fixtures (hass, hass_client, etc.)
- Supports async/await patterns required by HA integrations
- Includes utilities for mocking config entries, entity registry, device registry
- Standard approach used across HA custom integration ecosystem

**Alternatives Considered**:
- Pure pytest: Insufficient - lacks HA-specific test utilities and fixtures
- unittest framework: Would require significant boilerplate to support async and HA patterns
- Custom test framework: Unnecessary complexity, reinventing standard solutions

**Implementation Notes**:
- Use `pytest_homeassistant_custom_component.common` for test utilities
- Import standard fixtures from pytest-homeassistant-custom-component
- Follow async test patterns: `async def test_*()` with `await` for all HA calls

---

### 2. Mocking External Dependencies (ICS Calendar Sources)

**Decision**: Use pytest-aiohttp with aioresponses for mocking HTTP calendar requests

**Rationale**:
- Coordinator uses aiohttp via `async_get_clientsession()` for calendar fetches
- aioresponses allows mocking aiohttp requests without actual network calls
- Enables testing various response scenarios (success, failures, invalid data)
- Lightweight and doesn't require running test servers

**Alternatives Considered**:
- requests-mock: Not applicable - integration uses aiohttp, not requests library
- responses library: Synchronous only, incompatible with async patterns
- vcr.py: Records/replays real HTTP - not suitable for CI without external dependencies

**Implementation Notes**:
- Add `aioresponses` to requirements_test.txt if not present
- Mock calendar URL responses in conftest.py fixtures
- Create fixtures for various ICS response scenarios (valid, malformed, network errors)

---

### 3. Test Fixture Organization

**Decision**: Use centralized fixture modules in tests/fixtures/ with conftest.py for pytest fixtures

**Rationale**:
- Separates test data (ICS files, event descriptions) from test logic
- Allows reuse across unit and integration tests
- conftest.py provides pytest fixtures that inject test data
- Follows pytest best practices for fixture management

**Alternatives Considered**:
- Inline fixtures in test files: Causes duplication, harder to maintain
- JSON/YAML data files: Requires parsing, less type-safe than Python fixtures
- Single large conftest.py: Would become unwieldy with all test data inline

**Implementation Notes**:
- `tests/fixtures/calendar_data.py`: ICS string constants for various rental platforms
- `tests/fixtures/config_entries.py`: Mock ConfigEntry objects with various configurations
- `tests/fixtures/event_data.py`: Event description strings with guest info patterns
- `tests/conftest.py`: pytest fixtures that use the fixture data modules

---

### 4. Testing Door Code Generation Methods

**Decision**: Parameterized tests with deterministic inputs for all three generation methods

**Rationale**:
- Three methods exist: date_based, static_random (seed-based), last_four (phone)
- Parameterized tests reduce duplication while covering all methods
- Deterministic inputs (fixed dates, seeds, phone numbers) ensure reproducible results
- Validates both successful generation and edge cases (missing data)

**Alternatives Considered**:
- Separate test functions for each method: Excessive duplication
- Testing with random data: Non-reproducible, can't validate specific outputs
- Integration-only testing: Wouldn't catch method-specific edge cases

**Implementation Notes**:
```python
@pytest.mark.parametrize("method,event_data,expected", [
    ("date_based", {...}, "1234"),
    ("static_random", {...}, "5678"),
    ("last_four", {...}, "9012"),
])
async def test_code_generation(method, event_data, expected):
    # Test implementation
```

---

### 5. Testing Async Coordinator Updates

**Decision**: Use HA test time utilities (async_fire_time_changed) to control coordinator refresh cycles

**Rationale**:
- Coordinator uses DataUpdateCoordinator with configurable refresh intervals
- Home Assistant provides time manipulation utilities for testing time-based behavior
- Avoids actual sleep() calls that would slow test execution
- Allows testing refresh cycles, error handling, and state updates deterministically

**Alternatives Considered**:
- Actual time.sleep(): Unacceptably slow for test suite
- Mocking time.time(): Fragile, doesn't integrate with HA's event loop
- Manual coordinator.async_refresh() calls: Doesn't test automatic refresh behavior

**Implementation Notes**:
```python
from homeassistant.util import dt
from pytest_homeassistant_custom_component.common import async_fire_time_changed

async def test_coordinator_refresh(hass):
    # Setup coordinator
    future_time = dt.utcnow() + timedelta(minutes=5)
    async_fire_time_changed(hass, future_time)
    await hass.async_block_till_done()
    # Assert coordinator updated
```

---

### 6. Calendar Parsing Test Coverage

**Decision**: Create comprehensive ICS fixture set covering all major rental platforms and edge cases

**Rationale**:
- Integration must parse ICS from multiple sources (Airbnb, VRBO, custom)
- Different platforms use different ICS formats and property names
- Edge cases include: missing descriptions, malformed VEVENT, timezone issues
- Testing against known good/bad ICS samples ensures robust parsing

**Alternatives Considered**:
- Generate ICS programmatically: Doesn't catch real-world format variations
- Test only against one platform: Misses platform-specific issues
- Use external ICS files: Harder to version control and understand

**Implementation Notes**:
- Fixtures include valid ICS from each major platform
- Edge case fixtures: events without descriptions, missing required fields, invalid dates
- Timezone edge cases: events with various timezone specifications
- All as Python string constants for easy inspection and modification

---

### 7. Coverage Reporting Configuration

**Decision**: Use pytest-cov with setup.cfg configuration for coverage enforcement

**Rationale**:
- Already configured in setup.cfg with fail_under=100
- pytest-cov integrates seamlessly with pytest
- Generates HTML reports and terminal summary
- CI-friendly with configurable thresholds

**Alternatives Considered**:
- coverage.py directly: pytest-cov provides better pytest integration
- Manual coverage tracking: Error-prone and doesn't enforce standards
- No coverage tooling: Defeats the purpose of this feature

**Implementation Notes**:
- Current setup.cfg has `fail_under = 100` - this is aspirational
- Initial goal is 80% minimum coverage per spec
- Update pyproject.toml: `fail_under = 80` initially, increment as tests are added
- Generate HTML reports to `tests/coverage/` for developer review
- Note: setup.cfg shows fail_under=100 but pyproject.toml shows fail_under=0 - need to reconcile

---

### 8. Integration Test Patterns

**Decision**: Use pytest-homeassistant-custom-component's async_setup_component and entity registry helpers

**Rationale**:
- Integration tests must verify components work together in realistic HA environment
- pytest-homeassistant-custom-component provides `hass` fixture with minimal HA instance
- Entity registry helpers allow verifying entities are created correctly
- Async setup ensures proper initialization sequence

**Alternatives Considered**:
- Full HA instance: Excessive overhead, slow test execution
- Manual component initialization: Fragile, doesn't match real HA behavior
- Unit tests only: Wouldn't catch integration issues between components

**Implementation Notes**:
```python
from pytest_homeassistant_custom_component.common import MockConfigEntry

async def test_full_setup(hass):
    entry = MockConfigEntry(domain="rental_control", data={...})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    # Verify entities created
```

---

### 9. Testing Configuration Flow

**Decision**: Use config_flow test patterns with UserFlow simulation

**Rationale**:
- Configuration flow uses OptionsFlow and ConfigFlow classes
- Must test validation, default values, error handling
- Home Assistant provides patterns for testing flow steps
- Tests ensure UI configuration works correctly

**Alternatives Considered**:
- Direct class instantiation: Misses HA flow manager integration
- Manual form submission simulation: Fragile, tightly coupled to implementation
- Skip config flow testing: Would leave critical UX untested

**Implementation Notes**:
```python
from homeassistant.config_entries import SOURCE_USER

async def test_config_flow(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] == "form"
    # Test form submission, validation, etc.
```

---

### 10. Pre-commit Hook Compatibility

**Decision**: Ensure all test code passes interrogate (100% docstring coverage) and mypy type checking

**Rationale**:
- Pre-commit configuration requires 100% interrogate coverage
- All code must pass mypy type checking
- Test utilities and fixtures need docstrings
- Type hints required for test functions

**Alternatives Considered**:
- Exempt test code from interrogate: Violates constitution Principle IV
- Skip type hints in tests: Makes tests harder to understand and maintain
- Use # type: ignore extensively: Technical debt, defeats purpose of type checking

**Implementation Notes**:
- Add docstrings to all test functions explaining what they test
- Add docstrings to all fixture functions
- Use proper type hints: `async def test_coordinator_refresh(hass: HomeAssistant) -> None:`
- For complex mocks, use typing.TYPE_CHECKING and proper stubs

---

## Best Practices Summary

Based on research, the following practices will be applied:

1. **Async First**: All tests use `async def` and `await` for HA calls
2. **Fixture Reuse**: Common test data in `tests/fixtures/`, pytest fixtures in `conftest.py`
3. **Deterministic**: No random values, use fixed seeds/dates for reproducibility
4. **Fast**: Use time manipulation, not sleep; mock external calls
5. **Isolated**: Each test is independent, doesn't rely on execution order
6. **Clear**: Every test has a docstring explaining purpose and approach
7. **Type-Safe**: Full type hints on all test code
8. **Coverage-Driven**: Aim for 100% coverage, minimum 80% enforced

---

## Risk Mitigation

### Identified Risks

1. **Risk**: Test execution time exceeds 5-minute target
   - **Mitigation**: Profile tests, parallelize with pytest-xdist if needed, mock expensive operations

2. **Risk**: Flaky tests due to timing issues
   - **Mitigation**: Use HA's time manipulation utilities, avoid sleep(), ensure proper async_block_till_done()

3. **Risk**: Coverage gaps due to unreachable error conditions
   - **Mitigation**: Use pytest.raises() and mock exceptions, verify error paths explicitly

4. **Risk**: Fixture maintenance becomes burden
   - **Mitigation**: Keep fixtures simple and focused, document fixture purpose clearly

5. **Risk**: Type checking failures on test code
   - **Mitigation**: Add proper type stubs for mocks, use pytest stubs, add typing.TYPE_CHECKING blocks

---

## Dependencies Required

All dependencies are already present in the project:

- ✅ pytest-homeassistant-custom-component (in requirements_test.txt)
- ✅ pytest (transitive dependency)
- ✅ pytest-cov (configured in pyproject.toml)
- ⚠️ aioresponses (need to verify if present, may need to add)

**Action Required**: Check if aioresponses is available, add to requirements_test.txt if needed.

---

## Next Steps (Phase 1)

With all research complete, proceed to Phase 1:

1. Generate `data-model.md` documenting test structure and entities
2. Generate API contracts in `/contracts/` (test fixture schemas)
3. Generate `quickstart.md` for running and developing tests
4. Update agent context files

---

**Research Complete**: All NEEDS CLARIFICATION items resolved. Ready for Phase 1.
