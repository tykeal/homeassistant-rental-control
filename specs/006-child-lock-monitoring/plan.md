<!--
SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Implementation Plan: Child Lock Monitoring for Keymaster Parent/Child Lock Setups

**Branch**: `006-child-lock-monitoring` | **Date**: 2025-07-18 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/006-child-lock-monitoring/spec.md`

## Summary

Extend rental-control's keymaster integration to accept unlock events from child locks associated with a configured parent lock. Currently, only the parent lock's `lockname` is matched in the event bus listener, so guests entering through child lock doors (e.g., side door, garage) are never detected as checked in. The implementation adds dynamic child lock discovery on the coordinator's refresh cycle, expands the event bus listener to match any lockname in the monitored set (parent + children), and passes lock identity through to the check-in sensor for attribution.

## Technical Context

**Language/Version**: Python ≥3.14.2
**Primary Dependencies**: Home Assistant ≥2026.4.0, keymaster (HACS integration), icalendar ≥7.0.0
**Storage**: N/A (HA state machine + config entries)
**Testing**: pytest + pytest-homeassistant-custom-component (552 existing tests)
**Target Platform**: Home Assistant (Linux/any HA-supported OS)
**Project Type**: Single HA custom integration
**Performance Goals**: Event processing must not block the HA event loop; child lock discovery must complete within the existing coordinator refresh cycle (configurable 30s–1440min, default 2min)
**Constraints**: Async-only I/O; must remain lightweight for Raspberry Pi; no new config options
**Scale/Scope**: Typically 1–5 child locks per parent; single rental-control entry per parent lock

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| **I: Code Quality & Testing** | ✅ PASS | New code will have full test coverage; type hints required; docstrings on all public functions |
| **II: Atomic Commit Discipline** | ✅ PASS | Feature decomposes into 4–5 atomic commits: coordinator changes, event listener changes, sensor changes, tests |
| **III: Licensing & Attribution** | ✅ PASS | All new/modified files will include SPDX headers |
| **IV: Pre-Commit Integrity** | ✅ PASS | All commits must pass ruff, mypy, interrogate, reuse-tool, gitlint |
| **V: Agent Co-Authorship & DCO** | ✅ PASS | Agent commits will include Co-authored-by + Signed-off-by trailers |
| **VI: User Experience Consistency** | ✅ PASS | No new config options; no entity naming changes; backward compatible |
| **VII: Performance Requirements** | ✅ PASS | Child lock discovery piggybacks on existing refresh cycle; event matching uses set lookup O(1) |

**Gate Result**: ✅ ALL PASS — proceed to Phase 0.

## Project Structure

### Documentation (this feature)

```text
specs/006-child-lock-monitoring/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   └── events.md        # Updated event contract with lock_name field
└── tasks.md             # Phase 2 output (NOT created by plan)
```

### Source Code (repository root)

```text
custom_components/rental_control/
├── __init__.py          # MODIFIED: event bus listener lockname matching
├── coordinator.py       # MODIFIED: child lock discovery, monitored_locknames property
├── const.py             # UNCHANGED (no new constants needed)
├── config_flow.py       # UNCHANGED (still configure single parent lock)
├── switch.py            # UNCHANGED (monitoring switch already governs all event processing)
├── sensors/
│   └── checkinsensor.py # MODIFIED: accept lock_name parameter, include in event data
└── util.py              # UNCHANGED

tests/
├── unit/
│   ├── test_coordinator.py      # MODIFIED: add child lock discovery tests
│   ├── test_init.py             # MODIFIED: add event listener child lock tests
│   └── test_checkin_sensor.py   # MODIFIED: add lock identity tests
└── integration/
    └── test_checkin_tracking.py  # MODIFIED: add child lock lifecycle tests
```

**Structure Decision**: Existing Home Assistant custom integration structure. No new files in production code — only modifications to existing files. Tests added to existing test modules.

## Complexity Tracking

> No constitution violations. No complexity justifications needed.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| *(none)* | — | — |
