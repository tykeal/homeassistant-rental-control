<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Implementation Plan: Code Health Improvement

**Branch**: `002-code-health` | **Date**: 2026-03-10 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/002-code-health/spec.md`

## Summary

Improve the overall code health of the Rental Control integration by
fixing all identified bugs and logic issues from the code review,
adding missing error handling around calendar fetching and parsing,
converting eager log formatting to lazy evaluation, modernizing
deprecated patterns, removing dead code, and closing test coverage
gaps. All changes are non-architectural — no coordinator migration,
UUID changes, or polling redesign.

## Technical Context

**Language/Version**: Python ≥3.13.2
**Runtime Dependencies**: icalendar ≥6.1.0, x-wr-timezone ≥2.0.0
**Platform/Dev Dependencies**: homeassistant ≥2025.8.0
**Storage**: N/A (Home Assistant config entries)
**Testing**: pytest with pytest-homeassistant-custom-component,
aioresponses; asyncio_mode = auto
**Target Platform**: Home Assistant (Linux, various architectures
including Raspberry Pi)
**Project Type**: Single project — Home Assistant custom integration
**Performance Goals**: Calendar refresh must not degrade; log
formatting deferred when log level inactive
**Constraints**: Must pass pre-commit pipeline (ruff, mypy,
interrogate 100%, reuse); coverage ≥85% (fail_under); zero
regressions in the existing test suite
**Scale/Scope**: ~2,863 LOC source, ~7,036 LOC tests; 6 source
files affected, ~22 atomic commits

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1
design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I: Code Quality & Testing | ✅ PASS | All changes include tests; coverage will increase |
| II: Atomic Commit Discipline | ✅ PASS | Plan defines ~22 atomic commits, one logical change each |
| III: Licensing & Attribution | ✅ PASS | New doc files include SPDX headers; source files already have them |
| IV: Pre-Commit Integrity | ✅ PASS | All commits must pass pre-commit; no bypass |
| V: Agent Co-Authorship & DCO | ✅ PASS | All commits include Co-authored-by + DCO sign-off |
| VI: User Experience Consistency | ✅ PASS | No user-facing changes; entity IDs/config preserved |
| VII: Performance Requirements | ✅ PASS | Lazy logging improves performance; no degradation |

## Project Structure

### Documentation (this feature)

```text
specs/002-code-health/
├── plan.md              # This file
├── research.md          # Phase 0: research decisions
├── quickstart.md        # Phase 1: developer implementation guide
└── tasks.md             # Generated later by /speckit.tasks (not in this PR)
```

### Source Code (repository root)

```text
custom_components/rental_control/
├── __init__.py          # FR-010, FR-015, FR-017
├── calendar.py          # (no changes planned)
├── config_flow.py       # FR-011, FR-016, FR-019
├── const.py             # FR-021
├── coordinator.py       # FR-001–009, FR-010, FR-011, FR-013, FR-014
├── event_overrides.py   # FR-004, FR-010, FR-011, FR-020
├── sensor.py            # (no changes planned)
├── util.py              # FR-004, FR-010–012, FR-014, FR-018
└── sensors/
    └── calsensor.py     # (no changes planned)

tests/
├── unit/
│   ├── test_coordinator.py  # FR-023, FR-024 (new tests)
│   └── test_util.py         # FR-022 (new tests)
└── integration/
    └── test_error_handling.py  # FR-023 (new tests)
```

**Structure Decision**: Existing HA custom integration layout. No
new source or test files are created; all code changes modify
existing source and test files.
