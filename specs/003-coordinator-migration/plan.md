<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Implementation Plan: Coordinator Base Class Migration

**Branch**: `003-coordinator-migration` | **Date**: 2026-03-11
**Spec**: [spec.md](spec.md)
**Input**: Feature specification from
`/specs/003-coordinator-migration/spec.md`

## Summary

Migrate the hand-rolled `RentalControlCoordinator` to inherit from
Home Assistant's `DataUpdateCoordinator` base class. This replaces
custom `next_refresh` timestamp scheduling with the platform's
built-in refresh lifecycle, gains automatic error tracking via
`last_update_success`, and adopts the standard entity subscription
model (`CoordinatorEntity`). The migration must be fully transparent
to users — no entity ID changes, no configuration changes, and no
behavioral regressions.

## Technical Context

**Language/Version**: Python ≥ 3.13.2 (target: py313)
**Primary Dependencies**: Home Assistant ≥ 2025.8.0, icalendar
≥ 6.1.0, x-wr-timezone ≥ 2.0.0
**Storage**: N/A (all state in-memory, config via HA config entries)
**Testing**: pytest + pytest-homeassistant-custom-component +
aioresponses
**Target Platform**: Home Assistant (Linux, various hardware)
**Project Type**: Single project (HA custom component)
**Performance Goals**: Refresh within configured interval (0–1440
minutes), no event loop blocking, lightweight memory
**Constraints**: Must not break existing entity IDs, sensor
attributes, or automation references; must pass full pre-commit
pipeline (ruff, mypy, interrogate 100%, reuse)
**Scale/Scope**: ~7 source files affected, ~640-line coordinator,
6 dependent entity/utility modules, consolidated unit and
integration test suite

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1
design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I: Code Quality & Testing | ✅ PASS | Tests required for all changes; coverage ≥ 85% enforced |
| II: Atomic Commits | ✅ PASS | Plan will decompose into single-concern phases |
| III: Licensing & Attribution | ✅ PASS | SPDX headers on all new/modified files |
| IV: Pre-Commit Integrity | ✅ PASS | All hooks must pass; no --no-verify |
| V: Agent Co-Authorship & DCO | ✅ PASS | Co-authored-by + git commit -s on every commit |
| VI: UX Consistency | ✅ PASS | FR-010 through FR-013 enforce zero regression |
| VII: Performance | ✅ PASS | Refresh interval and async patterns preserved |

**Gate Result**: PASS — no violations.

## Project Structure

### Documentation (this feature)

```text
specs/003-coordinator-migration/
├── spec.md
├── plan.md              # This file
├── research.md          # Phase 0: DataUpdateCoordinator research
├── data-model.md        # Phase 1: Entity/coordinator data model
├── quickstart.md        # Phase 1: Migration quickstart guide
├── contracts/           # Phase 1: Interface contracts
│   └── coordinator.md   # Coordinator public interface contract
├── checklists/
│   └── requirements.md  # Existing spec quality checklist
└── tasks.md             # Phase 2 output (speckit.tasks)
```

### Source Code (repository root)

```text
custom_components/rental_control/
├── __init__.py          # Integration setup (coordinator creation)
├── coordinator.py       # PRIMARY: Migration target
├── calendar.py          # Entity: migrate to CoordinatorEntity
├── sensor.py            # Platform setup (entity creation)
├── sensors/
│   └── calsensor.py     # Entity: migrate to CoordinatorEntity
├── event_overrides.py   # Collaborator (no base class change)
├── util.py              # Utilities (listener setup, service calls)
└── const.py             # Constants

tests/
├── conftest.py          # Shared fixtures (need update)
├── unit/
│   ├── test_coordinator.py   # Major rewrite
│   ├── test_calendar.py      # Update for CoordinatorEntity
│   ├── test_sensors.py       # Update for CoordinatorEntity
│   ├── test_event_overrides.py  # Minor updates
│   ├── test_init.py          # Update setup flow
│   └── test_util.py          # Minimal changes
├── integration/
│   ├── test_full_setup.py    # Verify end-to-end
│   ├── test_refresh_cycle.py # Verify DUC scheduling
│   └── test_error_handling.py # Verify UpdateFailed
└── fixtures/                 # Calendar test data
```

**Structure Decision**: Existing single-project layout. No new
directories needed — all changes are in-place modifications to
existing files.

## Complexity Tracking

No constitution violations to justify.

## Constitution Re-Check (Post Phase 1 Design)

| Principle | Status | Notes |
|-----------|--------|-------|
| I: Code Quality & Testing | ✅ PASS | All phases include test updates; coverage ≥ 85% |
| II: Atomic Commits | ✅ PASS | 5 phases, each a single logical concern |
| III: Licensing & Attribution | ✅ PASS | SPDX headers on all spec artifacts |
| IV: Pre-Commit Integrity | ✅ PASS | No bypass; hooks run on every commit |
| V: Agent Co-Authorship & DCO | ✅ PASS | All commits include Co-authored-by + DCO |
| VI: UX Consistency | ✅ PASS | Zero entity ID / attribute changes per data model |
| VII: Performance | ✅ PASS | DUC uses same async patterns; no blocking I/O |

**Gate Result**: PASS — design introduces no violations.
