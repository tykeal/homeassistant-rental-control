# Implementation Plan: Static Random Seed from iCal UID

**Branch**: `001-static-random-uid-seed` | **Date**: 2026-04-24 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/001-static-random-uid-seed/spec.md`

## Summary

Switch the `static_random` door code generator seed from the calendar event description to the iCal UID. The UID is immutable per RFC 5545, so generated codes will remain stable across description edits. When the UID is unavailable (`None`), the system falls back to description-based seeding (legacy behavior), and then to date-based generation if both are `None`. The iCal UID is also exposed as a new sensor event attribute for automation and diagnostic use.

This is a **BREAKING CHANGE** — existing `static_random` users will experience a one-time code rotation on upgrade.

## Technical Context

**Language/Version**: Python 3.14+ (pyproject.toml `requires-python = ">=3.14.2"`)
**Primary Dependencies**: homeassistant ≥2026.4.0, icalendar ≥7.0.0, x-wr-timezone ≥2.0.0
**Storage**: N/A (in-memory event attributes on sensor entities)
**Testing**: pytest with pytest-homeassistant-custom-component, freezegun, aioresponses; coverage ≥85%
**Target Platform**: Home Assistant (Linux, macOS, various ARM/x86 hardware)
**Project Type**: Single project — Home Assistant custom component
**Performance Goals**: Lock code generation must complete within sensor update cycle (configurable 30s–1440min, default 2min). `random.seed()` + `random.randrange()` is O(1) — no performance concern.
**Constraints**: Must not block HA event loop; all sensor updates are synchronous callbacks dispatched by the coordinator. No new async work needed.
**Scale/Scope**: Single file change (`calsensor.py`) + test updates. ~20 lines of production code changed.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I: Code Quality & Testing | ✅ PASS | All changes will have unit tests; coverage target ≥85% maintained |
| II: Atomic Commit Discipline | ✅ PASS | Feature decomposes into 2-3 atomic commits (attribute exposure, seed change, test updates) |
| III: Licensing & Attribution | ✅ PASS | Modified files already have correct SPDX headers |
| IV: Pre-Commit Integrity | ✅ PASS | All hooks (ruff, mypy, interrogate, reuse-tool, gitlint) will be run before each commit |
| V: Agent Co-Authorship & DCO | ✅ PASS | Agent commits will include Co-authored-by and Signed-off-by trailers |
| VI: User Experience Consistency | ✅ PASS | New `uid` attribute follows existing attribute patterns; backward-compatible fallback preserves existing behavior |
| VII: Performance Requirements | ✅ PASS | `random.seed(str)` is O(1); no new I/O, no blocking calls, no memory impact |

**Gate result: ALL PASS — proceed to Phase 0.**

## Project Structure

### Documentation (this feature)

```text
specs/001-static-random-uid-seed/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── checklists/
│   └── requirements.md  # Pre-existing spec quality checklist
└── tasks.md             # Phase 2 output (NOT created by plan)
```

### Source Code (repository root)

```text
custom_components/
└── rental_control/
    ├── sensors/
    │   └── calsensor.py        # PRIMARY: _generate_door_code() seed change + uid attribute
    ├── coordinator.py           # UNCHANGED: already parses UID from iCal
    ├── const.py                 # UNCHANGED: no new constants needed
    └── event_overrides.py       # UNCHANGED: already tracks UIDs for slot identity

tests/
├── unit/
│   └── test_sensors.py          # PRIMARY: update static_random tests, add UID-based tests
└── fixtures/
    └── config_entries.py        # UNCHANGED: existing static_random fixtures work as-is
```

**Structure Decision**: Single project structure — this is a Home Assistant custom component with all source under `custom_components/rental_control/` and all tests under `tests/`. No new files are created; only existing files are modified.

## Complexity Tracking

> No constitution violations. No complexity justifications needed.
