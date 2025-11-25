<!--
SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Quickstart: Test Coverage Development

**Feature**: 001-test-coverage
**Audience**: Developers implementing and running tests

This guide provides everything you need to develop, run, and maintain the test suite for the Rental Control integration.

---

## Prerequisites

### Required Tools

- Python 3.11+ (3.11, 3.12, or 3.13 supported)
- pip or uv (for dependency management)
- Git (for version control)
- pre-commit (for code quality checks)

### Environment Setup

```bash
# Clone the repository (if not already done)
cd /home/tykeal/repos/personal/homeassistant/rental-control

# Install dependencies (using uv - recommended)
uv sync --group dev --group test

# OR using pip
pip install -r requirements_test.txt

# Install pre-commit hooks
pre-commit install
```

---

## Running Tests

### Run All Tests

```bash
# Using pytest directly
pytest

# With coverage report
pytest --cov=custom_components.rental_control --cov-report=html --cov-report=term

# Quiet mode (less verbose)
pytest -q

# Verbose mode (for debugging)
pytest -v
```

### Run Specific Test Categories

```bash
# Unit tests only
pytest tests/unit/

# Integration tests only
pytest tests/integration/

# Specific test file
pytest tests/unit/test_coordinator.py

# Specific test function
pytest tests/unit/test_coordinator.py::test_coordinator_initialization
```

### Run with Markers (once implemented)

```bash
# Run only fast tests
pytest -m fast

# Run only slow tests
pytest -m slow

# Skip integration tests
pytest -m "not integration"
```

---

## Test Development Workflow

### 1. Create a New Test File

```bash
# For unit tests (one file per production module)
touch tests/unit/test_<module_name>.py

# For integration tests
touch tests/integration/test_<scenario_name>.py
```

### 2. Test File Template

```python
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>

"""Tests for <module description>."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

# Import module under test
from custom_components.rental_control.<module> import <classes/functions>


async def test_example(hass: HomeAssistant) -> None:
    """Test that <specific behavior> works correctly.

    This test verifies:
    - <expectation 1>
    - <expectation 2>
    - <expectation 3>

    Args:
        hass: Home Assistant instance fixture
    """
    # Arrange: Set up test data and mocks
    test_data = {...}

    # Act: Execute the code under test
    result = await some_function(hass, test_data)

    # Assert: Verify expected outcomes
    assert result == expected_value
    assert some_condition_is_true
```

### 3. Add Test Fixtures

If you need reusable test data, add to `tests/conftest.py`:

```python
@pytest.fixture
def my_fixture() -> SomeType:
    """Provide test data for <scenario>.

    Returns:
        Description of what this fixture provides
    """
    return SomeType(...)
```

### 4. Run Your New Tests

```bash
# Run just your new test file
pytest tests/unit/test_<module_name>.py -v

# Run with coverage for your module
pytest tests/unit/test_<module_name>.py --cov=custom_components.rental_control.<module> --cov-report=term
```

### 5. Check Coverage

```bash
# Generate HTML coverage report
pytest --cov=custom_components.rental_control --cov-report=html

# Open in browser
# Firefox/Chrome: open htmlcov/index.html
# The report shows which lines are covered and which are not
```

### 6. Verify Code Quality

```bash
# Run pre-commit checks on changed files
pre-commit run

# Run pre-commit checks on all files
pre-commit run --all-files

# This runs:
# - ruff (linting and formatting)
# - mypy (type checking)
# - interrogate (docstring coverage)
# - reuse (license headers)
# - and other checks
```

### 7. Commit Your Changes

```bash
# Stage your changes
git add tests/unit/test_<module_name>.py

# Commit with conventional commit format and sign-off
git commit -s -m "Test: add tests for <module_name>

- Test <specific functionality>
- Cover <edge cases>
- Achieve <coverage percentage>% coverage

Co-Authored-By: GitHub Copilot <copilot@github.com>"
```

---

## Test Writing Guidelines

### Naming Conventions

- **Test files**: `test_<module_name>.py` (mirrors production module)
- **Test functions**: `test_<what_is_tested>` or `test_<scenario>`
- **Fixtures**: `<what_it_provides>` (e.g., `valid_ics_calendar`, `mock_coordinator`)

### Docstring Requirements

Every test must have a docstring following this format:

```python
async def test_something(hass: HomeAssistant) -> None:
    """Test that <specific behavior> works correctly.

    This test verifies:
    - <expectation 1>
    - <expectation 2>

    Args:
        hass: Description of hass parameter
        other_fixture: Description of other fixture parameters
    """
```

### Assertion Guidelines

```python
# Good: Specific assertions
assert result.status == "success"
assert len(events) == 5
assert event.guest_email == "test@example.com"

# Bad: Generic assertions
assert result  # Not clear what's being tested
assert events  # Doesn't specify expected count

# Use pytest.raises for exceptions
with pytest.raises(ValueError, match="Invalid configuration"):
    await setup_integration(invalid_config)
```

### Async/Await Patterns

```python
# Always use async def for tests that interact with Home Assistant
async def test_coordinator_update(hass: HomeAssistant) -> None:
    """Test coordinator data update."""
    # Setup
    coordinator = RentalControlCoordinator(hass, config)

    # Execute async operation
    await coordinator.async_config_entry_first_refresh()

    # Wait for HA to process events
    await hass.async_block_till_done()

    # Assert
    assert coordinator.data is not None
```

### Mocking Best Practices

```python
from unittest.mock import AsyncMock, patch

async def test_with_mock(hass: HomeAssistant) -> None:
    """Test with mocked external dependency."""
    # Mock HTTP requests
    with patch("aiohttp.ClientSession.get") as mock_get:
        mock_get.return_value = AsyncMock()
        mock_get.return_value.text = AsyncMock(return_value="ICS content")

        # Test code that uses the mocked dependency
        result = await fetch_calendar(hass, "https://example.com/cal.ics")

        # Verify mock was called correctly
        mock_get.assert_called_once()
        assert result == "ICS content"
```

---

## Common Test Patterns

### Pattern 1: Testing Config Flow

```python
from homeassistant.config_entries import SOURCE_USER
from custom_components.rental_control.const import DOMAIN

async def test_config_flow_user_init(hass: HomeAssistant) -> None:
    """Test the initial config flow step."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    assert result["type"] == "form"
    assert result["step_id"] == "user"
    assert "name" in result["data_schema"].schema
    assert "url" in result["data_schema"].schema
```

### Pattern 2: Testing Coordinator

```python
from datetime import timedelta
from homeassistant.util import dt
from pytest_homeassistant_custom_component.common import async_fire_time_changed

async def test_coordinator_refresh(hass: HomeAssistant, mock_calendar_url) -> None:
    """Test that coordinator refreshes data on schedule."""
    # Setup coordinator with 2-minute refresh
    coordinator = RentalControlCoordinator(hass, config)
    await coordinator.async_config_entry_first_refresh()

    initial_update_time = coordinator.last_update_success

    # Advance time by 2 minutes
    future_time = dt.utcnow() + timedelta(minutes=2)
    async_fire_time_changed(hass, future_time)
    await hass.async_block_till_done()

    # Verify coordinator updated
    assert coordinator.last_update_success > initial_update_time
```

### Pattern 3: Testing Entity Creation

```python
from homeassistant.helpers import entity_registry as er

async def test_sensor_entities_created(hass: HomeAssistant, setup_integration) -> None:
    """Test that sensor entities are created correctly."""
    entity_registry = er.async_get(hass)

    # Check sensor entities exist
    sensor_1 = entity_registry.async_get("sensor.rental_control_test_event_1")
    assert sensor_1 is not None
    assert sensor_1.platform == "rental_control"

    # Check state
    state = hass.states.get("sensor.rental_control_test_event_1")
    assert state is not None
    assert state.state != "unavailable"
```

### Pattern 4: Testing with Fixtures

```python
@pytest.fixture
def sample_ics_calendar() -> str:
    """Provide a sample ICS calendar for testing."""
    return """BEGIN:VCALENDAR
VERSION:2.0
PRODID:Test Calendar
X-WR-TIMEZONE:America/New_York
BEGIN:VEVENT
UID:event1@test.com
DTSTART:20250115T160000Z
DTEND:20250120T110000Z
SUMMARY:Reserved: John Doe
DESCRIPTION:Email: john@example.com
END:VEVENT
END:VCALENDAR"""

async def test_parse_calendar(sample_ics_calendar: str) -> None:
    """Test ICS calendar parsing."""
    events = parse_ics(sample_ics_calendar)

    assert len(events) == 1
    assert events[0].summary == "Reserved: John Doe"
```

---

## Debugging Tests

### Run Tests with PDB

```bash
# Drop into debugger on test failure
pytest --pdb

# Drop into debugger at start of test
pytest --trace
```

### Increase Logging

```python
import logging

# In conftest.py or test file
logging.basicConfig(level=logging.DEBUG)

# Or for specific logger
logging.getLogger("custom_components.rental_control").setLevel(logging.DEBUG)
```

### Print Debugging

```python
# pytest captures output by default
# Use -s flag to see print statements
pytest tests/unit/test_coordinator.py -s

# In test
def test_something():
    print(f"Debug value: {some_variable}")
    assert some_variable == expected
```

---

## Coverage Goals

### Module Coverage Targets

| Priority | Modules | Target Coverage |
|----------|---------|----------------|
| P1 | coordinator.py, config_flow.py, __init__.py | 90-95% |
| P2 | calendar.py, event_overrides.py, util.py | 85-90% |
| P3 | sensor.py, sensors/calsensor.py | 80-85% |

### Overall Target

- **Minimum**: 80% overall coverage (enforced by CI)
- **Goal**: 100% overall coverage

### Checking Coverage

```bash
# Generate coverage report
pytest --cov=custom_components.rental_control --cov-report=term-missing

# This shows:
# - Overall coverage percentage
# - Coverage per file
# - Line numbers that are NOT covered (missing)
```

---

## Continuous Integration

Tests run automatically on:

- Every push to feature branches
- Every pull request
- Merge to main branch

### CI Requirements

All of these must pass:

1. ✅ All tests pass (pytest)
2. ✅ Code coverage ≥ 80% (pytest-cov)
3. ✅ Pre-commit hooks pass (ruff, mypy, interrogate, reuse)
4. ✅ No linting errors
5. ✅ All docstrings present

### Local Pre-CI Check

```bash
# Run everything that CI will check
pre-commit run --all-files && pytest --cov=custom_components.rental_control --cov-report=term --cov-fail-under=80
```

---

## Troubleshooting

### Issue: Import Errors

```bash
# Problem: "ModuleNotFoundError: No module named 'custom_components'"
# Solution: Ensure tests are run from repository root
cd /home/tykeal/repos/personal/homeassistant/rental-control
pytest

# OR set PYTHONPATH
export PYTHONPATH="${PYTHONPATH}:$(pwd)"
pytest
```

### Issue: Async Warnings

```bash
# Problem: "RuntimeWarning: coroutine was never awaited"
# Solution: Ensure async functions are called with await
# Wrong:
result = async_function()
# Correct:
result = await async_function()
```

### Issue: Tests Hang

```bash
# Problem: Tests never complete
# Solution: Check for missing await or async_block_till_done()
await hass.async_block_till_done()  # Add after operations that trigger events
```

### Issue: Fixture Not Found

```bash
# Problem: "fixture 'my_fixture' not found"
# Solution: Ensure fixture is defined in conftest.py or test file
# Check fixture name matches exactly (case-sensitive)
```

### Issue: Coverage Not 100%

```bash
# Problem: Some lines not covered
# Solution: Generate HTML report to see what's missing
pytest --cov=custom_components.rental_control --cov-report=html
# Open htmlcov/index.html and look for red (uncovered) lines
# Add tests for those code paths
```

---

## Resources

### Documentation

- [pytest documentation](https://docs.pytest.org/)
- [pytest-homeassistant-custom-component](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component)
- [Home Assistant testing guidelines](https://developers.home-assistant.io/docs/development_testing)

### Project Files

- `specs/001-test-coverage/plan.md`: Implementation plan
- `specs/001-test-coverage/research.md`: Research findings
- `specs/001-test-coverage/data-model.md`: Test structure and entities
- `specs/001-test-coverage/contracts/`: Test fixture contracts

### Getting Help

- Review existing tests for patterns
- Check pytest output for error details
- Review coverage report to find gaps
- Consult Home Assistant developer documentation

---

## Next Steps

1. **Read** `data-model.md` to understand test structure
2. **Review** `contracts/test-fixtures.md` for fixture interfaces
3. **Start** with unit tests for core modules (coordinator, config_flow)
4. **Check** coverage frequently to track progress
5. **Commit** small atomic changes with proper sign-off

---

**Quickstart Complete**: You're ready to develop and run tests!
