# Implementation Plan: Lock Code Buffer Times

**Branch**: `009-lock-code-buffer` | **Date**: 2025-07-17 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/009-lock-code-buffer/spec.md`

## Summary

Add configurable pre-check-in and post-checkout buffer times (in minutes) for lock codes. The buffer offsets are applied only to the Keymaster `date_range_start`/`date_range_end` values sent via `async_fire_set_code` and `async_fire_update_times`. All internal integration logic (calendar display, check-in sensors, event overrides, auto check-in/checkout timers) continues to use unbuffered reservation times. Implementation follows the established `trim_names` pattern: new constants, config flow fields (conditional on lock entry), coordinator properties, migration v9→v10, and lazy update on next refresh cycle.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: Home Assistant core, Keymaster integration, icalendar, voluptuous
**Storage**: HA config entries (persisted via `config_entry.data`)
**Testing**: pytest via `uv run pytest tests/`
**Target Platform**: Home Assistant (Linux/Docker, Raspberry Pi to full servers)
**Project Type**: Single project — Home Assistant custom integration (HACS)
**Performance Goals**: Lock code generation must complete within sensor update cycle (~30s); buffer calculation is O(1) datetime arithmetic — no performance concern
**Constraints**: Must not block HA event loop; minute-granularity datetime arithmetic
**Scale/Scope**: 2 new config fields, ~6 files modified, ~8 test files added/modified

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I: Code Quality & Testing | ✅ PASS | New code will have type hints, docstrings, full test coverage |
| II: Atomic Commit Discipline | ✅ PASS | Feature decomposes into ~6 atomic commits (const, migration, coordinator, config_flow, util, tests) |
| III: Licensing & Attribution | ✅ PASS | All modified files already have SPDX headers; new files will include them |
| IV: Pre-Commit Integrity | ✅ PASS | ruff, mypy, interrogate, reuse-tool will be run before each commit |
| V: Agent Co-Authorship & DCO | ✅ PASS | Commits will include Co-authored-by and Signed-off-by trailers |
| VI: User Experience Consistency | ✅ PASS | Buffer fields follow existing config flow patterns; conditional on lock entry like trim_names |
| VII: Performance Requirements | ✅ PASS | Buffer is O(1) timedelta addition; no impact on refresh cycle or event loop |

**Gate result: PASS** — No violations. Proceeding to Phase 0.

## Project Structure

### Documentation (this feature)

```text
specs/009-lock-code-buffer/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output (internal API contracts)
└── tasks.md             # Phase 2 output (/speckit.tasks command)
```

### Source Code (repository root)

```text
custom_components/rental_control/
├── __init__.py          # Migration logic (v9→v10)
├── config_flow.py       # Options flow — buffer fields
├── const.py             # CONF_CODE_BUFFER_BEFORE, CONF_CODE_BUFFER_AFTER, defaults
├── coordinator.py       # Buffer properties, update_config
├── sensors/
│   └── calsensor.py     # (unchanged — passes event attrs to util functions)
├── util.py              # async_fire_set_code / async_fire_update_times — apply buffer offset
├── event_overrides.py   # (unchanged — uses unbuffered times for internal matching)
└── strings.json         # UI labels for buffer fields

tests/
├── unit/
│   ├── test_config_flow.py   # Buffer field validation
│   ├── test_coordinator.py   # Buffer property tests
│   ├── test_init.py          # Migration v9→v10 tests
│   └── test_util.py          # Buffer offset application tests
└── integration/
    └── test_refresh_cycle.py  # Buffer changes propagate on refresh
```

**Structure Decision**: Single-project Home Assistant custom integration. All source under `custom_components/rental_control/`, all tests under `tests/`.

## Complexity Tracking

> No violations to justify — all gates pass.
