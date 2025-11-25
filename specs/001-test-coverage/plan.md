<!--
SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Implementation Plan: Comprehensive Test Coverage

**Branch**: `001-test-coverage` | **Date**: 2025-11-25 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/001-test-coverage/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command. See `.specify/templates/commands/plan.md` for the execution workflow.

## Summary

Add comprehensive test coverage for the Rental Control Home Assistant integration (~2800 lines of code) to achieve minimum 80% coverage target. The test suite will cover core components (coordinator, config flow, initialization), calendar and event processing (ICS parsing, door code generation), sensor entities, and integration scenarios. Tests will use Home Assistant's pytest-homeassistant-custom-component framework with async patterns, mock external dependencies, and ensure reliable CI execution.

## Technical Context

**Language/Version**: Python 3.11+ (supports 3.11, 3.12, 3.13 per pyproject.toml)
**Primary Dependencies**: Home Assistant, pytest-homeassistant-custom-component, icalendar>=6.1.0, x-wr-timezone>=2.0.0
**Storage**: N/A (tests use mocks and fixtures)
**Testing**: pytest with pytest-homeassistant-custom-component, pytest-cov for coverage reporting
**Target Platform**: Home Assistant integration (Linux/cross-platform Python)
**Project Type**: Single project - Home Assistant custom integration
**Performance Goals**: Test suite execution under 5 minutes total, individual test modules under 10 seconds
**Constraints**: 100% code coverage required (per setup.cfg fail_under=100), all tests must use async/await patterns, no external network calls in tests
**Scale/Scope**: ~2800 lines of production code across 10 Python modules, targeting 80% minimum coverage with goal of 100%

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Principle I: Code Quality & Testing Standards
**Status**: ✅ PASS (by definition)
**Notes**: This feature CREATES the test coverage required by the constitution. All test code will include type hints, follow async patterns, and meet linting standards. Tests themselves will be documented per interrogate requirements.

### Principle II: Atomic Commit Discipline
**Status**: ✅ PASS
**Notes**: Test implementation will be delivered in logical atomic commits:
- Test infrastructure setup (fixtures, conftest)
- Core component tests (coordinator, config_flow, __init__)
- Event processing tests (calendar parsing, door codes)
- Sensor and entity tests
- Integration tests
Each commit will be independently runnable.

### Principle III: Licensing & Attribution Standards
**Status**: ✅ PASS
**Notes**: All test files will include proper SPDX headers (Apache-2.0 license, copyright to Andrew Grimberg).

### Principle IV: Pre-Commit Integrity
**Status**: ✅ PASS
**Notes**: Test code will pass all pre-commit hooks:
- ruff/ruff-format for code style
- mypy for type checking (with proper stubs for test fixtures)
- reuse-tool for licensing
- interrogate for docstring coverage on test utilities

### Principle V: Agent Co-Authorship & DCO Requirements
**Status**: ✅ PASS
**Notes**: Commits will include `Co-Authored-By: GitHub Copilot <copilot@github.com>` and DCO sign-off via `git commit -s`.

### Principle VI: User Experience Consistency
**Status**: ✅ PASS
**Notes**: Not directly applicable (no user-facing changes). Tests verify existing UX patterns remain consistent.

### Principle VII: Performance Requirements
**Status**: ✅ PASS
**Notes**: Test suite designed to complete under 5 minutes total. Tests will not impact runtime performance of integration. Tests verify calendar refresh and event processing performance characteristics are maintained.

**Overall Assessment**: All constitutional requirements are met. This feature enhances code quality and reliability.

## Project Structure

### Documentation (this feature)

```text
specs/[###-feature]/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (/speckit.plan command)
├── data-model.md        # Phase 1 output (/speckit.plan command)
├── quickstart.md        # Phase 1 output (/speckit.plan command)
├── contracts/           # Phase 1 output (/speckit.plan command)
└── tasks.md             # Phase 2 output (/speckit.tasks command - NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
tests/
├── __init__.py
├── conftest.py              # Shared fixtures and test configuration
├── fixtures/
│   ├── __init__.py
│   ├── calendar_data.py     # ICS calendar fixtures (various formats)
│   ├── config_entries.py    # Mock configuration entries
│   └── event_data.py        # Event description fixtures
├── unit/
│   ├── __init__.py
│   ├── test_coordinator.py  # RentalControlCoordinator tests
│   ├── test_config_flow.py  # Configuration flow tests
│   ├── test_init.py         # Integration initialization tests
│   ├── test_calendar.py     # Calendar parsing tests
│   ├── test_event_overrides.py  # Event override logic tests
│   ├── test_util.py         # Utility function tests
│   └── test_sensors.py      # Sensor entity tests
├── integration/
│   ├── __init__.py
│   ├── test_full_setup.py   # End-to-end setup tests
│   ├── test_refresh_cycle.py  # Data refresh integration tests
│   └── test_error_handling.py  # Error scenario tests
└── coverage/                # Coverage reports (generated)

custom_components/rental_control/
├── __init__.py              # Existing: Integration setup
├── coordinator.py           # Existing: Data coordinator
├── config_flow.py           # Existing: Configuration UI
├── const.py                 # Existing: Constants
├── calendar.py              # Existing: Calendar entity
├── sensor.py                # Existing: Sensor platform
├── event_overrides.py       # Existing: Event override logic
├── util.py                  # Existing: Utility functions
└── sensors/
    ├── __init__.py          # Existing: Sensor module
    └── calsensor.py         # Existing: Calendar sensor
```

**Structure Decision**: Standard Home Assistant custom component test structure with:
- `tests/` directory at repository root (standard HA pattern)
- `fixtures/` subdirectory for reusable test data
- `unit/` tests for individual modules (one test file per production module)
- `integration/` tests for cross-component scenarios
- `conftest.py` for pytest fixtures and Home Assistant test setup
- Coverage reports generated to `tests/coverage/` (gitignored)

## Complexity Tracking

> **No violations to justify - all constitutional requirements are met.**

---

## Implementation Status

### Phase 0: Research ✅ COMPLETE
- ✅ `research.md` created with all technical unknowns resolved
- ✅ Testing framework patterns researched (pytest-homeassistant-custom-component)
- ✅ Mocking strategies defined (aioresponses for HTTP, pytest fixtures)
- ✅ Fixture organization planned
- ✅ Code generation testing approach defined
- ✅ Async coordinator testing patterns documented
- ✅ Pre-commit hook compatibility verified

### Phase 1: Design & Contracts ✅ COMPLETE
- ✅ `data-model.md` created defining test structure and entities
- ✅ `contracts/test-fixtures.md` created with fixture interface contracts
- ✅ `quickstart.md` created for developer guidance
- ✅ Agent context updated (`.github/agents/copilot-instructions.md`)
- ✅ Constitution Check re-evaluated (all requirements remain satisfied)

### Phase 1 Constitution Re-evaluation

**Post-Design Assessment**: All constitutional principles remain satisfied after design phase:

- **Principle I (Code Quality)**: Design includes type-safe fixtures, comprehensive docstring requirements, and async patterns throughout
- **Principle II (Atomic Commits)**: Implementation plan clearly defines logical commit boundaries
- **Principle III (Licensing)**: All design documents include proper SPDX headers
- **Principle IV (Pre-commit)**: Design accounts for all pre-commit requirements (ruff, mypy, interrogate, reuse)
- **Principle V (Agent Co-authorship)**: Commit guidance includes Co-Authored-By trailers
- **Principle VI (UX Consistency)**: Tests designed to validate existing UX patterns
- **Principle VII (Performance)**: Performance targets defined and achievable with planned approach

**Design Quality Gates**: ✅ All passed
- Design documents are complete and comprehensive
- No technical debt introduced
- All unknowns from Phase 0 resolved
- Contracts provide clear interfaces for implementation
- Structure aligns with Home Assistant best practices

### Next Steps

**Phase 2**: Use `/speckit.tasks` command to generate task breakdown for implementation.

This implementation plan is complete and ready for task generation.
