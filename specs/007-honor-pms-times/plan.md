<!--
SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Implementation Plan: Honor PMS Calendar Event Times

**Branch**: `007-honor-pms-times` | **Date**: 2025-07-22 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/007-honor-pms-times/spec.md`

## Summary

Add a boolean "Honor event times" configuration option that, when enabled, changes the time
resolution priority in `coordinator._ical_parser()` so that calendar-provided check-in/check-out
times (from the PMS) take precedence over stored Keymaster override times for events with explicit
times. All-day events continue to fall back to override times or configured defaults. The existing
`async_reserve_or_get_slot()` → `async_fire_update_times()` pipeline already detects time
differences and propagates updates to Keymaster — no downstream changes are needed.

## Technical Context

**Language/Version**: Python ≥ 3.14.2
**Primary Dependencies**: homeassistant (core), icalendar, voluptuous, x-wr-timezone, aiohttp
**Storage**: Home Assistant config entries (persisted via HA's `.storage/` JSON files)
**Testing**: pytest (with pytest-homeassistant-custom-component, aioresponses)
**Target Platform**: Home Assistant (Linux/aarch64/x86_64, Raspberry Pi to server)
**Project Type**: Single HA custom integration
**Performance Goals**: No degradation to calendar refresh cycle (configurable 30s–1440min, default 2min)
**Constraints**: Must not block HA event loop; async patterns required; must work on constrained hardware
**Scale/Scope**: Single integration, ~3k LOC in `custom_components/rental_control/`, ~10 source files touched

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I: Code Quality & Testing | ✅ PASS | New code will have full test coverage, type hints, docstrings |
| II: Atomic Commit Discipline | ✅ PASS | Feature decomposes into 5-7 atomic commits (const→config→coordinator→migration→tests→strings) |
| III: Licensing & Attribution | ✅ PASS | All new/modified files will include SPDX headers |
| IV: Pre-Commit Integrity | ✅ PASS | All commits must pass ruff, mypy, interrogate, reuse-tool, gitlint |
| V: Agent Co-Authorship & DCO | ✅ PASS | Commits will include Co-authored-by and Signed-off-by trailers |
| VI: User Experience Consistency | ✅ PASS | New toggle follows existing `should_update_code` boolean pattern in options flow |
| VII: Performance Requirements | ✅ PASS | Change is a conditional branch in existing code path — zero perf impact |

**Gate result**: ALL PASS — proceed to Phase 0.

## Project Structure

### Documentation (this feature)

```text
specs/007-honor-pms-times/
├── plan.md              # This file
├── research.md          # Phase 0 output — research findings
├── data-model.md        # Phase 1 output — entity and config model
├── quickstart.md        # Phase 1 output — developer quickstart
├── contracts/           # Phase 1 output — internal interface contracts
│   └── time-resolution.md
└── tasks.md             # Phase 2 output (NOT created by plan)
```

### Source Code (repository root)

```text
custom_components/rental_control/
├── __init__.py          # MODIFY: Add v7→v8 migration for new config key
├── config_flow.py       # MODIFY: Add honor_event_times toggle to schema, bump VERSION
├── const.py             # MODIFY: Add CONF_HONOR_EVENT_TIMES + DEFAULT_HONOR_EVENT_TIMES
├── coordinator.py       # MODIFY: Read new config, alter time resolution in _ical_parser
├── strings.json         # MODIFY: Add UI labels for new option
└── translations/
    ├── en.json          # MODIFY: Add English translation
    └── fr.json          # MODIFY: Add French translation

tests/
├── unit/
│   ├── test_config_flow.py   # MODIFY: Test new toggle appears and persists
│   └── test_coordinator.py   # MODIFY: Test time resolution with honor_event_times on/off
└── integration/               # May need integration test for end-to-end time propagation
```

**Structure Decision**: Existing single HA custom integration layout. No new files created in
source tree — all changes are additions to existing modules. One new documentation contract file.

## Complexity Tracking

> No constitution violations. No complexity justifications needed.
