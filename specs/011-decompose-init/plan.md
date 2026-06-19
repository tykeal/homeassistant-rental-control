<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Implementation Plan: Decompose Integration Entry Module

**Feature**: `011-decompose-init` | **Planning Branch**: `011-decompose-init-plan` | **Date**: 2026-06-19 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/011-decompose-init/spec.md`

## Summary

Decompose `custom_components/rental_control/__init__.py` for issue #572 by
moving migration logic to `migrations.py` and keymaster event-listener logic to
`listeners.py`. Keep package setup, unload, update-listener, and state-change
listener orchestration in `__init__.py`, and re-export moved public entry points
so Home Assistant and existing tests continue to import them from the integration
package. Split the moved over-long bodies into private helpers in their new
modules so the implementation satisfies the 400-line file threshold and the
80-line function threshold without changing runtime behavior.

## Technical Context

**Language/Version**: Python ≥3.14.2
**Primary Dependencies**: Home Assistant core, pytest-homeassistant-custom-component
**Storage**: HA runtime storage in `hass.data[DOMAIN][entry_id]`; no persisted
storage schema changes beyond preserving existing config-entry migrations
**Testing**: pytest via `uv run pytest tests/`; ruff via `uv run ruff check ...`;
aislop complexity scan via the existing project tooling
**Target Platform**: Home Assistant custom integration on Linux/Docker/HA OS
**Project Type**: Single project — Home Assistant custom integration (HACS)
**Performance Goals**: No additional I/O or blocking work; event-listener hot
path remains O(1) dictionary/set checks with early unmonitored-lock return
**Constraints**: Preserve Home Assistant package-level entry points, avoid import
cycles, add SPDX/docstrings/type hints for new Python modules, and make no
source-code changes during this PLAN stage
**Scale/Scope**: One integration entry module split into two new implementation
modules; existing tests remain the behavioral baseline

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I: Code Quality & Testing | PASS | Future modules must be type hinted, documented, ruff/mypy clean, and covered by existing setup, migration, and keymaster-listener tests |
| II: Atomic Commit Discipline | PASS | PLAN artifacts are one docs-only commit; implementation remains future-stage scope |
| III: Licensing & Attribution | PASS | New spec artifacts include SPDX headers; future Python modules must include SPDX headers |
| IV: Pre-Commit Integrity | PASS | Hooks must not be bypassed; docs PR still runs repository hooks and CI |
| V: Agent Co-Authorship & DCO | PASS | Commit uses `git commit -s` and the required AI co-authorship trailer |
| VI: User Experience Consistency | PASS | Refactor introduces no options, services, entities, states, or behavior changes |
| VII: Performance Requirements | PASS | Design preserves existing async patterns and the keymaster early-return hot path |

**Gate result: PASS** — No violations. Proceeding to Phase 0.

## Project Structure

### Documentation (this feature)

```text
specs/011-decompose-init/
├── plan.md                    # This file
├── research.md                # Phase 0 module-split decisions and inventories
├── quickstart.md              # Phase 1 maintainer verification guide
├── checklists/
│   └── requirements.md        # Existing SPEC-stage checklist
└── tasks.md                   # Phase 2 output only; not created in PLAN stage
```

`data-model.md` is intentionally omitted because this internal refactor adds no
entities, fields, relationships, validation rules, or state transitions.
`contracts/` is intentionally omitted because no external API, service,
configuration, entity, or integration contract changes are introduced.

### Source Code (repository root)

```text
custom_components/rental_control/
├── __init__.py                # Keep setup, unload, update_listener, and
│                              # async_start_listener orchestration; re-export
│                              # moved public entry points
├── migrations.py              # Move async_migrate_entry and split version
│                              # transitions into private helpers
├── listeners.py               # Move async_register_keymaster_listener and
│                              # split event handling/diagnostics into helpers
├── const.py                   # Import constants directly; no new constants
├── coordinator.py             # Existing coordinator dependency only
└── util.py                    # Existing get_entry_data and handle_state_change

tests/
├── unit/
│   ├── test_init.py           # Package imports, update listener, migrations
│   ├── test_keymaster_event_diagnostics.py  # Event diagnostics behavior
│   └── test_checkin_sensor.py # Keymaster listener forwarding/rejection cases
└── integration/
    └── test_full_setup.py     # Full setup/unload regression coverage if needed
```

**Structure Decision**: Single-project Home Assistant custom integration. The
implementation stage should only move and helper-split the issue-reported
migration and keymaster-listener responsibilities, update package imports and
re-exports, and adjust tests only if module-boundary assertions require it.

## Phase 0 Research

Research is complete in [research.md](research.md). It resolves these decisions:

1. use `migrations.py` and `listeners.py` as the target module names;
2. keep `async_start_listener()` in `__init__.py`;
3. preserve package-level imports for `async_setup_entry`, `async_unload_entry`,
   `async_migrate_entry`, `update_listener`, `async_start_listener`, and
   `async_register_keymaster_listener`;
4. split `async_migrate_entry()` into per-version helpers because the live body
   is 132 lines; and
5. split keymaster listener registration from event filtering and diagnostics
   helpers because `async_register_keymaster_listener()` is 165 lines including its
   decorator (164-line function body) and its inner event handler is 132 lines.

### Live Source Findings

| Item | Live location/size | Plan |
|------|--------------------|------|
| `async_migrate_entry` | `__init__.py:169-300`, 132 lines | Move to `migrations.py`; keep public package re-export; split version transitions into private helpers |
| `@callback async_register_keymaster_listener` | `__init__.py:379-543`, 165 lines including decorator; 164-line function body | Move to `listeners.py`; keep public package re-export; split event handling and diagnostics helpers |
| `_handle_keymaster_event` closure | `__init__.py:403-534`, 132 lines | Extract to helper(s) inside `listeners.py`; preserve callback behavior |
| `async_start_listener` | `__init__.py:349-376`, 28 lines | Stay in `__init__.py`; it manages Keymaster state-change tracking, not event-bus filtering |
| `__init__.py` file | 543 lines by `wc -l` | Removing the two long bodies leaves the entry module safely below 400 lines |

## Phase 1 Design Artifacts

- [research.md](research.md): required and complete.
- [quickstart.md](quickstart.md): required and complete.
- `data-model.md`: N/A for this internal refactor; intentionally omitted.
- `contracts/`: N/A because no API or user-facing contracts change;
  intentionally omitted.
- `update-agent-context.sh`: intentionally not run. The design adds no new
  technology, dependency, platform, or agent-relevant context.

## Post-Design Constitution Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I: Code Quality & Testing | PASS | Quickstart requires the full existing test suite, ruff, public import checks, and aislop verification |
| II: Atomic Commit Discipline | PASS | PLAN PR contains only spec design artifacts; future implementation and tasks remain separate |
| III: Licensing & Attribution | PASS | `plan.md`, `research.md`, and `quickstart.md` include SPDX headers |
| IV: Pre-Commit Integrity | PASS | No bypasses planned; pre-commit and CI must pass before merge |
| V: Agent Co-Authorship & DCO | PASS | PLAN commit will be signed off and co-authored |
| VI: User Experience Consistency | PASS | Public entry points and runtime behavior are preserved by re-export and no semantic changes |
| VII: Performance Requirements | PASS | Helper split keeps the unmonitored-lock early return and adds no blocking work |

**Gate result: PASS** — No complexity violations.

## Complexity Tracking

> No violations to justify — all gates pass.

## Phase Notes

- PLAN stage owns only `plan.md`, `research.md`, and `quickstart.md`.
- TASKS and IMPLEMENT stages must not be performed in this PR.
- Future implementation should not import from `custom_components.rental_control`
  inside `migrations.py` or `listeners.py`; both new modules import from
  `const.py`, `util.py`, Home Assistant, and the standard library to avoid
  circular imports.
- Future implementation should use `logging.getLogger(__name__)` in the new
  modules if preserving the existing module logger convention is desired without
  importing `_LOGGER` from `__init__.py`.
